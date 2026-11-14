"""
notch.audio.speech — local streaming speech recognition behind one small interface.

Two backends, both fully offline:

  * sherpa-onnx streaming zipformer (preferred). In
    ``tests/bench_speech_engines.py`` it decodes roughly three times faster per
    second of audio than the Vosk small model on the same machine, at the same
    order of accuracy, and its endpoint rules are configurable.
  * Vosk / Kaldi (fallback), used when the sherpa model folder is missing so an
    existing installation keeps working.

A Silero VAD (through sherpa-onnx) segments utterances: it decides when the
speaker has stopped, which is the pause the reader waits through, and it lets
the worker skip decoding while nobody is talking. Nothing here opens a socket,
reads an environment token or calls a service: audio is decoded by a local
model and the text never leaves the process.
"""

import json
import os
import sys
import threading
from typing import Optional, Tuple

import numpy as np

SAMPLE_RATE = 16_000

# How long the speaker must be silent before an utterance is closed. 0.75 s is
# the same window the RMS gate used, so the reader's feel does not change with
# the engine.
TRAILING_SILENCE = 0.75

# Vosk layout: model/conf + model/am (and the graph next to them).
VOSK_MARKERS = ("conf", "am")

# One loaded engine, leased to the single worker that is listening. See
# create_recognizer for why it is kept between workers.
_cache_lock = threading.Lock()
_cached_key: Optional[Tuple[str, int]] = None
_cached_engine: Optional["StreamRecognizer"] = None
_cached_busy = False
_drop_requested = False


def _log(message: str):
    print(f"[Notch speech] {message}", flush=True)


class StreamRecognizer:
    """Common surface for a streaming recogniser.

    ``accept`` returns the text heard so far and whether the engine closed the
    utterance itself. An empty final means "nothing was said"; callers drop it.
    """

    name = "none"

    def load(self):
        raise NotImplementedError

    def accept(self, pcm: bytes) -> Tuple[str, bool]:
        raise NotImplementedError

    def finalize(self) -> str:
        """Close the current utterance and start a new one."""
        raise NotImplementedError

    def reset(self):
        """Drop any partial utterance and start fresh."""
        raise NotImplementedError

    def close(self):
        pass


def _looks_like_sherpa(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    if not os.path.exists(os.path.join(path, "tokens.txt")):
        return False
    return any(name.endswith(".onnx") and "encoder" in name
               for name in os.listdir(path))


def find_sherpa_model_dir(model_dir: str) -> Optional[str]:
    """Locate a streaming zipformer export inside ``model_dir``.

    Accepts either the export itself or a folder holding it, which is how the
    archives are extracted (``model/sherpa`` or a release-named subfolder).
    """
    if not model_dir or not os.path.isdir(model_dir):
        return None
    for candidate in (model_dir, os.path.join(model_dir, "sherpa")):
        if _looks_like_sherpa(candidate):
            return candidate
    try:
        for name in sorted(os.listdir(model_dir)):
            subfolder = os.path.join(model_dir, name)
            if _looks_like_sherpa(subfolder):
                return subfolder
    except OSError:
        pass
    return None


def _pick_onnx(folder: str, kind: str) -> Optional[str]:
    """The int8 export of one model part, when the archive carries both."""
    try:
        names = [name for name in os.listdir(folder)
                 if name.endswith(".onnx") and kind in name]
    except OSError:
        return None
    if not names:
        return None
    int8 = [name for name in names if "int8" in name]
    return os.path.join(folder, (int8 or names)[0])


class SherpaEngine(StreamRecognizer):
    """A sherpa-onnx streaming zipformer transducer."""

    def __init__(self, model_dir: str, num_threads: int = 2):
        self.model_dir = model_dir
        self.num_threads = num_threads
        self.name = "sherpa-onnx zipformer"
        self._recognizer = None
        self._stream = None

    def load(self):
        import sherpa_onnx

        encoder = _pick_onnx(self.model_dir, "encoder")
        decoder = _pick_onnx(self.model_dir, "decoder")
        joiner = _pick_onnx(self.model_dir, "joiner")
        tokens = os.path.join(self.model_dir, "tokens.txt")
        if not (encoder and decoder and joiner and os.path.exists(tokens)):
            raise RuntimeError(f"incomplete sherpa model in {self.model_dir}")

        self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=tokens,
            encoder=encoder,
            decoder=decoder,
            joiner=joiner,
            num_threads=self.num_threads,
            sample_rate=SAMPLE_RATE,
            feature_dim=80,
            decoding_method="greedy_search",
            enable_endpoint_detection=True,
            # The VAD normally closes the utterance; these rules only matter
            # when the VAD model is absent, so they match its 0.75 s window.
            rule1_min_trailing_silence=TRAILING_SILENCE,
            rule2_min_trailing_silence=TRAILING_SILENCE,
            rule3_min_utterance_length=20.0,
            provider="cpu",
        )
        self._stream = self._recognizer.create_stream()

    def accept(self, pcm: bytes) -> Tuple[str, bool]:
        if self._recognizer is None:
            return "", False
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        self._stream.accept_waveform(SAMPLE_RATE, samples)
        while self._recognizer.is_ready(self._stream):
            self._recognizer.decode_stream(self._stream)
        text = self._recognizer.get_result(self._stream).strip()
        if self._recognizer.is_endpoint(self._stream):
            self._recognizer.reset(self._stream)
            return text, True
        return text, False

    def finalize(self) -> str:
        if self._recognizer is None:
            return ""
        text = self._recognizer.get_result(self._stream).strip()
        self._recognizer.reset(self._stream)
        return text

    def reset(self):
        if self._recognizer is not None:
            self._recognizer.reset(self._stream)


