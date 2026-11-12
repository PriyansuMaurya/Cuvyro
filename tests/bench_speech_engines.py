"""Offline benchmark for candidate speech engines and voice-activity detection.

Everything here runs on the local machine: the audio fixtures are synthesised
with the Windows SAPI voices already installed (offline), and every candidate
decodes locally. No audio, transcript or script leaves the machine.

What is measured, per candidate, on the same fixtures:

  * load time (cold import + model construction),
  * throughput as real-time factor, decoding the whole file as fast as
    possible, so the number is comparable across models,
  * words behind the speaker during a real-time pass (audio fed in the same
    100 ms blocks the app uses, paced in wall-clock time),
  * tail latency: how long after the last speech block the final result
    appears, which is exactly the pause the reader waits through,
  * word error rate on the final transcript,
  * for the VAD candidates: CPU cost per block and how long after speech
    stops the detector calls silence.

Run:

    .venv/Scripts/python.exe tests/bench_speech_engines.py
    .venv/Scripts/python.exe tests/bench_speech_engines.py --engine sherpa20m
    .venv/Scripts/python.exe tests/bench_speech_engines.py --vad-only

The fixture audio is written to %TEMP%\\notch-bench by default.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import wave
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
os.chdir(REPO_ROOT)

DEFAULT_MODEL_ROOT = os.path.join(REPO_ROOT, "bench-models")
DEFAULT_VOSK_MODEL = os.path.join(REPO_ROOT, "model")

SAMPLE_RATE = 16_000
BLOCK_SAMPLES = 1_600  # 100 ms, the block size the app feeds the recogniser

# Sentences are ordinary teleprompter copy: numbers, contractions, names and
# clause boundaries, so a candidate is judged on the text a reader really
# sees rather than on a clean single clause.
SENTENCES = [
    "Good evening, and thanks for joining.",
    "Tonight I want to show you three small things, not thirty.",
    "First, how the board reads a city block.",
    "Second, why the harbour plan changed.",
    "Third, what we need from you by Friday.",
    "Everything here is our own material, written for this rehearsal.",
    "The council approved 14 of the 19 amendments last Tuesday.",
    "We didn't expect the second reading to take that long.",
    "Katherine, could you walk us through the cost table again?",
    "Roughly 60 per cent of the delay came from one supplier.",
    "That isn't a rounding error, it's a scheduling problem.",
    "If we move the review to March, the numbers still hold.",
    "So the question is simple: what do we ship, and when?",
    "Thank you, and I'll take questions at the end.",
]

FIXTURE_VOICES = [
    ("david-us", "Microsoft David Desktop", 0, "normal"),
    ("hazel-gb", "Microsoft Hazel Desktop", 0, "normal"),
    ("zira-us-slow", "Microsoft Zira Desktop", -3, "slow"),
    ("zira-us-fast", "Microsoft Zira Desktop", 3, "fast"),
]

SILENCE_TAIL_SECONDS = 0.7


# ── Fixtures ────────────────────────────────────────────────────────────────


def _synthesise(text: str, voice: str, rate: int, path: str) -> bool:
    """Write one 16 kHz mono 16-bit WAV with the system speech voice.

    System.Speech is part of Windows itself, so this is an offline step: the
    benchmark never needs a network or a cloud TTS account.
    """
    escaped = text.replace('"', '`"')
    script = f"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice("{voice}")
$s.Rate = {rate}
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile("{path}", $fmt)
$s.Speak("{escaped}")
$s.Dispose()
"""
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=120,
        )
        return done.returncode == 0 and os.path.exists(path)
    except Exception:
        return False


def _append_silence(src: str, dst: str, seconds: float):
    """Append digital silence so a streaming recogniser can endpoint."""
    with wave.open(src, "rb") as reader:
        params = reader.getparams()
        frames = reader.readframes(reader.getnframes())
    padding = b"\x00\x00" * int(seconds * params.framerate)
    with wave.open(dst, "wb") as writer:
        writer.setparams(params)
        writer.writeframes(frames + padding)


