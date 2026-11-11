"""Engine-level checks for the local speech pipeline.

Complements ``check_voice_follow.py``, which drives the reader with synthetic
text. This check uses the real models and real audio, all generated on this
machine:

  * the engine that ``speech_engine`` selects for ``model/``,
  * decoding a synthesised sentence: the text that comes back, and the cost of
    one 100 ms block (the loop must stay well inside the block to keep the
    scroll smooth),
  * Silero VAD: silence is silence, speech is speech, and the utterance closes
    a trailing-silence window after the speaker stops,
  * the worker's gating: blocks are only decoded while speech is detected, and
    the utterance close emits one final,
  * no socket is opened while the model loads or decodes -- audio, transcripts
    and script text never leave the process.

Run:

    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_speech_engine.py

The audio fixture is synthesised with the Windows SAPI voice already installed,
so the check needs no network and no recorded asset. When no model is present
the check reports SKIP rather than failing, because CI installs it separately.
"""

import contextlib
import io
import os
import socket
import sys
import tempfile
import time
import wave

import numpy as np
from PyQt5.QtWidgets import QApplication

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, HERE)
os.chdir(REPO_ROOT)

MODEL_DIR = os.path.join(REPO_ROOT, "model")
FIXTURE_DIR = os.path.join(tempfile.gettempdir(), "notch-bench")
SENTENCE = "Second, why the harbour plan changed."
BLOCK = 1600  # 100 ms at 16 kHz, the worker's block size

failures = []


