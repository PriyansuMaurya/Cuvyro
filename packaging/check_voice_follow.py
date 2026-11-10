"""Voice-follow checks for the line-based teleprompter.

Drives the reader with synthetic partial and final speech results (no
microphone) and asserts the behaviour the teleprompter promises:

  * a line is held still while it is being spoken, through pauses,
  * only a confirmed result that reaches the line's last word advances it,
  * advancement is by rendered line, never by word; when one result confirms
    several lines the reader catches up one line at a time,
  * line breaks are rebuilt from the rendered layout when the font size,
    font family or viewport width changes, without losing the read position.

Run headless:

    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe packaging/check_voice_follow.py
"""

import contextlib
import io
import os
import sys
import tempfile

from PyQt5.QtGui import QFont
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


def quiet_speech(win, text, is_final):
    """Send a recogniser result without its console log polluting the report."""
    with contextlib.redirect_stdout(io.StringIO()):
        win._on_speech_text(text, is_final)


def line_words(win, line_index):
    line = win._line_spans[line_index]
    return win._matching.raw_words_in_range(line.first_word, line.last_word)


def settle(win, frames=120):
    for _ in range(frames):
        win._tick()


def load(win, text, family="Segoe UI", size=12):
    """Load a script the way the reader does, at a known typography."""
    win._font_family = family
    win._font_size = size
    win._text_edit.setFont(QFont(family, size))
    win._text_edit.setPlainText(text)
    win._script_text = text
    win._matching.load_script(text)
    win._line_spans = []
    win._current_line = 0
    win._confirmed_word = -1
    win._partial_word = -1
    win._apply_highlight(-1)
    win._refresh_lines()
    win._text_edit.verticalScrollBar().setValue(0)
    win._scroll_to_reading_line(0)
    settle(win)


def coverage_ok(win):
    """Every script word is on exactly one rendered line, in reading order."""
    if not win._line_spans or not win._matching.word_count:
        return False
    if win._line_spans[0].first_word != 0:
        return False
    for previous, line in zip(win._line_spans, win._line_spans[1:]):
        if line.first_word != previous.last_word + 1:
            return False
    return win._line_spans[-1].last_word == win._matching.word_count - 1


def line_holding(win, word):
    line = win._line_spans[win._current_line]
    return line.first_word <= word <= line.last_word


def position_kept(win, word):
    """Reader is on the anchor word, or just past its line because it ended."""
    if line_holding(win, word):
        return True
    if win._current_line == 0:
        return False
    previous = win._line_spans[win._current_line - 1]
    return (previous.first_word <= word <= previous.last_word
            and previous.last_word <= win._confirmed_word)


def target_matches_line(win):
    line = win._line_spans[win._current_line]
    bar = win._text_edit.verticalScrollBar()
    expected = max(0.0, min(float(bar.maximum()),
                            line.top - win._text_edit.document().documentMargin()))
    return abs((win._voice_target_scroll or 0.0) - expected) <= 0.5


