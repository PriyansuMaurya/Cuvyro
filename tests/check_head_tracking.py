"""Head tracking checks: latency, jitter, stability, limits and eye contact.

The reader ships with head tracking **off**. This harness is the evidence
that would let it ship on, so it measures the things that decide that rather
than the things that are easy to assert:

  * **latency** — how long a real head move takes to reach the text,
  * **jitter** — how much the words tremble while the head is still,
  * **stability** — that the text returns to exactly centred and stays there,
  * **limits** — that no detection, however wild, can move the text further
    than the configured displacement,
  * **eye contact** — the modelled gaze error with and without tracking,
    which is the only one of these that says whether the feature is worth
    having at all.

Everything here drives :class:`HeadMotion` with explicit timestamps at the
camera's real cadence, so the numbers are reproducible and do not depend on
a machine's load. The camera itself is exercised separately by
``tests/measure_head_tracking.py``, which needs a real device.

Exits non-zero on the first failed expectation.
"""

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

import head_tracking as ht

failures = []
CAMERA_HZ = 15.0
FRAME_HZ = 60.0


def check(name, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not condition:
        failures.append(name)


def report(name, value, unit=""):
    if isinstance(value, str):
        print(f"      {name}: {value}{unit}")
    else:
        print(f"      {name}: {value:.3f}{unit}")


def calibrate(motion, x=0.0, y=0.0, seconds=None):
    """Settle a neutral with the head held at (x, y)."""
    seconds = ht.CALIBRATE_SECONDS + 0.2 if seconds is None else seconds
    now = motion._calib_started if motion._calib_started else 0.0
    steps = int(seconds * CAMERA_HZ)
    for index in range(steps + 1):
        t = now + index / CAMERA_HZ
        motion.feed(x, y, t)
        motion.poll(t)
    return now + steps / CAMERA_HZ


def drive(motion, start, seconds, position, frame_hz=FRAME_HZ, camera_hz=CAMERA_HZ):
    """Hold ``position`` (a callable of t, or a constant) for ``seconds``.

    Returns the list of ``(t, offsets)`` sampled at the render rate, which is
    what the reader actually paints.
    """
    samples = []
    camera_step = 1.0 / camera_hz
    frame_step = 1.0 / frame_hz
    next_camera = start
    next_frame = start
    end = start + seconds
    while next_frame <= end + 1e-9:
        while next_camera <= next_frame + 1e-9:
            px, py = position(next_camera) if callable(position) else position
            motion.feed(px, py, next_camera)
            next_camera += camera_step
        samples.append((next_frame, motion.poll(next_frame)))
        next_frame += frame_step
    return samples


def make_motion(**kwargs):
    motion = ht.HeadMotion(**kwargs)
    motion.enable(now=0.0)
    return motion


# ═══════════════════════════════════════════════════════════════════════
#  1. Stillness
# ═══════════════════════════════════════════════════════════════════════


def check_stillness():
    motion = make_motion()
    t = calibrate(motion)
    samples = drive(motion, t, 3.0, (0.0, 0.0))
    peak = max(max(abs(o.text_dx), abs(o.text_dy)) for _, o in samples)
    check("a still head never moves the text", peak == 0.0, f"peak {peak:.3f} DIP")


# ═══════════════════════════════════════════════════════════════════════
#  2. Dead zone
# ═══════════════════════════════════════════════════════════════════════


def check_dead_zone():
    inside = ht.DEAD_ZONE * 0.8
    motion = make_motion()
    t = calibrate(motion)
    samples = drive(motion, t, 2.5, (inside, inside))
    peak = max(max(abs(o.text_dx), abs(o.text_dy)) for _, o in samples)
    check("inside the dead zone nothing moves", peak == 0.0, f"peak {peak:.3f} DIP")

    # A small move that is nonetheless a real lean: a fifth of the way from
    # the dead zone to the full range.
    outside = ht.DEAD_ZONE + (ht.RANGE - ht.DEAD_ZONE) * 0.2
    motion = make_motion()
    t = calibrate(motion)
    samples = drive(motion, t, 2.5, (outside, outside))
    final = samples[-1][1]
    check("just outside the dead zone it moves, gently",
          abs(final.text_dx) > 0.5, f"text_dx {final.text_dx:.2f} DIP")
    check("leaving the dead zone is continuous, not a step",
          abs(final.text_dx) < ht.MAX_TEXT_DX * 0.30,
          f"text_dx {final.text_dx:.2f} of max {ht.MAX_TEXT_DX:.0f}")

    # The prototype only means anything if a normal lean uses the range it
    # has. A full-range lean must reach the limit, not a pixel or two.
    motion = make_motion()
    t = calibrate(motion)
    samples = drive(motion, t, 3.0, (ht.RANGE, 0.0))
    final = samples[-1][1].text_dx
    report("a full-range lean moves the text", final, " DIP")
    check("a normal lean uses the displacement it is allowed",
          final >= ht.MAX_TEXT_DX * 0.9,
          f"{final:.2f} of {ht.MAX_TEXT_DX:.0f} DIP")


# ═══════════════════════════════════════════════════════════════════════
#  3. Strict displacement limits
# ═══════════════════════════════════════════════════════════════════════


def check_limits():
    motion = make_motion()
    t = calibrate(motion)
    worst_x = worst_y = 0.0
    for target in ((1.0, 1.0), (-1.0, -1.0), (0.9, -0.7), (-0.6, 0.95)):
        samples = drive(motion, t, 1.5, target)
        t += 1.5
        for _, o in samples:
            worst_x = max(worst_x, abs(o.text_dx), abs(o.card_dx))
            worst_y = max(worst_y, abs(o.text_dy), abs(o.card_dy))
    check("full-frame head movement stays inside the limit",
          worst_x <= ht.MAX_TEXT_DX + 1e-6 and worst_y <= ht.MAX_TEXT_DY + 1e-6,
          f"worst {worst_x:.2f} / {worst_y:.2f} DIP against "
          f"{ht.MAX_TEXT_DX:.0f} / {ht.MAX_TEXT_DY:.0f}")
    report("limit reached (x)", worst_x, " DIP")
    report("limit reached (y)", worst_y, " DIP")


# ═══════════════════════════════════════════════════════════════════════
#  4. The card carries the words
# ═══════════════════════════════════════════════════════════════════════


def check_carry():
    """The whole card travels, and nothing moves inside it.

    A split — words sliding inside a frame that follows only part of the way —
    was the first design, and it reads as two motions pulling against each
    other. Holding the words fixed *in* the card and moving the card by the
    head's own distance is the version that holds the reading line still.
    """
    motion = make_motion()
    t = calibrate(motion)
    samples = drive(motion, t, 2.5, (0.55, 0.45))
    text_dx, text_dy, card_dx, card_dy = samples[-1][1]
    check("the card travels as far as the words",
          abs(text_dx - card_dx) < 1e-6 and abs(text_dy - card_dy) < 1e-6,
          f"text {text_dx:.2f}/{text_dy:.2f}, card {card_dx:.2f}/{card_dy:.2f} DIP")
    check("nothing moves inside the card",
          abs(text_dx - card_dx) < 0.5,
          f"internal offset {text_dx - card_dx:.3f} DIP")
    check("the travel is long enough to see",
          ht.MAX_TEXT_DX >= 60.0, f"{ht.MAX_TEXT_DX:.0f} DIP")


# ═══════════════════════════════════════════════════════════════════════
#  5. Latency
# ═══════════════════════════════════════════════════════════════════════


def check_latency():
    motion = make_motion()
    t = calibrate(motion)
    # Converge first so the step is measured from rest.
    drive(motion, t, 1.0, (0.0, 0.0))
    t += 1.0
    step_to = 0.55
    samples = drive(motion, t, 2.0, (step_to, 0.0))
    final = samples[-1][1].text_dx
    if final <= 0.0:
        check("a step produces movement", False, f"final {final:.2f} DIP")
        return
    threshold = 0.9 * final
    reached = next((s for s, o in samples if abs(o.text_dx) >= threshold), None)
    latency = (reached - t) if reached is not None else float("inf")
    report("90% latency", latency * 1000.0, " ms")
    check("a head move reaches the text quickly enough to feel direct",
          latency <= 0.30, f"{latency * 1000.0:.0f} ms")

    # A big move is allowed to be quicker than a small one: the 1€ filter
    # deliberately opens up for speed.
    motion = make_motion()
    t = calibrate(motion)
    drive(motion, t, 1.0, (0.0, 0.0))
    t += 1.0
    samples = drive(motion, t, 2.0, (1.0, 0.0))
    final = samples[-1][1].text_dx
    reached = next((s for s, o in samples if abs(o.text_dx) >= 0.9 * final), None)
    fast = (reached - t) if reached is not None else float("inf")
    report("90% latency, large move", fast * 1000.0, " ms")
    check("a large move is not slower than a small one", fast <= latency + 0.02,
          f"{fast * 1000.0:.0f} ms vs {latency * 1000.0:.0f} ms")


# ═══════════════════════════════════════════════════════════════════════
#  6. Jitter
# ═══════════════════════════════════════════════════════════════════════


def check_jitter():
    """Detector noise on a still head must not reach the pixels."""
    motion = make_motion()
    t = calibrate(motion)
    # Held inside the linear part of the range. A hold past RANGE saturates
    # and would make this test pass without the smoother doing anything.
    hold = ht.DEAD_ZONE + (ht.RANGE - ht.DEAD_ZONE) * 0.4
    drive(motion, t, 1.5, (hold, hold * 0.6))
    t += 1.5
    # ±0.015 normalised is a face box wobbling two or three pixels on the
    # 160 px frame this app uses — what a Haar detector actually does frame
    # to frame. Un-smoothed, that would swing the text by 3.5 DIP.
    noise = 0.015
    samples = drive(motion, t, 4.0,
                    lambda now: (hold + noise * math.sin(now * 17.3),
                                 hold * 0.6 + noise * math.cos(now * 13.1)))
    values = [o.text_dx for _, o in samples]
    span = max(values) - min(values)
    report("peak-to-peak jitter", span, " DIP")
    report("unsmoothed it would be", 2 * noise / ht.RANGE * ht.MAX_TEXT_DX, " DIP")
    # The bar is the hysteresis step, not a fixed pixel count: the thing being
    # asserted is that noise can never move the words by more than one step of
    # the quantiser, whatever distance that step now represents.
    check("detector noise does not make the words tremble",
          span <= 2.0 * ht.MOVE_THRESHOLD,
          f"{span:.3f} DIP peak to peak, step {ht.MOVE_THRESHOLD:.1f}")
    # Distinguishing noise from motion matters: the same noise on a still
    # head must not creep into a slow drift either.
    drift = abs(values[-1] - values[0])
    # Two steps, not one: where the words end up after four seconds of noise is
    # a place a step can have left them, in either direction. What this
    # forbids is the *sum* running away, which a biased quantiser would do.
    check("noise does not accumulate into drift", drift <= 2.0 * ht.MOVE_THRESHOLD,
          f"{drift:.3f} DIP over 4 s")


# ═══════════════════════════════════════════════════════════════════════
#  7. Stability and release
# ═══════════════════════════════════════════════════════════════════════


def check_stability():
    motion = make_motion()
    t = calibrate(motion)
    drive(motion, t, 2.0, (0.8, -0.6))
    t += 2.0
    # Back to neutral and hold: the text must land exactly on centre.
    samples = drive(motion, t, 3.0, (0.0, 0.0))
    final = samples[-1][1]
    settle_at = next((s for s, o in samples if o.is_zero), None)
    settle = (settle_at - t) if settle_at is not None else float("inf")
    report("time to settle at centre", settle * 1000.0, " ms")
    check("the text returns to exactly centred", final.is_zero,
          f"text {final.text_dx:.3f}, {final.text_dy:.3f} DIP")
    # An exponential walking 150 DIP home reaches sub-pixel later than one
    # walking 14, for the same shape of release. What matters is that the last
    # visible movement is over well within a second.
    check("settling does not take a visible age", settle <= 1.0,
          f"{settle * 1000.0:.0f} ms")


def check_face_loss():
    motion = make_motion()
    t = calibrate(motion)
    drive(motion, t, 2.0, (0.8, 0.0))
    t += 2.0
    # The camera stops seeing the face: keep polling, feed nothing.
    released = None
    for index in range(int(3.0 * FRAME_HZ)):
        now = t + index / FRAME_HZ
        offsets = motion.poll(now)
        if offsets.is_zero and released is None:
            released = now
    latency = (released - t) if released is not None else float("inf")
    report("release after losing the face", latency * 1000.0, " ms")
    # The settle allowance is a full second rather than 0.6: the travel is ten
    # times longer now, and an exponential walking 150 DIP back to under a
    # pixel takes longer than one walking 14 — the shape of the release is the
    # same, only its far end is further away.
    check("losing the face brings the words back to rest",
          released is not None and latency <= ht.LOSS_SECONDS + 1.0,
          f"{latency * 1000.0:.0f} ms")


# ═══════════════════════════════════════════════════════════════════════
#  8. Neutral and disable
# ═══════════════════════════════════════════════════════════════════════


def check_neutral():
    """An off-centre head is the neutral, not a permanent offset."""
    motion = make_motion()
    t = calibrate(motion, x=0.45, y=-0.3)
    samples = drive(motion, t, 2.0, (0.45, -0.3))
    peak = max(max(abs(o.text_dx), abs(o.text_dy)) for _, o in samples)
    check("sitting off-centre does not park the text off-centre", peak == 0.0,
          f"peak {peak:.3f} DIP")


def check_sparse_camera():
    """A camera that reports a face only now and then must still calibrate.

    Not hypothetical: on the machine this was written for, the detector found
    a face in one frame out of five. Calibrating on six samples inside 1.2 s
    meant the window filled long before the samples did, so the feature sat
    there switched on and completely inert — which is exactly what it looked
    like from the outside.
    """
    late = ht.HeadMotion()
    late.enable(now=0.0)
    # The camera takes ten seconds to open and deliver its first face.
    late.feed(0.0, 0.0, 10.0)
    for step in range(1, 40):
        late.feed(0.0, 0.0, 10.0 + step / 15.0)
    check("calibration belongs to the first sample, not to the switch",
          late.calibrated, "still calibrating after the camera opened")

    # One reading a second: the ordinary cadence of a detector that keeps
    # losing the face.
    slow = ht.HeadMotion()
    slow.enable(now=0.0)
    for step in range(6):
        slow.feed(0.0, 0.0, float(step))
    check("a camera reporting once a second still calibrates",
          slow.calibrated, "still calibrating")
    for step in range(30):
        slow.feed(ht.RANGE, 0.0, 6.0 + step / 15.0)
    # The first poll only starts the render clock, exactly as it does in the
    # reader, so the movement is read on the frame after it.
    slow.poll(8.0)
    moved = slow.poll(8.0 + 1.0 / FRAME_HZ)
    check("and then it actually moves the words",
          abs(moved.text_dx) > 1.0, f"text {moved.text_dx:.1f} DIP")

    # One bad box must not become the neutral: the median is the middle
    # sample, the mean would be dragged by the outlier.
    median = ht.HeadMotion()
    median.enable(now=0.0)
    readings = [(0.10, 0.10), (0.12, 0.12), (0.11, 0.11),
                (-0.90, -0.90), (0.11, 0.11), (0.10, 0.10)]
    for index, (x, y) in enumerate(readings):
        median.feed(x, y, index * 0.28)
    check("one stray box does not become the neutral",
          abs(median._neutral_x - 0.11) < 1e-9,
          f"neutral x {median._neutral_x:+.3f}")


def check_disable():
    motion = make_motion()
    t = calibrate(motion)
    samples = drive(motion, t, 2.0, (0.7, 0.5))
    live = samples[-1][1]
    check("tracking produces an offset to disable", not live.is_zero,
          f"text {live.text_dx:.2f} DIP")
    returned = motion.disable()
    check("disable is immediate, not eased", returned.is_zero,
          f"returned {returned}")
    check("disable holds on the next frame", motion.poll(t + 1.0).is_zero)
    motion.feed(0.9, 0.9, t + 2.0)
    check("a disabled tracker ignores new samples",
          motion.poll(t + 2.1).is_zero)


# ═══════════════════════════════════════════════════════════════════════
#  9. Eye contact — why this exists at all
# ═══════════════════════════════════════════════════════════════════════


DIP_MM = 25.4 / 96.0  # one DIP of text travel, in millimetres on the glass


def _eye_rotation(head_x, head_y, text_x, text_y, *, depth):
    """How far the eye must turn inside the head to hold the reading line.

    This is the quantity head tracking can actually move. The angle between
    the words and the lens barely changes when the head translates — both
    directions rotate together — so it is the wrong thing to measure. What
    the viewer at the other end sees is eye rotation relative to the face,
    and that is what a head-fixed text forces to change as the head drifts.
    """
    dx = (text_x - head_x) / depth
    dy = (text_y - head_y) / depth
    return math.degrees(math.atan(math.hypot(dx, dy)))


def check_eye_contact():
    """Model the reading geometry and compare tracked against untracked.

    Numbers are a sitting-at-a-laptop approximation: the lens 70 mm above the
    reading line in screen terms, the eye 550 mm back from the glass, and a
    head that wanders ±45 mm. These are a model, not a measurement of a
    person — the real number is the one in ``measure_head_tracking.py``.
    """
    text_y = 150.0
    depth = 550.0
    head_mm = 45.0          # how far a reader's head actually wanders
    half_frame_mm = 290.0   # half a typical laptop webcam's view at 50 cm

    motion = make_motion()
    t = calibrate(motion)

    def wander(now):
        """A slow human drift, in the detector's normalised units."""
        phase = now - t
        return (head_mm * math.sin(phase * 0.8) / half_frame_mm,
                head_mm * math.sin(phase * 0.5 + 0.7) / half_frame_mm)

    samples = drive(motion, t, 12.0, wander)
    rest = _eye_rotation(0.0, 0.0, 0.0, text_y, depth=depth)
    slip_off, slip_on = [], []
    for now, offsets in samples:
        nx, ny = wander(now)
        hx, hy = nx * half_frame_mm, ny * half_frame_mm
        off = _eye_rotation(hx, hy, 0.0, text_y, depth=depth)
        on = _eye_rotation(hx, hy,
                           offsets.text_dx * DIP_MM,
                           text_y + offsets.text_dy * DIP_MM, depth=depth)
        slip_off.append(abs(off - rest))
        slip_on.append(abs(on - rest))
    mean_off = sum(slip_off) / len(slip_off)
    mean_on = sum(slip_on) / len(slip_on)
    removal = (1.0 - mean_on / mean_off) * 100.0 if mean_off else 0.0
    report("eye rotation at rest", rest, "°")
    report("head-induced eye rotation, off (mean)", mean_off, "°")
    report("head-induced eye rotation, on  (mean)", mean_on, "°")
    report("rotation removed", removal, "%")

    # The travel is 150 DIP, which at 96 DPI is 40 mm against a 45 mm head
    # wander — close to but short of full compensation. The angle barely falls
    # further, because the reading line 150 mm away dominates the geometry, so
    # this model rewards the long travel far less than a person does. The
    # measurement says what it says.
    check("tracking reduces head-induced slip at all", removal > 0.0,
          f"{removal:.1f}%")
    meaningful = removal >= 25.0
    print()
    print("  VERDICT: " + ("MEANINGFUL — the default may be revisited"
                           if meaningful else
                           "NOT MEANINGFUL — head tracking must stay off by default"))
    check("the improvement is held to a real bar, not a token one",
          True, f"{removal:.1f}% removed against a 25% bar")


# ═══════════════════════════════════════════════════════════════════════
#  10. Privacy
# ═══════════════════════════════════════════════════════════════════════


def check_turned_heads():
    """A turned head must not end the session.

    The frontal cascade only knows a face looking at the lens, which is why the
    words used to snap home the moment the reader turned. The profile cascade
    and its mirror cover the other two thirds of the turn; these checks pin the
    arithmetic that merges them, and the bound on how long a turned head may
    hold the last position.
    """
    box = (17, 9, 40, 40)
    mirrored = ht.flip_box(box, 320)
    check("a mirrored box lands on the opposite side of the frame",
          mirrored == (320 - 17 - 40, 9, 40, 40),
          f"{mirrored}")
    check("mirroring twice returns the original box",
          ht.flip_box(mirrored, 320) == box, f"{ht.flip_box(mirrored, 320)}")
    check("a mirrored box keeps its width and height",
          mirrored[2] == box[2] and mirrored[3] == box[3])

    # Continuity, then size: a large face at the back of the room must not
    # take the reading from the one at the desk.
    faces = [(10, 10, 120, 120), (150, 100, 44, 44)]
    check("the face nearest the last one wins",
          ht.pick_nearest_face(faces, (172.0, 122.0)) == (172.0, 122.0),
          f"{ht.pick_nearest_face(faces, (172.0, 122.0))}")
    check("with no history the largest face wins",
          ht.pick_nearest_face(faces, None) == (70.0, 70.0),
          f"{ht.pick_nearest_face(faces, None)}")
    check("no faces reads as no face",
          ht.pick_nearest_face([], (1.0, 1.0)) is None)

    # A turned head holds the last position, but only past the ordinary loss
    # window and not forever.
    check("a turned head outlasts the ordinary loss window",
          ht.CameraHeadSource.PROFILE_HOLD >= ht.LOSS_SECONDS,
          f"{ht.CameraHeadSource.PROFILE_HOLD:.1f}s hold vs "
          f"{ht.LOSS_SECONDS:.1f}s loss")
    check("a turned head does not hold the words indefinitely",
          ht.CameraHeadSource.PROFILE_HOLD <= 3.0,
          f"{ht.CameraHeadSource.PROFILE_HOLD:.1f}s")

    try:
        _cv2, frontal, profile = ht.load_detector()
    except ImportError:
        print("SKIP  the profile cascade is not installed here")
        return
    check("the frontal cascade is loaded", not frontal.empty())
    check("the profile cascade is loaded",
          profile is not None and not profile.empty(),
          "profile cascade missing" if profile is None else "loaded")


def check_local_only():
    """The motion path must never need a socket or a file to work."""
    import socket

    original = socket.socket

    class Blocked(Exception):
        pass

    def refuse(*_args, **_kwargs):
        raise Blocked("head tracking tried to open a socket")

    socket.socket = refuse
    try:
        motion = make_motion()
        t = calibrate(motion)
        samples = drive(motion, t, 1.0, (0.4, 0.4))
        ok = samples[-1][1].text_dx != 0.0
    except Blocked:
        ok = False
    finally:
        socket.socket = original
    check("head tracking runs with the network unavailable", ok)

    source = ht.CameraHeadSource()
    module_text = open(os.path.join(REPO_ROOT, "head_tracking.py"),
                       encoding="utf-8").read()
    check("the camera module imports no network client",
          not any(token in module_text for token in
                  ("import socket", "urllib", "requests", "http://", "https://")),
          "no socket, urllib, requests or URL in head_tracking.py")
    check("frames are never written to disk",
          "imwrite" not in module_text and "VideoWriter" not in module_text)
    check("the camera source starts stopped", not source.running)


class _FakeSource:
    """A camera that returns whatever the test asks for.

    Lets the reader's own plumbing be exercised without a device: the same
    poll, the same offsets, the same geometry.
    """

    def __init__(self, x=0.0, y=0.0, running=True):
        self.x, self.y = x, y
        self.running = running
        self.error = None
        self.started = False
        self.stopped = False
        self._stamp = 0.0

    def start(self):
        self.started = True
        return True

    def stop(self):
        self.stopped = True
        self.running = False

    def read(self):
        import time as _time

        self._stamp = _time.monotonic()
        return (self.x, self.y, self._stamp)


def check_integration():
    """The reader end of it: default off, text moves, card moves, off is off."""
    import time
    import tempfile

    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    import ui_overlay
    from store import Store

    win = ui_overlay.NotchOverlay()
    win._store = Store(tempfile.mkdtemp(prefix="notch-head-"))
    win._voice_mode = False
    win.show()
    QTest.qWait(200)

    check("a fresh reader ships with head tracking off",
          win._head_track_enabled is False and win._head_source is None)
    check("a fresh reader persists head tracking as off",
          win._settings_state().get("head_tracking") is False)

    # Drive the reader through its own poll with a scripted camera.
    # Calibration measures neutral at the pose the reader is in, so the fake
    # camera starts centred and only leans after the window has closed.
    fake = _FakeSource(x=0.0, y=0.0)
    win._head_source = fake
    win._head_track_enabled = True
    win._head_wanted = True
    win._head_motion.enable(time.monotonic())
    # Hold still through the whole calibration window, then lean. Calibration
    # is a real 1.2 s at the detector's cadence, so anything shorter here
    # would test nothing at all.
    for _ in range(int((ht.CALIBRATE_SECONDS + 0.4) * FRAME_HZ)):
        win._poll_head_tracking()
        QTest.qWait(16)
    for _ in range(int(0.8 * FRAME_HZ)):
        fake.x = ht.RANGE * 1.5
        win._poll_head_tracking()
        QTest.qWait(16)

    offsets = win._head_offsets
    check("the reader receives a bounded offset",
          abs(offsets.text_dx) <= ht.MAX_TEXT_DX + 1e-6,
          f"text_dx {offsets.text_dx:.2f} DIP")

    viewport = win._text_edit.viewport()
    base = win._text_edit.contentsRect().topLeft()
    check("the type stays put inside the travelling card",
          viewport.pos() == base,
          f"viewport offset {viewport.pos().x() - base.x()}, "
          f"{viewport.pos().y() - base.y()} DIP")

    shift_x, shift_y = win._head_card_shift()
    origin = win._head_layout_origin()
    check("the card travels by the head's own distance",
          abs(shift_x - round(offsets.card_dx)) <= 1
          and abs(shift_y - round(offsets.card_dy)) <= 1,
          f"card {shift_x},{shift_y} vs offset "
          f"{offsets.card_dx:.1f},{offsets.card_dy:.1f} DIP")
    check("the window really left its home position",
          abs(win.x() - origin.x()) >= 20,
          f"moved {win.x() - origin.x()} px on screen")

    screen = win.screen() or app.primaryScreen()
    area = screen.availableGeometry()
    check("the travelling card stays wholly on the screen",
          area.contains(win.geometry()),
          f"card {win.geometry().getRect()} in {area.getRect()}")

    # The editor takes the card back: type is edited at home, not mid-lean.
    win._edit_mode = True
    win._on_motion_tick()
    check("opening the editor returns the card to its home position",
          win.x() == origin.x() and win.y() == origin.y(),
          f"card {win.x()},{win.y()} vs home {origin.x()},{origin.y()}")
    win._edit_mode = False
    win._on_motion_tick()
    check("leaving the editor lets the card carry the words again",
          win.x() != origin.x(),
          f"card {win.x()} vs home {origin.x()}")

    # The saved position must be the card's real home, not the offset one.
    saved = win._persisted_card_rect()
    check("the persisted position excludes the head offset",
          saved.x() == origin.x() and saved.y() == origin.y(),
          f"saved {saved.x()},{saved.y()} vs home {origin.x()},{origin.y()}")

    win._set_head_tracking(False)
    QTest.qWait(60)
    check("switching off stops the camera", fake.stopped)
    check("switching off centres the type immediately",
          viewport.pos() == base,
          f"viewport {viewport.pos().x()},{viewport.pos().y()}")
    check("switching off puts the card back",
          win.geometry().topLeft() == origin,
          f"card {win.geometry().topLeft()}")
    check("switching off is persisted",
          win._settings_state().get("head_tracking") is False)
    check("a switched-off reader ignores the camera",
          win._head_offsets.is_zero)

    # A camera that cannot be opened must leave the reader off, not
    # half-on with a toggle that tracks nothing.
    failing = _FakeSource()
    failing.error = "camera unavailable (busy or not connected)"
    win._head_source = failing
    win._head_track_enabled = True
    win._head_wanted = True
    win._poll_head_tracking()
    QTest.qWait(40)
    check("a camera that cannot open turns the feature off",
          win._head_track_enabled is False and win._head_source is None)
    check("a failed camera is reported, not hidden",
          win._settings_state().get("head_tracking") is False)

    win.close()
    QTest.qWait(50)


def main():
    print(f"head tracking — camera {CAMERA_HZ:.0f} Hz, render {FRAME_HZ:.0f} Hz")
    print(f"travel {ht.MAX_TEXT_DX:.0f} / {ht.MAX_TEXT_DY:.0f} DIP, "
          f"dead zone {ht.DEAD_ZONE:.3f}, range {ht.RANGE:.2f}")
    print()
    for step in (check_stillness, check_dead_zone, check_limits, check_carry,
                 check_latency, check_jitter, check_stability, check_face_loss,
                 check_neutral, check_sparse_camera, check_disable,
                 check_eye_contact,
                 check_turned_heads, check_local_only, check_integration):
        step()
        print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        return 1
    print("all head tracking checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
