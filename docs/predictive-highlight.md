# Predictive highlighting

The reader used to shade a word only after the recogniser reported it. That
always trails the voice: audio arrives in 100 ms blocks, the decoder needs a
little context, and an utterance final waits for the pause at its end. The
highlight now anticipates the next words instead.

## What is painted

There are three tiers, all inside the existing line-gated reader:

| Tier | Treatment | Purpose |
| :--- | :--- | :--- |
| Already spoken | dimmed `#8a8a8a` (dark) / `#767676` (light), 4.5:1 or better | shows what has been read |
| Being spoken | full-brightness type on one accent band | the eye's anchor; the scroll still waits for this line |
| Anticipated | accent `#8FB4FF` (dark, 10.1:1) / `#0B47C4` (light, 7.7:1), with an underline | the next word or phrase, before it is said |

Anticipated words deliberately do **not** take a background. The one tinted
band belongs to the words the voice is actually on; a second band would make
it ambiguous which words are current.

Prediction is a paint-only layer. It never changes the reading line, the
30/70 word anchor, the checkpoints or the scrollbar. Its result is bounded by
the rendered line on screen, with one extra word allowed because that is the
first word of the next visible line.

## How the estimate works

`notch/audio/predict.py` combines three signals:

- **Adaptive rate.** Every confirmed word interval updates a session rate
  (fast, weight 0.14/0.30 for partial/final) and a cross-session prior (slow,
  weight 0.03). Faster and slower passages are followed without throwing away
  what previous sessions learned.
- **A five-weight online timing model.** Word duration is predicted from
  length, clause punctuation, sentence punctuation and the recent rate. Each
  observed word takes one ridge-regularised gradient step. It learns that a
  word before a full stop is followed by a pause and that long words take
  longer, with no training pass, dataset or network call.
- **Pause state.** When the voice-activity detector reports silence, the lead
  freezes rather than growing on elapsed time. The held run is released after
  2.5-4 s, scaled by the speaker's learned pause length, so a breath keeps its
  place while a long silence does not leave stale words marked.

The target is the words that fit in the recogniser's own delay
(`0.45 s`), a reaction window (`0.30 s`) and the time since the last result.
It is capped at 8 words. A clause that ends within two words extends the
anticipated phrase to its punctuation.

An earlier version also learned a position-error bias, comparing each spoken
word with the painted boundary. The benchmark showed it pegging at its floor:
prediction is ahead by design, so "the spoken word is behind the paint" is not
an error. It was removed rather than left in as dead calibration; the budget
above already covers recognition and reaction time.

Anti-flicker rules: the boundary advances one word at a time (45 ms cadence),
retreats only after being wrong for 0.7 s, and never moves backwards while the
line or the voice is advancing. A repeated partial, a skipped clause or a
backward jump therefore cannot make the paint jitter.

The profile is `speaking-profile.json` beside the reader's `state.json`. It
contains aggregate timings only — no audio, transcript or script. It is
written after 40 learned words and on a normal state save/close, but only
when speech has actually changed it.

## Measurements

`tests/bench_predictive_highlight.py` simulates a speaker who changes pace
through 120, 190, 100, 170 and 230 wpm, pauses at punctuation, skips two words
and repeats one. A recogniser with 280 ms of latency sees the same timeline.

| Metric | Reactive (before) | Initial predictive | Tuned predictive |
| :--- | ---: | ---: | ---: |
| Words anticipated before spoken | 0% | 70.5% | **98.2%** |
| Prediction precision | — | 97.5% | **97.6%** |
| Mean lead over the voice | -0.58 words | +0.61 words | **+2.24 words** |
| Mean lead time | — | 0.45 s | **1.05 s** (p50 0.91 s) |
| Anticipatory words on screen | 0 | 0.68 | **2.23** |
| Flicker decreases | — | 0 | **0** |
| Growth during a pause | — | 0 | **0** |
| Prediction update, mean | — | 0.005 ms | **0.011 ms** (p95 0.022 ms) |
| Paint path, mean | — | 0.080 ms | **0.102 ms** (p95 0.307 ms) |

The tuned pass added the one-word line-boundary lookahead, raised the
recognition/reaction budget from 0.28/0.12 s to 0.45/0.30 s, and removed the
self-reinforcing position-error bias. Coverage rose from 70.5% to 98.2% and
mean lead from 0.61 to 2.24 words, with precision essentially unchanged and
no flicker. The simulated speaker skips two words and repeats one; the
predicted paint retreats quietly on both instead of jumping the scroll.

Read that table for what it is. The simulated recogniser delivers one word at
a time, 280 ms after the word is spoken. The installed engine does not behave
that way: it emits a growing partial transcript and one **final for a whole
utterance**, and the paint may never pass the end of the rendered line. So 98.2%
is an upper bound of the design, not a promise for a real reader. The numbers
that matter are the ones measured on real audio.

### Real audio

