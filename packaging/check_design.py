"""Design invariants for the overlay, asserted against rendered pixels.

These are the claims the redesign makes, in a form that fails loudly if the
surface drifts back:

  * the card is neutral behind the script (the old radial wash tinted the
    lower half blue on both themes),
  * the voice level line is actually drawn at the bottom of the card,
  * the resting pill carries a visible level meter instead of being empty,
  * the narrow reader no longer sets type below 8 pt,
  * the editor reserves a strip so its last line never runs under Done,
  * the menu is one panel with one mode control, and its secondary rows
    stay legible in both themes.

Run it on a real display, not offscreen: the text-ink checks need the
platform's font database, which the offscreen plugin does not provide.

    .venv/Scripts/python.exe packaging/check_design.py
"""

import os
import sys
import tempfile

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
    """Pixels inside box that differ from the card fill."""
    x0, y0, x1, y1 = box
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
    from controls import MoreButton
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
        fill = pixel(image, 12, 60)  # inside the card, away from text
        name = "dark" if theme_dark else "light"

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

    # The text actually occupies the card at the reference size.
    win._dark_theme = False
    win._apply_read_mode_style()
    QTest.qWait(50)
    image = win.grab().toImage()
    fill = pixel(image, 12, 60)
    body_ink = ink_count(image, (20, 34, win.width() - 20, 78), fill)
    check("script text is rendered in the card", body_ink > 200, f"{body_ink} ink px")

    # The viewport always cuts a line in half. The band has to sit on that
    # line, not on the card's edge, or its top half shows through as a grey
    # ghost under the script.
    fade = win._bottom_fade
    check("the fade covers the clipped line", fade.isVisible())
    band = fade.geometry()
    check("the fade starts inside a line of the text area",
          band.top() < win._text_edit.y() + win._text_edit.height(),
          f"band {band.getRect()} text area bottom "
          f"{win._text_edit.y() + win._text_edit.height()}")
    stray = 0
    for y in range(band.top() + 4, band.bottom() - 2):
        for x in range(20, win.width() - 20):
            if sum(abs(a - b) for a, b in zip(pixel(image, x, y), fill)) > 60:
                stray += 1
    check("the clipped line is dissolved, not ghosted", stray == 0,
          f"{stray} stray ink px under the script")

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

    # Editor: the Done button gets a reserved strip under the text.
    win._toggle_edit_mode()
    QTest.qWait(700)
    margin = win._text_edit.document().rootFrame().frameFormat().bottomMargin()
    check("editor reserves room for the Done button", margin >= 36,
          f"document bottom margin {margin}")
    win._toggle_edit_mode()
    QTest.qWait(700)
    margin = win._text_edit.document().rootFrame().frameFormat().bottomMargin()
    check("reader mode gives the room back", margin == 0, f"margin {margin}")

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

    # The menu entry point no longer hides a filled glyph in the toolbar.
    check("toolbar entry point is a quiet 'more' control",
          isinstance(win._settings_btn, MoreButton),
          type(win._settings_btn).__name__)

    win.close()
    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All design checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
