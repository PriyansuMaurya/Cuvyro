"""Voice-follow checks for the line-based teleprompter.

Drives the reader with synthetic partial and final speech results (no
microphone) and asserts the behaviour the teleprompter promises:

  * a line is held still while it is being spoken, through pauses,
  * only a confirmed result that reaches the line's last word advances it,
  * advancement is by rendered line, never by word; when one result confirms
    several lines the reader catches up one line at a time,
  * about 30% of the words on screen stay behind the line being read, at
    every font size, window size and speaking rhythm; the start and the end
    of a script are exempt by design,
  * line breaks are rebuilt from the rendered layout when the font size,
    font family or viewport width changes, without losing the read position.

Run headless:

    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_voice_follow.py
"""

import contextlib
import io
import os
import sys
import tempfile
import time

from PyQt5.QtGui import QFont
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
    win._stalled_finals = 0
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
    """The target is the word-balanced anchor for the line being read."""
    expected = win._scroll_target_for_line(win._current_line)
    return abs((win._voice_target_scroll or 0.0) - expected) <= 0.5


def land(win, timeout_ms=2500):
    """Wait for the spring to settle, then read the resting bar value."""
    bar = win._text_edit.verticalScrollBar()
    waited = 0
    while waited < timeout_ms:
        if (not win._scroll_moving
                and abs(bar.value() - (win._voice_target_scroll or 0)) <= 1):
            break
        QTest.qWait(10)
        waited += 10
    return bar.value()


def word_split(win, index):
    """(words above the reading line, visible words, fullest line's words).

    The third value is the slack a whole-line anchor is allowed: it can only
    move a line at a time, so the closest split it can reach is one line's
    worth of words away from the exact share.
    """
    edit = win._text_edit
    lines = win._line_spans
    height = float(edit.viewport().height())
    top = float(edit.verticalScrollBar().value())
    above = visible = slack = 0
    for j, line in enumerate(lines):
        y = line.top - top
        if y >= height:
            break
        if y + line.height / 2.0 < 0.0:
            continue
        count = line.last_word - line.first_word + 1
        visible += count
        slack = max(slack, count)
        if j < index:
            above += count
        elif j == index == win._current_line:
            # The words of the reading line already spoken are behind the
            # speaker too, not part of the upcoming share.
            position = win._display_word()
            if position >= line.first_word:
                above += min(count, position - line.first_word + 1)
    return above, visible, slack


def share_ok(win, above, visible, slack):
    return abs(above - win.READING_SHARE * visible) <= slack


