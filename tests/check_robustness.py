"""Robustness checks for persistence, window placement, import and access.

These cover the surfaces the design and reading checks do not: what happens
when a note is typed into, when the menu is opened repeatedly, when a saved
window belongs to a monitor that is gone, when a script is not UTF-8, and
whether every custom-drawn control can be reached without a mouse.

Run headless:

    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_robustness.py
"""

import json
import os
import sys
import tempfile
import time
from unittest.mock import patch

from PyQt5.QtCore import QObject, QPoint, QRect, QSettings, Qt
from PyQt5.QtGui import QTextCursor
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLabel, QShortcut

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


def saved_note(directory, index=0):
    notes = state_file(directory).get("notes") or []
    if index >= len(notes) or not isinstance(notes[index], dict):
        return ""
    return str(notes[index].get("text") or "")


def make_overlay(directory):
    import ui_overlay
    from store import Store

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
    import ui_overlay
    import settings_dialog
    from store import Store

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

    # A note opened while the reader sits against the right edge stays whole.
    restored.move(screen.right() - 60, screen.y())
    QTest.qWait(80)
    edge_note = restored._open_note()
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

    spoken = (0x8A, 0x8A, 0x8A)
    base = (0xFA, 0xFA, 0xFA)
    shade_win._apply_highlight(10)
    check("speech shading marks the spoken run",
          shade_at(10) == spoken and shade_at(40) == base,
          f"word 10 {shade_at(10)}, word 40 {shade_at(40)}")
    shade_win._apply_highlight(5)
    check("stepping back un-shades the words the reader returned to",
          shade_at(10) == base and shade_at(5) == spoken,
          f"word 10 {shade_at(10)}, word 5 {shade_at(5)}")
    shade_win._apply_highlight(30)
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
    QTest.qWait(150)
    check("a note closes from the keyboard",
          first_id not in [note.note_id for note in import_win._notes])

    second = import_win._open_note()
    second_id = second.note_id
    second._escape.activated.emit()
    QTest.qWait(150)
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

    from controls import TextButton
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
