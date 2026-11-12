"""Head and eye tracking for the reader, on an invisible 5x2 grid.

The reader sits next to the camera so the eyes stay near the lens. What it
cannot do is follow the head: when the reader leans or turns, the words stay
put and the eyes have to leave the camera to find them again. This module
measures the head, the eyes and an estimate of where the reader is looking,
and moves the card to the region of the screen that best matches all three.

The movement space is the **upper half** of the display. That half is divided
into five equally spaced columns and two rows: ten approximate tracking
regions. The lower half is never a destination, so the words can never be
parked under the reader's hands or below the desk line. Movement is chosen at
region granularity but rendered continuously: the card eases between region
anchors on a spring, and a bounded share of the within-region signal gives it
a little life without crossing the boundary. It never snaps.

Two layers, deliberately separated:

* :class:`HeadMotion` is pure arithmetic — no Qt, no camera, no clock of its
  own. Every timestamp is passed in, so the harness in
  ``tests/check_head_tracking.py`` can measure every region, continuity,
  latency, jitter and the release deterministically.
* :class:`CameraHeadSource` owns the webcam. It reads at the smallest frame
  size that still finds a face at reading distance, at a low rate, is fully
  local (nothing is written or sent anywhere; only a few floats ever leave the
  worker), and never gives up on a camera that has worked once.

The numbers here are the whole design, so they are named constants rather than
magic values. In the motion layer:

``GRID_COLUMNS`` / ``GRID_ROWS`` / ``TOP_SHARE``
    The tracking space: five columns by two rows across the top half.

``HEAD_WEIGHT`` / ``EYE_WEIGHT`` / ``DIRECTION_WEIGHT``
    The three inputs are fused, not chosen between. Head position is the
    coarse signal (where the face is), iris deviation is the fine signal
    (where the eyes are inside the face), and the face's yaw/pitch estimate is
    the direction signal (where the face is pointing). The weights sum to one.

``HEAD_FULL_*`` / ``GAZE_FULL`` / ``YAW_FULL`` / ``PITCH_FULL``
    The residual that counts as a deliberate movement of that input. Each is
    measured from the calibration neutral, so an off-centre webcam or a face
    that rests its irises a little to one side does not bias the grid.

``SIGNAL_DEAD_ZONE``
    Fused movement smaller than this does nothing at all. Sitting still is the
    normal case; tracking it would make the card breathe.

``GRID_HYSTERESIS_*``
    A region is kept until the signal is this far past its boundary. This is
    what stops a head hovering on a boundary from rattling between two cells.

``INTRA_*``
    The continuous, bounded movement allowed inside the selected region. It is
    a share of the residual, with a small dead band so detector noise never
    becomes a tremble, and a cap below half a cell so the words cannot drift
    out of the region the tracker chose.

In the camera layer, ``REOPEN_AFTER_SECONDS``, ``REOPEN_BACKOFF_*`` and
``STALL_SECONDS`` are the difference between a camera that recovers from a
video call taking it for a moment and one that flickers as it reopens in a
loop.
"""

from __future__ import annotations

import atexit
import math
import os
import sys
import threading
import time
from typing import NamedTuple, Optional

# ── The tracking space ───────────────────────────────────────────────────
# Five columns, two rows, over the top half of the work area. The grid is
# invisible: it is a way to choose a stable destination, not a ruler the
# reader should ever see. The lower half is excluded by construction — v is
# normalised 0..1 over the top half only.
GRID_COLUMNS = 5
GRID_ROWS = 2
TOP_SHARE = 0.5

# Where a reader who is not moving belongs. The top row is where the reading
# card normally lives, so a neutral head keeps it near the top of the screen
# rather than dropping it to the middle of the upper half.
NEUTRAL_COLUMN = GRID_COLUMNS // 2
NEUTRAL_ROW = 0
NEUTRAL_REGION = NEUTRAL_ROW * GRID_COLUMNS + NEUTRAL_COLUMN

# ── Input fusion ─────────────────────────────────────────────────────────
# The three signals, each normalised by the residual that means a deliberate
# movement. The weights sum to one: no input can run away with the card, and
# none is decorative.
HEAD_WEIGHT = 0.55
EYE_WEIGHT = 0.25
DIRECTION_WEIGHT = 0.20

# A lean of 0.20 of a half-frame is roughly 58 mm at laptop distance. At the
# head weight that reaches column 3 from the neutral column; a stronger lean,
# helped by a turn, reaches columns 0 and 4. The vertical figure is smaller
# because a head nods less than it sways.
HEAD_FULL_X = 0.20
HEAD_FULL_Y = 0.16
# Iris deviation inside the eye, 1.0 at the eye's half-width.
GAZE_FULL = 0.60
# Nose offset from the eye midpoint, in eye-widths. This is a cheap face-pose
# estimate from the landmark mesh: it is translation invariant (nose and eyes
# move together) and needs no extra model output.
YAW_FULL = 0.30
PITCH_FULL = 0.60
# tanh gain. At one full-range signal this maps to 0.80 of a half-grid, which
# reaches the outer anchors; stronger movements saturate instead of running
# off the screen.
SIGNAL_GAIN = 1.15

# Fused residuals below this are stillness. In head terms it is about 10 mm of
# lean, comfortably above the landmarker's few-millimetre wobble.
SIGNAL_DEAD_ZONE = 0.08
# Output-side region hysteresis, in cell units. A quarter of a column is
# enough to absorb a boundary hover without making the grid feel sticky.
GRID_HYSTERESIS_X = 0.12
GRID_HYSTERESIS_Y = 0.14

# Continuous movement inside the selected region. The share keeps it gentle;
# the dead band is expressed in cells so it scales with the display; the limit
# stays below half a cell, which is what guarantees the rendered position can
# never enter a neighbouring region without the region selector agreeing.
INTRA_SHARE = 0.45
INTRA_DEAD_BAND = 0.12
INTRA_LIMIT = 0.34

# When the card moves toward where the reader is looking, the eyes need less
# deviation to keep reading it. Without compensation that relaxation reads as
# a new gaze direction, pulls the card back, and produces a slow hunt between
# two regions. The gaze reference therefore follows the card: a fraction of
# every rendered movement is added to the neutral the eyes are measured
# against. The fraction is deliberately below 1.0 because the eyes do not
# follow the full screen distance; the remaining error is absorbed by the
# dead zone and the region hysteresis.
GAZE_CARD_COUPLING = 0.65
GAZE_REFERENCE_LIMIT = 1.5

# ── Smoothing ────────────────────────────────────────────────────────────
# Head position uses the 1€ filter: heavy smoothing at rest, light smoothing
# in motion. The point of that filter is that speed opens the cutoff, so a
# deliberate move does not feel like it is dragging through treacle.
SMOOTH_MIN_CUTOFF = 0.8
SMOOTH_BETA = 3.0
SMOOTH_D_CUTOFF = 6.0
# Gaze and face direction use a fixed low pass. A fixed time constant is the
# right tool here because the one thing gaze must ignore is speed: reading is
# a series of saccades, the fastest movement the eyes make.
GAZE_TAU = 0.40

