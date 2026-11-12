"""Where the reader will be next, learned on this machine.

The highlighter used to shade only words a recogniser had already reported.
That is always a little late: the microphone waits for a block, the recogniser
waits for enough context, and the final result waits for the end of the
utterance. This module turns those waits into an estimate of the speaker's
next position, so the script can lead the voice instead of trailing it.

Three signals are combined:

  * **the adaptive speaking rate.** A session rate moves towards every new
    word interval, and a prior learned across launches pulls it back when a
    figure has no recent evidence. Fast passages are followed without
    forgetting a slow speaker.
  * **a tiny online timing model.** Five weights predict how long a word
    takes from its length, its punctuation and the recent rate. They are
    updated by one gradient step per observed word. That is enough to learn
    that a word before a full stop is followed by a pause and that long words
    take longer, without a training pass or a bundled dataset.
  * **pause and speech state.** The lead is frozen while the speaker is
    silent, so a pause cannot make the highlight run ahead on its own, and
    the model learns how long this speaker's pauses usually last.

Nothing here touches the scroll. It returns the last word that should be
anticipated; the overlay paints it and keeps its line-gated scroll rules
unchanged, which is what stops a prediction from being able to throw the
reader to another part of the script.

The learned profile is one small JSON file beside the reader's state. It never
contains audio, transcripts or the script: only aggregate timings.
"""

import json
import math
import os
import re
import tempfile
import time
from typing import List, Optional, Sequence

SCHEMA = 1

# A typical presenter, in words per second. The profile replaces this after a
# few sentences, and keeps it as the prior for the next launch.
DEFAULT_WPS = 2.4
MIN_WPS = 0.8          # 48 wpm: a very deliberate delivery
MAX_WPS = 8.0          # 480 wpm: faster than auctioneers

# How far ahead the reader is willing to paint. This is deliberately small:
# the purpose is to cover the recogniser's lag, not to guess a paragraph.
RECOGNITION_LATENCY = 0.45
REACTION_SECONDS = 0.30
MAX_LEAD_SECONDS = 2.6
MAX_LEAD_WORDS = 8

# A gap longer than this is a pause, not a slower word. Rate samples from
# across a pause would teach the model that the speaker suddenly crawls.
PAUSE_SECONDS = 1.2

# The model keeps following a final result that arrives after the voice
# activity detector has already declared silence.
ACTIVE_GRACE_SECONDS = 8.0

# A predicted run is held through a pause, then released. Holding it stops a
# breath from flickering the highlight off and on; releasing it keeps a long
# silence from leaving stale words marked for ever. This is the upper bound:
# the actual hold scales with the speaker's learned pause length.
PREDICTION_HOLD_SECONDS = 4.0

# Anti-flicker: the painted boundary grows at a readable cadence and retreats
# only after it has been wrong for a while.
GROW_INTERVAL = 0.045
RETREAT_DELAY = 0.70
RETREAT_INTERVAL = 0.16

# Persist after this many learned words, and on every state save/close.
SAVE_EVERY_WORDS = 40

# Defaults for the online timing regression:
#   [bias, word length, clause punctuation, sentence punctuation, recent rate]
DEFAULT_WEIGHTS = (0.14, 0.035, 0.10, 0.38, 0.55)
WEIGHT_LIMITS = ((-0.2, 0.8), (-0.2, 0.5), (-0.2, 0.8),
                 (-0.2, 1.5), (0.0, 1.6))
LEARNING_RATE = 0.06
RIDGE = 0.4

