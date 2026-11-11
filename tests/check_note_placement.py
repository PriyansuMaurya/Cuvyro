"""Where a new sticky note lands, and what happens to that spot afterwards.

The placement search is checked directly against fabricated work areas, then
the reader is driven offscreen: new notes must land inside the display and
clear of the notes already open and of the reader's controls; a dragged note
must keep the corner the reader gave it across another note opening and
across a relaunch; and a note left on a display that is gone must come back
into view.

Run headless:

    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_note_placement.py
"""

import json
import os
import random
import sys
import tempfile

from PyQt5.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

from note_placement import (  # noqa: E402
    GRID_LIMIT, _candidate_points, choose_position,
)

failures = []
NOTE_SIZE = QSize(260, 165)


def check(name, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not condition:
        failures.append(name)


def overlapping(a, b):
    return not QRect(a).intersected(QRect(b)).isEmpty()


def place(notes, controls=(), avoid=(), areas=None, preferred=None, seed=1):
    areas = list(areas) if areas is not None else [QRect(0, 0, 1920, 1040)]
    preferred = preferred if preferred is not None else areas[0]
    return choose_position(NOTE_SIZE, areas, notes=notes, controls=controls,
                           avoid=avoid, preferred=preferred,
                           rng=random.Random(seed))


def drag(note, delta):
    """Drive the note's own drag handlers with the events a mouse produces."""
    start = QPoint(20, 10)  # inside the header strip
    origin = note.mapToGlobal(start)
    moved = origin + delta

    def send(kind, local, global_pos, button, buttons):
        event = QMouseEvent(kind, QPointF(local), QPointF(global_pos),
                            button, buttons, Qt.NoModifier)
        QApplication.sendEvent(note, event)

    send(QEvent.MouseButtonPress, start, origin, Qt.LeftButton, Qt.LeftButton)
    send(QEvent.MouseMove, start + delta, moved, Qt.NoButton, Qt.LeftButton)
    send(QEvent.MouseButtonRelease, start + delta, moved, Qt.LeftButton, Qt.NoButton)


def stored_notes(directory):
    try:
        with open(os.path.join(directory, "state.json"), encoding="utf-8") as handle:
            return json.load(handle).get("notes") or []
    except (OSError, ValueError):
        return []


def write_state(directory, notes):
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "state.json"), "w", encoding="utf-8") as handle:
        json.dump({"schema": 1, "script": "", "notes": notes, "settings": {}}, handle)


