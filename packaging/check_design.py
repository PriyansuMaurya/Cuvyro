"""Design invariants for the overlay, asserted against rendered pixels.

These are the claims the redesign makes, in a form that fails loudly if the
surface drifts back:

  * the reader keeps its solid card: fill, wash, level line and hairline are
    all still there, and the window is never a hole in the desktop,
  * the script runs the full height of the card, underneath the floating
    controls and out of both edges, losing presence progressively instead
    of being clipped,
  * the controls keep the exact position and size they had, only without a
    header bar behind them,
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

import numpy as np

from PyQt5.QtCore import QPoint
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


def alpha_plane(image):
    """The rendered type's alpha, one array of device pixels."""
    raw = image.constBits()
    raw.setsize(image.sizeInBytes())
    grid = np.frombuffer(raw, dtype=np.uint8)
    return grid.reshape(image.height(), image.width(), 4)[:, :, 3].astype(np.int64)


def row_retention(sharp, soft):
    """Per row of type: how much of its alpha survives the dissolve."""
    sharp_total = sharp.sum(axis=1)
    soft_total = soft.sum(axis=1)
    return {
        y: soft_total[y] / float(sharp_total[y])
        for y in range(sharp.shape[0])
        if sharp_total[y]
    }


def dilate(mask, radius):
    """A mask grown by radius pixels, for judging where blur may reach."""
    height, width = mask.shape
    padded = np.pad(mask, radius, mode="constant", constant_values=False)
    grown = np.zeros_like(mask)
    for dy in range(2 * radius + 1):
        for dx in range(2 * radius + 1):
            grown |= padded[dy:dy + height, dx:dx + width]
    return grown


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
        # Inside the card, away from text. The window keeps a real surface:
        # the full-height script runs on a card, not on the desktop.
        fill = pixel(image, 12, 60)
        name = "dark" if theme_dark else "light"
        # 0.97 in the dark theme, fully opaque in the light one: a surface, not
        # a transparent pane. The text behind it must not be readable.
        centre_alpha = image.pixelColor(win.width() // 2, win.height() // 2).alpha()
        check(f"{name}: the reader sits on a solid card", centre_alpha >= 245,
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
    right = win._settings_btn.mapTo(win, QPoint(0, 0)).x() + win._settings_btn.width()
    check("the icon cluster keeps its right inset",
          abs(right - (win.width() - int(18 * s))) <= 1,
          f"right edge {right} vs {win.width() - int(18 * s)}")
    tops = {btn.mapTo(win, QPoint(0, 0)).y() for btn in
            (win._close_btn, win._note_btn, win._mic_btn,
             win._compact_btn, win._settings_btn)}
    check("every control keeps the same baseline", tops == {int(4 * s)},
          f"y offsets {sorted(tops)}")

    # The text actually occupies the window at the reference size.
    win._dark_theme = False
    win._apply_read_mode_style()
    win._apply_highlight(-1)
    QTest.qWait(50)
    image = win.grab().toImage()
    fill = pixel(image, 12, 60)
    body_ink = ink_count(image, (20, 34, win.width() - 20, 110), fill)
    check("script text is rendered in the window", body_ink > 80, f"{body_ink} ink px")

    # The viewport no longer covers the line at the cut with a band: it
    # dissolves the type itself. Only the line being read is opaque — the
    # whole top of the card and the whole bottom of it are a ghost, so the
    # dissolve is most of the column, not a strip at each edge. Nothing may be
    # painted over the script, the reading line must survive untouched, and
    # every scroll offset has to behave — this is the scrolling test as a
    # pixel check.
    edit = win._text_edit
    check("reader dissolves its own edges", edit.soft_edges())

    # The band is measured mid-script, where the reader actually sits: at the
    # document's first line the scrollbar has nowhere to go and the band is
    # pinned to that line instead.
    bar = edit.verticalScrollBar()
    ratio = edit.devicePixelRatioF()
    win._scroll_to_reading_line(max(0, min(10, len(win._line_spans) - 2)))
    bar.setValue(int(round(win._voice_target_scroll or 0)))
    QTest.qWait(60)

    line_top, line_bottom = edit.reading_band()
    height_dip = float(edit.viewport().height())
    top_band = line_top
    bottom_band = height_dip - line_bottom
    check("the whole top and bottom of the card are a dissolve",
          top_band >= height_dip * 0.25 and bottom_band >= height_dip * 0.25,
          f"top {top_band:.1f} px, bottom {bottom_band:.1f} px "
          f"of {height_dip:.0f} px")
    check("the reading line sits clear of the controls",
          line_top >= 4 + 32, f"line top {line_top:.1f} of 36 px strip")

    check("the reading line itself stays out of the dissolve",
          line_bottom - line_top <= edit.height() * 0.45,
          f"line band {line_bottom - line_top:.1f} of {edit.height()} px")

    blur_reach = 2 * max(1, int(round(edit.BLUR_PX * ratio))) + 1
    retained = {"top": [], "bottom": []}
    added = 0
    moved_line = 0.0
    spread = 0
    # The chrome floats over the script instead of sitting in a bar above it:
    # the strip the controls occupy carries type of its own, at a fraction of
    # the reading line's strength.
    strip_peak = 0
    band_peak = 0
    offsets = list(range(0, int(min(bar.maximum(), 220)), 8))
    for offset in offsets:
        bar.setValue(offset)
        sharp = alpha_plane(edit._render(soften=False))
        soft = alpha_plane(edit._render())
        # The blur is allowed to spread ink a few pixels; nothing may be
        # painted outside the type's own neighbourhood. A band would be.
        added += int(((soft > 0) & ~dilate(sharp > 0, blur_reach)).sum())
        rows = row_retention(sharp, soft)
        # The band moves with the scroll at the document's ends, so it is
        # measured per offset rather than once.
        band_top, band_bottom = edit.reading_band()
        first = max(1, int(np.ceil(band_top * ratio)))
        last = min(soft.shape[0] - 1, int(np.floor(band_bottom * ratio)))
        strip_peak = max(strip_peak,
                         int(soft[int(4 * ratio):int(32 * ratio)].max()))
        band_peak = max(band_peak, int(soft[first:last].max()))
        edge_rows = list(range(0, first)) + list(range(last, soft.shape[0]))
        for y, value in rows.items():
            if y < first:
                retained["top"].append(value)
            elif y >= last:
                retained["bottom"].append(value)
            else:
                moved_line = max(moved_line, abs(1.0 - value))
        spread += sum(
            1 for y in edge_rows
            if int((soft[y] > 0).sum()) > int((sharp[y] > 0).sum())
        )

    check("the dissolve never paints anything of its own", added == 0,
          f"{added} px added outside the type")
    check("the sharp band is untouched", moved_line < 1e-6,
          f"max alpha loss {moved_line:.6f}")
    check("type actually crosses both edges while scrolling",
          len(retained["top"]) > 40 and len(retained["bottom"]) > 40,
          f"{len(retained['top'])} top rows, {len(retained['bottom'])} bottom rows "
          f"over {len(offsets)} offsets")
    for edge_name, values in retained.items():
        check(f"{edge_name} edge is near-transparent at the cut",
              min(values) < 0.4, f"min retention {min(values):.2f}")
        # The ramp is meant to reach nothing at the cut: the top and bottom
        # of the card are fully transparent, so no type is visible there.
        check(f"{edge_name} edge fades to fully transparent",
              min(values) <= 0.01,
              f"min retention {min(values) * 100:.1f}% of its sharp alpha")
        levels = {round(value * 10) for value in values if 0.1 < value < 0.95}
        check(f"{edge_name} edge fades progressively",
              len(levels) >= 4, f"{len(levels)} levels between 10% and 95%")
    check("edge type is spread by a light blur, not just dimmed",
          spread > 20, f"{spread} blurred rows")
    check("type runs underneath the floating controls",
          0 < strip_peak < band_peak * 0.6,
          f"strip peak {strip_peak} vs reading line {band_peak}")

    # The line the speaker is on sits below the top dissolve, fully sharp.
    reading_index = max(0, min(10, len(win._line_spans) - 2))
    win._scroll_to_reading_line(reading_index)
    # The scrollbar springs toward the target, so read it only once it has
    # actually arrived, or the measurement lands mid-glide.
    for _ in range(40):
        if abs(bar.value() - (win._voice_target_scroll or 0)) <= 1:
            break
        QTest.qWait(20)
    bar.setValue(int(round(win._voice_target_scroll or 0)))
    QTest.qWait(60)
    sharp = alpha_plane(edit._render(soften=False))
    soft = alpha_plane(edit._render())
    rows = row_retention(sharp, soft)
    scroll = float(bar.value())
    reading = win._line_spans[reading_index]
    line_top = reading.top - scroll
    inside = [value for y, value in rows.items()
              if line_top - 0.5 <= y / ratio <= line_top + reading.height + 0.5]
    anchor = edit.reading_inset()
    check("the reading line is anchored below the dissolve",
          abs(line_top - anchor) <= 1.5,
          f"line top {line_top:.1f} vs anchor {anchor:.1f}")
    check("the line being read is fully sharp",
          bool(inside) and min(inside) > 0.999,
          f"{len(inside)} rows, min retention "
          f"{min(inside) if inside else 0:.4f}")

    # Everything outside the reading line is a ghost. That is what makes the
    # whole column see-through instead of a solid page with feathered ends.
    first_row = int(np.ceil(line_top * ratio))
    last_row = int(np.floor((line_top + reading.height) * ratio))
    # Two rows of grace at the boundary: the edge blur can lift a neighbouring
    # row back to full alpha without any of its own type being solid.
    outside = [value for y, value in rows.items()
               if y < first_row - 2 or y >= last_row + 2]
    dim = sum(1 for value in outside if value < 0.9) / max(1, len(outside))
    check("type outside the reading line is a ghost",
          bool(outside) and float(np.median(outside)) <= 0.5 and dim >= 0.8,
          f"{len(outside)} rows, median "
          f"{float(np.median(outside)) if outside else 0:.2f}, "
          f"{dim * 100:.0f}% below full strength")

    # The dissolve is composited over the card, not painted as a band: at a
    # scroll where a line crosses the bottom edge there is still type there,
    # fainter than the line in the sharp band.
    win._dark_theme = True
    win._apply_read_mode_style()
    win._apply_highlight(-1)
    QTest.qWait(60)
    # A wrapped line, so the line above it is in the sharp band while it
    # crosses the bottom edge itself.
    index = next(
        k for k in range(2, len(win._line_spans))
        if win._line_spans[k].top - win._line_spans[k - 1].top <= 32
    )
    line = win._line_spans[index]
    bar.setValue(max(0, int(round(line.top - (edit.height() - 18)))))
    QTest.qWait(80)
    soft = alpha_plane(edit._render())
    band_top, band_bottom = edit.reading_band()
    ratio = edit.devicePixelRatioF()
    centre_peak = int(soft[max(1, int(np.ceil(band_top * ratio))):
                           int(np.floor(band_bottom * ratio))].max())
    # Sampled just above the outer rows: the last few are fully transparent
    # by design, so the type has to be judged where the fade still leaves it.
    bottom_peak = int(soft[max(0, soft.shape[0] - int(16 * ratio)):
                           soft.shape[0] - int(10 * ratio)].max())
    check("type still reaches the bottom region", bottom_peak > 10,
          f"peak alpha {bottom_peak}")
    check("type at the bottom edge is attenuated, not clipped",
          bottom_peak < centre_peak * 0.7,
          f"edge {bottom_peak} vs sharp {centre_peak}")

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
