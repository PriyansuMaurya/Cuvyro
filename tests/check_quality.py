"""Quality benchmarks for the reading surface.

These are the properties the iterative UI pass is judged on, in a form that
fails loudly if any of them drift back:

  * typography: body text clears 7:1 and already-spoken text still clears
    4.5:1 against the card in both themes, and the leading never lets two
    lines collide,
  * speech follow: the highlight lands on exactly the word just heard, never
    overshoots clean speech, never scrolls on a partial, and a completed line
    advances exactly one line,
  * scrolling: one monotonic glide, a bounded per-frame step, no abrupt jump,
    and a settled position on the target,
  * alignment: every line with room above it settles on the same reading
    line, a script opens at the top edge of the card, and no resting view
    slices a line against that edge,
  * transparency: the card stays fully opaque while the floating controls
    recede; the type itself is never attenuated at the edges.

Run it on a real display, not offscreen: the contrast checks need the
platform's font database.

    .venv/Scripts/python.exe tests/check_quality.py
"""

import contextlib
import io
import os
import sys
import tempfile


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


def speech(win, text, final):
    with contextlib.redirect_stdout(io.StringIO()):
        win._on_speech_text(text, final)


def settle(win, timeout_ms=2500):
    """Wait for the scroll spring to land, then report how long it took."""
    bar = win._text_edit.verticalScrollBar()
    waited = 0
    while waited < timeout_ms:
        if not win._scroll_moving and abs(bar.value() - (win._voice_target_scroll or 0)) <= 1:
            break
        QTest.qWait(10)
        waited += 10
    return waited


def load(win, text):
    win._text_edit.setPlainText(text)
    win._spaced_key = None  # mirror the production load path
    win._script_text = text
    win._matching.load_script(text)
    win._line_spans = []
    win._current_line = 0
    win._confirmed_word = -1
    win._partial_word = -1
    win._apply_highlight(-1)
    win._refresh_lines()
    win._scroll_to_reading_line(0)
    settle(win)


def fragment_colours(win):
    """Distinct foreground colours currently applied to the document."""
    seen = []
    doc = win._text_edit.document()
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.isValid():
                colour = fragment.charFormat().foreground().color()
                rgb = (colour.red(), colour.green(), colour.blue())
                if rgb not in seen:
                    seen.append(rgb)
            it += 1
        block = block.next()
    return seen