Three passes measure the same thing on audio the test did not write: a word
counts as anticipated only if it was painted before the recogniser confirmed
it. SAPI reads as 96% rather than the 68% an earlier version of this check
reported, because the check now decodes through the worker's own block handler;
the hand-rolled loop it replaced saw no partials and one final per sentence.

| Metric | SAPI sentences | Session 1 (45.6 s) | Session 2 (30.5 s) |
| :--- | ---: | ---: | ---: |
| Words confirmed | 25/25 | 25/25 | 25/25 |
| Words anticipated before confirmation | **96%** | 68% | 28% |
| Mean lead over confirmation | 1.40 s | 3.84 s | 4.33 s |
| Corrections (one-word retreats) | 0 | 1 | 0 |
| Reversals inside the 0.7 s retreat delay | 0 | 0 | 0 |
| Speech slot handled | 0.64 ms | 0.35 ms | 0.28 ms |
| Decoder per 100 ms block | 8.2 ms | 6.3 ms | 4.3 ms |

The decoder row was measured on an otherwise idle machine; with other work
running it can take several times longer while the mean stays inside the block
and the worker's queue absorbs the tail. The speech slot is the overlay's own
cost and does not move.

Sessions 1 and 2 are recordings of a person reading the same 25-word script,
replayed through the production block handler. They are the honest ceiling: the
paint cannot leave the current line, the line steps only when a word is
confirmed, and the card renders three words per line at this size. A fluent
reading produces one 17-word final, so most words are confirmed long before the
line could ever reach them; a reading with pauses produces several smaller
finals and more anticipation. Coverage over confirmation therefore measures the
recogniser's endpointing as much as the paint, which is why it is reported here
and asserted only weakly.

The live pass of session 1 reported exactly what its replay reports, 68% and
the same one correction; session 2 did not, 80% live against 28% replayed,
because the live worker and the replay cut that reading into a different number
of finals. Repeats of one recording agree to about 30 ms of mean lead and to
the same correction, so the replay is the repeatable number and the live run is
the one that includes the room.

The correction in session 1 is the design working: the paint reached one word
past the end of the line, the recogniser disagreed, and it retreated a single
word 3.2 s after its last advance. Every replay reproduces it at the same
moment, which is what the 0.7 s retreat rule is for.

`check_predictive_speech.py --live` records as it measures, and `--replay`
decodes that recording again through the same recogniser, detector and block
handler, so one reading of the script becomes a benchmark that needs nobody in
front of the screen. The session is a WAV and a JSON report in `.sessions/`;
both stay local and are ignored by git.

What the check asserts is the design's own invariants: every word confirmed, an
anticipation lead that is positive on average, no reversal inside the 0.7 s
retreat delay, no jump back of more than one word, and a speech result handled
in under 5 ms.

## Reproducing

```bash
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_predictive_highlight.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/bench_predictive_highlight.py
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_predictive_speech.py
```

The last command SKIPs when SAPI or the local model is missing. A human pass
with the real microphone uses the production `AudioWorker` and the same
measurements, and records the reading as it goes:

```bash
.venv/Scripts/python.exe tests/check_predictive_speech.py --live --seconds 30
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_predictive_speech.py --replay .sessions/<timestamp>
```

Read the displayed script aloud; the recording starts after the acoustic model
is live, so the clip is the reading and not the model load. The live pass SKIPs
when there is no input device, and it still measures even if the device refuses
the second capture, in which case no session is saved. `--replay` accepts a
session folder or a bare WAV; a bare WAV needs `--script FILE` because the
script is not inside it.

## Known limits

- The paint cannot pass the end of the rendered line, and the line steps only
  when a word is confirmed. At the card's current width that is three words per
  line, so a fluent reader gets at most one line of lead and most words are
  confirmed before the line reaches them. Widening the card and stepping the
  line on partial evidence are the two levers; neither is implemented.
- The learned rate is an aggregate for the speaker, not per script or per
  word. A deliberately theatrical change mid-sentence is followed over the
  next few words, not anticipated in the first one.
- The predictor does not know the meaning of the text. Clause punctuation is
  the only contextual signal; a phrase with no punctuation is bounded by the
  eight-word cap and the line.
- The simulation's timeline is synthetic; the SAPI and live passes exercise
  the real acoustic model, and `--replay` replays one real reading. A session
  is one voice reading one script in one room, so an accent, a cold or a noisy
  room still needs a fresh `--live` run; `tests/calibrate_voice.py` is the
  broader corpus check. Replay is repeatable to a millisecond, which makes it a
  benchmark, but it cannot reproduce a live room: silence at the start of a
  session, an unrelated voice or a page turn only happen in the live pass.
- `tests/check_design.py` asserts rendered pixels and needs the platform's
  font database, so it has to run on a real display; offscreen, its text-ink
  and control-geometry checks fail even on unchanged code. The predictive
  checks and `tests/check_quality.py` run offscreen.
