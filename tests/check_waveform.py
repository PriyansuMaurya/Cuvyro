"""Microphone waveform checks: silent, speaking, muted, disconnected.

The ribbon at the foot of the card is the only element that moves with the
microphone, so it has to stay honest in every state of the mic, keep out of
the reading line, and stay cheap enough not to steal frames from the
recogniser or the scroll clock.

The levels are fed straight into the overlay's slot - the same place the
worker's signal lands - so a real microphone is never opened here.

    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_waveform.py
"""

import os
import sys
import tempfile
import time

from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

failures = []


def check(name, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not condition:
        failures.append(name)


def wait(ms):
    QTest.qWait(ms)


class Feed:
    """One microphone level per 100 ms block, as the worker emits them."""

    def __init__(self, win):
        self.win = win

    def blocks(self, level, count, speaking=True, gap_ms=40):
        self.win._is_speaking = speaking
        for _ in range(count):
            self.win._on_volume_level(level)
            wait(gap_ms)


def ribbon_pixels(win):
    """Column heights of the drawn ribbon, in pixels.

    Voice mode paints blue, Keys & Scroll paints green; either way the bars
    carry more blue or more green than red, which separates them from the
    white type that shares the foot strip.
    """
    image = win.grab().toImage()
    x0, pitch, count, bar_w = win.waveform_layout()
    band = win.waveform_band()
    channel = 2 if win._voice_mode else 1
    columns = []
    for index in range(count):
        sx = int(x0 + index * pitch + bar_w / 2.0)
        tall = 0
        for y in range(int(band.top()), int(band.bottom()) + 1):
            color = image.pixelColor(sx, y)
            channels = (color.red(), color.green(), color.blue())
            if channels[channel] - channels[0] > 8:
                tall += 1
        columns.append(tall)
    return columns


def count_updates(win):
    """Count the repaints the overlay asks for in Python."""
    box = {"count": 0}
    original = win.update

    def counted(*args, **kwargs):
        box["count"] += 1
        return original(*args, **kwargs)

    win.update = counted
    box["restore"] = lambda: setattr(win, "update", original)
    box["reset"] = lambda: box.__setitem__("count", 0)
    return box


def main():
    # The first-run guide is modal. A machine that has never run Cuvyro (a
    # runner, a new laptop) would block on it instead of starting, so the
    # check states the machine it describes. check_note_clearing, the
    # robustness check and the upgrade check do the same.
    from PyQt5.QtCore import QSettings
    QSettings("Cuvyro", "Cuvyro").setValue("guide_seen", True)
    app = QApplication(sys.argv)
    from cuvyro.ui import overlay as ui_overlay
    from cuvyro.storage.store import Store

    win = ui_overlay.CuvyroOverlay()
    win._store = Store(tempfile.mkdtemp(prefix="cuvyro-wave-"))
    win._voice_mode = False          # keep the microphone out of the run
    win.show()
    wait(600)                        # let the card and its springs settle
    win._voice_mode = True           # paint in the voice colour, no worker
    win._text_edit.clear()
    wait(120)

    s = win._scale
    count = win.WAVE_BARS
    updates = count_updates(win)

    # ── Placement: the ribbon lives at the foot, under the reading line ──
    band = win.waveform_band()
    reading = win._text_edit.reading_inset()
    check("the ribbon sits below the line being read", band.top() > reading,
          f"band {band.top():.1f}..{band.bottom():.1f}, reading line {reading:.1f}")
    check("the ribbon stays inside the card's foot",
          band.bottom() <= win.height()
          and band.left() >= 0
          and band.right() <= win._card_width(),
          f"band {band.left():.1f}..{band.right():.1f} in "
          f"0..{win._card_width()}, height {win.height()}")

    # ── Silent microphone: a resting ribbon, and no repaint churn ────────
    columns = ribbon_pixels(win)
    check("a silent microphone still draws the ribbon",
          sum(1 for tall in columns if tall > 0) >= len(columns) - 1,
          f"columns {columns}")
    rest_tall = max(columns)
    check("a resting bar is a short dash", rest_tall <= int(3 * s),
          f"tallest resting bar {rest_tall} px at {s:.2f}x")

    updates["reset"]()
    wait(400)
    check("a silent reader asks for no frames", updates["count"] == 0,
          f"{updates['count']} repaints in 400 ms")

    # ── Speaking: quiet and loud intensities read differently ────────────
    feed = Feed(win)
    feed.blocks(0.10, 8)
    wait(160)
    quiet = max(ribbon_pixels(win))
    quiet_heights = list(win._wave_heights)

    win._reset_waveform()
    wait(60)
    feed.blocks(0.85, 8)
    wait(160)
    loud = max(ribbon_pixels(win))
    loud_heights = list(win._wave_heights)

    check("the ribbon reacts to microphone input", loud > rest_tall + 1,
          f"loud {loud} px vs resting {rest_tall} px")
    check("louder speech draws a taller ribbon", loud >= quiet + 2,
          f"quiet {quiet} px, loud {loud} px")
    check("the bars stay bounded", all(0.0 <= h <= 1.0 for h in loud_heights),
          f"range {min(loud_heights):.2f}..{max(loud_heights):.2f}")
    check("the newest level enters on the right",
          loud_heights[-1] > loud_heights[0] + 0.2,
          f"left {loud_heights[0]:.2f}, right {loud_heights[-1]:.2f}")

    # Smooth, not stepped: consecutive frames differ while the ribbon rises.
    win._reset_waveform()
    wait(60)
    feed.blocks(0.8, 3, gap_ms=40)
    first = list(win._wave_heights)
    wait(70)
    second = list(win._wave_heights)
    check("the ribbon moves between frames while it rises",
          any(abs(a - b) > 1e-6 for a, b in zip(first, second))
          and max(second) > max(first),
          f"{max(first):.2f} -> {max(second):.2f}")

    # ── Silence: the ribbon drains on its own ───────────────────────────
    feed.blocks(0.0, 10, speaking=False, gap_ms=30)
    wait(300)
    drained = max(win._wave_heights)
    check("silence drains the ribbon back to rest", drained < 0.02,
          f"tallest bar {drained:.3f}")

    # ── Muted: no signal reaches the ribbon and nothing repaints ────────
    win._muted = True
    win._reset_waveform()
    wait(60)
    muted_rest = ribbon_pixels(win)
    updates["reset"]()
    feed.blocks(0.9, 8)
    wait(300)
    muted_loud = ribbon_pixels(win)
    check("a muted microphone never lifts the ribbon",
          max(win._wave_heights) == 0.0, f"tallest bar {max(win._wave_heights):.3f}")
    check("the muted ribbon looks like the resting one",
          muted_loud == muted_rest,
          f"muted {muted_loud} vs rest {muted_rest}")
    check("a muted microphone asks for no frames", updates["count"] == 0,
          f"{updates['count']} repaints in 300 ms")

    win._muted = False
    win._is_speaking = True
    wait(200)
    check("unmuting does not replay the audio heard while muted",
          max(win._wave_heights) == 0.0,
          f"tallest bar {max(win._wave_heights):.3f}")

    # ── Disconnected: the stream stops without a last loud frame frozen ──
    feed.blocks(0.85, 6)
    wait(120)
    check("the connected ribbon is live before the stream drops",
          max(win._wave_heights) > 0.4, f"tallest bar {max(win._wave_heights):.2f}")
    win._audio_worker = None                       # the worker never returns
    win._wave_last_sample = time.monotonic() - 2.0  # no block for a while
    wait(400)
    check("a disconnected microphone drains the ribbon",
          max(win._wave_heights) < 0.02,
          f"tallest bar {max(win._wave_heights):.3f}")

    # The drain must follow the clock, not the number of frames the host
    # delivered. A busy machine used to leave a dead mic's bars standing for
    # visibly longer than an idle one, because each frame eased by a fixed
    # fraction of the gap.
    win._muted = False
    win._is_speaking = False
    win._wave_heights[:] = [1.0] * count
    win._wave_step_at = time.monotonic() - 0.4
    win._advance_waveform()
    check("one late frame drains as far as a frame budget would",
          max(win._wave_heights) < 0.02,
          f"tallest bar {max(win._wave_heights):.3f} after a single 400 ms step")
    win._is_speaking = True          # the next section is a live microphone again

    # Repeated loss and recovery must not grow the history without bound.
    for _ in range(120):
        win._on_volume_level(0.5)
    win._wave_last_sample = time.monotonic()
    wait(60)
    check("the level history stays bounded",
          len(win._wave_levels) == count, f"{len(win._wave_levels)} levels")
    check("a reconnected microphone lifts the ribbon again",
          max(win._wave_heights) > 0.05, f"tallest bar {max(win._wave_heights):.2f}")

    # ── Editor: the ribbon is not part of the typing surface ────────────
    win._edit_mode = True
    before = list(win._wave_heights)
    feed.blocks(0.9, 5)
    wait(120)
    check("the editor does not animate the ribbon",
          win._wave_heights == before,
          f"{max(win._wave_heights):.2f} vs {max(before):.2f}")
    win._edit_mode = False

    # ── Cost: the per-frame step is constant work, not scene work ───────
    for _ in range(count):
        win._wave_levels.append(0.6)
    started = time.perf_counter()
    steps = 600
    for _ in range(steps):
        win._advance_waveform()
    elapsed = time.perf_counter() - started
    per_step = elapsed / steps * 1000.0
    check("one waveform step costs well under a frame",
          per_step < 0.5, f"{per_step:.3f} ms per step")

    # And silence never reaches the scroll clock with a repaint request.
    win._reset_waveform()
    updates["reset"]()
    wait(300)
    check("the ribbon leaves the scroll clock untouched at rest",
          updates["count"] <= 1, f"{updates['count']} repaints after reset")

    updates["restore"]()
    win.close()
    print()
    if failures:
        print(f"{len(failures)} waveform check(s) failed:")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("All waveform checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
