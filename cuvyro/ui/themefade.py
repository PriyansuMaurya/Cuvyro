"""A flicker-free change of appearance.

Switching light to dark used to recolour each part of a surface as its own
repaint arrived: the window fill first, then the field's stylesheet, then the
few things drawn by hand. On a frameless translucent window that reads as a
flash, and a note and the reader crossing over at slightly different moments
reads as two unrelated changes.

The fix is the one every compositor uses: keep the frame that is already on
screen, recolour everything underneath it in one go, then release the old
frame over a short crossfade. Nothing is painted in an intermediate palette,
so there is no frame in which the surface is half one theme and half the
other, and the duration is a single token (``motion.THEME_CROSSFADE``).

The snapshot is a raised, input-transparent child rather than part of the
parent's ``paintEvent``: children paint after their parent, so a screenshot
drawn in the parent would be covered by the recoloured text instead of
covering it.
"""

from PyQt5 import sip
from PyQt5.QtCore import QElapsedTimer, QPoint, Qt, QTimer
from PyQt5.QtGui import QPainter, QPixmap, QRegion
from PyQt5.QtWidgets import QWidget

from cuvyro.ui import motion
from cuvyro.ui.spring import system_allows_motion


class SnapshotFade(QWidget):
    """The previous frame, fading out over the recoloured surface."""

    def __init__(self, parent: QWidget, pixmap: QPixmap, seconds: float):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # A raised overlay must not take the keyboard from the note's field
        # or the reader's script.
        self.setFocusPolicy(Qt.NoFocus)
        self._pixmap = pixmap
        self._duration = max(0.001, float(seconds))
        self._progress = 0.0
        self._elapsed = QElapsedTimer()
        self._clock = QTimer(self)
        self._clock.setTimerType(Qt.PreciseTimer)
        self._clock.setInterval(16)
        self._clock.timeout.connect(self._tick)
        self.setGeometry(parent.rect())
        self.show()
        self.raise_()
        self._elapsed.start()
        self._clock.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        # A linear release is perceptually even here: both frames are the
        # same surface at the same opacity, so only their colour differs.
        painter.setOpacity(1.0 - self._progress)
        painter.drawPixmap(self.rect(), self._pixmap)

    def _tick(self):
        if sip.isdeleted(self):
            return
        parent = self.parentWidget()
        if parent is None or sip.isdeleted(parent):
            self.finish()
            return
        self._progress = min(1.0, self._elapsed.elapsed() / 1000.0 / self._duration)
        self.update()
        if self._progress >= 1.0:
            self.finish()

    def finish(self):
        """Release the old frame now (the timer, a close, or a new change)."""
        if sip.isdeleted(self):
            return
        self._clock.stop()
        parent = self.parentWidget()
        if parent is not None and not sip.isdeleted(parent):
            if getattr(parent, "_theme_fade", None) is self:
                parent._theme_fade = None
        self.hide()
        self.deleteLater()


def capture(widget: QWidget) -> QPixmap | None:
    """The frame currently on screen, or ``None`` when there is nothing to keep.

    Must be called *before* the surface starts recolouring itself. The pixmap
    is transparent outside the surface's own shape, which is what keeps a
    rounded translucent card from gaining square corners for the length of
    the fade.
    """
    if widget is None or sip.isdeleted(widget) or not widget.isVisible():
        return None
    if not system_allows_motion():
        return None
    size = widget.size()
    if size.width() <= 0 or size.height() <= 0:
        return None
    ratio = max(1.0, widget.devicePixelRatioF())
    pixmap = QPixmap(int(round(size.width() * ratio)),
                     int(round(size.height() * ratio)))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
    # Draw the children but not the window background: the widget paints its
    # own rounded shape, and the background would fill the corners.
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    widget.render(painter, QPoint(0, 0), QRegion(widget.rect()),
                  QWidget.DrawChildren)
    painter.end()
    return pixmap


def crossfade(widget: QWidget, pixmap: QPixmap | None,
              seconds: float | None = None) -> "SnapshotFade | None":
    """Hold ``pixmap`` over ``widget`` and release it, replacing any fade
    already in flight so a second theme change never stacks two old frames."""
    if pixmap is None or widget is None or sip.isdeleted(widget):
        return None
    if not system_allows_motion():
        return None
    existing = getattr(widget, "_theme_fade", None)
    if existing is not None and not sip.isdeleted(existing):
        existing.finish()
    fade = SnapshotFade(
        widget, pixmap,
        motion.THEME_CROSSFADE if seconds is None else seconds)
    widget._theme_fade = fade
    return fade
