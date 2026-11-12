"""Measure head and eye tracking against a real camera, on this machine.

``tests/check_head_tracking.py`` proves the motion policy with explicit
timestamps. This is the other half: what the device actually delivers, which
no simulation can tell you — effective sample rate, what the landmarker costs
per frame, how much the published position moves while a person holds still,
and whether the reader's own pipeline turns a lean and a glance into a card
that moves.

It runs five phases and prints what to do before each one. Sit in front of the
camera within about a metre, face it, and follow the prompts:

    .venv/Scripts/python.exe tests/measure_head_tracking.py

It is a measurement, not a pass/fail gate. It prints numbers and, when no
camera or no face is available, reports SKIP the way
``tests/check_speech_engine.py`` does for a missing model.

Nothing is recorded, written or sent: the worker publishes a few numbers per
frame and this script keeps them in memory.
"""

import math
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

import head_tracking as ht

# (label, instruction, seconds)
PHASES = (
    ("still", "sit still and look straight at the words", 5.0),
    ("look right", "look to your right, without turning your head", 3.0),
    ("look left", "look to your left, without turning your head", 3.0),
    ("lean right", "lean your head to the right, then hold", 3.0),
    ("lean left", "lean your head to the left, then hold", 3.0),
    ("still again", "sit still and look straight at the words", 3.0),
)


def skip(what, why):
    print(f"SKIP  {what} — {why}")


def phase_prompt(label, instruction, seconds):
    print(f"\n  >>> {label.upper()}: {instruction} ({seconds:.0f} s)", flush=True)
    for remaining in range(int(seconds), 0, -1):
        print(f"      {remaining}…", end="", flush=True)
        time.sleep(1.0)
    print()


