"""Capture every surface of the overlay, including the ones the geometry
script does not reach: both themes, the menu, and the narrow reader.

    .venv/Scripts/python.exe packaging/shots.py --prefix before
    .venv/Scripts/python.exe packaging/shots.py --prefix after --theme light

Capture exclusion is lifted for each grab and restored immediately, because
with WDA_EXCLUDEFROMCAPTURE set every screenshot of these windows is blank.
"""

import ctypes
import os
import sys
import tempfile

from PIL import ImageGrab
from PyQt5.QtCore import QPoint, QRect, QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

SHOTS = os.path.join(os.environ.get("TEMP", "."), "shots")
FIXTURE = os.path.join(REPO_ROOT, "fixtures", "demo-script.txt")
os.makedirs(SHOTS, exist_ok=True)

WDA_NONE = 0x0
WDA_EXCLUDEFROMCAPTURE = 0x11
user32 = ctypes.windll.user32


def affinity(widget, value):
    return bool(user32.SetWindowDisplayAffinity(int(widget.winId()), value))


def grab(widget, name, pad: int = 0):
    if not widget.isVisible():
        print(f"[shot] {name}: NOT VISIBLE")
        return
    restored = affinity(widget, WDA_NONE)
    widget.repaint()
    QApplication.processEvents()
    # DWM needs a moment to recomposite a layered popup after the capture
    # exclusion is lifted; grabbing straight away blends the menu with
    # whatever was behind it. This has to keep pumping events: a blocking
    # sleep lets the popup go unpainted and the grab comes back empty.
    QTest.qWait(180)
    geo = widget.frameGeometry()
    if widget.isWindow() and widget.parent() is not None:
        # A popup is placed by the platform; frameGeometry can lag behind it.
        origin = widget.mapToGlobal(QPoint(0, 0))
        geo = QRect(origin.x(), origin.y(), geo.width(), geo.height())
    dpr = widget.devicePixelRatioF()
    box = tuple(int(round(v * dpr)) for v in
                (geo.x() - pad, geo.y() - pad,
                 geo.x() + geo.width() + pad, geo.y() + geo.height() + pad))
    ImageGrab.grab(bbox=box).save(os.path.join(SHOTS, name))
    print(f"[shot] {name}: {geo.width()}x{geo.height()} at ({geo.x()},{geo.y()})")
    if restored:
        affinity(widget, WDA_EXCLUDEFROMCAPTURE)


def grab_widget(widget, name):
    """Render a widget offscreen. Used for popups: a translucent menu window
    does not composite into a screen grab, so it comes back as wallpaper."""
    if not widget.isVisible():
        print(f"[shot] {name}: NOT VISIBLE")
        return
    QApplication.processEvents()
    pixmap = widget.grab()
    pixmap.save(os.path.join(SHOTS, name))
    print(f"[shot] {name}: {pixmap.width()}x{pixmap.height()} (offscreen)")


