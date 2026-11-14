# Predictive highlighting

The reader used to shade a word only after the recogniser reported it. That
always trails the voice: audio arrives in 100 ms blocks, the decoder needs a
little context, and an utterance final waits for the pause at its end. The
highlight now anticipates the next words instead.

## What is painted

There are three tiers, all inside the existing line-gated reader:

| Tier | Treatment | Contrast | Purpose |
| :--- | :--- | :--- | :--- |
| Already spoken | dimmed `#8a8a8a` (dark) / `#767676` (light) | 6.1:1 / 4.5:1 | shows what has been read |
| Being spoken | brighter accent `#A9CCFF` (dark) / `#0A3A8F` (light), on one band (`#0A1940` / `#E1EFFF`), with a hairline stroke | 12.8:1 on the card, 10.4:1 on the band / 10.4:1 and 8.9:1 | the eye's anchor; the scroll still waits for this line |
| Anticipated | quieter accent `#9FB3DC` (dark) / `#3F5382` (light), with an underline, two to four words | 10.0:1 / 7.6:1 | the next words, before they are said |

Both accents clear 7:1 against the card, the current words clear 7:1 against
their own band, and spoken words stay at 4.5:1 or better, so a state change
is a change of ink and weight rather than a change of legibility. The
anticipated accent is deliberately lower-chroma than the current one: it has
to read as a hint, not as a second confirmation of what the voice is doing.

Anticipated words do **not** take a background. The one tinted band belongs
to the words the voice is actually on; a second band would make it ambiguous
which words are current.

"Slightly heavier" is a hairline stroke around the glyph (`CURRENT_STROKE`,
0.6 px scaled), not a font weight. Raising the weight would change every
advance width, re-wrap the line and move the type the reader is following;
a stroke thickens the same glyphs without touching the layout. Segoe UI has
no weight above Bold, so a weight step would fall back silently anyway.

A state change fades over 120 ms rather than snapping, driven from the
reader's own 60 Hz clock: ink and band alpha interpolate on an ease-out
curve, the rule switches at the halfway point, and the font always comes
from the target, so a word can never change metrics mid-transition. The
origin is read back out of the document, so a change that interrupts another
fade continues from the colour on screen; a pending fade that is only partly
overlapped is split around the new one instead of being dropped. Theme and
type changes paint at once: a theme is a new surface, not a state change.

Prediction is a paint-only layer. It never changes the reading line, the
30/70 word anchor, the checkpoints or the scrollbar. Its result is bounded
above the spoken boundary, so the hint can never overwrite the band, and to
two to four words: two are the words the voice reaches next whatever the
timing model says, four is where a hint stops clarifying and starts
competing with the type already on screen. At a line boundary those words
may run into the first words of the next visible line rather than stopping
at one; painting is not scrolling, so none of this can move the reader.

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
The estimate is capped at 8 words and the paint at `PREDICTED_WORDS_MAX`
(4). A clause that ends within two words extends the anticipated phrase to
its punctuation.

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

- The paint stops four words past the boundary and the line steps only when
  a word is confirmed. At the card's current width that is three words per
  line, so at most the first words of the next visible line are within reach
  and most words are confirmed before the line arrives at them. Widening the
  card and stepping the line on partial evidence are the two levers; neither
  is implemented.
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
