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
- **Translucency**: 0.97 compact, 0.99 editor. Desktop text behind the card
  deviates at most **4 levels** out of 255 from the card's own fill, i.e.
  present as depth but never readable as competing content.
- **Light theme**: white surface, `#111111` text, `#E2E2E2` hairline, and the
  voice glow scaled to 45% so it reads as a hint rather than a blue smudge.
- **Controls**: outline glyphs only. The narrow/wide toggle was a filled
  square and became two 1.4 px strokes; hover brightens the glyph from 136 to
  176 (measured on the rendered button), and the shape does not change size.

## Fixes found while comparing themes

- The sticky note ignored the theme entirely and stayed charcoal on a white
  desktop.
- The lower-edge fade cached its colour and only refreshed on resize, so a
  theme restored from settings left a dark band across a light card.
- The same fade was pinned to the card's bottom edge rather than to the line
  the viewport cuts in half, so the top of the next line stayed visible as a
  grey smear under the script. It now follows that line: a sliver (the tops
  of the glyphs only) is covered outright, a line with more of itself in view
  dissolves into the cut, and a script that ends on a whole line has no band
  at all, because there is nothing to dissolve. Measured across both themes
  and 60 scroll offsets: no state leaves stray ink at the cut.
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
