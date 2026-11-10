"""Sharper measurement of two claims: what Escape does, and what idle costs.

    .venv/Scripts/python.exe packaging/audit_escape.py
"""

import ctypes
import os
import sys
import tempfile
import time

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_ulong),
                ("dwHighDateTime", ctypes.c_ulong)]


kernel32 = ctypes.windll.kernel32


def cpu_seconds():
    creation, exit_, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
    kernel32.GetProcessTimes(kernel32.GetCurrentProcess(),
                             ctypes.byref(creation), ctypes.byref(exit_),
                             ctypes.byref(kernel), ctypes.byref(user))
    def to_int(ft):
        return (ft.dwHighDateTime << 32) | ft.dwLowDateTime
    return (to_int(kernel) + to_int(user)) / 10_000_000.0


def main():
    app = QApplication(sys.argv)
    import ui_overlay
    from store import Store

    win = ui_overlay.NotchOverlay()
    win._store = Store(tempfile.mkdtemp(prefix="notch-audit-"))
    win._voice_mode = False
    win.show()
    win.raise_()
    win.activateWindow()
    QTest.qWait(600)
    win._load_script_from_file(os.path.join(REPO_ROOT, "fixtures", "demo-script.txt"))
    QTest.qWait(300)

    print(f"window active: {win.isActiveWindow()}")

    # ── Escape from the reader ──
    quit_seen = {"n": 0}
    app.aboutToQuit.connect(lambda: quit_seen.__setitem__("n", quit_seen["n"] + 1))
    visible = {"v": True}
    win.destroyed.connect(lambda: visible.__setitem__("v", False))
    QTest.keyClick(win, Qt.Key_Escape)
    QTest.qWait(200)
    print(f"Escape in reader -> app quit: {quit_seen['n'] > 0}, window gone: {not visible['v']}")
    if quit_seen["n"] or not visible["v"]:
        return

    # ── Escape while editing ──
    win._toggle_edit_mode()
    QTest.qWait(900)
    print(f"in edit mode: {win._edit_mode}")
    QTest.keyClick(win._text_edit, Qt.Key_Escape)
    QTest.qWait(200)
    print(f"Escape in editor -> app quit: {quit_seen['n'] > 0}, "
          f"still editing: {win._edit_mode}")

    # ── Idle CPU over two seconds, no speech, no animation ──
    QTest.qWait(1500)
    start = cpu_seconds()
    t0 = time.monotonic()
    QTest.qWait(2000)
    spent = cpu_seconds() - start
    elapsed = time.monotonic() - t0
    print(f"idle CPU: {spent:.3f}s over {elapsed:.2f}s "
          f"= {100 * spent / elapsed:.1f}% of one core")

    app.quit()


if __name__ == "__main__":
    main()