# The card's own interpolation between region targets. A critically damped
# spring starts from the position on screen and carries its velocity, so a
# change of mind mid-flight bends the motion instead of restarting it.
#
# The response is deliberately slower than a UI transition. A full row is half
# the upper half — 270 px on a 1080p display — and at 0.24 s the peak speed
# was 150 px in one 60 Hz frame, which reads as a fling rather than a move.
# At 0.38 s the same journey peaks near 95 px a frame and still lands in about
# a third of a second, which is the balance between direct and calm.
RENDER_RESPONSE = 0.38
RELEASE_RESPONSE = 0.48
SETTLE_UV = 0.0008
SETTLE_VELOCITY = 0.02

# ── Calibration and loss ─────────────────────────────────────────────────
# Neutral is measured from the first still window. Every enable recalibrates,
# because a posture change between sessions is the normal case.
CALIBRATE_SECONDS = 1.2
CALIBRATE_MIN_SAMPLES = 6
# The window only closes on a still head. A lean captured as neutral turns
# every later lean into the full travel in the wrong direction.
CALIBRATION_STILLNESS = 0.05
# A face that has not been seen for this long is treated as gone. Long enough
# that a blink or a single missed detection does not shove the card around.
LOSS_SECONDS = 1.20

# An eye open narrower than this, as a fraction of its own width, is a blink
# or a shut eye. Its iris reading is not gaze and must not move the card.
EYE_OPEN_MIN = 0.14

# The largest believable change in head position between two camera samples,
# as a share of a half-frame. A head cannot cross a fifth of the picture in
# 66 ms; the mesh occasionally can, when it re-locks on the wrong thing.
MAX_STEP = 0.22


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


class TrackingGrid:
    """The invisible movement space: five columns by two rows over the top half.

    Coordinates are normalised: ``u`` runs 0..1 left to right across the
    display, ``v`` runs 0..1 from the top of the work area to its midline. The
    lower half does not exist in this coordinate system, which is the simplest
    possible guarantee that the tracker cannot move the words there.

    The grid is plain geometry with no Qt import, so the mapping can be tested
    against any screen size without a display.
    """

    __slots__ = ("columns", "rows", "top_share")

    def __init__(self, columns: int = GRID_COLUMNS, rows: int = GRID_ROWS,
                 top_share: float = TOP_SHARE):
        self.columns = max(1, int(columns))
        self.rows = max(1, int(rows))
        self.top_share = max(0.1, min(1.0, float(top_share)))

    @property
    def count(self) -> int:
        return self.columns * self.rows

    def column(self, region: int) -> int:
        return int(region) % self.columns

    def row(self, region: int) -> int:
        return int(region) // self.columns

    def clamp_region(self, region: int) -> int:
        return max(0, min(self.count - 1, int(region)))

    def region_index(self, u: float, v: float) -> int:
        """Which region contains this normalised point."""
        u = _clamp(float(u), 0.0, 1.0)
        v = _clamp(float(v), 0.0, 1.0)
        column = min(self.columns - 1, int(u * self.columns))
        row = min(self.rows - 1, int(v * self.rows))
        return row * self.columns + column

    def anchor(self, region: int) -> tuple:
        """The centre of a region, as ``(u, v)``."""
        region = self.clamp_region(region)
        column = self.column(region)
        row = self.row(region)
        return ((column + 0.5) / self.columns,
                (row + 0.5) / self.rows)

    def region_rect(self, region: int) -> tuple:
        """``(u0, v0, u1, v1)`` for one region, inside the upper half."""
        region = self.clamp_region(region)
        column = self.column(region)
        row = self.row(region)
        return (column / self.columns, row / self.rows,
                (column + 1) / self.columns, (row + 1) / self.rows)

    def contains(self, region: int, u: float, v: float,
                 tolerance: float = 0.0) -> bool:
        u0, v0, u1, v1 = self.region_rect(region)
        return (u0 - tolerance <= u <= u1 + tolerance
                and v0 - tolerance <= v <= v1 + tolerance)


class FaceSample(NamedTuple):
    """One landmarker reading: where the head and the eyes are.

    ``x`` and ``y`` are the head anchor measured from the frame's centre with
    1.0 at the frame's edge, in screen directions (a lean to the reader's
    right reads positive x). ``gaze_x`` and ``gaze_y`` are the iris deviation
    inside the eye, 1.0 at the eye's half-width. ``yaw`` and ``pitch`` are the
    face's estimated direction from the nose/eye geometry, in eye-widths;
    positive yaw is the reader's right and positive pitch is downward.
    ``eyes`` is how many eyes the reading actually used: 0 means the face was
    found but not its gaze.
    """

    x: float
    y: float
    gaze_x: float
    gaze_y: float
    stamp: float
    eyes: int = 0
    yaw: float = 0.0
    pitch: float = 0.0


class HeadOffsets(NamedTuple):
    """Where the card should be, in the tracking grid's normalised space.

    ``region`` is -1 while the tracker is calibrating or switched off; the
    reader treats that as "no movement" and leaves the card at home. ``u`` and
    ``v`` are the smoothly interpolated position, which is what the card
    actually follows. The selected region is reported beside them so a trace
    can show the discrete decision and the continuous motion separately.
    """

    region: int
    column: int
    row: int
    u: float
    v: float

    @property
    def is_zero(self) -> bool:
        return self.region < 0


ZERO = HeadOffsets(-1, -1, -1, 0.5, 0.25)


def _alpha(cutoff: float, dt: float) -> float:
    """One-Euro's smoothing factor for a cutoff (Hz) over a step (s)."""
    tau = 1.0 / (2.0 * math.pi * max(1e-3, cutoff))
    return 1.0 / (1.0 + tau / dt)


class _LowPass:
    """First-order smoothing with a fixed time constant.

    Used for gaze and face direction, where the point is to ignore fast
    movement. The 1€ filter is the opposite kind of smoother: it deliberately
    lets speed through.
    """

    __slots__ = ("tau", "_x", "_t", "_primed")

    def __init__(self, tau: float = GAZE_TAU):
        self.tau = max(1e-3, float(tau))
        self.reset()

    def reset(self) -> None:
        self._x = 0.0
        self._t = 0.0
        self._primed = False

    def filter(self, value: float, now: float) -> float:
        if not self._primed:
            self._x = float(value)
            self._t = now
            self._primed = True
            return self._x
        dt = max(1e-3, now - self._t)
        self._t = now
        k = 1.0 - math.exp(-dt / self.tau)
        self._x += (float(value) - self._x) * k
        return self._x


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


