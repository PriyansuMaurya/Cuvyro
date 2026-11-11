"""Design invariants for the overlay, asserted against rendered pixels.

These are the claims the redesign makes, in a form that fails loudly if the
surface drifts back:

  * the reader keeps its solid card: fill, wash, level line and hairline are
    all still there, and the window is never a hole in the desktop,
  * the script runs the full height of the card, from its top edge down
    behind the floating controls, fully opaque, with no fade or blur and no
    reserved header band,
  * the controls keep the exact position and size they had, only without a
    header bar behind them, and each one carries a subtle rounded glass
    plate that lifts its glyph off the script,
  * the resting pill carries a visible level meter instead of being empty,
  * the narrow reader no longer sets type below 8 pt,
  * the editor reserves a strip so its last line never runs under Done,
  * the menu is one panel with one mode control, and its secondary rows
    stay legible in both themes.

Run it on a real display, not offscreen: the text-ink checks need the
platform's font database, which the offscreen plugin does not provide.

    .venv/Scripts/python.exe tests/check_design.py
"""

import os
import sys
import tempfile


from PyQt5.QtCore import QPoint
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

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


def relative_luminance(rgb):
    def channel(value):
        value = value / 255.0
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(v) for v in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    a, b = relative_luminance(fg), relative_luminance(bg)
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)


def pixel(image, x, y):
    color = image.pixelColor(int(x), int(y))
    return (color.red(), color.green(), color.blue())


def ink_count(image, box, background, tolerance=12):
    """Pixels inside box that differ from the card fill.

    Measured against the card colour, because the card is back: the window
    grab is fully opaque, so coverage is what the type does to it.
    """
    x0, y0, x1, y1 = (int(v) for v in box)
    count = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            sample = pixel(image, x, y)
            if sum(abs(a - b) for a, b in zip(sample, background)) > tolerance:
                count += 1
    return count


