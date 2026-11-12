"""
notch.audio.worker — Microphone audio worker running on a QThread.

Reads raw PCM from the microphone via sounddevice and emits volume levels
for voice-activity-driven scrolling. Optionally uses local speech recognition
for word alignment; volume detection always works independently.

The recogniser and the voice-activity detector both come from
``notch.audio.speech``, which decodes on this machine only. A Silero VAD segments
utterances, so an utterance is closed when the speaker actually stops and
silence never reaches the recogniser.
"""

import os
import queue
import sys
import traceback
from collections import deque
from typing import Optional

import numpy as np
import sounddevice as sd

from PyQt5.QtCore import QThread, pyqtSignal


class AudioWorker(QThread):
    """Continuously streams microphone audio and emits volume levels."""

    # ── Signals ──────────────────────────────────────────────────────
    volume_level_signal = pyqtSignal(float)  # RMS volume 0.0 – 1.0
    speaking_signal     = pyqtSignal(bool)   # True = speaking, False = silent
    speech_text_signal  = pyqtSignal(str, bool)  # text, is_final
    error_signal        = pyqtSignal(str)    # fatal errors
    status_signal       = pyqtSignal(str)    # status messages

    SAMPLE_RATE = 16_000  # 16 kHz mono
    # 100 ms of audio per recogniser step. The old 250 ms block put up to a
    # quarter second between the speaker and the live highlight; 100 ms is
    # still comfortable for the recogniser and keeps each step well inside
    # real time on the test machine.
    BLOCK_SAMPLES = 1_600
    # How long a pause must last before the level meter and VAD call it
    # silence. Expressed in time, not frames, so changing the block size
    # cannot silently change the feel of the reader.
    SILENCE_SECONDS = 0.75
    # Blocks kept from before the VAD confirmed speech. The detector needs
    # ~150 ms of speech before it reports any, so without this buffer the
    # first word of every sentence would be clipped from the recogniser.
    ONSET_SECONDS = 0.4

    @staticmethod
    def _resolve_model_dir(model_path: str) -> str:
        """
        Make model loading resilient to common zip-extract layouts.

        Expected structure (directly in model_path) for the Vosk fallback:
          model_path\\conf\\...
          model_path\\am\\...

        But the data is sometimes in a subfolder. If we detect that,
        we automatically use the inner folder. A sherpa-onnx model is found
        by notch.audio.speech wherever it sits under the same root.
        """
        try:
            if not model_path:
                return model_path
            if not os.path.isdir(model_path):
                return model_path

            # If it already looks like the expected data folder, keep it.
            if os.path.isdir(os.path.join(model_path, "conf")) and os.path.isdir(os.path.join(model_path, "am")):
                return model_path

            # Look one directory down for a folder that looks like a model.
            subdirs = [
                os.path.join(model_path, name)
                for name in os.listdir(model_path)
                if os.path.isdir(os.path.join(model_path, name))
            ]
            for sd_path in subdirs:
                if os.path.isdir(os.path.join(sd_path, "conf")) and os.path.isdir(os.path.join(sd_path, "am")):
                    return sd_path
        except Exception:
            pass
        return model_path

    @staticmethod
    def _log_frozen_error(message: str):
        """
        In the windowed PyInstaller build, stdout isn't visible.
        Write key failures to notch.log in the exe install directory for debugging.
        """
        try:
            if getattr(sys, "frozen", False):
                log_dir = os.path.dirname(sys.executable)
            else:
                log_dir = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
                log_dir = os.path.join(log_dir, "Notch")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "notch.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(message.rstrip() + "\n")
        except Exception:
            pass

    def __init__(self, device_index: Optional[int] = None,
                 noise_gate: float = 0.01, input_gain: float = 2.0,
                 model_path: str = "model", parent=None):
        super().__init__(parent)
        self.device_index = device_index
        self.noise_gate = noise_gate
        self.input_gain = max(0.1, min(input_gain, 10.0))
        self.model_path = model_path
        self._running = False
        self._audio_queue: queue.Queue = queue.Queue()

        # Speech state tracking
        self._is_speaking = False
        self._silence_frames = 0
        self._block_seconds = self.BLOCK_SAMPLES / float(self.SAMPLE_RATE)
        # Consecutive silent blocks before we declare "not speaking". Derived
        # from the duration so the 100 ms cadence keeps the same 0.75 s feel.
        self._silence_threshold = max(
            1, int(round(self.SILENCE_SECONDS / self._block_seconds)))
        # Mute: do not emit speech (overlay toggles); reset recognizer on unmute to clear backlog
        self._muted = False
        self._reset_recognizer_requested = False

        # Recognition state (assigned in run so a load failure leaves the
        # volume meter working on its own).
        self._engine = None          # notch.audio.speech.StreamRecognizer
        self._vad = None             # notch.audio.speech.SpeechDetector
        self._last_partial = ""
        self._onset_blocks = max(
            1, int(round(self.ONSET_SECONDS / self._block_seconds)))
        self._pending = deque(maxlen=self._onset_blocks)

    # ── Public helpers ───────────────────────────────────────────────
    def set_muted(self, muted: bool):
        self._muted = bool(muted)
        if not self._muted:
            self._reset_recognizer_requested = True  # clear backlog when unmuting

    def request_reset_recognizer(self):
        """Call when unmuting so recognizer state is cleared and we don't get backlog text."""
        self._reset_recognizer_requested = True

    def set_device(self, index: Optional[int]):
        self.device_index = index

    def set_noise_gate(self, value: float):
        self.noise_gate = max(0.0, min(value, 1.0))

    def set_input_gain(self, value: float):
        self.input_gain = max(0.1, min(value, 10.0))

    def stop(self):
        self._running = False

    # ── Recognition helpers ──────────────────────────────────────────
    def _reset_recognition(self):
        """Forget the current utterance and any buffered onset audio."""
        self._pending.clear()
        self._last_partial = ""
        if self._engine is not None:
            try:
                self._engine.reset()
            except Exception:
                pass
        if self._vad is not None:
            try:
                self._vad.reset()
            except Exception:
                pass

    def _accept_block(self, data: bytes):
        """Feed one block to the recogniser and emit what it heard."""
        try:
            text, is_final = self._engine.accept(data)
        except Exception:
            return
        if not text:
            if is_final:
                self._last_partial = ""
            return
        if is_final:
            print(f"[Notch audio] (final): {text!r}", flush=True)
            self.speech_text_signal.emit(text, True)
            self._last_partial = ""
            return
        if text != self._last_partial:
            print(f"[Notch audio] (partial): {text!r}", flush=True)
            self.speech_text_signal.emit(text, False)
            self._last_partial = text

    def _close_utterance(self):
        """The speaker stopped: confirm the words heard and start fresh."""
        self._pending.clear()
        text = ""
        if self._engine is not None:
            try:
                text = self._engine.finalize()
            except Exception:
                text = ""
        self._last_partial = ""
        if text:
            print(f"[Notch audio] (final): {text!r}", flush=True)
            self.speech_text_signal.emit(text, True)

    def _feed_recognizer(self, data: bytes, speech: Optional[bool]):
        """Send a block to the recogniser, gated by the VAD when present."""
        if self._engine is None:
            return
        if speech is None:
            self._accept_block(data)  # no VAD: the engine sees everything
            return
        if speech:
            # Replay the blocks captured before the detector confirmed speech,
            # so the first word of the sentence is decoded too.
            while self._pending:
                self._accept_block(self._pending.popleft())
            self._accept_block(data)
            return
        # Nobody is talking: remember a short window of audio in case a word
        # started before the detector was sure, and skip the decode cost.
        self._pending.append(data)

    # ── sounddevice callback (called from audio thread) ──────────────
    def _audio_callback(self, indata: np.ndarray, frames: int,
                        time_info, status):
        if status:
            pass  # drop-outs are non-fatal
        self._audio_queue.put(bytes(indata))

    # ── Main thread loop ─────────────────────────────────────────────
    def run(self):
        self._running = True
        print("[Notch] Audio worker thread started", flush=True)
        if getattr(sys, "frozen", False):
            self._log_frozen_error("[Notch] Audio worker started")

        # Open microphone stream
        try:
            dev = "default" if self.device_index is None else f"device #{self.device_index}"
            print(f"[Notch] Opening microphone ({dev})...", flush=True)
            stream = sd.RawInputStream(
                samplerate=self.SAMPLE_RATE,
                blocksize=self.BLOCK_SAMPLES,  # 100 ms chunks
                dtype="int16",
                channels=1,
                device=self.device_index,
                callback=self._audio_callback,
            )
            stream.start()
            print("[Notch] Microphone opened — listening", flush=True)
            self.status_signal.emit("Listening...")
        except Exception as exc:
            print(f"[Notch] Microphone error: {exc}", flush=True)
            self.error_signal.emit(f"Microphone error:\n{exc}")
            return

        # Optional speech recogniser (use instance attrs so we can reset on
        # unmute). notch.audio.speech picks sherpa-onnx when its model is present
        # and falls back to Vosk for an existing model folder.
        try:
            from notch.audio.speech import create_recognizer, create_vad

            model_dir = self._resolve_model_dir(self.model_path)
            # The model load costs seconds on a cold start; say so instead of
            # leaving the pill looking ready while nothing is being heard.
            self.status_signal.emit("Preparing voice match...")
            self._engine = create_recognizer(model_dir)
            if self._engine is not None:
                self._vad = create_vad(model_dir)
                print("[Notch] Speech recognition on "
                      f"({self._engine.name})", flush=True)
                if getattr(sys, "frozen", False):
                    self._log_frozen_error(f"Speech data loaded from: {model_dir}")
            else:
                print("[Notch] Speech recognition off (data folder not found)", flush=True)
                if getattr(sys, "frozen", False):
                    self._log_frozen_error(f"Speech data NOT found at: {model_dir}")
                self.status_signal.emit("Speech recognition unavailable — voice match off")
                self.error_signal.emit(
                    f"Speech data folder not found at:\n{model_dir}\n\n"
                    "Voice-follow and word highlighting need the data folder "
                    "(model\\sherpa, or a Vosk model\\conf + model\\am). "
                    "If you installed via the installer, reinstall and ensure the full package was used."
                )
        except Exception as e:
            print(f"[Notch] Speech recognition load failed: {e}", flush=True)
            traceback.print_exc()
            try:
                self._log_frozen_error("Speech load failed:\n" + "".join(traceback.format_exc()))
            except Exception:
                pass
            self.error_signal.emit(
                f"Speech recognition failed to load:\n{e}\n\n"
                "Check that the data folder exists next to the app and contains the required files."
            )
            self._engine = None
            self._vad = None

        # Processing loop — volume detection + VAD + optional speech recognition
        try:
            while self._running:
                try:
                    data = self._audio_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                # ── Reset recognizer (e.g. on unmute to clear backlog) ─
                if self._reset_recognizer_requested and self._engine is not None:
                    self._reset_recognizer_requested = False
                    self._reset_recognition()
                    # Drain queued audio from during mute so we don't feed stale audio
                    drain = max(1, int(round(3.0 / self._block_seconds)))
                    for _ in range(drain):  # ~3 s, whatever the block size
                        try:
                            self._audio_queue.get_nowait()
                        except queue.Empty:
                            break

                # ── Volume metering ──────────────────────────────────
                samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                rms = np.sqrt(np.mean(samples ** 2)) / 32768.0
                rms = min(rms * self.input_gain, 1.0)
                level = min(rms * 5.0, 1.0)  # amplified for UI
                self.volume_level_signal.emit(level)

                # ── Voice activity ───────────────────────────────────
                # Silero VAD when available: it hears speech through room
                # noise that an RMS gate mistakes for a voice, and its
                # trailing-silence window is the utterance boundary.
                speech: Optional[bool] = None
                utterance_ended = False
                if self._vad is not None and not self._muted:
                    try:
                        speech, utterance_ended = self._vad.accept(data)
                    except Exception:
                        speech, utterance_ended = None, False

                if speech is not None:
                    if speech:
                        self._silence_frames = 0
                        if not self._is_speaking:
                            self._is_speaking = True
                            self.speaking_signal.emit(True)
                    else:
                        self._silence_frames += 1
                        # The end of the pause, not the first silent block:
                        # the glow stays lit through a breath mid-sentence.
                        if self._is_speaking and (
                                utterance_ended
                                or self._silence_frames >= self._silence_threshold):
                            self._is_speaking = False
                            self.speaking_signal.emit(False)
                    if utterance_ended and not self._muted:
                        self._close_utterance()
                elif rms >= self.noise_gate:
                    # No VAD model: the RMS gate stands in for it.
                    self._silence_frames = 0
                    if not self._is_speaking:
                        self._is_speaking = True
                        self.speaking_signal.emit(True)
                else:
                    self._silence_frames += 1
                    if self._is_speaking and self._silence_frames >= self._silence_threshold:
                        self._is_speaking = False
                        self.speaking_signal.emit(False)

                # ── Speech recognition (optional) ───────────────────
                if self._engine is not None and not self._muted:
                    self._feed_recognizer(data, speech)
        finally:
            stream.stop()
            stream.close()
            try:
                from notch.audio.speech import release_recognizer
                release_recognizer(self._engine)
            except Exception:
                pass
