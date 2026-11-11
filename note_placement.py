"""Choosing where a new sticky note lands.

A note used to open in a fixed cascade: beside the reader, then 24 DIP
down-right of the previous one. Two notes filled that cascade with overlaps
and a third could land over the reader's controls. Placement is now a bounded search
over the desktop's work areas: an anchored lattice of note-sized cells,
scored so the chosen spot never covers another note or an essential control,
stays inside the work area, prefers the screen the reader is on, and keeps
its distance from the notes already open so the desktop does not turn into
one stack. Because the cells are at least one note plus clearance apart, two
notes picked from the lattice cannot overlap, and the remaining cells stay
usable for the next note instead of being fragmented by random coordinates.

The search is deliberately self-contained: it takes rectangles in, returns a
point, and touches no widgets. That keeps it testable without a display and
keeps the overlay free of geometry arithmetic.
"""

import random

from PyQt5.QtCore import QPoint, QRect, QSize

# Gap kept from the edge of a work area. Windows' work areas already exclude
# the taskbar, so this is breathing room, not a taskbar allowance.
EDGE_MARGIN = 10
# Gap kept between a note and anything it must not cover: another note, or an
# essential control. Zero overlap is not enough: two windows touching read as
# one slab.
CLEARANCE = 8
# Upper bound on candidates examined. The anchored lattice is already small
# (one cell per note-sized slot); this keeps a pathological work area or a
# tiny note from making the search unbounded.
GRID_LIMIT = 4096
# Lattice step used only when no anchored cell was clear. A small display
# with several notes already open can lose every coarse slot; a 16 px sweep
# finds the gap between them when one exists, and is itself bounded.
FINE_STEP = 16
FINE_LIMIT = 10000


def _inset(area: QRect) -> QRect:
    """The part of ``area`` a note may actually occupy."""
    return area.adjusted(EDGE_MARGIN, EDGE_MARGIN, -EDGE_MARGIN, -EDGE_MARGIN)


def _overlap(rect: QRect, obstacles) -> int:
    """Total pixel area of ``rect`` covered by ``obstacles``."""
    total = 0
    for obstacle in obstacles:
        hit = rect.intersected(obstacle)
        if not hit.isEmpty():
            total += hit.width() * hit.height()
    return total


def _separation(rect: QRect, others) -> int:
    """Gap between ``rect`` and the nearest other rectangle, in pixels.

    Manhattan distance between the edges: zero when they touch or overlap.
    Notes are ranked by this so the newest one takes the emptiest part of the
    screen instead of clustering on the first free spot.
    """
    if not others:
        return 1 << 30
    best = 1 << 30
    for other in others:
        dx = max(other.left() - rect.right(), rect.left() - other.right(), 0)
        dy = max(other.top() - rect.bottom(), rect.top() - other.bottom(), 0)
        best = min(best, dx + dy)
    return best


def _grid_points(area: QRect, size: QSize, step_x: int, step_y: int,
                 limit: int) -> list:
    """Every top-left corner on a bounded lattice inside ``area``.

    The far edge is only appended when it is at least one step away from the
    previous row or column. Appending it otherwise would put two adjacent
    candidates closer than the clearance, and the anchor's whole promise is
    that two notes chosen from this set cannot overlap.
    """
    max_x = area.right() - size.width() + 1
    max_y = area.bottom() - size.height() + 1
    if max_x < area.left() or max_y < area.top():
        # The note is larger than the work area. There is nowhere to put it
        # whole; the caller clamps it to the screen afterwards.
        return [(area.left(), area.top())]

    step_x = max(1, step_x)
    step_y = max(1, step_y)
    xs = list(range(area.left(), max_x + 1, step_x))
    if xs[-1] != max_x and max_x - xs[-1] >= step_x:
        xs.append(max_x)
    ys = list(range(area.top(), max_y + 1, step_y))
    if ys[-1] != max_y and max_y - ys[-1] >= step_y:
        ys.append(max_y)

    points = [(x, y) for x in xs for y in ys]
    if len(points) > limit:
        # Thin evenly rather than truncate: the tail of the list is the
        # bottom of the screen, and losing it would bias every placement up.
        stride = (len(points) + limit - 1) // limit
        points = points[::stride]
    return points


