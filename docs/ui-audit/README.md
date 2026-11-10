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
baseline pitch inside a paragraph is **24 px**, and the text band height is
16 px (12 pt Segoe UI at 96 DPI).

Surface treatment, also measured from the captures:

- **Hairline border**: one pixel of `#2A2C2A` (dark) / `#E2E2E2` (light), drawn
  last so the voice glow cannot wash it out. The stroke is inset half a pixel
  and lands on exactly one pixel row, with no anti-aliasing blur.
- **Rounded corners**: 24 px compact/editor, 12 px note.
- **Card material: unchanged.** The reader is still a surface, not a hole: the
  fill stays at **0.97** in the dark theme and fully opaque in the light one
  (measured centre alpha **247** / **255**), with the same quiet wash, the same
  level line at the bottom and the same hairline. The full-height script is
  what changed, not the window it runs on.
- **Floating layout**: the script's viewport is the whole window. The toolbar
  is a child placed at the exact rectangle it always had (x 18, y 4,
  304×32 at 100%) and simply rises above the type, which runs underneath it
  at up to **23%** of full coverage and dissolves to nothing at both edges.
  The reading line did not move: the document reserves the 36 px strip the
  controls float over, so the anchor is at **64.3 px** exactly as before, and
  the first line of a script can now be scrolled down to the anchor instead of
  sticking against the top edge.
- **Controls on a three-second leash**: while the reader is reading, the
  toolbar dissolves 3 s after the last movement and comes back the moment the
  pointer moves — over the script as much as over the controls, since the type
  covers the whole window. Measured: opacity 1.00 → 0.00 on the idle timeout,
  back to 1.00 on a mouse move over the script viewport. The editor and the
  resting pill keep their controls on screen, because there they are the point.
- **Editor and menu** keep the 0.99 fill and the hairline; the editor puts the
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
- The script viewport cut its type at both edges, and a painted band over
  the lower one hid the half-line there. The band was a rectangle pretending
  to be a fade: it matched one background (a theme restored from settings
  left a dark band across a light card) and it covered words instead of
  letting them leave. Both edges now dissolve the type itself (`SoftEdgeTextEdit`):
  the document is painted offscreen and its alpha is driven by distance from
  the line being read, with a light blur growing in the same rows. Nothing is
  painted on top.
  The line being read is the only opaque type in the card: it stays
  pixel-identical to the sharp render, and coverage falls with distance from
  it until the outermost row is fully transparent — the type leaves the card
  by losing presence, not by being cut. The fall is a curve, not a straight
  ramp: a straight ramp over a 64 px half kept the neighbouring line at
  two-thirds strength, which reads as two current lines.
  At the first and last lines the scrollbar cannot reach the anchor, so the
  reading band moves to the line instead of leaving it in the dissolve.
  Measured across both themes and 28 scroll offsets: no pixel added outside
  the type, the reading line at 1.0000 of its sharp alpha, a median of 0.48
  for every row outside it, the outermost row at 0% (fully transparent),
  nine distinct opacity levels between 10% and 95%, and type in the toolbar
  strip peaking at 59/255 against 255 on the reading line.
- The light theme applied the full-strength coloured glow over white, which
  read as a smudge rather than a status hint.

## Reproducing

```bash
# geometry + screenshots of all three surfaces
.venv/Scripts/python.exe packaging/verify_ui.py

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
