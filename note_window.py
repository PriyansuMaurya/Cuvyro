"""Sticky note window for the Notch teleprompter.

A separate 260 x 165 DIP card with a drag strip, a multiline field and a
compact close control, matching the reference geometry. It follows the
reader's theme: charcoal on dark, near-white on light. The window owns no
persistence: the overlay saves the text and position through ``store``.
"""

import ctypes
import sys
import uuid

from PyQt5 import sip
from PyQt5.QtCore import QPoint, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QKeySequence, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QHBoxLayout, QShortcut, QTextEdit, QVBoxLayout, QWidget

NOTE_W, NOTE_H = 260, 165
CORNER_RADIUS = 12
HEADER_H = 24
BODY_COLOR = "#1C1C1C"
TEXT_COLOR = "#E6E6E6"
PLACEHOLDER_COLOR = "#777777"
# Light theme keeps the same geometry and weight, inverted in value so the
# note does not read as a black hole next to a white reader.
LIGHT_BODY_COLOR = "#FFFFFF"
LIGHT_TEXT_COLOR = "#111111"
LIGHT_PLACEHOLDER_COLOR = "#8A8A8A"


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
        if self._dark:
            color = QColor("#EDEDED") if self._hover else QColor("#8A8A8A")
        else:
            color = QColor("#1C1C1E") if self._hover else QColor("#5E5E63")
        painter.setPen(QPen(color, 1.5, Qt.SolidLine, Qt.RoundCap))
        centre = self.width() / 2
        arm = 4
        painter.drawLine(QPoint(int(centre - arm), int(centre - arm)),
                         QPoint(int(centre + arm), int(centre + arm)))
        painter.drawLine(QPoint(int(centre + arm), int(centre - arm)),
                         QPoint(int(centre - arm), int(centre + arm)))

        if self.hasFocus():
            painter.setPen(QPen(QColor("#0A84FF" if self._dark else "#007AFF"), 1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 4, 4)


class NoteWindow(QWidget):
    """A single sticky note."""

    changed = pyqtSignal()
    closed = pyqtSignal(str)  # note id

    def __init__(self, text: str = "", note_id: str | None = None, scale: float = 1.0,
                 dark: bool = True, parent=None):
        super().__init__(parent)
        self.note_id = note_id or uuid.uuid4().hex[:12]
        self._scale = scale
        self._dark = dark
        self._drag_offset = None
        self._suppress_change_signal = False

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("Notch Note")
        self.setAccessibleName("Notch note")
        self.setMinimumSize(self._dim(NOTE_W), self._dim(NOTE_H))
        self.resize(self._dim(NOTE_W), self._dim(NOTE_H))

        self._build()
        # A note could only be closed with a mouse before: its × is a custom
        # glyph and the note had no keyboard handler. Escape gives every note
        # a way out from the keyboard.
        self._escape = QShortcut(QKeySequence("Escape"), self)
        self._escape.setContext(Qt.WindowShortcut)
        self._escape.activated.connect(self.close)

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
        self._close.clicked.connect(self.close)
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
        text_color = TEXT_COLOR if self._dark else LIGHT_TEXT_COLOR
        handle_color = "rgba(255,255,255,0.18)" if self._dark else "rgba(0,0,0,0.18)"
        self._edit.setStyleSheet(
            f"""
            QTextEdit {{
                background: transparent;
                border: none;
                color: {text_color};
                font-family: "Segoe UI", system-ui, sans-serif;
                font-size: {max(9, int(12 * self._scale))}px;
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
                         QColor(PLACEHOLDER_COLOR if self._dark else LIGHT_PLACEHOLDER_COLOR))
        self._edit.setPalette(palette)

    def apply_theme(self, dark: bool) -> None:
        """Follow the reader when its theme changes while a note is open."""
        if dark == self._dark:
            return
        self._dark = dark
        self._close._dark = dark
        self._close.update()
        self._style_editor()
        self.update()

    def _on_text_changed(self) -> None:
        if not self._suppress_change_signal:
            self.changed.emit()

    # ── painting ─────────────────────────────────────────────────────────
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        radius = self._dim(CORNER_RADIUS)

        fill = QPainterPath()
        fill.addRoundedRect(0, 0, self.width(), self.height(), radius, radius)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(BODY_COLOR if self._dark else LIGHT_BODY_COLOR))
        painter.drawPath(fill)

        # Hairline border, inset half a pixel so the stroke is fully inside.
        pen = QPen(QColor("#2A2A2A") if self._dark else QColor("#E2E2E2"))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        border = QPainterPath()
        border.addRoundedRect(0.5, 0.5, self.width() - 1.0, self.height() - 1.0,
                              radius - 0.5, radius - 0.5)
        painter.drawPath(border)

    # ── dragging (header strip only, so text selection still works) ──────
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.pos().y() <= self._dim(HEADER_H):
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_offset is not None:
            self._drag_offset = None
            self.changed.emit()  # persist the new position
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ── state ────────────────────────────────────────────────────────────
    def text(self) -> str:
        return self._edit.toPlainText()

    def to_state(self) -> dict:
        return {
            "id": self.note_id,
            "text": self.text(),
            "x": self.x(),
            "y": self.y(),
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

    def closeEvent(self, event):
        self.closed.emit(self.note_id)
        super().closeEvent(event)
