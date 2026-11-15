"""End-to-end check: prediction driven by real speech.

Three passes share one measurement path — the real recogniser, the real
voice-activity detector and the real overlay, fed one 100 ms block at a time
in wall-clock time. They differ only in where the audio comes from.

**SAPI (default).** Three sentences are synthesised offline with the Windows
voices, decoded, and timed. No microphone, no person, safe in CI.

    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_predictive_speech.py

**Live (``--live``).** The production ``AudioWorker`` opens the microphone,
the reader reads the displayed script aloud, and a second capture of the same
microphone is written to a session folder together with the measurements.

    .venv/Scripts/python.exe tests/check_predictive_speech.py --live --seconds 30

**Replay (``--replay``).** That recorded session is decoded again, through the
same recogniser and the same overlay. One reading of the script therefore
becomes a repeatable, real-audio benchmark that no longer needs the person,
which is what the synthetic timeline could not give.

    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_predictive_speech.py --replay .sessions/2026-01-01-120000

Each pass measures the one thing a reader can feel: was a word already
anticipated before the recogniser confirmed it, and by how long?

Nothing is uploaded: the synthesis, the acoustic model, the recording and the
profile all stay on this machine. The profile is written to a temporary store.
"""

import argparse
import json
import os
import sys
import tempfile
import time
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, HERE)
os.chdir(REPO_ROOT)

from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from bench_speech_engines import _synthesise, read_pcm, SAMPLE_RATE

BLOCK = 1_600  # 100 ms, the worker's block
DEFAULT_SESSION_DIR = os.path.join(REPO_ROOT, ".sessions")
failures = []
SENTENCES = [
    "Good evening, and thanks for joining.",
    "Tonight I want to show you three small things, not thirty.",
    "First, how the board reads a city block.",
]
SCRIPT = " ".join(SENTENCES)


def check(name, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {name}"
          f"{(' — ' + detail) if detail else ''}")
    if not condition:
        failures.append(name)


def skip(message):
    print(f"SKIP  {message}")
    return 0


def words_of(text):
    return [word for word in text.split() if word.strip()]


def pct(values, share):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1,
                       int(round(share * (len(ordered) - 1))))]


def build_audio(directory):
    """One 16 kHz mono WAV of the three sentences, with endpoint silence."""
    raw_paths = []
    voice = None
    for candidate in ("Microsoft David Desktop", "Microsoft Zira Desktop"):
        first = os.path.join(directory, "sapi-0.wav")
        if _synthesise(SENTENCES[0], candidate, 0, first):
            voice = candidate
            raw_paths.append(first)
            break
    if voice is None:
        return None
    for index, sentence in enumerate(SENTENCES[1:], start=1):
        raw = os.path.join(directory, f"sapi-{index}.wav")
        if not _synthesise(sentence, voice, 0, raw):
            return None
        raw_paths.append(raw)
    combined = os.path.join(directory, "sapi-combined.wav")
    with wave.open(raw_paths[0], "rb") as first:
        params = first.getparams()
    frames = b""
    for path in raw_paths:
        with wave.open(path, "rb") as reader:
            frames += reader.readframes(reader.getnframes())
        frames += b"\x00\x00" * int(0.7 * params.framerate)
    with wave.open(combined, "wb") as writer:
        writer.setparams(params)
        writer.writeframes(frames)
    return combined


def make_overlay(text, tmp):
    from notch.ui import overlay as ui_overlay
    from notch.storage.store import Store
    from notch.audio.predict import PredictiveHighlighter, ProfileStore

    win = ui_overlay.NotchOverlay()
    win._store = Store(tmp)
    win._predictor = PredictiveHighlighter(ProfileStore(tmp))
    win._voice_mode = False
    win.show()
    QTest.qWait(150)
    win._voice_mode = True
    win._font_family = "Segoe UI"
    win._font_size = 12
    win._text_edit.setFont(QFont("Segoe UI", 12))
    win._text_edit.setPlainText(text)
    win._script_text = text
    win._matching.load_script(text)
    win._predictor.set_script(win._matching.raw_words)
    win._line_spans = []
    win._current_line = 0
    win._confirmed_word = -1
    win._partial_word = -1
    win._predicted_word = None
    win._predicted_span = None
    win._apply_highlight(-1)
    win._refresh_lines()
    win._text_edit.verticalScrollBar().setValue(0)
    win._scroll_to_reading_line(0)
    return win


