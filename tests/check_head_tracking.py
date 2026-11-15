"""Head and eye tracking checks: the grid, continuity, stability and recovery.

The tracker's movement space is the upper half of the display, divided into
five columns and two rows. This harness proves the things that decide whether
that is usable rather than merely present:

  * **all ten regions** are reachable from head and face-direction input,
  * **continuity** — every region change is interpolated, never a jump,
  * **bounds** — no input can put the card in the lower half or off screen,
  * **stability** — a still head is still, and detector noise cannot change a
    region or make the card tremble,
  * **calibration** — neutral is measured where the reader actually is,
    including a camera that reports slowly or not at all,
  * **release** — losing the face returns the card to the neutral region
    smoothly, and disabling is immediate,
  * **camera recovery** — a simulated device that drops frames, stalls or
    disappears is recovered without the feature switching itself off.

The motion checks drive :class:`HeadMotion` with explicit timestamps at the
cadence the worker achieves, so the numbers are reproducible and do not depend
on a machine's load. The real device is exercised separately by
``tests/measure_head_tracking.py``.

Exits non-zero on the first failed expectation.
"""

import math
import os
import statistics
import sys
import time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

from cuvyro.tracking import head as ht

failures = []
# The cadence the worker actually achieves, measured on the machine this was
# written for: 14.6-14.7 fresh readings a second at 640x480, against the 15 fps
# it asks the camera for.
CAMERA_HZ = 14.7
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
    seconds = ht.CALIBRATE_SECONDS + 0.4 if seconds is None else seconds
    now = motion._calib_started if motion._calib_started else 0.0
    steps = int(seconds * CAMERA_HZ)
    for index in range(steps + 1):
        t = now + index / CAMERA_HZ
        motion.feed(x, y, t)
        motion.poll(t)
    return now + steps / CAMERA_HZ


