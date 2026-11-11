"""Calibrate the local speech engine on your own voice.

Records a set of sentences from the microphone into a local folder, then
decodes them with exactly what the app runs -- same model, same Silero VAD,
same 100 ms blocks -- and prints what the engine heard and where it went wrong.

The point is to find the words a particular voice loses, so decoding and
script matching can be calibrated to it. Nothing is uploaded: the recordings
are plain WAV files under the output folder and can be deleted at any time.

Use:

    .venv/Scripts/python.exe tests/calibrate_voice.py
    .venv/Scripts/python.exe tests/calibrate_voice.py --device 2
    .venv/Scripts/python.exe tests/calibrate_voice.py --sentences my-script.txt
    .venv/Scripts/python.exe tests/calibrate_voice.py --no-mic --dir <folder>

``--no-mic`` scores WAV files that already exist (16 kHz, 16-bit, mono), which
is also how the script is exercised without a microphone.

The report is written to <out>/report.json next to the recordings.
"""

import argparse
import json
import os
import queue
import subprocess
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

MODEL_DIR = os.path.join(REPO_ROOT, "model")
SAMPLE_RATE = 16_000
BLOCK = 1_600  # 100 ms, the app's block size

from bench_speech_engines import SENTENCES  # noqa: E402  (same corpus)

END_OF_UTTERANCE_SECONDS = 1.2  # fallback when no VAD model is installed
RMS_GATE = 0.02


def read_wav(path):
    with wave.open(path, "rb") as reader:
        if reader.getframerate() != SAMPLE_RATE or reader.getnchannels() != 1:
            raise SystemExit(
                f"{os.path.basename(path)} is {reader.getframerate()} Hz "
                f"{reader.getnchannels()}ch; the engine wants 16000 Hz mono")
        raw = reader.readframes(reader.getnframes())
    return np.frombuffer(raw, dtype=np.int16)


def blocks_of(samples):
    return [samples[i:i + BLOCK] for i in range(0, len(samples), BLOCK)]


def words(text):
    cleaned = []
    for token in text.lower().split():
        word = "".join(ch for ch in token if ch.isalnum() or ch == "'")
        if word:
            cleaned.append(word)
    return cleaned


