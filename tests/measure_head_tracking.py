"""Measure head and eye tracking against a real camera, on this machine.

``tests/check_head_tracking.py`` proves the motion policy with explicit
timestamps. This is the other half: what the device actually delivers, which
no simulation can tell you — effective sample rate, what the landmarker costs
per frame, how much the published position moves while a person holds still,
whether the worker survives a long run without dropping out, and whether a
person can actually reach all ten regions of the grid.

It runs a still phase, a guided walk across the ten regions, and a continuous
phase, and prints what to do before each one. Sit in front of the camera within
about a metre, face it, and follow the prompts:

    .venv/Scripts/python.exe tests/measure_head_tracking.py

It is a measurement, not a pass/fail gate. It prints numbers and, when no
camera or no face is available, reports SKIP the way
``tests/check_speech_engine.py`` does for a missing model.

Nothing is recorded, written or sent: the worker publishes a few numbers per
frame and this script keeps them in memory.
"""

import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

from cuvyro.tracking import head as ht

# The guided walk uses the same ten poses the deterministic check uses, but a
# person is asked for the intent rather than a number: lean and turn toward
# the corner, and look that way.
_REGION_ROWS = ("top", "bottom")
_COLUMN_HINTS = ("far left", "left", "centre", "right", "far right")
STILL_SECONDS = 6.0
REGION_SECONDS = 2.6
CONTINUOUS_SECONDS = 12.0


def skip(what, why):
    print(f"SKIP  {what} — {why}")


def status_prompt(label, instruction, seconds):
    """Announce a phase. The measurement runs for ``seconds`` from here."""
    print(f"\n  >>> {label.upper()}: {instruction} ({seconds:.1f} s, starting now)",
          flush=True)


def run_phase(source, motion, seconds, clock, fresh):
    """Feed the real camera into the motion policy for one phase."""
    start = time.monotonic()
    samples = []
    polls = 0
    while time.monotonic() - start < seconds:
        sample = source.read()
        polls += 1
        if sample is not None and (clock["stamp"] is None
                                   or sample.stamp > clock["stamp"]):
            clock["stamp"] = sample.stamp
            fresh.append(sample.stamp)
            samples.append(sample)
            motion.feed(sample.x, sample.y, sample.stamp,
                        sample.gaze_x, sample.gaze_y,
                        sample.yaw, sample.pitch)
        motion.poll(time.monotonic())
        if source.error:
            break
        time.sleep(0.01)
    return samples, polls


def summarise(samples):
    if not samples:
        return None
    return {
        "n": len(samples),
        "x": statistics.mean(s.x for s in samples),
        "y": statistics.mean(s.y for s in samples),
        "gx": statistics.mean(s.gaze_x for s in samples),
        "gy": statistics.mean(s.gaze_y for s in samples),
        "yaw": statistics.mean(s.yaw for s in samples),
        "pitch": statistics.mean(s.pitch for s in samples),
        "eyes": statistics.mean(s.eyes for s in samples),
        "sx": statistics.pstdev([s.x for s in samples]) if len(samples) > 2 else 0.0,
        "sy": statistics.pstdev([s.y for s in samples]) if len(samples) > 2 else 0.0,
    }