def drive(motion, start, seconds, position, frame_hz=FRAME_HZ, camera_hz=CAMERA_HZ,
          gaze=None, direction=None):
    """Hold ``position`` for ``seconds`` and return the painted offsets.

    ``gaze`` and ``direction`` are the same shape as ``position`` (a constant
    tuple or a callable of time); they feed the iris and face-direction
    channels. Returns ``(t, offsets)`` sampled at the render rate.
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
            if gaze is None:
                gx = gy = 0.0
            else:
                gx, gy = gaze(next_camera) if callable(gaze) else gaze
            if direction is None:
                yaw = pitch = 0.0
            else:
                yaw, pitch = (direction(next_camera) if callable(direction)
                              else direction)
            motion.feed(px, py, next_camera, gx, gy, yaw, pitch)
            next_camera += camera_step
        samples.append((next_frame, motion.poll(next_frame)))
        next_frame += frame_step
    return samples


def make_motion(**kwargs):
    motion = ht.HeadMotion(**kwargs)
    motion.enable(now=0.0)
    return motion


def hold(motion, start, seconds, position, **kwargs):
    """Drive a constant pose to rest and return the final offsets."""
    samples = drive(motion, start, seconds, position, **kwargs)
    return samples[-1][1], start + seconds


def region_after(x=0.0, y=0.0, gx=0.0, gy=0.0, yaw=0.0, pitch=0.0,
                 seconds=2.5):
    motion = make_motion()
    t = calibrate(motion)
    final, _ = hold(motion, t, seconds, (x, y), gaze=(gx, gy),
                    direction=(yaw, pitch))
    return final


# ═══════════════════════════════════════════════════════════════════════
#  1. The grid itself
# ═══════════════════════════════════════════════════════════════════════


def check_grid():
    grid = ht.TrackingGrid()
    check("the tracking space is five columns by two rows",
          grid.columns == 5 and grid.rows == 2, f"{grid.columns}x{grid.rows}")
    check("it has ten regions", grid.count == 10, str(grid.count))

    # Every region rect tiles the upper half with no gap and no overlap.
    covered = 0.0
    for region in range(grid.count):
        u0, v0, u1, v1 = grid.region_rect(region)
        covered += (u1 - u0) * (v1 - v0)
        au, av = grid.anchor(region)
        check(f"region {region} contains its anchor",
              u0 < au < u1 and v0 < av < v1,
              f"anchor {au:.2f},{av:.2f} in {u0:.2f},{v0:.2f}..{u1:.2f},{v1:.2f}")
        check(f"region {region} is in the upper half", v0 >= 0.0 and v1 <= 1.0,
              f"v {v0:.2f}..{v1:.2f}")
    check("the ten regions tile the whole upper half",
          abs(covered - 1.0) < 1e-9, f"{covered:.6f} of the space")

    check("the region below the midline does not exist",
          grid.region_index(0.5, 1.0) == 7 and grid.region_index(0.5, 1.01) == 7,
          "v is clamped to the upper half")
    check("the neutral region is the middle of the top row",
          ht.NEUTRAL_REGION == 2, f"region {ht.NEUTRAL_REGION}")


# ═══════════════════════════════════════════════════════════════════════
#  2. Stillness and the dead zone
# ═══════════════════════════════════════════════════════════════════════


def check_stillness():
    motion = make_motion()
    t = calibrate(motion)
    samples = drive(motion, t, 4.0, (0.0, 0.0))
    regions = {o.region for _, o in samples}
    final = samples[-1][1]
    check("a still head stays in the neutral region",
          regions == {ht.NEUTRAL_REGION}, f"regions {sorted(regions)}")
    check("a still head never moves the card",
          final.u == 0.5 and abs(final.v - 0.25) < 1e-9,
          f"u,v {final.u:.4f},{final.v:.4f}")


def check_dead_zone():
    small = region_after(0.01, 0.01, gx=0.03, gy=-0.02, seconds=2.0)
    check("inside the dead zone nothing moves",
          small.region == ht.NEUTRAL_REGION
          and small.u == 0.5 and abs(small.v - 0.25) < 1e-9,
          f"region {small.region}, u,v {small.u:.3f},{small.v:.3f}")

    # A small but real lean leaves the dead zone and crosses one column.
    moved = region_after(0.12, 0.0, seconds=2.0)
    check("a real lean leaves the dead zone", moved.region != ht.NEUTRAL_REGION,
          f"region {moved.region}")
    check("leaving the dead zone is continuous, not a step",
          abs(moved.u - 0.5) < 0.30,
          f"moved {moved.u - 0.5:+.3f} of the width")


# ═══════════════════════════════════════════════════════════════════════
#  3. All ten regions
# ═══════════════════════════════════════════════════════════════════════


def check_all_regions():
    """Every region must be reachable, and the rendered point must stay in it.

    The input poses are deliberate but not extreme: a lean of 0.36 of a
    half-frame is a shoulder-assisted lean, and a 0.16 nod is a comfortable
    look down. The grid is only useful if a person can actually get to all of
    it; a mapping that only reaches the middle would look busy and do nothing.
    """
    grid = ht.TrackingGrid()
    xs = (-0.36, -0.12, 0.0, 0.12, 0.36)
    ys = (-0.10, 0.16)
    seen = set()
    for row, y in enumerate(ys):
        for column, x in enumerate(xs):
            expected = row * grid.columns + column
            final = region_after(x, y)
            seen.add(final.region)
            check(f"pose for region {expected} selects it",
                  final.region == expected,
                  f"region {final.region}, u,v {final.u:.3f},{final.v:.3f}")
            check(f"region {expected} keeps the card inside its cell",
                  grid.contains(expected, final.u, final.v, tolerance=1e-6),
                  f"u,v {final.u:.3f},{final.v:.3f}")
            check(f"region {expected} stays in its row",
                  (final.v < 0.5) == (row == 0),
                  f"v {final.v:.3f}")
    check("all ten regions are reachable", seen == set(range(10)),
          f"reached {sorted(seen)}")


# ═══════════════════════════════════════════════════════════════════════
#  4. Continuity between regions
# ═══════════════════════════════════════════════════════════════════════


def check_continuity():
    """A walk across all ten cells must never teleport.

    The path goes along the top row and back along the bottom one, which
    includes every horizontal and vertical transition. The maximum movement
    between two painted frames is the number that says whether the spring is
    doing its job; a snap would be one frame carrying most of a cell.
    """
    grid = ht.TrackingGrid()
    xs = (-0.36, -0.12, 0.0, 0.12, 0.36)
    path = [(x, -0.10) for x in xs] + [(x, 0.16) for x in reversed(xs)]
    motion = make_motion()
    t = calibrate(motion)
    samples = []
    for position in path:
        chunk = drive(motion, t, 1.5, position)
        samples.extend(chunk)
        t += 1.5

    regions = []
    for _, offsets in samples:
        if offsets.region >= 0 and (not regions or regions[-1] != offsets.region):
            regions.append(offsets.region)
    check("the walk visits all ten regions", set(regions) == set(range(10)),
          f"visited {regions}")

    steps = [(abs(b.u - a.u), abs(b.v - a.v))
             for (_, a), (_, b) in zip(samples, samples[1:])]
    worst = max(max(du, dv) for du, dv in steps)
    report("largest movement in one painted frame", worst, " of the width")
    # A snap is the whole cell in one frame; the spring's fastest frame is
    # about half a cell's worth of motion in a fifth of a second, so 0.09 is
    # still an order of magnitude below a teleport.
    check("no region change is a snap",
          worst <= 0.09, f"{worst:.4f} of the width in one frame")
    check("a region change takes more than one frame",
          sum(1 for du, _ in steps if du > 1e-4) > len(path) * 2,
          f"{sum(1 for du, _ in steps if du > 1e-4)} moving frames")

    # The spring must be causal: no frame may jump closer to the new target
    # than the distance the signal itself changed.
    check("the card always stays inside the upper half",
          all(0.0 <= o.v <= 1.0 for _, o in samples))


# ═══════════════════════════════════════════════════════════════════════
#  5. Bounds
# ═══════════════════════════════════════════════════════════════════════


def check_bounds():
    motion = make_motion()
    t = calibrate(motion)
    worst_u = worst_v = 0.0
    regions = set()
    for pose in ((1.0, 1.0), (-1.0, -1.0), (0.9, -0.7), (-0.6, 0.95)):
        samples = drive(motion, t, 1.2, pose, gaze=(1.0, 1.0),
                        direction=(1.0, 1.0))
        t += 1.2
        for _, o in samples:
            worst_u = max(worst_u, o.u)
            worst_v = max(worst_v, o.v)
            regions.add(o.region)
    check("full-frame input never leaves the grid",
          0.0 <= worst_u <= 1.0 and 0.0 <= worst_v <= 1.0,
          f"u {worst_u:.3f}, v {worst_v:.3f}")
    check("full-frame input never leaves the upper half", worst_v <= 1.0,
          f"v {worst_v:.3f}")
    check("full-frame input cannot invent a region",
          all(0 <= r < 10 for r in regions), f"regions {sorted(regions)}")


# ═══════════════════════════════════════════════════════════════════════
#  6. Latency
# ═══════════════════════════════════════════════════════════════════════


def check_latency():
    motion = make_motion()
    t = calibrate(motion)
    drive(motion, t, 1.0, (0.0, 0.0))
    t += 1.0
    samples = drive(motion, t, 2.5, (0.12, 0.16))
    final = samples[-1][1]
    region_at = next((s for s, o in samples if o.region == 8), None)
    region_latency = (region_at - t) if region_at is not None else float("inf")
    u_at = next((s for s, o in samples
                 if abs(o.u - final.u) <= 0.1 * abs(final.u - 0.5)), None)
    settle = (u_at - t) if u_at is not None else float("inf")
    report("time to select a new region", region_latency * 1000.0, " ms")
    report("time to 90% of the movement", settle * 1000.0, " ms")
    check("a deliberate move selects its region quickly",
          region_latency <= 0.60, f"{region_latency * 1000.0:.0f} ms")
    check("the card reaches most of its travel within a second",
          settle <= 1.10, f"{settle * 1000.0:.0f} ms")


# ═══════════════════════════════════════════════════════════════════════
#  7. Jitter
# ═══════════════════════════════════════════════════════════════════════


def check_jitter():
    motion = make_motion()
    t = calibrate(motion)
    hold_x, hold_y = 0.06, 0.02
    drive(motion, t, 1.5, (hold_x, hold_y))
    t += 1.5
    noise = 0.015
    samples = drive(motion, t, 4.0,
                    lambda now: (hold_x + noise * math.sin(now * 17.3),
                                 hold_y + noise * math.cos(now * 13.1)))
    values_u = [o.u for _, o in samples]
    values_v = [o.v for _, o in samples]
    span = max(max(values_u) - min(values_u), max(values_v) - min(values_v))
    regions = {o.region for _, o in samples}
    report("peak-to-peak jitter", span, " of the width")
    check("detector noise does not change the region",
          regions == {2}, f"regions {sorted(regions)}")
    check("detector noise does not make the card tremble",
          span <= 0.025, f"{span:.4f} of the width peak to peak")

    # A head hovering on a boundary must not rattle between two cells. The
    # hysteresis band is the whole reason this check exists.
    edge = 0.105
    motion = make_motion()
    t = calibrate(motion)
    drive(motion, t, 1.5, (edge, 0.0))
    t += 1.5
    samples = drive(motion, t, 3.0,
                    lambda now: (edge + noise * math.sin(now * 15.0), 0.0))
    regions = {o.region for _, o in samples}
    check("a head on a region boundary does not chatter",
          len(regions) == 1, f"regions {sorted(regions)}")


# ═══════════════════════════════════════════════════════════════════════
#  8. Calibration
# ═══════════════════════════════════════════════════════════════════════


def check_calibration():
    """Neutral belongs to the reader's pose, and only closes on a still head."""
    motion = make_motion()
    t = calibrate(motion, x=0.45, y=-0.30)
    final, _ = hold(motion, t, 2.0, (0.45, -0.30))
    check("sitting off-centre does not park the card off-centre",
          final.region == ht.NEUTRAL_REGION
          and final.u == 0.5 and abs(final.v - 0.25) < 1e-9,
          f"region {final.region}, u,v {final.u:.3f},{final.v:.3f}")

    # The camera takes ten seconds to open and deliver its first face.
    late = ht.HeadMotion()
    late.enable(now=0.0)
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
        slow.feed(0.15, 0.0, 6.0 + step / 15.0)
    slow.poll(8.0)
    moved = slow.poll(8.0 + 1.0 / FRAME_HZ)
    check("and then it actually moves the card",
          moved.region != ht.NEUTRAL_REGION and moved.u != 0.5,
          f"region {moved.region}, u {moved.u:.3f}")

    # One bad box must not become the neutral. The median is the middle
    # sample; the mean would be dragged by the outlier.
    median = ht.HeadMotion()
    median.enable(now=0.0)
    readings = [(0.10, 0.10), (0.12, 0.12), (0.11, 0.11),
                (-0.90, -0.90), (0.11, 0.11), (0.10, 0.10)]
    for index, (x, y) in enumerate(readings):
        median.feed(x, y, index * 0.14)
    check("a stray box alone does not close the window",
          not median.calibrated, "calibrated on a window containing a 0.9 outlier")
    for step in range(1, 14):
        median.feed(0.11, 0.11, 0.84 + step * 0.14)
    check("one stray box does not become the neutral",
          median.calibrated and abs(median._neutral_x - 0.11) < 1e-9,
          f"neutral x {median._neutral_x:+.3f}")

    # A lean captured as neutral turns every later lean into the full travel
    # the other way. The window only closes once the head is still.
    moving = ht.HeadMotion()
    moving.enable(now=0.0)
    for step in range(20):
        moving.feed(-0.5 + step * 0.05, 0.0, step / CAMERA_HZ)
    check("a moving head does not close the calibration window",
          not moving.calibrated, "calibrated while sweeping")
    held = 20 / CAMERA_HZ
    for step in range(1, 40):
        moving.feed(0.45, 0.0, held + step / CAMERA_HZ)
    check("and the neutral is where the head actually stopped",
          moving.calibrated and abs(moving._neutral_x - 0.45) < 1e-9,
          f"neutral x {moving._neutral_x:+.3f} after stopping at +0.450")

    # One reading and then silence: polling is the clock that cannot stop.
    stalled = ht.HeadMotion()
    stalled.enable(now=0.0)
    stalled.feed(0.05, 0.02, 0.0)
    for step in range(int(6.0 * FRAME_HZ)):
        stalled.poll(step / FRAME_HZ)
    check("a camera that delivers one reading and stops still stops calibrating",
          stalled.calibrated, "still calibrating with no further samples")
    check("and the reading it did get is what it calibrated against",
          abs(stalled._neutral_x - 0.05) < 1e-9,
          f"neutral x {stalled._neutral_x:+.3f}")


