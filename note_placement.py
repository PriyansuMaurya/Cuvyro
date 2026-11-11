"""Choosing where a new sticky note lands.

A note used to open in a fixed cascade: beside the reader, then 24 DIP
down-right of the previous one. Two notes filled that cascade with overlaps
and a third could land over the reader's controls. Placement is now a small
search over the desktop's work areas: random candidates, scored so the chosen
spot never covers another note or an essential control, stays inside the work
area, prefers the screen the reader is on, and keeps its distance from the
notes already open so the desktop does not turn into one stack.

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
# Random candidates per work area. Enough that the landing spot moves between
# launches, few enough that a crowded desktop still scores in microseconds.
SAMPLES = 64
# Lattice step used only when no random candidate was clear. A small display
# with several notes already open can lose every coarse slot; a 16 px sweep
# finds the gap between them when one exists.
FINE_STEP = 16


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


def _candidate_points(area: QRect, size: QSize, rng: random.Random,
                      samples: int) -> list:
    """Places the top-left corner could go, random plus a coarse lattice.

    The random samples are what make the placement feel alive. The lattice is
    the safety net: on a desktop already crowded with notes the random samples
    keep missing the one free slot, and the grid does not.
    """
    max_x = area.right() - size.width() + 1
    max_y = area.bottom() - size.height() + 1
    if max_x < area.left() or max_y < area.top():
        # The note is larger than the work area. There is nowhere to put it
        # whole; the caller clamps it to the screen afterwards.
        return [(area.left(), area.top())]

    span_x = max_x - area.left()
    span_y = max_y - area.top()
    points = [
        (area.left(), area.top()),
        (max_x, area.top()),
        (area.left(), max_y),
        (max_x, max_y),
    ]
    for _ in range(max(0, samples)):
        points.append((area.left() + rng.randint(0, span_x),
                       area.top() + rng.randint(0, span_y)))

    step_x = max(1, size.width() // 2)
    step_y = max(1, size.height() // 2)
    x = area.left()
    while x <= max_x:
        y = area.top()
        while y <= max_y:
            points.append((x, y))
            y += step_y
        points.append((x, max_y))
        x += step_x
    for y in range(area.top(), max_y + 1, step_y):
        points.append((max_x, y))

    return list(dict.fromkeys(points))


def _lattice(area: QRect, size: QSize, step: int) -> list:
    """Every corner on a ``step`` lattice, clipped to the work area."""
    max_x = area.right() - size.width() + 1
    max_y = area.bottom() - size.height() + 1
    if max_x < area.left() or max_y < area.top():
        return [(area.left(), area.top())]
    points = []
    for x in range(area.left(), max_x + 1, max(1, step)):
        for y in range(area.top(), max_y + 1, max(1, step)):
            points.append((x, y))
    return points


def _best_candidate(points, area: QRect, size: QSize, notes, hard, soft,
                    preferred: QRect | None, rng: random.Random):
    """Score ``points``; returns ``(key, rect)`` for the best one."""
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
            rng.random(),              # break ties unpredictably
        )
        if best_key is None or key < best_key:
            best_key = key
            best = candidate
    return best_key, best


def choose_position(size: QSize, areas, notes=(), controls=(), avoid=(),
                    preferred: QRect | None = None,
                    rng: random.Random | None = None,
                    samples: int = SAMPLES) -> QPoint:
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
    best_key = None
    best = None
    for area in rooms:
        points = _candidate_points(_inset(area), size, rng, samples)
        key, candidate = _best_candidate(points, area, size, notes, hard,
                                         soft, preferred, rng)
        if key is not None and (best_key is None or key < best_key):
            best_key, best = key, candidate

    # The coarse candidates are for feel; they are not exhaustive. When the
    # best they found still covers something, sweep the work area finely
    # before accepting an overlap: on a small display with a few notes open,
    # the free gap is real but easy for random samples to step over.
    if best is not None and best_key[0]:
        best_key = None
        best = None
        for area in rooms:
            points = _lattice(_inset(area), size, FINE_STEP)
            key, candidate = _best_candidate(points, area, size, notes, hard,
                                             soft, preferred, rng)
            if key is not None and (best_key is None or key < best_key):
                best_key, best = key, candidate

    if best is None:
        # A platform that reports no screens at all. The caller still has to
        # put the window somewhere; the clamp on show takes it from here.
        return QPoint(0, 0)
    return best.topLeft()