def _candidate_points(area: QRect, size: QSize) -> list:
    """The anchored note-sized cells across ``area``."""
    return _grid_points(area, size, size.width() + CLEARANCE,
                        size.height() + CLEARANCE, GRID_LIMIT)


def _best_candidate(points, area: QRect, size: QSize, notes, hard, soft,
                    preferred: QRect | None, rng: random.Random,
                    randomize: bool = True):
    """Score ``points``; returns ``(key, rect)`` for the best one.

    With ``randomize`` off the final tie-break is a constant, so equal
    candidates resolve by list order. That is what makes the crowded-desktop
    fallback reproducible instead of moving a little on every launch.
    """
    elsewhere = 0 if preferred is not None and area == preferred else 1
    best_key = None
    best = None
    for x, y in points:
        candidate = QRect(x, y, size.width(), size.height())
        hard_hit = _overlap(candidate, hard)
        soft_hit = _overlap(candidate, soft)
        key = (
            1 if hard_hit else 0,      # cover nothing important
            0 if area.contains(candidate) else 1,  # stay on a display
            elsewhere,                 # stay on the reader's screen
            1 if soft_hit else 0,      # keep the reader readable
            hard_hit,
            soft_hit,
            -_separation(candidate, notes),  # spread out
            rng.random() if randomize else 0.0,  # break ties unpredictably
        )
        if best_key is None or key < best_key:
            best_key = key
            best = candidate
    return best_key, best


def choose_position(size: QSize, areas, notes=(), controls=(), avoid=(),
                    preferred: QRect | None = None,
                    rng: random.Random | None = None) -> QPoint:
    """Pick a top-left corner for a ``size`` note.

    ``areas`` are the screens' available (work-area) rectangles, ``notes`` the
    windows already open, ``controls`` the rectangles that must never be
    covered, and ``avoid`` soft zones that are only penalised — the reader's
    card, which a note should sit beside rather than on top of.

    The ordering of the score is the policy:

    1. never cover a note or a control (a hard requirement);
    2. fit wholly on a display at all;
    3. stay on ``preferred`` — the screen the reader is on;
    4. keep off the soft zones;
    5. keep away from the notes already open, and randomise the rest.

    A work area with no clear spot therefore yields to another screen rather
    than opening a note over a control; a note on another screen is still
    findable, a note over the mute button is not.
    """
    rng = rng or random.Random()
    notes = [QRect(rect) for rect in notes]
    hard = [rect.adjusted(-CLEARANCE, -CLEARANCE, CLEARANCE, CLEARANCE)
            for rect in list(notes) + list(controls)]
    soft = [QRect(rect) for rect in avoid]
    preferred = QRect(preferred) if preferred is not None else None

    rooms = [QRect(area) for area in areas if not QRect(area).isEmpty()]

    def best_of(points_for, randomize):
        best_key = None
        best = None
        for area in rooms:
            points = points_for(_inset(area))
            key, candidate = _best_candidate(points, area, size, notes, hard,
                                             soft, preferred, rng, randomize)
            if key is not None and (best_key is None or key < best_key):
                best_key, best = key, candidate
        return best_key, best

    # 1. Anchored cells: clear ones cannot overlap each other, and choosing
    #    among them keeps the rest of the screen usable. The randomness is
    #    the choice of cell, not a random coordinate.
    best_key, best = best_of(lambda area: _candidate_points(area, size), True)

    # 2. No clear anchored cell: sweep finely for a gap the lattice stepped
    #    over (a small display with several notes already open).
    if best is not None and best_key[0]:
        best_key, best = best_of(
            lambda area: _grid_points(area, size, FINE_STEP, FINE_STEP,
                                      FINE_LIMIT),
            True)

    # 3. Display genuinely full: the smallest overlap, resolved in a fixed
    #    order, so the same desktop produces the same corner every launch.
    if best is not None and best_key[0]:
        best_key, best = best_of(
            lambda area: _candidate_points(area, size), False)

    if best is None:
        # A platform that reports no screens at all. The caller still has to
        # put the window somewhere; the clamp on show takes it from here.
        return QPoint(0, 0)
    return best.topLeft()
