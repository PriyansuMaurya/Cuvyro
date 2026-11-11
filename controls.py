"""Toolbar controls and materials for the Notch overlay.

The rules these implement:

* Feedback lands on pointer-**down**, not on release. A control that only
  reacts after the click has already been decided feels dead.
* The hit area is larger than the glyph. 24 px glyphs inside 30 px targets
  keeps the toolbar quiet without making it fiddly.
* Press and hover are separate states with different motion: press is fast
  and slightly springy, hover is a plain brightness change.
* A keyboard focus ring exists, because a control you can tab to but cannot
  see is not reachable.
"""

from PyQt5.QtCore import (
    QEvent, QObject, QRectF, QPointF, Qt, QTimer, QVariantAnimation, QEasingCurve,
)
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen, QTextBlockFormat
from PyQt5.QtWidgets import QAbstractButton, QMenu, QTextEdit, QWidget

from spring import Spring, system_allows_motion
from win_capture import exclude_from_capture


class GlyphButton(QAbstractButton):
    """An outline glyph with press feedback and a hit area bigger than it."""

    HIT = 30          # clickable box
    GLYPH = 24        # design box the glyph is drawn in

    # Palette: (idle, hover, disabled/muted). Kept as one table per theme so
    # the whole toolbar stays in step.
    PALETTE = {
        True: {"idle": "#8E8E93", "hover": "#F2F2F7", "muted": "#5A5A5E",
               "focus": "#0A84FF"},
        False: {"idle": "#7A7A80", "hover": "#1C1C1E", "muted": "#C0C0C6",
                "focus": "#007AFF"},
    }

    # Plate under each glyph: a solid fill, a hairline, and one lit line along
    # the top. The script runs under the chrome, so a bare glyph would sit
    # directly on the words - and the plate used to be low-alpha glass, which
    # let those words read through the controls. The fill is opaque now, mixed
    # to the exact value the glass used to blend to over the card, so the
    # toolbar looks unchanged while nothing shows through it. The hairline and
    # the lit line stay translucent: they are light catching the material, not
    # the material itself.
    PLATE = {
        True: {"fill": "#0F0F0F", "hover": "#1C1C1C", "press": "#262626",
               "edge": (255, 255, 255, 0.10), "lit": (255, 255, 255, 0.17)},
        False: {"fill": "#F4F4F4", "hover": "#ECECEC", "press": "#E6E6E6",
                "edge": (0, 0, 0, 0.09), "lit": (255, 255, 255, 0.85)},
    }
    PLATE_INSET = 0.10      # of the hit box, so it tracks the target size
    PLATE_RADIUS = 0.30     # of the plate: 9 px at a 30 px target

    def __init__(self, parent=None, hit: int | None = None):
        super().__init__(parent)
        self.setFixedSize(hit or self.HIT, hit or self.HIT)
        self.setCursor(Qt.PointingHandCursor)
        self._hover = False
        self._pressed = False
        self._dark = True
        self._focus_ring = False
        self._glyph_px = float(self.GLYPH)
        self._reduced = not system_allows_motion()

        # Press scale: fast attack, softer release, with a little bounce so
        # the control feels like a physical key. 0 = rest, 1 = fully pressed.
        self._press = Spring(0.0, damping=0.62, response=0.16,
                             rest_value=0.004, rest_velocity=0.35)
        self._press_clock = QTimer(self)
        self._press_clock.setInterval(16)
        self._press_clock.timeout.connect(self._step_press)
        self.setFocusPolicy(Qt.TabFocus)

    # ── state plumbing ───────────────────────────────────────────────
    def set_dark(self, dark: bool) -> None:
        if self._dark != dark:
            self._dark = dark
            self.update()

    def set_glyph_size(self, pixels: float) -> None:
        """Draw the glyph at a given size, independent of the hit box."""
        if self._glyph_px != pixels:
            self._glyph_px = float(pixels)
            self.update()

    def _set_press(self, pressed: bool) -> None:
        self._pressed = pressed
        target = 1.0 if pressed else 0.0
        if self._reduced:
            self._press.jump_to(target)
            self.update()
            return
        self._press.set_target(target)
        self._press_clock.start()

    def _step_press(self) -> None:
        self._press.step(1 / 60.0)
        if self._press.settled:
            self._press.land()
            self._press_clock.stop()
        self.update()

    def _color(self, muted: bool = False) -> QColor:
        table = self.PALETTE[self._dark]
        if muted:
            return QColor(table["muted"])
        if self._pressed or self._hover:
            return QColor(table["hover"])
        return QColor(table["idle"])

    # ── input ────────────────────────────────────────────────────────
    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self._set_press(False)
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # A pointer press is not keyboard navigation: drop the ring so a
            # clicked toolbar does not look like a focused form field.
            self._focus_ring = False
            self._set_press(True)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._set_press(False)
        super().mouseReleaseEvent(event)

    def focusInEvent(self, event):
        # Focus arrives on window activation too, and a ring that appears
        # just because the card was clicked reads as a selection. Only show
        # it when the user actually reached the control from the keyboard.
        self._focus_ring = event.reason() in (
            Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._focus_ring = False
        self.update()
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        # Space/Return already click; show the same press state while held.
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self._set_press(True)
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        self._set_press(False)
        super().keyReleaseEvent(event)

    # ── painting ─────────────────────────────────────────────────────
    def plate_color(self) -> QColor:
        """The plate's material for the state the control is in.

        Opaque by construction. The script passes under the chrome, so a word
        that happens to sit behind a control must not read through it.
        """
        table = self.PLATE[self._dark]
        if self._press.value > 0.05:
            return QColor(table["press"])
        if self._hover:
            return QColor(table["hover"])
        return QColor(table["fill"])

    def _paint_plate(self, painter: QPainter) -> None:
        inset = self.width() * self.PLATE_INSET
        plate = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        radius = plate.width() * self.PLATE_RADIUS
        table = self.PLATE[self._dark]

        painter.setPen(Qt.NoPen)
        painter.setBrush(self.plate_color())
        painter.drawRoundedRect(plate, radius, radius)

        edge = QColor(*table["edge"][:3])
        edge.setAlphaF(table["edge"][3])
        painter.setPen(QPen(edge, 1.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(plate.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

        # One bright line across the top: that highlight is what reads as a
        # lit surface. Without it the plate is just a slightly tinted rectangle.
        lit = QColor(*table["lit"][:3])
        lit.setAlphaF(table["lit"][3])
        painter.setPen(QPen(lit, 1.0))
        painter.drawLine(QPointF(plate.left() + radius, plate.top() + 0.5),
                         QPointF(plate.right() - radius, plate.top() + 0.5))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        self._paint_plate(painter)

        cx, cy = self.width() / 2.0, self.height() / 2.0
        # Glyphs are authored in a 24 px box; the design scale decouples the
        # drawn size from the hit box, so padding costs no legibility.
        design = self._glyph_px / 24.0
        scale = design * (1.0 - 0.10 * self._press.value)

        painter.translate(cx, cy)
        painter.scale(scale, scale)
        painter.translate(-12.0, -12.0)   # 24×24 design box

        self.paint_glyph(painter)

        painter.resetTransform()
        if self.hasFocus() and self._focus_ring:
            pen = QPen(QColor(self.PALETTE[self._dark]["focus"]))
            pen.setWidthF(1.5)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            inset = self.width() * self.PLATE_INSET
            plate = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
            radius = plate.width() * self.PLATE_RADIUS
            painter.drawRoundedRect(plate.adjusted(-0.5, -0.5, 0.5, 0.5),
                                    radius + 0.5, radius + 0.5)

    def paint_glyph(self, painter: QPainter) -> None:
        """Draw inside a 24×24 box whose origin is (0,0). Override."""
        raise NotImplementedError


class MinimizeButton(GlyphButton):
    """The minus. It minimizes into the pill, it does not quit.

    A minus at the top-left of a panel means "put it away"; wiring it to
    quit made the most familiar control in the window the most destructive
    one. Quitting lives in the menu, where it is deliberate.
    """

    def paint_glyph(self, painter):
        color = self._color()
        pen = QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(7.0, 12.0), QPointF(17.0, 12.0))


class NoteButton(GlyphButton):
    def paint_glyph(self, painter):
        painter.setPen(QPen(self._color(), 1.4, Qt.SolidLine, Qt.RoundCap))
        painter.setBrush(Qt.NoBrush)
        rect = QRectF(7.5, 6.0, 9.0, 12.0)
        painter.drawRoundedRect(rect, 1.5, 1.5)
        painter.drawLine(QPointF(rect.left() + 2, rect.top() + 4),
                         QPointF(rect.right() - 2, rect.top() + 4))
        painter.drawLine(QPointF(rect.left() + 2, rect.top() + 7),
                         QPointF(rect.right() - 3.5, rect.top() + 7))


class MicButton(GlyphButton):
    def __init__(self, parent=None, hit: int | None = None):
        super().__init__(parent, hit)
        self._muted = False

    def set_muted(self, muted: bool):
        if self._muted != muted:
            self._muted = muted
            self.update()

    def paint_glyph(self, painter):
        color = self._color(muted=self._muted)
        painter.setPen(QPen(color, 1.5, Qt.SolidLine, Qt.RoundCap))
        painter.setBrush(Qt.NoBrush)
        # Capsule, stem, base arc — drawn to the 24 box, not the widget.
        painter.drawRoundedRect(QRectF(9.0, 5.0, 6.0, 10.0), 3.0, 3.0)
        painter.drawLine(QPointF(12.0, 15.0), QPointF(12.0, 19.0))
        painter.drawArc(QRectF(7.0, 11.0, 10.0, 8.0), 180 * 16, 180 * 16)
        if self._muted:
            painter.drawLine(QPointF(6.0, 6.0), QPointF(18.0, 18.0))


class CompactButton(GlyphButton):
    """Narrow / wide reader, drawn as a width, not as a media control.

    The previous glyph was two vertical bars in the wide state — which is
    exactly a pause icon. In an app that has timed playback, the most
    dangerous thing a toolbar can do is make "shrink the window" look like
    "stop the script". One double-headed arrow, nothing else.
    """

    def __init__(self, parent=None, hit: int | None = None):
        super().__init__(parent, hit)
        self._compact = False

    def set_compact(self, compact: bool):
        if self._compact != compact:
            self._compact = compact
            self.update()

    def paint_glyph(self, painter):
        pen = QPen(self._color(), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(QPointF(5.0, 12.0), QPointF(19.0, 12.0))
        for tip, back in ((5.0, 8.4), (19.0, 15.6)):
            painter.drawLine(QPointF(tip, 12.0), QPointF(back, 9.4))
            painter.drawLine(QPointF(tip, 12.0), QPointF(back, 14.6))


class TextButton(QAbstractButton):
    """A small labelled button (the editor's Done)."""

    # White text on Apple blue (#007AFF) is 4.4:1, and on the dark-mode
    # #0A84FF it is 3.6:1: both below the 4.5:1 a 9 pt label needs. The same
    # blue a step deeper keeps the accent and clears the bar (5.6:1).
    PRIMARY = "#0066CC"
    PRIMARY_HOVER = "#0059B3"

    def __init__(self, text: str, parent=None, primary: bool = False):
        super().__init__(parent)
        self.setText(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(self.sizePolicy().horizontalPolicy(), self.sizePolicy().verticalPolicy())
        self._hover = False
        self._pressed = False
        self._dark = True
        self._focus_ring = False
        self._primary = primary

    def set_dark(self, dark: bool) -> None:
        if self._dark != dark:
            self._dark = dark
            self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self._pressed = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self._focus_ring = False
            self.update()
        super().mousePressEvent(event)

    def focusInEvent(self, event):
        self._focus_ring = event.reason() in (
            Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._focus_ring = False
        self.update()
        super().focusOutEvent(event)

    def mouseReleaseEvent(self, event):
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self._dark:
            base = QColor("#2C2C2E") if not self._primary else QColor(self.PRIMARY)
            hover = QColor("#3A3A3C") if not self._primary else QColor(self.PRIMARY_HOVER)
            text = QColor("#F2F2F7")
        else:
            base = QColor("#E8E8EA") if not self._primary else QColor(self.PRIMARY)
            hover = QColor("#DCDCDE") if not self._primary else QColor(self.PRIMARY_HOVER)
            text = QColor("#1C1C1E") if not self._primary else QColor("#FFFFFF")
        fill = hover if (self._hover or self._pressed) else base
        if self._pressed:
            fill = fill.darker(112)
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        radius = self.height() / 2.0
        painter.drawRoundedRect(QRectF(self.rect()), radius, radius)
        painter.setPen(QPen(text))
        font = painter.font()
        font.setPointSizeF(9.0)
        font.setWeight(500)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignCenter, self.text())
        if self.hasFocus() and self._focus_ring:
            pen = QPen(QColor("#0A84FF" if self._dark else "#007AFF"))
            pen.setWidthF(1.5)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5),
                                    radius, radius)


class ScriptTextEdit(QTextEdit):
    """A script viewport with a stable reading anchor.

    The viewport is the whole height of the card, so the script is one plain,
    fully opaque column of type from the top edge to the bottom. It paints
    through QTextEdit itself: no offscreen image, no fade and no blur.

    Nothing is reserved above the first line, so a script opens at the top
    edge and runs behind the floating chrome; only the foot keeps its room,
    which lets the last line be scrolled up onto the reading anchor. The
    anchor itself is snapped to the document's line grid so the top edge of
    the card always falls on a line boundary (see reading_inset).
    """

    # Where the reading line sits inside the reading area, and how much room
    # must be left under it so a line is never crowded by the bottom edge.
    READING_TOP = 0.32
    READING_TOP_MIN = 12.0
    READING_BOTTOM_MIN = 12.0

    def __init__(self, parent=None):
        super().__init__(parent)
        # The viewport covers the card, but the reading rhythm is still
        # measured against the reading area: the lead is the strip the top
        # controls float over, the foot is the padding below the last line.
        self._lead_top = 0.0
        self._lead_bottom = 0.0

    def set_reading_leads(self, top: float, bottom: float) -> None:
        """Reserve the strips the floating chrome and the bottom padding use.

        The viewport spans the card's full height so the script can run under
        the controls, but the line being read must not move when it does: the
        lead is where the top of the reading area used to be.
        """
        top, bottom = float(max(0.0, top)), float(max(0.0, bottom))
        if (abs(top - self._lead_top) > 0.01
                or abs(bottom - self._lead_bottom) > 0.01):
            self._lead_top, self._lead_bottom = top, bottom
            self.viewport().update()

    def line_grid(self) -> float:
        """The vertical step between lines, as the document lays them out.

        Every block is set to a fixed height, so the step is uniform across
        the script, blank lines included. Reading it back from the document
        keeps the anchor locked to the real layout instead of to a metrics
        estimate that can differ from it by a pixel.
        """
        block = self.document().begin().blockFormat()
        if (block.lineHeightType() == QTextBlockFormat.FixedHeight
                and block.lineHeight() > 0):
            return float(block.lineHeight())
        return float(max(1, self.fontMetrics().lineSpacing()))

    def reading_offset(self) -> float:
        """Distance from the chrome strip down to the reading line, in DIP.

        This is the anchor's position inside the reading area, independent of
        the document's own margins.
        """
        height = float(self.viewport().height())
        pitch = float(max(1, self.fontMetrics().lineSpacing()))
        margin = float(self.document().documentMargin())
        usable = max(0.0, height - self._lead_top - self._lead_bottom)
        top = max(usable * self.READING_TOP, self.READING_TOP_MIN)
        top = min(top, usable - pitch - margin - self.READING_BOTTOM_MIN)
        return max(0.0, top)

    def reading_inset(self) -> float:
        """Where the line being read begins, measured from the window top.

        The line is anchored below the strip the controls float over, so the
        chrome sits above the words rather than on them.

        The anchor is snapped to the document's line grid. Every line then
        lands on a whole multiple of the grid from the top of the card, so the
        topmost line is complete and the line above it has left the view
        entirely. Without the snap, an anchor that is not on the grid leaves
        the topmost line cut through its ascenders at rest, which reads as a
        broken row of type against the card's edge.
        """
        margin = float(self.document().documentMargin())
        desired = max(0.0, self._lead_top + margin + self.reading_offset())
        pitch = self.line_grid()
        if pitch <= 0:
            return desired
        return max(pitch, float(round(desired / pitch)) * pitch)

    def anchor_padding(self) -> float:
        """Bottom padding that lets the last line reach the same anchor.

        Without it the scrollbar runs out first and the final lines are the
        only ones that do not sit where every other line sits.
        """
        height = float(self.viewport().height())
        pitch = float(max(1, self.fontMetrics().lineSpacing()))
        margin = float(self.document().documentMargin())
        return max(0.0, height - self.reading_inset() - pitch - margin)




class RoundedMenu(QMenu):
    """A menu that follows the reader's theme instead of always being dark."""

    def __init__(self, parent=None, dark: bool = True):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint
                            | Qt.NoDropShadowWindowHint)
        self._radius = 12
        self._dark = dark

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, lambda: exclude_from_capture(self))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self._radius, self._radius)

        fill = QColor("#1E1E1E") if self._dark else QColor("#F7F7F8")
        stroke = QColor("#3A3A3C") if self._dark else QColor("#DCDCDE")
        painter.setPen(QPen(stroke, 1))
        painter.setBrush(fill)
        painter.drawPath(path)

        # Bright top edge: light catching the material, the same cue the
        # card uses so the two surfaces read as the same family.
        edge = QColor(255, 255, 255, 26 if self._dark else 200)
        painter.setPen(QPen(edge, 1))
        painter.drawLine(QPointF(self._radius, 1.0),
                         QPointF(self.width() - self._radius, 1.0))

        painter.setClipPath(path)
        super().paintEvent(event)


