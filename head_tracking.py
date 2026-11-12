"""Head tracking for the reader — a prototype, off until it earns its place.

The reader sits next to the camera so the eyes stay near the lens. What it
cannot do is follow the head: when the reader leans or turns, the words stay
put and the eyes have to leave the camera to find them again. This module
measures where the head is and moves the card by the same distance, so the
words stay in the same place relative to the eyes. The shift is bounded and
screened, never proportional to noise.

Two layers, deliberately separated:

* :class:`HeadMotion` is pure arithmetic — no Qt, no camera, no clock of its
  own. Every timestamp is passed in, so the harness in
  ``tests/check_head_tracking.py`` can measure latency, jitter and stability
  deterministically instead of guessing from a live session.
* :class:`CameraHeadSource` owns the webcam. It reads at the smallest frame
  size that still finds a face at reading distance, at a low rate, is fully
  local (nothing is written or sent anywhere; only two floats ever leave the
  worker), and tries MediaFoundation's shared mode first on Windows so a video
  call can keep the camera.

The numbers here are the whole design, so they are named constants rather
than magic values:

``DEAD_ZONE``
    A head displacement this small does nothing at all. Sitting still is the
    normal case; tracking it would make the text breathe.

``MOVE_THRESHOLD``
    Output-side hysteresis in pixels. A change smaller than this is not
    applied, so the quantiser cannot chatter between two adjacent values.

``MAX_*``
    The strict displacement limit. Nothing this module returns can exceed it,
    whatever the detector reports — a stray detection cannot throw the card
    across the desktop.

``TEXT_GAIN`` / ``CARD_GAIN``
    Both at 1.0: the card carries the words, and nothing moves inside the
    card. A card that follows only part of the way leaves the words sliding
    within a moving frame, which reads as two motions fighting.
"""

from __future__ import annotations

import math
import os
import threading
import time
from typing import NamedTuple, Optional

# ── The design numbers ───────────────────────────────────────────────────
# Strict displacement limits, in DIP. This is how far the words may travel
# across the screen, and the card carries them at full distance: the point is
# that the words stay put relative to the eyes, and the eyes move with the head.
#
# The number is not decorative. A reader's head wanders about ±45 mm, and the
# words only hold their place relative to the eyes if they move by the same
# distance: 45 mm at 96 DPI is ~170 DIP. 150 keeps a margin inside that, and
# the vertical figure is lower because a head nods less than it sways.
MAX_TEXT_DX = 150.0
MAX_TEXT_DY = 100.0
# The card travels with the words, so no part of the shift happens *inside*
# the card. Anything less and the words would slide within a frame that was
# still moving, which reads as two motions fighting.
CARD_GAIN = 1.0
TEXT_GAIN = 1.0