def build_fixtures(fixture_dir: str) -> List[Dict]:
    """Synthesise the sentence corpus once per voice, at 16 kHz mono."""
    os.makedirs(fixture_dir, exist_ok=True)
    fixtures: List[Dict] = []
    for voice_id, voice, rate, pace in FIXTURE_VOICES:
        for index, sentence in enumerate(SENTENCES):
            raw = os.path.join(fixture_dir, f"{voice_id}-{index:02d}-raw.wav")
            path = os.path.join(fixture_dir, f"{voice_id}-{index:02d}.wav")
            if not os.path.exists(path):
                if not _synthesise(sentence, voice, rate, raw):
                    print(f"  ! could not synthesise {voice_id}-{index:02d}")
                    continue
                _append_silence(raw, path, SILENCE_TAIL_SECONDS)
            fixtures.append({
                "id": f"{voice_id}-{index:02d}",
                "text": sentence,
                "path": path,
                "unpadded_path": raw,
                "pace": pace,
            })
    return fixtures


def read_pcm(path: str) -> np.ndarray:
    """Read a fixture as float32 mono at 16 kHz."""
    with wave.open(path, "rb") as reader:
        assert reader.getframerate() == SAMPLE_RATE, reader.getframerate()
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
        raw = reader.readframes(reader.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


# ── Scoring ─────────────────────────────────────────────────────────────────


def normalise_words(text: str) -> List[str]:
    """Lower-case tokens without punctuation, the same view the matcher uses."""
    cleaned = []
    for token in text.lower().split():
        word = "".join(ch for ch in token if ch.isalnum() or ch == "'")
        if word:
            cleaned.append(word)
    return cleaned


def word_error_rate(reference: Sequence[str], hypothesis: Sequence[str]) -> float:
    """Levenshtein distance over words, divided by the reference length."""
    if not reference:
        return 0.0 if not hypothesis else 1.0
    previous = list(range(len(hypothesis) + 1))
    for i, ref in enumerate(reference, start=1):
        current = [i]
        for j, hyp in enumerate(hypothesis, start=1):
            current.append(min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (ref != hyp),
            ))
        previous = current
    return previous[-1] / len(reference)


# ── Engine adapters ─────────────────────────────────────────────────────────
#
# Both adapters expose the same tiny surface as the app's recogniser use:
# feed a 100 ms block, ask for the text so far, and learn when the engine has
# finalised an utterance.


class VoskEngine:
    name = "vosk small en-us 0.15 (current)"

    def __init__(self, model_dir: str):
        self.model_dir = model_dir
        self._recognizer = None
        self._model = None

    def load(self) -> float:
        from vosk import KaldiRecognizer, Model
        start = time.perf_counter()
        self._model = Model(self.model_dir)
        self._recognizer = KaldiRecognizer(self._model, SAMPLE_RATE)
        return time.perf_counter() - start

    def reset(self):
        from vosk import KaldiRecognizer
        self._recognizer = KaldiRecognizer(self._model, SAMPLE_RATE)

    def accept(self, samples: np.ndarray) -> Tuple[str, Optional[str]]:
        data = (samples * 32768.0).astype(np.int16).tobytes()
        if self._recognizer.AcceptWaveform(data):
            text = json.loads(self._recognizer.Result()).get("text", "").strip()
            return text, text
        partial = json.loads(self._recognizer.PartialResult()).get("partial", "").strip()
        return partial, None

    def flush(self) -> str:
        return json.loads(self._recognizer.FinalResult()).get("text", "").strip()