def main():
    # The first-run guide is modal. A machine that has never run Notch (a
    # runner, a new laptop) would block on it instead of starting, so the
    # check states the machine it describes. check_note_clearing, the
    # robustness check and the upgrade check do the same.
    from PyQt5.QtCore import QSettings
    QSettings("Notch", "Notch").setValue("guide_seen", True)
    app = QApplication(sys.argv)
    from notch.ui import overlay as ui_overlay
    from notch.audio.worker import AudioWorker
    from notch.audio.matching import MatchingEngine
    from notch.storage.store import Store

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

    # ── Recogniser cadence ───────────────────────────────────────────
    # The dominant fixed delay is how long audio is batched before the
    # recogniser is asked for a result. 100 ms keeps the live highlight
    # within a syllable of the speaker without loading the model.
    block_ms = AudioWorker.BLOCK_SAMPLES / AudioWorker.SAMPLE_RATE * 1000.0
    silence_ms = AudioWorker()._silence_threshold * block_ms
    check("audio reaches the recogniser in 100 ms blocks",
          block_ms <= 100.0, f"{block_ms:.0f} ms")
    check("the VAD still waits about 0.75 s of silence",
          abs(silence_ms - 750.0) <= block_ms,
          f"{silence_ms:.0f} ms")

    # Silence alone must never move the reader.
    settle(win, 60)
    QTest.qWait(400)
    check("the reader never advances without confirmed speech",
          win._current_line == 0 and win._confirmed_word == -1,
          f"line={win._current_line} confirmed={win._confirmed_word}")

    # ── Partials hold the line until its last word ───────────────────
    settle(win)
    before_bar = win._text_edit.verticalScrollBar().value()
    before_target = win._voice_target_scroll
    words = line_words(win, 0)
    for count in range(1, len(words)):
        quiet_speech(win, " ".join(words[:count]), False)
        check_inside = win._current_line == 0 and win._confirmed_word == -1
        if not check_inside:
            break
    check("a partial mid-line does not advance the reader",
          win._current_line == 0 and win._confirmed_word == -1,
          f"line={win._current_line} confirmed={win._confirmed_word}")
    settle(win)
    check("a partial mid-line does not scroll the view",
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

    # The step must not wait for the final: a partial that reaches the last
    # word already proves it was spoken, so the scroll starts at the boundary
    # instead of after the recogniser's end-of-sentence pause.
    load(win, script)
    words = line_words(win, 0)
    first_line = win._line_spans[0]
    win._voice_wpm = 60
    win._last_match_pos = 0
    win._last_match_time = time.monotonic() - 0.5
    quiet_speech(win, " ".join(words), False)
    check("a completed line feeds the speed estimate",
          win._voice_wpm > 60, f"60 -> {win._voice_wpm} wpm")
    check("a partial that reaches the last word steps at once",
          win._current_line == 1
          and win._confirmed_word == first_line.last_word,
          f"line={win._current_line} confirmed={win._confirmed_word}")
    check("that step already targets the new line before any final",
          target_matches_line(win), f"target={win._voice_target_scroll}")

    # A wrong last word must not be treated as confirmation.
    load(win, script)
    words = line_words(win, 0)
    quiet_speech(win, " ".join(words[:-1] + ["banana"]), True)
    check("a mismatched last word does not finish the line",
          win._current_line == 0 and win._confirmed_word < win._line_spans[0].last_word,
          f"line={win._current_line} confirmed={win._confirmed_word}")

    # ── Approximate recognition ─────────────────────────────────────
    # Spelling drift and pronunciation variants are normal Vosk output; the
    # fuzzy score has to absorb them without loosening short words into noise.
    engine = MatchingEngine()
    engine.load_script("we need to recognise the harbour plan")
    aligned = engine.align_forward(
        "we need to recognize the harbor plan", 0, engine.word_count)
    check("spelling and pronunciation variants still align",
          aligned is not None and aligned.last_word == engine.word_count - 1,
          f"{aligned} of {engine.word_count}")

    # A skipped clause should resync on a clear phrase, never on one lonely
    # word. The wider search is only allowed with the next-word agreement.
    engine.load_script("one two three four five six seven eight nine ten")
    skipped = engine.align_forward(
        "one seven eight nine ten", 0, engine.word_count, resync_skip=9)
    stray = engine.align_forward(
        "seven", 0, engine.word_count, resync_skip=9)
    check("a skipped clause resyncs on a clear phrase",
          skipped is not None and skipped.last_word == engine.word_count - 1,
          f"{skipped} of {engine.word_count}")
    check("one stray word cannot jump across a skipped clause",
          stray is None, f"{stray}")

    # Scripts print numbers as digits and speakers say them as words. An
    # accented speaker makes that mismatch likelier, not rarer, and a number
    # the matcher cannot see is a sentence it cannot follow.
    engine.load_script("Roughly 60 per cent of the delay came from one supplier.")
    spoken_number = engine.align_forward(
        "roughly sixty per cent", 0, engine.word_count)
    check("a number spoken as a word matches the digits in the script",
          spoken_number is not None and spoken_number.last_word == 3,
          f"{spoken_number} of {engine.word_count}")
    engine.load_script("The council approved 14 of the 19 amendments.")
    ordinal_number = engine.align_forward(
        "the council approved fourteenth of the nineteenth", 0,
        engine.word_count)
    check("an ordinal number still matches the digits in the script",
          ordinal_number is not None and ordinal_number.last_word == 6,
          f"{ordinal_number} of {engine.word_count}")

    # A reader that missed a sentence has to find its place again. One gap is
    # the resync window's job; a whole missed sentence is only reachable by
    # re-anchoring, which is why it needs a clear run of words.
    lost_script = ("The council approved it. "
                   "Then the numbers were checked twice. "
                   "We did not expect delivery to take that long.")
    engine.load_script(lost_script)
    recovered = engine.align_forward(
        "we did not expect delivery to take", 0, engine.word_count,
        resync_skip=9, reanchor=True)
    without = engine.align_forward(
        "we did not expect delivery to take", 0, engine.word_count,
        resync_skip=9)
    check("a missed sentence is out of reach without re-anchoring",
          without is None, f"{without}")
    check("a lost reader re-anchors on a clear phrase",
          recovered is not None and recovered.last_word == 12,
          f"{recovered} of {engine.word_count}")

    # Recovery is the app's promise, not the matcher's. A single final that
    # confirms nothing is ordinary (a cough, a repeated phrase) and must not
    # move the reader; the second in a row means it is lost and may re-anchor.
    load(win, lost_script)
    quiet_speech(win, "we did not expect delivery to take", True)
    QTest.qWait(30)
    stayed = win._confirmed_word
    quiet_speech(win, "we did not expect delivery to take", True)
    check("one unproductive final leaves the reader where it was",
          stayed == -1, f"confirmed={stayed}")
    check("a second unproductive final re-anchors the reader",
          win._confirmed_word == 12, f"confirmed={win._confirmed_word}")

    # ── Latency budget ───────────────────────────────────────────────
    # Recognition itself is the microphone thread's job; this budget covers
    # the UI half of the loop: a result must shade the words and retarget
    # the scroll immediately, not after a timer tick.
    load(win, script)
    sample = line_words(win, 0)
    chunk = " ".join(sample[:max(1, len(sample) // 2)])
    t0 = time.perf_counter()
    for _ in range(100):
        quiet_speech(win, chunk, False)
    partial_ms = (time.perf_counter() - t0) * 1000.0 / 100
    t0 = time.perf_counter()
    for _ in range(100):
        quiet_speech(win, chunk, True)
    final_ms = (time.perf_counter() - t0) * 1000.0 / 100
    check("a partial result updates the reader in under 2 ms",
          partial_ms < 2.0, f"{partial_ms:.3f} ms")
    check("a final result updates the reader in under 2 ms",
          final_ms < 2.0, f"{final_ms:.3f} ms")

    # The speed estimate follows the speaker in both directions, so a slow
    # passage cannot inherit the number from a fast one.
    saved_wpm = win._voice_wpm
    win._voice_wpm = 300
    win._last_match_pos = 0
    win._last_match_time = time.monotonic() - 2.0
    win._update_voice_speed(1, True)
    slowed = win._voice_wpm
    win._voice_wpm = 60
    win._last_match_pos = 0
    win._last_match_time = time.monotonic() - 0.5
    win._update_voice_speed(10, True)
    raised = win._voice_wpm
    win._voice_wpm = saved_wpm
    check("the speed estimate follows slower speech", slowed < 300,
          f"300 -> {slowed} wpm")
    check("the speed estimate follows faster speech", raised > 60,
          f"60 -> {raised} wpm")

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
        # Widths are card widths; the transparent rail adds to the window.
        win.resize(width + win.RAIL_W, 122)
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

    # ── 30 / 70 balance across sizes and windows ────────────────────
    # The share is measured on the words actually on screen, and the anchor
    # has to hold it at every font size and card size, not only at the one
    # the default happens to use.
    win.setMinimumSize(1, 1)
    balance = []
    balance_ok = True
    balance_detail = ""
    measured = 0
    too_small = []
    for size in (8, 10, 12, 16, 24):
        for width, height in ((340, 122), (480, 200), (260, 150)):
            win.resize(width + win.RAIL_W, height)
            QTest.qWait(220)  # let the resize debounce rebuild the lines
            win._font_size = size
            win._apply_typography()
            QTest.qWait(30)
            index = max(1, min(len(win._line_spans) // 2,
                               len(win._line_spans) - 2))
            win._set_reading_position(win._line_spans[index].first_word)
            land(win)
            # At the largest sizes a rendered line is a single word, so
            # adopting its first word as the position completes it and the
            # reader steps on to the next one. Measure where it actually is.
            index = win._current_line
            above, visible, slack = word_split(win, index)
            # A share needs a screenful to be a share. At the largest sizes a
            # short card can hold a single word on a machine whose line height
            # is a little taller than this one's, and two visible words do not
            # describe a 30/70 split of anything.
            if visible < 4:
                too_small.append(f"{size}pt {width}x{height}={visible}w")
                continue
            measured += 1
            balance.append(round(above / visible, 2) if visible else 0.0)
            # A short card fits only a few lines, so a whole-line anchor can
            # only land within one line's words of the exact 30/70 split.
            if not share_ok(win, above, visible, slack):
                balance_ok = False
                balance_detail = (f"{size}pt {width}x{height}: {above}/{visible} "
                                  f"words behind, one line is {slack}")
                break
        if not balance_ok:
            break
    detail = balance_detail or f"{measured} layouts {balance}"
    if too_small:
        detail += f"; too few visible words to measure: {', '.join(too_small)}"
    check("about 30% of the visible words sit behind the reading line",
          balance_ok and measured >= 8, detail)

    # A different speaking rhythm must not move the balance: the anchor only
    # depends on the words, never on how fast they arrive.
    win.resize(340 + win.RAIL_W, 122)
    QTest.qWait(220)
    win._font_size = 12
    win._apply_typography()
    QTest.qWait(30)
    # Start mid-script: the opening is exempt, so the balance is only
    # promised once there are spoken words to put behind the reading line.
    start = min(20, len(win._line_spans) - 4)
    win._set_reading_position(win._line_spans[start].first_word)
    land(win)
    rhythm_ok = True
    rhythm_detail = ""
    for offset in range(3):
        line_index = start + offset
        words = line_words(win, line_index)
        cursor = 0
        chunk = (1, 2, 4)[offset]
        while cursor < len(words):
            end = min(len(words), cursor + chunk)
            piece = words[cursor:end]
            quiet_speech(win, " ".join(piece), False)
            quiet_speech(win, " ".join(piece), True)
            QTest.qWait(40)
            cursor = end
        land(win)
        index = win._current_line
        if index >= len(win._line_spans) - 2:
            break
        above, visible, slack = word_split(win, index)
        if not share_ok(win, above, visible, slack):
            rhythm_ok = False
            rhythm_detail = (f"line {index}: {above}/{visible} words behind, "
                             f"one line is {slack}")
            break
    check("the balance holds at slow and fast delivery",
          rhythm_ok, rhythm_detail)

    # ── The two exceptions ──────────────────────────────────────────
    load(win, script)
    QTest.qWait(30)
    land(win)
    above, visible, _ = word_split(win, 0)
    check("a script opens as pure upcoming text",
          above == 0 and win._text_edit.verticalScrollBar().value() == 0,
          f"{above}/{visible} words behind, "
          f"scroll {win._text_edit.verticalScrollBar().value()}")
    win._scroll_to_reading_line(len(win._line_spans) - 1)
    land(win)
    last = win._line_spans[-1]
    bottom = last.top - win._text_edit.verticalScrollBar().value() + last.height
    check("the end of the script is shown as it ends, not forced to a ratio",
          0 < bottom <= win._text_edit.viewport().height() + 1,
          f"last line bottom {bottom:.1f} of "
          f"{win._text_edit.viewport().height()}")

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
