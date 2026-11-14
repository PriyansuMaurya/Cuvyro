"""Sticky notes belong to the script they were written under.

Clearing the main script has to take its notes off the screen and out of the
saved state, and no recovery path may bring them back: not a relaunch, not the
next script, and not the ``state.json.bak`` fallback. Preferences are not
notes and must survive the clear.

Run headless:

    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_note_clearing.py
"""

import json
import os
import sys
import tempfile

from PyQt5 import sip
from PyQt5.QtCore import QSettings
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


def json_at(directory, name):
    try:
        with open(os.path.join(directory, name), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def state_file(directory):
    return json_at(directory, "state.json")


def visible_notes():
    """Every note window currently on screen, whoever owns it."""
    from notch.ui.notes import NoteWindow

    on_screen = []
    for widget in QApplication.topLevelWidgets():
        if not isinstance(widget, NoteWindow) or sip.isdeleted(widget):
            continue
        try:
            if widget.isVisible():
                on_screen.append(widget)
        except RuntimeError:
            continue
    return on_screen


def make_overlay(directory):
    from notch.ui import overlay as ui_overlay
    from notch.storage.store import Store

    win = ui_overlay.NotchOverlay()
    win._store = Store(directory)
    win._voice_mode = False  # keep the microphone out of an automated run
    win.show()
    QTest.qWait(300)
    return win


def set_script(win, text):
    win._text_edit.setPlainText(text)
    win._script_text = text
    win._matching.load_script(text)
    win._save_state()


def clear_script(win):
    """Empty the script the way the reader does: open the editor, delete, Done."""
    win._toggle_edit_mode()
    QTest.qWait(400)
    win._text_edit.setPlainText("")
    win._toggle_edit_mode()
    QTest.qWait(400)


def write_script(directory, name, text):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def main():
    QSettings("Notch", "Notch").setValue("guide_seen", True)
    app = QApplication(sys.argv)
    from notch.storage.store import SCHEMA

    # ── Clearing the script takes its notes with it ───────────────────
    tmp = tempfile.mkdtemp(prefix="notch-clear-")
    win = make_overlay(tmp)
    script = "Morning block.\nShip the note fix, then rehearse."
    set_script(win, script)
    win._dark_theme = False  # a preference, not a note
    win._save_state()

    note = win._open_note()
    note._edit.setPlainText("Board block reads")
    QTest.qWait(60)
    win._save_state()
    check("a written note is on screen and saved",
          len(visible_notes()) == 1
          and state_file(tmp).get("notes")
          and state_file(tmp)["notes"][0].get("text") == "Board block reads",
          f"{len(visible_notes())} on screen")

    clear_script(win)
    check("clearing the script closes its notes",
          not win._notes and not visible_notes(),
          f"{len(win._notes)} in the list, {len(visible_notes())} on screen")

    cleared = state_file(tmp)
    check("clearing the script drops the notes from storage",
          cleared.get("notes") == [], repr(cleared.get("notes")))
    check("clearing the script empties the script itself",
          cleared.get("script") == "" and cleared.get("schema") == SCHEMA,
          f"script={cleared.get('script')!r}")
    check("clearing the script leaves the preferences alone",
          cleared.get("settings", {}).get("dark_theme") is False,
          repr(cleared.get("settings")))

    # ── Relaunch: a cleared script's notes do not come back ───────────
    relaunched = make_overlay(tmp)
    check("a relaunch does not restore notes for a cleared script",
          not relaunched._notes and not visible_notes(),
          f"{len(relaunched._notes)} restored")

    # ── Recovery: the .bak copy is not an undo stack ──────────────────
    # The backup exists to survive a half-written primary, not to undo a
    # deliberate deletion, so both copies must already agree.
    backup = json_at(tmp, "state.json.bak")
    check("the backup holds the cleared state too",
          backup.get("notes") == [] and backup.get("script") == "",
          f"notes={backup.get('notes')!r}")

    primary = os.path.join(tmp, "state.json")
    with open(primary, "w", encoding="utf-8") as handle:
        handle.write('{"schema": 1, "script": "')  # a truncated write
    recovered = make_overlay(tmp)
    check("recovering from the backup does not bring cleared notes back",
          not recovered._notes and not visible_notes(),
          f"{len(recovered._notes)} recovered")

    # ── A new script does not inherit the cleared notes ──────────────
    fresh_path = write_script(tmp, "new-script.txt",
                              "Afternoon block.\nReview the edit pass.")
    check("the new script loads",
          recovered._load_script_from_file(fresh_path)
          and recovered._script_text.startswith("Afternoon block."))
    QTest.qWait(100)
    check("a new script opens with no notes over it",
          not recovered._notes and not visible_notes(),
          f"{len(recovered._notes)} open")
    check("the new script stores no note of its own",
          state_file(tmp).get("notes") == [],
          repr(state_file(tmp).get("notes")))

    # ── Loading a script is not clearing one ─────────────────────────
    # Notes are only discarded when the script they belong to is emptied;
    # swapping the script must not silently destroy what the reader wrote.
    kept = recovered._open_note()
    kept._edit.setPlainText("keep me")
    QTest.qWait(60)
    recovered._save_state()
    other_path = write_script(tmp, "other-script.txt", "Evening block.")
    recovered._load_script_from_file(other_path)
    QTest.qWait(100)
    check("loading another script keeps the notes it did not clear",
          kept in recovered._notes and kept.text() == "keep me",
          f"{len(recovered._notes)} open")
    check("the kept note survives in storage",
          [n.get("text") for n in state_file(tmp).get("notes") or []]
          == ["keep me"],
          repr(state_file(tmp).get("notes")))

    # And the kept note is still cleared by the clear it does belong to.
    clear_script(recovered)
    check("clearing after a script swap still drops the note",
          not recovered._notes and not visible_notes()
          and state_file(tmp).get("notes") == [],
          f"{len(recovered._notes)} open")

    # ── An empty file is a clear, not a new script ────────────────────
    # Dropping a blank .txt is the other way the reader ends up with no
    # script; it must leave no notes behind either.
    emptied = recovered._open_note()
    emptied._edit.setPlainText("gets cleared by an empty file")
    QTest.qWait(60)
    recovered._save_state()
    empty_path = write_script(tmp, "empty-script.txt", "")
    check("an empty file loads", recovered._load_script_from_file(empty_path))
    QTest.qWait(100)
    check("loading an empty script clears its notes",
          not recovered._notes and not visible_notes()
          and state_file(tmp).get("notes") == [],
          f"{len(recovered._notes)} open")
    check("the backup of the empty script holds no notes either",
          json_at(tmp, "state.json.bak").get("notes") == [],
          repr(json_at(tmp, "state.json.bak").get("notes")))

    for window in (win, relaunched, recovered):
        window._close_all_notes()
        window.close()

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All note-clearing checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
