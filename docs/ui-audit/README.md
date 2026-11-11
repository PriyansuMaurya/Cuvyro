# UI audit captures

Before/after evidence for the Windows overlay work, captured against the
reference geometry used for the redesign. All captures are from one 1920×1080
display at 100% scaling, grabbed at the window's own rect, so the pixel sizes
are 1:1 with the DIP values below.

| Surface | Before | After | Reference target |
|---|---|---|---|
| Compact reader | 300×110 at (810, 8) | **340×122 at (790, 6)** | 340×122, 6 DIP inset |
| Script editor | 860×320 at (530, 8) | **860×320 at (530, 6)** | 860×320, same top anchor |
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
  the same quiet wash, the same level line at the bottom and the same
  hairline. Transparency is spent only on the floating controls; the type
  itself is fully opaque, so the desktop can never ghost through the words.
  The full-height script is what changed, not the window it runs on.
- **Floating layout**: the script's viewport spans the card's full height
  (inside the 18 px side padding). The toolbar is a child placed at the exact
  rectangle it always had (x 18, y 4, 304×32 at 100%) and floats over the
  script's own column, so the type is not pushed into a smaller box below a
  title bar.
  **Nothing is reserved above the first line.** A script opens at the card's
  top edge (measured **7.8 px**, the document's own margin) and runs behind
  the floating chrome, so the window is type from its first pixel instead of
  type below an empty header band. Only the foot keeps its room, so the last
  line can still be scrolled up onto the reading line.
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
  toolbar dissolves 3 s after the last movement and comes back the moment the
  pointer moves — over the script as much as over the controls, since the type
  column covers the card's full height. Measured: opacity 1.00 → 0.00 on the
  idle timeout, back to 1.00 on a mouse move over the script viewport. The
  editor and the resting pill keep their controls on screen, because there
  they are the point.
- **Editor and menu** use the same solid fill and hairline; the editor puts the
  type back below the control strip while it is open, because it is a document
  you type into rather than a script passing under a toolbar.
- **Light theme**: white surface, `#111111` text, `#E2E2E2` hairline, and the
  voice glow scaled to 45% so it reads as a hint rather than a blue smudge.
- **Controls**: four outline glyphs, each on its own rounded glass plate.
  The plate is a low-alpha fill (6% white on the dark card, 4.5% black on the
  light one) with a hairline and one lit line across its top, at a 9 px radius
  inside the 30 px target. Measured: the plate sits **20** levels off the card
  in dark and **16** in light, so words behind the chrome still read through
  it; hover raises the fill, press raises it again, and the glyph brightens
  from 136 to 176 without changing size. The narrow/wide toggle is two 1.4 px
  strokes rather than a filled square.
- **No menu control**: the "more" glyph earned its place only while the menu
  had nowhere else to open. It is gone, and the menu opens on a right click
  over the card or over the script, so the toolbar is four glyphs instead of
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
- The reserved header band above line one went with it: a new script used to
  open with the upper half of the card empty, because the document reserved
  the anchor offset above its first line. Measured now: first line at 7.8 px,
  no reserved space, last line still able to reach the anchor.
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

Known limits of this pass:

- Scripts are `.txt` only. The README advertised PDF and DOCX import, but no
extractor was ever wired in; the picker, drop target and README now agree.
- Dialog surfaces and control names were verified from rendered pixels and
  accessible-name properties, not with a screen reader application.
- Multi-monitor behaviour was verified by synthetic geometry (an off-screen
  save point), not by physically unplugging a display.
- The floating toolbar deliberately sits over the first line of type until it
  recedes after three seconds of stillness. The captures show that overlap;
  reserving a header strip would cost a line of script in the compact reader,
  so it stays a deliberate trade-off rather than a measured defect.
- Speech recognition itself was exercised with synthetic word timings in
  `check_voice_follow.py`; live microphone recognition accuracy was not
  measured on this machine.

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

# debounced saves, menu lifecycle, screen clamping, import, shortcuts, access
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_robustness.py

# design invariants, asserted against rendered pixels (needs a real display)
.venv/Scripts/python.exe tests/check_design.py
```

Screenshots are written to `%TEMP%\shots`. `verify_ui.py` lifts
`WDA_EXCLUDEFROMCAPTURE` for the duration of each grab and restores it, because
with capture protection active every screenshot of these windows comes back
blank — which is itself the evidence that protection is working.