class Sampler:
    """Records when each script word was first anticipated and confirmed."""

    def __init__(self, win, word_count):
        self.win = win
        self.word_count = word_count
        self.predicted_at = [None] * word_count
        self.confirmed_at = [None] * word_count
        # When the recogniser first showed any evidence of the word, partial or
        # confirmed. This is the tight measure: a final can arrive a sentence
        # late and in one jump, which says more about the recogniser's
        # endpointing than about the paint.
        self.heard_at = [None] * word_count
        self.last_confirmed = -1
        self.last_predicted = None
        self.flicker = 0
        self.max_decrease = 0
        self.speaking_seen = False
        self.started = time.monotonic()
        self.last_growth_at = None
        # Every backward move, with the context that caused it: a slow
        # correction after a wrong prediction is part of the design, a fast
        # back-and-forth is the defect the anti-flicker rule exists for.
        self.decreases = []

    def sample(self):
        win = self.win
        now = time.monotonic()
        confirmed = win._confirmed_word
        if confirmed > self.last_confirmed:
            for word in range(self.last_confirmed + 1,
                              min(confirmed + 1, self.word_count)):
                if self.confirmed_at[word] is None:
                    self.confirmed_at[word] = now
            self.last_confirmed = confirmed
        heard = max(win._confirmed_word, win._partial_word)
        for word in range(self.word_count):
            if word <= heard and self.heard_at[word] is None:
                self.heard_at[word] = now
        predicted = win._predicted_word
        if predicted is not None:
            first = max(0, win._display_word() + 1)
            for word in range(first, min(predicted + 1, self.word_count)):
                if self.predicted_at[word] is None:
                    self.predicted_at[word] = now
            if self.last_predicted is not None:
                if predicted > self.last_predicted:
                    self.last_growth_at = now
                elif predicted < self.last_predicted:
                    self.decreases.append({
                        "at_ms": round(1000.0 * (now - self.started)),
                        "from": self.last_predicted,
                        "to": predicted,
                        "speaking": bool(win._is_speaking),
                        "spoken": win._display_word(),
                        "since_growth_ms": (
                            round(1000.0 * (now - self.last_growth_at))
                            if self.last_growth_at is not None else None),
                    })
                    if win._is_speaking:
                        self.flicker += 1
                        self.max_decrease = max(
                            self.max_decrease,
                            self.last_predicted - predicted)
        if predicted is not None:
            self.last_predicted = predicted
        if win._is_speaking:
            self.speaking_seen = True

    def report(self):
        confirmed = [word for word in range(self.word_count)
                     if self.confirmed_at[word] is not None]
        anticipated = [word for word in confirmed
                       if self.predicted_at[word] is not None
                       and self.predicted_at[word] < self.confirmed_at[word]]
        leads = [self.confirmed_at[word] - self.predicted_at[word]
                 for word in anticipated]
        heard = [word for word in range(self.word_count)
                 if self.heard_at[word] is not None]
        early = [word for word in heard
                 if self.predicted_at[word] is not None
                 and self.predicted_at[word] < self.heard_at[word]]
        early_leads = [self.heard_at[word] - self.predicted_at[word]
                       for word in early]
        return {
            "script_words": self.word_count,
            "confirmed_words": len(confirmed),
            "confirmed_share": round(
                len(confirmed) / max(1, self.word_count), 3),
            "anticipated_before_confirmation": len(anticipated),
            "prediction_coverage": round(
                len(anticipated) / max(1, len(confirmed)), 3),
            "mean_lead_over_confirmation_ms": round(
                1000.0 * (sum(leads) / len(leads)) if leads else 0.0, 1),
            "heard_words": len(heard),
            "anticipated_before_evidence": len(early),
            "coverage_vs_evidence": round(
                len(early) / max(1, len(heard)), 3),
            "mean_lead_over_evidence_ms": round(
                1000.0 * (sum(early_leads) / len(early_leads))
                if early_leads else 0.0, 1),
            "flicker_decreases": self.flicker,
            "max_decrease_words": self.max_decrease,
            "decreases": self.decreases,
            "speaking_seen": self.speaking_seen,
        }


