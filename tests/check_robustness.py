"""Robustness checks for persistence, window placement, import and access.

These cover the surfaces the design and reading checks do not: what happens
when a note is typed into, when the menu is opened repeatedly, when a saved
window belongs to a monitor that is gone, when a script is not UTF-8, and
whether every custom-drawn control can be reached without a mouse.

Run headless:

    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_robustness.py
"""

import io
import json
import os
import sys
import tempfile
import time
from unittest.mock import patch

from PyQt5.QtCore import QObject, QPoint, QRect, QSettings, QSize, Qt
from PyQt5.QtGui import QTextCursor
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLabel, QShortcut, QWidgetAction

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "demo-script.txt")
failures = []


def check(name, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not condition:
        failures.append(name)


def state_file(directory):
    path = os.path.join(directory, "state.json")
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def relative_luminance(rgb):
    def channel(value):
        value = value / 255.0
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(v) for v in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    high, low = sorted((relative_luminance(fg), relative_luminance(bg)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def hex_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


def settle_closing(window, note, label=""):
    """Finish a note's exit animation.

    The corner and Escape now leave through the note's dismissal, which fades
    and shrinks the card before closing it, so the window is still listed for
    a moment after the key. Stepping the spring directly keeps the check
    deterministic instead of guessing at a wait.
    """
    for _ in range(240):
        if note not in window._notes:
            return
        note._presence.step(1 / 60)
        note._apply_presence()
    raise AssertionError(f"{note.note_id} never finished closing ({label})")


def saved_note(directory, index=0):
    notes = state_file(directory).get("notes") or []
    if index >= len(notes) or not isinstance(notes[index], dict):
        return ""
    return str(notes[index].get("text") or "")


def make_overlay(directory):
    from notch.ui import overlay as ui_overlay
    from notch.storage.store import Store

    win = ui_overlay.NotchOverlay()
    win._store = Store(directory)
    win._voice_mode = False  # keep the microphone out of an automated run
    win.show()
    QTest.qWait(300)
    return win


def on_screen(rect):
    return any(screen.availableGeometry().contains(rect)
               for screen in QApplication.screens())


def main():
    QSettings("Notch", "Notch").setValue("guide_seen", True)
    app = QApplication(sys.argv)
    from notch.ui import overlay as ui_overlay
    from notch.ui import settings as settings_dialog
    from notch.storage.store import SCHEMA, Store

    # ── Persistence is debounced, and every close path flushes ────────
    tmp = tempfile.mkdtemp(prefix="notch-robust-save-")
    win = make_overlay(tmp)
    text = open(FIXTURE, encoding="utf-8").read() * 20
    win._text_edit.setPlainText(text)
    win._script_text = text
    win._matching.load_script(text)
    win._refresh_lines()
    win._save_state()

    note = win._open_note()
    started = time.perf_counter()
    for _ in range(20):
        note._edit.insertPlainText("x")
    keystroke_ms = (time.perf_counter() - started) * 1000.0
    check("typing in a note does not wait on the disk",
          keystroke_ms < 200.0, f"20 keystrokes in {keystroke_ms:.1f} ms")
    check("typing schedules one deferred write instead of one per key",
          win._save_timer.isActive())
    check("the state file is not rewritten mid-word",
          saved_note(tmp) != "x" * 20)
    QTest.qWait(750)
    check("the deferred write lands after the pause",
          saved_note(tmp) == "x" * 20,
          "note text persisted")

    # Closing the app must not lose the last <=400 ms of typing.
    tmp_close = tempfile.mkdtemp(prefix="notch-robust-close-")
    closing = make_overlay(tmp_close)
    closing_note = closing._open_note()
    closing_note._edit.setPlainText("flushed on close")
    QTest.qWait(30)
    with patch.object(QApplication, "quit", lambda *_: None):
        closing.close()
    check("closing the app flushes a pending note edit",
          saved_note(tmp_close) == "flushed on close")

    # A shutdown is a separate exit path (logoff, power off). It arrives as
    # aboutToQuit, after which the notes list is cleared; saving that empty
    # list would erase the state the close itself just wrote.
    tmp_shutdown = tempfile.mkdtemp(prefix="notch-robust-shutdown-")
    shutting = make_overlay(tmp_shutdown)
    shutdown_note = shutting._open_note()
    shutdown_note._edit.setPlainText("survives shutdown")
    QTest.qWait(30)
    app.aboutToQuit.emit()
    check("an OS shutdown flushes a pending note edit",
          saved_note(tmp_shutdown) == "survives shutdown",
          repr(saved_note(tmp_shutdown)))
    with patch.object(QApplication, "quit", lambda *_: None):
        shutting.close()
    app.aboutToQuit.emit()
    check("a shutdown after close cannot erase saved notes",
          saved_note(tmp_shutdown) == "survives shutdown",
          repr(saved_note(tmp_shutdown)))

    # ── Menus are released, not accumulated ───────────────────────────
    menu_win = make_overlay(tempfile.mkdtemp(prefix="notch-robust-menu-"))
    menu_win._present_menu = lambda menu, pos: menu.close()  # no blocking exec_
    before = len(menu_win.findChildren(QObject))
    for _ in range(6):
        menu_win._show_menu(QPoint(10, 10))
    QTest.qWait(300)
    after = len(menu_win.findChildren(QObject))
    check("context menus are released after closing",
          after <= before + 2, f"{before} -> {after} QObjects after 6 menus")

    # ── Restored windows are clamped to the screens that exist ────────
    tmp_geom = tempfile.mkdtemp(prefix="notch-robust-geom-")
    Store(tmp_geom).save("", [{"id": "offscreen", "text": "kept",
                               "x": 99999, "y": 99999}],
                         {"window_x": 99999, "window_y": 99999})
    restored = make_overlay(tmp_geom)
    screen = QApplication.primaryScreen().availableGeometry()
    check("a reader saved off-screen restores on a real screen",
          on_screen(QRect(restored.pos(), restored.size())),
          f"at {restored.x()},{restored.y()} on {screen.width()}x{screen.height()}")
    restored_note = restored._notes[0] if restored._notes else None
    check("a note saved off-screen restores on a real screen",
          restored_note is not None
          and on_screen(QRect(restored_note.pos(), restored_note.size())),
          f"at {restored_note.x()},{restored_note.y()}" if restored_note else "missing")
    if restored_note is not None:
        check("the off-screen note keeps its text", restored_note.text() == "kept")

    # A monitor disappearing while the app is running is the other half of
    # the same problem: the saved geometry was valid at launch, then the
    # display it lived on went away.
    class FakeScreen:
        def __init__(self, rect):
            self._rect = rect

        def availableGeometry(self):
            return self._rect

    tmp_display = tempfile.mkdtemp(prefix="notch-robust-display-")
    display = make_overlay(tmp_display)
    display.move(6000, 6000)
    QTest.qWait(50)
    small_screen = QRect(0, 0, 640, 400)
    with patch.object(QApplication, "screens",
                      return_value=[FakeScreen(small_screen)]):
        display._on_screens_changed()
        QTest.qWait(80)
        check("a reader stranded by a display change is pulled back",
              small_screen.contains(QRect(display.pos(), display.size())),
              f"at {display.x()},{display.y()} "
              f"size {display.width()}x{display.height()}")
        stray_note = display._open_note()
        stray_note.move(6000, 6000)
        display._on_screens_changed()
        check("a note stranded by a display change is pulled back",
              small_screen.contains(QRect(stray_note.pos(), stray_note.size())),
              f"at {stray_note.x()},{stray_note.y()}")
    with patch.object(QApplication, "quit", lambda *_: None):
        display.close()

    # A note opened while the reader sits against the right edge stays whole.
    restored.move(screen.right() - 60, screen.y())
    QTest.qWait(80)
    edge_note = restored._open_note()
    # Let it finish emerging from the reader, so the check measures the
    # resting corner rather than the frame it is travelling through.
    QTest.qWait(500)
    check("a new note does not open past the screen edge",
          on_screen(QRect(edge_note.pos(), edge_note.size())),
          f"at {edge_note.x()},{edge_note.y()}")

    # A valid saved position is restored rather than re-centred.
    tmp_pos = tempfile.mkdtemp(prefix="notch-robust-pos-")
    wanted = QRect(screen.x() + 40, screen.y() + 30,
                   win.DEFAULT_W, win.DEFAULT_H)
    Store(tmp_pos).save("", [], {"window_x": wanted.x(), "window_y": wanted.y()})
    positioned = make_overlay(tmp_pos)
    check("a valid reader position survives a relaunch",
          positioned.pos() == wanted.topLeft(),
          f"{positioned.x()},{positioned.y()} vs {wanted.x()},{wanted.y()}")

    # Sticky notes own their position, not just their text.
    tmp_note_pos = tempfile.mkdtemp(prefix="notch-robust-note-pos-")
    note_wanted = QPoint(screen.x() + 120, screen.y() + 80)
    Store(tmp_note_pos).save("", [{"id": "placed", "text": "placed note",
                                   "x": note_wanted.x(), "y": note_wanted.y()}], {})
    note_placed = make_overlay(tmp_note_pos)
    check("a sticky note position survives a relaunch",
          note_placed._notes and note_placed._notes[0].pos() == note_wanted,
          str(note_placed._notes[0].pos()) if note_placed._notes else "missing")
    with patch.object(QApplication, "quit", lambda *_: None):
        note_placed.close()

    # ── Font, mode and setup preferences survive a relaunch ──────────
    tmp_prefs = tempfile.mkdtemp(prefix="notch-robust-prefs-")
    prefs = make_overlay(tmp_prefs)
    prefs._save_timer.stop()
    prefs._font_size = 16
    prefs._font_increase()
    check("changing the font schedules a save", prefs._save_timer.isActive())
    prefs._font_size = 18
    prefs._font_family = "Consolas"
    prefs._dark_theme = False
    prefs._voice_mode = False
    prefs._manual_wpm = 170
    prefs._voice_wpm = 75
    prefs._device_index = 3
    prefs._noise_gate = 0.07
    prefs._input_gain = 3.25
    prefs._save_state()
    with patch.object(QApplication, "quit", lambda *_: None):
        prefs.close()
    restarted = ui_overlay.NotchOverlay()
    restarted._store = Store(tmp_prefs)
    restarted._start_listening = lambda: None
    restarted.show()
    QTest.qWait(300)
    check("font family and size survive a relaunch",
          restarted._font_family == "Consolas" and restarted._font_size == 18,
          f"{restarted._font_family} {restarted._font_size}")
    check("theme, mode and reading speeds survive a relaunch",
          restarted._dark_theme is False and restarted._voice_mode is False
          and restarted._manual_wpm == 170 and restarted._voice_wpm == 75,
          f"dark {restarted._dark_theme}, voice {restarted._voice_mode}, "
          f"manual {restarted._manual_wpm}, voice {restarted._voice_wpm}")
    check("microphone setup survives a relaunch",
          restarted._device_index == 3
          and abs(restarted._noise_gate - 0.07) < 1e-9
          and abs(restarted._input_gain - 3.25) < 1e-9,
          f"device {restarted._device_index}, gate {restarted._noise_gate}, "
          f"gain {restarted._input_gain}")
    with patch.object(QApplication, "quit", lambda *_: None):
        restarted.close()

    # ── A saved size is restored, not just the position ───────────────
    tmp_size = tempfile.mkdtemp(prefix="notch-robust-size-")
    wanted_size = QSize(win.DEFAULT_W + 40, win.DEFAULT_H + 60)
    Store(tmp_size).save("", [], {"window_x": screen.x() + 25,
                                   "window_y": screen.y() + 15,
                                   "window_w": wanted_size.width(),
                                   "window_h": wanted_size.height()})
    sized = make_overlay(tmp_size)
    check("a saved window size survives a relaunch",
          sized.size() == wanted_size,
          f"{sized.width()}x{sized.height()} vs "
          f"{wanted_size.width()}x{wanted_size.height()}")
    check("the reader collapses back to the restored size",
          sized._collapsed_size == wanted_size, str(sized._collapsed_size))
    with patch.object(QApplication, "quit", lambda *_: None):
        sized.close()

    # A size saved on a bigger display cannot put the card off the screen.
    tmp_oversize = tempfile.mkdtemp(prefix="notch-robust-oversize-")
    Store(tmp_oversize).save("", [], {"window_x": 0, "window_y": 0,
                                       "window_w": int(860 * win._scale)
                                       + win.RAIL_W,
                                       "window_h": int(320 * win._scale)})
    oversized = make_overlay(tmp_oversize)
    check("a window saved larger than the screen is shrunk to fit",
          on_screen(QRect(oversized.pos(), oversized.size())),
          f"{oversized.width()}x{oversized.height()} at "
          f"{oversized.x()},{oversized.y()}")
    with patch.object(QApplication, "quit", lambda *_: None):
        oversized.close()

    # A size no reader state could have produced is corrupt, not a resize.
    tmp_absurd_size = tempfile.mkdtemp(prefix="notch-robust-absurd-size-")
    Store(tmp_absurd_size).save("", [], {"window_x": 10, "window_y": 10,
                                          "window_w": 999999,
                                          "window_h": 999999})
    absurd = make_overlay(tmp_absurd_size)
    check("a corrupt size restores as the default card, not a full screen",
          absurd.size() == QSize(absurd.DEFAULT_W, absurd.DEFAULT_H),
          f"{absurd.width()}x{absurd.height()} vs "
          f"{absurd.DEFAULT_W}x{absurd.DEFAULT_H}")
    with patch.object(QApplication, "quit", lambda *_: None):
        absurd.close()

    # ── Invalid saved values are rejected, never trusted ──────────────
    # Everything here is what a hand-edited or half-written file can hold:
    # a list where a string belongs, coordinates past QRect's 32-bit range
    # (which raises OverflowError, not ValueError), booleans, numeric
    # strings, NaN and infinity, and sizes below the minimum.
    tmp_bad = tempfile.mkdtemp(prefix="notch-robust-bad-")
    with open(os.path.join(tmp_bad, "state.json"), "w", encoding="utf-8") as handle:
        json.dump({
            "schema": 1,
            "script": ["not", "a", "script"],
            "notes": [
                {"id": "absurd", "text": "kept", "x": 1e30, "y": 1e30},
                {"id": "bools", "text": "kept too", "x": True, "y": False},
                {"id": "nan", "text": "kept as well", "x": float("nan"), "y": 0},
                {"id": "strings", "text": "and this", "x": "40", "y": "40"},
                {"id": "not-text", "text": {"a": 1}, "x": 5, "y": 5},
            ],
            "settings": {
                "device_index": -4,
                "noise_gate": "loud",
                "input_gain": float("nan"),
                "font_family": "Comic Sans",
                "font_size": 1e30,
                "dark_theme": "yes",
                "manual_wpm": "fast",
                "voice_wpm": float("inf"),
                "voice_mode": 3,
                "window_x": 1e30,
                "window_y": 1e30,
                "window_w": -5,
                "window_h": True,
            },
        }, handle)
    bad = make_overlay(tmp_bad)
    check("a corrupt script field is dropped instead of rendered",
          bad._script_text == "" and bad._text_edit.toPlainText() == "",
          repr(bad._text_edit.toPlainText()[:40]))
    check("an unknown font family falls back to the default",
          bad._font_family == "Segoe UI", bad._font_family)
    check("an absurd font size falls back into the readable range",
          6 <= bad._font_size <= 24, str(bad._font_size))
    check("a non-boolean theme flag is ignored", bad._dark_theme is True)
    check("text speeds that are strings or infinite are ignored",
          bad._manual_wpm == 150 and bad._voice_wpm == 60,
          f"manual {bad._manual_wpm}, voice {bad._voice_wpm}")
    check("a corrupt position leaves the reader on a real screen",
          on_screen(QRect(bad.pos(), bad.size())),
          f"at {bad.x()},{bad.y()}")
    check("notes with unusable coordinates still come back readable",
          len(bad._notes) == 4
          and all(note.text() for note in bad._notes)
          and all(on_screen(QRect(note.pos(), note.size()))
                  for note in bad._notes),
          f"{len(bad._notes)} note(s)")
    check("a note whose text is corrupt is dropped, not stringified",
          all(note.text() != "{'a': 1}" for note in bad._notes),
          str([note.text() for note in bad._notes]))
    check("corrupt microphone preferences fall back to defaults",
          bad._noise_gate == 0.01 and bad._input_gain == 2.0
          and bad._device_index is None,
          f"device {bad._device_index}, gate {bad._noise_gate}, "
          f"gain {bad._input_gain}")
    with patch.object(QApplication, "quit", lambda *_: None):
        bad.close()

    # ── Recovery: a truncated file falls back to the last good copy ───
    tmp_bak = tempfile.mkdtemp(prefix="notch-robust-bak-")
    Store(tmp_bak).save("recovered script",
                        [{"id": "n1", "text": "recovered note",
                          "x": 10, "y": 10}], {})
    # The second save moves the first file to .bak before replacing it.
    Store(tmp_bak).save("newer script", [], {})
    with open(os.path.join(tmp_bak, "state.json"), "w", encoding="utf-8") as handle:
        handle.write('{"schema": 1, "script": ')
    recovered = Store(tmp_bak).load()
    check("a truncated state file falls back to the backup",
          recovered.get("script") == "recovered script",
          repr(recovered.get("script")))
    check("the backup keeps the notes it held",
          [n.get("text") for n in recovered.get("notes") or []]
          == ["recovered note"])

    # Launching against a corrupt primary and then saving must not promote
    # that corrupt file over the last good backup: the next crash would then
    # have nothing valid to recover from.
    tmp_guard = tempfile.mkdtemp(prefix="notch-robust-bak-guard-")
    Store(tmp_guard).save("first good", [], {})
    Store(tmp_guard).save("second good", [], {})
    with open(os.path.join(tmp_guard, "state.json"), "w", encoding="utf-8") as handle:
        handle.write("{broken")
    Store(tmp_guard).save("rebuilt from the backup", [], {})
    with open(os.path.join(tmp_guard, "state.json"), "w", encoding="utf-8") as handle:
        handle.write("{broken again")
    guarded = Store(tmp_guard).load()
    check("a corrupt primary is not promoted over the last good backup",
          guarded.get("script") == "first good",
          repr(guarded.get("script")))

    fresh = Store(tempfile.mkdtemp(prefix="notch-robust-fresh-")).load()
    check("a first launch loads empty state",
          fresh.get("schema") == SCHEMA and fresh.get("script") == ""
          and fresh.get("notes") == [])
    fresh["notes"].append({"id": "leak"})
    check("a failed load cannot pollute the shared defaults",
          Store(tempfile.mkdtemp(prefix="notch-robust-fresh-")).load().get("notes") == [],
          "defaults were mutated by a caller")

    # ── Showing the window again keeps the live state ────────────────
    tmp_live = tempfile.mkdtemp(prefix="notch-robust-live-")
    Store(tmp_live).save("", [], {"font_size": 10, "font_family": "Segoe UI"})
    live = make_overlay(tmp_live)
    live._font_size = 19
    live._font_family = "Consolas"
    live.hide()
    live.show()
    QTest.qWait(150)
    check("re-showing the window keeps the live preferences",
          live._font_size == 19 and live._font_family == "Consolas",
          f"{live._font_family} {live._font_size}")
    with patch.object(QApplication, "quit", lambda *_: None):
        live.close()

    # ── Import: encodings, failures, and honesty ─────────────────────
    import_win = make_overlay(tempfile.mkdtemp(prefix="notch-robust-import-"))
    cp1252 = os.path.join(tempfile.mkdtemp(prefix="notch-script-"), "windows.txt")
    with open(cp1252, "wb") as handle:
        handle.write("Café — déjà vu".encode("cp1252"))
    check("a Windows-encoded script decodes losslessly",
          import_win._decode_script(open(cp1252, "rb").read()) == "Café — déjà vu")
    with open(cp1252, "rb") as handle:
        bom_text = import_win._decode_script(b"\xef\xbb\xbf" + b"Hello")
    check("a UTF-8 BOM is stripped", bom_text == "Hello", repr(bom_text))

    loaded = import_win._load_script_from_file(cp1252)
    check("a cp1252 script actually loads",
          loaded and import_win._text_edit.toPlainText() == "Café — déjà vu")

    reported = []
    import_win._report_import_error = lambda path, error: reported.append(
        (path, str(error)))
    missing = os.path.join(tempfile.mkdtemp(prefix="notch-missing-"), "gone.txt")
    loaded = import_win._load_script_from_file(missing)
    check("a missing script reports an error instead of failing silently",
          loaded is False and reported and reported[0][0] == missing)

    # ── Shading is incremental, and still correct when it goes back ──
    shade_win = make_overlay(tempfile.mkdtemp(prefix="notch-robust-shade-"))
    shade_text = (open(FIXTURE, encoding="utf-8").read() + "\n") * 5
    shade_win._text_edit.setPlainText(shade_text)
    shade_win._script_text = shade_text
    shade_win._matching.load_script(shade_text)
    shade_win._highlight_key = None
    shade_win._apply_highlight(-1)
    shade_win._refresh_lines()

    def shade_at(word):
        start, _ = shade_win._matching.word_span(word)
        cursor = QTextCursor(shade_win._text_edit.document())
        cursor.setPosition(start)
        cursor.setPosition(start + 1, QTextCursor.KeepAnchor)
        colour = cursor.charFormat().foreground().color()
        return (colour.red(), colour.green(), colour.blue())

    def band_at(word):
        start, _ = shade_win._matching.word_span(word)
        cursor = QTextCursor(shade_win._text_edit.document())
        cursor.setPosition(start)
        cursor.setPosition(start + 1, QTextCursor.KeepAnchor)
        return cursor.charFormat().background().style() != Qt.NoBrush

    spoken = (0x8A, 0x8A, 0x8A)
    base = (0xFA, 0xFA, 0xFA)
    current = tuple(int(shade_win.CURRENT_DARK.lstrip("#")[index:index + 2], 16)
                    for index in (0, 2, 4))

    def settle_shade():
        """Finish the tier crossfade, so the assertion reads the resting ink."""
        shade_win._advance_tier_fades(time.monotonic() + 1.0)

    shade_win._apply_highlight(10)
    settle_shade()
    check("speech shading marks the spoken run",
          shade_at(4) == spoken and shade_at(40) == base,
          f"word 4 {shade_at(4)}, word 40 {shade_at(40)}")
    check("the words being spoken are the accent tier on one band",
          shade_at(10) == current and band_at(9) and band_at(10)
          and not band_at(4),
          f"word 10 {shade_at(10)}, banded={band_at(10)}")
    shade_win._apply_highlight(5)
    settle_shade()
    check("stepping back un-shades the words the reader returned to",
          shade_at(10) == base and shade_at(2) == spoken,
          f"word 10 {shade_at(10)}, word 2 {shade_at(2)}")
    check("the band follows the reader back",
          band_at(5) and not band_at(10))
    shade_win._apply_highlight(30)
    settle_shade()
    check("stepping forward re-shades correctly after a backward jump",
          shade_at(10) == spoken and shade_at(40) == base,
          f"word 10 {shade_at(10)}, word 40 {shade_at(40)}")

    shade_win._highlight_key = None
    shade_win._apply_highlight(0)
    started = time.perf_counter()
    for index in range(1, 200):
        shade_win._apply_highlight(index)
    shade_ms = (time.perf_counter() - started) * 1000.0
    check("speech shading does not re-format the whole script per word",
          shade_ms < 800.0,
          f"199 advances in {shade_ms:.0f} ms ({shade_ms / 199:.2f} ms each)")
    with patch.object(QApplication, "quit", lambda *_: None):
        shade_win.close()

    # ── Documented shortcuts and help match the app ──────────────────
    registered = {shortcut.key().toString()
                  for shortcut in import_win.findChildren(QShortcut)}
    check("Ctrl+= is registered for a larger script", "Ctrl+=" in registered,
          str(sorted(registered)))
    check("Ctrl+- is registered for a smaller script", "Ctrl+-" in registered)
    before_size = import_win._font_size
    import_win._sc_bigger.activated.emit()
    bigger = import_win._font_size
    import_win._sc_smaller.activated.emit()
    check("the size shortcuts change the script size",
          bigger > before_size and import_win._font_size == before_size,
          f"{before_size} -> {bigger} -> {import_win._font_size}")

    help_dialog = ui_overlay.HelpDialog(import_win)
    help_text = " ".join(label.text() for label in help_dialog.findChildren(QLabel))
    check("help points at the menu that exists",
          "right-click" in help_text.lower())
    check("help no longer invents a gear control",
          "gear" not in help_text.lower())
    help_dialog.deleteLater()

    # ── Reader keys stay out of the editor ──────────────────────────
    # Space and the arrow keys used to be application shortcuts, so a space
    # typed in the editor was consumed as "start playback" and the caret
    # never moved. Both must behave like text input while editing.
    import_win._voice_mode = False
    import_win._toggle_edit_mode()
    QTest.qWait(900)
    editor = import_win._text_edit
    editor.setPlainText("ab")
    editor.moveCursor(QTextCursor.End)
    editor.setFocus()
    QTest.keyClick(editor, Qt.Key_Space)
    QTest.keyClick(editor, Qt.Key_Left)
    QTest.qWait(50)
    check("Space types a space in the editor",
          editor.toPlainText() == "ab ", repr(editor.toPlainText()))
    check("arrow keys move the caret in the editor, not the speed",
          editor.textCursor().position() == 2 and not import_win._manual_running,
          f"cursor {editor.textCursor().position()}, running {import_win._manual_running}")
    import_win._toggle_edit_mode()
    QTest.qWait(900)

    # ── Accessibility: custom controls have names, notes have a keyboard ──
    for label, button in (
        ("minimize", import_win._close_btn),
        ("new note", import_win._note_btn),
        ("microphone", import_win._mic_btn),
        ("reader width", import_win._compact_btn),
    ):
        check(f"the {label} control has an accessible name",
              bool(button.accessibleName()), button.accessibleName())

    menu = import_win._build_menu()
    check("both mode controls have accessible names",
          bool(menu._voice_btn.accessibleName())
          and bool(menu._manual_btn.accessibleName()))
    check("the speed slider has an accessible name",
          bool(menu._speed_slider.accessibleName()))
    menu.deleteLater()

    # ── Every row answers the keyboard ─────────────────────────────────
    # Every row is a QWidgetAction, and that is exactly where a menu's own
    # navigation stops: Qt highlights the row, then Return closes the whole
    # panel having run nothing. Each row therefore carries its own Left,
    # Right and Return, and these checks pin that down row by row.
    menu_hits = []
    keep = (import_win._show_help, import_win._open_settings, import_win.close,
            import_win._start_listening, import_win._stop_listening,
            import_win._load_file)
    import_win._show_help = lambda: menu_hits.append("help")
    import_win._open_settings = lambda: menu_hits.append("setup")
    import_win.close = lambda *args, **kwargs: menu_hits.append("quit")
    # The microphone stays out of an automated run; the mode itself is real.
    import_win._start_listening = lambda: None
    import_win._stop_listening = lambda: None
    import_win._load_file = lambda: menu_hits.append("load")

    def probe_menu():
        built = import_win._build_menu()
        built.popup(import_win.mapToGlobal(QPoint(0, 0)))
        built.adjustSize()
        QTest.qWait(120)
        return built

    def arrow_onto(built, row_action):
        """Walk down until the row is the menu's active action."""
        for _ in range(len(built.actions()) + 1):
            if built.activeAction() is row_action:
                return True
            QTest.keyClick(built, Qt.Key_Down)
            QTest.qWait(25)
        return False

    # One stop for the mode and its status line: the status is text, and a
    # row that cannot be pressed should not take a keyboard stop of its own.
    peek = probe_menu()
    first_row = peek.actions()[0]
    check("the mode and its status line are one keyboard stop",
          isinstance(first_row, QWidgetAction)
          and peek._status_label in first_row.defaultWidget().findChildren(QLabel),
          f"{len(peek.actions())} rows")
    peek.close()
    QTest.qWait(40)

    # Each row: Return runs it, or keeps the panel open - never a silent exit.
    silent = []
    snapshot = (import_win._voice_mode, import_win._dark_theme,
                import_win._font_size, import_win._font_family,
                import_win._manual_wpm)
    row_count = len(probe_menu().actions())
    for index in range(row_count):
        built = probe_menu()
        target = built.actions()[index]
        if not arrow_onto(built, target):
            built.close()
            QTest.qWait(30)
            continue
        before = (import_win._voice_mode, import_win._dark_theme,
                  import_win._font_size, import_win._font_family,
                  import_win._manual_wpm, tuple(menu_hits))
        QTest.keyClick(built, Qt.Key_Return)
        QTest.qWait(70)
        after = (import_win._voice_mode, import_win._dark_theme,
                 import_win._font_size, import_win._font_family,
                 import_win._manual_wpm, tuple(menu_hits))
        if not built.isVisible() and before == after:
            silent.append(target.text() or target.defaultWidget().__class__.__name__)
        built.close()
        QTest.qWait(30)
        (import_win._voice_mode, import_win._dark_theme, import_win._font_size,
         import_win._font_family, import_win._manual_wpm) = snapshot
        QTest.qWait(20)
    check("Return never closes the menu without running its row",
          not silent, f"silent: {silent}")

    # ── Rows that change a setting ────────────────────────────────────
    built = probe_menu()
    mode_row = built.actions()[0]
    arrow_onto(built, mode_row)
    nav = built._rows
    check("a row picks its current state on arrival",
          nav.active_row() is mode_row and nav.picked() == 1,
          f"picked {nav.picked()}")
    QTest.keyClick(built, Qt.Key_Right)
    QTest.qWait(30)
    QTest.keyClick(built, Qt.Key_Return)
    QTest.qWait(120)
    check("Right and Return switch the mode from the keyboard",
          import_win._voice_mode and built.isVisible()
          and built._status_label.text() == "Following your voice",
          f"voice {import_win._voice_mode}, "
          f"status {built._status_label.text()!r}")
    built.close()
    QTest.qWait(40)
    import_win._voice_mode = False
    import_win._toggle_mode(False)

    built = probe_menu()
    speed_row = built._speed_action
    arrow_onto(built, speed_row)
    speed_before = import_win._manual_wpm
    QTest.keyClick(built, Qt.Key_Left)
    QTest.qWait(40)
    check("Left moves a row's slider by ten words a minute",
          import_win._manual_wpm == speed_before - 10
          and built._speed_value.text() == str(import_win._manual_wpm),
          f"{speed_before} -> {import_win._manual_wpm}, "
          f"label {built._speed_value.text()!r}")
    QTest.keyClick(built, Qt.Key_Right)
    QTest.qWait(40)
    check("Right moves it back", import_win._manual_wpm == speed_before,
          f"{import_win._manual_wpm}")
    built.close()
    QTest.qWait(40)

    built = probe_menu()
    arrow_onto(built, built.actions()[3])          # font size
    size_before = import_win._font_size
    QTest.keyClick(built, Qt.Key_Return)
    QTest.qWait(80)
    check("Return on the size row steps the script down",
          import_win._font_size < size_before,
          f"{size_before} -> {import_win._font_size}")
    QTest.keyClick(built, Qt.Key_Right)
    QTest.qWait(30)
    QTest.keyClick(built, Qt.Key_Return)
    QTest.qWait(80)
    check("and Right then Return steps it back up",
          import_win._font_size == size_before,
          f"{import_win._font_size}")
    built.close()
    QTest.qWait(40)

    built = probe_menu()
    arrow_onto(built, built.actions()[4])          # typeface
    family_before = import_win._font_family
    QTest.keyClick(built, Qt.Key_Return)
    QTest.qWait(80)
    check("Return on the typeface row tries the next face",
          import_win._font_family != family_before,
          f"{family_before} -> {import_win._font_family}")
    import_win._font_family = family_before
    import_win._apply_typography()
    built.close()
    QTest.qWait(40)

    built = probe_menu()
    arrow_onto(built, built.actions()[5])          # appearance
    theme_before = import_win._dark_theme
    QTest.keyClick(built, Qt.Key_Right)
    QTest.qWait(30)
    QTest.keyClick(built, Qt.Key_Return)
    QTest.qWait(100)
    check("Right and Return switch the theme and close the menu",
          import_win._dark_theme != theme_before and not built.isVisible(),
          f"dark {import_win._dark_theme}, open {built.isVisible()}")
    import_win._dark_theme = theme_before
    import_win._apply_read_mode_style()
    QTest.qWait(40)

    # ── The action row: plates, order, Home and End ───────────────────
    menu_hits.clear()
    built = probe_menu()
    QTest.keyClick(built, Qt.Key_End)
    QTest.qWait(40)
    check("End reaches the action row",
          built._rows.active_row() is built.actions()[-1],
          f"{built._rows.active_row() is built.actions()[-1]}")
    nav = built._rows
    check("the row picks its first plate", nav.picked() == 0,
          f"picked {nav.picked()}")
    QTest.keyClick(built, Qt.Key_Left)
    QTest.qWait(30)
    check("Left wraps to the last plate", nav.picked() == 2,
          f"picked {nav.picked()}")
    QTest.keyClick(built, Qt.Key_Return)
    QTest.qWait(90)
    check("Return presses the picked plate and closes the menu",
          menu_hits == ["quit"] and not built.isVisible(),
          f"{menu_hits}, open {built.isVisible()}")
    built.close()
    QTest.qWait(40)
    menu_hits.clear()

    built = probe_menu()
    QTest.keyClick(built, Qt.Key_Home)
    QTest.qWait(40)
    check("Home returns to the first row",
          built._rows.active_row() is built.actions()[0],
          f"{built._rows.active_row() is built.actions()[0]}")
    built.close()
    QTest.qWait(40)

    (import_win._show_help, import_win._open_settings, import_win.close,
     import_win._start_listening, import_win._stop_listening,
     import_win._load_file) = keep

    note_window = import_win._open_note()
    check("the note close control has an accessible name",
          bool(note_window._close.accessibleName()))
    # Space on the focused glyph and Escape from the note both close it.
    # Activate the note explicitly: an offscreen desktop does not always hand
    # focus to a new Tool window, and an inactive window would route Space to
    # the reader's shortcut map instead of the glyph.
    note_window.activateWindow()
    note_window._close.setFocus()
    QTest.qWait(100)
    first_id = note_window.note_id
    QTest.keyClick(note_window._close, Qt.Key_Space)
    settle_closing(import_win, note_window, "space on the corner")
    check("a note closes from the keyboard",
          first_id not in [note.note_id for note in import_win._notes])

    second = import_win._open_note()
    second_id = second.note_id
    second._escape.activated.emit()
    settle_closing(import_win, second, "escape")
    check("Escape closes a note",
          second_id not in [note.note_id for note in import_win._notes])

    with patch.object(settings_dialog.sd, "query_devices", return_value=[]):
        with patch.object(settings_dialog.SettingsDialog, "_start_vu_stream",
                          lambda *_: None):
            settings = settings_dialog.SettingsDialog(None, 0.01, 2.0, "model")
    check("settings controls have accessible names",
          all(bool(widget.accessibleName()) for widget in
              (settings._device_combo, settings._gate_slider,
               settings._gain_slider, settings._vu_bar)))
    settings._vu_start_timer.stop()  # never shown: no meter to start
    settings.deleteLater()

    from notch.ui.controls import TextButton
    primary_contrast = contrast((255, 255, 255), hex_rgb(TextButton.PRIMARY))
    check("the primary button label clears 4.5:1 on its fill",
          primary_contrast >= 4.5,
          f"{primary_contrast:.1f}:1 on {TextButton.PRIMARY}")

    # ── Setup opens one stream, late, instead of probing every device ──
    fake_devices = [
        {"name": f"Microphone {index}", "max_input_channels": 1,
         "max_output_channels": 0}
        for index in range(6)
    ]
    with patch.object(settings_dialog.sd, "query_devices",
                      return_value=fake_devices):
        with patch.object(settings_dialog.sd, "InputStream") as stream_factory:
            probe_dialog = settings_dialog.SettingsDialog(None)
            QTest.qWait(80)
            opened = stream_factory.call_count
            probe_dialog._stop_vu_stream()
            probe_dialog.deleteLater()
    check("Setup opens one meter stream, not one probe per microphone",
          opened == 1, f"{opened} stream(s) for 6 devices")

    # ── Help and Setup follow the light theme too ────────────────────
    def dialog_surface(dialog, point):
        dialog.show()
        QTest.qWait(120)
        colour = dialog.grab().toImage().pixelColor(*point)
        return (colour.red(), colour.green(), colour.blue())

    with patch.object(settings_dialog.sd, "query_devices", return_value=[]):
        with patch.object(settings_dialog.SettingsDialog, "_start_vu_stream",
                          lambda *_: None):
            for dark, name, threshold in ((True, "dark", 60), (False, "light", 220)):
                sheet = settings_dialog.SettingsDialog(None, dark=dark)
                pixel = dialog_surface(sheet, (14, sheet.height() // 2))
                check(f"Setup uses the {name} surface",
                      all(v < threshold for v in pixel) if dark
                      else all(v > threshold for v in pixel),
                      str(pixel))
                sheet.deleteLater()

    for dark, name, threshold in ((True, "dark", 60), (False, "light", 220)):
        guide = ui_overlay.HelpDialog(None, dark=dark)
        pixel = dialog_surface(guide, (guide.width() // 2, 2))
        check(f"Help uses the {name} surface",
              all(v < threshold for v in pixel) if dark
              else all(v > threshold for v in pixel),
              str(pixel))
        guide.deleteLater()

    # ── One reader per session ───────────────────────────────────────
    # Two copies share one state file, one camera and one set of chords; the
    # last save wins, so the second launch can silently undo the first.
    from notch.platform import single_instance
    import subprocess

    def probe_from_another_process():
        """Ask a fresh interpreter whether it can take the reader's mutex."""
        code = (
            "import sys; sys.path.insert(0, %r);"
            "from notch.platform import single_instance as s;"
            "print('taken' if not s.acquire() else 'free')" % REPO_ROOT
        )
        done = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT,
                              capture_output=True, text=True, timeout=60)
        return done.stdout.strip()

    if sys.platform == "win32":
        check("a reader takes the session mutex at startup",
              single_instance.acquire())
        try:
            seen = probe_from_another_process()
        finally:
            single_instance.release()
        check("a second copy is refused while the reader runs",
              seen == "taken", seen)
        check("the mutex is free again once the reader quits",
              probe_from_another_process() == "free")

    # ── The reader's own words stay out of the log by default ────────
    from notch.audio import worker as audio_worker

    def trace_output(enabled):
        buffer = io.StringIO()
        with patch.object(audio_worker, "_TRACE_TRANSCRIPT", enabled):
            with patch("sys.stdout", buffer):
                audio_worker._trace_transcript("partial", "these are my words")
        return buffer.getvalue()

    check("a transcript is not written unless tracing is switched on",
          trace_output(False) == "" and "these are my words" in trace_output(True))

    for window in (win, menu_win, restored, positioned, import_win):
        with patch.object(QApplication, "quit", lambda *_: None):
            window.close()
    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All robustness checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
