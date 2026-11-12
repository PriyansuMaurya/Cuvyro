# UI audit captures

Before/after evidence for the Windows overlay work, captured against the
reference geometry used for the redesign. All captures are from one 1920×1080
display at 100% scaling, grabbed at the window's own rect, so the pixel sizes
are 1:1 with the DIP values below.

| Surface | Before | After | Reference target |
|---|---|---|---|
| Compact reader | 300×110 at (810, 8) | **340×122 card in a 384×122 window at (790, 6)** | 340×122 card, 6 DIP inset |
| Script editor | 860×320 at (530, 8) | **860×320 card in a 904×320 window at (530, 6)** | 860×320 card, same top anchor |
| Sticky note | did not exist | **260×165** | 260×165 |

`note-after.png` has no "before" counterpart: the committed build had no note
windows, so the whole surface is new rather than restyled.

The `light-*.png` captures are the same three surfaces with the light theme.
Both themes were checked at 100% and 125% scaling; the DIP sizes above hold at
both.

Reading rhythm was measured from the captures, not estimated: the wrapped-line
baseline pitch inside a paragraph is **31 px**, and the font's own line height
is 21 px (12 pt Segoe UI at 96 DPI), so the leading is 1.48× the type's height
and two lines can never collide.

Surface treatment, also measured from the captures:

- **Hairline border**: one pixel of `#2A2C2A` (dark) / `#E2E2E2` (light), drawn
  last so the voice glow cannot wash it out. The stroke is inset half a pixel
  and lands on exactly one pixel row, with no anti-aliasing blur.
- **Rounded corners**: 24 px compact/editor, 12 px note.
- **Card material: solid.** The reader is a surface, not a hole: the fill is
  fully opaque in both themes (measured centre alpha **255** / **255**), with
  the same quiet wash, the same microphone waveform at the bottom and the same
  hairline. Transparency is spent only on the floating controls; the type
  itself is fully opaque, so the desktop can never ghost through the words.
  The full-height script is what changed, not the window it runs on.
- **Floating rail, outside the card**: the reading surface stays 340 px
  wide; the window carries a transparent **44 px** strip to its right and the
  four controls sit in that gap, one vertical column, centred on the card's
  height with equal 2 px gaps. Measured: card right edge **340**, controls at
  **x 347**, card width **340**, text block ends at **322**. No glyph touches
  the type, and the strip beside the card reads alpha **0**.
  **The script opens 30% down the card** (measured **36.2 px**), so the first
  line is whole and clears the top edge. That lead belongs to the document
  rather than to the viewport: once reading starts it scrolls away, carrying
  the lines already read upward while the line being read keeps its usual
  place. Only the foot keeps its room, so the last line can still be scrolled
  up onto the reading line.
  The reading line sits at **62 px**, snapped to the document's **31 px** line
  grid. Every line therefore lands on a whole multiple of the grid from the top
  of the card: the topmost line is complete and the line above it has left the
  view, so a resting view never shows a row of ascenders cut off against the
  edge. Measured on a real display: every line with room above it settles
  within **0.2 px** of the anchor, and the topmost line's top edge never rises
  above **−0.2 px** across every resting position in the script.
- **Typographic hierarchy**: the reader does not fade its edges. Hierarchy is
  word-level: script type is `#fafafa` (dark) / `#111111` (light), already-
  spoken words are `#8a8a8a` / `#767676`, and the active line is the brightest
  unspoken run. Every glyph is fully opaque, so a line near the top edge is
  never half-faded. Measured: body contrast **20.1:1** / **18.9:1**, spoken
  contrast **6.1:1** / **4.5:1**.
- **Controls on a three-second leash**: while the reader is reading, the
  rail dissolves 3 s after the last movement and comes back the moment the
  pointer moves — over the script or over a control. Measured: opacity
  1.00 → 0.00 on the idle timeout, back to 1.00 on a mouse move over either.
  The editor and the resting pill keep their controls on screen, because
  there they are the point.
- **Editor and menu** use the same solid fill and hairline; the editor keeps
  its own top breathing room while it is open, because it is a document you
  type into. The rail stays outside the editor's text column.
- **Light theme**: white surface, `#111111` text, `#E2E2E2` hairline, and the
  voice glow scaled to 45% so it reads as a hint rather than a blue smudge.
