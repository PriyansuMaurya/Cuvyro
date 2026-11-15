"""Checks for the anticipatory highlight.

The reader used to paint only words the recogniser had already confirmed.
This check guards the layer that paints the next words instead:

  * the learned rate follows faster and slower speech,
  * the lead is bounded by the rendered line and never moves the scroll,
  * a pause freezes the lead instead of letting it run on the clock,
  * a repeated partial, a skipped clause or a backward jump cannot flicker
    or throw the prediction across the script,
  * the profile survives a restart and is only written once speech has
    actually been heard,
  * the accent tier is visible in both themes and is distinguishable from
    the spoken band,
  * one prediction update costs well under a frame.

Run headless:

    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_predictive_highlight.py
"""

import os
import sys
import tempfile
import time

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QTextCursor
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "demo-script.txt")
failures = []


def check(name, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {name}"
          f"{(' — ' + detail) if detail else ''}")
    if not condition:
        failures.append(name)


def relative_luminance(rgb):
    def channel(value):
        value = value / 255.0
        return (value / 12.92 if value <= 0.04045
                else ((value + 0.055) / 1.055) ** 2.4)
    red, green, blue = (channel(value) for value in rgb[:3])
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(foreground, background):
    high = max(relative_luminance(foreground), relative_luminance(background))
    low = min(relative_luminance(foreground), relative_luminance(background))
    return (high + 0.05) / (low + 0.05)


def hex_rgb(colour):
    text = colour.lstrip("#")
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))


def make_predictor(directory):
    from cuvyro.audio.predict import PredictiveHighlighter, ProfileStore
    return PredictiveHighlighter(ProfileStore(directory))


def feed(predictor, first, last, seconds_per_word, start_time):
    """Report word boundaries at a steady pace; return the new clock."""
    now = start_time
    for position in range(first, last + 1):
        now += seconds_per_word
        predictor.observe(position, now, position % 4 == 0)
    return now


def settle_tiers(win):
    """Finish any tier crossfade, so a check sees the resting state.

    The reader fades a tier change over 120 ms on its own clock; a test that
    inspects formats straight after a change would otherwise read the mix
    halfway through the transition, which is a different thing to assert.
    """
    win._advance_tier_fades(time.monotonic() + 1.0)


def tier_at(win, index):
    start, _ = win._matching.word_span(index)
    cursor = QTextCursor(win._text_edit.document())
    cursor.setPosition(start)
    cursor.setPosition(start + 1, QTextCursor.KeepAnchor)
    return cursor.charFormat()


def banded_words_in(win):
    """Script-word indices that carry a background right now."""
    banded = []
    for word in range(win._matching.word_count):
        if tier_at(win, word).background().style() != Qt.NoBrush:
            banded.append(word)
    return banded


def words_in(win, span):
    """Script-word indices whose characters sit inside a character span."""
    if span is None:
        return []
    return [word for word in range(win._matching.word_count)
            if win._matching.word_span(word)[0] >= span[0]
            and win._matching.word_span(word)[1] <= span[1]]


def chroma(rgb):
    return max(rgb) - min(rgb)