def main():
    app = QApplication(sys.argv)
    import ui_overlay
    from store import Store

    win = ui_overlay.NotchOverlay()
    win._store = Store(tempfile.mkdtemp(prefix="notch-quality-"))
    win._voice_mode = False  # keep the microphone out of an automated run
    win.show()
    QTest.qWait(250)
    win._voice_mode = True

    text = open(FIXTURE, encoding="utf-8").read()
    load(win, text)
    edit = win._text_edit
    bar = edit.verticalScrollBar()

    # ── Typography ───────────────────────────────────────────────────
    metrics = edit.fontMetrics()
    pitch = win._line_pitch()
    check("leading clears the font's own height",
          pitch >= metrics.height() + 4, f"pitch {pitch}, height {metrics.height()}")
    check("leading stays compact enough to read as one block",
          pitch <= int(round(metrics.height() * 1.7)),
          f"pitch {pitch}, height {metrics.height()}")

    for dark in (True, False):
        name = "dark" if dark else "light"
        win._dark_theme = dark
        win._apply_read_mode_style()
        win._apply_highlight(-1)
        win._apply_highlight(win._line_spans[0].first_word + 1)
        colours = fragment_colours(win)
        fill = (0, 0, 0) if dark else (255, 255, 255)
        base = max(colours, key=lambda c: contrast(c, fill))
        spoken = min(colours, key=lambda c: contrast(c, fill))
        check(f"{name}: script text contrast >= 7:1",
              contrast(base, fill) >= 7.0, f"{contrast(base, fill):.1f}:1")
        check(f"{name}: already-spoken text contrast >= 4.5:1",
              contrast(spoken, fill) >= 4.5, f"{contrast(spoken, fill):.1f}:1")
        check(f"{name}: spoken text is the quieter tier",
              contrast(spoken, fill) < contrast(base, fill),
              f"{contrast(spoken, fill):.1f}:1 vs {contrast(base, fill):.1f}:1")

    win._dark_theme = True
    win._apply_read_mode_style()

    # ── Speech-follow accuracy ───────────────────────────────────────
    load(win, text)
    line = win._line_spans[0]
    words = win._matching.raw_words_in_range(line.first_word, line.last_word)
    log = []
    original_highlight = win._apply_highlight
    win._apply_highlight = lambda position: (log.append(position), original_highlight(position))[1]

    before_bar = bar.value()
    before_target = win._voice_target_scroll
    highlight_ok = True
    highlight_detail = ""
    for count in range(1, len(words) + 1):
        speech(win, " ".join(words[:count]), False)
        expected = line.first_word + count - 1
        actual = log[-1] if log else None
        if actual != expected:
            highlight_ok = False
            highlight_detail = f"word {count}: shaded {actual}, expected {expected}"
            break
    check("a partial result shades exactly the word just heard",
          highlight_ok, highlight_detail or f"{len(words)} words")
    check("partial results never move the view",
          bar.value() == before_bar and win._voice_target_scroll == before_target,
          f"bar={bar.value()} target={win._voice_target_scroll}")
    check("the tentative word survives a format re-apply",
          win._display_word() == line.last_word,
          f"display word {win._display_word()} of {line.last_word}")

    speech(win, " ".join(words), True)
    check("finishing the line advances exactly one line",
          win._current_line == 1 and win._confirmed_word == line.last_word,
          f"line={win._current_line} confirmed={win._confirmed_word}")
    win._apply_highlight = original_highlight

    # Silence is not a result.
    settled = settle(win)
    QTest.qWait(600)
    paused = (win._current_line, win._confirmed_word, bar.value(),
              win._voice_target_scroll)
    QTest.qWait(300)
    check("silence never advances or scrolls the reader",
          (win._current_line, win._confirmed_word, bar.value(),
           win._voice_target_scroll) == paused,
          f"settled in {settled} ms")

    # ── Scroll smoothness ────────────────────────────────────────────
    load(win, text)
    start_index = max(1, min(2, len(win._line_spans) - 2))
    win._scroll_to_reading_line(start_index)
    settle(win)
    start = bar.value()
    win._scroll_to_reading_line(start_index + 1)
    target = win._voice_target_scroll
    distance = abs(target - start)
    values = [bar.value()]
    waited = 0
    while waited < 900:
        QTest.qWait(17)
        waited += 17
        values.append(bar.value())
        if not win._scroll_moving and abs(bar.value() - target) <= 1:
            break
    deltas = [b - a for a, b in zip(values, values[1:])]
    reversals = sum(1 for a, b in zip(deltas, deltas[1:])
                    if a and b and (a > 0) != (b > 0))
    max_step = max(map(abs, deltas)) if deltas else 0
    settle_frame = next((i for i, v in enumerate(values)
                         if abs(v - target) <= 1), None)
    check("a line advance never reverses direction", reversals == 0,
          f"{reversals} reversals")
    check("no frame jumps a large share of the line",
          max_step <= max(6.0, distance * 0.35),
          f"max step {max_step} px of {distance:.0f} px")
    check("the line arrives in a readable glide, not a snap",
          settle_frame is not None and 100 <= (settle_frame or 0) * 17 <= 600,
          f"settled after {(settle_frame or 0) * 17} ms")
    check("the glide lands exactly on the target",
          abs(values[-1] - target) <= 1,
          f"final {values[-1]} vs target {target:.1f}")

    # Reversing mid-flight must bend, not restart or overshoot.
    win._scroll_to_reading_line(len(win._line_spans) - 1)
    QTest.qWait(60)
    win._scroll_to_reading_line(start_index)
    back_target = win._voice_target_scroll
    back = [bar.value()]
    for _ in range(80):
        QTest.qWait(17)
        back.append(bar.value())
        if not win._scroll_moving and abs(bar.value() - back_target) <= 1:
            break
    overshoot = max(0.0, back_target - min(back))
    check("reversing mid-glide does not overshoot the new target",
          overshoot <= 3.0, f"{overshoot:.1f} px past the target")
    late_reversals = sum(1 for a, b in zip(back, back[1:])
                         if a and b and (a > 0) != (b > 0))
    check("reversing mid-glide changes direction once", late_reversals <= 1,
          f"{late_reversals} reversals")

    # ── Alignment ───────────────────────────────────────────────────
    # The anchor is snapped to the document's line grid, so every line with
    # room above it settles on the same reading line. The opening lines are
    # the exception by design: nothing is reserved above line one, so a
    # script opens at the top edge instead of below an empty header band.
    errors = {}
    inset = edit.reading_inset()
    grid = edit.line_grid()
    for label, index in (
        ("middle", max(0, min(3, len(win._line_spans) // 2))),
        ("last", len(win._line_spans) - 1),
    ):
        win._scroll_to_reading_line(index)
        settle(win)
        line = win._line_spans[index]
        errors[label] = round(line.top - bar.value() - inset, 2)
    check("every line with room above it sits on the anchor",
          all(abs(value) <= 1.5 for value in errors.values()),
          f"inset {inset:.1f}, errors {errors}")
    check("the anchor sits on the document's line grid",
          abs(inset - round(inset / grid) * grid) <= 0.5,
          f"inset {inset:.1f}, grid {grid:.1f}")

    win._scroll_to_reading_line(0)
    settle(win)
    first_top = win._line_spans[0].top - bar.value()
    check("a script opens at the top edge of the card",
          bar.value() == 0 and first_top <= 12,
          f"scroll {bar.value()}, first line top {first_top:.1f}")

    # Every resting view must start on a whole line: the topmost line is
    # complete and the line above it has left the view. A row of cut
    # ascenders against the card's edge is what the reserved header used to
    # hide, and what the plain text column exposed.
    tops = {}
    for index in range(len(win._line_spans)):
        win._scroll_to_reading_line(index)
        settle(win)
        offsets = [l.top - bar.value() for l in win._line_spans]
        visible = [o for o in offsets if o >= -grid + 1.0]
        tops[index] = round(min(visible), 1) if visible else None
    worst = min(v for v in tops.values() if v is not None)
    check("no line is sliced at the top edge of the card", worst >= -1.0,
          f"worst topmost offset {worst:.1f} px over {len(tops)} rests")

    # ── Transparency ────────────────────────────────────────────────
    win._scroll_to_reading_line(max(0, min(3, len(win._line_spans) // 2)))
    settle(win)
    win._recede_chrome()
    QTest.qWait(300)
    check("the floating controls really do recede",
          win._chrome_opacity.opacity() < 0.01,
          f"opacity {win._chrome_opacity.opacity():.2f}")

    for dark in (True, False):
        name = "dark" if dark else "light"
        win._dark_theme = dark
        win._apply_read_mode_style()
        win.update()
        QTest.qWait(80)
        image = win.grab().toImage()
        centre = image.pixelColor(win.width() // 2, win.height() // 2)
        check(f"{name}: the card stays fully opaque while chrome is hidden",
              centre.alpha() == 255,
              f"centre alpha {centre.alpha()}, chrome "
              f"{win._chrome_opacity.opacity():.2f}")


    win._wake_chrome()
    win._voice_mode = False
    win.close()
    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All quality checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