class VoskEngine(StreamRecognizer):
    """Kaldi/Vosk, kept so a pre-sherpa model folder still works."""

    def __init__(self, model_dir: str):
        self.model_dir = model_dir
        self.name = "vosk"
        self._model = None
        self._recognizer = None

    def load(self):
        from vosk import KaldiRecognizer, Model

        self._model = Model(self.model_dir)
        self._recognizer = KaldiRecognizer(self._model, SAMPLE_RATE)

    def accept(self, pcm: bytes) -> Tuple[str, bool]:
        if self._recognizer is None:
            return "", False
        if self._recognizer.AcceptWaveform(pcm):
            text = json.loads(self._recognizer.Result()).get("text", "").strip()
            return text, True
        partial = json.loads(self._recognizer.PartialResult()).get("partial", "")
        return partial.strip(), False

    def finalize(self) -> str:
        if self._recognizer is None:
            return ""
        text = json.loads(self._recognizer.FinalResult()).get("text", "").strip()
        self._new_recognizer()
        return text

    def reset(self):
        self._new_recognizer()

    def _new_recognizer(self):
        if self._model is None:
            return
        from vosk import KaldiRecognizer

        self._recognizer = KaldiRecognizer(self._model, SAMPLE_RATE)


class SpeechDetector:
    """Silero VAD: is the speaker talking, and has the utterance ended?

    ``accept`` returns two answers because they mean different things. The
    speech flag follows the microphone within a block, which is what gates the
    recogniser and draws the level. The utterance-end flag is late by the
    trailing-silence window, which is what separates a breath from the end of
    a sentence, and it is the signal the reader closes the utterance on.
    """

    name = "silero-vad"

    def __init__(self, model_path: str,
                 trailing_silence: float = TRAILING_SILENCE):
        self.model_path = model_path
        self.trailing_silence = trailing_silence
        self._vad = None

    def load(self):
        import sherpa_onnx

        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = self.model_path
        config.silero_vad.threshold = 0.5
        config.silero_vad.min_silence_duration = self.trailing_silence
        config.silero_vad.min_speech_duration = 0.15
        config.silero_vad.window_size = 512
        config.sample_rate = SAMPLE_RATE
        self._vad = sherpa_onnx.VoiceActivityDetector(
            config, buffer_size_in_seconds=20)

    def accept(self, pcm: bytes) -> Tuple[bool, bool]:
        """(speech now, utterance just ended) for one block."""
        if self._vad is None:
            return False, False
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        self._vad.accept_waveform(samples)
        # A finished segment is the endpoint; its samples are the recogniser's
        # business, so it is dropped to keep the buffer small.
        ended = False
        while not self._vad.empty():
            self._vad.pop()
            ended = True
        return bool(self._vad.is_speech_detected()), ended

    def reset(self):
        if self._vad is not None:
            self._vad.reset()


