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

from PyQt5.QtCore import QRectF, QPointF, Qt, QTimer, QVariantAnimation, QEasingCurve
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
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

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
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5),
                                    6, 6)

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


class MoreButton(GlyphButton):
    """Three dots, not a gear.

    The control opens a menu, and the menu is where settings live. A filled
    cog was the darkest, heaviest object in a card whose whole job is to
    stay out of the way; it also promised a settings panel and delivered a
    menu. Three dots at the same stroke weight as the other glyphs say
    "more" without competing with the script.
    """

    def paint_glyph(self, painter):
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._color())
        for dx in (-5.0, 0.0, 5.0):
            painter.drawEllipse(QPointF(12.0 + dx, 12.0), 1.4, 1.4)


class TextButton(QAbstractButton):
    """A small labelled button (the editor's Done)."""

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
            base = QColor("#2C2C2E") if not self._primary else QColor("#0A84FF")
            hover = QColor("#3A3A3C") if not self._primary else QColor("#3A9BFF")
            text = QColor("#F2F2F7")
        else:
            base = QColor("#E8E8EA") if not self._primary else QColor("#007AFF")
            hover = QColor("#DCDCDE") if not self._primary else QColor("#2F8DFF")
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
