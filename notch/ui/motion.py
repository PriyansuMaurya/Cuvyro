"""Motion tokens: how long things take, and what shape the motion has.

Timings used to be literals at the call site (300 ms here, 240 there, 150
somewhere else), which meant two surfaces could be doing the same thing at
different speeds and nothing caught it. The numbers now live here, with the
reason each one has the value it does.

Two families, because the app has two kinds of change:

* **Springs** for anything that moves through space or appears from nothing.
  A spring starts from the value on screen and carries the velocity it has,
  so re-targeting mid-flight continues the motion instead of restarting it.
  Apple's two parameters: ``response`` is roughly how long the value takes to
  reach its target (lower is snappier, and it is not a duration), ``damping``
  controls overshoot (1.0 is critically damped, below that bounces).
* **Crossfades** for a change of state that has no position: ink, opacity,
  a highlight moving from one word to the next. There is nothing to grab, so
  a fixed duration is honest and cheaper than a spring.

Shape rules the values follow:

* An appearance is slower than its dismissal (``NOTE_APPEAR`` 0.26 s against
  ``NOTE_DISMISS`` 0.18 s). Arriving needs to be read; leaving must not hold
  the user up.
* Bounce is only for motion the user's own hand started. A note created by a
  click carries momentum, so ``NOTE_APPEAR`` is slightly under-damped at
  0.92. Anything that appears from a menu, a shortcut or a timer is
  critically damped: an overshoot there has no cause the eye can see.
* Everything sits inside one frame budget: the longest spring here settles
  in about 0.5 s, and no crossfade exceeds 300 ms.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SpringSpec:
    """A damped spring, in Apple's two designer-facing parameters."""

    damping: float
    response: float


# ── Space and appearance ─────────────────────────────────────────────────
# The reader's own card. Critically damped: it is a surface the user is
# about to read, and a bounce under the words would be a distraction.
CARD = SpringSpec(1.0, 0.4)

# A dialog or a floating surface arriving over the card.
SHEET = SpringSpec(1.0, 0.3)

# Sticky notes. Created by a click, so the appearance carries a little of the
# hand's momentum; dismissed by intent, so the exit is clean and quicker.
NOTE_APPEAR = SpringSpec(0.92, 0.26)
NOTE_DISMISS = SpringSpec(1.0, 0.18)

# A hint that fades in under a still pointer. Short, because it is answering
# a question the user already asked by hovering.
TOOLTIP = SpringSpec(1.0, 0.16)

# A control's hover lift and press. Under-damped on purpose: at this size the
# overshoot reads as the control responding under the pointer.
CONTROL = SpringSpec(0.82, 0.24)

# A dragged window released. Long enough to absorb the last pointer frame,
# short enough that it never looks like the note drifting on its own.
NOTE_RELEASE = SpringSpec(1.0, 0.22)


# ── Crossfades, in seconds ───────────────────────────────────────────────
# The reading tiers. Fast enough that the paint is never behind the voice.
TIER_FADE = 0.12

# A window appearing or leaving: the card at launch, help, setup. All three
# used to carry their own value (400, 300, 300 ms); one number now, because
# they are the same event at different sizes.
WINDOW_FADE = 0.3

# The floating controls. Receding is passive, so it may take its time; coming
# back is an answer to the pointer and reads as slow the moment it lags.
CHROME_RECEDE = 0.24
CHROME_RETURN = 0.16

# The text swap during a resize, and the speed hint that follows the wheel.
# Both sit on top of motion the user is already watching, so they stay short.
TEXT_SWAP = 0.15
SPEED_HINT = 0.10

# A menu appearing under the pointer. It carries no momentum of its own, so
# this is a plain fade rather than a spring.
MENU_APPEAR = 0.12

# A change of appearance (light and dark). The old frame is held over the new
# one and released over this long, so every surface recolours as one gesture
# rather than each widget flashing on its own repaint. Long enough to read as
# a dissolve, short enough that it never delays the next click.
THEME_CROSSFADE = 0.22


# ── Note appearance ──────────────────────────────────────────────────────
# A note arrives at 94% and settles to full size. The scale is small on
# purpose: large enough to read as an arrival, small enough that the text
# does not visibly reflow on the way in.
NOTE_APPEAR_SCALE = 0.94

# A note leaves at 90%, a little smaller than it arrived, and (when it was
# opened from the reader) travels back to the card at the same time. The
# extra few percent is what makes the exit read as returning into the
# reader rather than as a fade in place.
NOTE_DISMISS_SCALE = 0.90

# A released note keeps the velocity it was let go with for exactly one
# frame, capped at six pixels. The pointer already chose where the note
# belongs, so this is not a throw: it is the difference between the last
# frame the pointer reported and the release, which is why the distance is
# small and bounded rather than projected.
NOTE_RELEASE_SECONDS = 1.0 / 60.0
NOTE_RELEASE_LIMIT = 6.0