def main():
    # The first-run guide is modal. A machine that has never run Cuvyro (a
    # runner, a new laptop) would block on it instead of starting, so the
    # check states the machine it describes. check_note_clearing, the
    # robustness check and the upgrade check do the same.
    from PyQt5.QtCore import QSettings
    QSettings("Cuvyro", "Cuvyro").setValue("guide_seen", True)
    app = QApplication(sys.argv)
    from cuvyro.ui import overlay as ui_overlay
    from cuvyro.storage.store import Store
    from cuvyro.audio.predict import (DEFAULT_WEIGHTS, MAX_LEAD_WORDS, MIN_WPS,
                                     MAX_WPS, PredictiveHighlighter,
                                     ProfileStore)

    script = open(FIXTURE, encoding="utf-8").read()
    words = script.split()

    # ── A session is required before anything is learned or saved ────────
    tmp = tempfile.mkdtemp(prefix="cuvyro-predict-profile-")
    silent = make_predictor(tmp)
    silent.set_script(words)
    silent.observe(4, 2.0, False)
    check("a recogniser result without a speech session learns nothing",
          silent.profile.words == 0 and not os.path.exists(silent._store.path),
          f"words={silent.profile.words}")

    # ── Adaptive rate ───────────────────────────────────────────────────
    directory = tempfile.mkdtemp(prefix="cuvyro-predict-rate-")
    predictor = make_predictor(directory)
    predictor.set_script(words)
    clock = 0.0
    predictor.speech_started(clock)
    clock = feed(predictor, 0, 14, 0.60, clock)  # 100 wpm
    slow_rate = predictor.rate_wps
    clock = feed(predictor, 15, 34, 0.25, clock)   # 240 wpm
    fast_rate = predictor.rate_wps
    check("the rate estimate follows faster speech",
          fast_rate > slow_rate + 0.8,
          f"{slow_rate:.2f} -> {fast_rate:.2f} words/s")
    clock = feed(predictor, 35, 54, 0.65, clock)   # deliberate again
    recovered = predictor.rate_wps
    check("the rate estimate follows slower speech",
          recovered < fast_rate - 0.5,
          f"{fast_rate:.2f} -> {recovered:.2f} words/s")
    check("the learned rate stays inside the supported range",
          MIN_WPS <= predictor.rate_wps <= MAX_WPS,
          f"{predictor.rate_wps:.2f} words/s")

    # ── The lead is bounded by the line and by the clock ────────────────
    line_last = 40
    spoken_end = 34
    lead = predictor.prediction(spoken_end, line_last, clock + 0.4,
                                speaking=True)
    check("a prediction anticipates at least one word",
          lead is not None and lead > spoken_end, f"lead={lead}")
    check("a prediction never crosses the rendered line",
          lead is not None and lead <= line_last,
          f"lead={lead} line_last={line_last}")
    check("a prediction stays within the lookahead cap",
          lead is not None and lead <= spoken_end + MAX_LEAD_WORDS,
          f"lead={lead} cap={spoken_end + MAX_LEAD_WORDS}")
    tight = predictor.prediction(line_last, line_last, clock + 0.4,
                                 speaking=True)
    check("past the last word there is nothing left to anticipate",
          tight is None, f"lead={tight}")

    # ── A pause freezes the lead, then releases it ──────────────────────
    predictor.speech_stopped(clock)
    paused = predictor.prediction(spoken_end, line_last, clock + 0.1,
                                  speaking=False)
    later = predictor.prediction(spoken_end, line_last, clock + 2.5,
                                 speaking=False)
    check("a pause holds the predicted run instead of advancing it",
          paused == later, f"{paused} -> {later}")
    released = predictor.prediction(spoken_end, line_last, clock + 4.2,
                                    speaking=False)
    check("a long silence releases the predicted run", released is None,
          f"lead={released}")

    # ── Repeated partials do not flicker ────────────────────────────────
    fl = make_predictor(tempfile.mkdtemp(prefix="cuvyro-predict-flicker-"))
    fl.set_script(words)
    now = 0.0
    fl.speech_started(now)
    changes = []
    last = None
    for step in range(90):
        now += 0.05
        if step % 6 == 0:  # a recogniser result every 300 ms
            fl.observe(4 + step // 6, now, step % 12 == 0)
        value = fl.prediction(4 + step // 6, 30, now, speaking=True)
        if value != last:
            changes.append((now, value))
            last = value
    decreases = sum(1 for (_, a), (_, b) in zip(changes, changes[1:])
                    if a is not None and b is not None and b < a)
    check("a steady partial stream never makes the lead flicker backwards",
          decreases == 0, f"{len(changes)} changes, {decreases} decreases")
    check("the lead does not repaint excessively",
          len(changes) <= 20, f"{len(changes)} changes in 4.5 s")

    # Repetition of the same result must not advance anything.
    before = fl.prediction(10, 30, now, speaking=True)
    repeated_at = 4 + 89 // 6
    for _ in range(20):
        fl.observe(repeated_at, now, True)
    after = fl.prediction(repeated_at, 30, now, speaking=True)
    check("a repeated result leaves the lead exactly where it was",
          before == after, f"{before} -> {after}")

    # ── Skipped clause and backward jump stay safe ──────────────────────
    jump = make_predictor(tempfile.mkdtemp(prefix="cuvyro-predict-jump-"))
    jump.set_script(words)
    jump.speech_started(0.0)
    jump.observe(5, 2.0, True)
    jump.observe(30, 2.5, True)  # a whole clause the recogniser missed
    jumped_lead = jump.prediction(30, 40, 2.8, speaking=True)
    check("a recovery jump does not inflate the rate past its ceiling",
          jump.rate_wps <= MAX_WPS, f"{jump.rate_wps:.2f} words/s")
    check("a recovery jump still predicts inside its line",
          jumped_lead is None or 30 < jumped_lead <= 40,
          f"lead={jumped_lead}")
    jump.jump(3, 3.0)
    reset_lead = jump.prediction(3, 40, 3.2, speaking=True)
    check("a backward jump restarts the lead near the new position",
          reset_lead is None or reset_lead <= 4,
          f"lead={reset_lead}")

    # ── Profile persistence across launches ─────────────────────────────
    learn = make_predictor(directory)
    clock = 0.0
    learn.set_script(words)
    learn.speech_started(clock)
    clock = feed(learn, 0, 40, 0.38, clock)
    learn.speech_stopped(clock)
    learned_rate = learn.rate_wps
    check("the learned profile really changed",
          abs(learn.profile.wps - 2.4) > 0.05
          or any(abs(a - b) > 0.001 for a, b in
                 zip(learn.profile.timing_weights, DEFAULT_WEIGHTS)),
          f"wps={learn.profile.wps:.2f}")
    check("the profile is written on demand", learn.save(force=True))
    reloaded = make_predictor(directory)
    check("a new launch restores the speaking profile",
          abs(reloaded.profile.wps - learn.profile.wps) < 0.001
          and reloaded.profile.words == learn.profile.words,
          f"wps={reloaded.profile.wps:.2f} words={reloaded.profile.words}")
    check("the learned rate is used by the next launch",
          abs(reloaded.rate_wps - reloaded.profile.wps) < 0.001
          and abs(reloaded.rate_wps - learned_rate) < 0.3,
          f"{reloaded.rate_wps:.2f} words/s, last session {learned_rate:.2f}")

    # A corrupt profile must not take the reader down.
    corrupt = tempfile.mkdtemp(prefix="cuvyro-predict-corrupt-")
    with open(os.path.join(corrupt, "speaking-profile.json"), "w",
              encoding="utf-8") as handle:
        handle.write('{"schema": 1, "wps": "fast", "timing_weights": [1]}')
    safe = make_predictor(corrupt)
    check("a corrupt profile falls back to the defaults",
          MIN_WPS <= safe.profile.wps <= MAX_WPS
          and len(safe.profile.timing_weights) == 5,
          f"wps={safe.profile.wps}")
    # A hand-edited type in place of the weight list must not raise on load.
    with open(os.path.join(corrupt, "speaking-profile.json"), "w",
              encoding="utf-8") as handle:
        handle.write('{"schema": 1, "timing_weights": 7}')
    odd = make_predictor(corrupt)
    check("a non-list weight profile loads the default model",
          odd.profile.timing_weights == list(DEFAULT_WEIGHTS),
          f"weights={odd.profile.timing_weights}")

    # ── Overlay integration ─────────────────────────────────────────────
    ui_tmp = tempfile.mkdtemp(prefix="cuvyro-predict-ui-")
    win = ui_overlay.CuvyroOverlay()
    win._store = Store(ui_tmp)
    win._predictor = PredictiveHighlighter(ProfileStore(ui_tmp))
    win._voice_mode = False
    win.show()
    QTest.qWait(200)
    win._voice_mode = True

    text = (script + "\n") * 3
    win._font_family = "Segoe UI"
    win._font_size = 12
    win._text_edit.setFont(QFont("Segoe UI", 12))
    win._text_edit.setPlainText(text)
    win._script_text = text
    win._matching.load_script(text)
    win._predictor.set_script(win._matching.raw_words)
    win._line_spans = []
    win._current_line = 0
    win._confirmed_word = -1
    win._partial_word = -1
    win._predicted_word = None
    win._predicted_span = None
    win._apply_highlight(-1)
    win._refresh_lines()
    win._text_edit.verticalScrollBar().setValue(0)
    win._scroll_to_reading_line(0)

    line = win._line_spans[0]
    line_words = win._matching.raw_words_in_range(line.first_word,
                                                  line.last_word)
    bar_before = win._text_edit.verticalScrollBar().value()
    target_before = win._voice_target_scroll
    win._on_speaking_changed(True)
    win._on_speech_text(" ".join(line_words[:1]), False)
    win._update_prediction()
    spoken = win._display_word()
    predicted = win._predicted_word
    check("the reader anticipates the word after the one just heard",
          predicted is not None and predicted > spoken,
          f"spoken={spoken} predicted={predicted}")
    check("a prediction never changes the reading position",
          win._display_word() == spoken,
          f"{spoken} -> {win._display_word()}")
    check("a prediction never moves the scroll target",
          win._text_edit.verticalScrollBar().value() == bar_before
          and win._voice_target_scroll == target_before,
          f"bar={win._text_edit.verticalScrollBar().value()} "
          f"target={win._voice_target_scroll}")

    def format_at(index):
        start, _ = win._matching.word_span(index)
        cursor = QTextCursor(win._text_edit.document())
        cursor.setPosition(start)
        cursor.setPosition(start + 1, QTextCursor.KeepAnchor)
        return cursor.charFormat()

    # Feed the rest of the line with predictions between results.
    for count in range(2, len(line_words) + 1):
        win._on_speech_text(" ".join(line_words[:count]), False)
        win._update_prediction()
        settle_tiers(win)
        QTest.qWait(20)

    check("the line advanced only after its last word",
          win._current_line == 1,
          f"line={win._current_line}")
    next_line = win._line_spans[win._current_line]
    if win._update_prediction():
        win._apply_highlight(win._display_word())
    check("the next line's first words are anticipated at a line boundary",
          win._predicted_word is not None
          and win._predicted_word >= next_line.first_word,
          f"predicted={win._predicted_word} "
          f"line_start={next_line.first_word}")

    settle_tiers(win)
    if win._predicted_word is not None:
        predicted_index = win._predicted_word
        predicted_format = format_at(predicted_index)
        colour = predicted_format.foreground().color()
        accent = (colour.red(), colour.green(), colour.blue())
        card = (0, 0, 0) if win._dark_theme else (255, 255, 255)
        check("anticipated words carry the accent tier",
              predicted_format.fontUnderline(), f"{colour.name()}")
        check("anticipated words are visible in dark mode",
              contrast(accent, (0, 0, 0)) >= 7.0,
              f"{contrast(accent, (0, 0, 0)):.1f}:1")
        check("anticipated words do not take the spoken band",
              predicted_format.background().style() == Qt.NoBrush,
              f"background style {predicted_format.background().style()}")
        win._dark_theme = False
        win._apply_read_mode_style()
        win._apply_highlight(win._display_word())
        settle_tiers(win)
        light_format = format_at(predicted_index)
        light_colour = light_format.foreground().color()
        light_accent = (light_colour.red(), light_colour.green(),
                        light_colour.blue())
        check("anticipated words are visible in light mode",
              contrast(light_accent, (255, 255, 255)) >= 7.0,
              f"{contrast(light_accent, (255, 255, 255)):.1f}:1")
        win._dark_theme = True
        win._apply_read_mode_style()
        win._apply_highlight(win._display_word())
        settle_tiers(win)

    # The word that was anticipated becomes spoken as soon as it is heard:
    # the accent and its rule are replaced by the spoken tier, and the new
    # lead starts after it. This is the incremental repaint that runs at
    # 20 Hz, so a stale underline here would be visible for a whole word.
    next_words = win._matching.raw_words_in_range(next_line.first_word,
                                                  next_line.last_word)
    if next_words and win._predicted_word is not None:
        # The previous lead covered this line's first word too.
        anticipated_word = next_line.first_word
        win._on_speech_text(next_words[0], False)
        win._update_prediction()
        win._apply_highlight(win._display_word())
        settle_tiers(win)
        became_spoken = format_at(anticipated_word)
        spoken_colour = became_spoken.foreground().color()
        # The words being spoken sit on the band at full brightness; older
        # spoken words are dimmed. Either way, the accent rule is gone.
        banded = became_spoken.background().style() != Qt.NoBrush
        check("a word leaves the accent tier once it is spoken",
              not became_spoken.fontUnderline()
              and (banded or (spoken_colour.red(), spoken_colour.green(),
                              spoken_colour.blue()) == (0x8A, 0x8A, 0x8A)),
              f"underline={became_spoken.fontUnderline()} "
              f"band={banded} colour={spoken_colour.name()}")
        check("the new lead starts after the spoken boundary",
              win._predicted_span is None
              or win._predicted_span[0]
              >= win._apply_highlight_end(win._display_word()),
              f"span={win._predicted_span} end="
              f"{win._apply_highlight_end(win._display_word())}")
        if win._predicted_word is not None:
            still_ahead = format_at(win._predicted_word)
            check("the lead after a spoken word keeps its accent",
                  still_ahead.fontUnderline(),
                  f"word={win._predicted_word} "
                  f"colour={still_ahead.foreground().color().name()}")

    # A pause holds the prediction; it must not immediately flicker off.
    win._on_speaking_changed(False)
    before_pause = win._predicted_word
    win._update_prediction()
    check("a pause leaves the anticipated run on screen",
          win._predicted_word == before_pause,
          f"{before_pause} -> {win._predicted_word}")

    # ── Three states, three treatments ──────────────────────────────────
    # Mid-sentence on a rendered line with room around the spoken boundary,
    # all three tiers are on screen at once. The checks read the character
    # formats the reader actually painted, after the transition has settled.
    win._on_speaking_changed(True)
    candidates = [span for span in win._line_spans[:12]
                  if span.first_word >= 6
                  and span.last_word - span.first_word >= 2]
    line = candidates[0] if candidates else win._line_spans[6]
    win._current_line = win._line_spans.index(line)
    position = line.first_word + 2
    win._confirmed_word = position
    win._partial_word = -1
    win._predicted_word = min(win._matching.word_count - 1, position + 4)
    win._apply_highlight(position)
    settle_tiers(win)

    focus_words = list(range(position - (win.FOCUS_WORDS - 1), position + 1))
    predicted_words = words_in(win, win._predicted_span)
    earlier = [word for word in range(0, focus_words[0])]
    check("the current tier holds two or three words",
          2 <= len(focus_words) <= win.FOCUS_WORDS,
          f"{len(focus_words)} words")
    check("the band covers exactly the current words",
          banded_words_in(win) == focus_words,
          f"banded={banded_words_in(win)} current={focus_words}")
    check("the predicted tier holds two to four words",
          win.PREDICTED_WORDS_MIN <= len(predicted_words)
          <= win.PREDICTED_WORDS_MAX,
          f"{len(predicted_words)} words: {predicted_words}")
    check("prediction never overlaps the words already spoken",
          win._predicted_span is not None
          and win._predicted_span[0] >= win._apply_highlight_end(position),
          f"span={win._predicted_span} end="
          f"{win._apply_highlight_end(position)}")

    current_fmt = tier_at(win, position)
    spoken_fmt = tier_at(win, earlier[-1])
    predicted_fmt = tier_at(win, predicted_words[0])
    check("the current words carry the brighter accent",
          current_fmt.foreground().color().name().lower()
          == win.CURRENT_DARK.lower(),
          current_fmt.foreground().color().name())
    check("the current words read slightly heavier without changing weight",
          current_fmt.textOutline().style() != Qt.NoPen
          and spoken_fmt.textOutline().style() == Qt.NoPen
          and predicted_fmt.textOutline().style() == Qt.NoPen,
          f"outline {current_fmt.textOutline().width()}")
    check("only the predicted words carry the rule",
          predicted_fmt.fontUnderline()
          and not current_fmt.fontUnderline()
          and not spoken_fmt.fontUnderline(),
          f"predicted={predicted_fmt.fontUnderline()}")
    check("spoken words keep no band, rule or stroke",
          spoken_fmt.background().style() == Qt.NoBrush
          and not spoken_fmt.fontUnderline()
          and spoken_fmt.textOutline().style() == Qt.NoPen,
          f"band={spoken_fmt.background().style()}")
    check("predicted words never take the spoken band",
          predicted_fmt.background().style() == Qt.NoBrush,
          f"background style {predicted_fmt.background().style()}")

    # Contrast, measured on the colours that were painted.
    card = (0, 0, 0) if win._dark_theme else (255, 255, 255)
    band = hex_rgb(win.BAND_DARK if win._dark_theme else win.BAND_LIGHT)
    current_rgb = tuple(current_fmt.foreground().color().getRgb()[:3])
    spoken_rgb = tuple(spoken_fmt.foreground().color().getRgb()[:3])
    predicted_rgb = tuple(predicted_fmt.foreground().color().getRgb()[:3])
    check("the current accent clears 7:1 on its band",
          contrast(current_rgb, band) >= 7.0,
          f"{contrast(current_rgb, band):.1f}:1")
    check("the current accent clears 7:1 on the card",
          contrast(current_rgb, card) >= 7.0,
          f"{contrast(current_rgb, card):.1f}:1")
    check("the predicted accent clears 7:1 on the card",
          contrast(predicted_rgb, card) >= 7.0,
          f"{contrast(predicted_rgb, card):.1f}:1")
    check("spoken words still clear 4.5:1",
          contrast(spoken_rgb, card) >= 4.5,
          f"{contrast(spoken_rgb, card):.1f}:1")
    check("the predicted accent is the quieter of the two",
          chroma(predicted_rgb) < chroma(current_rgb)
          and contrast(predicted_rgb, card) < contrast(current_rgb, card),
          f"chroma {chroma(predicted_rgb)} < {chroma(current_rgb)}")
    check("the three tiers are three distinct inks",
          len({current_rgb, spoken_rgb, predicted_rgb}) == 3)

    # The same three inks exist on paper.
    win._dark_theme = False
    win._apply_read_mode_style()
    win._apply_highlight(position)
    settle_tiers(win)
    light_current = tier_at(win, position)
    light_predicted = tier_at(win, predicted_words[0])
    light_current_rgb = tuple(light_current.foreground().color().getRgb()[:3])
    light_predicted_rgb = tuple(light_predicted.foreground().color().getRgb()[:3])
    light_band = hex_rgb(win.BAND_LIGHT)
    check("light theme: the current accent clears 7:1 on its band",
          contrast(light_current_rgb, light_band) >= 7.0,
          f"{contrast(light_current_rgb, light_band):.1f}:1")
    check("light theme: the predicted accent clears 7:1 on the card",
          contrast(light_predicted_rgb, (255, 255, 255)) >= 7.0,
          f"{contrast(light_predicted_rgb, (255, 255, 255)):.1f}:1")
    check("light theme: the predicted accent is the quieter of the two",
          chroma(light_predicted_rgb) < chroma(light_current_rgb))
    win._dark_theme = True
    win._apply_read_mode_style()

    # ── The transition is a crossfade, not a snap ───────────────────────
    win._apply_highlight(position)
    settle_tiers(win)
    entering = position + 1
    was = tier_at(win, entering).foreground().color()
    win._apply_highlight(entering)
    queued = len(win._tier_fades)
    started = time.monotonic()
    win._advance_tier_fades(started + win.TIER_FADE_SECONDS * 0.5)
    midway = tier_at(win, entering).foreground().color()
    win._advance_tier_fades(started + win.TIER_FADE_SECONDS + 0.02)
    settled = tier_at(win, entering).foreground().color()
    check("a state change fades over 100-150 ms",
          0.10 <= win.TIER_FADE_SECONDS <= 0.15, f"{win.TIER_FADE_SECONDS}s")
    check("the fade has frames partway between the two states",
          queued > 0 and midway.name() != was.name()
          and midway.name() != settled.name(),
          f"{was.name()} -> {midway.name()} -> {settled.name()}")
    check("the fade finishes on the exact target state",
          settled.name().lower() == win.CURRENT_DARK.lower()
          and not win._tier_fades,
          f"{settled.name()}, {len(win._tier_fades)} left")

    # ── Nothing about a tier moves the type ─────────────────────────────
    layout_before = [(span.first_word, span.last_word, round(span.top, 2))
                     for span in win._line_spans]
    spans_before = [win._matching.word_span(word)
                    for word in range(win._matching.word_count)]
    weights_before = {tier_at(win, word).fontWeight()
                      for word in range(min(40, win._matching.word_count))}
    for step in (position - 1, position, position + 2, 0, position):
        win._apply_highlight(step)
        settle_tiers(win)
    check("no tier re-wraps the rendered lines",
          layout_before == [(span.first_word, span.last_word,
                             round(span.top, 2))
                            for span in win._line_spans])
    check("no tier changes a word's character span",
          spans_before == [win._matching.word_span(word)
                           for word in range(win._matching.word_count)])
    check("no tier changes the font weight",
          weights_before == {tier_at(win, word).fontWeight()
                             for word in range(min(40, win._matching.word_count))},
          f"{weights_before}")

    # ── Corrections and skips leave nothing behind ──────────────────────
    win._apply_highlight(position)
    settle_tiers(win)
    win._apply_highlight(position - 2)
    settle_tiers(win)
    back_focus = list(range(position - 4, position - 1))
    check("a correction moves the band back with the voice",
          banded_words_in(win) == back_focus,
          f"banded={banded_words_in(win)} expected={back_focus}")
    check("a correction un-shades the words it returned to",
          tier_at(win, position).foreground().color().name().lower()
          != win.CURRENT_DARK.lower()
          and not tier_at(win, position).fontUnderline(),
          tier_at(win, position).foreground().color().name())

    win._apply_highlight(position + 3)
    settle_tiers(win)
    skipped_focus = list(range(position + 1, position + 4))
    check("a skipped clause leaves one band, not a trail",
          banded_words_in(win) == skipped_focus,
          f"banded={banded_words_in(win)} expected={skipped_focus}")
    check("a skip never marks the skipped words as spoken",
          tier_at(win, position + 1).background().style() != Qt.NoBrush
          and tier_at(win, position + 1).foreground().color().name().lower()
          == win.CURRENT_DARK.lower())

    # ── A pause leaves both tiers where they are ────────────────────────
    win._apply_highlight(position)
    settle_tiers(win)
    win._on_speaking_changed(False)
    held = (win._focus_span, win._predicted_span, win._display_word())
    for _ in range(6):
        win._update_prediction()
        win._apply_highlight(win._display_word())
        settle_tiers(win)
        QTest.qWait(20)
    check("a pause holds the band and the hint in place",
          (win._focus_span, win._predicted_span, win._display_word()) == held,
          f"{held} -> {(win._focus_span, win._predicted_span, win._display_word())}")

    # ── Update cost ─────────────────────────────────────────────────────
    # 200 prediction+paint cycles, the 20 Hz path the scroll clock takes.
    start = time.perf_counter()
    for _ in range(200):
        win._update_prediction()
        win._apply_highlight(win._display_word())
    per_update = (time.perf_counter() - start) * 1000.0 / 200.0
    check("a prediction update costs well under a frame",
          per_update < 2.0, f"{per_update:.3f} ms per update")

    win._voice_mode = False
    win.close()
    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All predictive-highlight checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
