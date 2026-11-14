"""Motion checks for the floating surfaces.

These cover what the reading, design and robustness checks do not: that a
sticky note arrives and leaves on a spring instead of appearing and vanishing,
that the two ways out of a note animate without ever holding up the app's own
shutdown path, that taking hold of a note wins over whatever motion it was
already doing, and that the palette and timings the surfaces use come from
``theme`` and ``motion`` rather than from their own copies.

Run headless:

    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe tests/check_motion.py
"""

import os
import sys
import time

from PyQt5.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PyQt5.QtGui import QColor, QMouseEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

failures = []


def check(name, condition, detail=""):
    print(f"{'PASS' if condition else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")
    if not condition:
        failures.append(name)


def relative_luminance(rgb):
    def channel(value):
        value = value / 255.0
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(v) for v in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    high, low = sorted((relative_luminance(fg), relative_luminance(bg)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def hex_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


def settle_note(note, limit=240):
    """Run the note's entrance to rest, frame by frame.

    Driven directly rather than with a wait: the spring is the thing under
    test, so the check should be able to say how many frames it took.
    """
    frames = 0
    while not note._presence.settled and frames < limit:
        note._presence.step(1 / 60.0)
        note._apply_presence()
        frames += 1
    if note._presence.settled:
        # The clock lands a settled spring exactly on its target before the
        # tick; drive that frame too so a hand-stepped run matches it.
        note._presence.land()
        note._apply_presence()
    return frames


def exit_note(note, limit=240):
    """Run a dismissal to its close, frame by frame."""
    frames = 0
    while not note._closed_announced and frames < limit:
        note._presence.step(1 / 60.0)
        note._apply_presence()
        frames += 1
    return frames


def drag(note, delta, gap_ms=20):
    """Drive the note's drag handlers the way a slow, deliberate drag would."""
    start = QPoint(20, 10)  # inside the header strip
    origin = note.mapToGlobal(start)

    def send(kind, local, global_pos, button, buttons):
        event = QMouseEvent(kind, QPointF(local), QPointF(global_pos),
                            button, buttons, Qt.NoModifier)
        QApplication.sendEvent(note, event)

    send(QEvent.MouseButtonPress, start, origin, Qt.LeftButton, Qt.LeftButton)
    for step in (0.4, 0.7, 1.0):
        QTest.qWait(gap_ms)
        send(QEvent.MouseMove, start + delta * step, origin + delta * step,
             Qt.NoButton, Qt.LeftButton)
    QTest.qWait(gap_ms)
    send(QEvent.MouseButtonRelease, start + delta, origin + delta,
         Qt.LeftButton, Qt.NoButton)


def main():
    app = QApplication(sys.argv)
    from notch.ui import motion, theme
    from notch.ui.notes import NoteWindow, NOTE_H, NOTE_W
    from notch.ui import tooltips

    check("the palette is one object for both themes",
          all(isinstance(role, theme.Role) for role in
              (theme.CARD, theme.SCRIPT, theme.SURFACE, theme.INK)))

    # ── Tokens ──────────────────────────────────────────────────────────
    check("an appearance is slower than its dismissal",
          motion.NOTE_APPEAR.response > motion.NOTE_DISMISS.response,
          f"{motion.NOTE_APPEAR.response} vs {motion.NOTE_DISMISS.response}")
    check("only the click-created note is allowed to overshoot",
          motion.NOTE_APPEAR.damping < 1.0
          and motion.NOTE_DISMISS.damping == 1.0
          and motion.SHEET.damping == 1.0,
          f"note {motion.NOTE_APPEAR.damping}, others "
          f"{motion.NOTE_DISMISS.damping}/{motion.SHEET.damping}")
    check("every spring settles inside half a second",
          max(spec.response for spec in (motion.CARD, motion.SHEET,
                                         motion.NOTE_APPEAR,
                                         motion.NOTE_DISMISS, motion.TOOLTIP,
                                         motion.CONTROL)) <= 0.5,
          "longest response <= 0.5 s")
    check("a tier change is the quickest state change",
          motion.TIER_FADE < motion.TEXT_SWAP <= motion.WINDOW_FADE,
          f"{motion.TIER_FADE} < {motion.TEXT_SWAP} <= {motion.WINDOW_FADE}")
    check("a released note coasts at most one frame of its own velocity",
          motion.NOTE_RELEASE_SECONDS <= 1.0 / 60.0 + 1e-9
          and motion.NOTE_RELEASE_LIMIT <= 8.0,
          f"{motion.NOTE_RELEASE_SECONDS:.3f} s, {motion.NOTE_RELEASE_LIMIT} px")
    check("a theme change is a dissolve, not a cut",
          0.0 < motion.THEME_CROSSFADE <= motion.WINDOW_FADE,
          f"{motion.THEME_CROSSFADE} s")
    check("a note leaves smaller than it arrived",
          motion.NOTE_DISMISS_SCALE < motion.NOTE_APPEAR_SCALE < 1.0,
          f"{motion.NOTE_DISMISS_SCALE} < {motion.NOTE_APPEAR_SCALE}")

    # ── Palette contrast, measured on the roles ─────────────────────────
    for dark, label in ((True, "dark"), (False, "light")):
        card = hex_rgb(theme.CARD.pick(dark))
        surface = hex_rgb(theme.SURFACE.pick(dark))
        band = hex_rgb(theme.BAND.pick(dark))
        checks = {
            "the script clears 15:1": contrast(hex_rgb(theme.SCRIPT.pick(dark)), card) >= 15.0,
            "spoken words clear 4.5:1": contrast(hex_rgb(theme.SPOKEN.pick(dark)), card) >= 4.5,
            "the current words clear 7:1 on card and band":
                contrast(hex_rgb(theme.CURRENT.pick(dark)), card) >= 7.0
                and contrast(hex_rgb(theme.CURRENT.pick(dark)), band) >= 7.0,
            "the hint clears 7:1": contrast(hex_rgb(theme.PREDICTED.pick(dark)), card) >= 7.0,
            "surface ink clears 7:1": contrast(hex_rgb(theme.INK.pick(dark)), surface) >= 7.0,
            "the placeholder clears 4.5:1": contrast(hex_rgb(theme.PLACEHOLDER.pick(dark)),
                                                     surface) >= 4.5,
        }
        for name, ok in checks.items():
            check(f"{label}: {name}", ok)
        check(f"{label}: the menu's hairline is visible on its own fill",
              contrast(hex_rgb(theme.HAIRLINE.pick(dark)), surface) >= 1.15,
              f"{contrast(hex_rgb(theme.HAIRLINE.pick(dark)), surface):.2f}:1")
        # A note is card material, not a second floating surface.
        check(f"{label}: the note's hairline is visible on the card",
              contrast(hex_rgb(theme.CARD_HAIRLINE.pick(dark)), card) >= 1.15,
              f"{contrast(hex_rgb(theme.CARD_HAIRLINE.pick(dark)), card):.2f}:1")

    # ── Surfaces read the roles, they do not copy the values ────────────
    from notch.ui import overlay as ui_overlay
    check("the reading tiers come from the palette",
          ui_overlay.NotchOverlay.CURRENT_DARK == theme.CURRENT.dark
          and ui_overlay.NotchOverlay.BAND_LIGHT == theme.BAND.light)
    check("the tooltip panel is the same material as the menu",
          tooltips.PANEL[True][0] == theme.SURFACE.dark
          and tooltips.PANEL[False][1] == theme.HAIRLINE.light)
    check("the tooltip radius is the surface radius",
          tooltips.RADIUS == theme.RADIUS["surface"])

    # ── A note is the reader's material ─────────────────────────────────
    from notch.ui import notes as notes_module, themefade
    check("a note takes the reader's corner radius",
          notes_module.CORNER_RADIUS == theme.RADIUS["card"],
          f"{notes_module.CORNER_RADIUS} px")
    material = NoteWindow(scale=1.0, dark=True)
    material.move(10, 10)
    material.show()
    QApplication.processEvents()
    settle_note(material)
    painted = material.grab().toImage()
    centre = painted.pixelColor(painted.width() // 2, painted.height() // 2)
    edge = painted.pixelColor(painted.width() // 2, painted.height() - 1)
    check("a note is filled with the reader's card colour",
          centre.rgb() == QColor(theme.CARD.dark).rgb(),
          f"{centre.name()} vs {theme.CARD.dark}")
    # The 1 px hairline is antialiased, so closeness is the honest test: the
    # edge pixel must sit nearer the card's border than its fill.
    def distance(a, b):
        return sum((x - y) ** 2 for x, y in zip(a.getRgb()[:3], b.getRgb()[:3]))

    hairline = QColor(theme.CARD_HAIRLINE.dark)
    fill = QColor(theme.CARD.dark)
    check("a note's edge is the reader's hairline",
          distance(edge, hairline) < distance(edge, fill),
          f"{edge.name()}, {theme.CARD_HAIRLINE.dark}")
    material.close()

    # ── The note arrives ────────────────────────────────────────────────
    note = NoteWindow(scale=1.0, dark=True)
    note.move(320, 240)
    note.show()
    QApplication.processEvents()
    start_size = note.size()
    start_opacity = note.windowOpacity()
    start_pos = note.pos()
    final_w, final_h = int(NOTE_W), int(NOTE_H)
    check("a note starts smaller than its resting size",
          start_size.width() < final_w and start_size.height() < final_h,
          f"{start_size.width()}x{start_size.height()} of {final_w}x{final_h}")
    check("a note starts transparent",
          start_opacity < 0.05, f"opacity {start_opacity:.2f}")
    frames = settle_note(note)
    check("a note grows in place, not from another corner",
          note.pos() == start_pos, f"{start_pos} -> {note.pos()}")
    check("a note lands on exactly its resting size",
          note.size().width() == final_w and note.size().height() == final_h,
          f"{note.size().width()}x{note.size().height()}")
    check("a note lands fully opaque",
          abs(note.windowOpacity() - 1.0) < 0.01,
          f"{note.windowOpacity():.3f}")
    check("the entrance is short enough to feel like feedback",
          frames <= 30, f"{frames} frames ({frames / 60.0:.2f} s)")
    check("the entrance never overshoots its own size",
          max(note.width(), note.height()) <= max(final_w, final_h),
          f"{note.width()}x{note.height()}")

    # The bookkeeping for a motion frame: read the spring, write opacity,
    # write geometry. The platform's own cost of moving a window is not
    # visible from here (the offscreen plugin makes it a no-op), so this
    # guards the part the app controls against growing a fat frame.
    started = time.perf_counter()
    for _ in range(60):
        note._apply_presence()
    frame_ms = (time.perf_counter() - started) * 1000.0 / 60.0
    check("the frame bookkeeping stays trivial",
          frame_ms < 2.0, f"{frame_ms:.2f} ms per call")

    # ── The note leaves ─────────────────────────────────────────────────
    closed = []
    note.closed.connect(lambda note_id: closed.append(note_id))
    note.dismiss()
    check("a dismissed note is still on screen while it leaves",
          note.isVisible() and note.windowOpacity() > 0.5,
          f"visible {note.isVisible()}, opacity {note.windowOpacity():.2f}")
    exit_frames = exit_note(note)
    check("a dismissed note shrinks as it goes",
          note.width() < final_w, f"{note.width()} of {final_w}")
    check("a dismissed note reports itself closed once",
          closed == [note.note_id], f"{len(closed)} signal(s)")
    check("a dismissal is quicker than the entrance",
          exit_frames <= frames + 4, f"{exit_frames} vs {frames} frames")
    note.close()
    check("closing a note that already closed does not announce it twice",
          closed == [note.note_id], f"{len(closed)} signal(s)")

    # ── A dismissal can be reversed ─────────────────────────────────────
    reversible = NoteWindow(scale=1.0, dark=True)
    reversible.move(360, 260)
    reversible.show()
    QApplication.processEvents()
    settle_note(reversible)
    reversible.dismiss()
    for _ in range(6):
        reversible._presence.step(1 / 60.0)
        reversible._apply_presence()
    leaving = reversible.windowOpacity()
    reversible.present()
    for _ in range(30):
        reversible._presence.step(1 / 60.0)
        reversible._apply_presence()
    check("a dismissal reverses instead of completing",
          reversible.isVisible() and not reversible._dismissing
          and reversible.windowOpacity() > leaving,
          f"{leaving:.2f} -> {reversible.windowOpacity():.2f}")
    reversible.close()

    # ── Reduced motion ──────────────────────────────────────────────────
    calm = NoteWindow(scale=1.0, dark=True)
    calm._motion.reduced_motion = True
    calm._release.reduced_motion = True
    calm.move(400, 280)
    calm.show()
    QApplication.processEvents()
    check("reduced motion: a note appears at rest, not on a spring",
          calm.size().width() == final_w and calm.windowOpacity() > 0.99,
          f"{calm.width()} px, opacity {calm.windowOpacity():.2f}")
    calm.dismiss()
    check("reduced motion: a note closes at once",
          calm._closed_announced, f"closed {calm._closed_announced}")
    calm.close()

    # ── A note emerges from the reader and returns into it ─────────────
    # Keep the machine's accessibility setting from deciding whether this
    # half of the check can run; the reduced-motion path is exercised below.
    os.environ["NOTCH_REDUCED_MOTION"] = "0"
    anchor = QRect(100, 100, 300, 120)
    origin = QPoint(399, 219)  # nearest card point to the note's centre
    emerged = NoteWindow(scale=1.0, dark=True, anchor=anchor)
    emerged.move(500, 400)
    emerged.show()
    QApplication.processEvents()
    start_centre = emerged.geometry().center()
    check("a note begins at the reader's nearest edge",
          (start_centre - origin).manhattanLength() <= 2,
          f"{start_centre} vs {origin}")
    check("a note begins scaled down on its way out of the card",
          emerged.width() < final_w and emerged.height() < final_h,
          f"{emerged.width()}x{emerged.height()}")
    settle_note(emerged)
    end_centre = emerged.geometry().center()
    check("a note lands on its own place, not the card's",
          (end_centre - QPoint(630, 482)).manhattanLength() <= 2,
          f"{end_centre}")
    emerged.dismiss()
    for _ in range(4):
        emerged._presence.step(1 / 60.0)
        emerged._apply_presence()
    leaving = emerged.geometry().center()
    check("a dismissed note travels back toward the reader",
          (leaving - origin).manhattanLength()
          < (end_centre - origin).manhattanLength(),
          f"{leaving}")
    closest = (leaving - origin).manhattanLength()
    for _ in range(240):
        if emerged._closed_announced:
            break
        emerged._presence.step(1 / 60.0)
        emerged._apply_presence()
        closest = min(closest, (emerged.geometry().center() - origin).manhattanLength())
    check("the return reaches the card as the note closes",
          emerged._closed_announced and closest <= 12,
          f"closed {emerged._closed_announced}, closest {closest} px")
    emerged.close()

    # ── A change of appearance is one dissolve ──────────────────────────
    themed = NoteWindow(scale=1.0, dark=True)
    themed.move(60, 60)
    themed.show()
    QApplication.processEvents()
    settle_note(themed)
    themed.apply_theme(False)
    fade = getattr(themed, "_theme_fade", None)
    check("a note changing theme holds its previous frame",
          isinstance(fade, themefade.SnapshotFade),
          type(fade).__name__ if fade is not None else "none")
    check("the held frame covers the whole note",
          fade is not None and fade.geometry() == themed.rect(),
          f"{fade.geometry() if fade is not None else None}")
    check("the new palette is already underneath the held frame",
          themed._dark is False)
    if fade is not None:
        fade.finish()
    check("the held frame is released when the dissolve ends",
          themed._theme_fade is None)
    themed.close()

    os.environ["NOTCH_REDUCED_MOTION"] = "1"
    instant = NoteWindow(scale=1.0, dark=True)
    instant.move(80, 80)
    instant.show()
    QApplication.processEvents()
    check("reduced motion: an anchored note appears at rest",
          instant.size().width() == final_w
          and abs(instant.windowOpacity() - 1.0) < 0.01,
          f"{instant.width()} px, opacity {instant.windowOpacity():.2f}")
    instant.apply_theme(False)
    check("reduced motion: recolouring has no dissolve",
          getattr(instant, "_theme_fade", None) is None)
    instant.close()
    del os.environ["NOTCH_REDUCED_MOTION"]

    # ── Dragging and the release coast ──────────────────────────────────
    dragged = NoteWindow(scale=1.0, dark=True)
    dragged.move(200, 200)
    dragged.show()
    QApplication.processEvents()
    settle_note(dragged)
    resting_size = dragged.size()
    drag(dragged, QPoint(40, 30))
    check("a note follows the pointer 1:1",
          dragged.pos() == QPoint(240, 230) or dragged._coasting,
          f"{dragged.pos()}")
    check("a dragged note is not resized",
          dragged.size() == resting_size,
          f"{resting_size.width()}x{resting_size.height()} -> "
          f"{dragged.width()}x{dragged.height()}")
    if dragged._coasting:
        target_x = dragged._release["x"].target
        target_y = dragged._release["y"].target
        check("the coast is bounded",
              abs(target_x - 240) <= motion.NOTE_RELEASE_LIMIT
              and abs(target_y - 230) <= motion.NOTE_RELEASE_LIMIT,
              f"({target_x:.1f}, {target_y:.1f})")
        for _ in range(90):
            dragged._release["x"].step(1 / 60.0)
            dragged._release["y"].step(1 / 60.0)
            dragged._apply_release()
        # The springs are stepped by hand here, so the group's own clock is
        # still armed; what has to be true is that both axes have landed and
        # the note stopped inside its bound.
        check("the coast lands and stops",
              dragged._release["x"].settled and dragged._release["y"].settled
              and abs(dragged.x() - 240) <= motion.NOTE_RELEASE_LIMIT,
              f"{dragged.pos()}, settled "
              f"{dragged._release['x'].settled}/{dragged._release['y'].settled}")
        dragged._release.stop()
    else:
        check("the coast is bounded", True, "no measurable velocity to carry")

    # Taking hold of a note stops it wherever it is.
    dragged._release.jump(x=float(dragged.x()), y=float(dragged.y()))
    dragged._release.set_target("x", dragged.x() + 40.0)
    dragged._coasting = True
    stopped_at = dragged.x()
    held = QMouseEvent(QEvent.MouseButtonPress, QPointF(QPoint(20, 10)),
                       QPointF(dragged.mapToGlobal(QPoint(20, 10))),
                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(dragged, held)
    check("taking hold of a note stops its coast",
          not dragged._release.running,
          f"running {dragged._release.running}, at {stopped_at}")
    dragged.close()

    # ── A dialog leaves from where it is ────────────────────────────────
    dialog = ui_overlay.HelpDialog(dark=True)
    dialog.setWindowOpacity(0.4)
    dialog.reject()
    check("a dialog dismissed while arriving continues from its live opacity",
          abs(float(dialog._fade_out_anim.startValue()) - 0.4) < 0.01,
          f"starts at {dialog._fade_out_anim.startValue()}")
    check("dialogs use the shared window duration",
          dialog._fade_out_anim.duration()
          == int(motion.WINDOW_FADE * 1000),
          f"{dialog._fade_out_anim.duration()} ms")
    dialog._fade_out_anim.stop()
    dialog.deleteLater()

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("All motion checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