def make_overlay(directory):
    import ui_overlay
    from store import Store

    win = ui_overlay.NotchOverlay()
    win._store = Store(directory)
    win._voice_mode = False  # keep the microphone out of an automated run
    win.show()
    QTest.qWait(300)
    return win


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)

    # ── The search itself ────────────────────────────────────────────
    screen = QRect(0, 0, 1920, 1040)
    notes = []
    for index in range(8):
        rect = QRect(place(notes, seed=index), NOTE_SIZE)
        notes.append(rect)
    check("every note lands wholly inside the work area",
          all(screen.contains(rect) for rect in notes),
          "; ".join(str(r) for r in notes if not screen.contains(r)))
    check("notes never overlap each other",
          not any(overlapping(a, b) for i, a in enumerate(notes)
                  for b in notes[i + 1:]),
          f"{len(notes)} notes")
    check("notes spread instead of stacking in a cascade",
          len({(r.x(), r.y()) for r in notes}) == len(notes),
          f"{len({(r.x(), r.y()) for r in notes})} distinct corners")

    first = place([], seed=1)
    second = place([], seed=2)
    check("placement is random, not a fixed corner", first != second,
          f"{first} vs {second}")

    # A small work area is the case that used to flake: random candidates
    # fragmented the space and the fourth note covered the first. Anchored
    # cells must fit four notes on 800x600 for every seed.
    small = [QRect(0, 0, 800, 600)]
    fits = True
    for seed in range(50):
        placed = []
        for _ in range(4):
            placed.append(QRect(place(placed, areas=small, seed=seed),
                                NOTE_SIZE))
        if any(overlapping(a, b) for i, a in enumerate(placed)
               for b in placed[i + 1:]):
            fits = False
            break
    check("four notes fit an 800x600 desktop without overlap, any seed", fits)

    # When nothing is free the fallback must be stable: the same desktop
    # cannot put the note somewhere new on every launch.
    buried = [QRect(0, 0, 800, 600)]
    full_first = place(buried, areas=small, seed=1)
    full_second = place(buried, areas=small, seed=2)
    check("a full desktop lands on the same corner",
          full_first == full_second, f"{full_first} vs {full_second}")

    # The anchored search must stay bounded even for an 8K work area and a
    # small note, where the raw lattice is far larger than the cap.
    wide = _candidate_points(QRect(0, 0, 7680, 4320), QSize(60, 40))
    check("the placement search is bounded", len(wide) <= GRID_LIMIT,
          f"{len(wide)} candidates, cap {GRID_LIMIT}")

    # ── Essential controls are hard obstacles ────────────────────────
    rail = [QRect(1540, 420, 44, 200), QRect(1540, 620, 44, 120)]
    controls = rail + [QRect(0, 0, 340, 122)]
    clear = []
    for index in range(6):
        clear.append(QRect(place(clear, controls=controls, seed=index), NOTE_SIZE))
    check("no note opens over the reader's controls",
          not any(overlapping(rect, control) for rect in clear
                  for control in controls),
          f"{len(clear)} notes")

    # A note keeps the card readable when there is room beside it.
    card = QRect(0, 0, 1920, 160)
    beside = [QRect(place([], avoid=[card], seed=index), NOTE_SIZE)
              for index in range(3)]
    check("notes sit beside the reader, not on it",
          not any(overlapping(rect, card) for rect in beside))

    # ── Several displays ─────────────────────────────────────────────
    left = QRect(0, 0, 1200, 1000)
    right = QRect(1200, 0, 1200, 1000)
    on_left = [QRect(place([], areas=[left, right], preferred=left, seed=index),
                     NOTE_SIZE) for index in range(3)]
    check("notes stay on the reader's screen while it has room",
          all(left.contains(rect) for rect in on_left),
          "; ".join(str(r) for r in on_left))

    tiny = QRect(0, 0, 90, 90)
    spilled = QRect(place([], areas=[tiny, right], preferred=tiny, seed=4),
                    NOTE_SIZE)
    check("a crowded reader screen yields to another display",
          right.contains(spilled), str(spilled))

    oversized = QRect(place([], areas=[QRect(0, 0, 800, 600)],
                            preferred=QRect(0, 0, 800, 600), seed=5),
                      QSize(1200, 900))
    check("a note larger than the display still starts on it",
          QRect(0, 0, 800, 600).contains(oversized.topLeft()))

    # ── The reader opens notes on screen, clear of itself ────────────
    tmp = tempfile.mkdtemp(prefix="notch-place-")
    win = make_overlay(tmp)
    available = QApplication.primaryScreen().availableGeometry()
    note = win._open_note()
    check("a new note opens inside the work area",
          available.contains(note.geometry()),
          f"{note.geometry()} in {available}")
    check("a new note does not cover the reader's controls",
          not any(overlapping(note.geometry(), control)
                  for control in win._control_rects()),
          f"{len(win._control_rects())} controls")
    check("a new note does not sit on the reader",
          not overlapping(note.geometry(), win.geometry()),
          f"note {note.geometry()} reader {win.geometry()}")

    opened = [win._open_note() for _ in range(3)]
    every = [note] + opened
    check("notes opened one after another do not overlap",
          not any(overlapping(a.geometry(), b.geometry())
                  for i, a in enumerate(every) for b in every[i + 1:]),
          "; ".join(str(n.geometry()) for n in every))
    check("notes stay on screen as they accumulate",
          all(available.contains(n.geometry()) for n in every))

    # ── Dragging is preserved ────────────────────────────────────────
    dragged = opened[-1]
    size_before = dragged.size()
    dragged.move(QPoint(60, 60))
    QTest.qWait(30)
    drag(dragged, QPoint(40, 25))
    QTest.qWait(30)
    moved = dragged.pos()
    check("a note follows the pointer while dragged",
          moved == QPoint(100, 85), str(moved))
    check("dragging moves the note without resizing it",
          dragged.size() == size_before,
          f"{size_before} -> {dragged.size()}")

    still = opened[0]
    still_at = still.pos()
    win._open_note()
    QTest.qWait(30)
    check("opening another note leaves a placed note alone",
          still.pos() == still_at and dragged.pos() == moved,
          f"{still_at} -> {still.pos()}")

    dragged._edit.setPlainText("Board block reads")
    QTest.qWait(60)
    win._save_state()
    check("the dragged note is written to the state file",
          any((n.get("x"), n.get("y")) == (moved.x(), moved.y())
              for n in stored_notes(tmp)),
          repr(stored_notes(tmp)))

    # ── Restart: the reader's corner survives ────────────────────────
    relaunched = make_overlay(tmp)
    restored = [n for n in relaunched._notes if n.text() == "Board block reads"]
    check("a relaunch restores the written note",
          len(restored) == 1, f"{len(relaunched._notes)} restored")
    check("a relaunch keeps the corner the reader dragged to",
          bool(restored) and restored[0].pos() == moved,
          f"{restored[0].pos() if restored else None} vs {moved}")

    # The same rule for a note left on a monitor that is gone.
    gone = tempfile.mkdtemp(prefix="notch-place-gone-")
    write_state(gone, [{"id": "abc123", "text": "remember the close",
                        "x": 60_000, "y": 60_000}])
    recovered = make_overlay(gone)
    check("a note left on a detached display comes back into view",
          bool(recovered._notes)
          and QApplication.primaryScreen().availableGeometry().contains(
              recovered._notes[0].geometry()),
          f"{recovered._notes[0].geometry() if recovered._notes else None}")

    # ── Resolution and layout changes ────────────────────────────────
    stray = recovered._notes[0]
    stray.move(QPoint(available.right() + 400, available.bottom() + 400))
    recovered._on_screens_changed()
    QTest.qWait(30)
    check("a screen change pulls a stray note back into view",
          available.contains(stray.geometry()), str(stray.geometry()))

    stray.move(QPoint(available.right() - 4, available.top() + 40))
    recovered._on_screens_changed()
    QTest.qWait(30)
    check("a partially off-screen note is pulled fully back",
          available.contains(stray.geometry()), str(stray.geometry()))

    stray.resize(available.width() + 400, available.height() + 300)
    recovered._on_screens_changed()
    QTest.qWait(30)
    check("a note larger than the display is shrunk to fit",
          stray.width() <= available.width()
          and stray.height() <= available.height()
          and available.contains(stray.geometry()),
          f"{stray.size()} in {available.size()}")

    # The reader's own resize (the narrow reading mode) must not make the
    # placement stale: the rail moves, and a note opened afterwards still
    # avoids it.
    compact = make_overlay(tempfile.mkdtemp(prefix="notch-place-compact-"))
    compact._toggle_compact_mode()
    QTest.qWait(600)
    compact_note = compact._open_note()
    check("the narrow reader still gets a clear, on-screen note",
          QApplication.primaryScreen().availableGeometry().contains(
              compact_note.geometry())
          and not any(overlapping(compact_note.geometry(), control)
                      for control in compact._control_rects()),
          f"{compact_note.geometry()}")

    for window in (win, relaunched, recovered, compact):
        window._close_all_notes()
        window.close()
    QTest.qWait(50)

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All note-placement checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