def edit_errors(reference, hypothesis):
    """Word-level Levenshtein path: the substitutions and deletions to learn.

    Returns (wer, [("said", "heard"), ...]) where the second element of a
    pair is None for a dropped word.
    """
    n, m = len(reference), len(hypothesis)
    cost = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        cost[i][0] = i
    for j in range(m + 1):
        cost[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost[i][j] = min(
                cost[i - 1][j] + 1,
                cost[i][j - 1] + 1,
                cost[i - 1][j - 1] + (reference[i - 1] != hypothesis[j - 1]),
            )
    # Walk back to collect what happened to each reference word.
    errors = []
    i, j = n, m
    while i > 0 and j > 0:
        if reference[i - 1] == hypothesis[j - 1]:
            i, j = i - 1, j - 1
        elif cost[i][j] == cost[i - 1][j - 1] + 1:
            errors.append((reference[i - 1], hypothesis[j - 1]))
            i, j = i - 1, j - 1
        elif cost[i][j] == cost[i - 1][j] + 1:
            errors.append((reference[i - 1], None))
            i -= 1
        else:
            j -= 1
    while i > 0:
        errors.append((reference[i - 1], None))
        i -= 1
    return cost[n][m] / max(1, n), list(reversed(errors))


def score(engine, samples):
    """Decode one recording the way the worker does and score it."""
    engine.reset()
    finals, events, costs, heard_now = [], [], [], ""
    for block in blocks_of(samples):
        began = time.perf_counter()
        text, is_final = engine.accept(block.tobytes())
        costs.append((time.perf_counter() - began) * 1000.0)
        if text:
            events.append((text, is_final))
        if is_final:
            if text:
                finals.append(text)
            heard_now = ""
        elif text:
            heard_now = text
    tail = engine.finalize()
    if tail:
        finals.append(tail)
        events.append((tail, True))
    transcript = " ".join(finals)
    return {
        "heard": transcript,
        "events": events,
        "finals": len(finals),
        "block_ms_mean": float(np.mean(costs)) if costs else 0.0,
        "block_ms_worst": float(np.max(costs)) if costs else 0.0,
        "last_partial": heard_now,
    }


def track(script: str, events) -> float:
    """How far the reader would get through the script from these results.

    This is the number the teleprompter promises: transcription errors are
    survivable, a reader that stops advancing is not. The matcher is the same
    one the overlay uses, with the same thresholds.
    """
    from matching_engine import MatchingEngine

    matcher = MatchingEngine()
    matcher.load_script(script)
    position = -1
    for text, _is_final in events:
        aligned = matcher.align_forward(
            text, max(0, position + 1), matcher.word_count,
            min_score=80, max_skip=3, resync_skip=9)
        if aligned is not None:
            position = aligned.last_word
    if matcher.word_count == 0:
        return 0.0
    return (position + 1) / matcher.word_count


# ── Recording ───────────────────────────────────────────────────────────────


def record_sentence(sd, device, vad, max_seconds=25.0, prompt=None):
    """Record until the speaker stops.

    When ``prompt`` is given it is spoken first, then the microphone is opened
    *before* the beep. Opening the mic after the cue lost the first word of
    several takes: the reader starts the moment the prompt ends, so the buffer
    has to be live before the cue, not after it. The same pre-onset trick the
    worker uses keeps a second and a half before the detector confirms speech.
    """
    if prompt:
        speak(prompt)  # the mic is closed here, so the prompt is not captured
    captured = queue.Queue()

    def callback(indata, frames, time_info, status):
        captured.put(bytes(indata))

    stream = sd.RawInputStream(
        samplerate=SAMPLE_RATE, blocksize=BLOCK, dtype="int16", channels=1,
        device=device, callback=callback)
    stream.start()
    if prompt:
        time.sleep(0.35)
        beep(880, 200)  # a cue now, not a start signal: buffering is already on
    try:
        chunks, pre_roll = [], []
        started = False
        silence_blocks = 0
        gate_silence = int(round(END_OF_UTTERANCE_SECONDS / (BLOCK / SAMPLE_RATE)))
        blocks_seen = 0
        while blocks_seen < max_seconds * (SAMPLE_RATE / BLOCK):
            try:
                block = captured.get(timeout=1.0)
            except queue.Empty:
                break
            blocks_seen += 1
            samples = np.frombuffer(block, dtype=np.int16).astype(np.float32) / 32768.0
            if vad is not None:
                speaking, ended = vad.accept(block)
            else:
                level = float(np.sqrt(np.mean(samples ** 2)))
                speaking = level >= RMS_GATE
                silence_blocks = 0 if speaking else silence_blocks + 1
                ended = silence_blocks >= gate_silence

            if not started:
                if speaking:
                    started = True
                    chunks.extend(pre_roll)
                    chunks.append(block)
                else:
                    pre_roll = (pre_roll + [block])[-15:]
                continue
            chunks.append(block)
            if ended:
                break
        if not started:
            return None
    finally:
        stream.stop()
        stream.close()
    if not chunks:
        return None
    return np.frombuffer(b"".join(chunks), dtype=np.int16)


def speak(text: str, rate: int = 0):
    """Say a prompt with the Windows voice, so the session needs no keyboard.

    PowerShell is used because System.Speech is already on the machine; no
    package, account or network is involved.
    """
    escaped = text.replace('"', '`"')
    script = (
        'Add-Type -AssemblyName System.Speech\n'
        '$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n'
        f'$s.Rate = {rate}\n'
        f'$s.Speak("{escaped}")\n'
        '$s.Dispose()'
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=90)
    except Exception as exc:
        print(f"    (could not speak the prompt: {exc})", flush=True)


def beep(frequency: int = 880, milliseconds: int = 220):
    """A go cue that cannot be mistaken for speech."""
    try:
        import winsound
        winsound.Beep(frequency, milliseconds)
    except Exception:
        pass


def speech_seconds(samples) -> float:
    """How much of a take is above the room floor."""
    parts = blocks_of(samples)
    loud = 0
    for part in parts:
        level = float(np.sqrt(np.mean((part.astype(np.float32) / 32768.0) ** 2)))
        if level > 0.01:
            loud += 1
    return loud * (BLOCK / SAMPLE_RATE)


def record_all_auto(sentences, out_dir, device, max_seconds=12.0):
    """Hands-free session: speak the sentence, beep, record the repeat.

    Two things make the takes trustworthy:

    * the microphone is opened only after the prompt has finished, so the
      prompt voice is not captured by it (headphones are still safer);
    * the endpoint waits 4 s of silence, not the app's 0.75 s, because a
      calibration take must survive a mid-sentence hesitation and a pause for
      breath. Shorter windows closed takes early and scored the missing audio
      as recognition errors it was not: at 2 s, "roughly sixty | per cent of
      the delay..." arrived as "roughly sixty".
    """
    import sounddevice as sd
    import speech_engine

    vad = speech_engine.create_vad(MODEL_DIR, trailing_silence=4.0)
    os.makedirs(out_dir, exist_ok=True)

    speak(f"Voice calibration, {len(sentences)} sentences. "
          "Wait for the beep, then repeat the sentence exactly. "
          "Starting in ten seconds.")
    for _ in range(3):
        time.sleep(3.3)
        beep(660, 120)
    print("recording session started", flush=True)

    saved = []
    for index, sentence in enumerate(sentences, start=1):
        print(f"[{index}/{len(sentences)}] recording: {sentence}", flush=True)
        samples = record_sentence(sd, device, vad, max_seconds=max_seconds,
                                  prompt=sentence)
        # A take that is far shorter than the sentence it repeats is a miss
        # (the reader is still getting ready), not a recognition error.
        word_count = max(1, len(sentence.split()))
        expected_min = max(1.0, word_count * 0.22)
        if samples is not None and speech_seconds(samples) < expected_min:
            print(f"    only {speech_seconds(samples):.1f} s of speech, asking again",
                  flush=True)
            retry = record_sentence(sd, device, vad, max_seconds=max_seconds,
                                    prompt="Once more please, " + sentence)
            previous = speech_seconds(samples) if samples is not None else 0.0
            if retry is not None and speech_seconds(retry) > previous:
                samples = retry
        beep(600, 140)
        if samples is None:
            print("    skipped", flush=True)
            continue
        path = os.path.join(out_dir, f"take-{index:02d}.wav")
        with wave.open(path, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(SAMPLE_RATE)
            writer.writeframes(samples.tobytes())
        print(f"    saved {os.path.basename(path)} "
              f"({len(samples) / SAMPLE_RATE:.1f} s, "
              f"{speech_seconds(samples):.1f} s speech)", flush=True)
        saved.append({"index": index, "text": sentence, "path": path})
        time.sleep(0.4)
    speak("That is the last one. Thank you.")
    return saved


def record_all(sentences, out_dir, device):
    import sounddevice as sd
    import speech_engine

    vad = speech_engine.create_vad(MODEL_DIR)
    model = None
    if vad is None:
        print("! no Silero VAD model: falling back to a level gate")
        model = "level"

    os.makedirs(out_dir, exist_ok=True)
    saved = []
    for index, sentence in enumerate(sentences, start=1):
        print(f"\n[{index}/{len(sentences)}] {sentence}")
        input("    press Enter, then read it out loud...")
        for count in (3, 2, 1):
            print(f"    {count}...", end="\r", flush=True)
            time.sleep(0.7)
        print("    listening", flush=True)
        samples = record_sentence(sd, device, vad)
        if samples is None:
            print("    heard nothing, skipped")
            continue
        path = os.path.join(out_dir, f"take-{index:02d}.wav")
        with wave.open(path, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(SAMPLE_RATE)
            writer.writeframes(samples.tobytes())
        seconds = len(samples) / SAMPLE_RATE
        print(f"    saved {os.path.basename(path)} ({seconds:.1f} s)")
        saved.append({"index": index, "text": sentence, "path": path})
    return saved


def reader_follow(sentences, takes, engine):
    """Drive the real overlay with the recordings and report where it got to.

    This is the end-to-end promise: the script is the whole session, the
    results are fed through the same slot the audio worker uses, and the
    reading line has to advance through it. A high word error rate that still
    steps the reader is a pass; a clean transcript that stalls is a fail.
    """
    import contextlib
    import io
    import tempfile

    from PyQt5.QtGui import QFont
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication

    import ui_overlay
    from store import Store

    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
    win = ui_overlay.NotchOverlay()
    win._store = Store(tempfile.mkdtemp(prefix="notch-reader-"))
    win._voice_mode = False
    win.show()
    QTest.qWait(200)
    win._voice_mode = True

    script = "\n".join(sentences)
    win._font_family = "Segoe UI"
    win._font_size = 12
    win._text_edit.setFont(QFont("Segoe UI", 12))
    win._text_edit.setPlainText(script)
    win._script_text = script
    win._matching.load_script(script)
    win._line_spans = []
    win._current_line = 0
    win._confirmed_word = -1
    win._partial_word = -1
    win._apply_highlight(-1)
    win._refresh_lines()
    QTest.qWait(100)

    per_take = []
    for take in takes:
        before = win._confirmed_word
        samples = read_wav(take["path"])
        engine.reset()
        with contextlib.redirect_stdout(io.StringIO()):
            for block in blocks_of(samples):
                text, is_final = engine.accept(block.tobytes())
                if text:
                    win._on_speech_text(text, is_final)
            tail = engine.finalize()
            if tail:
                win._on_speech_text(tail, True)
        # The reader advances at most one rendered line per LINE_STEP_INTERVAL,
        # so the measurement has to wait for that pacing rather than assume it.
        QTest.qWait(1500)
        per_take.append({
            "file": os.path.basename(take["path"]),
            "from": before + 1,
            "to": win._confirmed_word,
            "line": win._current_line,
        })

    reached = (win._confirmed_word + 1) / max(1, win._matching.word_count)
    lines = len(win._line_spans)
    line_reached = win._current_line / max(1, lines - 1)
    win._voice_mode = False
    win.close()
    return {
        "words": win._matching.word_count,
        "confirmed_word": win._confirmed_word,
        "reached": reached,
        "line": win._current_line,
        "lines": lines,
        "line_reached": line_reached,
        "takes": per_take,
    }


def build_engines(speech_engine, compare: bool = False):
    """The preferred engine, or every installed one when comparing."""
    engines = []
    sherpa_dir = speech_engine.find_sherpa_model_dir(MODEL_DIR)
    if sherpa_dir is not None:
        engine = speech_engine.SherpaEngine(sherpa_dir)
        engine.load()
        engines.append(engine)
    if all(os.path.isdir(os.path.join(MODEL_DIR, marker))
           for marker in speech_engine.VOSK_MARKERS):
        engine = speech_engine.VoskEngine(MODEL_DIR)
        engine.load()
        engines.append(engine)
    if not compare and engines:
        return engines[:1]
    return engines


def collect_existing(folder, sentences):
    """Score every WAV in a folder, pairing them with the corpus in order."""
    takes = []
    names = sorted(name for name in os.listdir(folder) if name.lower().endswith(".wav"))
    for position, name in enumerate(names):
        text = sentences[position] if position < len(sentences) else ""
        takes.append({"index": position + 1, "text": text,
                      "path": os.path.join(folder, name)})
    return takes


# ── Report ──────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sentences", default="",
                        help="a .txt file to read instead of the built-in corpus")
    parser.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "notch-voice"))
    parser.add_argument("--dir", default="", help="score existing WAVs in this folder")
    parser.add_argument("--no-mic", action="store_true", help="skip recording")
    parser.add_argument("--auto", action="store_true",
                        help="hands-free: speak each sentence, beep, record the repeat")
    parser.add_argument("--compare", action="store_true",
                        help="score every installed engine, not just the preferred one")
    parser.add_argument("--reader", action="store_true",
                        help="also drive the real overlay with the recordings")
    parser.add_argument("--device", type=int, default=None)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    if args.sentences:
        with open(args.sentences, encoding="utf-8") as handle:
            sentences = [line.strip() for line in handle
                         if line.strip() and not line.strip().startswith("#")]
    else:
        sentences = list(SENTENCES)
    if args.limit:
        sentences = sentences[:args.limit]

    import speech_engine

    engines = build_engines(speech_engine, compare=args.compare)
    if not engines:
        print(f"No speech model in {MODEL_DIR}")
        return 1

    if args.dir or args.no_mic:
        folder = args.dir or args.out
        takes = collect_existing(folder, sentences)
        if not takes:
            print(f"No WAV files in {folder}")
            return 1
    else:
        if args.auto:
            takes = record_all_auto(sentences, args.out, args.device)
        else:
            takes = record_all(sentences, args.out, args.device)
        if not takes:
            print("Nothing was recorded.")
            return 1

    per_engine = {}
    for engine in engines:
        results = []
        all_errors = {}
        for take in takes:
            samples = read_wav(take["path"])
            scored = score(engine, samples)
            reference = words(take["text"])
            hypothesis = words(scored["heard"])
            wer, errors = edit_errors(reference, hypothesis) if reference else (0.0, [])
            for said, heard in errors:
                key = said if heard is None else f"{said} -> {heard}"
                all_errors[key] = all_errors.get(key, 0) + 1
            results.append({
                "file": os.path.basename(take["path"]),
                "seconds": round(len(samples) / SAMPLE_RATE, 2),
                "said": take["text"],
                "heard": scored["heard"],
                "wer": round(wer, 3),
                "tracked": round(track(take["text"], scored["events"]), 3),
                "words": len(reference),
                "block_ms_mean": round(scored["block_ms_mean"], 2),
                "block_ms_worst": round(scored["block_ms_worst"], 1),
            })
        rows = [row for row in results if row["words"]]
        weighted = (sum(row["wer"] * row["words"] for row in rows)
                    / max(1, sum(row["words"] for row in rows)))
        per_engine[engine.name] = {
            "results": results,
            "errors": all_errors,
            "wer_weighted": weighted,
            "block_ms_mean": float(np.mean([r["block_ms_mean"] for r in results])),
            "block_ms_worst": float(np.max([r["block_ms_worst"] for r in results])),
        }

    if len(engines) > 1:
        print("\nengine comparison on these recordings:")
        print(f"  {'engine':34s} {'wer':>7s} {'tracked':>9s} {'block':>9s} {'worst':>8s}")
        for name, data in per_engine.items():
            tracked = float(np.mean([r["tracked"] for r in data["results"]]))
            print(f"  {name:34s} {data['wer_weighted'] * 100:6.1f}% "
                  f"{tracked * 100:8.1f}% {data['block_ms_mean']:7.1f} ms "
                  f"{data['block_ms_worst']:6.1f} ms")

    best_name = min(per_engine, key=lambda key: per_engine[key]["wer_weighted"])
    best = per_engine[best_name]
    print("\n" + "=" * 70)
    print(f"engine            {best_name}")
    print(f"recordings        {len(best['results'])}  "
          f"({sum(row['seconds'] for row in best['results']):.1f} s)")
    print(f"word error rate   {best['wer_weighted'] * 100:.1f}% weighted by length")
    tracked = float(np.mean([row["tracked"] for row in best["results"]]))
    print(f"script tracked    {tracked * 100:.1f}% of the words, on average")
    print(f"100 ms block      {best['block_ms_mean']:.1f} ms mean, "
          f"{best['block_ms_worst']:.1f} ms worst")
    for row in best["results"]:
        flag = "ok  " if row["wer"] == 0.0 else "err "
        print(f"  {flag}{row['file']}  {row['wer'] * 100:5.1f}%  "
              f"tracked {row['tracked'] * 100:5.1f}%  {row['heard']!r}")
    if best["errors"]:
        print("\nwords to calibrate (said -> heard, dropped if no arrow):")
        for key, count in sorted(best["errors"].items(), key=lambda item: -item[1]):
            print(f"  {count}x  {key}")
    else:
        print("\nevery word was heard correctly")

    follow = None
    if args.reader:
        engine = next(e for e in engines if e.name == best_name)
        follow = reader_follow(sentences, takes, engine)
        print("\nreader, driven by these recordings:")
        print(f"  script            {follow['words']} words, {follow['lines']} lines")
        print(f"  words confirmed   {follow['confirmed_word'] + 1} "
              f"({follow['reached'] * 100:.1f}%)")
        print(f"  reading line      {follow['line']} of {follow['lines'] - 1} "
              f"({follow['line_reached'] * 100:.1f}%)")
        stalled = [row for row in follow["takes"] if row["to"] <= row["from"] - 1]
        if stalled:
            print("  takes that did not advance the reader: "
                  + ", ".join(row["file"] for row in stalled))

    report = {
        "best_engine": best_name,
        "reader": follow,
        "engines": {
            name: {
                "wer_weighted": round(data["wer_weighted"], 4),
                "tracked_mean": round(float(np.mean(
                    [r["tracked"] for r in data["results"]])), 4),
                "block_ms_mean": round(data["block_ms_mean"], 2),
                "block_ms_worst": round(data["block_ms_worst"], 1),
                "errors": data["errors"],
                "takes": data["results"],
            }
            for name, data in per_engine.items()
        },
    }
    folder = args.dir or args.out
    os.makedirs(folder, exist_ok=True)
    report_path = os.path.join(folder, "report.json")
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    print(f"\nreport: {report_path}")
    print("The recordings are plain WAV files in the same folder; delete them "
          "whenever you like. Nothing was uploaded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