class MenuRowNav(QObject):
    """Keyboard for the menu rows that are built out of widgets.

    A QWidgetAction is where a menu's own keyboard navigation stops. The popup
    can highlight the row, but Return has no action to trigger, so Qt closes
    the whole panel having done nothing at all - a silent dismissal instead of
    a command. Each row registers the buttons it can press, or its slider, and
    gets them back on the keyboard:

      * Left and Right pick inside a row, or nudge a row's slider,
      * Return and Space press the pick,
      * Up and Down still move between rows.

    The pick is styled exactly like hover, so the row has one language for
    "this control is about to be pressed" whichever input picked it.
    """

    def __init__(self, menu: QMenu, parent=None):
        super().__init__(parent if parent is not None else menu)
        self._menu = menu
        self._rows = {}
        self._action = None
        self._picked = -1
        self._keyboard = False
        menu.installEventFilter(self)

    # ── wiring ────────────────────────────────────────────────────────
    def row(self, action, buttons=(), slider=None):
        """Register one widget row: what it can press, or what it can nudge."""
        self._rows[action] = (list(buttons), slider)
        return action

    # ── state ─────────────────────────────────────────────────────────
    def picked(self) -> int:
        """Index of the picked button in the active row, or -1."""
        return self._picked

    def active_row(self):
        return self._action

    def pick(self, index: int):
        if index == self._picked:
            return
        self._picked = index
        for position, button in enumerate(self._buttons()):
            self._style(button, position == index)

    # ── internals ─────────────────────────────────────────────────────
    def _buttons(self):
        return self._rows.get(self._action, ((), None))[0]

    @staticmethod
    def _style(button, picked: bool):
        button.setProperty("picked", picked)
        # A dynamic property only reaches the stylesheet on a re-polish.
        button.style().unpolish(button)
        button.style().polish(button)

    def _clear(self):
        for button in self._buttons():
            if button.property("picked"):
                self._style(button, False)
        self._picked = -1

    def _enter(self, action):
        """Make a row current, picking a sensible control for the keyboard."""
        if action is self._action:
            return
        self._clear()
        self._action = action
        buttons = self._buttons()
        if self._keyboard and buttons:
            # Start on the state the row is already showing, so Enter re-applies
            # it instead of switching blind, and one Right reaches the other.
            checked = [i for i, b in enumerate(buttons) if b.isChecked()]
            self.pick(checked[0] if checked else 0)

    def _sync(self):
        # The single-shot can outlive the popup: a menu closed on the same key
        # that scheduled it deletes its widgets before this runs.
        try:
            action = self._menu.activeAction()
        except RuntimeError:
            return
        self._enter(action if action in self._rows else None)

    def eventFilter(self, obj, event):
        if obj is not self._menu:
            return False
        kind = event.type()
        if kind in (QEvent.MouseMove, QEvent.MouseButtonPress):
            # The pointer takes over: a leftover pick would light a control
            # away from the cursor.
            self._keyboard = False
            self._clear()
            return False
        if kind != QEvent.KeyPress:
            return False
        self._keyboard = True
        key = event.key()
        if key in (Qt.Key_Home, Qt.Key_End):
            # QMenu's own handling of these can leave it with no active
            # action, where Return then does nothing at all.
            stops = [a for a in self._menu.actions()
                     if a.isVisible() and not a.isSeparator()]
            if stops:
                target = stops[0] if key == Qt.Key_Home else stops[-1]
                self._menu.setActiveAction(target)
                self._enter(target if target in self._rows else None)
            return True
        # Qt does not emit hovered() for keyboard selection, so the current
        # row is read from the menu itself on every key.
        action = self._menu.activeAction()
        if action in self._rows:
            self._enter(action)
            buttons, slider = self._rows[action]
            if key in (Qt.Key_Left, Qt.Key_Right):
                step = -1 if key == Qt.Key_Left else 1
                if buttons:
                    self.pick((max(self._picked, 0) + step) % len(buttons))
                elif slider is not None:
                    # Two single steps per press: the same 10 words a minute
                    # the reader's own arrow keys move by.
                    slider.setValue(slider.value()
                                    + step * max(1, slider.singleStep() * 2))
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
                if 0 <= self._picked < len(buttons):
                    buttons[self._picked].click()
                # Swallow the key either way: the menu's default would close
                # the panel without running the row.
                return True
        # Up and Down belong to the menu. It moves after this filter runs, so
        # the row it lands on is picked once the move has happened.
        QTimer.singleShot(0, self._sync)
        return False