class SherpaEngine:
    """A sherpa-onnx streaming zipformer transducer."""

    def __init__(self, model_dir: str, label: str, threads: int = 2,
                 trailing_silence: float = 0.75):
        self.name = label
        self.model_dir = model_dir
        self.threads = threads
        self.trailing_silence = trailing_silence
        self._recognizer = None
        self._stream = None

    def _files(self) -> Tuple[str, str, str]:
        encoder = decoder = joiner = ""
        for name in os.listdir(self.model_dir):
            if not name.endswith(".onnx"):
                continue
            # int8 weights are the CPU-friendly variant and the ones the app
            # would ship; take them when the archive carries both.
            if "encoder" in name and (not encoder or "int8" in name):
                encoder = name if "int8" in name or not encoder else encoder
            elif "decoder" in name and (not decoder or "int8" in name):
                decoder = name if "int8" in name or not decoder else decoder
            elif "joiner" in name and (not joiner or "int8" in name):
                joiner = name if "int8" in name or not joiner else joiner
        join = lambda name: os.path.join(self.model_dir, name)
        return join(encoder), join(decoder), join(joiner)

    def load(self) -> float:
        import sherpa_onnx
        encoder, decoder, joiner = self._files()
        start = time.perf_counter()
        self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=os.path.join(self.model_dir, "tokens.txt"),
            encoder=encoder,
            decoder=decoder,
            joiner=joiner,
            num_threads=self.threads,
            sample_rate=SAMPLE_RATE,
            feature_dim=80,
            decoding_method="greedy_search",
            enable_endpoint_detection=True,
            # The reader's pause: the app's own silence window is 0.75 s, so
            # the engine is judged with the same end-of-utterance rule it
            # would run with.
            rule1_min_trailing_silence=self.trailing_silence,
            rule2_min_trailing_silence=self.trailing_silence,
            rule3_min_utterance_length=20.0,
            provider="cpu",
        )
        self._stream = self._recognizer.create_stream()
        return time.perf_counter() - start

    def reset(self):
        self._stream = self._recognizer.create_stream()

    def accept(self, samples: np.ndarray) -> Tuple[str, Optional[str]]:
        self._stream.accept_waveform(SAMPLE_RATE, samples)
        while self._recognizer.is_ready(self._stream):
            self._recognizer.decode_stream(self._stream)
        text = self._recognizer.get_result(self._stream).strip()
        if self._recognizer.is_endpoint(self._stream):
            self._recognizer.reset(self._stream)
            return text, text
        return text, None

    def flush(self) -> str:
        tail = np.zeros(int(0.3 * SAMPLE_RATE), dtype=np.float32)
        self._stream.accept_waveform(SAMPLE_RATE, tail)
        self._stream.input_finished()
        while self._recognizer.is_ready(self._stream):
            self._recognizer.decode_stream(self._stream)
        return self._recognizer.get_result(self._stream).strip()


def engine_catalogue(model_root: str, threads: int = 2) -> Dict[str, callable]:
    return {
        "vosk": lambda: VoskEngine(DEFAULT_VOSK_MODEL),
        "sherpa20m": lambda: SherpaEngine(
            os.path.join(model_root, "sherpa-onnx-streaming-zipformer-en-20M-2023-02-17"),
            "sherpa-onnx zipformer en 20M int8", threads=threads),
        "sherpa2023": lambda: SherpaEngine(
            os.path.join(model_root, "sherpa-onnx-streaming-zipformer-en-2023-06-26"),
            f"sherpa-onnx zipformer en 2023-06-26 int8 ({threads} threads)",
            threads=threads),
    }


# ── Measurements ────────────────────────────────────────────────────────────


def blocks(audio: np.ndarray) -> List[np.ndarray]:
    return [audio[i:i + BLOCK_SAMPLES]
            for i in range(0, len(audio), BLOCK_SAMPLES)]


def measure_throughput(engine, fixtures: List[Dict]) -> Dict:
    """Decode every fixture as fast as possible: pure model throughput."""
    engine.load()
    audio_seconds = 0.0
    cpu_seconds = 0.0
    for fixture in fixtures:
        audio = read_pcm(fixture["path"])
        audio_seconds += len(audio) / SAMPLE_RATE
        engine.reset()
        start = time.perf_counter()
        for block in blocks(audio):
            engine.accept(block)
        engine.flush()
        cpu_seconds += time.perf_counter() - start
    return {
        "throughput_audio_s": round(audio_seconds, 2),
        "throughput_cpu_s": round(cpu_seconds, 2),
        "rtf": round(audio_seconds / cpu_seconds, 2) if cpu_seconds else 0.0,
        "cpu_ms_per_audio_s": round(cpu_seconds * 1000.0 / audio_seconds, 1)
        if audio_seconds else 0.0,
    }