- **Controls**: four outline glyphs, each on its own floating circle, with
  no connecting panel. The dark circle is `#171719`, the light one `#FFFFFF`,
  both inside a 28 px target with a 1 px edge. Three soft shadow rings sit
  under each circle and are wider/fainter on the outside, so the button reads
  as lifted off the desktop rather than as a card attached to the reader.
  Measured: hit-box corner alpha **0**, fill alpha **255**, shadow alpha
  **18–54** below the circle, and a control's pixels are identical whether it
  is drawn over black or white. Hover runs on a **0.24 s** spring: the circle
  lifts **1.2 px**, scales **1.035×**, brightens its fill, and the glyph
  blends from grey to near-white. Press keeps the faster **0.16 s** spring
  and adds a **0.94×** scale. The narrow/wide toggle is two 1.4 px strokes
  rather than a filled square.
- **No menu control**: the "more" glyph earned its place only while the menu
  had nowhere else to open. It is gone, and the menu opens on a right click
  over the card or over the script, so the rail is four glyphs instead of
  five with one that only ever opened the menu.

## Fixes found while comparing themes

- The sticky note ignored the theme entirely and stayed charcoal on a white
  desktop.
- The script viewport used to dissolve its edges. The alpha ramp made the
  line at the top edge fade to near zero before the card's edge, so only
  fragments of its letters showed: it looked clipped. The dissolve (and the
  blur before it) is gone. `ScriptTextEdit` now lets `QTextEdit` paint the
  document directly: the whole column is fully opaque, sharp to the card
  edges, with no offscreen image, no blur and no alpha ramp.
- With the dissolve gone, the cut it used to hide became visible: the reading
  anchor was 64.3 px, which is not a multiple of the 31 px line grid, so the
  line above the reading line sat 2.5 px inside the card and drew its
  ascenders against the edge. The anchor is now snapped to the grid, so the
  top edge always falls on a line boundary. The grid snap also holds when the
  card is resized: a 420×190 window puts the anchor at 93 px and still lands
  the topmost line on 0 instead of half a line above it.
- The fixed header band above line one stayed gone, but the opening is no
  longer flush with the card edge: the document keeps a 30% lead that scrolls
  away with the type, so the first line (measured **36.2 px**) sits below the
  controls and is never half-hidden behind them. The last line can still
  reach the anchor.
- The light theme applied the full-strength coloured glow over white, which
  read as a smudge rather than a status hint.

## Robustness, latency and access (second pass)

A later pass audited what the visual checks do not cover. These numbers are
from the same machine, with the checks in `tests/check_robustness.py`:

| Finding | Before | After |
|---|---|---|
| 20 keystrokes in a note (state file write) | 498 ms | **0.6 ms**, one deferred write; close flushes |
| QObjects left by opening the menu 6 times | 54 → 420 | **54 → 54** |
| Reader saved at (99999, 99999) | restored off-screen | **clamped to a real screen** |
| Note saved on a removed monitor | invisible | **clamped, text kept** |
| Windows-encoded `.txt` | silently ignored | **cp1252/BOM decode, errors shown** |
| Space in the editor | consumed by the reader shortcut | **types a space; arrows move the caret** |
| Manual mode while another app has focus | dead: Space and the arrows are window-scoped, so they went to the app in front, which is exactly when Manual is used | **system-wide chords, opt-in; the window keys still work when Notch has focus** |
| Manual mode after the menu, Help or Setup closes | keyboard left with the window behind | **the reader asks for activation back, and never takes it from another application** |
| Speech shading per advance (3,360 words) | 5.3 ms | **0.13 ms** |
| Setup construction, input devices | 780 ms (10 devices) | **2.4 ms median (17 devices)**, no probe streams; meter starts after paint |
| Help / Setup in the light theme | charcoal | **paper surface** |
| 9 pt Done label | 3.6:1 white on `#0A84FF` | **5.6:1 on `#0066CC`** |
| Note keyboard close | mouse only | **Esc, or Space on the focused ×** |

Custom-drawn controls now expose accessible names, menu sliders and inputs
have names and label buddies, and the reader's window has a title for the
task switcher. Setup's OK action is now the same blue primary as the reader's
Done and is the default action for Enter, instead of matching Cancel.