def main():
    source = ht.CameraHeadSource()
    print("head + eye tracking — real device, 5x2 upper-half grid")
    print(f"requesting {source.frame_w}x{source.frame_h} at {source.fps} fps")
    print(f"model {os.path.relpath(ht.model_path(), REPO_ROOT)}")
    source.start()

    # Wait for the first face, but not forever: a machine with no camera must
    # report SKIP rather than hang. The allowance covers the first model load
    # (~6 s here, mostly MediaPipe's import) plus the camera opening.
    deadline = time.monotonic() + 25.0
    while time.monotonic() < deadline:
        if source.error:
            break
        if source.read() is not None:
            break
        time.sleep(0.1)

    if source.error:
        skip("camera", source.error)
        source.stop()
        return 0
    if source.read() is None:
        skip("camera", "no face found — sit in front of the camera and rerun")
        source.stop()
        return 0

    print(f"opened on {source.backend or 'unknown backend'}"
          f"{' (shared mode)' if source.backend == 'MSMF' else ''}")

    # The reader's own motion policy, fed exactly what the worker publishes.
    motion = ht.HeadMotion()
    motion.enable(now=0.0)
    clock = {"stamp": None}
    fresh = []
    phases = []
    polls = 0
    costs = []
    last_cost = 0.0

    print("\n  The grid is the top half of the screen: five columns by two rows.")
    print("  The card starts in the middle of the top row after calibration.")
    status_prompt("still", "sit still and look straight at the words",
                  STILL_SECONDS)
    samples, count = run_phase(source, motion, STILL_SECONDS, clock, fresh)
    polls += count
    phases.append(("still", summarise(samples), motion.offsets()))
    if source.error:
        skip("guided walk", source.error)
    if len(samples) < 3:
        # A face that is not there for the still phase cannot walk the grid.
        # Saying so is more useful than ten phases that all read the neutral.
        skip("guided walk", "the camera saw no steady face during the still "
                            "phase — sit in front of the lens and rerun")
        print(f"tracker still running          "
              f"{'yes' if source.running and not source.error else 'NO'}")
        source.stop()
        return 0

    grid = ht.TrackingGrid()
    for row, row_name in enumerate(_REGION_ROWS):
        if source.error:
            break
        for column, hint in enumerate(_COLUMN_HINTS):
            label = f"{row_name}-{hint}"
            instruction = (
                f"lean and turn toward the {hint} of your comfortable range, "
                f"then {'look up' if row == 0 else 'look down'} and hold")
            status_prompt(label, instruction, REGION_SECONDS)
            deadline = time.monotonic() + REGION_SECONDS
            while time.monotonic() < deadline:
                sample = source.read()
                polls += 1
                if sample is not None and (clock["stamp"] is None
                                           or sample.stamp > clock["stamp"]):
                    clock["stamp"] = sample.stamp
                    fresh.append(sample.stamp)
                    motion.feed(sample.x, sample.y, sample.stamp,
                                sample.gaze_x, sample.gaze_y,
                                sample.yaw, sample.pitch)
                motion.poll(time.monotonic())
                cost = source.detect_seconds
                if cost and cost > 0.0 and cost != last_cost:
                    last_cost = cost
                    costs.append(cost)
                if source.error:
                    break
                time.sleep(0.01)
            # A short settle so the spring lands before the region is read.
            settle_until = time.monotonic() + 0.35
            while time.monotonic() < settle_until:
                motion.poll(time.monotonic())
                time.sleep(0.01)
            offsets = motion.offsets()
            expected = row * grid.columns + column
            print(f"      selected region {offsets.region} "
                  f"(the {label} pose asks for {expected})")
            phases.append((label, None, offsets))

    if source.error:
        skip("continuous phase", source.error)

    status_prompt("continuous", "sit still; this checks for a mid-run dropout",
                  CONTINUOUS_SECONDS)
    samples, count = run_phase(source, motion, CONTINUOUS_SECONDS, clock, fresh)
    polls += count
    phases.append(("continuous", summarise(samples), motion.offsets()))

    source.stop()
    if source.error:
        skip("end of run", source.error)

    # A camera the reader is not using must be free for the next application.
    try:
        import cv2

        probe = cv2.VideoCapture(0, getattr(cv2, "CAP_DSHOW", 0))
        freed = probe.isOpened()
        probe.release()
        print(f"\ndevice released after stop      {'yes' if freed else 'NO'}")
    except Exception:
        skip("release check", "OpenCV unavailable")

    if not fresh:
        skip("measurement", "no face readings during any phase")
        return 0

    # ── Cadence and continuity ───────────────────────────────────────
    gaps = [b - a for a, b in zip(fresh, fresh[1:])]
    print()
    print(f"fresh readings                 {len(fresh)} of {polls} polls"
          f"  ({100.0 * len(fresh) / max(1, polls):.0f}%)")
    if len(fresh) > 1:
        print(f"fresh rate                     "
              f"{len(fresh) / max(0.001, fresh[-1] - fresh[0]):.1f} Hz")
    if gaps:
        print(f"largest gap between readings   {max(gaps) * 1000:6.0f} ms"
              f"  (mean {statistics.mean(gaps) * 1000:5.0f} ms)")
        if max(gaps) > ht.LOSS_SECONDS:
            print("  NOTE: a gap this long is a dropout; the card will release"
                  " to the neutral region if it lasts.")
    print(f"tracker still running          "
          f"{'yes' if not source.error else 'NO — ' + str(source.error)}")
    if costs:
        print(f"frame cost (read + infer)      mean {statistics.mean(costs) * 1000:6.1f} ms"
              f", worst {max(costs) * 1000:6.1f} ms")
        print(f"  against a {1000.0 / max(1, source.fps):.0f} ms interval at the"
              f" requested {source.fps} fps")

    # ── Per-phase ────────────────────────────────────────────────────
    print()
    print(f"{'phase':<18}{'head x':>9}{'head y':>9}{'gaze x':>9}"
          f"{'yaw':>9}{'pitch':>9}{'region':>8}")
    for label, data, offsets in phases:
        if not data:
            print(f"{label:<18}{'—':>9}{'':>9}{'':>9}{'':>9}{'':>9}"
                  f"{offsets.region:>8}")
            continue
        print(f"{label:<18}{data['x']:>+9.3f}{data['y']:>+9.3f}"
              f"{data['gx']:>+9.3f}{data['yaw']:>+9.3f}"
              f"{data['pitch']:>+9.3f}{offsets.region:>8}")

    # ── Region coverage ──────────────────────────────────────────────
    guided = [(label, offsets) for label, data, offsets in phases
              if label not in ("still", "continuous")]
    expected_for = {"top-far left": 0, "top-left": 1, "top-centre": 2,
                    "top-right": 3, "top-far right": 4,
                    "bottom-far left": 5, "bottom-left": 6,
                    "bottom-centre": 7, "bottom-right": 8,
                    "bottom-far right": 9}
    hits = 0
    seen = set()
    print()
    print("  Guided region walk:")
    for label, offsets in guided:
        expected = expected_for.get(label, -1)
        seen.add(offsets.region)
        hit = offsets.region == expected
        hits += 1 if hit else 0
        print(f"    {label:<18} asked {expected:2d}  got {offsets.region:2d}"
              f"  {'ok' if hit else 'MISS'}")
    print(f"  coverage: {len(seen)}/10 regions seen, "
          f"{hits}/{len(guided)} asked-for regions matched")
    if len(seen) < 10:
        print("  NOTE: the poses are a suggestion; a smaller lean, a different"
              " distance or a tired neck can land in a neighbouring cell.")
    if hits < len(guided):
        print("  NOTE: this is why the full-grid walk is part of the manual"
              " measure — it says whether the mapping matches a person, not"
              " just a synthetic pose.")

    still = next((data for label, data, _ in phases if label == "still"), None)
    if still:
        print()
        print(f"while still: head x stdev      {still['sx']:.4f} normalised")
        print(f"             head y stdev      {still['sy']:.4f} normalised")
        print(f"             eyes usable       {still['eyes']:.1f} of 2")

    print()
    print("  Reading the numbers:")
    print("  * fresh rate and the largest gap are what the smoother actually")
    print("    sees; a gap near or above 1.2 s is a dropout;")
    print("  * frame cost is latency that cannot be smoothed away;")
    print("  * region coverage and matches are the only check that the grid")
    print("    makes sense to a person rather than to a synthetic face.")
    print()
    print("Camera closed; nothing was recorded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
