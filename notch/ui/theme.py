"""The app's palette and scale, in one place.

Every surface here is a frameless rounded card, and they used to carry their
own copies of the same colours: the note was ``#1C1C1C`` next to the menu's
``#1E1E1E``, its hairline ``#2A2A2A`` next to the menu's ``#343436``. Two
greys a few levels apart do not read as a deliberate hierarchy, they read as
two people painting the same room, so the values now live here and the
surfaces refer to them.

The numbers are not new. Each one was already in use and already measured
against the surface it sits on (``tests/check_quality.py``,
``tests/check_design.py`` and ``tests/check_predictive_highlight.py`` assert
the contrast bars). Moving them into a module is what makes the palette a
system instead of a habit.

Contrast against the card, measured with the WCAG relative-luminance formula:

===========  ==========  ===========  ========================
Role         Dark        Light        Measured
===========  ==========  ===========  ========================
SCRIPT       #FAFAFA     #111111      20.1:1 / 18.9:1
SPOKEN       #8A8A8A     #767676      6.1:1 / 4.5:1
CURRENT      #A9CCFF     #0A3A8F      12.8:1 / 10.4:1
PREDICTED    #9FB3DC     #3F5382      10.0:1 / 7.6:1
INK          #F2F2F7     #1C1C1E      on SURFACE, 14.9:1 / 15.9:1
PLACEHOLDER  #8E8E93     #6E6E73      on SURFACE, 5.9:1 / 5.4:1
===========  ==========  ===========  ========================

``CURRENT`` on its own band clears 10.4:1 (dark) and 8.9:1 (light); see
``docs/predictive-highlight.md``.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Role:
    """One semantic colour, held for both themes.

    A role rather than a hex constant, because every surface has to answer
    the same question ("what is a hairline?") and only one answer should
    exist.
    """

    dark: str
    light: str

    def pick(self, dark: bool) -> str:
        return self.dark if dark else self.light


# ── Reading surface ──────────────────────────────────────────────────────
# The card is a solid surface in both themes. Transparency belongs to the
# floating controls, never to the page the words sit on.
CARD = Role("#000000", "#FFFFFF")
SCRIPT = Role("#FAFAFA", "#111111")
SPOKEN = Role("#8A8A8A", "#767676")
CURRENT = Role("#A9CCFF", "#0A3A8F")
PREDICTED = Role("#9FB3DC", "#3F5382")
BAND = Role("#0A1940", "#E1EFFF")

# ── Floating surfaces ────────────────────────────────────────────────────
# Menu, tooltip and sticky note are one material: same fill, same hairline,
# same shadow weight. The reader is the page; these are the things on it.
SURFACE = Role("#1E1E1E", "#F7F7F8")
HAIRLINE = Role("#343436", "#E4E4E6")
INK = Role("#F2F2F7", "#1C1C1E")
INK_MUTED = Role("#8E8E93", "#7A7A80")
# The placeholder is a hint, not a label, but it still has to be readable: the
# values it replaced measured 3.7:1 and 3.2:1 on the fills they sat on, under
# the 4.5:1 a line of hint text needs.
PLACEHOLDER = Role("#8E8E93", "#6E6E73")
FOCUS = Role("#0A84FF", "#007AFF")

# Shadow alpha per theme. Dark draws its depth from a near-black ring, so a
# light shadow there disappears; light needs a softer, wider one.
SHADOW_ALPHA = {True: 26, False: 200}


# ── Shape ────────────────────────────────────────────────────────────────
# One radius per family, largest surface to smallest. A control is never
# rounder than the sheet it sits on.
RADIUS = {
    "card": 24,       # the reading pill
    "compact": 20,    # the narrow reader, one step tighter
    "dialog": 16,     # setup and help
    "menu": 14,
    "note": 12,
    "surface": 8,     # tooltip, and the control that opened it
}


# ── Space and type ───────────────────────────────────────────────────────
# A 4 px rhythm. Every gap in the app is one of these steps or a multiple of
# one, which is what lets a menu, a tooltip and a note line up by eye.
SPACE = (4, 8, 12, 16, 24, 32)

# Type sizes in DIP. The reader's own size is user-controlled and lives in
# the window's state; these are the labels and fields around it.
TYPE = {
    "label": 10,
    "field": 12,
    "note": 12,
    "title": 13,
    "display": 15,
}