The reader also remembers its position across relaunches, and a saved
position is clamped to the screens that currently exist.

## Speech following (third pass)

The follow loop was measured end to end rather than only checked for correct
word indices. Three fixed delays dominated the feel:

| Finding | Before | After |
|---|---|---|
| Audio handed to the recogniser | 250 ms blocks | **100 ms blocks**, 14.6× realtime (69 ms CPU per second of audio; the old 250 ms block was 13.9×) |
| Scroll at the end of a sentence | waited for Vosk's final, which only arrives after the recogniser's end-of-sentence pause | **steps on the partial that reaches the line's last word**, before the pause |
| Reading-speed estimate | rose with faster speech, never fell | **tracks both directions** (test: 300 → 252 wpm when slowing, 60 → 108 when speeding up) |
| A skipped clause | stalled at the first unheard word | **resyncs on two consecutive recognised words**; a single stray word cannot jump (old `last_word=0`, new `9` on the test phrase) |
| UI handling per result | 0.13 ms shading, latency unmeasured | **0.106 ms partial, 0.165 ms final** on this machine |
| Matcher work per 20-word final | 0.077 ms | **0.057 ms** |

The matcher and the UI were never the bottleneck: together they are well under
a millisecond. The delay was all in batching and in treating a partial as
untrustworthy until a final arrived. Partials still cannot leave the line on
screen; only a close match (88) for that line's last word promotes the
position, so the scroll cannot run ahead of what was said.

The follow checks now cover spelling and pronunciation variants, skipped
clauses, stray-word rejection, 100 ms cadence, partial and final latency
budgets, and speed adaptation in both directions, alongside the existing
slow/normal/fast delivery, pauses, 17 font sizes × 6 families, and five
viewport widths.

## Focus and system-wide Manual keys (fourth pass)

Manual mode was reported dead during presentations. It was: every Manual
control is a window-scoped shortcut, so it only exists while Notch owns the
keyboard, and presenting means another application does. Voice kept working
because the microphone needs no focus.

| Finding | Before | After |
|---|---|---|
| `Space` with Chrome focused | delivered to Chrome; `_manual_running` stayed False, scroll 0 | **`Ctrl+Shift+Space` starts Manual; scroll advanced 17–18 px on a real key press, foreground unchanged** |
| Speed while another app is focused | window-scoped arrows only | **`Ctrl+Alt+Left / Right` change the pace from anywhere (150 → 160 wpm measured)** |
| Registration failure | silent | **reported per chord; error 1409 means another application owns it** |
| Voice mode | — | **releases every chord and ignores them (`voice_wpm` 60 → 60 across a chord press)** |
| Closing the reader | — | **all three chords released; a fresh `RegisterHotKey` on the same window succeeds** |
| Consent | — | **the right-click menu's Global keys Off/On row, plus Setup; nothing is registered until it is switched on** |

Chord choice came out of measurement, not taste. Two combinations that look
obvious do not work on Windows:

- `Ctrl+Alt+Space` is read by the foreground application as `Alt+Space`, so
  it flashes that window's menu / the task switcher. The toggle is
  `Ctrl+Shift+Space` for that reason.
- `Ctrl+Shift+Left/Right` registers successfully and then never delivers
  `WM_HOTKEY`, so it would look live and do nothing. The speed chords are
  `Ctrl+Alt+arrows`, which do deliver and never open a menu.

The reader no longer reaches for the foreground at all: `_focus_reader()`
only asks for activation after Notch's own menu, Help or Setup closes, and it
declines while a note, dialog, the editor or the pill is active. Nothing is
attached to another application's input queue.

Sticky-note placement was also unstable at 800×600: random coordinates
fragmented the space until the fourth note covered the first (3 failures in 8
runs). Placement is now a bounded, anchored lattice — cells are at least one
note plus 8 px apart, so two chosen notes cannot overlap — with a bounded fine
sweep for small screens and a deterministic fallback when a screen is full.
Measured: four notes fit 800×600 for every one of 50 seeds, and the whole
placement check passed 20 runs in a row.

Known limits of this pass:

