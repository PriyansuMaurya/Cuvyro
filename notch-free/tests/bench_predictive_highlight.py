"""Benchmark anticipatory highlighting against a known speaker timeline.

A simulation knows exactly when every word was spoken, so it can answer the
two questions a real microphone cannot answer cheaply:

  * **coverage** — how many words were marked *before* the speaker said them?
  * **lead** — when the highlight was ahead of the voice, by how much, and
    when did it lag instead?

The simulated speaker changes pace through 120, 190, 100, 170 and 230 wpm,
pauses at punctuation, occasionally skips a word and occasionally repeats one.
The recogniser sees the timeline with a configurable delay and drops some
words, which is the behaviour that makes a reactive highlight trail the voice.

The same run measures the cost of the real painting path offscreen. Nothing
requires a microphone, a model or a display.

    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/bench_predictive_highlight.py
    ... --json predictive-bench.json
    ... --recogniser-latency 0.45

The reactive column is the same timeline with no prediction: its "lead" is
simply how far the recogniser is behind the voice, and its coverage is zero by
definition. It is shown to make the comparison explicit, not as a second
implementation.
"""

import argparse
import json
import math
import os
import statistics
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "demo-script.txt")
CHARS = "abcdefghijklmnopqrstuvwxyz"


def clean(word):
    return "".join(ch for ch in word.lower() if ch.isalnum() or ch == "'")


def sentence_end(word):
    return word.rstrip("\"')]").endswith((".", "!", "?"))


def build_timeline(words, seed=7):
    """(events, spoken_at, skipped, end_time) for a changing-pace speaker.

    An event is ``(index, start, end, final)``. Duplicate indices are
    repetitions. Every word has a first spoken time; a skipped word has None.
    """
    rates = (130, 190, 100, 170, 230, 120)
    events = []
    spoken_at = [None] * len(words)
    skipped = set()
    cursor = 0.4
    for index, word in enumerate(words):
        if index > 25 and index % 61 == 0:
            skipped.add(index)
            continue
        wpm = rates[(index // 17) % len(rates)]
        letters = max(2, len(clean(word)))
        duration = (60.0 / wpm) * (0.55 + min(letters, 11) * 0.085)
        if index > 30 and index % 43 == 0:
            # A repeated word: the speaker says it twice before moving on.
            events.append((index, cursor, cursor + duration, False))
            cursor += duration
            events.append((index, cursor, cursor + duration * 0.85, False))
            cursor += duration * 0.85
        else:
            events.append((index, cursor, cursor + duration, False))
            cursor += duration
        if spoken_at[index] is None:
            spoken_at[index] = events[-1][1]
        if sentence_end(word):
            cursor += 0.55
        elif index % 29 == 0 and index:
            cursor += 0.35
    # Mark the last event of each phrase as the final result for that phrase.
    for position, event in enumerate(events):
        following = events[position + 1] if position + 1 < len(events) else None
        if following is None or following[1] - event[2] > 0.22:
            events[position] = (event[0], event[1], event[2], True)
    return events, spoken_at, skipped


def pct(values, share):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1,
                       int(round(share * (len(ordered) - 1))))]