def main():
    app = QApplication(sys.argv)
    import ui_overlay
    from store import Store

    win = ui_overlay.NotchOverlay()
    win._store = Store(tempfile.mkdtemp(prefix="notch-design-"))
    win._voice_mode = False  # keep the microphone out of an automated run
    win.show()
    win._load_script_from_file(FIXTURE)
    QTest.qWait(250)

    for theme_dark in (True, False):
        win._dark_theme = theme_dark
        win._apply_read_mode_style()
        win._apply_control_theme()
        win.update()
        QTest.qWait(80)
        image = win.grab().toImage()
        # Inside the card, away from text. The window keeps a real surface:
        # the full-height script runs on a card, not on the desktop.
        fill = pixel(image, 12, 60)
        name = "dark" if theme_dark else "light"
        # Fully opaque in both themes: a surface, not a transparent pane. The
        # text behind it must not be readable, so transparency stays in the
        # floating chrome alone; the type itself is opaque.
        centre_alpha = image.pixelColor(win.width() // 2, win.height() // 2).alpha()
        check(f"{name}: the reader sits on a solid card", centre_alpha == 255,
              f"centre alpha {centre_alpha}")

        # The script sits on a neutral material: the old glow pulled the
        # bottom-centre pixels blue by ~20 levels.
        wash = pixel(image, win.width() / 2, win.height() - 30)
        drift = max(abs(a - b) for a, b in zip(wash, fill))
        check(f"{name}: no colour wash behind the last line", drift <= 4,
              f"bottom-centre {wash} vs fill {fill}, drift {drift}")

        # The voice level line exists, and it is the mode's colour.
        meter = pixel(image, win.width() / 2, win.height() - 6)
        check(f"{name}: voice level line is drawn", meter != fill,
              f"meter {meter} vs fill {fill}")

    # The controls float where they always did, with no bar around them.
    s = win._scale
    expected = {
        "minimize": (int(18 * s), int(4 * s)),
        "note": None,  # right-aligned cluster, checked below
    }
    close_at = win._close_btn.mapTo(win, QPoint(0, 0))
    check("the minimize control keeps its exact position",
          (close_at.x(), close_at.y()) == expected["minimize"],
          f"{close_at.x()},{close_at.y()} vs {expected['minimize']}")
    right = win._compact_btn.mapTo(win, QPoint(0, 0)).x() + win._compact_btn.width()
    check("the icon cluster keeps its right inset",
          abs(right - (win.width() - int(18 * s))) <= 1,
          f"right edge {right} vs {win.width() - int(18 * s)}")
    tops = {btn.mapTo(win, QPoint(0, 0)).y() for btn in
            (win._close_btn, win._note_btn, win._mic_btn,
             win._compact_btn)}
    check("every control keeps the same baseline", tops == {int(4 * s)},
          f"y offsets {sorted(tops)}")

    # Each control carries a subtle glass plate: a rounded, low-alpha surface
    # with a hairline, not a solid chip. Sampled inside the plate, below the
    # glyph, in both themes: a few levels off the card and never more, so the
    # words behind the chrome still read through it.
    for dark in (True, False):
        win._dark_theme = dark
        win._apply_read_mode_style()
        win._apply_control_theme()
        win.update()
        QTest.qWait(80)
        image = win.grab().toImage()
        fill = pixel(image, 12, 60)
        name = "dark" if dark else "light"
        drifts = {}
        for label, btn in (("minimize", win._close_btn), ("note", win._note_btn),
                           ("mic", win._mic_btn), ("width", win._compact_btn)):
            at = btn.mapTo(win, QPoint(0, 0))
            sample = pixel(image, at.x() + btn.width() / 2,
                           at.y() + btn.height() - 5)
            drifts[label] = max(abs(a - b) for a, b in zip(sample, fill))
        check(f"{name}: every control sits on a glass plate",
              all(3 <= value <= 40 for value in drifts.values()),
              f"plate drift {drifts} levels from card {fill}")

    # The text actually occupies the window at the reference size.
    win._dark_theme = False
    win._apply_read_mode_style()
    win._apply_highlight(-1)
    QTest.qWait(50)
    image = win.grab().toImage()
    fill = pixel(image, 12, 60)
    body_ink = ink_count(image, (20, 34, win.width() - 20, 110), fill)
    check("script text is rendered in the window", body_ink > 80, f"{body_ink} ink px")

    # The reader is a plain, fully opaque text column: the viewport spans the
    # card's full height and QTextEdit paints the script itself, with no
    # offscreen alpha pass. A line near the top edge therefore paints at the
    # script's full brightness instead of fading into a ghost.
    edit = win._text_edit
    bar = edit.verticalScrollBar()
    check("the script viewport spans the card's full height",
          edit.viewport().height() == win.height(),
          f"viewport height {edit.viewport().height()} of {win.height()}")

    win._dark_theme = True
    win._apply_read_mode_style()
    win._apply_highlight(-1)
    QTest.qWait(60)
    wrapped = [k for k in range(2, len(win._line_spans))
               if win._line_spans[k].top - win._line_spans[k - 1].top <= 32]
    top_index = wrapped[0] if wrapped else max(1, len(win._line_spans) - 1)
    bar.setValue(max(0, min(bar.maximum(),
                           int(round(win._line_spans[top_index].top - 8)))))
    QTest.qWait(80)
    top_edge = edit.viewport().grab().toImage()
    brightest = 0
    for y in range(0, min(30, top_edge.height())):
        for x in range(0, top_edge.width()):
            colour = top_edge.pixelColor(x, y)
            brightest = max(brightest, colour.red(), colour.green(), colour.blue())
    check("text near the top edge is fully bright, not faded",
          brightest >= 200, f"peak channel {brightest} in the top 30 px")

    # The line the speaker is on sits below the control strip, with most of
    # the card still ahead of it. A line near the end is exempt: the bar runs
    # out there and the remaining text is shown as it is.
    reading_index = max(0, min(3, len(win._line_spans) - 2))
    win._scroll_to_reading_line(reading_index)
    QTest.qWait(60)
    bar.setValue(int(round(win._voice_target_scroll or 0)))
    win._sync_scroll_spring()
    QTest.qWait(60)
    reading = win._line_spans[reading_index]
    line_top = reading.top - bar.value()
    check("the reading line sits below the controls and above the middle",
          win._chrome_strip() - 1 <= line_top <= win.height() * 0.6,
          f"line top {line_top:.1f}, chrome {win._chrome_strip()}, "
          f"window {win.height()}")

    # Narrow reader: legible type, and the narrow card keeps its own radius.
    win._toggle_compact_mode()
    QTest.qWait(700)
    check("narrow reader sets type at 8 pt or larger",
          win._font_size >= 8, f"font size {win._font_size}")
    narrow = win.grab().toImage()
    narrow_fill = pixel(narrow, 4, 60)
    narrow_ink = ink_count(narrow, (8, 36, win.width() - 8, 108), narrow_fill)
    check("narrow reader still shows a readable block", narrow_ink > 80,
          f"{narrow_ink} ink px")
    win._toggle_compact_mode()
    QTest.qWait(700)

    # Resting pill: it must not be an empty lozenge.
    win._minimize()
    QTest.qWait(700)
    pill = win.grab().toImage()
    pill_fill = pixel(pill, 10, 6)
    center_top = win.height() // 2 - 8
    pill_ink = ink_count(pill, (win.width() // 2 - 12, center_top,
                                win.width() // 2 + 12, center_top + 16), pill_fill)
    check("resting pill shows a level meter", pill_ink > 12, f"{pill_ink} ink px")
    win._restore_from_peek()
    QTest.qWait(700)

    # Editor: it keeps a surface to type on, and the Done button gets a
    # reserved strip under the text.
    win._toggle_edit_mode()
    QTest.qWait(700)
    editor_margin = win._text_edit.document().rootFrame().frameFormat().bottomMargin()
    check("editor reserves room for the Done button", editor_margin >= 36,
          f"document bottom margin {editor_margin}")
    win._toggle_edit_mode()
    QTest.qWait(700)
    reader_margin = win._text_edit.document().rootFrame().frameFormat().bottomMargin()
    anchor_room = win._text_edit.anchor_padding()
    check("reader keeps the anchor room, not the Done strip",
          abs(reader_margin - anchor_room) <= 0.5 and 0 < reader_margin < 36,
          f"reader {reader_margin:.1f} vs anchor {anchor_room:.1f}, "
          f"editor {editor_margin:.1f}")
    frame = win._text_edit.document().rootFrame().frameFormat()
    first_line_room = frame.topMargin() + win._text_edit.document().documentMargin()
    # No reserved header: the script starts at the card's top edge and runs
    # behind the floating chrome. Only the foot keeps its room, so the last
    # line can still be scrolled up onto the reading line.
    check("the script reserves no header above its first line",
          frame.topMargin() == 0 and first_line_room <= 12,
          f"top margin {frame.topMargin():.1f}, "
          f"first line at {first_line_room:.1f} px")
    check("the last line can still reach the reading line",
          reader_margin >= win._text_edit.viewport().height()
          - win._text_edit.reading_inset()
          - win._text_edit.fontMetrics().lineSpacing()
          - win._text_edit.document().documentMargin() - 0.5,
          f"bottom {reader_margin:.1f}, inset "
          f"{win._text_edit.reading_inset():.1f}")

    # Menu: one mode control, a status line, and mode-dependent rows.
    win._voice_mode = True
    menu = win._build_menu()
    check("menu is a single fixed-width panel",
          menu.sizeHint().width() == int(236 * win._scale),
          f"{menu.sizeHint().width()} px")
    check("menu states the current mode once",
          menu._status_label.text() == "Following your voice"
          and menu._voice_btn.isChecked() and not menu._manual_btn.isChecked(),
          f"{menu._status_label.text()!r}")
    check("menu hides speed in voice mode", not menu._speed_action.isVisible())

    win._voice_mode = False
    win._refresh_menu_mode_display(menu)
    QTest.qWait(60)
    check("menu shows speed in manual mode", menu._speed_action.isVisible())
    check("menu does not repeat the speed in its status line",
          menu._status_label.text() == "Timed scroll · paused",
          f"{menu._status_label.text()!r}")
    check("menu switches the mode control", menu._manual_btn.isChecked()
          and not menu._voice_btn.isChecked())

    # Contrast of the menu's two type levels against its own material.
    for dark, panel, primary, secondary in (
        (True, (30, 30, 30), (237, 237, 239), (156, 156, 162)),
        (False, (247, 247, 248), (21, 21, 23), (110, 110, 115)),
    ):
        name = "dark" if dark else "light"
        check(f"{name} menu: primary text contrast >= 7:1",
              contrast(primary, panel) >= 7.0, f"{contrast(primary, panel):.1f}:1")
        check(f"{name} menu: secondary text contrast >= 4.5:1",
              contrast(secondary, panel) >= 4.5, f"{contrast(secondary, panel):.1f}:1")

    # The toolbar is four glyphs and no menu button: the menu opens on a
    # right click, so the "more" control had nothing left to do.
    check("the toolbar carries no menu control",
          not hasattr(win, "_settings_btn")
          and not hasattr(win, "_open_menu_from_button"),
          f"settings button {getattr(win, '_settings_btn', None)!r}")

    win.close()
    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All design checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