def measure_accuracy(engine, fixtures: List[Dict]) -> Dict:
    """Decode at full speed and score the transcript against the script.

    Segments are concatenated: a streaming engine closes an utterance at every
    pause, and the reader follows across all of them, so scoring only the last
    segment would grade the tail of the sentence rather than the read.
    """
    errors = []
    per_fixture = []
    for fixture in fixtures:
        audio = read_pcm(fixture["path"])
        engine.reset()
        heard: List[str] = []
        for block in blocks(audio):
            _, done = engine.accept(block)
            if done:
                heard.append(done)
        tail = engine.flush()
        if tail:
            heard.append(tail)
        final = " ".join(heard)
        reference = normalise_words(fixture["text"])
        hypothesis = normalise_words(final)
        error = word_error_rate(reference, hypothesis)
        errors.append(error)
        per_fixture.append({"id": fixture["id"], "wer": round(error, 3),
                            "heard": final, "said": fixture["text"]})
    return {
        "wer_mean": round(float(np.mean(errors)), 3) if errors else 1.0,
        "wer_worst": round(float(np.max(errors)), 3) if errors else 1.0,
        "wer_detail": per_fixture,
    }


def measure_realtime(engine, fixtures: List[Dict], timeout: float = 3.0) -> Dict:
    """Feed blocks in wall-clock time and watch what the reader would see.

    The reader only needs the words the speaker has just said, so the two
    numbers that matter are how many words behind the audio the recognition
    sits, and how long the final takes after the speech stops. The decode
    time of a single 100 ms block is measured too: a block that costs more
    than the block itself makes the loop fall behind and the scroll stutter.
    """
    lags: List[float] = []
    tails: List[float] = []
    block_costs: List[float] = []
    for fixture in fixtures:
        audio = read_pcm(fixture["path"])
        engine.reset()
        total_words = len(normalise_words(fixture["text"]))
        start = time.perf_counter()
        fed = 0
        final_at = None
        committed = 0
        for index, block in enumerate(blocks(audio)):
            deadline = start + (index + 1) * (BLOCK_SAMPLES / SAMPLE_RATE)
            remaining = deadline - time.perf_counter()
            if remaining > 0:
                time.sleep(remaining)
            fed += len(block)
            decode_start = time.perf_counter()
            partial, done = engine.accept(block)
            block_costs.append(time.perf_counter() - decode_start)
            if done:
                committed += len(normalise_words(done))
                final_at = time.perf_counter()
            heard = committed + len(normalise_words(partial))
            expected = total_words * min(1.0, fed / len(audio))
            lags.append(expected - heard)
        if final_at is None or committed < total_words:
            # The microphone keeps streaming silence after a sentence; keep
            # emulating that until the engine finalises or the budget runs out.
            last_speech_at = time.perf_counter()
            silence = np.zeros(BLOCK_SAMPLES, dtype=np.float32)
            while time.perf_counter() - last_speech_at < timeout:
                time.sleep(BLOCK_SAMPLES / SAMPLE_RATE)
                partial, done = engine.accept(silence)
                if done:
                    committed += len(normalise_words(done))
                    final_at = time.perf_counter()
                    if committed >= total_words:
                        break
            if final_at is None:
                final_at = time.perf_counter()
        tails.append(final_at - start - len(audio) / SAMPLE_RATE)
    costs = np.array(block_costs) if block_costs else np.array([0.0])
    return {
        "words_behind_mean": round(float(np.mean(lags)), 2) if lags else 0.0,
        "words_behind_worst": round(float(np.max(lags)), 2) if lags else 0.0,
        "tail_latency_ms": round(float(np.mean(tails)) * 1000.0, 0),
        "tail_latency_worst_ms": round(float(np.max(tails)) * 1000.0, 0),
        "block_ms_p50": round(float(np.percentile(costs, 50)) * 1000.0, 1),
        "block_ms_p95": round(float(np.percentile(costs, 95)) * 1000.0, 1),
        "block_ms_worst": round(float(np.max(costs)) * 1000.0, 1),
    }


def measure_silence_cost(engine, seconds: float = 10.0) -> Dict:
    """Decode pure silence: what the engine costs while nobody is talking.

    A reader pauses between sentences, so this is the share of the loop that
    a voice-activity gate can take off the recogniser.
    """
    engine.load()
    engine.reset()
    silence = np.zeros(BLOCK_SAMPLES, dtype=np.float32)
    start = time.perf_counter()
    for _ in range(int(seconds * SAMPLE_RATE / BLOCK_SAMPLES)):
        engine.accept(silence)
    cpu = time.perf_counter() - start
    return {
        "silence_cpu_ms_per_audio_s": round(cpu * 1000.0 / seconds, 1),
        "silence_rtf": round(seconds / cpu, 1) if cpu else 0.0,
    }


