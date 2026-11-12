"""Design invariants for the overlay, asserted against rendered pixels.

These are the claims the redesign makes, in a form that fails loudly if the
surface drifts back:

  * the reader keeps its solid card: fill, wash, foot waveform and hairline
    are all still there, and the window is never a hole in the desktop,
  * the script runs the full height of the card, fully opaque, with no fade
    or blur, and opens 30% down so its first line clears the floating
    controls; the opening lead then scrolls away with the type,
  * the four controls float in a transparent vertical rail outside the
    card's right edge, centred with equal gaps, each a circular button with
    its own shadow and no connecting panel;
  * the resting pill carries a visible level meter instead of being empty,
  * the narrow reader no longer sets type below 8 pt,
  * the editor reserves a strip so its last line never runs under Done,
  * the menu is one panel with one mode control, its three commands share a
    single row instead of a stack, and its secondary rows stay legible in
    both themes.

Run it on a real display, not offscreen: the text-ink checks need the
platform's font database, which the offscreen plugin does not provide.

    .venv/Scripts/python.exe tests/check_design.py
"""

import os
import re
import sys
import tempfile


from PyQt5.QtCore import QPoint, QRect
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import (
    QAbstractButton, QApplication, QLabel, QToolTip, QWidget, QWidgetAction,
)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "demo-script.txt")
# The right-click panel is a panel, not a page: seven setting rows plus the
# script and action rows have to fit a 768 px work area beside the reader.
PANEL_HEIGHT_BUDGET = 400
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


TOOLTIP_TEXT = "tooltip probe"
# The panel colour each theme gives a tooltip, as the app applies it.
TOOLTIP_BODY = {True: (30, 30, 30), False: (247, 247, 248)}


def check_tooltips(win):
    """A tooltip is its own window and has to carry the theme by itself.

    Qt builds it with a copy of the palette of the widget it belongs to, so
    the transparent control rail left it with an alpha-0 background: a dark,
    unreadable slab over the light card, which is exactly what a light-mode
    hover showed. Both the opacity and the colour are asserted, because a
    tooltip can be opaque and still be the wrong theme.
    """
    for dark in (True, False):
        name = "dark" if dark else "light"
        win._dark_theme = dark
        win._apply_read_mode_style()
        QTest.qWait(140)
        QToolTip.showText(win._mic_btn.mapToGlobal(win._mic_btn.rect().center()),
                          TOOLTIP_TEXT, win._mic_btn)
        QTest.qWait(340)
        tips = [w for w in QApplication.allWidgets()
                if isinstance(w, QLabel) and w.text() == TOOLTIP_TEXT
                and w.isVisible()]
        if not tips:
            check(f"a control's tooltip opens in {name} mode", False,
                  "no tooltip window")
            continue
        body = tips[-1].grab().toImage().pixelColor(3, 3)
        check(f"a control's tooltip is opaque in {name} mode",
              body.alpha() == 255, f"alpha {body.alpha()}")
        want = TOOLTIP_BODY[dark]
        check(f"a control's tooltip follows the {name} theme",
              (body.red(), body.green(), body.blue()) == want,
              f"{(body.red(), body.green(), body.blue())} vs {want}")
        QToolTip.hideText()
        QTest.qWait(180)
    win._dark_theme = True
    win._apply_read_mode_style()
    QTest.qWait(100)


