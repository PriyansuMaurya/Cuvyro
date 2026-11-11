"""Measure head tracking against a real camera, on this machine.

``tests/check_head_tracking.py`` proves the motion policy with explicit
timestamps. This is the other half: what the device actually delivers, which
no simulation can tell you — effective sample rate, what the detector costs
per frame, how much the published position moves while a person holds still,
and whether the camera can be opened in a mode that lets a video call keep
using it.

It is a measurement, not a pass/fail gate. It prints numbers and, when no
camera or no face is available, reports SKIP the way
``tests/check_speech_engine.py`` does for a missing model.

Run it, sit in front of the camera, and hold still for the first five
seconds:

    .venv/Scripts/python.exe tests/measure_head_tracking.py

Nothing is recorded, written or sent: the worker publishes two floats per
frame and this script keeps a list of them in memory.
"""

import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

import head_tracking as ht

STILL_SECONDS = 5.0
TOTAL_SECONDS = 10.0


def skip(what, why):
    print(f"SKIP  {what} — {why}")


def main():
    source = ht.CameraHeadSource()
    print("head tracking — real device")
    print(f"requesting {source.frame_w}x{source.frame_h} at {source.fps} fps")
    source.start()

    # Wait for the first face, but not forever: a machine with no camera must
    # report SKIP rather than hang.
    deadline = time.monotonic() + 8.0
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
    print()

    samples = []
    costs = []
    start = time.monotonic()
    last_stamp = None
    last_cost = 0.0
    while time.monotonic() - start < TOTAL_SECONDS:
        sample = source.read()
        if sample is not None and (last_stamp is None or sample[2] > last_stamp):
            last_stamp = sample[2]
            samples.append((sample[2] - start, sample[0], sample[1]))
        cost = source.detect_seconds
        if cost and cost != last_cost:
            last_cost = cost
            costs.append(cost)
        if source.error:
            break
        time.sleep(0.01)
    source.stop()

    # A camera the reader is not using must be free for the next application.
    # This is the whole promise of "does not interfere with other camera apps",
    # so it is measured rather than assumed.
    try:
        import cv2

        probe = cv2.VideoCapture(0, getattr(cv2, "CAP_DSHOW", 0))
        freed = probe.isOpened()
        probe.release()
        print(f"device released after stop      {'yes' if freed else 'NO'}")
    except Exception:
        skip("release check", "OpenCV unavailable")

    if source.error:
        skip("second half of the run", source.error)
    if len(samples) < 10:
        skip("measurement", f"only {len(samples)} face readings in "
                            f"{TOTAL_SECONDS:.0f} s")
        return 0

    # ── Sample rate ──────────────────────────────────────────────────
    span = samples[-1][0] - samples[0][0]
    rate = (len(samples) - 1) / span if span > 0 else 0.0
    print(f"effective sample rate       {rate:6.1f} Hz")
    gaps = [samples[i + 1][0] - samples[i][0] for i in range(len(samples) - 1)]
    if gaps:
        print(f"gap between readings        mean {statistics.mean(gaps) * 1000:6.1f} ms"
              f", worst {max(gaps) * 1000:6.1f} ms")

    # ── Detector cost ────────────────────────────────────────────────
    if costs:
        print(f"frame cost (read+detect)    mean {statistics.mean(costs) * 1000:6.1f} ms"
              f", worst {max(costs) * 1000:6.1f} ms")
        print(f"  against a {1000.0 / max(1, source.fps):.0f} ms frame interval"
              f" at the requested {source.fps} fps")
    if rate and rate < source.fps * 0.5:
        print(f"  NOTE: readings arrive at {rate:.1f} Hz, well under the"
              f" {source.fps} fps asked for — a gap longer than"
              f" {ht.LOSS_SECONDS * 1000:.0f} ms releases the text and makes"
              f" the words step rather than follow.")

    # ── Stillness ────────────────────────────────────────────────────
    still = [s for s in samples if s[0] <= min(STILL_SECONDS, span)]
    if len(still) >= 8:
        xs = [s[1] for s in still]
        ys = [s[2] for s in still]
        spread_x = statistics.pstdev(xs)
        spread_y = statistics.pstdev(ys)
        span_x = max(xs) - min(xs)
        span_y = max(ys) - min(ys)
        print()
        print("while the subject holds still:")
        print(f"  x  stdev {spread_x:.4f}  peak-to-peak {span_x:.4f} (normalised)")
        print(f"  y  stdev {spread_y:.4f}  peak-to-peak {span_y:.4f} (normalised)")

        # Turn the raw detector noise into what the reader would paint, by
        # running the same samples through the real smoothing policy.
        motion = ht.HeadMotion()
        motion.enable(now=0.0)
        for _ in range(int((ht.CALIBRATE_SECONDS + 0.2) * 10)):
            motion.feed(xs[0], ys[0], 0.0)
            motion.poll(0.0)
        painted = []
        t = 0.0
        for _, x, y in still:
            motion.feed(x, y, t)
            painted.append(motion.poll(t).text_dx)
            t += 1.0 / max(1.0, rate)
        if painted:
            print(f"  the text it would paint    peak-to-peak "
                  f"{max(painted) - min(painted):.2f} DIP")

    # ── Drift ────────────────────────────────────────────────────────
    quarter = max(1, len(samples) // 4)
    head = samples[:quarter]
    tail = samples[-quarter:]
    drift_x = statistics.mean([s[1] for s in tail]) - statistics.mean([s[1] for s in head])
    drift_y = statistics.mean([s[2] for s in tail]) - statistics.mean([s[2] for s in head])
    print()
    print(f"drift over the run          x {drift_x:+.4f}  y {drift_y:+.4f} (normalised)")

    # ── What this means for the reader ───────────────────────────────
    print()
    print("  Reading the numbers:")
    print("  * sample rate and frame cost are latency you cannot smooth away;")
    print("  * still-subject spread is what the dead zone and the 1€ filter face;")
    print("  * drift is posture change, which is why enabling recalibrates;")
    print("  * eye contact is not measured here — it is the modelled result in")
    print("    tests/check_head_tracking.py, and it is why the feature ships off.")
    print()
    print("Camera closed; nothing was recorded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
