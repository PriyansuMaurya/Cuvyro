"""Behaviour checks for the reading surfaces, run without a visible desktop.

Covers the flows the UI audit calls out: wheel scrolling with a fixed toolbar,
Space-driven timed playback (start, speed, end of script), the persistence
round trip for an edited script, and the note lifecycle (blank notes are
dropped, written notes survive).

Exits non-zero on the first failed expectation.
"""

import os
import sys
import tempfile

from PyQt5.QtCore import QPoint, QPointF, Qt
from PyQt5.QtGui import QWheelEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

FIXTURE = os.path.join(REPO_ROOT, "fixtures", "demo-script.txt")
failures = []


def check(name, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not condition:
        failures.append(name)


def wheel(widget, notches):
    """Send a real wheel event to the script viewport."""
    pos = QPointF(widget.rect().center())
    event = QWheelEvent(
        pos,
        widget.mapToGlobal(widget.rect().center()).toPointF()
        if hasattr(widget.mapToGlobal(widget.rect().center()), "toPointF")
        else QPointF(widget.mapToGlobal(widget.rect().center())),
        QPoint(0, 0),
        QPoint(0, 120 * notches),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(widget, event)


def main():
    app = QApplication(sys.argv)
    import ui_overlay
    from note_window import NoteWindow
    from store import Store

    tmp = tempfile.mkdtemp(prefix="notch-check-")
    win = ui_overlay.NotchOverlay()
    win._store = Store(tmp)
    win._voice_mode = False  # keep the microphone out of an automated run
    win.show()
    QTest.qWait(300)

    long_text = (open(FIXTURE, encoding="utf-8").read() + "\n") * 6
    win._text_edit.setPlainText(long_text)
    win._script_text = long_text
    win._matching.load_script(long_text)
    win._apply_highlight(-1)
    QTest.qWait(200)

    bar = win._text_edit.verticalScrollBar()
    check("long script overflows the viewport", bar.maximum() > 0, f"max={bar.maximum()}")

    toolbar_before = win._close_btn.mapTo(win, QPoint(0, 0))
    settings_before = win._settings_btn.mapTo(win, QPoint(0, 0))

    wheel(win._text_edit.viewport(), -5)
    QTest.qWait(60)
    after_down = bar.value()
    check("wheel down advances the script", after_down > 0, f"scroll={after_down}")

    wheel(win._text_edit.viewport(), 5)
    QTest.qWait(60)
    check("wheel up returns toward the start", bar.value() < after_down, f"scroll={bar.value()}")

    check(
        "toolbar stays fixed while the script moves",
        win._close_btn.mapTo(win, QPoint(0, 0)) == toolbar_before
        and win._settings_btn.mapTo(win, QPoint(0, 0)) == settings_before,
    )

    # Timed playback (Manual mode).
    bar.setValue(0)
    win._manual_running = False
    win._toggle_manual_playback()
    check("space starts timed playback", win._manual_running)
    QTest.qWait(500)
    played = bar.value()
    check("timed playback advances the script", played > 0, f"scroll={played} after 0.5 s")

    win._adjust_speed_dir(1)
    faster = win._manual_wpm
    win._adjust_speed_dir(-1)
    check("speed adjusts both ways", faster > win._manual_wpm, f"{faster} -> {win._manual_wpm}")

    win._toggle_manual_playback()
    check("space pauses timed playback", not win._manual_running)
    paused_at = bar.value()
    QTest.qWait(300)
    check("paused playback holds position", bar.value() == paused_at)

    bar.setValue(bar.maximum() - 1)
    win._manual_running = True
    QTest.qWait(700)
    check("clock stops at the end of the script", not win._manual_running)

    # Persistence round trip: edit, commit, reload in a fresh overlay.
    edited = "Edited script for the persistence check.\nSecond line stays put."
    win._text_edit.setPlainText(edited)
    win._script_text = edited
    win._save_state()
    saved = Store(tmp).load()
    check("edited script is written to the state file", saved["script"] == edited)

    win2 = ui_overlay.NotchOverlay()
    win2._store = Store(tmp)
    win2._voice_mode = False
    win2.show()
    QTest.qWait(300)
    check(
        "a fresh launch restores the edited script",
        win2._text_edit.toPlainText() == edited,
        f"{len(win2._text_edit.toPlainText())} chars",
    )

    # Notes: blank notes are dropped, written notes survive.
    blank = NoteWindow(scale=win2._scale)
    win2._notes.append(blank)
    written = win2._open_note()
    written._edit.setPlainText("Board block reads")
    QTest.qWait(50)
    state = win2._note_state()
    check("blank notes are dropped, written ones are kept", len(state) == 1, f"{len(state)} kept")

    win2._save_state()
    check(
        "written note is stored",
        Store(tmp).load()["notes"][0]["text"] == "Board block reads",
    )

    # Closing a note with its own × destroyed the widget inside the click that
    # closed it, and the rest of that handler then touched a deleted object —
    # an unhandled exception in an event handler, which ends the process. The
    # click has to leave the app running.
    before_close = len(win2._notes)
    closing = win2._open_note()
    closing._edit.setPlainText("closes on click")
    QTest.qWait(50)
    QTest.mouseClick(closing._close, Qt.LeftButton)
    QTest.qWait(200)
    check("a note closes from its own ×", len(win2._notes) == before_close,
          f"{len(win2._notes)} of {before_close} open")
    survivor = win2._open_note()
    QTest.qWait(100)
    check("the reader still opens notes after one was closed",
          survivor.isVisible(), f"{len(win2._notes)} open")

    win2._close_all_notes()
    win._close_all_notes()
    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All behaviour checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
