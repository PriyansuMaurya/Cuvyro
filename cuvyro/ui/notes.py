"""Sticky note window for the Cuvyro teleprompter.

A separate 260 x 165 DIP card with a drag strip, a multiline field and a
compact close control, matching the reference geometry. It follows the
reader's theme: charcoal on dark, near-white on light. The window owns no
persistence: the overlay saves the text and position through ``store``.
"""

import ctypes
import sys
import time
import uuid

from PyQt5 import sip
from PyQt5.QtCore import QPoint, QPointF, QRect, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QKeySequence, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QHBoxLayout, QShortcut, QTextEdit, QVBoxLayout, QWidget

from cuvyro.ui import motion, theme, themefade
from cuvyro.ui.spring import SpringGroup

NOTE_W, NOTE_H = 260, 165
# A note is a piece of the reader's card lifted off it, so it takes the
# card's corner, fill and hairline rather than a second set of each.
CORNER_RADIUS = theme.RADIUS["card"]
HEADER_H = 24


class _CloseGlyph(QWidget):
    """Muted × in the note header."""

    clicked = pyqtSignal()

    def __init__(self, parent=None, dark: bool = True):
        super().__init__(parent)
        self.setFixedSize(18, 18)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Close note")
        # A glyph with no text still needs a name for a screen reader, and a
        # keyboard path if it is drawn rather than derived from QPushButton.
        self.setAccessibleName("Close note")
        self.setFocusPolicy(Qt.TabFocus)
        self._hover = False
        self._dark = dark

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        # A release can arrive after the note this glyph belongs to has been
        # destroyed (closed by its own click, or as the app put its notes
        # away). Touching a deleted widget raises, and an unhandled exception
        # in an event handler ends the whole process.
        if sip.isdeleted(self):
            return
        super().mouseReleaseEvent(event)
        if sip.isdeleted(self) or event.button() != Qt.LeftButton:
            return
        self.clicked.emit()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # Same two states as every other glyph in the app: muted at rest,
        # full ink under the pointer.
        color = QColor(theme.INK.pick(self._dark) if self._hover
                       else theme.INK_MUTED.pick(self._dark))
        painter.setPen(QPen(color, 1.5, Qt.SolidLine, Qt.RoundCap))
        centre = self.width() / 2
        arm = 4
        painter.drawLine(QPoint(int(centre - arm), int(centre - arm)),
                         QPoint(int(centre + arm), int(centre + arm)))
        painter.drawLine(QPoint(int(centre + arm), int(centre - arm)),
                         QPoint(int(centre - arm), int(centre + arm)))

        if self.hasFocus():
            painter.setPen(QPen(QColor(theme.FOCUS.pick(self._dark)), 1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 4, 4)


class NoteWindow(QWidget):
    """A single sticky note."""

    changed = pyqtSignal()
    closed = pyqtSignal(str)  # note id

    def __init__(self, text: str = "", note_id: str | None = None, scale: float = 1.0,
                 dark: bool = True, parent=None, anchor: QRect | None = None):
        super().__init__(parent)
        self.note_id = note_id or uuid.uuid4().hex[:12]
        self._scale = scale
        self._dark = dark
        self._drag_offset = None
        self._suppress_change_signal = False
        # The reader's card in global coordinates, when the note was opened
        # from it. The entrance starts at the nearest point of that rectangle
        # and the dismissal returns there, so the note is visibly part of the
        # card rather than a window that happened to appear nearby.
        # A rectangle, or a callable that returns the reader's current card:
        # a live answer keeps the anchor true when the reader moves.
        self._anchor_source = anchor
        self._origin_center: QPointF | None = None
        self._final_center: QPointF | None = None
        self._theme_fade = None

        # ── Motion ──────────────────────────────────────────────────
        # One spring drives opacity and size together, so a note can never
        # fade before it has finished arriving or land before it is visible.
        # The group reads the system's reduced-motion setting itself; when
        # that is on, every retarget jumps and the note simply appears.
        #
        # Deliberately parentless. A QObject child of a widget that the
        # overlay destroys with deleteLater() outlives the Python wrapper in
        # C++, and that mismatch produced an access violation at interpreter
        # shutdown in about one run in four. The note owns the reference
        # instead, and stops both clocks when it is destroyed.
        self._motion = SpringGroup(None, interval_ms=16)
        self._presence = self._motion.add(
            "presence", 1.0,
            damping=motion.NOTE_APPEAR.damping,
            response=motion.NOTE_APPEAR.response,
            rest_value=0.004, rest_velocity=0.08)
        self._motion.tick.connect(self._apply_presence)
        self._motion.settled.connect(self._on_presence_settled)
        self._final_geometry = None
        self._entrance_landed = False
        self._dismissing = False

        # A released note coasts on a small spring of its own, so letting go
        # reads as the note settling rather than stopping dead.
        self._release = SpringGroup(None, interval_ms=16)
        self._release.add("x", 0.0, damping=motion.NOTE_RELEASE.damping,
                          response=motion.NOTE_RELEASE.response)
        self._release.add("y", 0.0, damping=motion.NOTE_RELEASE.damping,
                          response=motion.NOTE_RELEASE.response)
        self._release.tick.connect(self._apply_release)
        self._release.settled.connect(self._on_release_settled)
        # Two recent pointer positions are all the release velocity needs.
        self._samples = []
        # The clocks are not QObject children, so stop them explicitly if the
        # window is destroyed without ever being closed.
        self.destroyed.connect(self._stop_motion)
        self._coasting = False
        self._closed_announced = False

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("Cuvyro Note")
        self.setAccessibleName("Cuvyro note")
        self.setMinimumSize(self._dim(NOTE_W), self._dim(NOTE_H))
        self.resize(self._dim(NOTE_W), self._dim(NOTE_H))

        self._build()
        # A note could only be closed with a mouse before: its × is a custom
        # glyph and the note had no keyboard handler. Escape gives every note
        # a way out from the keyboard.
        self._escape = QShortcut(QKeySequence("Escape"), self)
        self._escape.setContext(Qt.WindowShortcut)
        self._escape.activated.connect(self.dismiss)

        if text:
            self._suppress_change_signal = True
            self._edit.setPlainText(text)
            self._suppress_change_signal = False

        self._apply_capture_exclusion()

    # ── helpers ──────────────────────────────────────────────────────────
    def _dim(self, value: float) -> int:
        return max(1, int(value * self._scale))

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(self._dim(11), self._dim(8), self._dim(11), self._dim(10))
        root.setSpacing(self._dim(4))

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addStretch()
        self._close = _CloseGlyph(self, dark=self._dark)
        self._close.clicked.connect(self.dismiss)
        header.addWidget(self._close)
        root.addLayout(header)

        self._edit = QTextEdit()
        self._edit.setAcceptRichText(False)
        self._edit.setFrameShape(QTextEdit.NoFrame)
        self._edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._edit.setLineWrapMode(QTextEdit.WidgetWidth)
        self._edit.setPlaceholderText("Talking points")
        self._edit.setAccessibleName("Note text")
        self._style_editor()
        self._edit.textChanged.connect(self._on_text_changed)
        root.addWidget(self._edit)

    def _style_editor(self) -> None:
        """Theme-dependent field styling; re-run when the reader's theme flips."""
        text_color = theme.INK.pick(self._dark)
        handle_color = "rgba(255,255,255,0.18)" if self._dark else "rgba(0,0,0,0.18)"
        self._edit.setStyleSheet(
            f"""
            QTextEdit {{
                background: transparent;
                border: none;
                color: {text_color};
                font-family: "Segoe UI", system-ui, sans-serif;
                font-size: {max(9, int(theme.TYPE["note"] * self._scale))}px;
            }}
            QScrollBar:vertical {{
                background: transparent; width: 6px; margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {handle_color};
                border-radius: 3px; min-height: 20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; background: transparent;
            }}
            """
        )

        # Placeholder colour is not settable via stylesheet on QTextEdit.
        palette = self._edit.palette()
        palette.setColor(palette.PlaceholderText,
                         QColor(theme.PLACEHOLDER.pick(self._dark)))
        self._edit.setPalette(palette)

    def set_anchor(self, anchor) -> None:
        """Point later arrivals and dismissals at the reader's current card.

        The reader moves (head tracking, a drag, a screen change), so the
        rectangle is read when the motion starts rather than captured once
        when the note was created. ``anchor`` may be a rectangle or a
        callable that answers with one.
        """
        self._anchor_source = anchor

    def apply_theme(self, dark: bool) -> None:
        """Follow the reader when its theme changes while a note is open.

        The frame already on screen is held over the recoloured note, so the
        fill, hairline and field change together instead of each repaint
        landing as its own flash. Reduced motion still switches at once; the
        snapshot returns ``None`` and there is nothing to hold.
        """
        if dark == self._dark:
            return
        snapshot = themefade.capture(self)
        self._dark = dark
        self._close._dark = dark
        self._close.update()
        self._style_editor()
        self.update()
        themefade.crossfade(self, snapshot)

    def _on_text_changed(self) -> None:
        if not self._suppress_change_signal:
            self.changed.emit()

    # ── motion ───────────────────────────────────────────────────────────
    def showEvent(self, event):
        """Arrive from nothing instead of appearing at full size.

        ``showEvent`` runs before the first paint, so the note never flashes
        at its resting size for one frame. The geometry it was given is kept:
        every frame of the entrance is that rect scaled about its own centre
        and, when it has an anchor, drawn out of the reader's nearest edge.
        Without an anchor it grows about its corner, exactly as before.
        """
        super().showEvent(event)
        if self._final_geometry is None:
            self._final_geometry = QRect(self.geometry())
            self._final_center = QPointF(self._final_geometry.center())
        if self._final_center is None:
            self._final_center = QPointF(self._final_geometry.center())
        if self._origin_center is None and not self._dismissing:
            self._origin_center = self._anchor_origin()
        # The keyboard belongs in the field, not on the close glyph. Qt hands
        # focus to the first tab-focusable child, which is the corner: the
        # note then opened with a focus ring on its × and typing went nowhere
        # until it was clicked.
        self._edit.setFocus()
        if self._motion.reduced_motion:
            self._presence.jump_to(1.0)
            self._apply_presence()
            return
        if self._dismissing:
            self._dismissing = False
            self._motion.set_target("presence", 1.0)
        elif self._presence.value >= 0.999:
            self._presence.jump_to(0.0)
            self._entrance_landed = False
            self._presence.set_target(1.0)
            self._apply_presence()
            self._motion.start()
        else:
            self._motion.set_target("presence", 1.0)

    def _anchor_origin(self) -> QPointF | None:
        """The point on the reader's card the note grows out of.

        The nearest point of the card to where the note is going, not its
        centre: a note opening below the card should emerge from its lower
        edge, and one opening beside it from the nearest corner. That is what
        keeps the note and the card in the same spatial relationship through
        the whole transition.
        """
        anchor = self._anchor_source
        if callable(anchor):
            try:
                anchor = anchor()
            except (RuntimeError, TypeError):
                return None  # the surface that owned the anchor is gone
        if not isinstance(anchor, QRect) or not anchor.isValid():
            return None
        centre = self._final_center or QPointF(self.geometry().center())
        x = min(max(centre.x(), anchor.left()), anchor.right())
        y = min(max(centre.y(), anchor.top()), anchor.bottom())
        return QPointF(x, y)

    def _hold_at_current(self) -> None:
        """Make the note's current place its resting place.

        Used when the pointer takes hold of a note mid-flight: the note is
        already there, so it should finish arriving where the hand found it
        instead of jumping back to the position it was first opened at.
        """
        self._final_geometry = QRect(self.geometry())
        self._final_center = QPointF(self._final_geometry.center())

    def _apply_presence(self) -> None:
        """One frame of the entrance or the exit.

        Opacity, size and (when the note belongs to the reader) position share
        the same spring, so all three always finish together. A frameless note
        has no resize handle, so its minimum size only has to hold the
        entrance: lowering it here is what lets the card grow from 94%
        instead of snapping to its resting size on the first frame.
        """
        if sip.isdeleted(self):
            return
        progress = max(0.0, min(1.0, self._presence.value))
        self.setWindowOpacity(progress)
        if self._final_geometry is None:
            return
        if progress <= 0.0005 and self._dismissing:
            # The exit lands on the closing frame itself, so a dismissal does
            # not depend on a second signal arriving after the last tick.
            self._finish_close()
            return
        if progress >= 0.999:
            self.setMinimumSize(self._dim(NOTE_W), self._dim(NOTE_H))
            if not self._entrance_landed and not self._dismissing:
                # The interpolated last frame can be a pixel short of the aim;
                # land exactly once. Doing it only on the landing frame keeps
                # a later drag from being undone by a still-armed clock.
                self._entrance_landed = True
                self.setGeometry(self._final_geometry)
                return
            self.setGeometry(self.x(), self.y(),
                             self._final_geometry.width(),
                             self._final_geometry.height())
            return
        # The exit starts from a slightly smaller scale than the entrance so
        # the note reads as going back into the card rather than as fading.
        base = (motion.NOTE_DISMISS_SCALE if self._dismissing
                else motion.NOTE_APPEAR_SCALE)
        factor = base + (1.0 - base) * progress
        width = max(1, int(round(self._final_geometry.width() * factor)))
        height = max(1, int(round(self._final_geometry.height() * factor)))
        self.setMinimumSize(width, height)
        if self._origin_center is not None and self._final_center is not None:
            # Travel and scaling are one gesture: the note's centre moves from
            # the card to its resting place while it grows, and the reverse on
            # the way out.
            centre_x = (self._origin_center.x()
                        + (self._final_center.x() - self._origin_center.x()) * progress)
            centre_y = (self._origin_center.y()
                        + (self._final_center.y() - self._origin_center.y()) * progress)
            x = int(round(centre_x - width / 2.0))
            y = int(round(centre_y - height / 2.0))
        else:
            # No parent to come from: grow about the top-left corner it was
            # given, which is the same in-place arrival as before.
            x, y = self.x(), self.y()
        self.setGeometry(x, y, width, height)

    def _on_presence_settled(self) -> None:
        if self._dismissing:
            self._finish_close()

    def dismiss(self) -> None:
        """Leave the way it arrived: shrink, fade, then close.

        ``close()`` stays immediate on purpose. The app closes notes during
        shutdown and when a script is replaced, and neither path may be held
        by an animation; only the reader's own two ways out (the corner and
        Escape) come through here.
        """
        if sip.isdeleted(self) or self._dismissing:
            return
        self._dismissing = True
        # Leave from where the note is now, and return to the card as it is
        # now: the reader can have moved since the note was opened.
        if self._final_geometry is not None:
            self._hold_at_current()
            origin = self._anchor_origin()
            if origin is not None:
                self._origin_center = origin
        if not self.isVisible() or self._motion.reduced_motion:
            self._finish_close()
            return
        self._presence.damping = motion.NOTE_DISMISS.damping
        self._presence.response = motion.NOTE_DISMISS.response
        self._motion.set_target("presence", 0.0)

    def present(self) -> None:
        """Reverse a dismissal that is still in flight."""
        if sip.isdeleted(self):
            return
        self._dismissing = False
        self._presence.damping = motion.NOTE_APPEAR.damping
        self._presence.response = motion.NOTE_APPEAR.response
        self.show()
        self._motion.set_target("presence", 1.0)

    def _finish_close(self) -> None:
        self._dismissing = True
        self.close()

    # ── release coast ────────────────────────────────────────────────────
    def _coast_from_release(self) -> None:
        """Let the note settle on the velocity it was released with.

        The pointer already chose where the note belongs, so there is no
        projection and no snap target: the coast only covers the frame between
        the last move and the release, capped at ``NOTE_RELEASE_LIMIT`` pixels
        so a fast flick cannot throw a note across the screen.
        """
        if self._release.reduced_motion:
            return
        if len(self._samples) < 2:
            return
        (before_at, before_x, before_y), (last_at, last_x, last_y) = self._samples[-2:]
        elapsed = last_at - before_at
        # Below 4 ms there is no measurable velocity, only the gap between two
        # events: dividing by it would throw the note at its own limit. The
        # upper bound drops a paused drag, where the pointer stopped before it
        # was released and the note should stop with it.
        if elapsed < 0.004 or elapsed > 0.12:
            return
        origin = self.frameGeometry().topLeft()
        limit = motion.NOTE_RELEASE_LIMIT
        dx = (last_x - before_x) / elapsed * motion.NOTE_RELEASE_SECONDS
        dy = (last_y - before_y) / elapsed * motion.NOTE_RELEASE_SECONDS
        dx = max(-limit, min(limit, dx))
        dy = max(-limit, min(limit, dy))
        if abs(dx) < 0.5 and abs(dy) < 0.5:
            return
        self._coasting = True
        self._release.jump(x=float(origin.x()), y=float(origin.y()))
        self._release.set_target("x", origin.x() + dx)
        self._release.set_target("y", origin.y() + dy)

    def _apply_release(self) -> None:
        if sip.isdeleted(self):
            return
        self.move(int(round(self._release["x"].value)),
                  int(round(self._release["y"].value)))

    def _on_release_settled(self) -> None:
        if not self._coasting:
            return
        self._coasting = False
        # The resting position is what gets saved, not the one the pointer
        # happened to be at on the release event.
        self.changed.emit()

    # ── painting ─────────────────────────────────────────────────────────
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        radius = self._dim(CORNER_RADIUS)

        fill = QPainterPath()
        fill.addRoundedRect(0, 0, self.width(), self.height(), radius, radius)
        painter.setPen(Qt.NoPen)
        # The reader's own fill, not a second surface grey: the note is a piece
        # of that card, and two near-blacks three levels apart read as a
        # mismatch rather than as depth.
        painter.setBrush(QColor(theme.CARD.pick(self._dark)))
        painter.drawPath(fill)

        # The same light catching the top edge as the reader's card, so the
        # lifted note still carries the card's material in dark mode.
        if self._dark:
            painter.save()
            painter.setClipPath(fill)
            painter.setPen(QPen(QColor(255, 255, 255, theme.CARD_EDGE_ALPHA), 1.0))
            painter.drawLine(QPointF(radius, 1.0),
                             QPointF(self.width() - radius, 1.0))
            painter.restore()

        # Hairline border, inset half a pixel so the stroke is fully inside.
        pen = QPen(QColor(theme.CARD_HAIRLINE.pick(self._dark)))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        border = QPainterPath()
        border.addRoundedRect(0.5, 0.5, self.width() - 1.0, self.height() - 1.0,
                              radius - 0.5, radius - 0.5)
        painter.drawPath(border)

    # ── dragging (header strip only, so text selection still works) ──────
    def mousePressEvent(self, event):
        # The pointer always wins over the app's own motion: taking hold of a
        # note stops a release coast, finishes an entrance and reverses a
        # dismissal that is still in flight.
        self._release.stop()
        if self._dismissing:
            self.present()
        if not self._presence.settled:
            self._hold_at_current()
            self._presence.jump_to(1.0)
            self._apply_presence()
        if event.button() == Qt.LeftButton and event.pos().y() <= self._dim(HEADER_H):
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            corner = self.frameGeometry().topLeft()
            self._samples = [(time.monotonic(), corner.x(), corner.y())]
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_offset)
            # 1:1 with the pointer. Every move is sampled, but only the last
            # two are kept: the release needs a velocity, not a path.
            corner = self.frameGeometry().topLeft()
            self._samples.append((time.monotonic(), corner.x(), corner.y()))
            del self._samples[:-3]
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_offset is not None:
            self._drag_offset = None
            event.accept()
            self.changed.emit()  # persist the new position
            self._coast_from_release()
            return
        super().mouseReleaseEvent(event)

    # ── state ────────────────────────────────────────────────────────────
    def text(self) -> str:
        return self._edit.toPlainText()

    def resting_geometry(self) -> QRect:
        """Where the note belongs, even while it is still travelling there.

        Placement and saving ask this rather than the live geometry: during
        the entrance the window is on its way out of the reader, and a note
        saved at that temporary position would restore halfway across the
        screen.
        """
        if self._final_geometry is not None and self._motion.running:
            return QRect(self._final_geometry)
        return QRect(self.geometry())

    def to_state(self) -> dict:
        resting = self.resting_geometry()
        return {
            "id": self.note_id,
            "text": self.text(),
            "x": resting.x(),
            "y": resting.y(),
        }

    def _apply_capture_exclusion(self) -> None:
        """Keep the note out of screen captures, same policy as the overlay."""
        if sys.platform != "win32":
            return
        try:
            user32 = ctypes.windll.user32
            WDA_EXCLUDEFROMCAPTURE = 0x11
            user32.SetWindowDisplayAffinity(int(self.winId()), WDA_EXCLUDEFROMCAPTURE)
        except Exception:
            pass

    def _stop_motion(self, *_):
        self._motion.stop()
        self._release.stop()

    def hideEvent(self, event):
        # A hidden note has no motion left to run. Leaving the clock going is
        # how a spring ends up ticking a window that is halfway gone.
        self._stop_motion()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._stop_motion()
        # A note can be asked to close twice (an exit animation landing on a
        # close the app already started); the overlay must hear about it once.
        if not self._closed_announced:
            self._closed_announced = True
            self.closed.emit(self.note_id)
        super().closeEvent(event)