_PUNCTUATION = re.compile(r"[^\w']")
_SENTENCE_END = re.compile(r"[.!?][\"')\]]*$")
_CLAUSE_END = re.compile(r"[,;:\u2014\u2013][\"')\]]*$")


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def _number(value, low: float, high: float, fallback: float) -> float:
    """A finite number inside the range, or the fallback.

    The profile is user data on disk and may have been hand-edited. NaN, a
    string or a wild outlier must not reach a prediction.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        return fallback
    return number


class TimingModel:
    """One ridge-regularised gradient step per word.

    The features are intentionally the things a person can observe about the
    next word: how long it is, whether it ends a clause or a sentence, and how
    fast the last few words went. Five weights are enough to adapt the
    prediction to a speaker without turning the reader into a trainer.
    """

    def __init__(self, weights: Optional[Sequence[float]] = None):
        # The profile is user data on disk. A hand-edited number or object in
        # place of the weight list must fall back, not raise on startup.
        values = (list(weights) if isinstance(weights, (list, tuple))
                  else list(DEFAULT_WEIGHTS))
        if len(values) != len(DEFAULT_WEIGHTS):
            values = list(DEFAULT_WEIGHTS)
        self.weights = [
            _clamp(_number(value, low, high, default), low, high)
            for value, (low, high), default in zip(
                values, WEIGHT_LIMITS, DEFAULT_WEIGHTS)
        ]

    @staticmethod
    def features(word: str, recent_seconds: float) -> List[float]:
        raw = (word or "").strip()
        letters = _PUNCTUATION.sub("", raw)
        length = min(len(letters), 14) / 7.0
        clause = 1.0 if _CLAUSE_END.search(raw) else 0.0
        sentence = 1.0 if _SENTENCE_END.search(raw) else 0.0
        return [1.0, length, clause, sentence,
                _clamp(float(recent_seconds), 0.08, 2.0)]

    def predict(self, word: str, recent_seconds: float) -> float:
        features = self.features(word, recent_seconds)
        seconds = sum(weight * value
                      for weight, value in zip(self.weights, features))
        return _clamp(seconds, 0.10, 2.0)

    def observe(self, word: str, recent_seconds: float,
                actual_seconds: float) -> None:
        """Move each weight a little towards this word's real duration."""
        features = self.features(word, recent_seconds)
        predicted = sum(weight * value
                        for weight, value in zip(self.weights, features))
        error = _clamp(actual_seconds - predicted, -1.0, 1.0)
        norm = sum(value * value for value in features) + RIDGE
        for index, value in enumerate(features):
            low, high = WEIGHT_LIMITS[index]
            self.weights[index] = _clamp(
                self.weights[index] + LEARNING_RATE * error * value / norm,
                low, high)

    def as_list(self) -> List[float]:
        return list(self.weights)


class SpeakerProfile:
    """What the reader has learned about this speaker, across launches."""

    def __init__(self, **values):
        self.sessions = int(_number(values.get("sessions"), 0, 1_000_000, 0))
        self.words = int(_number(values.get("words"), 0, 100_000_000, 0))
        self.seconds = _number(values.get("seconds"), 0, 10_000_000, 0.0)
        self.wps = _number(values.get("wps"), MIN_WPS, MAX_WPS, DEFAULT_WPS)
        self.pause_seconds = _number(values.get("pause_seconds"), 0.2, 5.0, 0.9)
        self.pause_count = int(_number(values.get("pause_count"), 0, 1_000_000, 0))
        self.timing_weights = TimingModel(
            values.get("timing_weights")).as_list()

    @property
    def wpm(self) -> float:
        return self.wps * 60.0

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "sessions": self.sessions,
            "words": self.words,
            "seconds": round(self.seconds, 3),
            "wps": round(self.wps, 4),
            "pause_seconds": round(self.pause_seconds, 4),
            "pause_count": self.pause_count,
            "timing_weights": [round(value, 5)
                               for value in self.timing_weights],
        }