def run_simulation(words, latency, tick=0.05, window=2.5):
    from notch.audio.predict import PredictiveHighlighter

    events, spoken_at, skipped = build_timeline(words)
    # What the recogniser can see, after its own delay. Partial results keep
    # arriving between words, so the visible boundary is the last word that
    # has had time to decode.
    visible = [(start + latency, index, final, start)
               for index, start, _end, final in events]
    visible.sort(key=lambda item: item[0])
    end_time = max(event[2] for event in events) + latency + 1.0
    line_words = 8
    directory = tempfile.mkdtemp(prefix="notch-predict-bench-")
    predictor = PredictiveHighlighter()  # no store: measurement only
    predictor.set_script(words)

    tick_count = int(math.ceil(end_time / tick))
    speaking_before = False
    visible_before = -1
    predicted_before = None
    predicted_at = [None] * len(words)
    rate_errors = []
    lead_vs_voice = []
    anticipatory_counts = []
    stale_counts = []
    correct_hits = []
    wrong_hits = []
    flicker = 0
    pause_growth = 0
    max_decrease = 0
    prediction_ms = []
    last_good_prediction = None

    for step in range(tick_count):
        now = step * tick
        speaking = any(start <= now <= start + max(0.20, (end - start) + 0.12)
                       for _index, start, end, _final in events
                       if start <= now)
        if speaking and not speaking_before:
            predictor.speech_started(now)
        elif not speaking and speaking_before:
            predictor.speech_stopped(now)
        speaking_before = speaking

        # The latest word the recogniser has delivered.
        seen = [item for item in visible if item[0] <= now]
        if seen:
            _, index, final, _start = seen[-1]
            if index != visible_before or final:
                predictor.observe(index, now, final)
                visible_before = index
        if seen:
            visible_index = seen[-1][1]
        else:
            visible_index = -1

        # One word past the rendered line is the first word of the next
        # visible line, which the overlay is allowed to anticipate.
        word_limit = min(len(words) - 1,
                         ((max(0, visible_index) // line_words) + 1)
                         * line_words)
        before = time.perf_counter()
        predicted = predictor.prediction(visible_index, word_limit, now,
                                         speaking=speaking)
        prediction_ms.append((time.perf_counter() - before) * 1000.0)

        # Ground truth: the furthest word whose start has passed, and the
        # words actually said, so a repeated word is not counted twice.
        spoken = [i for i, at in enumerate(spoken_at)
                  if at is not None and at <= now]
        true_position = max(spoken) if spoken else -1

        if predicted is not None:
            lead_vs_voice.append(predicted - true_position)
        elif speaking:
            lead_vs_voice.append(visible_index - true_position)

        if predicted is not None and speaking:
            count = max(0, predicted - visible_index)
            anticipatory = []
            stale = 0
            for word in range(visible_index + 1, predicted + 1):
                at = spoken_at[word] if 0 <= word < len(words) else None
                if word in skipped:
                    anticipatory.append(word)
                    wrong_hits.append(1)
                    correct_hits.append(0)
                elif at is None:
                    continue
                elif at > now:
                    anticipatory.append(word)
                    if at - now <= window:
                        correct_hits.append(1)
                        wrong_hits.append(0)
                    else:
                        correct_hits.append(0)
                        wrong_hits.append(1)
                else:
                    stale += 1
            anticipatory_counts.append(len(anticipatory))
            stale_counts.append(stale)
            for word in anticipatory:
                if predicted_at[word] is None:
                    predicted_at[word] = now

        if speaking and predicted is not None and last_good_prediction is not None:
            if predicted < last_good_prediction:
                flicker += 1
                max_decrease = max(max_decrease, last_good_prediction - predicted)
        if not speaking and predicted is not None and last_good_prediction is not None:
            if predicted > last_good_prediction:
                pause_growth += 1
        if predicted is not None:
            last_good_prediction = predicted

        # Rate error against the words actually said in the last 3 seconds.
        if speaking and now > 4.0 and now % 0.5 < tick:
            recent = [i for i, at in enumerate(spoken_at)
                      if at is not None and now - 3.0 <= at <= now
                      and i not in skipped]
            if len(recent) >= 3:
                true_rate = len(recent) / 3.0
                rate_errors.append(abs(predictor.rate_wps - true_rate)
                                   / max(0.5, true_rate))

    said = [i for i in range(len(words)) if i not in skipped]
    anticipated = [i for i in said
                   if predicted_at[i] is not None
                   and predicted_at[i] < spoken_at[i]]
    lead_times = [spoken_at[i] - predicted_at[i] for i in anticipated]
    coverage = len(anticipated) / max(1, len(said))
    precision = (sum(correct_hits) / len(correct_hits)
                 if correct_hits else 0.0)
    # Reactive comparison: the recogniser boundary is all the old reader had.
    reactive_lead = []
    last_visible = -1
    for step in range(tick_count):
        now = step * tick
        seen = [item for item in visible if item[0] <= now]
        if seen:
            last_visible = seen[-1][1]
        spoken = [i for i, at in enumerate(spoken_at)
                  if at is not None and at <= now]
        true_position = max(spoken) if spoken else -1
        if last_visible >= 0:
            reactive_lead.append(last_visible - true_position)

    return {
        "script_words": len(words),
        "spoken_words": len(said),
        "skipped_words": len(skipped),
        "recogniser_latency_ms": round(latency * 1000.0),
        "prediction": {
            "coverage": round(coverage, 3),
            "precision": round(precision, 3),
            "mean_lead_words": round(statistics.fmean(lead_vs_voice)
                                     if lead_vs_voice else 0.0, 2),
            "mean_lead_seconds": round(
                statistics.fmean(lead_times) if lead_times else 0.0, 2),
            "p50_lead_seconds": round(pct(lead_times, 0.5), 2),
            "mean_anticipatory_words": round(
                statistics.fmean(anticipatory_counts)
                if anticipatory_counts else 0.0, 2),
            "mean_catch_up_words": round(
                statistics.fmean(stale_counts) if stale_counts else 0.0, 2),
            "flicker_decreases": flicker,
            "max_decrease_words": max_decrease,
            "pause_growth": pause_growth,
            "rate_error": round(statistics.fmean(rate_errors)
                                if rate_errors else 0.0, 3),
            "update_ms_mean": round(statistics.fmean(prediction_ms), 4),
            "update_ms_p95": round(pct(prediction_ms, 0.95), 4),
            "update_ms_max": round(max(prediction_ms), 4),
            "learned_wps": round(predictor.rate_wps, 2),
        },
        "reactive": {
            "coverage": 0.0,
            "mean_lead_words": round(statistics.fmean(reactive_lead)
                                     if reactive_lead else 0.0, 2),
            "note": "visible boundary minus the true voice; negative is lag",
        },
    }


def measure_painting():
    """Cost of the real offscreen painting path."""
    from PyQt5.QtGui import QFont
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication
    from notch.ui import overlay as ui_overlay
    from notch.storage.store import Store
    from notch.audio.predict import PredictiveHighlighter, ProfileStore

    app = QApplication.instance() or QApplication(sys.argv)
    win = ui_overlay.NotchOverlay()
    win._store = Store(tempfile.mkdtemp(prefix="notch-predict-paint-"))
    win._predictor = PredictiveHighlighter(
        ProfileStore(win._store.directory))
    win._voice_mode = False
    win.show()
    QTest.qWait(150)
    win._voice_mode = True
    text = (open(FIXTURE, encoding="utf-8").read() + "\n") * 4
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
    win._apply_highlight(-1)
    win._refresh_lines()

    line = win._line_spans[0]
    win._on_speaking_changed(True)
    base = line.first_word
    costs = []
    for step in range(300):
        win._predicted_word = min(line.last_word, base + 1 + step % 5)
        started = time.perf_counter()
        win._apply_highlight(base)
        costs.append((time.perf_counter() - started) * 1000.0)
    win._voice_mode = False
    win.close()
    return {
        "paint_ms_mean": round(statistics.fmean(costs), 4),
        "paint_ms_p95": round(pct(costs, 0.95), 4),
        "paint_ms_max": round(max(costs), 4),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--recogniser-latency", type=float, default=0.28)
    parser.add_argument("--json")
    parser.add_argument("--no-painting", action="store_true")
    args = parser.parse_args()

    script = open(FIXTURE, encoding="utf-8").read()
    words = script.split() * 3
    report = run_simulation(words, args.recogniser_latency)
    if not args.no_painting:
        report["painting"] = measure_painting()
    report["results"] = {
        "coverage_gain": round(
            report["prediction"]["coverage"] - report["reactive"]["coverage"], 3),
        "lead_gain_words": round(
            report["prediction"]["mean_lead_words"]
            - report["reactive"]["mean_lead_words"], 2),
    }
    print(json.dumps(report, indent=2))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
