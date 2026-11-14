"""Control tooltips: one small material, dressed once for every control.

Qt's own tooltip is a system rectangle. It cannot carry the card's material,
it cannot be animated, and it never appears for a control reached from the
keyboard, so the reader had four different looking hints depending on which
window they hung off. This module owns all of them from one place instead.

What the tooltip is:

* The menu's material, exactly: ``#1E1E1E`` / ``#F7F7F8`` fill, the same
  hairline, the same bright top edge, so the hint reads as part of the
  reader rather than as an operating-system popup. Radius 8 DIP, padding
  10 x 7, on three soft shadow rings instead of a frame.
* 12 px on the app's scale, regular weight, +0.2 px tracking. It wraps only
  past 260 DIP, which is the width at which a hint stops being a hint.

What it does:

* Arrives after 650 ms of rest on a control, or 350 ms after FocusIn from
  Tab, so the pointer has to mean it and the keyboard never waits as long.
* Leaves on pointer-out, press, wheel, any key, focus loss, the close of the
  window it describes, app deactivation, and after 9 s regardless. A hint
  that survives a click is a hint in the way of the next one.
* Is placed to the right of the control, then below, left, above, and is
  always clamped inside the work area. It never covers the control that
  owns it, and it never runs off the screen edge.
* Fades on a critically damped spring, from the value on screen, so a
  dismissal during arrival reverses instead of stepping. Reduced motion
  snaps.

Wording, enforced by ``tests/check_design.py``:

* describe the outcome, not the gesture ("New note", not "Click to add a
  note");
* sentence case, no trailing full stop;
* one clause where possible, two at most, inside 72 characters;
* a keyboard shortcut rides in parentheses at the end, which is the only
  thing parentheses are for.

``install`` is idempotent and every surface calls it, because the Setup
dialog is also built on its own by the robustness checks.
"""

import math

from PyQt5 import sip
from PyQt5.QtCore import (
    QEvent, QObject, QPoint, QRect, QRectF, Qt, QTimer,
)
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPalette, QPen
from PyQt5.QtWidgets import QApplication, QLabel, QMenu, QWidget

from notch.platform.capture import exclude_from_capture
from notch.ui.spring import Spring, system_allows_motion

HOVER_DELAY_MS = 650
FOCUS_DELAY_MS = 350
LEAVE_GRACE_MS = 90
AUTO_HIDE_MS = 9000

PAD_X, PAD_Y = 10.0, 7.0
RADIUS = 8.0
SHADOW = 10.0          # room inside the window for the rings
GAP = 8.0              # between the control and the panel
SCREEN_MARGIN = 8.0
MAX_TEXT_W = 260.0
TYPE_PX = 12.0
TRACKING_PX = 0.2

# (fill, hairline, bright top edge alpha), one per theme.
PANEL = {True: ("#1E1E1E", "#343436", 26),
         False: ("#F7F7F8", "#E4E4E6", 200)}
INK = {True: "#EDEDEF", False: "#151517"}


# ── reading a widget ─────────────────────────────────────────────────


def tip_text(widget) -> str:
    """The description for a widget, or "" when it has none.

    Qt resolves a tooltip up the parent chain, so a label inside a row can
    lend its description to the row. The walk keeps that behaviour, and the
    whitespace is normalised because a tooltip built from a wrapped literal
    can arrive with runs of spaces in it.
    """
    node = widget
    while node is not None:
        text = node.toolTip().strip()
        if text:
            return " ".join(text.split())
        node = node.parentWidget()
    return ""


def _walk(widget, attributes):
    """First attribute found on the widget or one of its containers."""
    node = widget
    while node is not None:
        for name in attributes:
            if hasattr(node, name):
                return getattr(node, name)
        node = node.parentWidget()
    return None


def _theme_for(widget) -> bool:
    """True when the surface this control belongs to is dark.

    Every surface already keeps its theme on itself (``_dark_theme`` on the
    reader, ``_dark`` on the menu, notes, dialogs and painted controls), so
    reading it back keeps one tooltip layer in step with a split-screen case:
    a light menu over a dark card gets a light tooltip.
    """
    found = _walk(widget, ("_dark_theme", "_dark"))
    if found is not None:
        return bool(found)
    return QApplication.palette().color(QPalette.Window).lightness() < 128


def _scale_for(widget) -> float:
    found = _walk(widget, ("_scale",))
    try:
        return float(found) if found else 1.0
    except (TypeError, ValueError):
        return 1.0