class ProfileStore:
    """Atomic persistence for one aggregate speaking profile."""

    def __init__(self, directory: str):
        self.directory = directory
        self.path = os.path.join(directory, "speaking-profile.json")

    def load(self) -> SpeakerProfile:
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return SpeakerProfile()
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            return SpeakerProfile()
        return SpeakerProfile(**data)

    def save(self, profile: SpeakerProfile) -> None:
        os.makedirs(self.directory, exist_ok=True)
        handle, temp_path = tempfile.mkstemp(
            dir=self.directory, suffix=".profile.tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump(profile.to_dict(), out, ensure_ascii=False, indent=2)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp_path, self.path)
        except OSError:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise


class PredictiveHighlighter:
    """Estimate the next words from rate, context and the optional profile.

    The public surface is deliberately small: ``set_script``, the speech
    lifecycle, ``observe`` for recogniser progress, ``prediction`` for the
    word to paint, and ``save``. It has no Qt dependency, so the estimator can
    be benchmarked without a display or a microphone.
    """

    def __init__(self, store: Optional[ProfileStore] = None,
                 clock=time.monotonic):
        self._store = store
        self._clock = clock
        self._profile = store.load() if store is not None else SpeakerProfile()
        self._timing = TimingModel(self._profile.timing_weights)
        self._session_rate = _clamp(self._profile.wps, MIN_WPS, MAX_WPS)
        self._rate = self._session_rate

        self._words: List[str] = []
        self._position = -1
        self._last_progress_at: Optional[float] = None
        self._last_speech_at: Optional[float] = None
        self._speech_stopped_at: Optional[float] = None
        self._speech_started_at: Optional[float] = None
        self._speaking = False
        self._session_on = False
        self._session_counted = False
        self._pause_elapsed = 0.0

        self._applied: Optional[int] = None
        self._last_grow = 0.0
        self._last_retreat = 0.0
        self._retreat_since: Optional[float] = None
        self._words_since_save = 0
        self._dirty = False

    # ── Public surface ───────────────────────────────────────────────
    @property
    def profile(self) -> SpeakerProfile:
        return self._profile

    @property
    def rate_wps(self) -> float:
        return self._rate

    def active(self, now: Optional[float] = None) -> bool:
        if not self._session_on or self._last_speech_at is None:
            return False
        moment = self._clock() if now is None else now
        return moment - self._last_speech_at <= ACTIVE_GRACE_SECONDS

    def set_script(self, words: Sequence[str]) -> None:
        """Start a new script, keeping the learned profile.

        The previous session's speech state is dropped. A script loaded while
        the old recogniser is still shutting down must not inherit its elapsed
        time and predict half a line before the first result.
        """
        self._words = [str(word) for word in words]
        self.reset()
        self._session_on = False
        self._session_counted = False
        self._speaking = False
        self._last_speech_at = None
        self._speech_started_at = None
        self._speech_stopped_at = None

    def reset(self, position: int = -1) -> None:
        self._position = position
        self._last_progress_at = None
        self._pause_elapsed = 0.0
        self._applied = None
        self._retreat_since = None

    def jump(self, position: int, now: Optional[float] = None) -> None:
        """The reader moved directly (wheel, checkpoint, restart)."""
        moment = self._clock() if now is None else now
        self._position = max(-1, int(position))
        self._last_progress_at = moment
        self._pause_elapsed = 0.0
        self._applied = None
        self._retreat_since = None

    def speech_started(self, now: Optional[float] = None) -> None:
        moment = self._clock() if now is None else now
        self._session_on = True
        if not self._session_counted:
            self._profile.sessions += 1
            self._session_counted = True
            self._dirty = True
        was_speaking = self._speaking
        self._speaking = True
        self._speech_started_at = (moment if not was_speaking
                                   else self._speech_started_at)
        self._last_speech_at = moment
        self._speech_stopped_at = None
        self._pause_elapsed = 0.0
        # A new utterance starts a fresh lead. Keeping the pre-pause elapsed
        # time would make the first tick after a breath predict far ahead.
        if not was_speaking:
            self._last_progress_at = moment

    def speech_stopped(self, now: Optional[float] = None) -> None:
        moment = self._clock() if now is None else now
        was_speaking = self._speaking
        self._speaking = False
        self._last_speech_at = moment
        if was_speaking:
            self._speech_stopped_at = moment
        gap = 0.0
        if self._last_progress_at is not None:
            gap = max(0.0, moment - self._last_progress_at)
        self._pause_elapsed = min(MAX_LEAD_SECONDS, gap)
        if gap >= 0.8:
            self._learn_pause(gap)
        self._retreat_since = None

    def observe(self, position: int, now: Optional[float] = None,
                is_final: bool = False) -> bool:
        """Record a recognised word boundary and update the learned model.

        Returns True when the position advanced. Observations outside a
        speech session are ignored: a test that drives the matcher without a
        microphone must not change the speaker's saved profile.
        """
        moment = self._clock() if now is None else now
        if not self.active(moment):
            return False
        # A result is evidence that speech is still going on. Without this a
        # long uninterrupted sentence would age out of the session window
        # even while the recogniser is delivering words.
        self._last_speech_at = moment
        if position < 0:
            return False
        if position < self._position:
            self.jump(position, moment)
            return False
        if position == self._position:
            return False
        if self._last_progress_at is None:
            self._position = position
            self._last_progress_at = moment
            return True

        delta_words = position - self._position
        delta_time = max(0.0, moment - self._last_progress_at)
        recent_seconds = 1.0 / max(MIN_WPS, self._rate)
        self._position = position

        if delta_time < 0.05:
            self._last_progress_at = moment
            return True

        # A jump across a sentence is recovery, not evidence of speed. It
        # still moves the reader; it just must not teach the model that the
        # speaker suddenly says nine words at once.
        coherent = delta_words <= 8 and delta_time <= 2.5
        paused = delta_time >= PAUSE_SECONDS
        if paused:
            self._learn_pause(delta_time)
        elif coherent:
            sample = delta_words / delta_time
            if MIN_WPS * 0.6 <= sample <= MAX_WPS * 1.05:
                weight = 0.30 if is_final else 0.14
                self._rate = _clamp(
                    self._rate + (sample - self._rate) * weight,
                    MIN_WPS, MAX_WPS)
                self._profile.wps = _clamp(
                    self._profile.wps + (sample - self._profile.wps) * 0.03,
                    MIN_WPS, MAX_WPS)
                self._profile.words += delta_words
                self._profile.seconds += delta_time
                self._words_since_save += delta_words

        if coherent:
            self._timing.observe(
                self._words[position] if 0 <= position < len(self._words) else "",
                recent_seconds,
                _clamp(delta_time / max(1, delta_words), 0.08, 2.5))
            self._profile.timing_weights = self._timing.as_list()

        self._last_progress_at = moment
        self._pause_elapsed = 0.0
        self._dirty = True
        return True

    def prediction(self, spoken_end: int, word_limit: int,
                   now: Optional[float] = None,
                   speaking: Optional[bool] = None) -> Optional[int]:
        """Last script word to anticipate, or None when none should be painted.

        ``spoken_end`` is the last word already heard (or the word before the
        current line's first word at a line boundary). ``word_limit`` is the
        highest script word index that may be painted: the last word of the
        rendered line, or one past it at a line boundary. The result never
        crosses it, so prediction cannot make the reader step or scroll.
        """
        moment = self._clock() if now is None else now
        if speaking is None:
            speaking = self._speaking
        if not self.active(moment):
            self._applied = None
            return None
        if not self._words or word_limit < 0:
            self._applied = None
            return None
        if not speaking:
            stopped = self._speech_stopped_at
            # How long a predicted run is held depends on how long this
            # speaker's pauses usually last, so a reader who pauses briefly
            # does not lose the lead and a reader who stops for a while does
            # not leave stale words marked.
            hold = _clamp(self._profile.pause_seconds * 3.0, 2.5,
                          PREDICTION_HOLD_SECONDS)
            if stopped is not None and moment - stopped > hold:
                self._applied = None
                return None
        base = max(-1, min(int(spoken_end), word_limit))
        if base >= word_limit:
            self._applied = None
            return None
        if not speaking:
            elapsed = self._pause_elapsed
        elif self._last_progress_at is not None:
            elapsed = moment - self._last_progress_at
        elif self._speech_started_at is not None:
            elapsed = moment - self._speech_started_at
        else:
            elapsed = 0.0
        elapsed = _clamp(elapsed, 0.0, MAX_LEAD_SECONDS)

        # Seconds of speech the next words may cover: the recogniser's own
        # delay, a human reaction window and the time since the last result.
        budget = RECOGNITION_LATENCY + REACTION_SECONDS + elapsed
        budget = _clamp(budget, 0.12, MAX_LEAD_SECONDS + 0.5)
        recent_seconds = 1.0 / max(MIN_WPS, self._rate)

        desired = base
        spent = 0.0
        limit = min(word_limit, base + MAX_LEAD_WORDS)
        for word_index in range(base + 1, limit + 1):
            word = (self._words[word_index]
                    if 0 <= word_index < len(self._words) else "")
            spent += self._timing.predict(word, recent_seconds)
            desired = word_index
            if spent >= budget:
                break
        # Context: if the clause ends in the next couple of words, anticipate
        # the whole clause rather than stopping mid-phrase.
        boundary = self._next_boundary(desired + 1, word_limit)
        if (boundary is not None and boundary - desired <= 2
                and boundary <= base + MAX_LEAD_WORDS):
            desired = boundary

        if not speaking:
            # A pause holds the painted run. An end-of-utterance final can
            # still move the spoken boundary past it, so it follows that much
            # and no further; the clock must not add words by itself.
            if self._applied is None:
                self._applied = min(desired, base + 1)
            self._applied = int(_clamp(self._applied, base + 1, word_limit))
            return self._applied

        # Anti-flicker: advance one word at a time, and only retreat after the
        # target has been wrong long enough to be real. Without both, a noisy
        # partial would repaint the boundary many times a second.
        if self._applied is None or self._applied < base + 1:
            self._applied = min(desired, base + 1)
            self._last_grow = moment
        elif desired > self._applied:
            if moment - self._last_grow >= GROW_INTERVAL:
                self._applied += 1
                self._last_grow = moment
        elif desired < self._applied:
            if self._retreat_since is None:
                self._retreat_since = moment
            elif (moment - self._retreat_since >= RETREAT_DELAY
                  and moment - self._last_retreat >= RETREAT_INTERVAL):
                self._applied -= 1
                self._last_retreat = moment
        else:
            self._retreat_since = None

        self._applied = int(_clamp(self._applied, base + 1, word_limit))
        if desired >= self._applied:
            self._retreat_since = None
        return self._applied

    def save(self, force: bool = False) -> bool:
        """Write the learned profile when there is something new to keep."""
        if self._store is None:
            return False
        if not force and not self._dirty:
            return False
        self._profile.timing_weights = self._timing.as_list()
        try:
            self._store.save(self._profile)
        except OSError:
            return False
        self._words_since_save = 0
        self._dirty = False
        return True

    def should_save(self) -> bool:
        return self._words_since_save >= SAVE_EVERY_WORDS

    # ── Internals ────────────────────────────────────────────────────
    def _learn_pause(self, seconds: float) -> None:
        seconds = _clamp(seconds, 0.2, 5.0)
        self._profile.pause_seconds = _clamp(
            self._profile.pause_seconds
            + (seconds - self._profile.pause_seconds) * 0.12,
            0.2, 5.0)
        self._profile.pause_count += 1
        self._dirty = True

    def _next_boundary(self, start: int, word_limit: int) -> Optional[int]:
        for index in range(max(0, start), word_limit + 1):
            word = (self._words[index]
                    if 0 <= index < len(self._words) else "")
            if _SENTENCE_END.search(word) or _CLAUSE_END.search(word):
                return index
        return None