def check(name, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not condition:
        failures.append(name)


def skip(name, reason):
    print(f"SKIP  {name} — {reason}")


def fixture() -> str:
    """A 16 kHz sentence from the installed SAPI voices, built once."""
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    path = os.path.join(FIXTURE_DIR, "engine-check.wav")
    raw = os.path.join(FIXTURE_DIR, "engine-check-raw.wav")
    if not os.path.exists(path):
        import bench_speech_engines as bench
        if not bench._synthesise(SENTENCE, "Microsoft David Desktop", 0, raw):
            return ""
        bench._append_silence(raw, path, 0.7)
    return path


def pcm_blocks(path: str) -> list:
    with wave.open(path, "rb") as reader:
        assert reader.getframerate() == 16000, reader.getframerate()
        raw = reader.readframes(reader.getnframes())
    return [raw[i:i + BLOCK * 2] for i in range(0, len(raw), BLOCK * 2)]


def with_silence(blocks: list, seconds: float) -> list:
    """Append a controlled pause, so an endpoint is not left hanging."""
    return blocks + [b"\x00\x00" * BLOCK] * int(seconds / (BLOCK / 16000.0))


def words(text: str) -> list:
    return ["".join(ch for ch in token if ch.isalnum()).lower()
            for token in text.split() if token.strip()]


def main() -> int:
    app = QApplication(sys.argv)  # noqa: F841 — the worker is a QThread

    import speech_engine
    from audio_worker import AudioWorker
    from matching_engine import MatchingEngine

    if speech_engine.find_sherpa_model_dir(MODEL_DIR) is None and not os.path.isdir(
            os.path.join(MODEL_DIR, "conf")):
        skip("speech engine checks", f"no model in {MODEL_DIR}")
        print("\nSKIP: no speech model installed.")
        return 0

    audio_path = fixture()
    if not audio_path:
        print("FAIL  the SAPI fixture could not be synthesised")
        return 1
    blocks = pcm_blocks(audio_path)

    # ── Engine selection ─────────────────────────────────────────────
    start = time.perf_counter()
    engine = speech_engine.create_recognizer(MODEL_DIR)
    load_ms = (time.perf_counter() - start) * 1000.0
    check("a local recogniser loads from model/", engine is not None,
          f"{load_ms:.0f} ms")
    if engine is None:
        print("\nFAILED: no recogniser")
        return 1
    sherpa_present = speech_engine.find_sherpa_model_dir(MODEL_DIR) is not None
    check("sherpa-onnx is preferred when its model is present",
          (not sherpa_present) or engine.name.startswith("sherpa-onnx"),
          f"selected {engine.name}")

    # ── Decoding the sentence ────────────────────────────────────────
    def decode(blocks_to_feed):
        engine.reset()
        heard, finals, costs = "", [], []
        for block in blocks_to_feed:
            began = time.perf_counter()
            text, is_final = engine.accept(block)
            costs.append(time.perf_counter() - began)
            if is_final:
                finals.append(text)
            heard = text or heard
        return heard, finals, costs

    heard, finals, costs = decode(blocks)
    tail = engine.finalize()
    transcript = " ".join([t for t in finals if t] + ([tail] if tail else []))
    expected = words(SENTENCE)
    got = words(transcript)
    hits = sum(1 for word in expected if word in got)
    check("the sentence is recognised", hits >= len(expected) - 1,
          f"{hits}/{len(expected)} words, heard {transcript!r}")

    # The matcher, not the raw transcript, is what the reader depends on: the
    # recognised words have to land on the script line.
    matcher = MatchingEngine()
    matcher.load_script(SENTENCE)
    aligned = matcher.align_forward(transcript, 0, matcher.word_count,
                                    min_score=80, max_skip=3, resync_skip=9)
    check("the recognised words align with the script",
          aligned is not None and aligned.last_word >= matcher.word_count - 2,
          f"reached word {aligned.last_word if aligned else None} "
          f"of {matcher.word_count}")

    # The reader consumes partials, not the final transcript: the live
    # highlight follows them and the line steps on the one that reaches the
    # last word. So the partial sequence itself has to be cumulative and
    # align as it grows, not just the text at the end.
    engine.reset()
    partial_texts = []
    with contextlib.redirect_stdout(io.StringIO()):
        for block in blocks:
            text, is_final = engine.accept(block)
            if text and not is_final:
                partial_texts.append(text)
    engine.finalize()
    growth = [len(words(text)) for text in partial_texts]
    check("partial results only ever grow",
          growth == sorted(growth) and len(growth) >= 5,
          f"{len(partial_texts)} partials, word counts {growth[:3]}...{growth[-3:]}")
    progresses = []
    for text in partial_texts:
        aligned = matcher.align_forward(
            text, max(0, (progresses[-1] + 1) if progresses else 0),
            matcher.word_count, min_score=80, max_skip=3, resync_skip=9)
        if aligned is not None:
            progresses.append(aligned.last_word)
    check("every partial lands on the script, in order",
          progresses == sorted(progresses) and bool(progresses),
          f"reached {progresses}")

    costs = np.array(costs) if costs else np.array([0.0])
    check("one 100 ms block decodes well inside the block",
          float(np.mean(costs)) * 1000.0 < 40.0,
          f"mean {np.mean(costs) * 1000.0:.1f} ms, "
          f"p95 {np.percentile(costs, 95) * 1000.0:.1f} ms, "
          f"worst {np.max(costs) * 1000.0:.1f} ms")

    # ── Silero VAD ───────────────────────────────────────────────────
    detector = speech_engine.create_vad(MODEL_DIR)
    if detector is None:
        skip("Silero VAD checks", "model/silero_vad.onnx not installed")
    else:
        silence = b"\x00\x00" * BLOCK
        spoke = ended_on_silence = False
        for _ in range(10):  # 1 s of silence
            speech, ended = detector.accept(silence)
            spoke = spoke or speech
            ended_on_silence = ended_on_silence or ended
        check("silence is not speech", not spoke)
        check("silence does not close an utterance that never started",
              not ended_on_silence)

        padded = with_silence(blocks, 2.0)
        speech_seen = 0
        last_end = None
        with contextlib.redirect_stdout(io.StringIO()):
            for index, block in enumerate(padded):
                speech, ended = detector.accept(block)
                if speech:
                    speech_seen += 1
                if ended:
                    last_end = index
        check("the VAD hears the sentence", speech_seen >= 10,
              f"{speech_seen} speech blocks")
        check("the utterance is closed once the speaker stops",
              last_end is not None)

        # The pause the reader waits through, measured against the audio rather
        # than against the detector's own flag: the last block that carries any
        # signal is where the speaker actually stopped, and the endpoint must
        # come a pause window later (not a syllable, not two seconds).
        acoustic_end = 0
        for index, block in enumerate(padded):
            if any(np.frombuffer(block, dtype=np.int16)):
                acoustic_end = index
        if last_end is not None:
            gap = (last_end - acoustic_end) * BLOCK / 16000.0
            check("the utterance closes about one pause window after speech",
                  0.4 <= gap <= 1.3,
                  f"{gap:.2f} s after the last audible block")

    # ── Worker gating and utterance close ────────────────────────────
    worker = AudioWorker(model_path=MODEL_DIR)
    worker._engine = engine
    worker._vad = detector
    worker._block_seconds = worker.BLOCK_SAMPLES / float(worker.SAMPLE_RATE)
    worker._onset_blocks = max(
        1, int(round(worker.ONSET_SECONDS / worker._block_seconds)))
    from collections import deque
    worker._pending = deque(maxlen=worker._onset_blocks)
    events = []
    worker.speech_text_signal.connect(
        lambda text, is_final: events.append((text, is_final)))
    engine.reset()
    if detector is not None:
        detector.reset()

    with contextlib.redirect_stdout(io.StringIO()):
        # Silence first: nothing should reach the recogniser.
        for _ in range(10):
            worker._feed_recognizer(b"\x00\x00" * BLOCK, False)
        after_silence = len(events)
        for block in blocks:
            if worker._vad is not None:
                speech, ended = worker._vad.accept(block)
                worker._feed_recognizer(block, speech)
                if ended:
                    worker._close_utterance()
            else:
                worker._feed_recognizer(block, None)
        worker._close_utterance()

    check("silence never reaches the recogniser",
          after_silence == 0, f"{after_silence} events during silence")
    finals = [text for text, is_final in events if is_final]
    check("the worker emits a final when the speaker stops",
          bool(finals), f"{len(finals)} finals, {events!r}")
    if finals:
        hits = sum(1 for word in words(SENTENCE) if word in words(finals[-1]))
        check("that final carries the sentence",
              hits >= len(words(SENTENCE)) - 2,
              f"{hits}/{len(words(SENTENCE))} words: {finals[-1]!r}")

    # ── Nothing leaves the machine ───────────────────────────────────
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create = socket.create_connection
    attempts = []

    def refuse(self, *args, **kwargs):
        attempts.append(("connect", args))
        raise AssertionError("a socket was opened while decoding audio")

    def refuse_ex(self, *args, **kwargs):
        attempts.append(("connect_ex", args))
        return 1

    def refuse_create(*args, **kwargs):
        attempts.append(("create_connection", args))
        raise AssertionError("a connection was opened while decoding audio")

    socket.socket.connect = refuse
    socket.socket.connect_ex = refuse_ex
    socket.create_connection = refuse_create
    try:
        offline_engine = speech_engine.create_recognizer(MODEL_DIR)
        with contextlib.redirect_stdout(io.StringIO()):
            for block in blocks[:20]:
                offline_engine.accept(block)
            offline_engine.finalize()
        offline = True
        detail = "no socket opened"
    except AssertionError as exc:  # our own guard
        offline = False
        detail = str(exc)
    finally:
        socket.socket.connect = real_connect
        socket.socket.connect_ex = real_connect_ex
        socket.create_connection = real_create
    check("decoding runs without opening any socket", offline,
          detail if attempts else "")

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All speech-engine checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