def _anchor_rect(widget) -> QRect:
    """The rectangle the panel is placed against.

    For a control inside a menu row, that is the menu's own span at the
    control's height: a hint placed beside the row would hang over the
    field it sits next to, and inside a 236 px panel there is no room to
    put it anywhere else without covering a second control.
    """
    window = widget.window()
    if isinstance(window, QMenu) and window is not widget:
        centre = widget.mapToGlobal(widget.rect().center())
        origin = window.mapToGlobal(QPoint(0, 0))
        height = max(1, widget.height())
        return QRect(origin.x(), centre.y() - height // 2,
                     window.width(), height)
    return QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())


# ── the material ─────────────────────────────────────────────────────


class Tooltip(QWidget):
    """The panel. One instance is reused for every control."""

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint
                         | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        # A hint must never take the pointer: a window that grabs it would
        # cancel the hover that asked for it.
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)

        self._dark = True
        self._scale = 1.0
        self._text = ""
        self._panel = QRectF(0.0, 0.0, 1.0, 1.0)

        self._label = QLabel(self)
        self._label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._label.setWordWrap(True)
        self._label.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        # Arrival is a fade, not a slide: a hint that moves under the pointer
        # is a hint you chase. Critical damping keeps it calm; it is a
        # consequence of the pointer resting, not a gesture with momentum.
        # Rest thresholds are set to where the eye stops seeing the fade, not
        # to a mathematically settled value: an opacity within 2% is gone, and
        # waiting for the last 0.4% made every dismissal feel a beat too slow.
        self._presence = Spring(0.0, damping=1.0, response=0.16,
                                rest_value=0.02, rest_velocity=0.6)
        self._clock = QTimer(self)
        self._clock.setInterval(16)
        self._clock.timeout.connect(self._tick)

    # ── content ──────────────────────────────────────────────────────
    def text(self) -> str:
        return self._text

    def label(self) -> QLabel:
        """The text element (the checks measure its ink)."""
        return self._label

    @staticmethod
    def _font(scale: float) -> QFont:
        font = QFont("Segoe UI")
        font.setPixelSize(max(1, int(round(TYPE_PX * scale))))
        font.setWeight(QFont.Normal)
        # Large type wants negative tracking, small type wants a little air.
        # A hint is the smallest type in the app, so it gets the positive end.
        font.setLetterSpacing(QFont.AbsoluteSpacing, TRACKING_PX * scale)
        return font

    def prepare(self, text: str, dark: bool, scale: float) -> None:
        """Build the panel around one description, at the surface's scale."""
        self._text = text
        self._dark = bool(dark)
        self._scale = float(scale)
        font = self._font(self._scale)
        self._label.setFont(font)
        self._label.setStyleSheet(
            f"color: {INK[self._dark]}; background: transparent;")
        self._label.setText(text)

        shadow, pad_x, pad_y = (SHADOW * self._scale, PAD_X * self._scale,
                                PAD_Y * self._scale)
        # The type is measured here rather than by asking the label: a
        # QLabel answers heightForWidth from a layout that is one event-loop
        # turn behind a font change, which once made a single-line hint
        # reserve two lines. The same metrics that laid out the panel are the
        # only honest source for its size.
        metrics = QFontMetrics(font)
        natural = float(metrics.boundingRect(text).width()) + 4.0
        text_w = min(natural, MAX_TEXT_W * self._scale)
        width = int(round(text_w))
        wrapped = metrics.boundingRect(QRect(0, 0, width, 10000),
                                       Qt.TextWordWrap | Qt.AlignLeft, text)
        height = max(metrics.height(), wrapped.height())
        self._label.setFixedSize(width, int(height))

        panel_w = text_w + 2.0 * pad_x
        panel_h = float(height) + 2.0 * pad_y
        self._panel = QRectF(shadow, shadow, panel_w, panel_h)
        self.setFixedSize(int(math.ceil(panel_w + 2.0 * shadow)),
                          int(math.ceil(panel_h + 2.0 * shadow)))
        self._label.move(int(round(shadow + pad_x)),
                         int(round(shadow + pad_y)))
        self.update()

    def place(self, widget) -> None:
        """Put the panel beside the control, inside the work area."""
        rect = _anchor_rect(widget)
        screen = (QApplication.screenAt(rect.center())
                  or QApplication.primaryScreen())
        area = screen.availableGeometry()
        margin = int(round(SCREEN_MARGIN * self._scale))
        gap = int(round(GAP * self._scale))
        w, h = self.width(), self.height()
        candidates = (
            QPoint(rect.right() + 1 + gap, rect.center().y() - h // 2),
            QPoint(rect.left(), rect.bottom() + 1 + gap),
            QPoint(rect.left() - gap - w, rect.center().y() - h // 2),
            QPoint(rect.left(), rect.top() - gap - h),
        )
        for point in candidates:
            box = QRect(point, self.size())
            if area.contains(box):
                self.move(point)
                return
        # Nothing fits whole: keep the conventional below-left spot but pull
        # it inside the work area, so it is always readable.
        x = min(max(rect.left(), area.left() + margin),
                area.right() - w + 1 - margin)
        y = min(max(rect.bottom() + 1 + gap, area.top() + margin),
                area.bottom() - h + 1 - margin)
        self.move(int(x), int(y))

    # ── motion ───────────────────────────────────────────────────────
    def appear(self) -> None:
        if not system_allows_motion():
            self._clock.stop()
            self._presence.jump_to(1.0)
            self.setWindowOpacity(1.0)
            self.show()
            self.raise_()
            self.update()
            return
        # A fresh arrival starts from nothing: there is no on-screen value to
        # preserve, because the window was hidden.
        self._presence.jump_to(0.0)
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self._presence.set_target(1.0, carry_velocity=False)
        self._clock.start()

    def dismiss(self, immediate: bool = False) -> None:
        if not self.isVisible():
            self._clock.stop()
            self._presence.jump_to(0.0)
            return
        if immediate or not system_allows_motion():
            self._clock.stop()
            self._presence.jump_to(0.0)
            self.setWindowOpacity(0.0)
            self.hide()
            return
        self._presence.set_target(0.0, carry_velocity=False)
        self._clock.start()

    def _tick(self) -> None:
        self._presence.step(1 / 60.0)
        if self._presence.settled:
            self._presence.land()
            self.setWindowOpacity(max(0.0, min(1.0, self._presence.value)))
            self.update()
            self._clock.stop()
            if self._presence.target <= 0.0:
                self.hide()
            return
        self.setWindowOpacity(max(0.0, min(1.0, self._presence.value)))
        self.update()

    # ── painting ─────────────────────────────────────────────────────
    def showEvent(self, event):
        super().showEvent(event)
        # The reader keeps itself out of screen capture, and a hint that leaks
        # into a share while the card does not is the same bug the menu fixed.
        QTimer.singleShot(0, lambda: exclude_from_capture(self))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        fill, stroke, edge_alpha = PANEL[self._dark]
        scale = self._scale
        radius = RADIUS * scale
        panel = QRectF(self._panel)

        # Three soft rings, the same material the rail's plates use: light
        # falling off a floating surface, never a border around a box.
        painter.setPen(Qt.NoPen)
        for spread, alpha in ((4.0, 13), (2.6, 19), (1.4, 26)):
            spread *= scale
            painter.setBrush(QColor(0, 0, 0, alpha))
            halo = QRectF(panel).adjusted(-spread, -spread, spread, spread)
            halo.translate(0.0, 1.6 * scale)
            painter.drawRoundedRect(halo, radius + spread, radius + spread)

        painter.setPen(QPen(QColor(stroke), 1.0))
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(panel, radius, radius)

        # Bright top edge: light catching the material, the same cue the card
        # and the menu use so the three read as one family.
        painter.setPen(QPen(QColor(255, 255, 255, edge_alpha), 1.0))
        painter.drawLine(QPoint(int(panel.left() + radius), int(panel.top() + 1)),
                         QPoint(int(panel.right() - radius), int(panel.top() + 1)))


# ── the one service ──────────────────────────────────────────────────


class TooltipService(QObject):
    """Turns hover and keyboard focus into one tooltip, and takes it back."""

    # The only event types the service acts on. This filter sees every event
    # in the application, including the paint and timer traffic of a reader
    # that is scrolling, so the cheap type test comes before any widget work.
    WATCHED = frozenset((
        QEvent.Enter, QEvent.Leave, QEvent.FocusIn, QEvent.FocusOut,
        QEvent.MouseButtonPress, QEvent.MouseButtonDblClick, QEvent.Wheel,
        QEvent.KeyPress, QEvent.ApplicationDeactivate,
        QEvent.WindowDeactivate, QEvent.Hide, QEvent.Close,
    ))

    def __init__(self, app):
        super().__init__(app)
        self._tip = Tooltip()
        self._anchor = None
        self._pending_anchor = None

        self._pending = QTimer(self)
        self._pending.setSingleShot(True)
        self._pending.timeout.connect(self._show_pending)

        self._leave = QTimer(self)
        self._leave.setSingleShot(True)
        self._leave.setInterval(LEAVE_GRACE_MS)
        self._leave.timeout.connect(self.dismiss)

        self._auto = QTimer(self)
        self._auto.setSingleShot(True)
        self._auto.setInterval(AUTO_HIDE_MS)
        self._auto.timeout.connect(self.dismiss)

        app.installEventFilter(self)

    # ── access ───────────────────────────────────────────────────────
    def tooltip(self) -> Tooltip:
        """The one tooltip window; the checks drive it directly."""
        return self._tip

    # ── state ────────────────────────────────────────────────────────
    def _schedule(self, widget, delay: int) -> None:
        self._leave.stop()
        self._pending_anchor = widget
        self._pending.start(delay)

    def _show_pending(self) -> None:
        widget = self._pending_anchor
        self._pending_anchor = None
        if widget is None or sip.isdeleted(widget) or sip.isdeleted(self._tip):
            return
        if not widget.isVisible():
            return
        text = tip_text(widget)
        if not text:
            return
        self._tip.prepare(text, _theme_for(widget), _scale_for(widget))
        self._tip.place(widget)
        self._tip.appear()
        self._anchor = widget
        self._auto.start()

    def dismiss(self, immediate: bool = False) -> None:
        self._pending.stop()
        self._pending_anchor = None
        self._leave.stop()
        self._auto.stop()
        self._anchor = None
        if not sip.isdeleted(self._tip):
            self._tip.dismiss(immediate)

    # ── events ───────────────────────────────────────────────────────
    def _is_own(self, obj) -> bool:
        # At interpreter shutdown Qt destroys the windows and keeps delivering
        # events through this filter; touching the deleted tooltip there would
        # raise inside an event handler, which ends the process.
        if obj is self._tip:
            return True
        if sip.isdeleted(self._tip):
            return False
        return self._tip.isAncestorOf(obj)

    def eventFilter(self, obj, event):
        kind = event.type()

        # The app's hint, not Qt's: this is also what keeps a system tooltip
        # rectangle from appearing on top of the themed one.
        if kind == QEvent.ToolTip:
            return True
        if kind not in self.WATCHED:
            return False
        if not isinstance(obj, QWidget) or self._is_own(obj):
            return False

        if kind == QEvent.Enter:
            self._leave.stop()
            if tip_text(obj):
                self._schedule(obj, HOVER_DELAY_MS)
            return False

        if kind == QEvent.Leave:
            if self._pending_anchor is obj:
                self._pending.stop()
                self._pending_anchor = None
            anchor = self._anchor
            if anchor is not None and (obj is anchor
                                       or obj.isAncestorOf(anchor)):
                self._leave.start()
            return False

        if kind == QEvent.FocusIn:
            reason = getattr(event, "reason", lambda: None)()
            if (reason in (Qt.TabFocusReason, Qt.BacktabFocusReason,
                           Qt.ShortcutFocusReason) and tip_text(obj)):
                self._schedule(obj, FOCUS_DELAY_MS)
            return False

        if kind == QEvent.FocusOut:
            if obj is self._anchor or obj is self._pending_anchor:
                self.dismiss()
            return False

        # Any decision the user makes outranks a hint: the next one can
        # arrive 650 ms later, but this one must not sit over the result.
        if kind in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick,
                    QEvent.Wheel, QEvent.KeyPress):
            self.dismiss()
            return False

        if kind == QEvent.ApplicationDeactivate:
            self.dismiss(immediate=True)
            return False

        if kind in (QEvent.WindowDeactivate, QEvent.Hide, QEvent.Close):
            anchor = self._anchor
            if anchor is not None and not sip.isdeleted(anchor):
                window = anchor.window()
                if obj is window or obj.isAncestorOf(anchor):
                    self.dismiss(immediate=True)
            return False

        return False


_SERVICE = None


def install(app=None) -> TooltipService:
    """Return the app's tooltip service, creating it once.

    Idempotent on purpose: the reader installs it, and so does the Setup
    dialog, because that dialog is also built on its own by checks that
    never create an overlay.
    """
    global _SERVICE
    app = app or QApplication.instance()
    if app is None:
        return _SERVICE
    if _SERVICE is None or sip.isdeleted(_SERVICE) or _SERVICE.parent() is not app:
        _SERVICE = TooltipService(app)
    return _SERVICE