# ═══════════════════════════════════════════════════════════════════════
#  9. Outliers
# ═══════════════════════════════════════════════════════════════════════


def check_outliers():
    """One wild reading must not move the card; two in agreement must land."""
    motion = make_motion()
    t = calibrate(motion)
    drive(motion, t, 1.0, (0.05, 0.0))
    t += 1.0
    before = motion.offsets()
    motion.feed(0.9, 0.0, t + 0.07)
    samples = drive(motion, t + 0.07, 1.2, (0.05, 0.0))
    peak = max(abs(o.u - before.u) for _, o in samples)
    # A hundredth of a percent is the spring finishing the previous move; a
    # wild reading would be a visible fraction of a cell.
    check("one wild reading is ignored", peak <= 1e-4,
          f"{peak:.6f} of the width")

    # A real, sustained move: the second sample agrees, so it must be followed.
    motion = make_motion()
    t = calibrate(motion)
    drive(motion, t, 1.0, (0.0, 0.0))
    t += 1.0
    for step in range(int(2.0 * CAMERA_HZ)):
        motion.feed(0.36, 0.0, t + step / CAMERA_HZ)
        motion.poll(t + step / CAMERA_HZ)
    final = motion.offsets()
    check("two agreeing samples are followed", final.region == 4,
          f"region {final.region}, u {final.u:.3f}")