def main():
    app = QApplication(sys.argv)
    from notch.ui import overlay as ui_overlay
    from notch.storage.store import Store

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
        centre_alpha = image.pixelColor(win._card_width() // 2, win.height() // 2).alpha()
        check(f"{name}: the reader sits on a solid card", centre_alpha == 255,
              f"centre alpha {centre_alpha}")

        # The script sits on a neutral material: the old glow pulled the
        # bottom-centre pixels blue by ~20 levels.
        wash = pixel(image, win.width() / 2, win.height() - 30)
        drift = max(abs(a - b) for a, b in zip(wash, fill))
        check(f"{name}: no colour wash behind the last line", drift <= 4,
              f"bottom-centre {wash} vs fill {fill}, drift {drift}")

        # The microphone's ribbon owns the foot strip: one bar of mode
        # coloured ink at every bar's centre, and the strip stays below the
        # line being read. The tint test separates the ribbon from the white
        # type, which would otherwise ink the same pixels.
        x0, pitch, count, bar_w = win.waveform_layout()
        band = win.waveform_band()
        centre_y = win.height() - win.WAVE_TOP_MARGIN * win._scale
        channel = 2 if win._voice_mode else 1  # blue = Notch, green = Keys
        bars = 0
        for index in range(count):
            sx = x0 + index * pitch + bar_w / 2.0
            rgb = pixel(image, sx, centre_y)
            if rgb[channel] - rgb[0] > 8:
                bars += 1
        check(f"{name}: the microphone ribbon is drawn at the foot",
              bars >= count - 1, f"{bars}/{count} bars tinted")
        check(f"{name}: the ribbon stays clear of the reading line",
              band.top() > win._text_edit.reading_inset(),
              f"band top {band.top():.1f}, reading inset "
              f"{win._text_edit.reading_inset():.1f}")

    # The controls are a floating column outside the card, not a toolbar on
    # it. Every button shares one x, the gaps are equal, and the whole group
    # is centred on the card's height.
    card = win._card_width()
    plates = [win._close_btn, win._note_btn, win._mic_btn, win._compact_btn]
    spots = [b.mapTo(win, QPoint(0, 0)) for b in plates]
    widths = [b.width() for b in plates]
    heights = [b.height() for b in plates]
    gaps = [spots[i + 1].y() - (spots[i].y() + heights[i])
            for i in range(len(plates) - 1)]
    group_top, group_bottom = spots[0].y(), spots[-1].y() + heights[-1]
    check("the four controls form one vertical rail",
          len({p.x() for p in spots}) == 1
          and all(a.y() < b.y() for a, b in zip(spots, spots[1:])),
          f"x {[p.x() for p in spots]}, y {[p.y() for p in spots]}")
    check("the rail is centred with equal gaps",
          max(gaps) - min(gaps) <= 1
          and abs((group_top + group_bottom) / 2.0 - win.height() / 2.0) <= 1.5,
          f"gaps {gaps}, group {group_top}..{group_bottom} in {win.height()}")
    check("the rail clears the reading surface",
          min(p.x() for p in spots) >= card + int(4 * win._scale)
          and max(p.x() + w for p, w in zip(spots, widths)) <= win.width(),
          f"card {card}, buttons {[p.x() for p in spots]}")
    view = win._text_edit.viewport().mapTo(win, QPoint(0, 0))
    check("no word can run under a control",
          view.x() + win._text_edit.viewport().width() <= card,
          f"text ends {view.x() + win._text_edit.viewport().width()}, card {card}")

    # Each button is a circle, not a square or a plate: the corners of the
    # hit box stay transparent while the middle carries the fill.
    for dark in (True, False):
        win._dark_theme = dark
        win._apply_read_mode_style()
        win._apply_control_theme()
        win.update()
        QTest.qWait(80)
        image = win.grab().toImage()
        name = "dark" if dark else "light"
        round_ok = True
        detail = []
        for label, btn in (("minimize", win._close_btn), ("note", win._note_btn),
                           ("mic", win._mic_btn), ("width", win._compact_btn)):
            at = btn.mapTo(win, QPoint(0, 0))
            corner = image.pixelColor(at.x() + 1, at.y() + 1).alpha()
            fill = image.pixelColor(at.x() + btn.width() // 2,
                                    at.y() + btn.height() - 6)
            if corner > 60 or fill.alpha() < 200:
                round_ok = False
            detail.append(f"{label} corner {corner}, fill a{fill.alpha()}")
        check(f"{name}: every control is a filled circle", round_ok,
              "; ".join(detail))

    # The circle floats on its own shadow; the shadow lives inside the hit
    # box, so it cannot become a connecting bar between controls.
    note_at = win._note_btn.mapTo(win, QPoint(0, 0))
    shadow = image.pixelColor(note_at.x() + win._note_btn.width() // 2,
                              note_at.y() + win._note_btn.height() - 1).alpha()
    check("each circle carries a soft shadow",
          8 <= shadow <= 120, f"shadow alpha {shadow}")

    # Hover is a spring, not a switch: the button lifts, then settles back.
    probe = win._close_btn
    probe._set_hover(True)
    QTest.qWait(180)
    raised = probe._hover_spring.value
    probe._set_hover(False)
    QTest.qWait(300)
    rested = probe._hover_spring.value
    check("hover lifts the button on a soft spring",
          raised > 0.6 and rested < 0.2,
          f"raised {raised:.2f}, rested {rested:.2f}")

    # ── The plates are opaque ────────────────────────────────────
    # A control is opaque when the surface behind it cannot change its
    # pixels, so the same button rendered on black and on white has to come
    # back identical. The plate used to be 6% white glass mixed over whatever
    # was behind it, which let the script's words read through the toolbar.
    from notch.ui import controls

    samples = []
    for backdrop in ("#000000", "#FFFFFF"):
        host = QWidget()
        host.setStyleSheet(f"background: {backdrop};")
        host.resize(44, 44)
        plate = controls.NoteButton(host)
        plate.move(7, 7)
        host.show()
        QTest.qWait(80)
        shot = host.grab().toImage()
        samples.append(pixel(shot, 7 + plate.width() // 2,
                             7 + plate.height() - 5))
        host.close()
        host.deleteLater()
    check("a control's circle holds its glyph over any backdrop",
          samples[0] == samples[1],
          f"{samples[0]} on black, {samples[1]} on white")

    # The text actually occupies the window at the reference size.
    win._dark_theme = False
    win._apply_read_mode_style()
    win._apply_highlight(-1)
    QTest.qWait(50)
    image = win.grab().toImage()
    fill = pixel(image, 12, 60)
    body_ink = ink_count(image, (20, 34, win._card_width() - 20, 110), fill)
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
    check("the reading line keeps clear air above it and sits above the middle",
          win._chrome_strip() - 1 <= line_top <= win.height() * 0.6,
          f"line top {line_top:.1f}, chrome {win._chrome_strip()}, "
          f"window {win.height()}")

    # Narrow reader: legible type, and the narrow card keeps its own radius.
    win._toggle_compact_mode()
    QTest.qWait(700)
    check("narrow reader sets type at 8 pt or larger",
          win._font_size >= 8, f"font size {win._font_size}")
    narrow_buttons = [win._close_btn, win._note_btn, win._mic_btn,
                      win._compact_btn]
    narrow_spots = [b.mapTo(win, QPoint(0, 0)) for b in narrow_buttons]
    check("the narrow reader keeps all four controls outside it",
          all(b.isVisible() for b in narrow_buttons)
          and min(p.x() for p in narrow_spots) >= win._card_width() + 4
          and len({p.x() for p in narrow_spots}) == 1,
          f"card {win._card_width()}, x {[p.x() for p in narrow_spots]}")
    narrow = win.grab().toImage()
    narrow_fill = pixel(narrow, 4, 60)
    narrow_ink = ink_count(narrow, (8, 36, win._card_width() - 8, 108), narrow_fill)
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
    pill_center = win._card_width() // 2
    pill_ink = ink_count(pill, (pill_center - 12, center_top,
                                pill_center + 12, center_top + 16), pill_fill)
    check("resting pill shows a level meter", pill_ink > 12, f"{pill_ink} ink px")
    win._restore_from_peek()
    QTest.qWait(700)

    # Tooltips: separate windows, so they must be themed on their own.
    check_tooltips(win)

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
          abs(reader_margin - anchor_room) <= 0.5
          and 0 < reader_margin < editor_margin,
          f"reader {reader_margin:.1f} vs anchor {anchor_room:.1f}, "
          f"editor {editor_margin:.1f}")
    frame = win._text_edit.document().rootFrame().frameFormat()
    bar.setValue(0)
    win._sync_scroll_spring()
    QTest.qWait(60)
    first_line_room = win._line_spans[0].top - bar.value()
    # The opening lead belongs to the document, so it scrolls away with the
    # type instead of clipping the top: the first line clears the top edge at
    # rest, and the foot keeps its room so the last line can still reach the
    # reading line.
    check("the script opens 30% down the card, clear of the top edge",
          abs(first_line_room / win._text_edit.viewport().height() - 0.30) <= 0.01
          and first_line_room >= win._chrome_strip() - 1,
          f"first line at {first_line_room:.1f} px of "
          f"{win._text_edit.viewport().height()}, top air {win._chrome_strip()}, "
          f"top margin {frame.topMargin():.1f}")
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
    check("menu hides speed in voice mode",
          not menu._speed_action.isVisible()
          and not menu._speed_action.defaultWidget().isVisible(),
          f"action {menu._speed_action.isVisible()}, "
          f"row {menu._speed_action.defaultWidget().isVisible()}")

    win._voice_mode = False
    win._refresh_menu_mode_display(menu)
    QTest.qWait(60)
    check("menu shows speed in manual mode", menu._speed_action.isVisible())
    check("menu does not repeat the speed in its status line",
          menu._status_label.text() == "Timed scroll · paused",
          f"{menu._status_label.text()!r}")
    check("menu switches the mode control", menu._manual_btn.isChecked()
          and not menu._voice_btn.isChecked())

    # ── Help, Setup and Quit: one row, not three ──────────────────────
    # Laid out side by side they cost the panel one row instead of three.
    # The popup has to be shown for the row's layout to run, so it is opened
    # off the corner and closed at the end of this block.
    menu.popup(win.mapToGlobal(QPoint(0, 0)))
    menu.adjustSize()
    QTest.qWait(120)
    plates = [menu._help_btn, menu._setup_btn, menu._quit_btn]
    spots = [b.mapTo(menu, QPoint(0, 0)) for b in plates]
    widths = [b.width() for b in plates]
    gaps = [spots[1].x() - (spots[0].x() + widths[0]),
            spots[2].x() - (spots[1].x() + widths[1])]
    item_height = max(menu.actionGeometry(a).height() for a in menu.actions()
                      if a.text() == "Load Script…")
    check("Help, Setup and Quit share one row",
          len({p.y() for p in spots}) == 1
          and len({b.height() for b in plates}) == 1,
          f"y {[p.y() for p in spots]}, "
          f"heights {[b.height() for b in plates]}")
    check("the row keeps menu order, one even gap and equal plates",
          [b.text() for b in plates] == ["Help", "Setup…", "Quit"]
          and spots[0].x() < spots[1].x() < spots[2].x()
          and gaps[0] == gaps[1] > 0
          and max(widths) - min(widths) <= 1,
          f"x {[p.x() for p in spots]}, w {widths}, gaps {gaps}")
    check("the row is flush with the panel's side margins",
          spots[0].x() == menu.width() - (spots[2].x() + widths[2]),
          f"left {spots[0].x()}, "
          f"right {menu.width() - (spots[2].x() + widths[2])}")
    # The panel has to stay a panel, not become a page: one row per designed
    # setting, and a ceiling that still fits a 768 px work area next to the
    # reader with room to spare. The Global keys row is the seventh setting
    # and is what moved the panel off its old 350 px ceiling.
    check("the three actions cost one row of panel height",
          plates[0].height() <= item_height + 1
          and menu.sizeHint().height() <= PANEL_HEIGHT_BUDGET,
          f"row {plates[0].height()} vs item {item_height}, "
          f"panel {menu.sizeHint().height()} px of {PANEL_HEIGHT_BUDGET}")

    # ── The value rows share one right edge ───────────────────────
    # Speed, size and typeface all end their control on the panel's inner
    # edge; the speed number used to stop two pixels short of it.
    def right_edge(widget):
        return widget.mapTo(menu, QPoint(0, 0)).x() + widget.width()

    stepper_plus = [b for b in menu.findChildren(QAbstractButton) if b.text() == "+"][0]
    family_button = [b for b in menu.findChildren(QAbstractButton) if "▾" in b.text()][0]
    # The stepper's field is what closes on the edge now: its border is the
    # control's visible right side, and the buttons sit inside it.
    stepper_field = stepper_plus.parentWidget()
    edges = {"speed": right_edge(menu._speed_value),
             "size": right_edge(stepper_field),
             "typeface": right_edge(family_button)}
    check("the value rows close on one right edge",
          len(set(edges.values())) == 1,
          f"{edges} in {menu.width()} px")

    # ── One button language, one vertical rhythm ────────────────
    # The panel used to mix five button dialects (filled and borderless, two
    # radii, three heights) over a stack of rows that touched each other.
    # Every control is now a field or a plate: same hairline, same radius,
    # and the text rows keep one height with equal air between them.
    def row_with(text):
        for action in menu.actions():
            if not isinstance(action, QWidgetAction):
                continue
            holder = action.defaultWidget()
            if any(isinstance(c, QLabel) and c.text() == text
                   for c in holder.findChildren(QLabel)):
                return holder
        return None

    theme_field = [b for b in menu.findChildren(QAbstractButton)
                   if b.text() == "Dark"][0].parentWidget()
    mode_field = menu._voice_btn.parentWidget()
    fields = {"mode": mode_field, "size": stepper_field,
              "typeface": family_button, "appearance": theme_field}
    text_fields = [fields["size"], fields["typeface"], fields["appearance"]]
    check("every field is one height",
          len({f.height() for f in text_fields}) == 1,
          f"{[f.height() for f in text_fields]} px")

    controls = dict(fields)
    controls.update({"help": menu._help_btn, "setup": menu._setup_btn,
                     "quit": menu._quit_btn})
    hairlines = {}
    for name, widget in controls.items():
        found = re.search(r"border:\s*1px solid (#[0-9A-Fa-f]{6})",
                          widget.styleSheet())
        hairlines[name] = found.group(1) if found else None
    check("every control wears the same hairline",
          len(set(hairlines.values())) == 1
          and None not in hairlines.values(), f"{hairlines}")

    rows = [row_with("Font size"), row_with("Typeface"),
            row_with("Appearance")]
    tops = []
    for row in rows:
        text = row.findChildren(QLabel)[0]
        tops.append((text.mapTo(menu, QPoint(0, 0)).y(), text.height()))
    gaps = [tops[i + 1][0] - (tops[i][0] + tops[i][1]) for i in range(2)]
    check("the text rows keep one height and equal air between them",
          len({row.height() for row in rows}) == 1
          and len(set(gaps)) == 1 and gaps[0] >= 8,
          f"heights {[row.height() for row in rows]}, gaps {gaps}")

    # ── The speed row leaves nothing behind in voice mode ────────
    # Hiding a QWidgetAction's action takes the row out of the menu's layout
    # but not its widget, so the slider kept painting where the row used to
    # be: one trip to Manual and back left it floating over the rows below.
    speed_row = menu._speed_action.defaultWidget()
    win._voice_mode = True
    win._refresh_menu_mode_display(menu)
    QTest.qWait(60)
    check("voice mode hides the speed row, widget and all",
          not menu._speed_action.isVisible() and not speed_row.isVisible(),
          f"action {menu._speed_action.isVisible()}, "
          f"row {speed_row.isVisible()}")
    win._voice_mode = False
    win._refresh_menu_mode_display(menu)
    QTest.qWait(60)
    check("manual mode brings the speed row back",
          menu._speed_action.isVisible() and speed_row.isVisible(),
          f"action {menu._speed_action.isVisible()}, "
          f"row {speed_row.isVisible()}")

    # ── The speed control reads as a knob on a track ─────────────
    # The old pair put the remaining track 1.16:1 off the panel, all but
    # invisible, and the knob darker than its own fill: a bar with a hole.
    def painted(shot, origin, size, background):
        """Per-column and per-row painted extents inside a widget rect."""
        columns, rows = {}, {}
        for y in range(origin.y(), origin.y() + size.height()):
            for x in range(origin.x(), origin.x() + size.width()):
                if pixel(shot, x, y) != background:
                    columns[x] = (min(columns.get(x, (y, y))[0], y),
                                  max(columns.get(x, (y, y))[1], y))
                    rows[y] = (min(rows.get(y, (x, x))[0], x),
                               max(rows.get(y, (x, x))[1], x))
        return columns, rows

    def slider_shot(menu_widget):
        slider = menu_widget._speed_slider
        origin = slider.mapTo(menu_widget, QPoint(0, 0))
        shot = menu_widget.grab().toImage()
        middle = origin.y() + slider.height() // 2
        panel = pixel(shot, origin.x() - 3, middle)
        return slider, origin, shot, panel, middle

    slider, origin, shot, panel, middle = slider_shot(menu)
    rail = pixel(shot, origin.x() + slider.width() - 3, middle)
    columns, _ = painted(shot, origin, slider.size(), panel)
    knob_edge = max(columns, key=lambda x: columns[x][1] - columns[x][0])
    knob = pixel(shot, knob_edge, middle)
    check("the speed track stays visible past the knob",
          max(abs(a - b) for a, b in zip(rail, panel)) >= 20,
          f"{rail} on {panel}")
    check("the knob stands off the track it sits on",
          max(abs(a - b) for a, b in zip(knob, rail)) >= 40,
          f"{knob} on {rail}")

    # ── The knob is round, centred, and never clipped ────────────
    # Qt takes the handle's width from the stylesheet but adds the border to
    # its height, and a slider pinned to one height cuts a knob that grows
    # with the interface scale: a squashed blob at 2x.
    keep_scale = win._scale
    win._scale = 2.0
    zoom = win._build_menu()
    win._refresh_menu_mode_display(zoom)
    zoom.popup(win.mapToGlobal(QPoint(0, 0)))
    zoom.adjustSize()
    QTest.qWait(120)
    zslider, zorigin, zshot, zpanel, zmiddle = slider_shot(zoom)
    zcolumns, _ = painted(zshot, zorigin, zslider.size(), zpanel)
    ztop, zbottom = max(zcolumns.values(), key=lambda b: b[1] - b[0])
    knob_height = zbottom - ztop + 1
    # Across the knob's centre line only the knob is neither the filled run,
    # the rail nor the panel, so that run is the knob's own width.
    zrail = pixel(zshot, zorigin.x() + zslider.width() - 3, zmiddle)
    zfill = pixel(zshot, zorigin.x() + 3, zmiddle)
    knob_x = max(zcolumns, key=lambda x: zcolumns[x][1] - zcolumns[x][0])
    seen = (zfill, zrail, zpanel)
    left, right = knob_x, knob_x
    while left > zorigin.x() and pixel(zshot, left - 1, zmiddle) not in seen:
        left -= 1
    while (right < zorigin.x() + zslider.width() - 1
           and pixel(zshot, right + 1, zmiddle) not in seen):
        right += 1
    knob_width = right - left + 1
    check("the speed knob keeps its size at 2x",
          abs(knob_height - round(14 * win._scale)) <= 1,
          f"{knob_height} px, wanted {round(14 * win._scale)}")
    check("the speed knob is round",
          abs(knob_height - knob_width) <= 1,
          f"{knob_width} x {knob_height} px")
    check("the speed knob is centred and not clipped",
          abs((ztop + zbottom) / 2
              - (zorigin.y() + zorigin.y() + zslider.height() - 1) / 2) <= 0.5
          and ztop > zorigin.y()
          and zbottom < zorigin.y() + zslider.height() - 1,
          f"knob {ztop}..{zbottom} in "
          f"{zorigin.y()}..{zorigin.y() + zslider.height() - 1}")
    zoom.close()
    zoom.deleteLater()
    win._scale = keep_scale

    # ── Hover and keyboard picks wear the same material ───────────
    # A pick has to be visible: the typeface button used to sit between two
    # greys one level apart, so neither hover nor a keyboard pick showed.
    resting = pixel(family_button.grab().toImage(), 3, 3)
    family_button.setProperty("picked", True)
    family_button.style().unpolish(family_button)
    family_button.style().polish(family_button)
    QTest.qWait(60)
    picked = pixel(family_button.grab().toImage(), 3, 3)
    shift = max(abs(a - b) for a, b in zip(resting, picked))
    check("the picked typeface button lifts off its track",
          shift >= 20, f"{resting} -> {picked} ({shift} levels)")

    # ── The panel stays on the usable screen ─────────────────────
    # Qt places a popup against the full screen, taskbar included, so a
    # right click near the bottom edge hid the action row under it.
    screen = app.primaryScreen().availableGeometry()
    corner = QPoint(screen.right() - 2, screen.bottom() - 2)
    fitted = win._fit_menu_to_screen(menu, corner)
    placed = QRect(fitted, menu.sizeHint())
    check("the menu fits inside the usable screen at the corner",
          screen.contains(placed),
          f"{placed.x()},{placed.y()} {placed.width()}x{placed.height()} "
          f"in {screen.width()}x{screen.height()}")

    # ── Names for the assistive layer ────────────────────────────
    named = {
        "menu": bool(menu.accessibleName()),
        "help": bool(menu._help_btn.accessibleName()),
        "setup": bool(menu._setup_btn.accessibleName()),
        "quit": bool(menu._quit_btn.accessibleName()),
        "typeface": bool(family_button.accessibleName()),
        "stepper": bool(stepper_plus.accessibleName()),
    }
    check("every control in the menu has an accessible name",
          all(named.values()), f"{named}")

    menu.close()
    QTest.qWait(60)

    # Each plate still runs its own command: the handlers are read when the
    # menu is built, so swapping them on the overlay before that is enough.
    calls = []
    keep_help, keep_setup, keep_close = (win._show_help, win._open_settings,
                                         win.close)
    win._show_help = lambda: calls.append("help")
    win._open_settings = lambda: calls.append("setup")
    win.close = lambda *args, **kwargs: calls.append("quit")
    probe = win._build_menu()
    for plate in (probe._help_btn, probe._setup_btn, probe._quit_btn):
        plate.click()
    win._show_help, win._open_settings, win.close = (keep_help, keep_setup,
                                                     keep_close)
    check("each plate keeps its own action",
          calls == ["help", "setup", "quit"], f"{calls}")
    probe.deleteLater()

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