def drive_audio(path, script, source, trace=None):
    """Decode one WAV through the real pipeline in wall-clock time.

    ``trace`` is a path for a JSON timeline of every result and every painted
    boundary. It is how a surprising number gets explained instead of guessed
    at: the difference between a live pass and its replay is visible there.
    """
    from notch.audio.speech import (create_recognizer, create_vad,
                                    release_recognizer)
    from notch.audio.worker import AudioWorker
    from notch import paths

    # Decode with the production block handler, not a hand-rolled loop: the
    # worker's voice-activity state machine, onset buffer and utterance final
    # decide which results the reader ever sees. A straight `engine.accept`
    # here produced a different recogniser stream (few partials, whole-sentence
    # finals) and under-reported coverage, which a second recorded session
    # exposed.
    worker = AudioWorker(model_path=paths.model_dir())
    model_dir = worker._resolve_model_dir(worker.model_path)
    engine = create_recognizer(model_dir)
    if engine is None:
        return None, f"no local speech model in {model_dir}"
    vad = create_vad(model_dir)
    if vad is None:
        release_recognizer(engine)
        return None, "no local voice-activity model"
    worker._engine = engine
    worker._vad = vad

    app = QApplication.instance() or QApplication(sys.argv)
    win = make_overlay(script, tempfile.mkdtemp(prefix="notch-predict-e2e-"))
    slot_costs = []

    def on_text(text, is_final):
        """Time only the overlay's share, not the decoder feeding it."""
        before = time.perf_counter()
        win._on_speech_text(text, is_final)
        slot_costs.append((time.perf_counter() - before) * 1000.0)

    worker.speech_text_signal.connect(on_text)
    worker.speaking_signal.connect(win._on_speaking_changed)
    worker.volume_level_signal.connect(win._on_volume_level)
    sampler = Sampler(win, len(words_of(script)))
    stream = read_pcm(path)
    samples = (np.clip(stream, -1.0, 1.0) * 32767.0).astype(np.int16)
    blocks = [samples[start:start + BLOCK].tobytes()
              for start in range(0, len(samples), BLOCK)]

    print(f"Decoding {len(blocks) * BLOCK / SAMPLE_RATE:.1f} s from {source} "
          f"with {engine.name} ...")
    decode_costs = []
    timeline = []
    started = time.perf_counter()
    for index, block in enumerate(blocks):
        # Keep the audio wall-clock, because the predictor's lead is measured
        # in real seconds and the real app never decodes faster than a voice.
        target = started + index * (BLOCK / SAMPLE_RATE)
        while True:
            remaining = target - time.perf_counter()
            if remaining <= 0.0:
                break
            # The overlay advances its own prediction every PREDICTION_INTERVAL
            # from its 16 ms tick. Sleeping through this wait would starve that
            # tick and under-measure the lead by a whole recognition block, so
            # the wait pumps the event loop instead, exactly as the live pass
            # does while the person is reading.
            app.processEvents()
            sampler.sample()
            time.sleep(min(remaining, 0.003))
        app.processEvents()

        before = time.perf_counter()
        worker._process_block(block)
        decode_costs.append((time.perf_counter() - before) * 1000.0)
        sampler.sample()
        if trace is not None:
            timeline.append({
                "t_ms": round(1000.0 * (time.perf_counter() - started)),
                "speaking": bool(win._is_speaking),
                "confirmed": win._confirmed_word,
                "partial": win._partial_word,
                "predicted": win._predicted_word,
                "line": win._current_line,
            })

    # Let the last utterance finalise and settle.
    QTest.qWait(900)
    sampler.sample()
    report = sampler.report()
    report["engine"] = engine.name
    report["source"] = source
    report["audio_seconds"] = round(len(samples) / SAMPLE_RATE, 1)
    report["handler_ms_mean"] = round(
        sum(slot_costs) / max(1, len(slot_costs)), 3)
    report["decode_ms_mean"] = round(
        sum(decode_costs) / max(1, len(decode_costs)), 3)
    report["decode_ms_p95"] = round(pct(decode_costs, 0.95), 3)
    report["decode_ms_max"] = round(max(decode_costs), 3)

    if trace is not None:
        with open(trace, "w", encoding="utf-8") as handle:
            json.dump(timeline, handle, indent=1)

    win._voice_mode = False
    win.close()
    release_recognizer(engine)
    return report, None