def main():
    source = ht.CameraHeadSource()
    print("head + eye tracking — real device")
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
    clock = 0.0

    results = []
    fresh = 0
    polls = 0
    costs = []
    last_stamp = None
    last_cost = 0.0

    for label, instruction, seconds in PHASES:
        if label == "still":
            # Calibration happens on the first phase's samples, as it does in
            # the reader: a prompt that changed posture first would be
            # measuring the wrong neutral.
            pass
        phase_prompt(label, instruction, seconds)
        start = time.monotonic()
        samples = []
        while time.monotonic() - start < seconds:
            sample = source.read()
            polls += 1
            if sample is not None and (last_stamp is None
                                       or sample.stamp > last_stamp):
                last_stamp = sample.stamp
                fresh += 1
                samples.append(sample)
                clock = sample.stamp
                motion.feed(sample.x, sample.y, clock,
                            sample.gaze_x, sample.gaze_y)
            cost = source.detect_seconds
            if cost and cost > 0.0 and cost != last_cost:
                last_cost = cost
                costs.append(cost)
            if source.error:
                break
            time.sleep(0.01)
        offsets = motion.poll(time.monotonic())
        if source.error:
            break
        if not samples:
            results.append((label, None))
            continue
        results.append((label, {
            "n": len(samples),
            "sx": statistics.pstdev([s.x for s in samples]) if len(samples) > 2 else 0.0,
            "sy": statistics.pstdev([s.y for s in samples]) if len(samples) > 2 else 0.0,
            "sgx": statistics.pstdev([s.gaze_x for s in samples]) if len(samples) > 2 else 0.0,
            "x": statistics.mean(s.x for s in samples),
            "y": statistics.mean(s.y for s in samples),
            "gx": statistics.mean(s.gaze_x for s in samples),
            "gy": statistics.mean(s.gaze_y for s in samples),
            "eyes": statistics.mean(s.eyes for s in samples),
            "text_x": offsets.text_dx,
            "text_y": offsets.text_dy,
            "offset": offsets,
        }))

    source.stop()
    if source.error:
        skip("second half of the run", source.error)

    # A camera the reader is not using must be free for the next application.
    # This is the whole promise of "does not interfere with other camera apps",
    # so it is measured rather than assumed.
    try:
        import cv2

        probe = cv2.VideoCapture(0, getattr(cv2, "CAP_DSHOW", 0))
        freed = probe.isOpened()
        probe.release()
        print(f"\ndevice released after stop      {'yes' if freed else 'NO'}")
    except Exception:
        skip("release check", "OpenCV unavailable")

    if not any(r[1] for r in results):
        skip("measurement", "no face readings during any phase")
        return 0

    # ── Cadence ──────────────────────────────────────────────────────
    print()
    print(f"fresh readings                 {fresh} of {polls} polls"
          f"  ({100.0 * fresh / max(1, polls):.0f}%)")
    print(f"gaze usable                    "
          f"{statistics.mean(r[1]['eyes'] for r in results if r[1]):.1f} of 2 eyes")
    if costs:
        print(f"frame cost (read + infer)      mean {statistics.mean(costs) * 1000:6.1f} ms"
              f", worst {max(costs) * 1000:6.1f} ms")
        print(f"  against a {1000.0 / max(1, source.fps):.0f} ms interval at the"
              f" requested {source.fps} fps")

    # ── Per-phase ────────────────────────────────────────────────────
    print()
    print(f"{'phase':<12}{'head x':>9}{'head y':>9}{'gaze x':>9}{'gaze y':>9}"
          f"{'text x':>9}{'text y':>9}")
    for label, data in results:
        if not data:
            print(f"{label:<12}{'—':>9}")
            continue
        print(f"{label:<12}{data['x']:>+9.3f}{data['y']:>+9.3f}"
              f"{data['gx']:>+9.3f}{data['gy']:>+9.3f}"
              f"{data['text_x']:>+9.1f}{data['text_y']:>+9.1f}")

    def find(label):
        return next((d for name, d in results if name == label and d), None)

    still, again = find("still"), find("still again")
    if still:
        print()
        print(f"while still: head x stdev      {still['sx']:.4f} normalised"
              f"  ({still['sx'] / ht.RANGE * ht.MAX_TEXT_DX:.1f} DIP if unfiltered)")
        print(f"             gaze x stdev      {still['sgx']:.3f} of a half-eye")
    if still and again:
        print(f"drift between the two stills   "
              f"x {again['x'] - still['x']:+.4f}  y {again['y'] - still['y']:+.4f}")

    # ── Does a lean move the card? ───────────────────────────────────
    right, left = find("lean right"), find("lean left")
    if right and left:
        travel = right["text_x"] - left["text_x"]
        print()
        print(f"lean right → text x            {right['text_x']:+.1f} DIP")
        print(f"lean left  → text x            {left['text_x']:+.1f} DIP")
        print(f"travel between the two leans   {travel:+.1f} DIP")
        if abs(travel) < 10.0:
            print("  NOTE: a lean this size should cross far more than 10 DIP;"
                  " either the lean was small or the sign is wrong.")
        elif travel < 0.0:
            print("  NOTE: the card moved against the lean. HEAD_X_SIGN is"
                  " inverted for this camera.")
        else:
            print("  the card follows the lean, in the right direction.")

    # ── Does a glance bias it? ───────────────────────────────────────
    look_r, look_l = find("look right"), find("look left")
    if look_r and look_l:
        print()
        print(f"look right → gaze x            {look_r['gx']:+.3f}"
              f"   text x {look_r['text_x']:+.1f} DIP")
        print(f"look left  → gaze x            {look_l['gx']:+.3f}"
              f"   text x {look_l['text_x']:+.1f} DIP")
        spit = look_r["gx"] - look_l["gx"]
        print(f"gaze separation                {spit:+.3f} of an eye half-width")
        if abs(spit) < 0.10:
            print("  NOTE: the irises barely moved — at this size the gaze"
                  " signal is noise, and the card is right to ignore it.")
        else:
            # The gaze share is bounded: however far the eyes go, it is
            # GAZE_GAIN of the travel at most.
            limit = ht.MAX_TEXT_DX * ht.GAZE_GAIN
            print(f"  gaze share of the travel is capped at {limit:.1f} DIP")

    print()
    print("  Reading the numbers:")
    print("  * fresh readings are the cadence the smoother actually sees;")
    print("  * frame cost is latency that cannot be smoothed away;")
    print("  * lean travel is the card following the head, and its sign is")
    print("    the one thing only a real person can confirm;")
    print("  * gaze separation says whether the iris signal is usable at")
    print("    this distance, which no synthetic face can tell you.")
    print()
    print("Camera closed; nothing was recorded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