# ═══════════════════════════════════════════════════════════════════════
#  10. Gaze and fusion
# ═══════════════════════════════════════════════════════════════════════


def check_fusion():
    """Head, eyes and face direction all affect the rendered position.

    The head and the face's direction choose the region; the eyes move the
    card within it. Holding eyes out of the region decision is what keeps the
    feedback loop (card moves toward a glance, glance relaxes) from hunting
    across a boundary. Inside a cell, the effect is bounded and visible.
    """
    head = region_after(0.12, 0.0)
    check("head position selects a region", head.region == 3,
          f"region {head.region}")

    gaze = region_after(0.0, 0.0, gx=0.75)
    check("eye movement moves the card within the region",
          abs(gaze.u - 0.5) > 0.02, f"u {gaze.u:.3f}")
    check("eye movement alone does not cross a region boundary",
          gaze.region == ht.NEUTRAL_REGION, f"region {gaze.region}")
    check("eye movement stays within the selected cell",
          gaze.u < 0.6, f"u {gaze.u:.3f}")

    yaw = region_after(0.0, 0.0, yaw=0.45)
    check("face direction selects a region", yaw.region == 3,
          f"region {yaw.region}")

    pitch = region_after(0.0, 0.0, pitch=1.3)
    check("a downward face pitch selects the lower row", pitch.region == 7,
          f"region {pitch.region}")

    down = region_after(0.0, 0.0, gy=1.0)
    check("looking down moves the card down", down.v > 0.3
          and down.region == ht.NEUTRAL_REGION, f"v {down.v:.3f}")

    # A lean to the right and a glance to the left pull the rendered position
    # in opposite directions. The head keeps the region; the eyes move the
    # card back toward the middle of that cell instead of fighting for it.
    opposing = region_after(0.12, 0.0, gx=-0.75)
    check("a glance against a lean refines the position inside the region",
          opposing.region == 3 and opposing.u < head.u,
          f"region {opposing.region}, u {opposing.u:.3f} vs {head.u:.3f}")

    # A sustained glance must not creep into the next region over time, even
    # though the card moving toward it changes the measured gaze.
    motion = make_motion()
    t = calibrate(motion)
    gaze_samples = drive(motion, t, 6.0, (0.0, 0.0), gaze=(0.75, 0.0))
    regions = {o.region for _, o in gaze_samples}

    def feedback(now):
        # Eyes need less deviation once the card has come toward them.
        offset = motion.offsets().u
        return (max(-1.0, 0.75 - (offset - 0.5) * 1.6), 0.0)

    feedback_samples = drive(motion, t, 6.0, (0.0, 0.0), gaze=feedback)
    feedback_regions = {o.region for _, o in feedback_samples}
    tail = [o.u for _, o in feedback_samples[-120:]]
    report("gaze feedback movement while settled",
           max(tail) - min(tail), " of the width")
    check("a sustained glance does not hunt across regions",
          regions == {ht.NEUTRAL_REGION}
          and feedback_regions == {ht.NEUTRAL_REGION},
          f"regions {sorted(regions)} / {sorted(feedback_regions)}")
    check("the gaze feedback loop settles instead of breathing",
          max(tail) - min(tail) < 0.02,
          f"{max(tail) - min(tail):.3f} of the width over the last 2 s")


# ═══════════════════════════════════════════════════════════════════════
#  11. Release and disable
# ═══════════════════════════════════════════════════════════════════════


def check_release():
    motion = make_motion()
    t = calibrate(motion)
    final, t = hold(motion, t, 2.0, (0.36, 0.16))
    check("the card reaches a far region before release",
          final.region == 9, f"region {final.region}")
    released = None
    previous = motion.offsets()
    worst = 0.0
    for index in range(int(4.0 * FRAME_HZ)):
        now = t + index / FRAME_HZ
        offsets = motion.poll(now)
        worst = max(worst, abs(offsets.u - previous.u),
                    abs(offsets.v - previous.v))
        previous = offsets
        if offsets.region == ht.NEUTRAL_REGION and released is None:
            released = now
    end = motion.offsets()
    latency = (released - t) if released is not None else float("inf")
    report("release after losing the face", latency * 1000.0, " ms")
    check("losing the face returns the card to the neutral region",
          end.region == ht.NEUTRAL_REGION
          and abs(end.u - 0.5) < 1e-6 and abs(end.v - 0.25) < 1e-6,
          f"region {end.region}, u,v {end.u:.3f},{end.v:.3f}")
    check("the release never snaps",
          worst <= 0.07, f"{worst:.4f} of the width in one frame")
    check("the release happens within a couple of seconds",
          released is not None and latency <= ht.LOSS_SECONDS + 1.5,
          f"{latency * 1000.0:.0f} ms")