def main():
    app = QApplication(sys.argv)
    import ui_overlay
    from store import Store

    tmp = tempfile.mkdtemp(prefix="notch-voice-check-")
    win = ui_overlay.NotchOverlay()
    win._store = Store(tmp)
    win._voice_mode = False  # keep the microphone out of an automated run
    win.show()
    QTest.qWait(200)
    win._voice_mode = True

    base = open(FIXTURE, encoding="utf-8").read()
    script = (base + "\n") * 6
    load(win, script)

    # ── Rendered line map ────────────────────────────────────────────
    check("line map covers the script in reading order", coverage_ok(win),
          f"{len(win._line_spans)} lines, {win._matching.word_count} words")
    check("line tops increase down the page",
          all(b.top > a.top for a, b in zip(win._line_spans, win._line_spans[1:])))
    check("reader starts on the first line",
          win._current_line == 0 and win._confirmed_word == -1,
          f"line={win._current_line} confirmed={win._confirmed_word}")

    # Silence alone must never move the reader.
    settle(win, 60)
    QTest.qWait(400)
    check("the reader never advances without confirmed speech",
          win._current_line == 0 and win._confirmed_word == -1,
          f"line={win._current_line} confirmed={win._confirmed_word}")

    # ── Partials hold the line, even when they contain all of it ─────
    settle(win)
    before_bar = win._text_edit.verticalScrollBar().value()
    before_target = win._voice_target_scroll
    words = line_words(win, 0)
    for count in range(1, len(words) + 1):
        quiet_speech(win, " ".join(words[:count]), False)
        check_inside = win._current_line == 0 and win._confirmed_word == -1
        if not check_inside:
            break
    check("partial results never advance the reader",
          win._current_line == 0 and win._confirmed_word == -1,
          f"line={win._current_line} confirmed={win._confirmed_word}")
    settle(win)
    check("partial results never scroll the view",
          win._text_edit.verticalScrollBar().value() == before_bar
          and win._voice_target_scroll == before_target,
          f"bar={win._text_edit.verticalScrollBar().value()} target={win._voice_target_scroll}")

    # ── Mid-line pause ───────────────────────────────────────────────
    half = max(1, len(words) // 2)
    quiet_speech(win, " ".join(words[:half]), True)
    check("a mid-line final does not advance the line",
          win._current_line == 0 and win._confirmed_word < win._line_spans[0].last_word,
          f"line={win._current_line} confirmed={win._confirmed_word}")
    settle(win, 240)
    paused_bar = win._text_edit.verticalScrollBar().value()
    paused_target = win._voice_target_scroll
    quiet_speech(win, " ".join(words[:half]), True)  # recogniser repeats itself
    settle(win, 240)
    check("a pause mid-line leaves the line exactly in place",
          win._current_line == 0
          and win._text_edit.verticalScrollBar().value() == paused_bar
          and win._voice_target_scroll == paused_target,
          f"bar={win._text_edit.verticalScrollBar().value()} target={win._voice_target_scroll}")

    # ── Finish the line: one-line advance ────────────────────────────
    quiet_speech(win, " ".join(words), True)
    check("finishing the line advances exactly one line",
          win._current_line == 1 and win._confirmed_word == win._line_spans[0].last_word,
          f"line={win._current_line} confirmed={win._confirmed_word}")
    check("the view re-anchors to the new line", target_matches_line(win),
          f"target={win._voice_target_scroll}")

    # A wrong last word must not be treated as confirmation.
    load(win, script)
    words = line_words(win, 0)
    quiet_speech(win, " ".join(words[:-1] + ["banana"]), True)
    check("a mismatched last word does not finish the line",
          win._current_line == 0 and win._confirmed_word < win._line_spans[0].last_word,
          f"line={win._current_line} confirmed={win._confirmed_word}")

    # ── Slow speech, word by word ────────────────────────────────────
    load(win, script)
    slow_ok = True
    slow_detail = ""
    for line_index in range(3):
        words = line_words(win, line_index)
        for position, word in enumerate(words):
            expected_line = line_index + 1 if position == len(words) - 1 else line_index
            QTest.qWait(60)  # deliberate, slow delivery
            quiet_speech(win, word, True)
            actual_line = win._current_line
            if actual_line != expected_line:
                slow_ok = False
                slow_detail = f"line {line_index} word {position}: line={actual_line}"
                break
        if not slow_ok:
            break
    check("slow word-by-word speech advances only on each line's last word",
          slow_ok, slow_detail)

    # ── Normal speech, short phrases with partials between ──────────
    load(win, script)
    normal_ok = True
    normal_detail = ""
    for line_index in range(3):
        words = line_words(win, line_index)
        cursor = 0
        chunk = 3
        while cursor < len(words):
            end = min(len(words), cursor + chunk)
            piece = words[cursor:end]
            quiet_speech(win, " ".join(piece), False)  # live partial
            expected_line = line_index + 1 if end == len(words) else line_index
            QTest.qWait(30)
            quiet_speech(win, " ".join(piece), True)   # utterance final
            if win._current_line != expected_line:
                normal_ok = False
                normal_detail = f"line {line_index} words {cursor}:{end}: line={win._current_line}"
                break
            cursor = end
            chunk = 2 + (cursor % 3)
        if not normal_ok:
            break
    check("normal phrase delivery advances once per completed line", normal_ok, normal_detail)

    # ── One final that covers several lines ─────────────────────────
    load(win, script)
    first = line_words(win, 0)
    second = line_words(win, 1)
    quiet_speech(win, " ".join(first + second), True)
    confirmed = win._confirmed_word
    check("a multi-line final confirms both lines",
          win._current_line == 1 and confirmed == win._line_spans[1].last_word,
          f"line={win._current_line} confirmed={confirmed}")
    check("the first line is left only after its last word",
          win._line_spans[0].last_word <= confirmed and target_matches_line(win))
    # The rest of the confirmed lines arrive one at a time, not in a jump.
    QTest.qWait(80)
    check("the reader is still one line in during catch-up",
          win._current_line == 1 and win._confirmed_word == confirmed,
          f"line={win._current_line}")
    QTest.qWait(300)
    check("a multi-line final catches up one line at a time",
          win._current_line == 2 and win._confirmed_word == confirmed,
          f"line={win._current_line} confirmed={win._confirmed_word}")
    check("a multi-line final re-anchors the view", target_matches_line(win))

    # ── Font size and family reflow ─────────────────────────────────
    load(win, script)
    # Anchor on a line's first word so the position is not already a finished
    # line (which the reader is allowed to step past).
    win._set_reading_position(win._line_spans[20].first_word)
    word = win._confirmed_word
    lines_at = {}
    font_ok = True
    font_detail = ""
    families = [family for _, family in win.FONT_OPTIONS]
    smallest, largest = win.COMPACT_FONT, 24
    for size in range(smallest, largest + 1):
        for family in families:
            win._font_family = family
            win._font_size = size
            win._apply_typography()
            if not position_kept(win, word) or not target_matches_line(win):
                font_ok = False
                font_detail = f"{family} {size}pt: line={win._current_line} confirmed={word}"
                break
            lines_at[size] = len(win._line_spans)
        if not font_ok:
            break
    check("reflow at every font size and family keeps the read position",
          font_ok,
          font_detail if font_detail
          else f"{len(lines_at)} sizes x {len(families)} families")
    check("font size changes recalculate the rendered line breaks",
          lines_at.get(smallest) != lines_at.get(largest),
          f"{smallest}pt={lines_at.get(smallest)} lines, "
          f"{largest}pt={lines_at.get(largest)} lines")

    # The line as reflowed is the unit that must be finished.
    win._font_family = "Segoe UI"
    win._font_size = 12
    win._apply_typography()
    win._set_reading_position(win._line_spans[win._current_line].first_word)
    words = line_words(win, win._current_line)
    quiet_speech(win, " ".join(words[:-1]), True)
    held_line = win._current_line
    quiet_speech(win, words[-1], True)
    check("the reflowed line still advances only after its last word",
          held_line == win._current_line - 1 and win._confirmed_word >= win._line_spans[held_line].last_word,
          f"{held_line} -> {win._current_line}")

    # ── Width reflow ────────────────────────────────────────────────
    load(win, script)
    win._set_reading_position(win._line_spans[20].first_word)
    word = win._confirmed_word
    win.setMinimumSize(1, 1)
    width_ok = True
    width_detail = ""
    widths = [120, 180, 240, 340, 480]
    counts = []
    for width in widths:
        win.resize(width, 122)
        QTest.qWait(220)  # let the resize debounce rebuild the lines
        counts.append(len(win._line_spans))
        if not position_kept(win, word) or not target_matches_line(win):
            width_ok = False
            width_detail = f"{width}px: line={win._current_line} confirmed={word}"
            break
    check("width reflow keeps the read position", width_ok, width_detail)
    check("width changes recalculate the rendered line breaks",
          len(set(counts)) > 1, f"line counts={counts}")

    # Finish the line at the final width and make sure the gate still holds.
    win._set_reading_position(win._line_spans[win._current_line].first_word)
    words = line_words(win, win._current_line)
    quiet_speech(win, " ".join(words[:-1]), True)
    held_line = win._current_line
    quiet_speech(win, words[-1], True)
    check("the line advances only after its last word after a width change",
          held_line == win._current_line - 1,
          f"{held_line} -> {win._current_line}")

    win._voice_mode = False
    win.close()
    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All voice-follow checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