def measure_vad(model_root: str, fixtures: List[Dict],
                trailing_silence: float = 0.75) -> Dict:
    """Silero VAD through sherpa-onnx, on the same blocks the app feeds.

    Fed from the unpadded TTS output, so ``endpoint_silence_s`` is how much
    silence the detector needed after the last sample before it called the
    utterance over: the pause the reader waits through, and the window in
    which the reader must not move.
    """
    import sherpa_onnx

    path = os.path.join(model_root, "silero_vad.onnx")
    if not os.path.exists(path):
        return {"error": f"missing {path}"}

    def build():
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = path
        config.silero_vad.threshold = 0.5
        config.silero_vad.min_silence_duration = trailing_silence
        config.silero_vad.min_speech_duration = 0.15
        config.silero_vad.window_size = 512
        config.sample_rate = SAMPLE_RATE
        return sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=20)

    attacks: List[float] = []
    endpoints: List[float] = []
    cpu = 0.0
    audio_seconds = 0.0
    for fixture in fixtures:
        audio = read_pcm(fixture.get("unpadded_path") or fixture["path"])
        audio_seconds += len(audio) / SAMPLE_RATE
        vad = build()
        start = time.perf_counter()
        fed = 0
        first_segment_at: Optional[float] = None
        for block in blocks(audio):
            vad.accept_waveform(block)
            fed += len(block)
            if first_segment_at is None and not vad.empty():
                first_segment_at = fed / SAMPLE_RATE
        cpu += time.perf_counter() - start
        attacks.append(first_segment_at if first_segment_at is not None else -1.0)
        # Silence after the last audio sample until the utterance is closed.
        silence = np.zeros(BLOCK_SAMPLES, dtype=np.float32)
        extra = 0
        while vad.empty() and extra < 3.0:
            vad.accept_waveform(silence)
            extra += BLOCK_SAMPLES / SAMPLE_RATE
        endpoints.append(extra)
        while not vad.empty():
            vad.pop()
    return {
        "rtf": round(audio_seconds / cpu, 2) if cpu else 0.0,
        "cpu_ms_per_audio_s": round(cpu * 1000.0 / audio_seconds, 1)
        if audio_seconds else 0.0,
        "endpoint_silence_s": round(float(np.mean(endpoints)), 2),
        "first_segment_at_s": round(float(np.mean(attacks)), 2),
        "trailing_silence_s": trailing_silence,
    }


def measure_rms_gate(fixtures: List[Dict], gate: float = 0.01,
                     silence_seconds: float = 0.75) -> Dict:
    """The gate the app runs today: RMS threshold plus a silence window."""
    silence_threshold = max(1, int(round(silence_seconds / (BLOCK_SAMPLES / SAMPLE_RATE))))
    cpu = 0.0
    audio_seconds = 0.0
    for fixture in fixtures:
        audio = read_pcm(fixture["path"])
        audio_seconds += len(audio) / SAMPLE_RATE
        start = time.perf_counter()
        for block in blocks(audio):
            rms = float(np.sqrt(np.mean(block ** 2)))
            _ = rms >= gate
        cpu += time.perf_counter() - start
    return {
        "cpu_ms_per_audio_s": round(cpu * 1000.0 / audio_seconds, 1)
        if audio_seconds else 0.0,
        "rtf": round(audio_seconds / cpu, 2) if cpu else 0.0,
        "silence_window_s": silence_seconds,
        "silence_threshold_blocks": silence_threshold,
    }


# ── Reporting ───────────────────────────────────────────────────────────────