# Normalised head displacement (1.0 = half the frame) that must be exceeded
# before anything moves at all. A head sways a few millimetres while a person
# reads; tracking that would make the words breathe.
DEAD_ZONE = 0.03
# The head displacement that reaches the full text limit. Deliberately much
# smaller than the frame: a lean of ~35 mm at laptop distance is the range the
# words should span end to end, and anything past it saturates. Scaling to the
# frame instead would compress a real lean into a pixel or two — a prototype
# that measures as working while looking like nothing happens.
# 0.20 of a half-frame is ~58 mm at laptop distance — a lean a little past the
# 45 mm the eye-contact model allows for, so a deliberate lean reaches the end
# of the travel without a shoulder roll being needed to get there.
RANGE = 0.20
# Output hysteresis, in DIP. Raised with the travel: at 150 DIP the same
# smoothed noise would put a 7 DIP tremble on a still head, which reads as the
# card shivering. Three DIP is 2% of the travel — 1.2 mm on the glass, below
# what a reader can see — and still 50 positions a side, so a real lean is not
# quantised into steps you can see either.
MOVE_THRESHOLD = 3.0
# One-Euro smoothing: how much of a *fast* move is let through. The cutoff
# rises with speed, so a slow drift is filtered hard and a real turn is not.
#
# These were 0.4 / 0.5 / 1.0, and the measured consequence was that a real
# 45 mm lean (0.12 of a half-frame) took 783 ms to reach 90% of its travel on
# the cadence this machine actually runs — 9.3 Hz, because a frame is
# 66 ms of camera plus ~47 ms of detection. The old harness reported 167 ms
# only because it measured a 0.55 step, four times a head lean, which saturates
# the range and makes the speed term open the filter wide. Nobody moves their
# head 0.55 of a half-frame.
#
# Re-measured over the range of lean sizes at that cadence, with the tremble
# modelled on the noise the cascade actually shows (0.0125 peak to peak),
# 0.7 / 3.2 / 6.0 gives 267 ms for a 45 mm lean and 4.5 DIP of tremble against
# the 6 DIP two-step bar; 0.4 keeps the tremble under 3.5 DIP but puts the lean
# back at 333 ms. The speed terms are what move; the rest cutoff is what stops
# the words breathing, and 0.7 is the highest that keeps a still head still.
SMOOTH_MIN_CUTOFF = 0.7
SMOOTH_BETA = 3.2
SMOOTH_D_CUTOFF = 6.0

# How long the render clock takes to walk to a new target, in seconds. This
# is what turns 15 camera samples a second into motion at the reader's frame
# rate instead of a staircase.
RENDER_SECONDS = 0.05
# Exit is slower than entry: a lost face must not snap the words back.
RELEASE_SECONDS = 0.14
# A face that has not been seen for this long is treated as gone. Measured on
# this webcam: a frame arrives in 0.07 s when the face is visible, but a
# momentary turn or a missed detection leaves gaps of half a second, and 0.40
# made the words snap to centre and back on every one of them.
LOSS_SECONDS = 0.80
# Below this the offset is snapped to exactly zero, so a still head leaves the
# text exactly centred instead of a pixel and a half off.
SETTLE = 0.6
# Auto-neutral: the head is measured against where it was when tracking
# started, so an off-centre webcam does not park the text off-centre too.
CALIBRATE_SECONDS = 1.2
CALIBRATE_MIN_SAMPLES = 6


class HeadOffsets(NamedTuple):
    """What the reader should do with this sample, in DIP."""

    text_dx: float
    text_dy: float
    card_dx: float
    card_dy: float

    @property
    def is_zero(self) -> bool:
        return (self.text_dx == 0.0 and self.text_dy == 0.0
                and self.card_dx == 0.0 and self.card_dy == 0.0)


ZERO = HeadOffsets(0.0, 0.0, 0.0, 0.0)


def _alpha(cutoff: float, dt: float) -> float:
    """One-Euro's smoothing factor for a cutoff (Hz) over a step (s)."""
    tau = 1.0 / (2.0 * math.pi * max(1e-3, cutoff))
    return 1.0 / (1.0 + tau / dt)


class _OneEuro:
    """The 1€ filter: heavy smoothing at rest, light smoothing in motion.

    A plain exponential average forces one choice — smooth enough to hide the
    detector's noise, or quick enough to feel responsive. This picks per
    sample from the measured speed, which is exactly the trade-off head
    tracking needs: no visible tremor while sitting still, no lag on a turn.
    """

    __slots__ = ("min_cutoff", "beta", "d_cutoff", "_x", "_dx", "_t", "_primed")

    def __init__(self, min_cutoff: float = SMOOTH_MIN_CUTOFF,
                 beta: float = SMOOTH_BETA, d_cutoff: float = SMOOTH_D_CUTOFF):
        self.min_cutoff = float(min_cutoff)
        self.beta = float(beta)
        self.d_cutoff = float(d_cutoff)
        self.reset()

    def reset(self) -> None:
        self._x = 0.0
        self._dx = 0.0
        self._t = 0.0
        self._primed = False

    def filter(self, value: float, now: float) -> float:
        if not self._primed:
            self._x = float(value)
            self._dx = 0.0
            self._t = now
            self._primed = True
            return self._x
        dt = max(1e-3, now - self._t)
        self._t = now
        speed = (value - self._x) / dt
        a_d = _alpha(self.d_cutoff, dt)
        self._dx = a_d * speed + (1.0 - a_d) * self._dx
        cutoff = self.min_cutoff + self.beta * abs(self._dx)
        a = _alpha(cutoff, dt)
        self._x = a * value + (1.0 - a) * self._x
        return self._x