- Scripts are `.txt` only. The README advertised PDF and DOCX import, but no
extractor was ever wired in; the picker, drop target and README now agree.
- Dialog surfaces and control names were verified from rendered pixels and
  accessible-name properties, not with a screen reader application.
- Multi-monitor behaviour was verified by synthetic geometry (an off-screen
  save point), not by physically unplugging a display.
- Speech recognition itself was exercised with synthetic word timings in
  `check_voice_follow.py`; live microphone recognition accuracy, and therefore
  real accents through the acoustic model, was not measured on this machine.
  Pronunciation variants are covered at the text-alignment level.
- The 100 ms cadence and the partial line step were verified with the model
  decoding silence for throughput and with synthetic results for behaviour;
  a live multi-sentence read was not recorded.

## Speech engine (fourth pass)

The third pass measured the loop around the recogniser. This pass replaced the
recogniser itself, because the model was the dominant cost left: Vosk spent
about a quarter of a core decoding speech, and its endpointing was inside the
binary where it could not be tuned.

Candidates were decoded on the same local audio, from
`tests/bench_speech_engines.py`. The audio is synthesised with the Windows SAPI
voices already on the machine (en-US and en-GB, slow/normal/fast), so the
benchmark itself never touches the network. Reference readings were taken with
the previous engine still in place, in three interleaved rounds on an
i7-13620H (10C/16T, 16 GB), best round per engine, because the desktop had
Chrome open and single runs swung by 3x:

| Engine | CPU per audio second | Real-time factor | Word error (56 sentences) | 100 ms block, median | Model load |
|---|---|---|---|---|---|
| Vosk small en-us 0.15 (previous) | 234 ms | 4.3x | 6.5% | 2.0 ms | 0.5 s |
| **sherpa-onnx zipformer en 2023-06-26, int8, 2 threads** | **68 ms** | **14.8x** | **7.9%** | **0.7 ms** | 4.2 s |
| sherpa-onnx zipformer en 20M int8 | n/a | n/a | n/a | n/a | excluded |

The 20M model is faster still, but it drops the first two or three words of
every utterance with sherpa-onnx 2.0.1 (fp32 weights as well as int8), so it is
excluded rather than tuned around. The zipformer in use is 3.4x cheaper per
second of audio with the same order of accuracy, and it halves the median block
cost, which is the headroom the scroll timer runs in.

What changed in `notch/audio/worker.py`, behind the same `speech_text_signal`:

| Finding | Before | After |
|---|---|---|
| Decode cost while speaking | 234 ms per audio second (Vosk) | **68 ms** (zipformer) |
| Decode cost while nobody talks | 62 ms per audio second, the recogniser saw all of it | **0 ms**: a Silero VAD gates the recogniser, at **6 ms** per audio second to run |
| End of an utterance | Vosk's internal silence rule, ~1 s, not adjustable | **one 0.75 s pause**, measured 0.80 s after the last audible block |
| Room noise | RMS gate called a fan "speech", and filler words followed | VAD flags speech, so the glow and the filler-word filter follow a voice |
| First word after a pause | fed as it arrived | **not clipped**: 400 ms of pre-onset audio is replayed when the VAD confirms speech, because the detector needs ~150 ms before it reports any |
| Voice mode reactivation | model reloaded on every toggle to the editor and back | **reused**: the loaded engine is leased to one worker at a time, so the 4.2 s cold load is paid once per launch |
| Waiting for the model | nothing said why the first words were late | `status_signal` reports it, next to the existing "Listening..." |

The matcher's stepping rules were left alone: partials still drive the
highlight, only a close match for a line's last word steps the line, and
silence still never moves the reader. Two gaps in it were found by measuring a
real accented speaker, though, and are fixed (see below).

A 4.2 s cold load is the price of the bigger acoustic model; it is measured on
a machine with 2.8 GB free of 16 GB, and the load happens once because the
engine is reused afterwards.

Privacy is asserted, not assumed: `tests/check_speech_engine.py` patches
`socket.connect`, `connect_ex` and `create_connection` to raise, then loads the
model and decodes a sentence. Nothing reaches for a socket, and there is no
network code in either engine — `notch/audio/speech.py` only calls into local ONNX
models and, for the fallback, Vosk's local Kaldi model.