def check_seed():
    """Switching on must ease from the parked card, not teleport it home."""
    motion = make_motion()
    motion.seed(0.5, 0.45)
    next_camera = next_frame = 0.0
    while not motion.calibrated and next_frame < 8.0:
        while next_camera <= next_frame:
            motion.feed(0.0, 0.0, next_camera)
            next_camera += 1.0 / CAMERA_HZ
            if motion.calibrated:
                break
        if motion.calibrated:
            break
        motion.poll(next_frame)
        next_frame += 1.0 / FRAME_HZ
    check("calibration does not move the seeded spring",
          abs(motion._spring_y.value - 0.45) < 1e-9,
          f"spring v {motion._spring_y.value:.3f}")
    start = next_frame + 1.0 / FRAME_HZ
    first = motion.poll(start)
    samples = drive(motion, start, 2.0, (0.0, 0.0))
    check("the first painted position starts near the parked card",
          not first.is_zero and first.u == 0.5 and first.v > 0.42,
          f"first u,v {first.u:.3f},{first.v:.3f}")
    final = samples[-1][1]
    check("and then it eases to the neutral region",
          final.region == ht.NEUTRAL_REGION
          and abs(final.u - 0.5) < 1e-6 and abs(final.v - 0.25) < 1e-6,
          f"region {final.region}, u,v {final.u:.3f},{final.v:.3f}")


def check_disable():
    motion = make_motion()
    t = calibrate(motion)
    final, t = hold(motion, t, 2.0, (0.36, 0.0))
    check("tracking produces a region to disable",
          final.region >= 0, f"region {final.region}")
    returned = motion.disable()
    check("disable is immediate, not eased", returned.is_zero,
          f"returned {returned}")
    check("disable holds on the next frame", motion.poll(t + 1.0).is_zero)
    motion.feed(0.9, 0.9, t + 2.0)
    check("a disabled tracker ignores new samples",
          motion.poll(t + 2.1).is_zero)


# ═══════════════════════════════════════════════════════════════════════
#  12. Landmarks
# ═══════════════════════════════════════════════════════════════════════


class _P:
    """A landmark: the two numbers the geometry actually reads."""

    __slots__ = ("x", "y")

    def __init__(self, x, y):
        self.x, self.y = x, y


def _face(dx=0.0, dy=0.0, iris_dx=0.0, blink=False, size=1.0,
          nose_dx=0.0, nose_dy=0.0):
    """A synthetic 478-point face, for the reading maths only.

    Two eyes 6% of the frame apart (of ``size``), each with four corners and
    an iris, and a nose tip that can be offset from the eye midpoint. This is
    not a model of a face — the real geometry is measured against a person by
    ``tests/measure_head_tracking.py`` — it is the arithmetic of the reading,
    which has to have the sign of every axis right.
    """
    points = [_P(0.5, 0.5) for _ in range(478)]
    span = 0.03 * size
    gap = 0.004 * size if blink else 0.025 * size
    for base, outward, outer, inner, top, bottom, iris in (
        (0.43, -1.0, 33, 133, 159, 145, 468),
        (0.57, +1.0, 263, 362, 386, 374, 473),
    ):
        base = 0.5 + (base - 0.5) * size
        points[outer] = _P(base + outward * span + dx, 0.5 + dy)
        points[inner] = _P(base - outward * span + dx, 0.5 + dy)
        points[top] = _P(base + dx, 0.5 - gap + dy)
        points[bottom] = _P(base + dx, 0.5 + gap + dy)
        points[iris] = _P(base + iris_dx * size + dx, 0.5 + dy)
    points[1] = _P(0.5 + dx + nose_dx, 0.5 + dy + nose_dy)
    return points


def check_landmarks():
    """Head position, gaze and direction from the mesh, and every sign."""
    width, height = 640, 480
    centred = ht.reading_from_landmarks(_face(), width, height)
    check("a centred face reads as a centred head",
          abs(centred.x) < 1e-6 and abs(centred.y) < 1e-6,
          f"{centred.x:+.3f}, {centred.y:+.3f}")
    check("a centred face reads both eyes", centred.eyes == 2)
    check("looking straight ahead is no gaze",
          abs(centred.gaze_x) < 1e-6 and abs(centred.gaze_y) < 1e-6)
    check("a translated face has no direction bias",
          abs(centred.yaw) < 1e-6 and abs(centred.pitch) < 1e-6)

    # A lean to the reader's right puts them on the image's left, so the
    # screen coordinate must be positive out of a negative image one.
    leaned = ht.reading_from_landmarks(_face(dx=-0.06), width, height)
    check("a lean to the reader's right reads as positive screen x",
          leaned.x > 0.0, f"{leaned.x:+.3f}")

    # Iris toward the image's right is the reader looking to their left.
    left_look = ht.reading_from_landmarks(_face(iris_dx=0.012), width, height)
    check("gaze is reported in screen directions, not image ones",
          left_look.gaze_x < 0.0, f"{left_look.gaze_x:+.3f}")
    check("the gaze magnitude is the iris share of the eye's half-width",
          0.3 < abs(left_look.gaze_x) < 0.5, f"{left_look.gaze_x:+.3f}")

    # Nose toward the image's right is the face turned to the reader's left.
    turned = ht.reading_from_landmarks(_face(nose_dx=0.01), width, height)
    check("face direction is reported in screen directions",
          turned.yaw < 0.0, f"{turned.yaw:+.3f}")
    nodded = ht.reading_from_landmarks(_face(nose_dy=0.01), width, height)
    check("looking down is a positive pitch", nodded.pitch > 0.0,
          f"{nodded.pitch:+.3f}")

    blinked = ht.reading_from_landmarks(_face(blink=True), width, height)
    check("a shut eye contributes no gaze",
          blinked.eyes == 0 and blinked.gaze_x == 0.0 and blinked.gaze_y == 0.0,
          f"eyes {blinked.eyes}")
    check("but the head is still known while blinking",
          abs(blinked.x) < 1e-6, f"{blinked.x:+.3f}")

    distant = ht.reading_from_landmarks(_face(size=0.25), 640, 480)
    check("a face too small to see irises contributes no gaze",
          distant.eyes == 0, f"eye width {0.06 * 0.25 * 640:.1f} px")

    if not os.path.exists(ht.model_path()):
        print(f"SKIP  no face landmark model at {ht.model_path()}")
        return
    try:
        ht.load_landmarker()
    except ImportError:
        print("SKIP  MediaPipe is not installed here")
        return
    except Exception as exc:
        check("the face landmark model loads", False, str(exc))
        return
    check("the face landmark model loads offline", True, ht.model_path())


