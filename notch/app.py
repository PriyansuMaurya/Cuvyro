"""
notch.app — Entry point for the Notch teleprompter.

  ┌─────────────────────────────────────────────────────────────┐
  │  NOTCH  —  Local, offline teleprompter for your webcam.     │
  │  Reads your voice, scrolls your script.  No cloud. Ever.   │
  └─────────────────────────────────────────────────────────────┘

Keyboard shortcuts (while the reader is focused):
    Space      — Start / pause timed scroll (Manual mode)
    Ctrl+= / - — Increase / decrease font size
    Ctrl+R     — Reset scroll to beginning
    Ctrl+N     — New note
    Esc        — Back out of the editor or pill, or close the reader

Right-click the card for mode, typeface, theme, Help, Setup and Quit.
"""

import sys

from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtGui import QFont
from PyQt5.QtCore import Qt

from notch.platform import single_instance


def main():
    print("[Notch] Starting...", flush=True)
    # ── High-DPI scaling (Windows 10/11) ─────────────────────────────
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("Notch")
    app.setStyle("Fusion")

    # One reader per session: a second copy would share the state file, the
    # global chords and the camera with the first, and the last save wins.
    if not single_instance.acquire():
        QMessageBox.information(
            None, "Notch",
            "Notch is already running.\n\n"
            "Use the copy that is already open, or quit it from its menu "
            "before starting another.")
        return 0
    app.aboutToQuit.connect(single_instance.release)

    # ── Default font ─────────────────────────────────────────────────
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    # ── Launch the overlay ───────────────────────────────────────────
    from notch.ui.overlay import NotchOverlay

    print("[Notch] Creating overlay...", flush=True)
    overlay = NotchOverlay()
    print("[Notch] Overlay created, showing...", flush=True)
    overlay.show()
    print("[Notch] Running (load a script and speak in Notch mode)", flush=True)

    sys.exit(app.exec_())