def main():
    prefix = "after"
    if "--prefix" in sys.argv:
        prefix = sys.argv[sys.argv.index("--prefix") + 1]
    theme = "dark"
    if "--theme" in sys.argv:
        theme = sys.argv[sys.argv.index("--theme") + 1]
    light = theme == "light"

    app = QApplication(sys.argv)
    import ui_overlay

    win = ui_overlay.NotchOverlay()
    # Captures must not write into the app's saved state: the notes they open
    # used to come back as extra note windows on the next real launch.
    from store import Store
    win._store = Store(tempfile.mkdtemp(prefix="notch-shots-"))
    win.show()
    # After show(): showEvent restores the saved theme from the store, so the
    # capture theme has to be applied here or the store wins.
    win._dark_theme = not light
    win._apply_read_mode_style()
    win._apply_control_theme()
    win.update()

    steps = []

    def add(delay, fn):
        steps.append((delay, fn))

    def load():
        # Capture from a known state: the store restores whatever the last
        # interactive run left behind, and a saved narrow reader would make
        # every "after" capture a different surface.
        win._compact_mode = False
        win._peeked = False
        win._edit_target = False
        win._font_size = max(6, min(24, int(12 * win._scale)))
        win._dark_theme = not light
        win._apply_read_mode_style()
        win._apply_typography()
        win._close_btn.show()
        win._note_btn.show()
        win._mic_btn.show()
        win._compact_btn.show()
        win._settings_btn.show()
        win._set_top_bar_icon_size(int(24 * win._scale))
        win._text_edit.show()
        win._chrome.show()
        win._wake_chrome()
        win._load_script_from_file(FIXTURE)
        app.processEvents()

    def shot_reader():
        grab(win, f"{prefix}-reader.png")

    menu_holder = {}

    def open_menu(key="menu", name="menu"):
        menu = win._build_menu()
        menu_holder[key] = menu
        pos = win._settings_btn.mapToGlobal(QPoint(0, win._settings_btn.height()))
        menu.popup(QPoint(pos.x() - menu.sizeHint().width() + win._settings_btn.width(),
                          pos.y() + 8))
        # Let the popup lay out and polish before it is grabbed, otherwise
        # the capture catches the pre-layout 100 px stub.
        menu.adjustSize()
        for _ in range(6):
            app.processEvents()
        kids = []
        for act in menu.actions():
            wd = act.defaultWidget() if hasattr(act, "defaultWidget") else None
            if wd is not None:
                kids.append(wd.sizeHint().width())
        print(f"[menu] compact={win._compact_mode} scale={win._scale:.3f} "
              f"hint={menu.sizeHint().width()}x{menu.sizeHint().height()} kids={kids}")


    def shot_menu(key="menu", name="menu"):
        menu = menu_holder.get(key)
        if menu:
            # Offscreen render: a popup's layered window composites
            # inconsistently in a screen grab (it came back as the desktop
            # behind it), while widget.grab() renders the whole menu including
            # its QWidgetAction children.
            grab_widget(menu, f"{prefix}-{name}.png")

    def close_menu(key="menu"):
        menu = menu_holder.pop(key, None)
        if menu:
            menu.close()
        app.processEvents()

    def to_manual():
        if win._voice_mode:
            win._toggle_mode(False)

    def to_voice():
        if not win._voice_mode:
            win._toggle_mode(True)

    def show_chrome():
        if hasattr(win, "_wake_chrome"):
            win._wake_chrome()

    def shot_chrome():
        grab(win, f"{prefix}-chrome.png")

    def expand():
        win._toggle_edit_mode()

    def shot_editor():
        grab(win, f"{prefix}-editor.png")

    def collapse():
        win._toggle_edit_mode()

    def narrow():
        win._toggle_compact_mode()

    def shot_narrow():
        grab(win, f"{prefix}-narrow.png")

    def widen():
        win._toggle_compact_mode()

    def note():
        n = win._open_note()
        n._edit.setPlainText("Board block reads\nHarbour plan changed\nFriday: sign-off")
        app.processEvents()
        menu_holder["note"] = n

    def shot_note():
        n = menu_holder.get("note")
        if n:
            grab(n, f"{prefix}-note.png")

    def peek():
        win._minimize()

    def shot_peek():
        grab(win, f"{prefix}-peek.png")

    def unpeek():
        win._restore_from_peek()

    def finish():
        win._save_state()
        print(f"[state] notes={len(win._notes)}")
        app.quit()

    for delay, fn in [
        (900, load),
        (1500, shot_reader),
        (1800, open_menu),
        (2300, shot_menu),
        (2600, close_menu),
        (2750, to_manual),
        (2900, lambda: open_menu("menu-manual", "menu-manual")),
        (3400, lambda: shot_menu("menu-manual", "menu-manual")),
        (3700, lambda: close_menu("menu-manual")),
        (3850, to_voice),
        (6200, shot_chrome),     # chrome has receded by now
        (6400, show_chrome),
        (6600, expand),
        (7500, shot_editor),
        (7800, collapse),
        (8800, narrow),
        (9600, shot_narrow),
        (9900, widen),
        (10900, note),
        (11300, shot_note),
        (12000, peek),
        (12800, shot_peek),
        (13000, unpeek),
        (13800, finish),
    ]:
        QTimer.singleShot(delay, fn)

    app.exec_()


if __name__ == "__main__":
    main()
