# UI audit captures

Before/after evidence for the Windows overlay work, matching the reference
geometry recorded in `Notch_Windows_Replica_Spec.md` section 2. All captures are
from one 1920×1080 display at 100% scaling, grabbed at the window's own rect, so
the pixel sizes are 1:1 with the DIP values below.

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
- **Controls**: outline glyphs only. The narrow/wide toggle was a filled
  square and became two 1.4 px strokes; hover brightens the glyph from 136 to
  176 (measured on the rendered button), and the shape does not change size.

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

## Reproducing

```bash
# geometry + screenshots of all three surfaces
.venv/Scripts/python.exe packaging/verify_ui.py

# typography, speech-follow accuracy, scroll glide, anchor and transparency
.venv/Scripts/python.exe packaging/check_quality.py

# capture-protection assertion (expects 0x11 on overlay and note)
.venv/Scripts/python.exe packaging/verify_ui.py --check-protection

# wheel scroll, timed playback, persistence, note lifecycle
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe packaging/check_behavior.py

# line-gated voice follow: pauses, speeds, multi-line finals, reflow
QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe packaging/check_voice_follow.py

# design invariants, asserted against rendered pixels (needs a real display)
.venv/Scripts/python.exe packaging/check_design.py
```

Screenshots are written to `%TEMP%\shots`. `verify_ui.py` lifts
`WDA_EXCLUDEFROMCAPTURE` for the duration of each grab and restores it, because
with capture protection active every screenshot of these windows comes back
blank — which is itself the evidence that protection is working.