def report(results: Dict):
    print()
    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    for name, data in results.items():
        if "error" in data:
            print(f"{name}: {data['error']}")
            continue
        if "wer_mean" in data:
            print(f"{name}")
            print(f"  load                {data['load_ms']:.0f} ms")
            print(f"  throughput          {data['rtf']:.2f}x realtime "
                  f"({data['cpu_ms_per_audio_s']:.0f} ms CPU per audio second)")
            print(f"  cost on silence     {data['silence_cpu_ms_per_audio_s']:.0f} "
                  f"ms CPU per audio second")
            if "words_behind_mean" in data:
                print(f"  words behind        {data['words_behind_mean']:.2f} mean, "
                      f"{data['words_behind_worst']:.2f} worst")
                print(f"  final after speech  {data['tail_latency_ms']:.0f} ms mean, "
                      f"{data['tail_latency_worst_ms']:.0f} ms worst")
                print(f"  100 ms block cost   {data['block_ms_p50']:.1f} ms median, "
                      f"{data['block_ms_p95']:.1f} ms p95, "
                      f"{data['block_ms_worst']:.1f} ms worst")
            print(f"  word error rate     {data['wer_mean'] * 100:.1f}% mean, "
                  f"{data['wer_worst'] * 100:.1f}% worst")
        else:
            print(f"{name}: {json.dumps(data)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", default="all",
                        help="vosk | sherpa20m | sherpa2023 | all (comma separated)")
    parser.add_argument("--model-root", default=DEFAULT_MODEL_ROOT)
    parser.add_argument("--fixture-dir",
                        default=os.path.join(tempfile.gettempdir(), "notch-bench"))
    parser.add_argument("--voices", default="all",
                        help="comma separated fixture voice ids, or all")
    parser.add_argument("--limit", type=int, default=0,
                        help="use only the first N fixtures (0 = all)")
    parser.add_argument("--threads", type=int, default=2,
                        help="ONNX inference threads for the sherpa engines")
    parser.add_argument("--repeat", type=int, default=3,
                        help="interleaved rounds; the best round per engine is kept")
    parser.add_argument("--skip-realtime", action="store_true")
    parser.add_argument("--vad-only", action="store_true")
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    print("Building local fixtures with the installed SAPI voices...")
    fixtures = build_fixtures(args.fixture_dir)
    if args.voices != "all":
        wanted = {name.strip() for name in args.voices.split(",")}
        fixtures = [f for f in fixtures if f["id"].rsplit("-", 1)[0] in wanted]
    if args.limit:
        fixtures = fixtures[:args.limit]
    if not fixtures:
        print("No fixtures — SAPI synthesis failed.")
        return 1
    total = sum(len(read_pcm(f["path"])) for f in fixtures) / SAMPLE_RATE
    print(f"{len(fixtures)} fixtures, {total:.1f} s of audio, "
          f"{len(fixtures[0]['text'].split())} words each on average")

    results: Dict[str, Dict] = {}

    if args.vad_only:
        results["silero VAD (sherpa-onnx)"] = measure_vad(args.model_root, fixtures)
        results["RMS gate (current)"] = measure_rms_gate(fixtures)
        report(results)
        if args.json:
            with open(args.json, "w", encoding="utf-8") as handle:
                json.dump(results, handle, indent=2)
            print(f"\nwrote {args.json}")
        return 0

    catalogue = engine_catalogue(args.model_root, threads=args.threads)
    names = list(catalogue) if args.engine == "all" else [
        name.strip() for name in args.engine.split(",")]
    names = [name for name in names if name in catalogue
             and os.path.isdir(catalogue[name]().model_dir)]

    # Rounds are interleaved across engines and the best round per engine is
    # kept. A desktop with a browser open has background load that moves
    # between runs; comparing engines measured seconds apart under different
    # load would blame the load on whichever engine ran second.
    best: Dict[str, Dict] = {}
    for round_index in range(max(1, args.repeat)):
        if args.repeat > 1:
            print(f"\nround {round_index + 1}/{args.repeat}")
        for name in names:
            engine = catalogue[name]()
            load_seconds = engine.load()
            data = {"load_ms": load_seconds * 1000.0}
            data.update(measure_silence_cost(engine))
            data.update(measure_throughput(engine, fixtures))
            data.update(measure_accuracy(engine, fixtures))
            if not args.skip_realtime:
                data.update(measure_realtime(engine, fixtures))
            print(f"  {engine.name}: {data['rtf']:.2f}x realtime")
            previous = best.get(engine.name)
            if previous is None or data["cpu_ms_per_audio_s"] < previous["cpu_ms_per_audio_s"]:
                best[engine.name] = data
    results.update(best)

    results["silero VAD (sherpa-onnx)"] = measure_vad(args.model_root, fixtures)
    results["RMS gate (current)"] = measure_rms_gate(fixtures)

    report(results)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=2)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