def evaluate(report, words_label, coverage_bar=0.6):
    from notch.audio.predict import RETREAT_DELAY

    print(report)
    check(f"real recogniser produced words for the {words_label}",
          report["confirmed_words"] >= max(4, report["script_words"] // 2),
          f"{report['confirmed_words']} of {report['script_words']}")
    # Coverage is reported, not maximised. The paint may never pass the
    # rendered line, and the line only steps on a confirmed word, so a
    # recording's ceiling is the words per line times how many finals the
    # recogniser cuts. A lower bar is therefore the honest comparison for a
    # recording; the mean lead below is the part that must not go negative.
    check("prediction covered the words the recogniser confirmed",
          report["prediction_coverage"] >= coverage_bar,
          f"{report['prediction_coverage']:.0%} coverage"
          f" (bar {coverage_bar:.0%})")
    check("prediction preceded confirmation on average",
          report["mean_lead_over_confirmation_ms"] > 0,
          f"{report['mean_lead_over_confirmation_ms']:.0f} ms mean lead")

    # The design permits one slow correction: a run that was wrong retreats by
    # a word after RETREAT_DELAY. What it forbids is a reversal *inside* that
    # delay, which is the back-and-forth a reader sees as flicker, and a jump
    # backwards of more than one word.
    decreases = report.get("decreases", [])
    early = [event for event in decreases
             if event["speaking"]
             and event.get("since_growth_ms") is not None
             and event["since_growth_ms"] < RETREAT_DELAY * 1000.0 - 50.0]
    jumps = [event for event in decreases
             if event["from"] - event["to"] > 1]
    check("the predicted run never reverses inside the retreat delay",
          not early,
          f"{len(early)} early reversals in {len(decreases)} corrections")
    check("a correction never jumps back more than one word",
          not jumps, f"{len(jumps)} jumps")
    if "handler_ms_mean" in report:
        check("a real result is handled well inside a frame",
              report["handler_ms_mean"] < 5.0,
              f"{report['handler_ms_mean']:.3f} ms")
    # The decoder's own cost is reported, not asserted: tests/check_speech_engine.py
    # owns that threshold, and its number moves by an order of magnitude with
    # whatever else is running on the machine.
    if "decode_ms_mean" in report:
        print(f"NOTE  decode {report['decode_ms_mean']:.1f} ms mean, "
              f"{report['decode_ms_p95']:.1f} ms p95 per 100 ms block")
    return 0 if not failures else 1


# ── Recording ────────────────────────────────────────────────────────


def _resample(raw, from_rate, to_rate):
    """Linear resample of int16 PCM; only used when the device refuses 16 kHz."""
    if from_rate == to_rate or not raw:
        return raw
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    count = int(round(len(samples) * to_rate / from_rate))
    if count <= 0:
        return b""
    source = np.linspace(0.0, 1.0, len(samples), endpoint=False)
    target = np.linspace(0.0, 1.0, count, endpoint=False)
    out = np.interp(target, source, samples)
    return np.clip(out, -32768, 32767).astype(np.int16).tobytes()


class MicRecorder:
    """A second, independent capture of the microphone.

    ``AudioWorker`` deliberately does not expose its audio, so the session is
    recorded beside it. Shared-mode input allows more than one stream on the
    same device; if the device refuses, the live check still measures the
    prediction and only the replayable recording is lost.
    """

    def __init__(self):
        self.blocks = []
        self.error = None
        self.rate = SAMPLE_RATE
        self._stream = None

    def _callback(self, indata, frames, time_info, status):
        self.blocks.append(bytes(indata))

    def start(self):
        try:
            import sounddevice as sd
        except Exception as exc:
            self.error = f"sounddevice unavailable: {exc}"
            return False
        rates = [SAMPLE_RATE]
        try:
            default_rate = int(sd.query_devices(kind="input")["default_samplerate"])
            if default_rate and default_rate != SAMPLE_RATE:
                rates.append(default_rate)
        except Exception:
            pass
        for rate in rates:
            try:
                self._stream = sd.RawInputStream(
                    samplerate=rate, blocksize=BLOCK, dtype="int16",
                    channels=1, callback=self._callback)
                self._stream.start()
                self.rate = rate
                self.error = None
                return True
            except Exception as exc:
                self.error = str(exc)
                self._stream = None
        return False

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        return b"".join(self.blocks)

    def frames(self):
        return len(b"".join(self.blocks)) // 2

    def seconds(self):
        return self.frames() / float(self.rate or SAMPLE_RATE)


def save_session(directory, raw, rate, script, report, source_note):
    """Write the recorded voice and its measurements as one session."""
    os.makedirs(directory, exist_ok=True)
    pcm = _resample(raw, rate, SAMPLE_RATE)
    audio_path = os.path.join(directory, "mic.wav")
    with wave.open(audio_path, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes(pcm)
    meta = {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "capture_rate": rate,
        "audio_seconds": round(len(pcm) / 2 / SAMPLE_RATE, 2),
        "script": script,
        "note": source_note,
        "live_report": report,
    }
    with open(os.path.join(directory, "session.json"), "w",
              encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2)
    return audio_path


# ── Passes ───────────────────────────────────────────────────────────


def run_sapi(trace=None):
    audio_dir = tempfile.mkdtemp(prefix="notch-predict-sapi-")
    path = build_audio(audio_dir)
    if path is None:
        return skip("SAPI synthesis is unavailable on this machine")
    report, reason = drive_audio(path, SCRIPT, "SAPI synthesis", trace)
    if report is None:
        return skip(reason)
    return evaluate(report, "script")


def run_live(seconds, save_dir):
    from notch import paths
    from notch.audio.speech import find_sherpa_model_dir

    model_root = paths.model_dir()
    has_sherpa = find_sherpa_model_dir(model_root) is not None
    has_vosk = all(os.path.isdir(os.path.join(model_root, marker))
                   for marker in ("conf", "am"))
    if not (has_sherpa or has_vosk):
        return skip(f"no local speech model in {model_root}")
    try:
        import sounddevice as sd
        devices = [index for index, device in enumerate(sd.query_devices())
                   if device.get("max_input_channels", 0) > 0]
    except Exception:
        devices = []
    if not devices:
        return skip("no microphone input device")

    app = QApplication.instance() or QApplication(sys.argv)
    directory = os.path.join(save_dir, time.strftime("%Y-%m-%d-%H%M%S"))
    win = make_overlay(SCRIPT, tempfile.mkdtemp(prefix="notch-predict-live-"))
    sampler = Sampler(win, len(words_of(SCRIPT)))

    recorder = MicRecorder()

    # The app shows a modal dialog when the microphone fails. A check has to
    # report that and skip, not wait for someone to click OK.
    errors = []
    win._on_audio_error = lambda message: errors.append(message)
    win._start_listening()
    # The worker loads the acoustic model inside its own thread; words spoken
    # during that load are never heard, so wait for the engine to be live.
    deadline = time.monotonic() + 25.0
    ready = False
    while time.monotonic() < deadline:
        app.processEvents()
        worker = win._audio_worker
        if worker is None or not worker.isRunning():
            break
        if getattr(worker, "_engine", None) is not None:
            ready = True
            break
        time.sleep(0.05)
    if not ready:
        if errors:
            for message in errors:
                print(f"NOTE  {message.splitlines()[0]}")
            win._voice_mode = False
            win.close()
            return skip("the microphone or the model could not start")
        print("NOTE  the recogniser did not report ready before the timeout")

    # Start the capture only now: the recording should be the reading, not the
    # model load in front of it.
    if not recorder.start():
        print(f"NOTE  session will not be repeatable: {recorder.error}")

    print("Read the script on the card aloud. "
          f"Recording {seconds:.0f} s ...")
    QTest.qWait(400)  # let the empty buffer flush before the clock starts
    timer = QTimer()
    timer.timeout.connect(sampler.sample)
    timer.start(50)
    QTest.qWait(int(seconds * 1000))
    timer.stop()
    win._stop_listening()
    win._stop_listening_sync()
    sampler.sample()

    raw = recorder.stop()
    report = sampler.report()
    report["source"] = "microphone"
    report["recorded_seconds"] = round(recorder.seconds(), 2)

    if raw:
        save_session(directory, raw, recorder.rate, SCRIPT, report,
                     "live microphone reading")
        report["session"] = directory
        print(f"Saved the session to {directory}")
        print("Replay it with:")
        print(f"  .venv/Scripts/python.exe tests/check_predictive_speech.py "
              f"--replay {os.path.relpath(directory)}")
    else:
        print("No audio captured; nothing to replay.")

    win._voice_mode = False
    win.close()
    return evaluate(report, "microphone")


def run_replay(target, script_override=None, trace=None):
    if os.path.isdir(target):
        audio_path = os.path.join(target, "mic.wav")
        meta_path = os.path.join(target, "session.json")
        script = script_override
        meta = {}
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as handle:
                    meta = json.load(handle)
            except Exception:
                meta = {}
            script = script or meta.get("script")
        source = "recorded session " + os.path.basename(os.path.abspath(target))
    else:
        audio_path = target
        script = script_override
        meta = {}
        source = "recorded audio " + os.path.basename(target)

    if not os.path.isfile(audio_path):
        return skip(f"no recording at {audio_path}")
    if not script:
        return skip("no script recorded with the session; pass --script FILE")
    report, reason = drive_audio(audio_path, script, source, trace)
    if report is None:
        return skip(reason)
    if meta.get("live_report"):
        live = meta["live_report"]
        report["live_session"] = {
            "recorded_seconds": meta.get("audio_seconds"),
            "coverage": live.get("prediction_coverage"),
            "mean_lead_over_confirmation_ms":
                live.get("mean_lead_over_confirmation_ms"),
        }
    return evaluate(report, "recorded session", coverage_bar=0.2)


def main():
    # The first-run guide is modal. A machine that has never run Notch (a
    # runner, a new laptop) would block on it instead of starting, so the
    # check states the machine it describes. check_note_clearing, the
    # robustness check and the upgrade check do the same.
    from PyQt5.QtCore import QSettings
    QSettings("Notch", "Notch").setValue("guide_seen", True)
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true",
                        help="use the real microphone and record the session")
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--save", default=DEFAULT_SESSION_DIR,
                        help="where a live session is written")
    parser.add_argument("--replay", metavar="SESSION",
                        help="re-decode a session folder or a WAV file")
    parser.add_argument("--script", metavar="FILE",
                        help="script text for a WAV recorded outside a session")
    parser.add_argument("--trace", metavar="FILE",
                        help="write a JSON timeline of results and paint")
    args = parser.parse_args()

    script_override = None
    if args.script:
        with open(args.script, encoding="utf-8") as handle:
            script_override = handle.read()

    if args.replay:
        return run_replay(args.replay, script_override, args.trace)
    if args.live:
        return run_live(args.seconds, args.save)
    return run_sapi(args.trace)


if __name__ == "__main__":
    sys.exit(main())
