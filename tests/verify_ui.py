"""Drive the overlay through its three surfaces and capture each one.

Rects come from the widgets themselves, so the printed geometry is exact
rather than inferred from the image. Capture exclusion is lifted for the
length of each grab and restored immediately: with
WDA_EXCLUDEFROMCAPTURE set, every screenshot of these windows is blank.
The exclusion itself is asserted separately (see --check-protection).
"""

import ctypes
import os
import sys
import tempfile

from PIL import ImageGrab
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

SHOTS = os.path.join(os.environ.get("TEMP", "."), "shots")
FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "demo-script.txt")
os.makedirs(SHOTS, exist_ok=True)

WDA_NONE = 0x0
WDA_EXCLUDEFROMCAPTURE = 0x11

user32 = ctypes.windll.user32


def set_affinity(widget, value):
    return bool(user32.SetWindowDisplayAffinity(int(widget.winId()), value))


def get_affinity(widget):
    out = ctypes.c_ulong(0)
    if not user32.GetWindowDisplayAffinity(int(widget.winId()), ctypes.byref(out)):
        return None
    return out.value


def shot(widget, name, lift_exclusion=True):
    """Grab the widget's rect, temporarily making it visible to screen capture."""
    if not widget.isVisible():
        print(f"[shot] {name}: NOT VISIBLE")
        return
    restored = False
    if lift_exclusion:
        restored = set_affinity(widget, WDA_NONE)
    widget.repaint()
    QApplication.processEvents()

    geo = widget.frameGeometry()
    dpr = widget.devicePixelRatioF()  # grab in physical pixels, report in DIP
    box = tuple(int(round(v * dpr)) for v in (
        geo.x(), geo.y(), geo.x() + geo.width(), geo.y() + geo.height(),
    ))
    ImageGrab.grab(bbox=box).save(os.path.join(SHOTS, name))
    print(
        f"[shot] {name}: {geo.width()}x{geo.height()} DIP at ({geo.x()},{geo.y()}) "
        f"dpr={dpr:g}"
    )
    if restored:
        set_affinity(widget, WDA_EXCLUDEFROMCAPTURE)


def main():
    if "--check-protection" in sys.argv:
        check_protection()
        return

    app = QApplication(sys.argv)
    from cuvyro.ui import overlay as ui_overlay

    # --prefix NAME names the captures; --theme light flips the palette.
    prefix = "after"
    if "--prefix" in sys.argv:
        prefix = sys.argv[sys.argv.index("--prefix") + 1]
    light = "--theme" in sys.argv and sys.argv[sys.argv.index("--theme") + 1] == "light"

    win = ui_overlay.CuvyroOverlay()
    # A capture run must not write into the app's own saved state: the note it
    # opens used to come back as an extra note window on the next launch.
    from cuvyro.storage.store import Store
    win._store = Store(tempfile.mkdtemp(prefix="cuvyro-verify-"))
    if light:
        win._dark_theme = False
        win._apply_read_mode_style()
    win.show()

    def load():
        if os.path.exists(FIXTURE):
            win._load_script_from_file(FIXTURE)
        app.processEvents()

    def shot_compact():
        shot(win, f"{prefix}-compact.png")

    def expand():
        win._toggle_edit_mode()

    def shot_editor():
        shot(win, f"{prefix}-editor.png")

    def collapse():
        win._toggle_edit_mode()

    def make_note():
        note = win._open_note()
        note._edit.setPlainText("Board block reads\nHarbour plan changed\nFriday: sign-off")
        app.processEvents()
        # A note arrives on a spring, so it is 94% of its size for the first
        # few frames. The capture and the geometry report are about the
        # resting surface, so let the entrance land before grabbing it.
        for _ in range(120):
            if note._presence.settled:
                break
            note._presence.step(1 / 60.0)
            note._apply_presence()
        app.processEvents()
        shot(note, f"{prefix}-note.png")

    def finish():
        win._save_state()
        print(f"[state] notes={len(win._notes)} script_len={len(win._script_text)}")
        print(f"[confirm] note rect expected 260x165, editor 860x320, compact 340x122")
        app.quit()

    for delay, fn in [
        (1200, load),
        (2000, shot_compact),
        (2600, expand),
        (4000, shot_editor),
        (4600, collapse),
        (6000, make_note),
        (7400, finish),
    ]:
        QTimer.singleShot(delay, fn)

    app.exec_()


def check_protection():
    """Assert the windows really do carry capture exclusion when shown."""
    app = QApplication([])
    from cuvyro.ui import overlay as ui_overlay

    win = ui_overlay.CuvyroOverlay()
    from cuvyro.storage.store import Store
    win._store = Store(tempfile.mkdtemp(prefix="cuvyro-protection-"))
    win.show()
    note = win._open_note()
    win._load_script_from_file(FIXTURE)

    def report():
        print(f"[protection] overlay affinity=0x{get_affinity(win):02x} (want 0x11)")
        print(f"[protection] note    affinity=0x{get_affinity(note):02x} (want 0x11)")
        app.quit()

    QTimer.singleShot(1500, report)
    app.exec_()


if __name__ == "__main__":
    main()
