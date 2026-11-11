"""Measure the overlay's real behaviour, so the audit is evidence.

Each check prints what was observed. Nothing here changes the app; it reads
the live widgets and drives them the way a user would.

    .venv/Scripts/python.exe packaging/audit_current.py
"""

import ctypes
import os
import sys
import time
import tempfile

from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
from PyQt5.QtGui import QFont, QFontMetricsF, QKeyEvent, QMouseEvent, QTextCursor
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

FIXTURE = os.path.join(REPO_ROOT, "fixtures", "demo-script.txt")

user32 = ctypes.windll.user32
SPI_GETCLIENTAREAANIMATION = 0x1042


def report(label, value):
    print(f"{label}: {value}")


def applied_pitch(text_edit):
    """Read the line height actually on the first block of the document."""
    block = text_edit.document().firstBlock()
    if not block.isValid():
        return None
    return block.blockFormat().lineHeight()


def main():
    app = QApplication(sys.argv)
    import ui_overlay
    from store import Store

    win = ui_overlay.NotchOverlay()
    win._store = Store(tempfile.mkdtemp(prefix="notch-audit-"))
    win._voice_mode = False
    win.show()
    QTest.qWait(400)
    win._load_script_from_file(FIXTURE)
    QTest.qWait(300)

    # ── 1. Typography: does the leading follow the font? ──
    metrics = QFontMetricsF(win._text_edit.font())
    pitch = applied_pitch(win._text_edit)
    report("font point size", win._font_size)
    report("font pixel height (ascent+descent)", round(metrics.height(), 1))
    report("line pitch applied", pitch)
    report("leading headroom at default size", round((pitch or 0) - metrics.height(), 1))
    report("letter spacing at default size",
           round(win._text_edit.font().letterSpacing(), 2))

    win._font_size = 24
    win._apply_typography()
    QTest.qWait(150)
    metrics_big = QFontMetricsF(win._text_edit.font())
    pitch_big = applied_pitch(win._text_edit)
    report("font pixel height at max size (24 pt)", round(metrics_big.height(), 1))
    report("line pitch at max size", pitch_big)
    report("leading headroom at max size", round((pitch_big or 0) - metrics_big.height(), 1))
    report("OVERLAP at max size", (pitch_big or 0) < metrics_big.height())
    report("letter spacing at max size",
           round(win._text_edit.font().letterSpacing(), 2))
    win._font_size = 12
    win._apply_typography()
    QTest.qWait(120)

    # ── 2. Hit targets ──
    buttons = {
        "close": win._close_btn,
        "note": win._note_btn,
        "mic": win._mic_btn,
        "compact": win._compact_btn,
    }
    for name, btn in buttons.items():
        report(f"hit target {name}", f"{btn.width()}x{btn.height()} px")

    # ── 3. Does a button react on pointer-down? ──
    before = win._close_btn._press.value
    win._close_btn.mousePressEvent(
        QMouseEvent(QEvent.MouseButtonPress, QPointF(15, 15), Qt.LeftButton,
                    Qt.LeftButton, Qt.NoModifier)
    )
    QTest.qWait(60)
    mid = win._close_btn._press.value
    # Release outside the button so this measures press feedback without
    # also clicking it (which would put the card away mid-audit).
    win._close_btn.mouseReleaseEvent(
        QMouseEvent(QEvent.MouseButtonRelease, QPointF(-40, -40), Qt.LeftButton,
                    Qt.NoButton, Qt.NoModifier)
    )
    report("press spring at rest", round(before, 3))
    report("press spring after pointer-down", round(mid, 3))
    report("press visible before release", mid > 0.2)

    # ── 4. Is motion interruptible? Try to reverse mid-expand ──
    win._toggle_edit_mode()          # start expanding
    QTest.qWait(120)                  # mid-flight
    running = win._motion.running
    width_mid = win.width()
    win._toggle_edit_mode()           # user changes their mind, same gesture
    QTest.qWait(60)
    reversed_immediately = (not win._motion_intent == "expand")
    QTest.qWait(900)
    report("expand still animating when user reverses", running)
    report("width at reversal", width_mid)
    report("reversal accepted mid-flight (returned to reader)",
           reversed_immediately and not win._edit_target)
    report("final width after reversal", win.width())
    report("restored to reader width", abs(win.width() - win.DEFAULT_W) <= 2)

    # ── 5. Escape while editing ──
    win._toggle_edit_mode()
    QTest.qWait(700)
    report("in edit mode before Escape", win._edit_target)
    exited = {"closed": False}
    win.destroyed.connect(lambda: exited.__setitem__("closed", True))
    app.aboutToQuit.connect(lambda: exited.__setitem__("closed", True))
    QTest.keyClick(win._text_edit, Qt.Key_Escape)
    QTest.qWait(700)
    report("Escape in editor quit the app", exited["closed"])
    report("Escape in editor returned to reader", not win._edit_target)

    if exited["closed"]:
        win = ui_overlay.NotchOverlay()
        win._store = Store(tempfile.mkdtemp(prefix="notch-audit-"))
        win._voice_mode = False
        win.show()
        QTest.qWait(400)
        win._load_script_from_file(FIXTURE)
        QTest.qWait(200)

    # ── 6. Idle repaint cost and chrome recede ──
    paints = {"n": 0}
    original_paint = win.paintEvent

    def counting_paint(event):
        paints["n"] += 1
        original_paint(event)

    win.paintEvent = counting_paint
    paints["n"] = 0
    QTest.qWait(1000)
    report("whole-window repaints per second while idle", paints["n"])
    win.paintEvent = original_paint

    win._wake_chrome()
    report("chrome opacity when awake", round(win._chrome_opacity.opacity(), 2))
    win._idle_timer.stop()
    win._recede_chrome()
    QTest.qWait(400)
    report("chrome opacity after idle", round(win._chrome_opacity.opacity(), 2))
    win._wake_chrome()
    QTest.qWait(250)
    report("chrome opacity after pointer returns",
           round(win._chrome_opacity.opacity(), 2))

    # ── 7. Does the minus quit, or put the card away? ──
    report("minus button tooltip", win._close_btn.toolTip())
    win._close_btn.click()
    QTest.qWait(700)
    report("card still alive after minus", bool(win.isVisible()))
    report("peeked width after minus", win.width())
    report("chrome hidden while peeked", not win._chrome.isVisible())
    win._restore_from_peek()
    QTest.qWait(700)
    report("width after restoring from peek", win.width())

    # ── 8. Capture exclusion coverage of every surface ──
    def affinity(widget):
        out = ctypes.c_ulong(0)
        ok = user32.GetWindowDisplayAffinity(int(widget.winId()), ctypes.byref(out))
        return f"0x{out.value:02x}" if ok else "unavailable"

    help_dlg = ui_overlay.HelpDialog(win)
    help_dlg.show()
    settings_dlg = None
    try:
        from settings_dialog import SettingsDialog
        settings_dlg = SettingsDialog(None, 0.01, 2.0, "model", win)
        settings_dlg.show()
    except Exception as exc:  # audio device may be absent in CI
        report("settings dialog", f"could not open: {exc}")
    QTest.qWait(300)
    report("capture affinity overlay (want 0x11)", affinity(win))
    report("capture affinity help dialog", affinity(help_dlg))
    if settings_dlg is not None:
        report("capture affinity settings dialog", affinity(settings_dlg))
    help_dlg.close()
    if settings_dlg is not None:
        settings_dlg.close()

    # ── 9. Reduced motion is read, not assumed ──
    import spring
    enabled = ctypes.c_int(1)
    ok = user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0,
                                     ctypes.byref(enabled), 0)
    report("Windows 'show animations' readable", bool(ok))
    report("Windows 'show animations' enabled", bool(enabled.value))
    report("app reads that setting", spring.system_allows_motion() == bool(enabled.value))
    report("motion clock honours reduced motion", win._motion.reduced_motion ==
           (not spring.system_allows_motion()))

    app.quit()


if __name__ == "__main__":
    main()