def _through_dead_zone(value: float, dead_zone: float, full: float) -> float:
    """Map a normalised displacement through the dead zone, continuously.

    Inside the zone the result is zero. Outside it the value is rescaled so
    that ``full`` reaches 1.0, which leaves the zone from zero rather than
    jumping to the zone's width — a step there would read as the text
    twitching.
    """
    magnitude = abs(value)
    if magnitude <= dead_zone or full <= dead_zone:
        return 0.0
    remainder = (magnitude - dead_zone) / (full - dead_zone)
    return math.copysign(min(1.0, remainder), value)


class HeadMotion:
    """Turns head position samples into bounded, smoothed reader offsets.

    Feed it in the worker's cadence, poll it in the frame's::

        motion.enable()
        motion.feed(0.10, -0.05, now)
        offsets = motion.poll(now)

    ``feed`` and ``poll`` are separate because the camera runs at ~15 Hz and
    the reader paints at 60. Doing the easing in ``poll`` is what keeps the
    motion smooth across that gap.
    """

    def __init__(self, *, max_dx: float = MAX_TEXT_DX, max_dy: float = MAX_TEXT_DY,
                 card_gain: float = CARD_GAIN, dead_zone: float = DEAD_ZONE,
                 full_range: float = RANGE,
                 move_threshold: float = MOVE_THRESHOLD,
                 calibrate_seconds: float = CALIBRATE_SECONDS,
                 loss_seconds: float = LOSS_SECONDS):
        self.max_dx = float(max_dx)
        self.max_dy = float(max_dy)
        # The card can never travel further than the text limit either.
        self.card_gain = max(0.0, min(1.0, float(card_gain)))
        self.dead_zone = max(0.0, float(dead_zone))
        self.full_range = max(self.dead_zone, float(full_range))
        self.move_threshold = max(0.0, float(move_threshold))
        self.calibrate_seconds = max(0.0, float(calibrate_seconds))
        self.loss_seconds = max(0.0, float(loss_seconds))

        self._x_filter = _OneEuro()
        self._y_filter = _OneEuro()
        self._active = False
        self._calibrating = False
        self._calib_started = 0.0
        self._calib_samples: list[tuple[float, float]] = []
        self._neutral_x = 0.0
        self._neutral_y = 0.0
        self._last_sample = 0.0
        self._target = (0.0, 0.0)          # text target, DIP
        self._applied = (0.0, 0.0)         # text offset currently rendered
        self._offsets = ZERO
        self._polled_at: Optional[float] = None

    # ── lifecycle ────────────────────────────────────────────────────
    @property
    def active(self) -> bool:
        return self._active

    @property
    def calibrated(self) -> bool:
        return not self._calibrating

    def enable(self, now: Optional[float] = None) -> None:
        """Start tracking, and re-measure neutral from here.

        Every enable recalibrates. A posture change between sessions is the
        normal case, and a stale neutral would park the text permanently
        off-centre — the one failure mode that makes the feature worse than
        doing nothing.
        """
        now = time.monotonic() if now is None else now
        self._active = True
        self._x_filter.reset()
        self._y_filter.reset()
        self._calibrating = True
        self._calib_started = now
        self._calib_samples = []
        self._last_sample = now
        self._target = (0.0, 0.0)
        self._applied = (0.0, 0.0)
        self._offsets = ZERO
        self._polled_at = None

    def disable(self) -> HeadOffsets:
        """Stop now. The offset is zero on this call, not eased to it.

        The disable path is a user's escape hatch from a camera they no
        longer want, so it must not leave the words sliding even for the
        hundred milliseconds a release would take.
        """
        self._active = False
        self._calibrating = False
        self._calib_samples = []
        self._target = (0.0, 0.0)
        self._applied = (0.0, 0.0)
        self._offsets = ZERO
        self._polled_at = None
        return ZERO

    # ── input ────────────────────────────────────────────────────────
    def feed(self, x: float, y: float, now: float) -> None:
        """Take one detector reading: head centre, normalised to ±1.

        ``x`` and ``y`` are measured from the frame's centre with 1.0 at the
        frame's edge, so the numbers do not depend on the camera's resolution.
        """
        if not self._active:
            return
        if not (math.isfinite(x) and math.isfinite(y)):
            return
        self._last_sample = now
        x = max(-1.0, min(1.0, float(x)))
        y = max(-1.0, min(1.0, float(y)))

        if self._calibrating:
            if not self._calib_samples:
                # The window belongs to the first sample, not to the moment the
                # switch was flipped: the camera takes a second or two to open,
                # and a window that expired during that would stand no chance.
                self._calib_started = now
            self._calib_samples.append((now, x, y))
            # Rolling window: only the last ``calibrate_seconds`` of readings
            # describe where this reader's head is now. Until this was a
            # window at all, a lean that happened to fall inside it became the
            # neutral, and every later reading was measured against it — the
            # card then sat offset, moved by the wrong amount and pinned itself
            # against its limit. Measured in a live run: a 45 mm lean captured
            # as neutral turned the next 45 mm into the full 150 DIP in the
            # opposite direction.
            horizon = now - self.calibrate_seconds
            while (len(self._calib_samples) > 1
                   and self._calib_samples[0][0] < horizon):
                self._calib_samples.pop(0)
            elapsed = now - self._calib_started
            enough = len(self._calib_samples) >= CALIBRATE_MIN_SAMPLES
            # A neutral measured while the head was moving is worse than no
            # neutral at all, so the window only closes on a still head.
            if (elapsed >= self.calibrate_seconds and enough
                    and self._calibration_is_still()):
                self._finish_calibration()
            elif (elapsed >= self.calibrate_seconds * 4.0
                  and self._calib_samples):
                # A camera that reports a face only every second or so would
                # otherwise wait for six samples for ever, leaving the feature
                # silently inert — the worst outcome available. Bounded so a
                # reader who will not hold still still gets tracking, on the
                # best neutral available.
                self._finish_calibration()
            else:
                return

        rel_x = self._x_filter.filter(x - self._neutral_x, now)
        rel_y = self._y_filter.filter(y - self._neutral_y, now)
        shaped_x = _through_dead_zone(rel_x, self.dead_zone, self.full_range)
        shaped_y = _through_dead_zone(rel_y, self.dead_zone, self.full_range)
        self._target = (shaped_x * self.max_dx, shaped_y * self.max_dy)

    def _calibration_is_still(self) -> bool:
        """Whether the window describes a head that is holding still.

        The bar is the dead zone itself: a spread the tracker would not have
        moved for anyway cannot make the neutral wrong in a way the reader
        could see.
        """
        xs = [sample[1] for sample in self._calib_samples]
        ys = [sample[2] for sample in self._calib_samples]
        return (max(xs) - min(xs) <= self.dead_zone
                and max(ys) - min(ys) <= self.dead_zone)

    def _finish_calibration(self) -> None:
        """Take the neutral as the middle sample, not the average.

        A median ignores the one frame where the detector put the box in the
        wrong place, and with a handful of samples that is most of the
        difference between a neutral that works and one that leaves the words
        parked against the stop.
        """
        xs = sorted(sample[1] for sample in self._calib_samples)
        ys = sorted(sample[2] for sample in self._calib_samples)
        self._neutral_x = xs[len(xs) // 2]
        self._neutral_y = ys[len(ys) // 2]
        self._calibrating = False
        self._calib_samples = []
        # Start the filter at the neutral point, not at 0, so the first filtered
        # sample is not a step from the frame centre.
        self._x_filter.reset()
        self._y_filter.reset()

    # ── output ───────────────────────────────────────────────────────
    def poll(self, now: float) -> HeadOffsets:
        """Advance to ``now`` and return the offsets to render."""
        if not self._active:
            if not self._offsets.is_zero:
                self._offsets = ZERO
            return self._offsets

        if self._polled_at is None:
            self._polled_at = now
            return self._offsets
        dt = max(0.0, now - self._polled_at)
        self._polled_at = now

        # A calibration window that filled before the samples arrived must
        # still resolve. It used to be advanced only from ``feed``, so a camera
        # that delivered a reading and then nothing left the tracker
        # calibrating for ever — switched on, visibly doing nothing, with no
        # error to report. Polling is the clock that cannot stop, so the
        # deadline is enforced here too. The neutral is the median of whatever
        # did arrive, which is the best available answer when there is one
        # reading and no way to ask for another.
        if (self._calibrating and self._calib_samples
                and now - self._calib_started >= self.calibrate_seconds * 4.0):
            self._finish_calibration()

        if now - self._last_sample > self.loss_seconds:
            # No face: release. Slower than a normal frame, so a blink or a
            # missed detection does not shove the words around.
            self._target = (0.0, 0.0)

        if dt <= 0.0:
            return self._offsets

        tx, ty = self._target
        ax, ay = self._applied
        # Hysteresis: a step smaller than the threshold is not started, which
        # is what stops the offset chattering between two values while the
        # head is still. A release is exempt — it must always reach zero.
        released = (tx == 0.0 and ty == 0.0)
        if (not released
                and abs(tx - ax) < self.move_threshold
                and abs(ty - ay) < self.move_threshold):
            tx, ty = ax, ay

        tau = RELEASE_SECONDS if released else RENDER_SECONDS
        k = 1.0 - math.exp(-dt / max(1e-3, tau))
        nx = ax + (tx - ax) * k
        ny = ay + (ty - ay) * k
        # Strict limits on the way out as well as the way in. Nothing this
        # module returns may exceed them, whatever the filter did.
        nx = max(-self.max_dx, min(self.max_dx, nx))
        ny = max(-self.max_dy, min(self.max_dy, ny))
        if tx == 0.0 and abs(nx) < SETTLE:
            nx = 0.0
        if ty == 0.0 and abs(ny) < SETTLE:
            ny = 0.0
        self._applied = (nx, ny)

        card_x = max(-self.max_dx, min(self.max_dx, nx * self.card_gain))
        card_y = max(-self.max_dy, min(self.max_dy, ny * self.card_gain))
        text_x = nx * TEXT_GAIN
        text_y = ny * TEXT_GAIN
        # The text leads by the full amount; the card shares only its part,
        # so what the text gains inside the card is the difference. Both are
        # clamped independently: rounding at render time cannot push either
        # past the limit.
        self._offsets = HeadOffsets(
            max(-self.max_dx, min(self.max_dx, text_x)),
            max(-self.max_dy, min(self.max_dy, text_y)),
            card_x,
            card_y,
        )
        return self._offsets

    def offsets(self) -> HeadOffsets:
        return self._offsets


_detector_lock = threading.Lock()
_detector: Optional[tuple] = None


def load_detector() -> tuple:
    """Import OpenCV and load the face cascades once per process.

    Two cascades, because one cannot sit through a head turn: the frontal one
    belongs to a face looking at the lens, the profile one to a face turned
    away from it. A reader does both, so tracking must survive both.

    Loading is slow the first time — a cold ``import cv2`` plus parsing a
    cascade took over a second here. Doing that inside each enable made the
    first toggle feel stuck *and* let the worker finish opening the camera
    after the user had already switched tracking off, holding a device it had
    been told to release. One process, one detector, one slow first time.
    """
    global _detector
    with _detector_lock:
        if _detector is not None:
            return _detector
        import cv2

        data = getattr(cv2, "data", None)
        if data is None or not getattr(data, "haarcascades", ""):
            raise RuntimeError("face detector model is missing")
        frontal = cv2.CascadeClassifier(
            os.path.join(data.haarcascades, "haarcascade_frontalface_default.xml"))
        if frontal.empty():
            raise RuntimeError("face detector model is missing")
        # Optional: a wheel without the profile cascade still tracks a face
        # looking straight ahead, it just loses it on a turn.
        profile = None
        profile_path = os.path.join(data.haarcascades, "haarcascade_profileface.xml")
        if os.path.exists(profile_path):
            candidate = cv2.CascadeClassifier(profile_path)
            if not candidate.empty():
                profile = candidate
        _detector = (cv2, frontal, profile)
        return _detector


def flip_box(box, frame_w: int) -> tuple:
    """Mirror a box found in a horizontally flipped frame back into the frame.

    The profile cascade only knows faces turned one way. Running it a second
    time on a mirrored copy of the frame, and flipping the boxes back, teaches
    it the other way for the price of one copy of a 320x240 grey image.
    """
    x, y, w, h = (int(v) for v in box)
    return (int(frame_w) - x - w, y, w, h)


def pick_nearest_face(faces, last_centre):
    """Centre of the box closest to the last one, or of the largest first.

    Continuity first and size second: a face in the background must not steal
    the reading between frames, but with nothing to be continuous with, the
    biggest box is the one at the desk.
    """
    best = None
    for (fx, fy, fw, fh) in faces:
        cx = fx + fw / 2.0
        cy = fy + fh / 2.0
        if last_centre is None:
            distance = -fw * fh
        else:
            distance = ((cx - last_centre[0]) ** 2
                        + (cy - last_centre[1]) ** 2)
        if best is None or distance < best[0]:
            best = (distance, cx, cy)
    if best is None:
        return None
    return (best[1], best[2])


class CameraHeadSource:
    """Reads head position from the webcam, locally, on its own thread.

    Deliberate properties, each one a constraint from the brief:

    * **Local.** Only two floats leave this object. No frame is written to
      disk, no frame is sent anywhere. ``tests/check_head_tracking.py`` runs
      the pipeline with sockets disabled to keep it that way.
    * **Shared.** MediaFoundation (``CAP_MSMF``) is tried first, which is the
      backend that lets a video call keep the device; DirectShow — usually
      exclusive — is only the fallback. The frame size and rate are the
      smallest that still finds a face, so the device is not held at 1080p
      while a call wants it.
    * **Honest failure.** If the camera is busy or absent, ``error`` says so
      and the reader stays off. It does not retry in a loop or silently
      pretend to track.
    """

    # 640x480 at 15 fps. This was 320x240, chosen on the arithmetic that a
    # 14 cm face at 50 cm is ~77 px wide there and that the cascade's floor is
    # the configured MIN_FACE of 40. Measured against the shipped cascade, the
    # floor is not 40: it needs a face box around 56 px before it reports
    # anything with minNeighbors=5. At 320 wide a reader 70-80 cm back gives a
    # 62-71 px head, which is below that floor, so the detector returned no
    # face at all — and a feature that never sees a face is a feature that
    # silently does nothing, which is how this one failed in the field.
    #
    # At 640x480 the same reader is 124-142 px, comfortably inside the range:
    # a sweep of head heights from 84 to 252 px detected in every case, which
    # covers roughly 40 cm to 1.2 m. The frame costs ~33 ms to search against
    # a 67 ms budget; the profile cascade is no longer run on frames that
    # already found a face (see ``_run``), which is what pays for the area.
    FRAME_W = 640
    FRAME_H = 480
    FPS = 15
    MIN_FACE = 40
    # How long a profile detection may hold the last known position without a
    # frontal one to back it up. Long enough to sit through a glance away,
    # short enough that a reader who has left is not tracked forever.
    PROFILE_HOLD = 1.0

    def __init__(self, index: int = 0, *, frame_w: Optional[int] = None,
                 frame_h: Optional[int] = None, fps: Optional[int] = None):
        self.index = int(index)
        self.frame_w = int(frame_w or self.FRAME_W)
        self.frame_h = int(frame_h or self.FRAME_H)
        self.fps = int(fps or self.FPS)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest: Optional[tuple[float, float, float]] = None
        self._error: Optional[str] = None
        self._backend = ""
        self._detect_seconds = 0.0
        # Which cascade produced the last reading: "frontal", "profile", or
        # "none". Exposed so a live session can be checked rather than guessed
        # at — a turned head should read "profile", not silence.
        self._last_source = "none"

    # ── lifecycle ────────────────────────────────────────────────────
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def error(self) -> Optional[str]:
        with self._lock:
            return self._error

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def last_source(self) -> str:
        with self._lock:
            return self._last_source

    @property
    def detect_seconds(self) -> float:
        """Cost of the last detection, in seconds.

        Exposed so ``tests/measure_head_tracking.py`` can report what the
        detector adds to the latency budget instead of assuming it is free.
        """
        with self._lock:
            return self._detect_seconds

    def start(self) -> bool:
        """Begin capture. Opening the device happens on the worker.

        The open can block for a second on Windows, so it must not happen on
        the UI thread; the reader instead polls :attr:`error` and stays off
        when the camera could not be used.
        """
        if self.running:
            return True
        self._stop.clear()
        with self._lock:
            self._error = None
            self._latest = None
        self._thread = threading.Thread(target=self._run, name="notch-head",
                                        daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        """Stop and release the device. Safe to call twice."""
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)
        with self._lock:
            self._latest = None

    # ── reading ──────────────────────────────────────────────────────
    def read(self) -> Optional[tuple[float, float, float]]:
        """Latest ``(x, y, timestamp)``, or None before the first face."""
        with self._lock:
            return self._latest

    # ── worker ───────────────────────────────────────────────────────
    def _fail(self, message: str) -> None:
        with self._lock:
            self._error = message

    def _open(self, cv2):
        """Open the camera on the backend most likely to share it."""
        attempts = []
        if hasattr(cv2, "CAP_MSMF"):
            attempts.append(("MSMF", cv2.CAP_MSMF))
        if hasattr(cv2, "CAP_DSHOW"):
            attempts.append(("DSHOW", cv2.CAP_DSHOW))
        attempts.append(("default", 0))
        for name, backend in attempts:
            if self._stop.is_set():
                return None, ""
            try:
                capture = cv2.VideoCapture(self.index, backend)
            except Exception:
                continue
            if capture is not None and capture.isOpened():
                return capture, name
            try:
                if capture is not None:
                    capture.release()
            except Exception:
                pass
        return None, ""

    def _run(self) -> None:
        try:
            cv2, cascade, profile = load_detector()
        except ImportError:
            self._fail("OpenCV is not installed")
            return
        except Exception as exc:
            self._fail(str(exc) or "face detector could not be loaded")
            return
        # The detector load is the slow part; a stop that arrived while it ran
        # must win, or the camera is opened for a feature the user just offed.
        if self._stop.is_set():
            return

        capture, backend = self._open(cv2)
        if self._stop.is_set():
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    pass
            return
        if capture is None:
            self._fail("camera unavailable (busy or not connected)")
            return
        self._backend = backend
        try:
            # Small frame, low rate, one buffered frame: the least the device
            # can be asked for while a call might want it too, and the least
            # stale frame when it is ours.
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(self.frame_w))
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self.frame_h))
            capture.set(cv2.CAP_PROP_FPS, float(self.fps))
            if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
                capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            width = float(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or self.frame_w)
            height = float(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or self.frame_h)
            if width <= 0 or height <= 0:
                width, height = float(self.frame_w), float(self.frame_h)

            interval = 1.0 / max(1.0, float(self.fps))
            misses = 0
            last_centre: Optional[tuple[float, float]] = None
            # When a frontal face was last seen. A profile detection may keep
            # the last position alive, but only for a while: a reader who has
            # genuinely turned away should get their words back.
            last_frontal = 0.0
            while not self._stop.is_set():
                started = time.monotonic()
                ok, frame = capture.read()
                if not ok or frame is None:
                    misses += 1
                    if misses >= 6:
                        self._fail("camera stopped delivering frames")
                        return
                    self._stop.wait(interval)
                    continue
                misses = 0
                faces = ()
                profiles = ()
                try:
                    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    grey = cv2.equalizeHist(grey)
                    faces = cascade.detectMultiScale(
                        grey, scaleFactor=1.2, minNeighbors=5,
                        minSize=(self.MIN_FACE, self.MIN_FACE),
                    )
                    # The profile cascade only matters on a miss: when the
                    # frontal pass found a face its boxes are discarded below,
                    # so running it anyway spent ~25 ms of a 67 ms frame on
                    # two searches whose answer was thrown away — CPU taken
                    # straight from the speech recogniser. It now runs only
                    # when the face is actually turned or gone, at half scale,
                    # which is enough to keep a turned head alive and costs
                    # nothing on the frames that matter for movement.
                    if not len(faces) and profile is not None:
                        half = cv2.resize(
                            grey,
                            (max(1, grey.shape[1] // 2),
                             max(1, grey.shape[0] // 2)),
                            interpolation=cv2.INTER_AREA,
                        )
                        small = max(8, self.MIN_FACE // 2)
                        kwargs = {"scaleFactor": 1.15, "minNeighbors": 4,
                                  "minSize": (small, small)}
                        profiles = list(
                            profile.detectMultiScale(half, **kwargs))
                        flipped = cv2.flip(half, 1)
                        for box in profile.detectMultiScale(flipped, **kwargs):
                            profiles.append(flip_box(box, half.shape[1]))
                        # Back into frame pixels, the units every other box in
                        # this loop is in.
                        profiles = [(int(x * 2), int(y * 2),
                                     int(w * 2), int(h * 2))
                                    for (x, y, w, h) in profiles]
                except Exception:
                    faces, profiles = (), ()
                frame_cost = time.monotonic() - started
                with self._lock:
                    if frame_cost > 0.0:
                        # The whole cycle for this frame — waiting for it and
                        # detecting in it — whether or not it found a face. A
                        # missed frame is latency the reader still pays.
                        self._detect_seconds = frame_cost
                centre = pick_nearest_face(faces, last_centre)
                if centre is not None:
                    cx, cy = centre
                    last_centre = centre
                    last_frontal = time.monotonic()
                    # Normalised to the frame, independent of resolution:
                    # ±1 at the frame's edge.
                    nx = (cx - width / 2.0) / (width / 2.0)
                    ny = (cy - height / 2.0) / (height / 2.0)
                    with self._lock:
                        self._latest = (nx, ny, time.monotonic())
                        self._last_source = "frontal"
                elif profiles and self._latest is not None:
                    # A turned head is still this reader's head. The profile
                    # box is neither the same shape nor the same place as a
                    # frontal one, so it does not move the words — it keeps
                    # the last position alive, which is what stops a turn
                    # from sending the card home mid-paragraph. Bounded by
                    # PROFILE_HOLD so a reader who really has left the desk
                    # still gets their words back.
                    if time.monotonic() - last_frontal <= PROFILE_HOLD:
                        with self._lock:
                            self._latest = (self._latest[0], self._latest[1],
                                            time.monotonic())
                            self._last_source = "profile"
                wait = interval - (time.monotonic() - started)
                if wait > 0:
                    self._stop.wait(wait)
        finally:
            try:
                capture.release()
            except Exception:
                pass