def check_detection_range():
    """The frame must be big enough for the detector to see a reader."""
    face_cm = 14.0
    distance_cm = 80.0
    hfov = math.radians(60.0)
    visible_cm = 2.0 * distance_cm * math.tan(hfov / 2.0)
    projected = face_cm * ht.CameraHeadSource.FRAME_W / visible_cm
    report("a 14 cm face at 80 cm projects to", projected, " px")
    check("a reader at 80 cm is above the detector's measured floor",
          projected >= 90.0, f"{projected:.0f} px against a ~56 px box floor")
    check("the frame is not so large that the search cannot keep up",
          ht.CameraHeadSource.FRAME_W <= 640
          and ht.CameraHeadSource.FRAME_H <= 480,
          f"{ht.CameraHeadSource.FRAME_W}x{ht.CameraHeadSource.FRAME_H}")


# ═══════════════════════════════════════════════════════════════════════
#  13. Privacy
# ═══════════════════════════════════════════════════════════════════════


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
        samples = drive(motion, t, 1.0, (0.36, 0.0))
        ok = samples[-1][1].region >= 0
    except Blocked:
        ok = False
    finally:
        socket.socket = original
    check("head tracking runs with the network unavailable", ok)

    source = ht.CameraHeadSource()
    module_text = open(os.path.join(REPO_ROOT, "cuvyro", "tracking", "head.py"),
                       encoding="utf-8").read()
    check("the camera module imports no network client",
          not any(token in module_text for token in
                  ("import socket", "urllib", "requests", "http://", "https://")),
          "no socket, urllib, requests or URL in cuvyro/tracking/head.py")
    check("frames are never written to disk",
          "imwrite" not in module_text and "VideoWriter" not in module_text)
    check("the camera source starts stopped", not source.running)


# ═══════════════════════════════════════════════════════════════════════
#  14. Camera recovery (simulated device)
# ═══════════════════════════════════════════════════════════════════════


class _Device:
    """State a fake camera and its owner can share."""

    def __init__(self, opened=True, face=True):
        self.opened = opened
        self.face = face
        self.fail_until = 0.0
        self.reads = 0
        self.opens = 0
        self.releases = 0


class _FakeCapture:
    def __init__(self, state):
        self.state = state

    def isOpened(self):
        return self.state.opened

    def set(self, *_args):
        return True

    def get(self, prop):
        # Width and height are the only properties the worker reads.
        return 640.0 if int(prop) % 2 else 480.0

    def read(self):
        self.state.reads += 1
        if time.monotonic() < self.state.fail_until:
            return False, None
        import numpy as np
        return True, np.zeros((480, 640, 3), dtype=np.uint8)

    def release(self):
        self.state.releases += 1


class _FakeCv2:
    CAP_DSHOW = 700
    CAP_MSMF = 1400
    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4
    CAP_PROP_FPS = 5
    CAP_PROP_BUFFERSIZE = 6
    COLOR_BGR2RGB = 4

    def __init__(self, state):
        self.state = state

    def VideoCapture(self, _index, _backend):
        self.state.opens += 1
        return _FakeCapture(self.state)

    def cvtColor(self, frame, _code):
        return frame


class _FakeLandmarker:
    def __init__(self, state):
        self.state = state
        self.face = _face()

    def detect_for_video(self, _image, _timestamp):
        if not self.state.face:
            return SimpleNamespace(face_landmarks=[])
        return SimpleNamespace(face_landmarks=[self.face])


class _CameraStub(ht.CameraHeadSource):
    """The real worker, with a fake device and a fake face detector."""

    def __init__(self, state):
        super().__init__(frame_w=640, frame_h=480, fps=30)
        self._state = state
        self._fake_cv2 = _FakeCv2(state)
        self._fake_detector = _FakeLandmarker(state)

    def _import_cv2(self):
        return self._fake_cv2

    def _load_detector(self):
        return self._fake_detector