def find_vad_model(model_dir: str) -> Optional[str]:
    for candidate in (
        os.path.join(model_dir, "silero_vad.onnx"),
        os.path.join(model_dir, "sherpa", "silero_vad.onnx"),
    ):
        if os.path.exists(candidate):
            return candidate
    return None


def create_recognizer(model_dir: str,
                      num_threads: int = 2) -> Optional[StreamRecognizer]:
    """Build the best available local recogniser for ``model_dir``.

    sherpa-onnx is preferred; Vosk is the fallback so an existing model folder
    keeps working. Returns None when neither model is present, which the worker
    reports as voice matching being unavailable.

    Loading the zipformer model costs seconds, and the reader stops and starts
    the audio worker whenever it switches between the pill and the editor, so
    one loaded engine is kept and handed out again when the previous worker has
    released it. The lease means a second worker never shares a recogniser with
    a worker that is still running: it builds its own instead.
    """
    global _cached_key, _cached_engine, _cached_busy

    key = (os.path.abspath(model_dir or ""), num_threads)
    with _cache_lock:
        if (_cached_engine is not None and _cached_key == key
                and not _cached_busy):
            engine = _cached_engine
            try:
                engine.reset()
            except Exception:
                _cached_engine = None
            else:
                _cached_busy = True
                _log("reusing the loaded recogniser")
                return engine

    engine = _build_recognizer(model_dir, num_threads)
    if engine is None:
        return None

    with _cache_lock:
        # Keep the newest model only, and never evict one in use.
        if _cached_engine is None or not _cached_busy:
            _cached_key = key
            _cached_engine = engine
            _cached_busy = True
    return engine


def request_drop_recognizer() -> None:
    """Free the loaded model once the worker holding it has exited.

    Switching to Manual mode is not a pause: nothing is going to decode
    until the reader goes back to Voice, and the resident model is ~150 MB.
    Dropping it costs the next Voice start its load again (~4 s), which is
    the trade this exists to make explicit. A worker that is still running
    finishes first; dropping the reference underneath it would free nothing
    (the worker holds its own) and only lose the reuse it was counting on.
    """
    global _drop_requested, _cached_engine, _cached_key
    with _cache_lock:
        if _cached_busy:
            _drop_requested = True
            return
        _drop_requested = False
        _cached_engine = None
        _cached_key = None


def release_recognizer(engine: Optional[StreamRecognizer]):
    """Let a finished worker's engine be reused by the next one."""
    global _cached_busy, _cached_engine, _cached_key, _drop_requested
    if engine is None:
        return
    with _cache_lock:
        if engine is _cached_engine:
            _cached_busy = False
            if _drop_requested:
                _drop_requested = False
                _cached_engine = None
                _cached_key = None


def _build_recognizer(model_dir: str, num_threads: int
                      ) -> Optional[StreamRecognizer]:
    sherpa_dir = find_sherpa_model_dir(model_dir)
    if sherpa_dir is not None:
        engine = SherpaEngine(sherpa_dir, num_threads=num_threads)
        try:
            engine.load()
            _log(f"using {engine.name} from {sherpa_dir}")
            return engine
        except Exception as exc:  # a broken model must not kill the fallback
            _log(f"sherpa-onnx unavailable: {exc}")
            try:
                _log("".join(__import__("traceback").format_exc()))
            except Exception:
                pass
    if model_dir and os.path.isdir(model_dir) and all(
            os.path.isdir(os.path.join(model_dir, marker))
            for marker in VOSK_MARKERS):
        engine = VoskEngine(model_dir)
        try:
            engine.load()
            _log(f"using {engine.name} from {model_dir}")
            return engine
        except Exception as exc:
            _log(f"vosk unavailable: {exc}")
            if getattr(sys, "frozen", False):
                try:
                    import traceback
                    _log("".join(traceback.format_exc()))
                except Exception:
                    pass
    return None


def create_vad(model_dir: str,
               trailing_silence: float = TRAILING_SILENCE
               ) -> Optional[SpeechDetector]:
    """Silero VAD, when both the model and sherpa-onnx are available."""
    path = find_vad_model(model_dir)
    if path is None:
        return None
    detector = SpeechDetector(path, trailing_silence=trailing_silence)
    try:
        detector.load()
        return detector
    except Exception as exc:
        _log(f"voice activity detection unavailable: {exc}")
        return None