### Following an accent

A reader with an Indian English accent recorded the 14-sentence corpus
(`tests/calibrate_voice.py --auto`), and the same takes were decoded and then
pushed through the real overlay, line gating and all. Transcription is
imperfect on that voice — **34.6% word error rate** for zipformer on complete
takes — but transcription is not the promise; following the script is. Two
failures showed up, both of which would hit any speaker, accent or not:

| | Before | After |
|---|---|---|
| Script words reached, driven by these takes | 61.5% | **95.4%** |
| Rendered lines reached | 63.3% | **81.6%** |

- **Numbers were invisible.** Scripts print `60`, `14`, `19`; speakers say
  *sixty*, *fourteen*, *nineteen*. The fuzzy score between the two is 0, so one
  number in a sentence stopped the reader dead. Both sides are now
  canonicalised to the digit form (ordinals and hyphenated compounds included),
  which recovered the `60 per cent` sentence on its own.
- **One missed sentence was permanent.** Alignment only moves forward, and the
  resync window spans a clause, not a sentence: after a sentence the recogniser
  lost entirely, every later result was out of reach, and the reader sat at
  word 80 of 130 for the rest of the session. A final that confirms nothing now
  licenses a re-anchor — but only the second one in a row, and only onto **three
  consecutive** strongly matching words, so a cough or a repeated phrase cannot
  teleport the reader. The same script repeated twice is the trap this avoids.

The remaining 4.6% is audio the session never captured: two takes stop
mid-sentence at a breath (the recorder's endpoint, not the engine), and the
re-anchor moves past them as designed. Lines stay behind words because a line
only advances once its last word is confirmed, which is the intended pause at a
line boundary.

Known limits of this pass:

- The accent figures come from one speaker. They say the decoder and the
  matcher are usable on that accent, not that they are calibrated for accents in
  general; `tests/calibrate_voice.py` is what would answer that per speaker.
- Accent accuracy beyond decoding was addressed by matcher work only. The
  acoustic model was not fine-tuned: that needs the icefall training pipeline,
  hours of speech and a GPU, none of which belong in this repository.
- The benchmark's "words behind" figure (~1 word for both engines) is a rough
  proportional measure, not a per-word timestamp comparison; the per-word
  timestamps the matcher would need are not consumed yet.
- The 4.2 s load and the 68 ms/second figure come from one machine. The
  relative order (zipformer cheaper, both real-time capable) is what the
  selection rests on.
- Whisper-family models (whisper.cpp, faster-whisper) were excluded on
  architecture, not benchmarked: they produce a segment after an utterance
  rather than partials during it, and the reader steps on partials. A
  benchmark would only have confirmed a latency the design cannot use.

## Reproducing

```bash
# geometry + screenshots of all three surfaces
.venv/Scripts/python.exe tests/verify_ui.py

# typography, speech-follow accuracy, scroll glide, anchor and transparency
.venv/Scripts/python.exe tests/check_quality.py

# capture-protection assertion (expects 0x11 on overlay and note)
.venv/Scripts/python.exe tests/verify_ui.py --check-protection

# wheel scroll, timed playback, persistence, note lifecycle
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_behavior.py

# line-gated voice follow: pauses, speeds, multi-line finals, reflow
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_voice_follow.py

# the local engine: what it hears, block cost, VAD pause, no sockets opened
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_speech_engine.py

# engine benchmark: SAPI fixtures, throughput, words behind, word error rate
.venv/Scripts/python.exe tests/bench_speech_engines.py --repeat 3

# your own voice: record the corpus, decode it, list the words to calibrate
.venv/Scripts/python.exe tests/calibrate_voice.py

# re-score the recorded takes and drive the overlay with them
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/calibrate_voice.py --no-mic --reader

# debounced saves, menu lifecycle, screen clamping, import, shortcuts, access
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_robustness.py

# design invariants, asserted against rendered pixels (needs a real display)
.venv/Scripts/python.exe tests/check_design.py
```

Screenshots are written to `%TEMP%\shots`. `verify_ui.py` lifts
`WDA_EXCLUDEFROMCAPTURE` for the duration of each grab and restores it, because
with capture protection active every screenshot of these windows comes back
blank — which is itself the evidence that protection is working.