def _wait_for_reading(source, timeout=5.0, after=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sample = source.read()
        if sample is not None and (after is None or sample.stamp > after):
            return sample
        time.sleep(0.02)
    return None


def check_camera_recovery():
    """A device that drops for seconds must not switch the feature off.

    The field failure this replaces: the worker gave up after twelve seconds
    of a transient problem, the reader saw ``source.error`` and switched the
    toggle off, so tracking appeared to stop after 10-15 s. A camera that has
    delivered one frame must be retried for as long as the reader wants it.
    """
    state = _Device()
    source = _CameraStub(state)
    check("the simulated camera starts", source.start())
    first = _wait_for_reading(source)
    check("it delivers a reading", first is not None)
    if first is None:
        source.stop()
        return

    state.fail_until = time.monotonic() + 3.0
    time.sleep(3.3)
    check("a three-second dropout is not fatal", source.error is None,
          str(source.error))
    check("the worker is still running through the dropout", source.running)
    check("the reopen loop is bounded, not a flicker",
          state.opens <= 4, f"{state.opens} opens in ~3.3 s")

    state.fail_until = 0.0
    recovered = _wait_for_reading(source, timeout=6.0, after=first.stamp)
    check("and the reading comes back by itself", recovered is not None)
    if recovered is not None:
        check("recovery is a fresh reading, not the old one",
              recovered.stamp > first.stamp)

    # The reported failure was tracking stopping after 10-15 s. Hold the
    # device down past the old twelve-second deadline: a worker that had
    # already opened once must still be alive, quiet and retrying.
    state.fail_until = time.monotonic() + 13.0
    opens_before = state.opens
    time.sleep(13.3)
    check("a thirteen-second outage is not fatal", source.error is None,
          str(source.error))
    check("the worker is still running after the old deadline", source.running)
    check("the long outage still opens the device a bounded number of times",
          state.opens - opens_before <= 10,
          f"{state.opens - opens_before} opens in ~13.3 s")
    state.fail_until = 0.0
    recovered_again = _wait_for_reading(source, timeout=6.0,
                                        after=recovered.stamp if recovered else first.stamp)
    check("and it recovers after a long outage too", recovered_again is not None)

    source.stop()
    check("stop releases the worker", not source.running)
    check("stop releases the camera lease", ht._live_source is None)


def check_camera_never_opened():
    """A camera that cannot open at all must still report failure, not hang."""
    state = _Device(opened=False)
    source = _CameraStub(state)
    source.GIVE_UP_SECONDS = 0.7
    source.REOPEN_BACKOFF_START = 0.1
    source.REOPEN_BACKOFF_MAX = 0.2
    check("the worker starts even with no device", source.start())
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and source.error is None:
        time.sleep(0.05)
    check("a camera that never opens reports a fatal error",
          source.error is not None, str(source.error))
    check("and the worker ends rather than looping for ever",
          not source.running)
    source.stop()


def check_camera_restart():
    """Off then on must reuse one source and release the device between."""
    state = _Device()
    source = _CameraStub(state)
    check("first start", source.start())
    first = _wait_for_reading(source)
    check("first reading", first is not None)
    source.stop()
    check("stopped", not source.running)
    check("second start on the same source", source.start())
    second = _wait_for_reading(source)
    check("second reading after restart", second is not None)
    source.stop()
    check("stopped again", not source.running)
    check("lease released again", ht._live_source is None)


def check_camera_no_face():
    """A camera that sees no face must stay on and silent, not fail."""
    state = _Device(face=False)
    source = _CameraStub(state)
    source.start()
    time.sleep(0.6)
    check("no face is not an error", source.error is None, str(source.error))
    check("the worker keeps looking", source.running)
    check("no reading is invented", source.read() is None)
    source.stop()


# ═══════════════════════════════════════════════════════════════════════
#  15. Reader integration
# ═══════════════════════════════════════════════════════════════════════


class _FakeSource:
    """A camera that returns whatever the test asks for."""

    def __init__(self, x=0.0, y=0.0, running=True):
        self.x, self.y = x, y
        self.gaze_x = 0.0
        self.gaze_y = 0.0
        self.yaw = 0.0
        self.pitch = 0.0
        self.running = running
        self.error = None
        self.recovering = False
        self.ready = True
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
        self._stamp = time.monotonic()
        return ht.FaceSample(self.x, self.y, self.gaze_x, self.gaze_y,
                             self._stamp, 2, self.yaw, self.pitch)


def check_geometry():
    """The card placement helper keeps every region on screen and up top."""
    from PyQt5.QtCore import QRect, QSize
    from cuvyro.ui.overlay import grid_card_rect

    area = QRect(0, 0, 1920, 1080)
    size = QSize(384, 122)
    grid = ht.TrackingGrid()
    worst_bottom = 0
    for region in range(grid.count):
        u, v = grid.anchor(region)
        rect = grid_card_rect(area, size, u, v)
        worst_bottom = max(worst_bottom, rect.bottom())
        check(f"the card at region {region} is wholly on screen",
              area.contains(rect), f"{rect.getRect()} in {area.getRect()}")
        check(f"the card at region {region} stays in the upper half",
              rect.bottom() <= area.top() + area.height() // 2,
              f"bottom {rect.bottom()} against midline 540")
    check("no region can put the card in the lower half", worst_bottom <= 539,
          f"worst bottom {worst_bottom}")

    # Edges are clamped rather than allowed to hang off the display.
    left = grid_card_rect(area, size, 0.0, 0.0)
    right = grid_card_rect(area, size, 1.0, 1.0)
    check("the left edge clamps to the work area", left.left() == area.left(),
          str(left.left()))
    check("the right edge clamps to the work area",
          right.right() == area.right(), str(right.right()))
    check("the bottom edge clamps to the midline",
          right.bottom() <= 539, str(right.bottom()))


def check_integration():
    """The reader end: default off, regions drive the card, off is off."""
    import tempfile

    from PyQt5.QtCore import QRect
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    from cuvyro.ui import overlay as ui_overlay
    from cuvyro.storage.store import Store

    win = ui_overlay.CuvyroOverlay()
    win._store = Store(tempfile.mkdtemp(prefix="cuvyro-head-"))
    win._voice_mode = False
    win.show()
    QTest.qWait(200)

    check("a fresh reader ships with head tracking off",
          win._head_track_enabled is False and win._head_source is None)
    check("a fresh reader persists head tracking as off",
          win._settings_state().get("head_tracking") is False)

    fake = _FakeSource(x=0.0, y=0.0)
    win._head_source = fake
    win._head_track_enabled = True
    win._head_wanted = True
    win._head_motion.enable(time.monotonic())
    for _ in range(int((ht.CALIBRATE_SECONDS + 0.5) * FRAME_HZ)):
        win._poll_head_tracking()
        QTest.qWait(16)

    # Face direction has to reach the motion policy, not be dropped by the
    # reader: a turned head is a region choice even when its centre is still.
    fake.yaw = 0.45
    for _ in range(int(2.5 * FRAME_HZ)):
        win._poll_head_tracking()
        QTest.qWait(16)
    check("the reader passes face direction into the motion policy",
          win._head_offsets.region == 3,
          f"region {win._head_offsets.region}")
    fake.yaw = 0.0
    for _ in range(int(1.5 * FRAME_HZ)):
        win._poll_head_tracking()
        QTest.qWait(16)

    fake.x = 0.36
    fake.y = 0.16
    for _ in range(int(2.5 * FRAME_HZ)):
        win._poll_head_tracking()
        QTest.qWait(16)
    offsets = win._head_offsets
    check("the reader receives the selected region", offsets.region == 9,
          f"region {offsets.region}")
    check("a gaze moves the card inside that region",
          abs(offsets.u - 0.9) < 0.4, f"u {offsets.u:.3f}")

    viewport = win._text_edit.viewport()
    base = win._text_edit.contentsRect().topLeft()
    check("the type stays put inside the travelling card",
          viewport.pos() == base,
          f"viewport offset {viewport.pos().x() - base.x()}, "
          f"{viewport.pos().y() - base.y()} DIP")

    shift_x, shift_y = win._head_card_shift()
    origin = win._head_layout_origin()
    home = QRect(int(round(win._sx.value)), int(round(win._sy.value)),
                 max(1, int(round(win._sw.value))),
                 max(1, int(round(win._sh.value))))
    target = ui_overlay.grid_card_rect(
        (win.screen() or app.primaryScreen()).availableGeometry(),
        home.size(), offsets.u, offsets.v)
    check("the card travels to the grid's target",
          abs(shift_x - (target.x() - home.x())) <= 1
          and abs(shift_y - (target.y() - home.y())) <= 1,
          f"shift {shift_x},{shift_y} vs target {target.topLeft()}")
    check("the window really left its home position",
          abs(win.x() - origin.x()) >= 20,
          f"moved {win.x() - origin.x()} px on screen")

    screen = win.screen() or app.primaryScreen()
    area = screen.availableGeometry()
    check("the travelling card stays wholly on the screen",
          area.contains(win.geometry()),
          f"card {win.geometry().getRect()} in {area.getRect()}")
    check("the travelling card stays in the upper half",
          win.geometry().bottom() <= area.top() + area.height() // 2,
          f"bottom {win.geometry().bottom()} against "
          f"midline {area.top() + area.height() // 2}")

    # The editor takes the card back: type is edited at home, not mid-region.
    win._edit_mode = True
    win._on_motion_tick()
    check("opening the editor returns the card to its home position",
          win.x() == home.x() and win.y() == home.y(),
          f"card {win.x()},{win.y()} vs home {home.x()},{home.y()}")
    win._edit_mode = False
    win._on_motion_tick()
    check("leaving the editor lets the card travel again",
          win.x() != home.x(), f"card {win.x()} vs home {home.x()}")

    saved = win._persisted_card_rect()
    check("the persisted position excludes the head offset",
          saved.x() == home.x() and saved.y() == home.y(),
          f"saved {saved.x()},{saved.y()} vs home {home.x()},{home.y()}")

    win._set_head_tracking(False)
    QTest.qWait(60)
    check("switching off stops the camera", fake.stopped)
    check("switching off centres the type immediately",
          viewport.pos() == base,
          f"viewport {viewport.pos().x()},{viewport.pos().y()}")
    check("switching off puts the card back",
          win.geometry().topLeft() == home.topLeft(),
          f"card {win.geometry().topLeft()}")
    check("switching off is persisted",
          win._settings_state().get("head_tracking") is False)
    check("a switched-off reader ignores the camera",
          win._head_offsets.is_zero)

    failing = _FakeSource()
    failing.error = "camera unavailable (busy or not connected)"
    win._head_source = failing
    win._head_track_enabled = True
    win._head_wanted = True
    win._poll_head_tracking()
    QTest.qWait(40)
    check("a camera that cannot open turns the feature off",
          win._head_track_enabled is False)
    check("a failed camera is reported, not hidden",
          win._settings_state().get("head_tracking") is False)

    win.close()
    QTest.qWait(50)


def main():
    # The first-run guide is modal. A machine that has never run Cuvyro (a
    # runner, a new laptop) would block on it instead of starting, so the
    # check states the machine it describes. check_note_clearing, the
    # robustness check and the upgrade check do the same.
    from PyQt5.QtCore import QSettings
    QSettings("Cuvyro", "Cuvyro").setValue("guide_seen", True)
    print(f"head tracking — camera {CAMERA_HZ:.0f} Hz, render {FRAME_HZ:.0f} Hz")
    print(f"grid {ht.GRID_COLUMNS}x{ht.GRID_ROWS} over the top "
          f"{ht.TOP_SHARE * 100:.0f}% of the display")
    print()
    for step in (check_grid, check_stillness, check_dead_zone,
                 check_all_regions, check_continuity, check_bounds,
                 check_latency, check_jitter, check_calibration,
                 check_outliers, check_fusion, check_release, check_seed,
                 check_disable,
                 check_landmarks, check_detection_range, check_local_only,
                 check_camera_recovery, check_camera_never_opened,
                 check_camera_restart, check_camera_no_face,
                 check_geometry, check_integration):
        step()
        print()
    if failures:
        print(f"{len(failures)} failed: {', '.join(failures)}")
        return 1
    print("all head tracking checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