class _Spring:
    """A critically damped scalar spring for the card's rendered position.

    It starts from the position already on screen and keeps its velocity, so
    retargeting a region mid-flight bends the motion instead of restarting it.
    """

    __slots__ = ("value", "velocity")

    def __init__(self, value: float):
        self.value = float(value)
        self.velocity = 0.0

    def jump(self, value: float) -> None:
        self.value = float(value)
        self.velocity = 0.0

    def step(self, target: float, dt: float, response: float) -> float:
        if dt <= 0.0:
            return self.value
        w0 = (2.0 * math.pi) / max(0.02, float(response))
        stiffness = w0 * w0
        drag = 2.0 * w0
        # Sub-step so one long frame (a stall, a menu opening) cannot push the
        # integrator past its own target.
        substeps = max(1, min(8, int(dt / (1.0 / 240.0))))
        h = dt / substeps
        for _ in range(substeps):
            accel = -stiffness * (self.value - target) - drag * self.velocity
            self.velocity += accel * h
            self.value += self.velocity * h
        return self.value

    def settled_on(self, target: float) -> bool:
        return (abs(self.value - target) <= SETTLE_UV
                and abs(self.velocity) <= SETTLE_VELOCITY)


class HeadMotion:
    """Turns head, eye and direction readings into a region and a smooth point.

    Feed it in the worker's cadence, poll it in the frame's::

        motion.enable()
        motion.feed(0.10, -0.05, now, gaze_x=0.2, yaw=0.1)
        offsets = motion.poll(now)

    ``feed`` and ``poll`` are separate because the camera runs at ~15 Hz and
    the reader paints at 60. Doing the easing in ``poll`` is what keeps the
    motion smooth across that gap.
    """

    def __init__(self, *, grid: Optional[TrackingGrid] = None,
                 render_response: float = RENDER_RESPONSE,
                 release_response: float = RELEASE_RESPONSE,
                 calibrate_seconds: float = CALIBRATE_SECONDS,
                 loss_seconds: float = LOSS_SECONDS,
                 dead_zone: float = SIGNAL_DEAD_ZONE,
                 hysteresis_x: float = GRID_HYSTERESIS_X,
                 hysteresis_y: float = GRID_HYSTERESIS_Y,
                 intra_share: float = INTRA_SHARE):
        self.grid = grid or TrackingGrid()
        self.render_response = max(0.02, float(render_response))
        self.release_response = max(0.02, float(release_response))
        self.calibrate_seconds = max(0.0, float(calibrate_seconds))
        self.loss_seconds = max(0.0, float(loss_seconds))
        self.dead_zone = max(0.0, float(dead_zone))
        self.hysteresis_x = max(0.0, float(hysteresis_x))
        self.hysteresis_y = max(0.0, float(hysteresis_y))
        self.intra_share = max(0.0, float(intra_share))

        self._x_filter = _OneEuro()
        self._y_filter = _OneEuro()
        self._gaze_x_filter = _LowPass()
        self._gaze_y_filter = _LowPass()
        self._yaw_filter = _LowPass()
        self._pitch_filter = _LowPass()

        self._active = False
        self._calibrating = False
        self._calib_started = 0.0
        self._calib_samples: list[tuple[float, float, float, float, float, float, float]] = []
        self._neutral_x = 0.0
        self._neutral_y = 0.0
        self._neutral_gaze_x = 0.0
        self._neutral_gaze_y = 0.0
        self._neutral_yaw = 0.0
        self._neutral_pitch = 0.0
        # Where the eyes would sit if the card had never moved. ``poll`` adds
        # the card's own travel to this, and ``feed`` measures gaze against it.
        self._gaze_ref_x = 0.0
        self._gaze_ref_y = 0.0
        self._last_sample = 0.0

        # The last two raw readings, for the outlier gate in ``feed``.
        self._last_raw: Optional[tuple[float, float]] = None
        self._prev_raw: Optional[tuple[float, float]] = None

        # The selected region and the continuous target inside it.
        self._column: Optional[int] = None
        self._row: Optional[int] = None
        self._target = self.grid.anchor(NEUTRAL_REGION)
        anchor = self._target
        self._spring_x = _Spring(anchor[0])
        self._spring_y = _Spring(anchor[1])
        self._u, self._v = anchor
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
        self._gaze_x_filter.reset()
        self._gaze_y_filter.reset()
        self._yaw_filter.reset()
        self._pitch_filter.reset()
        self._last_raw = None
        self._prev_raw = None
        self._calibrating = True
        self._calib_started = now
        self._calib_samples = []
        self._last_sample = now
        self._gaze_ref_x = 0.0
        self._gaze_ref_y = 0.0
        self._column = None
        self._row = None
        anchor = self.grid.anchor(NEUTRAL_REGION)
        self._target = anchor
        self._spring_x.jump(anchor[0])
        self._spring_y.jump(anchor[1])
        self._u, self._v = anchor
        self._offsets = ZERO
        self._polled_at = None

    def seed(self, u: float, v: float) -> None:
        """Start the card's interpolation from where it already is.

        Enabling tracking does not teleport the card to the neutral anchor:
        the spring starts at the card's current place on screen and eases to
        the anchor from there. Without this, a card parked in a corner would
        jump to the top-middle the instant calibration finished, which is the
        one visible snap the rest of this module works to avoid.

        ``v`` is normalised over the upper half, so a card parked below the
        midline seeds above 1.0 and is clamped to 1.0. That first tracked
        position is at the midline boundary: bringing a card up from the lower
        half is a deliberate one-time move, because the lower half is never a
        tracking destination.
        """
        self._spring_x.jump(_clamp(float(u), 0.0, 1.0))
        self._spring_y.jump(_clamp(float(v), 0.0, 1.0))
        self._u, self._v = self._spring_x.value, self._spring_y.value

    def disable(self) -> HeadOffsets:
        """Stop now. The offset is zero on this call, not eased to it.

        The disable path is a user's escape hatch from a camera they no
        longer want, so it must not leave the words sliding even for the
        hundred milliseconds a release would take.
        """
        self._active = False
        self._calibrating = False
        self._calib_samples = []
        self._column = None
        self._row = None
        self._target = self.grid.anchor(NEUTRAL_REGION)
        self._offsets = ZERO
        self._polled_at = None
        self._gaze_ref_x = 0.0
        self._gaze_ref_y = 0.0
        return ZERO

    # ── input ────────────────────────────────────────────────────────
    def feed(self, x: float, y: float, now: float,
             gaze_x: float = 0.0, gaze_y: float = 0.0,
             yaw: float = 0.0, pitch: float = 0.0) -> None:
        """Take one detector reading: head anchor, eyes and face direction.

        ``x`` and ``y`` are measured from the frame's centre with 1.0 at the
        frame's edge, so the numbers do not depend on the camera's resolution.
        ``gaze_x`` / ``gaze_y`` are the iris deviation inside the eye, 1.0 at
        the eye's half-width. ``yaw`` / ``pitch`` are the face's direction in
        eye-widths, from the nose's offset against the eye midpoint.
        """
        if not self._active:
            return
        if not (math.isfinite(x) and math.isfinite(y)):
            return
        for name, value in (("gaze_x", gaze_x), ("gaze_y", gaze_y),
                            ("yaw", yaw), ("pitch", pitch)):
            if not math.isfinite(value):
                if name == "gaze_x":
                    gaze_x = 0.0
                elif name == "gaze_y":
                    gaze_y = 0.0
                elif name == "yaw":
                    yaw = 0.0
                else:
                    pitch = 0.0
        self._last_sample = now
        x = _clamp(float(x), -1.0, 1.0)
        y = _clamp(float(y), -1.0, 1.0)

        # Outlier gate. The mesh is stable to about a pixel a frame, so a jump
        # this large is the detector re-locking, not a head. A single such
        # sample is dropped; two in agreement are accepted, because a genuinely
        # fast turn also produces a big step and must not be ignored.
        last, prev = self._last_raw, self._prev_raw
        if last is not None and math.hypot(x - last[0], y - last[1]) > MAX_STEP:
            agreed = (prev is not None
                      and math.hypot(x - prev[0], y - prev[1]) <= MAX_STEP)
            if not agreed:
                self._prev_raw = (x, y)
                return
        self._prev_raw = last
        self._last_raw = (x, y)

        if self._calibrating:
            if not self._calib_samples:
                # The window belongs to the first sample, not to the moment the
                # switch was flipped: the camera takes a second or two to open,
                # and a window that expired during that would stand no chance.
                self._calib_started = now
            self._calib_samples.append((now, x, y, float(gaze_x),
                                        float(gaze_y), float(yaw), float(pitch)))
            # Rolling window: only the last ``calibrate_seconds`` of readings
            # describe where this reader's head is now. A lean that happened
            # to fall inside it used to become the neutral, after which a
            # 45 mm lean moved the card the full travel in the wrong
            # direction.
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
                # silently inert. Bounded so a reader who will not hold still
                # still gets tracking, on the best neutral available.
                self._finish_calibration()
            else:
                return

        x_rel = self._x_filter.filter(x - self._neutral_x, now)
        y_rel = self._y_filter.filter(y - self._neutral_y, now)
        gaze_x_rel = self._gaze_x_filter.filter(
            _clamp(gaze_x - self._neutral_gaze_x - self._gaze_ref_x,
                   -1.5, 1.5), now)
        gaze_y_rel = self._gaze_y_filter.filter(
            _clamp(gaze_y - self._neutral_gaze_y - self._gaze_ref_y,
                   -1.5, 1.5), now)
        yaw_rel = self._yaw_filter.filter(
            _clamp(yaw - self._neutral_yaw, -1.0, 1.0), now)
        pitch_rel = self._pitch_filter.filter(
            _clamp(pitch - self._neutral_pitch, -1.5, 1.5), now)

        # Fuse all three inputs. Head position is the coarse term, the face's
        # direction keeps a turned head meaningful when its centre has not
        # moved much, and the irises are the fine term that decides where in
        # the chosen region the card belongs.
        head_x = HEAD_WEIGHT * _clamp(x_rel / HEAD_FULL_X, -2.5, 2.5)
        head_y = HEAD_WEIGHT * _clamp(y_rel / HEAD_FULL_Y, -2.5, 2.5)
        direction_x = DIRECTION_WEIGHT * _clamp(yaw_rel / YAW_FULL, -2.0, 2.0)
        direction_y = DIRECTION_WEIGHT * _clamp(pitch_rel / PITCH_FULL, -2.0, 2.0)
        eye_x = EYE_WEIGHT * _clamp(gaze_x_rel / GAZE_FULL, -2.0, 2.0)
        eye_y = EYE_WEIGHT * _clamp(gaze_y_rel / GAZE_FULL, -2.0, 2.0)
        # The region is chosen from the head and its direction only. The eyes
        # refine the position inside that region; they do not get a vote on
        # crossing into the next one. The reason is feedback: the card moving
        # toward a glance changes the glance, and a signal that can cross a
        # region boundary can then hunt between two cells for ever. Bounded
        # eye movement inside a cell is stable, visible and useful.
        region_x = head_x + direction_x
        region_y = head_y + direction_y
        sx = region_x + eye_x
        sy = region_y + eye_y

        # The neutral anchor is the zero of each axis, not the middle of the
        # whole grid. The reading card lives in the top row, so a still head
        # has somewhere to be above as well as below it, and no signal means
        # no intra-region offset at all.
        anchor_u, anchor_v = self.grid.anchor(NEUTRAL_REGION)
        u = _map_axis(sx, anchor_u)
        v = _map_axis(sy, anchor_v)
        region_u = _map_axis(region_x, anchor_u)
        region_v = _map_axis(region_y, anchor_v)

        if math.hypot(region_x, region_y) <= self.dead_zone:
            # Stillness belongs to the neutral region. This is also what makes
            # a head returning to rest return the card, instead of stranding
            # it in the last region it visited.
            column = self.grid.column(NEUTRAL_REGION)
            row = self.grid.row(NEUTRAL_REGION)
            self._column, self._row = column, row
        else:
            column, row = self._select_region(region_u, region_v)
            if (column, row) != (self._column, self._row):
                self._column, self._row = column, row
        self._target = self._target_in_region(column, row, u, v)

    def _calibration_is_still(self) -> bool:
        """Whether the window describes a head that is holding still."""
        xs = [sample[1] for sample in self._calib_samples]
        ys = [sample[2] for sample in self._calib_samples]
        return (max(xs) - min(xs) <= CALIBRATION_STILLNESS
                and max(ys) - min(ys) <= CALIBRATION_STILLNESS)

    def _finish_calibration(self) -> None:
        """Take the neutral as the middle sample, not the average.

        A median ignores the one frame where the detector put the box in the
        wrong place, and with a handful of samples that is most of the
        difference between a neutral that works and one that leaves the words
        parked against a stop.
        """
        xs = sorted(sample[1] for sample in self._calib_samples)
        ys = sorted(sample[2] for sample in self._calib_samples)
        gxs = sorted(sample[3] for sample in self._calib_samples)
        gys = sorted(sample[4] for sample in self._calib_samples)
        yaws = sorted(sample[5] for sample in self._calib_samples)
        pitches = sorted(sample[6] for sample in self._calib_samples)
        self._neutral_x = xs[len(xs) // 2]
        self._neutral_y = ys[len(ys) // 2]
        self._neutral_gaze_x = gxs[len(gxs) // 2]
        self._neutral_gaze_y = gys[len(gys) // 2]
        self._neutral_yaw = yaws[len(yaws) // 2]
        self._neutral_pitch = pitches[len(pitches) // 2]
        self._calibrating = False
        self._calib_samples = []
        self._column = self.grid.column(NEUTRAL_REGION)
        self._row = self.grid.row(NEUTRAL_REGION)
        anchor = self.grid.anchor(NEUTRAL_REGION)
        self._target = anchor
        # Start the filters at neutral instead of at zero: the first filtered
        # sample must not be a step from the frame centre.
        self._x_filter.reset()
        self._y_filter.reset()
        self._gaze_x_filter.reset()
        self._gaze_y_filter.reset()
        self._yaw_filter.reset()
        self._pitch_filter.reset()
        # Do not move the spring here. It holds the card's current place
        # (seeded by the reader, or the anchor before any seed), and the next
        # poll eases from there to the neutral anchor. Jumping it would turn
        # enabling into a teleport for a card that had been parked elsewhere.

    def _select_region(self, u: float, v: float) -> tuple:
        """Pick a region, with hysteresis around every boundary.

        The rendered position may sit a little outside its cell during a
        transition; the region is what decides where it is allowed to come to
        rest, so it must not chatter when the signal hovers on a boundary.
        """
        if self._column is None or self._row is None:
            return (self.grid.region_index(u, v) % self.grid.columns,
                    self.grid.region_index(u, v) // self.grid.columns)
        cell_x = u * self.grid.columns
        cell_y = v * self.grid.rows
        column = self._column
        row = self._row
        # One step per sample is enough: a camera that jumps two cells in one
        # frame has already produced an outlier, and the gate in ``feed`` is
        # the right place to deal with that.
        if cell_x > column + 1.0 + self.hysteresis_x and column < self.grid.columns - 1:
            column += 1
        elif cell_x < column - self.hysteresis_x and column > 0:
            column -= 1
        if cell_y > row + 1.0 + self.hysteresis_y and row < self.grid.rows - 1:
            row += 1
        elif cell_y < row - self.hysteresis_y and row > 0:
            row -= 1
        return column, row

    def _target_in_region(self, column: int, row: int,
                          u: float, v: float) -> tuple:
        """The continuous point the card should head for.

        The region anchor is the destination; a bounded share of the residual
        inside the cell keeps the card from feeling glued to ten fixed spots.
        The cap is below half a cell, so this can never cross into a
        neighbouring region on its own.
        """
        region = row * self.grid.columns + column
        anchor_u, anchor_v = self.grid.anchor(region)
        cell_w = 1.0 / self.grid.columns
        cell_h = 1.0 / self.grid.rows
        return (anchor_u + _intra_offset(u - anchor_u, cell_w, self.intra_share),
                anchor_v + _intra_offset(v - anchor_v, cell_h, self.intra_share))

    # ── output ───────────────────────────────────────────────────────
    def poll(self, now: float) -> HeadOffsets:
        """Advance to ``now`` and return the position to render."""
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

        if self._calibrating:
            return ZERO

        releasing = now - self._last_sample > self.loss_seconds
        if releasing:
            # No face: return to the neutral region rather than stranding the
            # card wherever the last reading happened to leave it. Slower than
            # a normal frame, so a blink or a missed detection does not shove
            # the words around.
            self._column = self.grid.column(NEUTRAL_REGION)
            self._row = self.grid.row(NEUTRAL_REGION)
            self._target = self.grid.anchor(NEUTRAL_REGION)

        if dt <= 0.0:
            return self._offsets

        tx, ty = self._target
        response = self.release_response if releasing else self.render_response
        self._spring_x.step(tx, dt, response)
        self._spring_y.step(ty, dt, response)
        if self._spring_x.settled_on(tx):
            self._spring_x.jump(tx)
        if self._spring_y.settled_on(ty):
            self._spring_y.jump(ty)

        # Strict limits on the way out as well as the way in. Nothing this
        # module returns may leave the upper half, whatever the grid or the
        # filter did.
        previous_u, previous_v = self._u, self._v
        self._u = _clamp(self._spring_x.value, 0.0, 1.0)
        self._v = _clamp(self._spring_y.value, 0.0, 1.0)
        # The card is now nearer the reader's gaze, so the eyes need less
        # deviation to read it. Fold that movement into the gaze reference so
        # the relaxation is not mistaken for a look in the other direction.
        self._gaze_ref_x = _clamp(
            self._gaze_ref_x + (self._u - previous_u) * GAZE_CARD_COUPLING,
            -GAZE_REFERENCE_LIMIT, GAZE_REFERENCE_LIMIT)
        self._gaze_ref_y = _clamp(
            self._gaze_ref_y + (self._v - previous_v) * GAZE_CARD_COUPLING,
            -GAZE_REFERENCE_LIMIT, GAZE_REFERENCE_LIMIT)
        column = int(self._column if self._column is not None
                     else self.grid.column(NEUTRAL_REGION))
        row = int(self._row if self._row is not None
                  else self.grid.row(NEUTRAL_REGION))
        region = row * self.grid.columns + column
        self._offsets = HeadOffsets(region, column, row, self._u, self._v)
        return self._offsets

    def offsets(self) -> HeadOffsets:
        return self._offsets


def _map_axis(signal: float, neutral: float) -> float:
    """Map a fused signal to 0..1 with its neutral exactly at ``neutral``.

    A symmetric map around 0.5 would put the neutral of the vertical axis on
    the boundary between the two rows, where every small nod becomes a fresh
    region decision. Anchoring the map on the neutral region's own centre
    keeps stillness away from the boundary and still lets a deliberate
    movement reach both ends of the axis.
    """
    shaped = math.tanh(SIGNAL_GAIN * float(signal))
    if shaped >= 0.0:
        return _clamp(neutral + (1.0 - neutral) * shaped, 0.0, 1.0)
    return _clamp(neutral + neutral * shaped, 0.0, 1.0)


def _intra_offset(delta: float, cell: float, share: float) -> float:
    """Bounded, dead-banded movement inside one cell.

    The dead band is subtracted rather than simply zeroed: leaving the band
    must start from zero, not from a step the width of the band.
    """
    if cell <= 0.0 or share <= 0.0:
        return 0.0
    offset = float(delta) * share
    band = cell * INTRA_DEAD_BAND
    if abs(offset) <= band:
        return 0.0
    offset = math.copysign(abs(offset) - band, offset)
    limit = cell * INTRA_LIMIT
    return math.copysign(min(limit, abs(offset)), offset)


_detector_lock = threading.Lock()
_landmarker = None
_last_timestamp = 0
# Exactly one worker may own the camera. Two handles on one device is how
# the camera light ends up blinking, and how each handle keeps taking
# frames from the other.
_live_source_lock = threading.Lock()
_live_source = None
# The backend that last opened successfully. Remembering it means a reopen
# does not probe every backend again, and probing is what makes the camera
# light flash on a device that is merely busy.
_backend_lock = threading.Lock()
_preferred_backend: Optional[str] = None

# The model ships with the app, beside the speech model, so nothing is
# downloaded at run time and the feature keeps working offline. It is the
# MediaPipe face landmarker: 478 points per face, both irises included.
MODEL_NAME = "face_landmarker.task"

# Which way a lean moves the picture. The camera image is not mirrored: a
# reader leaning to their right appears to the *left* of the frame, so the
# card has to travel the opposite way to stay in front of them. Getting this
# sign wrong does not look like a bug — it looks like the words running away
# from the reader twice as fast, which is why it is named rather than inlined.
HEAD_X_SIGN = -1.0

# Both eyes, as (outer corner, inner corner, upper lid, lower lid, iris).
# MediaPipe's canonical indices; the irises are the last ten of the 478.
_EYE_GEOMETRY = (
    (33, 133, 159, 145, 468),
    (263, 362, 386, 374, 473),
)
# An eye narrower than this many pixels is too small for its iris to say
# anything: the deviation would be measuring landmark noise.
MIN_EYE_PX = 18.0


def model_dir() -> str:
    """Where the face model lives. See :func:`notch.paths.model_dir`.

    ``model/`` is copied next to Notch.exe by the build, not into the bundle —
    the same rule the speech model follows — so looking beside this package
    file would find nothing in a frozen build.
    """
    from notch import paths
    return paths.model_dir()


def model_path() -> str:
    return os.path.join(model_dir(), MODEL_NAME)


def load_landmarker():
    """Load the face landmarker once per process.

    Raises ``ImportError`` when MediaPipe is absent and ``RuntimeError`` when
    the model file is missing, so the reader can say which of the two is wrong
    instead of switching on a feature that will never see anything.

    ``num_faces=1`` is deliberate: the reader has one face, and asking for the
    most prominent one stops a face in a photo on the shelf from taking the
    reading.
    """
    global _landmarker
    with _detector_lock:
        if _landmarker is not None:
            return _landmarker
        from mediapipe.tasks.python import vision
        from mediapipe.tasks.python.core.base_options import BaseOptions

        path = model_path()
        if not os.path.exists(path):
            raise RuntimeError(
                "the face landmark model is missing (model/%s)" % MODEL_NAME)
        options = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=path),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=False,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        _landmarker = vision.FaceLandmarker.create_from_options(options)
        # Closed before the interpreter tears the C dispatcher down; letting
        # __del__ do it prints a TypeError traceback on every exit.
        atexit.register(_close_landmarker)
        return _landmarker


def _close_landmarker() -> None:
    """Close the shared model once, before the interpreter goes away."""
    global _landmarker
    with _detector_lock:
        landmarker, _landmarker = _landmarker, None
    if landmarker is None:
        return
    try:
        landmarker.close()
    except Exception:
        pass
    try:
        landmarker.__class__.__del__ = lambda self: None
    except Exception:
        pass


def _next_timestamp() -> int:
    """A strictly increasing millisecond clock for the video-mode model.

    The landmarker rejects a timestamp that does not advance, and it outlives
    any one worker: a stopped-and-restarted tracker must not begin its clock
    again from zero or the first frame after re-enabling is refused.
    """
    global _last_timestamp
    with _detector_lock:
        _last_timestamp = max(_last_timestamp + 1, int(time.monotonic() * 1000.0))
        return _last_timestamp


def gaze_from_landmarks(landmarks, width: float, height: float) -> tuple:
    """Iris deviation inside each usable eye: ``(gx, gy, eyes_used)``.

    ``1.0`` is the eye's own half-width, so the number does not depend on how
    big the face is on screen. A blink or a shut eye is rejected rather than
    read: its iris is behind a lid, and the lid's position is not a gaze.
    """
    gx_total = gy_total = 0.0
    used = 0
    for outer, inner, top, bottom, iris in _EYE_GEOMETRY:
        ox, oy = landmarks[outer].x * width, landmarks[outer].y * height
        ix, iy = landmarks[inner].x * width, landmarks[inner].y * height
        tx, ty = landmarks[top].x * width, landmarks[top].y * height
        bx, by = landmarks[bottom].x * width, landmarks[bottom].y * height
        px, py = landmarks[iris].x * width, landmarks[iris].y * height
        eye_w = math.hypot(ix - ox, iy - oy)
        if eye_w < MIN_EYE_PX:
            continue
        lid_gap = math.hypot(bx - tx, by - ty)
        if lid_gap / max(1e-6, eye_w) < EYE_OPEN_MIN:
            continue
        gx_total += (px - (ox + ix) / 2.0) / (eye_w / 2.0)
        gy_total += (py - (ty + by) / 2.0) / max(1e-6, lid_gap / 2.0)
        used += 1
    if not used:
        return 0.0, 0.0, 0
    return gx_total / used, gy_total / used, used


def reading_from_landmarks(landmarks, width: float, height: float) -> FaceSample:
    """Turn one face's 478 points into head position, gaze and direction.

    The head anchor is the midpoint of the two outer eye corners: unlike the
    nose or the irises, it does not move when the reader looks somewhere. The
    direction estimate is the nose tip's offset from that anchor, divided by
    the distance between the corners. Nose and eyes translate together, so
    this is translation invariant and measures where the face is pointing
    rather than where it is.
    """
    if not landmarks:
        return FaceSample(0.0, 0.0, 0.0, 0.0, 0.0, 0)
    rox = landmarks[33].x * width
    roy = landmarks[33].y * height
    lox = landmarks[263].x * width
    loy = landmarks[263].y * height
    ax = (rox + lox) / 2.0
    ay = (roy + loy) / 2.0
    nx = (ax - width / 2.0) / (width / 2.0) * HEAD_X_SIGN
    ny = (ay - height / 2.0) / (height / 2.0)
    gx, gy, used = gaze_from_landmarks(landmarks, width, height)

    yaw = pitch = 0.0
    if len(landmarks) > 1:
        eye_dist = max(1e-6, math.hypot(lox - rox, loy - roy))
        nose_x = landmarks[1].x * width
        nose_y = landmarks[1].y * height
        yaw = (nose_x - ax) / eye_dist * HEAD_X_SIGN
        pitch = (nose_y - ay) / eye_dist
    return FaceSample(_clamp(nx, -1.0, 1.0), _clamp(ny, -1.0, 1.0),
                      gx * HEAD_X_SIGN, gy, 0.0, used, yaw, pitch)


class CameraHeadSource:
    """Reads head position, eyes and face direction from the webcam, locally.

    Deliberate properties, each one a constraint from the brief:

    * **Local.** Only numbers leave this object. No frame is written to disk,
      no frame is sent anywhere. MediaPipe runs in-process against a model
      that ships with the app.
    * **One device.** A process-wide lease means a second tracker cannot open
      a second handle on the camera while the first is still closing. The old
      stop() cleared its thread reference before the join had finished, so a
      fast off/on could open a new capture on top of a live one — two handles
      fighting over a device that only one can own is exactly how the camera
      ends up switching itself on and off.
    * **Self-healing.** A dropped frame, a device that stalls or a backend
      that hands back nothing is retried and the capture is reopened. Once a
      camera has delivered a frame, this worker never declares it failed:
      turning the feature off after twelve seconds of a transient problem was
      what made tracking appear to stop after 10–15 s. Retries back off to a
      ceiling, and the backend that worked is tried first, so the camera light
      does not flicker while a problem passes.
    * **Not hangable.** A blocking ``read()`` would otherwise pin the worker
      for ever and keep the device from being reopened. A watchdog observes a
      heartbeat and releases a capture that has stopped answering.
    """

    # 640x480 at 15 fps. The landmarker works on the whole frame and finds a
    # face down to roughly a tenth of the frame's width, which is far below
    # the frontal cascade's ~56 px floor that made a reader at 80 cm invisible
    # at 320 px wide.
    FRAME_W = 640
    FRAME_H = 480
    FPS = 15
    # A reading older than this is not where the head is now. Reading it again
    # would let a face that has left the frame keep steering the words. Kept
    # above the calibration window and the blink gap: at 15 fps this is about
    # eighteen frames, so a single lost frame cannot look like a lost face.
    STALE_SECONDS = 1.2
    # Consecutive seconds without a frame before the device is reopened. At
    # 15 fps this is over a second of trouble, which a camera shared with a
    # video call recovers from routinely; the old six-frame rule reopened on
    # a third of a second and produced the flicker this replaces.
    REOPEN_AFTER_SECONDS = 1.5
    REOPEN_BACKOFF_START = 0.4
    REOPEN_BACKOFF_MAX = 2.0
    # How long the worker tries before reporting failure when it has *never*
    # opened the camera. After the first frame there is no deadline: a camera
    # unplugged for a minute is still worth reconnecting to.
    GIVE_UP_SECONDS = 12.0
    # A read that has not returned for this long is treated as wedged. The
    # watchdog releases it, which is the only way to get an OpenCV read out of
    # a blocking wait and back to the reopen path.
    STALL_SECONDS = 2.5

    def __init__(self, index: int = 0, *, frame_w: Optional[int] = None,
                 frame_h: Optional[int] = None, fps: Optional[int] = None):
        self.index = int(index)
        self.frame_w = int(frame_w or self.FRAME_W)
        self.frame_h = int(frame_h or self.FRAME_H)
        self.fps = int(fps or self.FPS)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._capture_guard = threading.Lock()
        self._current_capture = None
        self._heartbeat = 0.0
        self._latest: Optional[FaceSample] = None
        self._error: Optional[str] = None
        self._backend = ""
        self._detect_seconds = 0.0
        # "face" while the mesh is finding one, "none" before the first,
        # "reopen" while the device is being recovered.
        self._last_source = "none"
        self._recovering = False
        # False until the model is loaded. The first load costs ~5.8 s on this
        # machine (5.1 s of it importing MediaPipe), and a reader who toggles
        # tracking on deserves to be told that rather than shown a dead card.
        self._ready = False
        self._ever_opened = False

    # ── lifecycle ────────────────────────────────────────────────────
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def error(self) -> Optional[str]:
        """The reason tracking cannot work, or None. Only fatal failures."""
        with self._lock:
            return self._error

    @property
    def recovering(self) -> bool:
        """True while the device is being reopened after a dropped frame."""
        with self._lock:
            return self._recovering

    @property
    def ready(self) -> bool:
        """True once the face model is loaded and frames are being read."""
        with self._lock:
            return self._ready

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def last_source(self) -> str:
        with self._lock:
            return self._last_source

    @property
    def detect_seconds(self) -> float:
        """Cost of the last frame, camera wait plus inference, in seconds."""
        with self._lock:
            return self._detect_seconds

    def start(self) -> bool:
        """Begin capture. Opening the device happens on the worker."""
        global _live_source
        if self._thread is not None and self._thread.is_alive():
            if not self._stop.is_set():
                return True
            # A previous stop is still unwinding. Waiting here is what makes
            # off/on safe: the new worker cannot open a second handle while
            # the old one is still inside a read.
            self._thread.join(timeout=2.5)
            if self._thread.is_alive():
                self._fail("the camera is still being released")
                return False
            self._thread = None
        with _live_source_lock:
            other = _live_source
            if other is not None and other is not self and other.running:
                self._fail("the camera is already in use by another tracker")
                return False
            _live_source = self
        self._stop.clear()
        with self._lock:
            self._error = None
            self._latest = None
            self._recovering = False
            self._ready = False
        self._ever_opened = False
        self._thread = threading.Thread(target=self._run, name="notch-head",
                                        daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        """Stop and release the device. Safe to call twice."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        if thread is not None and thread.is_alive():
            # Still inside a blocking read. Keep the reference: `running`
            # stays true, so a new source refuses to open a second handle on
            # a device this one has not let go of yet. The watchdog normally
            # makes this branch unreachable.
            return
        self._thread = None
        global _live_source
        with _live_source_lock:
            if _live_source is self:
                _live_source = None
        with self._lock:
            self._latest = None
            self._recovering = False

    # ── reading ──────────────────────────────────────────────────────
    def read(self) -> Optional[FaceSample]:
        """Latest reading, or None when there is no fresh one."""
        with self._lock:
            latest = self._latest
        if latest is None:
            return None
        if time.monotonic() - latest.stamp > self.STALE_SECONDS:
            return None
        return latest

    # ── worker ───────────────────────────────────────────────────────
    def _fail(self, message: str) -> None:
        with self._lock:
            self._error = message

    def _import_cv2(self):
        """The camera library, behind a method so tests can supply a device."""
        import cv2
        return cv2

    def _load_detector(self):
        """The face model, behind a method so tests can supply a detector."""
        return load_landmarker()

    def _open(self, cv2):
        """Open the camera on the backend that delivers frames first.

        DirectShow is first here, not MediaFoundation, on measurement rather
        than theory: on the machine this was fixed for, MSMF's constructor
        took 5.5 s and the first usable reading arrived at 11.8 s, while
        DSHOW produced one in about a second. Both are shared on Windows 10+
        — a second process opened and read the same device alongside this
        worker — so the sharing argument that put MSMF first does not hold,
        and a camera that takes twelve seconds to say anything reads as a
        broken feature. MSMF stays as the fallback for machines where DSHOW
        cannot open the device at all.

        The last backend that worked is tried first on every later reopen.
        Probing all three backends opens and releases the device two extra
        times, which is visible as the camera light blinking.
        """
        global _preferred_backend
        attempts = []
        if hasattr(cv2, "CAP_DSHOW"):
            attempts.append(("DSHOW", cv2.CAP_DSHOW))
        if hasattr(cv2, "CAP_MSMF"):
            attempts.append(("MSMF", cv2.CAP_MSMF))
        attempts.append(("default", 0))
        with _backend_lock:
            preferred = _preferred_backend
        if preferred:
            attempts.sort(key=lambda item: 0 if item[0] == preferred else 1)
        for name, backend in attempts:
            if self._stop.is_set():
                return None, ""
            try:
                capture = cv2.VideoCapture(self.index, backend)
            except Exception:
                continue
            if capture is not None and capture.isOpened():
                with _backend_lock:
                    _preferred_backend = name
                return capture, name
            try:
                if capture is not None:
                    capture.release()
            except Exception:
                pass
        return None, ""

    def _configure(self, cv2, capture) -> None:
        try:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(self.frame_w))
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self.frame_h))
            capture.set(cv2.CAP_PROP_FPS, float(self.fps))
            if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
                capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

    @staticmethod
    def _frame_size(cv2, capture, fallback_w: int, fallback_h: int) -> tuple:
        try:
            width = float(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or fallback_w)
            height = float(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or fallback_h)
        except Exception:
            width, height = float(fallback_w), float(fallback_h)
        if width <= 0 or height <= 0:
            return float(fallback_w), float(fallback_h)
        return width, height

    def _set_capture(self, capture) -> None:
        with self._capture_guard:
            self._current_capture = capture
            self._heartbeat = time.monotonic()

    def _clear_capture(self, capture=None) -> None:
        with self._capture_guard:
            if capture is None or self._current_capture is capture:
                self._current_capture = None

    def _watch_stall(self, stop: threading.Event) -> None:
        """Release a capture whose ``read`` has stopped answering.

        OpenCV's read has no timeout. Without this, one wedged device leaves
        the worker alive but deaf for ever: ``stop()`` cannot join it, and a
        new enable refuses to open a second handle. Releasing from this thread
        is what turns that into an ordinary reopen.
        """
        while not stop.wait(0.5):
            now = time.monotonic()
            with self._capture_guard:
                capture = self._current_capture
                beat = self._heartbeat
            if capture is None or now - beat <= self.STALL_SECONDS:
                continue
            self._clear_capture(capture)
            with self._lock:
                self._recovering = True
                self._last_source = "reopen"
            try:
                capture.release()
            except Exception:
                pass

    def _reopen_delay(self, attempt: int) -> float:
        delay = self.REOPEN_BACKOFF_START * (2 ** max(0, attempt - 1))
        return min(self.REOPEN_BACKOFF_MAX, delay)

    def _run(self) -> None:
        try:
            landmarker = self._load_detector()
        except ImportError:
            self._fail("MediaPipe is not installed")
            return
        except Exception as exc:
            self._fail(str(exc) or "the face landmark model could not be loaded")
            return
        if self._stop.is_set():
            return
        with self._lock:
            self._ready = True
        try:
            cv2 = self._import_cv2()
        except ImportError:
            self._fail("OpenCV is not installed")
            return
        # The landmarker is process-wide and cached: closing it here would
        # leave the next enable with a closed model. It is closed once, at exit.
        try:
            self._capture(cv2, landmarker)
        except Exception as exc:
            # A worker that dies silently is worse than one that reports why:
            # the reader would see a toggle that is on and doing nothing, with
            # no error and no recovery. The capture is released by _capture's
            # finally before the exception reaches here.
            self._fail(str(exc) or "the camera worker stopped unexpectedly")

    def _capture(self, cv2, landmarker) -> None:
        started_at = time.monotonic()
        capture = None
        reopens = 0
        next_open = started_at
        last_frame = started_at
        watch_stop = threading.Event()
        watcher = threading.Thread(target=self._watch_stall, args=(watch_stop,),
                                   name="notch-head-watch", daemon=True)
        watcher.start()
        interval = 1.0 / max(1.0, float(self.fps))

        import mediapipe as mp

        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if capture is None:
                    if now < next_open:
                        self._stop.wait(min(0.1, next_open - now))
                        continue
                    capture, backend = self._open(cv2)
                    if capture is None:
                        if (not self._ever_opened
                                and time.monotonic() - started_at
                                > self.GIVE_UP_SECONDS):
                            self._fail("camera unavailable (busy or not connected)")
                            return
                        reopens += 1
                        next_open = time.monotonic() + self._reopen_delay(reopens)
                        with self._lock:
                            self._recovering = True
                            self._last_source = "reopen"
                        continue
                    self._backend = backend
                    self._configure(cv2, capture)
                    width, height = self._frame_size(cv2, capture,
                                                     self.frame_w, self.frame_h)
                    self._ever_opened = True
                    reopens = 0
                    last_frame = time.monotonic()
                    self._set_capture(capture)
                    with self._lock:
                        self._recovering = False
                        self._last_source = "none"

                started = time.monotonic()
                self._set_capture(capture)
                try:
                    ok, frame = capture.read()
                except Exception:
                    # A backend can raise instead of returning False when the
                    # device disappears. That is a dropped frame, not a crash.
                    ok, frame = False, None
                with self._capture_guard:
                    stalled = self._current_capture is not capture
                if stalled:
                    # The watchdog released this capture while read was
                    # blocked. Drop it and reopen instead of trusting whatever
                    # came back from a released handle.
                    self._clear_capture(capture)
                    capture = None
                    reopens += 1
                    next_open = time.monotonic() + self._reopen_delay(reopens)
                    with self._lock:
                        self._recovering = True
                        self._last_source = "reopen"
                    continue
                if not ok or frame is None:
                    if time.monotonic() - last_frame > self.REOPEN_AFTER_SECONDS:
                        try:
                            capture.release()
                        except Exception:
                            pass
                        self._clear_capture(capture)
                        capture = None
                        reopens += 1
                        next_open = time.monotonic() + self._reopen_delay(reopens)
                        with self._lock:
                            self._recovering = True
                            self._last_source = "reopen"
                        continue
                    self._stop.wait(min(0.05, interval))
                    continue
                last_frame = time.monotonic()

                try:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                    result = landmarker.detect_for_video(image, _next_timestamp())
                    faces = list(result.face_landmarks or [])
                except Exception:
                    faces = []
                # Report the whole frame cost, camera wait plus inference, so
                # the measurement says what latency actually looks like.
                with self._lock:
                    self._detect_seconds = time.monotonic() - started
                    self._recovering = False

                if faces:
                    reading = reading_from_landmarks(faces[0], width, height)
                    with self._lock:
                        self._latest = reading._replace(stamp=time.monotonic())
                        self._last_source = "face"
                        self._recovering = False

                wait = interval - (time.monotonic() - started)
                if wait > 0:
                    self._stop.wait(wait)
        finally:
            watch_stop.set()
            self._clear_capture(capture)
            try:
                if capture is not None:
                    capture.release()
            except Exception:
                pass
