import os
import sys
import time
import ctypes
from typing import List, NamedTuple, Optional

from PyQt5.QtCore import (
    Qt, QTimer, QPoint, pyqtSlot, QRectF, QRect, QEvent,
    QVariantAnimation, QEasingCurve, QPointF, QSize, QObject,
    QPropertyAnimation, QAbstractAnimation, QElapsedTimer, QSettings
)
from PyQt5.QtGui import (
    QFont, QFontMetrics, QColor, QPainter, QPen, QTextCursor, QTextCharFormat,
    QTextBlockFormat, QKeySequence,
    QDragEnterEvent, QDropEvent, QMouseEvent, QPaintEvent, QKeyEvent,
    QRadialGradient, QBrush, QPainterPath, QWheelEvent
)
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QTextEdit, QFileDialog,
    QApplication, QMenu, QAction, QShortcut,
    QSlider, QWidgetAction, QHBoxLayout, QLabel,
    QToolButton, QAbstractButton, QPushButton,
    QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
    QMessageBox, QDialog, QDialogButtonBox, QScrollArea,
    QStackedWidget, QButtonGroup,
)

# Assumed external dependencies based on your project structure
from audio_worker import AudioWorker
from matching_engine import MatchingEngine
from settings_dialog import SettingsDialog
from note_window import NoteWindow
from store import Store, sane_coord, sane_number


class _RenderedLine(NamedTuple):
    """One visual line of the script, resolved to script-word indices."""

    first_word: int
    last_word: int
    top: float
    height: float


from spring import Spring, SpringGroup, system_allows_motion
from controls import (
    CompactButton, GlyphButton, MenuRowNav, MicButton, MinimizeButton,
    NoteButton, RoundedMenu, ScriptTextEdit, TextButton,
)
from win_capture import exclude_from_capture


class HelpDialog(QDialog):
    """First-time guide / Help dialog — frameless, rounded, pill-style."""
    CORNER_RADIUS = 16

    def __init__(self, parent=None, dark: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Notch Help")
        self.setAccessibleName("Notch help")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(360, 310)
        self._dark = dark
        self._drag_pos = None
        self._close_pending = None
        self._opacity_anim = QPropertyAnimation(self, b"windowOpacity")
        self._opacity_anim.setDuration(300)
        self._opacity_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._opacity_anim.setStartValue(0.0)
        self._opacity_anim.setEndValue(1.0)
        self._fade_out_anim = QPropertyAnimation(self, b"windowOpacity")
        self._fade_out_anim.setDuration(300)
        self._fade_out_anim.setEasingCurve(QEasingCurve.InCubic)
        self._fade_out_anim.setStartValue(1.0)
        self._fade_out_anim.setEndValue(0.0)
        self._fade_out_anim.finished.connect(self._on_fade_out_finished)

        # The guide is a surface of the app, not a system leftover: on paper
        # it follows the same light palette as the reader and notes.
        if self._dark:
            surface, border = "#1e1e1e", "#333333"
            text, strong, muted = "#c8c8c8", "#ffffff", "#888888"
            button_bg, button_text, button_border = "#2a2a2a", "#b0b0b0", "#444444"
            scroll_bg, scroll_handle, scroll_hover = "#2a2a2a", "#444444", "#555555"
            hover_bg, press_bg = "#007AFF", "#005ECB"
        else:
            surface, border = "#F7F7F8", "#DCDCDE"
            text, strong, muted = "#1C1C1E", "#111111", "#6E6E73"
            button_bg, button_text, button_border = "#F2F2F3", "#1C1C1E", "#DCDCDE"
            scroll_bg, scroll_handle, scroll_hover = "#EDEDEF", "#C7C7CC", "#AEAEB2"
            # White on #007AFF is 4.4:1; the 10 px button label needs 4.5:1.
            hover_bg, press_bg = "#0066CC", "#0059B3"
        self._surface = surface
        self._border = border
        self.setStyleSheet(f"""
            QDialog {{ background: transparent; }}
            QLabel {{
                color: {text};
                font-family: "Poppins", "Segoe UI", sans-serif;
                font-weight: 400;
                font-size: 10px;
                background: transparent;
            }}
            QPushButton {{
                background: {button_bg};
                color: {button_text};
                border: 1px solid {button_border};
                border-radius: 8px;
                padding: 5px 16px;
                font-family: "Poppins", "Segoe UI", sans-serif;
                font-size: 10px;
            }}
            QPushButton:hover {{ background: {hover_bg}; color: #ffffff; border-color: {hover_bg}; }}
            QPushButton:pressed {{ background: {press_bg}; color: #ffffff; }}
            QScrollBar:vertical {{
                border: none;
                background: {scroll_bg};
                width: 8px;
                border-radius: 4px;
                margin: 0px 0px 0px 0px;
            }}
            QScrollBar::handle:vertical {{
                background: {scroll_handle};
                min-height: 20px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {scroll_hover};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                border: none;
                background: none;
                height: 0px;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: none;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 14, 16, 14)

        # Header label (small, subtle)
        header = QLabel("Help")
        header.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {strong}; background: transparent;")
        layout.addWidget(header)

        label_style = f"""
            QLabel {{
                color: {text};
                font-family: "Poppins", "Segoe UI", sans-serif;
                font-weight: 400;
                font-size: 10px;
                background: transparent;
                padding-right: 12px;
            }}
        """
        def _make_label(html):
            lbl = QLabel(html)
            lbl.setWordWrap(True)
            lbl.setTextFormat(Qt.RichText)
            lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            lbl.setStyleSheet(label_style)
            return lbl

        def _make_line():
            line = QWidget()
            line.setFixedHeight(1)
            line.setStyleSheet(f"background-color: {border};")
            return line

        body_widget = QWidget()
        body_layout = QVBoxLayout(body_widget)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)

        body_layout.addWidget(_make_label(
            "<p style='margin:4px 0'>Notch is a smart, invisible to screensharing teleprompter that stays on top of your screen.</p>"
            "<p style='margin:6px 0 2px 0'><b>Main features:</b></p>"
            "<ul style='margin:0; padding-left:16px;'>"
            "<li><b>Invisible to screenshare</b> — Not shown in screen recordings or video calls</li>"
            "<li><b>Stays on top</b> — Always visible above other windows</li>"
            "<li><b>Voice-follow</b> or <b>Manual</b> timed scroll</li>"
            "<li><b>Local speech recognition</b> — Runs offline, no data sent to the cloud</li>"
            "</ul>"
        ))
        body_layout.addWidget(_make_line())
        body_layout.addWidget(_make_label(
            "<p style='margin:6px 0 2px 0'><b>Get started:</b></p>"
            "<ul style='margin:0; padding-left:16px;'>"
            "<li><b>Load a script</b> — Double-click Notch to edit, drag a .txt file, or use <i>Load Script</i>.</li>"
            "<li><b>Voice</b> — the script follows your speech. Switch modes in the right-click menu.</li>"
            "<li><b>Manual</b> — press <b>Space</b> to start or pause timed scroll. <b>Arrow keys</b> adjust speed.</li>"
            "<li><b>Right-click</b> the card for mode, font, theme, Help, Setup and Quit.</li>"
            "<li><b>Minimize</b> (the − control) puts the card away as a small pill; click it to bring it back.</li>"
            "</ul>"
        ))
        body_layout.addWidget(_make_line())
        body_layout.addWidget(_make_label(
            "<p style='margin:6px 0 2px 0'><b>Voice commands</b> (Notch mode):</p>"
            "<ul style='margin:0; padding-left:16px;'>"
            "<li><i>\"Notch Restart\"</i> — Restart from the beginning</li>"
            "<li><i>\"Notch Expand\"</i> — Open edit mode</li>"
            "<li><i>\"Notch Mute\"</i> — Mute/unmute the mic</li>"
            "<li><i>\"Notch Close\"</i> — Close Notch</li>"
            "</ul>"
        ))
        body_layout.addWidget(_make_line())
        body_layout.addWidget(_make_label(
            "<p style='margin:6px 0 2px 0'><b>Shortcuts:</b></p>"
            "<ul style='margin:0; padding-left:16px;'>"
            "<li><i>Ctrl+R</i> — Restart script</li>"
            "<li><i>Ctrl+N</i> — New note</li>"
            "<li><i>Ctrl+=</i> / <i>Ctrl+-</i> — Larger / smaller script</li>"
            "<li><i>Space</i> — Start or pause timed scroll (Manual)</li>"
            "<li><i>Esc</i> — Leave the editor or pill; closes Notch when reading</li>"
            "</ul>"
        ))
        body_layout.addWidget(_make_line())
        credit = QLabel(
            f'<a href="https://socia.ae/products/notch" style="color:{muted}; '
            'font-size:9px; text-decoration:none;">Notch by Socia</a>')
        credit.setOpenExternalLinks(True)
        credit.setTextFormat(Qt.RichText)
        credit.setStyleSheet(
            f"QLabel {{ color: {muted}; font-size: 9px; background: transparent; "
            "padding-right: 12px; }")
        credit.setCursor(Qt.PointingHandCursor)
        credit.setTextInteractionFlags(Qt.TextBrowserInteraction)
        body_layout.addWidget(credit)

        scroll = QScrollArea()
        scroll.setWidget(body_widget)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.viewport().setStyleSheet("background: transparent;")
        layout.addWidget(scroll, 1)

        ok = QPushButton("Got it")
        ok.setCursor(Qt.PointingHandCursor)
        ok.clicked.connect(self.accept)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(ok)
        layout.addLayout(btn_layout)

    def _on_fade_out_finished(self):
        pending = self._close_pending
        self._close_pending = None
        if pending == "accept":
            super(HelpDialog, self).accept()
        elif pending == "reject":
            super(HelpDialog, self).reject()

    def closeEvent(self, event):
        if self._close_pending is not None or self._fade_out_anim.state() == QAbstractAnimation.Running:
            event.ignore()
            return
        self._close_pending = "reject"
        self._fade_out_anim.start()
        event.ignore()

    def reject(self):
        if self._fade_out_anim.state() == QAbstractAnimation.Running:
            return
        if self._close_pending is not None:
            return
        self._close_pending = "reject"
        self._fade_out_anim.start()

    def accept(self):
        if self._fade_out_anim.state() == QAbstractAnimation.Running:
            return
        if self._close_pending is not None:
            return
        self._close_pending = "accept"
        self._fade_out_anim.start()

    # ── Custom rounded painting (matches pill / settings) ──────────
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self.CORNER_RADIUS, self.CORNER_RADIUS)
        painter.setPen(QPen(QColor(self._border), 1))
        painter.setBrush(QColor(self._surface))
        painter.drawPath(path)

    # ── Draggable ──────────────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPos() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    # ── Position below parent (fade-in only, no slide) ──────────────
    def showEvent(self, event):
        super().showEvent(event)
        exclude_from_capture(self)
        parent = self.parent()
        if parent and parent.isVisible() and parent.width() > 0:
            gap = 8
            pt = parent.mapToGlobal(parent.rect().topLeft())
            x = pt.x() + (parent.width() - self.width()) // 2
            y_final = pt.y() + parent.height() + gap
            self.setGeometry(QRect(x, y_final, self.width(), self.height()))
            self.setWindowOpacity(0.0)
            self._opacity_anim.start()


class NotchOverlay(QWidget):
    """
    Minimalist 'Dynamic Island' style teleprompter.
    Pulses with voice activity.
    """

    # (display name, actual font family for QFont)
    FONT_OPTIONS = [
        ("Segoe UI", "Segoe UI"),
        ("Consolas", "Consolas"),
        ("Arial", "Arial"),
        ("Open Sans", "Open Sans"),
        ("Times", "Times New Roman"),
        ("Helvetica", "Helvetica"),
    ]

    # Dimensions — match the reference compact overlay (DIP). The window is
    # wider than the card by a transparent rail: the four controls live there,
    # outside the reading surface, so no glyph sits over a word.
    CARD_W, CARD_H = 340, 122
    RAIL_W = 44             # gap + button + right pad
    DEFAULT_W, DEFAULT_H = CARD_W + RAIL_W, CARD_H
    MIN_W, MIN_H = CARD_W + RAIL_W, CARD_H
    TOP_INSET = 6            # DIP between the work-area top and the card
    COMPACT_CARD_W = 100   # ~2 word width with compact font
    COMPACT_W = COMPACT_CARD_W + RAIL_W
    COMPACT_FONT = 8  # smallest allowed text size; 6 pt was below reading size
    MODEL_PATH = "model"  # overridden in __init__ when running as frozen exe

    # Script formats the reader can actually open. The README used to promise
    # PDF and DOCX as well, but no extractor was ever wired in, so the file
    # picker and the drop target now agree with the app.
    SCRIPT_SUFFIXES = (".txt",)

    # Aesthetic Constants
    CORNER_RADIUS = 24      # Pill shape (lower = less curved)
    GLOW_COLOR = QColor(41, 98, 255)       # Blue = Notch (follows voice)
    MANUAL_GLOW_COLOR = QColor(46, 204, 113)  # Green = Keys & Scroll

    # Voice follow is line based. A recognised word must be this close to a
    # script word to count, and it may match a few words ahead when the
    # speaker stumbles or the recogniser drops a word.
    WORD_MATCH_SCORE = 80
    WORD_SKIP_TOLERANCE = 3
    # A result may recover from a longer skipped clause or aside, but only on
    # two consecutive recognised words (see MatchingEngine.align_forward).
    # The caller's word range still bounds the search, so a partial can never
    # leave the line on screen.
    RESYNC_SKIP_TOLERANCE = 9
    # A partial can step the line before Vosk finalises, but only when its own
    # last word is a close match for the line's last word. That is what keeps
    # the scroll at the sentence boundary instead of after the pause.
    PARTIAL_FINISH_SCORE = 88
    # When one final result confirms several lines at once (a long utterance
    # with no pause), the reader catches up one line at a time instead of
    # jumping, so the eye is never asked to skip a line.
    LINE_STEP_INTERVAL = 0.18

    # Share of the words on screen that sits above the line being read. The
    # rest stays ahead of the speaker. The anchor is word weighted, not a
    # fixed height: one line can hold two words and the next twelve, so a
    # pixel anchor cannot hold a 30/70 split for long.
    READING_SHARE = 0.30
    # Words given the "being spoken" band behind the line.
    FOCUS_WORDS = 3

    def __init__(self):
        super().__init__()
        # When packaged as exe, look for model next to the executable
        if getattr(sys, "frozen", False):
            self.MODEL_PATH = os.path.join(os.path.dirname(sys.executable), "model")
            self._log_install_paths()
        else:
            self.MODEL_PATH = NotchOverlay.MODEL_PATH

        # ── State ────────────────────────────────────────────────────
        self._font_size = 10
        self._font_family = "Segoe UI"  # reference card: system sans, ~16 px
        self._dark_theme = True  # False = light (white pill in Notch/Keys & Scroll)
        self._opacity = 1.0
        self._voice_mode = True
        self._voice_wpm = 60
        self._manual_wpm = 150
        self._manual_running = False
        self._manual_scroll_accum = 0.0
        self._voice_scroll_accum = 0.0
        self._drag_pos: Optional[QPoint] = None
        self._is_speaking = False
        self._checkpoints = {}
        self._edit_mode = False
        self._text_fade_for_expand = True
        self._collapsed_size = None
        self._prev_voice_mode = True
        self._compact_mode = False
        self._peeked = False
        self._peek_restore: Optional[QRect] = None
        self._size_before_compact: Optional[QSize] = None
        self._font_size_before_compact = 10

        # ── DPI scaling (so UI looks similar on different screen sizes) ─
        desktop = QApplication.desktop()
        if desktop and hasattr(desktop, 'logicalDpiX'):
            dpi = desktop.logicalDpiX()
        else:
            screen = QApplication.primaryScreen()
            if screen and hasattr(screen, 'devicePixelRatio'):
                dpi = 96.0 * screen.devicePixelRatio()
            else:
                dpi = 96
        self._scale = max(0.75, min(2.0, dpi / 96.0))
        self.RAIL_W = int(self.RAIL_W * self._scale)
        self.RAIL_GAP = int(7 * self._scale)
        self.RAIL_SPACING = int(2 * self._scale)
        self.DEFAULT_W = int(self.CARD_W * self._scale) + self.RAIL_W
        self.DEFAULT_H = int(self.CARD_H * self._scale)
        self.MIN_W = int(self.CARD_W * self._scale) + self.RAIL_W
        self.MIN_H = int(self.CARD_H * self._scale)
        self.COMPACT_W = int(self.COMPACT_CARD_W * self._scale) + self.RAIL_W
        self.CORNER_RADIUS = int(24 * self._scale)
        self.COMPACT_FONT = max(6, min(13, int(8 * self._scale)))
        self._font_size = max(6, min(24, int(12 * self._scale)))  # 12pt ≈ 16px at 96 DPI
        self._font_size_before_compact = self._font_size

        # ── Animation State ──────────────────────────────────────────
        self._current_glow_alpha = 28  # Base glow (0-255); lower = more transparent
        self._target_glow_alpha = 28
        self._smoothed_volume_level = 0.0  # EMA of raw mic level for smooth bar

        # ── Workers ──────────────────────────────────────────────────
        self._audio_worker: Optional[AudioWorker] = None
        self._stopping_worker = None  # Worker being stopped; _start_listening waits for it
        self._device_index: Optional[int] = None
        self._noise_gate: float = 0.01
        self._input_gain: float = 2.0
        self._matching = MatchingEngine()
        self._script_text = ""
        self._notes: list[NoteWindow] = []
        self._store = Store()
        self._state_restored = False
        # Kept apart from ``_state_restored``: settings are applied before the
        # script, and one flag for both would make the second restore bail
        # out because the first one already ran.
        self._prefs_restored = False
        # Sticky notes are cleared while closing, so a shutdown flush that
        # arrives after closeEvent must not write that empty list over the
        # notes just saved. One flag closes the save path for good.
        self._save_enabled = True
        self._last_match_time: Optional[float] = None
        self._last_match_pos = -1
        # Consecutive finals that confirmed nothing. One is normal (a cough,
        # a repeated phrase); two in a row means the reader has lost its
        # place, and only then may a final re-anchor the pointer.
        self._stalled_finals = 0
        self._speech_active_until = 0.0
        # Voice mode: the word-balanced anchor, kept so a retarget can be
        # repeated after a wheel hold without recomputing the share.
        self._voice_target_scroll: Optional[float] = None
        self._last_manual_scroll_time = 0.0  # don't override scroll for this long after user scrolls (seconds)

        # ── Line-based voice follow ──────────────────────────────────
        # The line being read is held still until its last word has been
        # confirmed by a final speech result; only then does the reader step
        # to the next visual line. Line breaks come from the rendered
        # document layout, so they follow font size, style and width.
        self._line_spans: List[_RenderedLine] = []
        self._current_line = 0
        self._confirmed_word = -1  # furthest word confirmed by final results
        self._partial_word = -1    # furthest word seen in a tentative result
        self._reflow_pending_top: Optional[int] = None
        self._next_line_step_at = 0.0
        # Shading is applied incrementally: only the run between the previous
        # and the new boundary is re-formatted. A full document format is kept
        # for a text, theme or typography change, where old ranges are stale.
        self._highlight_end = None
        self._highlight_key = None
        self._focus_span = None   # character range of the banded words

        # ── Scroll motion ────────────────────────────────────────────
        # The script glides on a critically damped spring instead of an
        # exponential step. A spring starts from the pixel already on screen
        # and never overshoots, so a line advance reads as the page moving
        # rather than the text jumping a whole line in one frame.
        self._scroll_spring = Spring(0.0, damping=1.0, response=0.30,
                                     rest_value=0.25, rest_velocity=6.0)
        self._scroll_moving = False
        self._scroll_last_time = 0.0

        # ── Timers ───────────────────────────────────────────────────
        # Scroll timer (glow + manual scroll); 60 FPS for smooth response
        self._scroll_timer = QTimer(self)
        # Stay under the Windows timer tick (15.6 ms): at 17 ms every sixth
        # frame misses its slot and lands 31 ms late, which reads as the script
        # stumbling mid-line. 16 ms keeps the glide even.
        self._scroll_timer.setInterval(16)  # ~60 FPS
        self._scroll_timer.timeout.connect(self._tick)

        # Resize events arrive every frame while the narrow/wide animation
        # runs; rebuild the line map once the layout has settled.
        self._reflow_timer = QTimer(self)
        self._reflow_timer.setSingleShot(True)
        self._reflow_timer.setInterval(120)
        self._reflow_timer.timeout.connect(self._on_reflow_timeout)

        # Persistence is debounced. A note emits `changed` on every keystroke,
        # and the whole state file (script plus every note) used to be written
        # and fsynced each time: with a long script that is ~25 ms of disk work
        # per character, which is felt as typing lag. Typing now schedules one
        # write after the pause, and every close path still flushes it.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._save_state)

        # ── Window Setup ─────────────────────────────────────────────
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("Notch")
        self.setAccessibleName("Notch teleprompter")
        self.setAcceptDrops(True)
        self.setMinimumSize(self.MIN_W, self.MIN_H)
        self.resize(self.DEFAULT_W, self.DEFAULT_H)
        self._center_top()
        self._collapsed_size = self.size()

        # ── Fade-in animation ─────────────────────────────────────────
        self.setWindowOpacity(0.0)
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity")
        self._fade_anim.setDuration(400)  # 0.4s
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.OutCubic)

        # ── Motion ───────────────────────────────────────────────────
        # Every spatial transition (edit expand/collapse, narrow/wide) runs
        # on one spring clock, one spring per edge. A spring starts from the
        # geometry already on screen and keeps its velocity, so reversing
        # mid-flight bends the motion instead of restarting it.
        self._motion = SpringGroup(self, interval_ms=16)
        self._sx = self._motion.add("x", 0.0, damping=0.88, response=0.34)
        self._sy = self._motion.add("y", 0.0, damping=0.88, response=0.34)
        self._sw = self._motion.add("w", float(self.DEFAULT_W), damping=0.88, response=0.34)
        self._sh = self._motion.add("h", float(self.DEFAULT_H), damping=0.88, response=0.34)
        self._glow = self._motion.add("glow", 28.0, damping=1.0, response=0.26,
                                      rest_value=0.05, rest_velocity=0.5)
        self._motion.tick.connect(self._on_motion_tick)
        self._motion.settled.connect(self._on_motion_settled)
        self._motion_intent = None      # "expand" | "collapse" | "narrow" | "wide"
        self._edit_target = False       # what the user last asked for
        rect = self.geometry()
        self._motion.jump(x=float(rect.x()), y=float(rect.y()),
                          w=float(rect.width()), h=float(rect.height()))

        # ── Build UI ─────────────────────────────────────────────────
        self._build_ui()
        self._setup_shortcuts()
        self._connect_lifecycle()
        self._update_window_mask()
        QTimer.singleShot(0, self._apply_capture_exclusion)
        QTimer.singleShot(0, self._apply_blur_effect)
        
        # Start timer immediately for animation loop
        self._scroll_timer.start()
        print("[Notch] Overlay ready (mode: Notch=voice follow, Keys & Scroll=manual)", flush=True)

    def _on_motion_tick(self):
        """Apply the springs to the window. One place, every frame."""
        rect = QRect(int(round(self._sx.value)), int(round(self._sy.value)),
                     max(1, int(round(self._sw.value))),
                     max(1, int(round(self._sh.value))))
        if rect != self.geometry():
            self.setGeometry(rect)
        glow = int(round(self._glow.value))
        if glow != self._current_glow_alpha:
            self._current_glow_alpha = glow
            self.update()

    def _animate_geometry_to(self, target: QRect, response: float = 0.34):
        """Retarget the window's edges. Reversible at any point."""
        self._sx.response = self._sy.response = self._sw.response = self._sh.response = response
        self._motion.set_targets(x=float(target.x()), y=float(target.y()),
                                 w=float(target.width()), h=float(target.height()))

    def _on_motion_settled(self):
        """Run the finish step for whichever transition just landed."""
        intent, self._motion_intent = self._motion_intent, None
        if intent == "expand":
            self._on_edit_motion_finished()
        elif intent == "collapse":
            self._on_edit_motion_finished()
        elif intent in ("narrow", "wide"):
            self._on_compact_motion_finished(narrow=intent == "narrow")
        # Every finished move is the geometry the next launch should restore.
        if intent is not None:
            self._schedule_save()

    def _on_text_fade_out_finished(self):
        """Text has faded out; apply styling while invisible, then fade back in (or defer for collapse)."""
        if self._text_fade_for_expand:
            self._edit_mode = True
            self._text_edit.setReadOnly(False)
            # The editor is the one surface where undo belongs.
            self._text_edit.document().setUndoRedoEnabled(True)
            self._text_edit.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self._text_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self._text_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            # Reference editor: the same dark card as the reader, light text,
            # low-contrast scrollbars. Light theme keeps the paper surface.
            editor_bg = "#ffffff" if not self._dark_theme else "#000000"
            editor_fg = "#111111" if not self._dark_theme else "#fafafa"
            bar_track = "#f0f0f0" if not self._dark_theme else "#111111"
            bar_handle = "#c0c0c0" if not self._dark_theme else "#3c3c3c"
            bar_hover = "#a0a0a0" if not self._dark_theme else "#555555"
            self._text_edit.setStyleSheet("""
                QTextEdit { background: %s; color: %s; border: none; }
                QScrollBar:vertical {
                    background: %s;
                    width: 10px;
                    border-radius: 5px;
                    margin: 2px 0;
                }
                QScrollBar::handle:vertical {
                    background: %s;
                    min-height: 24px;
                    border-radius: 5px;
                }
                QScrollBar::handle:vertical:hover {
                    background: %s;
                }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                    height: 0;
                    background: none;
                }
                QScrollBar:horizontal {
                    background: %s;
                    height: 10px;
                    border-radius: 5px;
                    margin: 0 2px;
                }
                QScrollBar::handle:horizontal {
                    background: %s;
                    min-width: 24px;
                    border-radius: 5px;
                }
                QScrollBar::handle:horizontal:hover {
                    background: %s;
                }
                QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                    width: 0;
                    background: none;
                }
            """ % (editor_bg, editor_fg, bar_track, bar_handle, bar_hover,
                   bar_track, bar_handle, bar_hover))
            self._clear_edit_mode_char_formats()
            self._reserve_editor_footer(True)
            # The editor takes the reading area back, below the floating bar.
            self._apply_root_margins()
            self._text_edit.setFocus()
            self._layout_done_button()
            self._done_btn.show()
            self._done_btn.raise_()
            self._text_fade_in.start()
        else:
            # Collapsing: apply pill styling while text is invisible (no snap)
            new_text = self._text_edit.toPlainText()
            script_changed = new_text != self._script_text
            cleared = bool(script_changed and not new_text.strip())
            self._script_text = new_text
            self._matching.load_script(self._script_text)
            if cleared:
                # The script the notes were written under is gone. Saving
                # instead would keep them in the file and reopen them over the
                # empty reader on the next launch.
                self._discard_script_notes()
            else:
                self._save_state()  # commit the edit now, not only on close
            self._last_match_time = None
            self._last_match_pos = -1
            self._stalled_finals = 0
            self._line_spans = []
            self._current_line = 0
            self._confirmed_word = -1
            self._partial_word = -1
            self._voice_target_scroll = None
            self._edit_mode = False
            self._done_btn.hide()
            # The reader gives the whole window back to the script.
            self._apply_root_margins()
            self._reserve_editor_footer(False)
            self._text_edit.setReadOnly(True)
            self._text_edit.document().setUndoRedoEnabled(False)
            self._text_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self._text_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self._text_edit.setAlignment(Qt.AlignCenter)
            self._apply_read_mode_style()
            self._highlight_key = None  # the editor rewrote the document
            self._focus_span = None
            self._apply_highlight(-1)
            self._refresh_lines()
            if script_changed:
                self._text_edit.moveCursor(QTextCursor.Start)
                self._text_edit.verticalScrollBar().setValue(0)
            if self._voice_mode:
                self._scroll_to_reading_line(0)
            # Text stays invisible; fade-in deferred to _on_edit_geometry_anim_finished

    # ═══════════════════════════════════════════════════════════════════
    #  UI Construction (Ultra Minimal)
    # ═══════════════════════════════════════════════════════════════════

    def _build_ui(self):
        root = QVBoxLayout(self)
        self._root_layout = root
        s = self._scale
        # The type owns the full height of the window: no header strip, no
        # bottom strip, nothing between the script and the edges. The plain
        # text column paints opaquely to the edges; the controls float over it.
        root.setContentsMargins(int(18 * s), 0, int(18 * s) + self.RAIL_W, 0)
        root.setSpacing(0)

        # ── Chrome (vertical rail, outside the card) ─────────────────
        # The controls are not over the script: the window carries a
        # transparent strip to the right of the card and the rail floats in
        # that gap. One container still means one fade, not four.
        self._chrome = QWidget(self)
        self._chrome.setAttribute(Qt.WA_TranslucentBackground, True)
        self._chrome.setStyleSheet("background: transparent;")
        rail = QVBoxLayout(self._chrome)
        rail.setContentsMargins(0, 0, 0, 0)
        rail.setSpacing(self.RAIL_SPACING)

        self._muted = False
        self._close_btn = MinimizeButton()
        self._close_btn.setToolTip("Minimize (or Esc)")
        self._close_btn.setAccessibleName("Minimize")
        self._close_btn.clicked.connect(self._minimize)
        rail.addWidget(self._close_btn)

        self._note_btn = NoteButton()
        self._note_btn.setToolTip("New note (Ctrl+N)")
        self._note_btn.setAccessibleName("New note")
        self._note_btn.clicked.connect(self._open_note)
        rail.addWidget(self._note_btn)
        self._mic_btn = MicButton()
        self._mic_btn.setToolTip("Mute mic (stop voice from advancing script)")
        self._mic_btn.setAccessibleName("Mute microphone")
        self._mic_btn.clicked.connect(self._toggle_mic_mute)
        rail.addWidget(self._mic_btn)
        self._compact_btn = CompactButton()
        self._compact_btn.setToolTip("Narrow reader (about two words per line)")
        self._compact_btn.setAccessibleName("Switch to narrow reader")
        self._compact_btn.clicked.connect(self._toggle_compact_mode)
        rail.addWidget(self._compact_btn)
        for btn in (self._close_btn, self._note_btn, self._mic_btn,
                    self._compact_btn):
            btn.installEventFilter(self)
        # No menu button: a "more" glyph earned its place only while the menu
        # had nowhere else to open. The menu is still there on a right click,
        # over the card or over the script, and the rail stays four quiet
        # glyphs instead of five with one that does nothing else.
        self._set_top_bar_icon_size(int(18 * s))

        # Idle recede: the chrome dissolves after a pause and returns the
        # moment the pointer comes back, so the script is the only thing
        # left on screen while you are actually reading.
        self._chrome_opacity = QGraphicsOpacityEffect(self._chrome)
        self._chrome.setGraphicsEffect(self._chrome_opacity)
        self._chrome_opacity.setOpacity(1.0)
        self._chrome_fade = QPropertyAnimation(self._chrome_opacity, b"opacity", self)
        self._chrome_fade.setDuration(240)
        self._chrome_fade.setEasingCurve(QEasingCurve.OutCubic)
        self.setMouseTracking(True)
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        # Three seconds of stillness and the controls go: the script is the
        # only thing the reader should be looking at.
        self._idle_timer.setInterval(3000)
        self._idle_timer.timeout.connect(self._recede_chrome)

        # ── Script text area ─────────────────────────────────────────
        # Plain, fully opaque type from the card's top edge down (see
        # ScriptTextEdit): no fade and no blur. The opening lead is part of
        # the document, so it scrolls away behind the controls with the text.
        self._text_edit = ScriptTextEdit(self)
        self._text_edit.setAcceptRichText(False)  # Paste as plain text only (no colors, bg, etc.)
        self._text_edit.setReadOnly(True)
        # The reader formats a run of characters on every recognised word.
        # With undo enabled each of those writes is kept as an undo command,
        # so a session grew the stack by ~20 steps a second for nothing: there
        # is no undo history to protect while the document is read-only. The
        # stack is switched back on when the editor opens.
        self._text_edit.document().setUndoRedoEnabled(False)
        self._text_edit.setFont(self._text_font())
        self._text_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._text_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._text_edit.setLineWrapMode(QTextEdit.WidgetWidth)
        self._text_edit.setAlignment(Qt.AlignCenter)
        self._text_edit.setPlaceholderText("Notch\n(Double click or drop script)")
        self._apply_read_mode_style()
        self._text_edit.installEventFilter(self)
        self._text_edit.viewport().installEventFilter(self)
        # The script is under the pointer almost everywhere in the window, so
        # movement over it has to wake the controls as well.
        self._text_edit.viewport().setMouseTracking(True)
        root.addWidget(self._text_edit)

        # The editor's way out. Escape works from the keyboard, but a
        # surface you can only leave with a hidden key is a trap.
        self._done_btn = TextButton("Done", self, primary=True)
        self._done_btn.clicked.connect(self._toggle_edit_mode)
        self._done_btn.hide()

        # Text fade out/in for smooth mode transitions (expand/collapse)
        self._text_opacity = QGraphicsOpacityEffect(self._text_edit)
        self._text_edit.setGraphicsEffect(self._text_opacity)
        self._text_opacity.setOpacity(1.0)
        self._text_fade_out = QPropertyAnimation(self._text_opacity, b"opacity")
        self._text_fade_out.setDuration(150)
        self._text_fade_out.setStartValue(1.0)
        self._text_fade_out.setEndValue(0.0)
        self._text_fade_out.setEasingCurve(QEasingCurve.InCubic)
        self._text_fade_out.finished.connect(self._on_text_fade_out_finished)
        self._text_fade_in = QPropertyAnimation(self._text_opacity, b"opacity")
        self._text_fade_in.setDuration(150)
        self._text_fade_in.setStartValue(0.0)
        self._text_fade_in.setEndValue(1.0)
        self._text_fade_in.setEasingCurve(QEasingCurve.OutCubic)

        # Speed feedback popup (manual mode: bottom right, fade in/out)
        self._speed_feedback_lbl = QLabel(self)
        self._speed_feedback_lbl.setStyleSheet("""
            background-color: rgba(44, 44, 46, 0.55);
            color: #ffffff;
            padding: 4px 10px;
            border-radius: 6px;
            font-family: "Poppins", "Segoe UI", sans-serif;
            font-weight: 400;
            font-size: 11px;
        """)
        self._speed_feedback_lbl.hide()
        self._speed_feedback_opacity = QGraphicsOpacityEffect(self._speed_feedback_lbl)
        self._speed_feedback_lbl.setGraphicsEffect(self._speed_feedback_opacity)
        self._speed_feedback_fade_in = QPropertyAnimation(self._speed_feedback_opacity, b"opacity")
        self._speed_feedback_fade_in.setDuration(100)
        self._speed_feedback_fade_in.setStartValue(0.0)
        self._speed_feedback_fade_in.setEndValue(1.0)
        self._speed_feedback_fade_in.setEasingCurve(QEasingCurve.OutCubic)
        self._speed_feedback_fade_out = QPropertyAnimation(self._speed_feedback_opacity, b"opacity")
        self._speed_feedback_fade_out.setDuration(100)
        self._speed_feedback_fade_out.setStartValue(1.0)
        self._speed_feedback_fade_out.setEndValue(0.0)
        self._speed_feedback_fade_out.setEasingCurve(QEasingCurve.InCubic)
        self._speed_feedback_fade_out.finished.connect(self._speed_feedback_lbl.hide)
        self._speed_feedback_timer = QTimer(self)
        self._speed_feedback_timer.setSingleShot(True)
        self._speed_feedback_timer.timeout.connect(self._start_speed_feedback_fade_out)

        self._apply_root_margins()

    def _setup_shortcuts(self):
        def _mk(seq, handler):
            # Window context, not application: an application-wide Space fired
            # while a note or the settings dialog had focus, so typing a space
            # in a note triggered the reader and the note's own keyboard
            # controls never saw the key.
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(handler)
            return sc

        self._sc_space = _mk("Space", self._toggle_manual_run)
        self._sc_reset = _mk("Ctrl+R", self._reset_position)
        # The size keys have been in the entry-point docs since the beginning
        # but were never registered; the only way to resize was the menu.
        self._sc_bigger = _mk("Ctrl+=", self._font_increase)
        self._sc_smaller = _mk("Ctrl+-", self._font_decrease)
        self._sc_cp1 = _mk("Ctrl+1", lambda: self._set_checkpoint(1))
        self._sc_cp2 = _mk("Ctrl+2", lambda: self._set_checkpoint(2))
        self._sc_cp3 = _mk("Ctrl+3", lambda: self._set_checkpoint(3))
        self._sc_cp4 = _mk("Ctrl+4", lambda: self._set_checkpoint(4))
        self._sc_go1 = _mk("Ctrl+Shift+1", lambda: self._go_to_checkpoint(1))
        self._sc_go2 = _mk("Ctrl+Shift+2", lambda: self._go_to_checkpoint(2))
        self._sc_go3 = _mk("Ctrl+Shift+3", lambda: self._go_to_checkpoint(3))
        self._sc_go4 = _mk("Ctrl+Shift+4", lambda: self._go_to_checkpoint(4))
        self._sc_left = _mk("Left", lambda: self._adjust_speed_dir(-1))
        self._sc_right = _mk("Right", lambda: self._adjust_speed_dir(1))
        self._sc_esc = QShortcut(QKeySequence("Escape"), self)
        # Window context, not application: an application-wide Escape fired
        # from inside Settings and closed the reader behind the dialog.
        self._sc_esc.setContext(Qt.WindowShortcut)
        self._sc_esc.activated.connect(self._on_escape)

    # ═══════════════════════════════════════════════════════════════════
    #  Custom Painting (Floating Pill & Reactive Glow)
    # ═══════════════════════════════════════════════════════════════════

    def _update_window_mask(self):
        """
        Clear any mask so the window is transparent.
        We rely on paintEvent + WA_TranslucentBackground for smooth anti-aliased edges.
        """
        self.clearMask()

    def resizeEvent(self, event):
        # Capture the manual reader's top line before the text area takes its
        # new width, so reflowing can put the same words back under the eye.
        if (self._script_text and not self._edit_mode and not self._voice_mode
                and not self._reflow_timer.isActive()):
            self._reflow_pending_top = self._word_at_scroll_top()
        super().resizeEvent(event)
        # No mask needed - edges are smooth via paintEvent anti-aliasing
        self._layout_done_button()
        self._layout_chrome()
        # Line breaks depend on the viewport width: rebuild them once the
        # resize settles rather than on every animation frame.
        if self._script_text and not self._edit_mode:
            self._reflow_timer.start()

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self._card_width()
        h = self.height()

        # 1. Define Shape (Rounded Pill). The narrow card takes a smaller
        #    radius so the corners do not eat half of its width.
        radius = int(20 * self._scale) if self._compact_mode else self.CORNER_RADIUS
        path = QPainterPath()
        path.addRoundedRect(0, 0, w, h, radius, radius)

        # 2. Draw Background. The card stays a solid surface: the script runs
        #    the full height of the window behind floating controls, but the
        #    window itself is still a card, not a hole in the desktop.
        painter.setPen(Qt.NoPen)
        bg_color = QColor("#ffffff") if not self._dark_theme else QColor("#000000")
        # The card is a solid surface in both themes. Transparency belongs to
        # the floating controls (the idle recede); letting the desktop ghost
        # through the card put a second, moving layer behind the words and
        # hurt the script's contrast.
        bg_color.setAlphaF(1.0)
        painter.setBrush(bg_color)
        painter.drawPath(path)

        # 3. Material and status.
        # The old radial wash covered the lower half of the card in colour,
        # which on white read as a smudge under the words rather than depth.
        # What is left is a wash quiet enough to be felt and not read, plus a
        # short level line that carries the voice signal on its own.
        s = self._scale
        if not self._edit_mode:
            level = max(0.0, min(1.0, (self._current_glow_alpha - 28) / 140.0))
            glow_base = self.GLOW_COLOR if self._voice_mode else self.MANUAL_GLOW_COLOR

            ambient = QColor(glow_base)
            ambient.setAlpha(int((9 if self._dark_theme else 6) * (0.35 + 0.65 * level)))
            wash = QRadialGradient(QPointF(w / 2.0, float(h)), w / 2.1)
            wash.setColorAt(0.0, ambient)
            wash.setColorAt(1.0, QColor(0, 0, 0, 0))

            painter.save()
            painter.setClipPath(path)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(wash))
            painter.drawRect(0, 0, w, h)

            # The meter grows from the centre, so it reads as a level, not a
            # progress bar. At rest it is a short, quiet dash.
            if not self._peeked:
                inset = 30 * s
                track = max(1.0, w - 2 * inset)
                short = min(track, 26 * s)
                meter_w = short + (track - short) * level
                meter = QColor(glow_base)
                meter.setAlpha(int(105 + 115 * level))
                painter.setBrush(meter)
                painter.drawRoundedRect(
                    QRectF((w - meter_w) / 2.0, h - 7 * s,
                           meter_w, max(2.0, 2.0 * s)), 1.0, 1.0)
            painter.restore()

        # 3b. A resting pill still has to say something. Three bars at the
        #     centre read as a level meter, and they are the only element the
        #     peek state shows.
        if self._peeked:
            level = max(0.0, min(1.0, self._smoothed_volume_level * 4.0))
            color = QColor(self.GLOW_COLOR if self._voice_mode else self.MANUAL_GLOW_COLOR)
            color.setAlpha(235)
            bar_w = 3.0 * s
            gap = 3.6 * s
            # At rest the bars keep a short waveform shape rather than
            # collapsing to three dots, which read as "more options".
            tall = 9.0 * s + 11.0 * s * level
            total = bar_w * 3 + gap * 2
            x0 = (w - total) / 2.0
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            for index, factor in enumerate((0.68, 1.0, 0.68)):
                bar_h = tall * factor
                painter.drawRoundedRect(
                    QRectF(x0 + index * (bar_w + gap), (h - bar_h) / 2.0, bar_w, bar_h),
                    bar_w / 2.0, bar_w / 2.0)

        # 3c. Light catching the top edge of the material (dark only; on a
        #     white card there is no edge to catch).
        if self._dark_theme:
            painter.save()
            painter.setClipPath(path)
            painter.setPen(QPen(QColor(255, 255, 255, 16), 1.0))
            painter.drawLine(QPointF(radius, 1.0), QPointF(w - radius, 1.0))
            painter.restore()

        # 4. Hairline border, drawn last so the glow cannot wash it out.
        # Inset by half a pixel so a 1 px stroke lands on the pixel grid.
        painter.setBrush(Qt.NoBrush)
        border_pen = QPen(QColor("#2A2C2A") if self._dark_theme else QColor("#E2E2E2"))
        border_pen.setWidthF(1.0)
        painter.setPen(border_pen)
        inset = 0.5
        border_path = QPainterPath()
        border_path.addRoundedRect(
            inset, inset, w - 1.0, h - 1.0,
            radius - inset, radius - inset,
        )
        painter.drawPath(border_path)

    # ═══════════════════════════════════════════════════════════════════
    #  Animation & Scrolling Loop
    # ═══════════════════════════════════════════════════════════════════

    def _tick(self):
        """
        Handles both text scrolling AND glow animation smoothing.
        Runs at ~30 FPS.
        """
        # 1. Move the card and the glow. The spring clock only runs while
        #    something is actually in flight, so an idle reader costs no
        #    frames and the card does not repaint against a static screen.
        glow_target = 0.0 if self._edit_target else float(self._target_glow_alpha)
        if abs(self._glow.target - glow_target) > 0.5:
            self._glow.set_target(glow_target)

        # The rail is outside the card and follows the same geometry springs,
        # so it needs no clip of its own while the card collapses.

        # 2. Handle Scrolling
        if self._edit_mode:
            return
        bar = self._text_edit.verticalScrollBar()

        # Finish a multi-line catch-up one rendered line at a time.
        if self._voice_mode and self._step_reading_line():
            self._apply_highlight(self._display_word())

        if self._voice_mode:
            # Voice follow owns the scrollbar: a new line is a new target and
            # the spring carries the page there. A manual wheel gets a short
            # hold before the reader takes the scroll back.
            if (self._voice_target_scroll is not None
                    and time.monotonic() - self._last_manual_scroll_time >= 2.5
                    and not self._scroll_moving
                    and abs(self._scroll_spring.target - self._voice_target_scroll) > 0.5):
                self._retarget_scroll(self._voice_target_scroll)
            self._advance_scroll()
        elif self._manual_running:
            # Timed playback writes the scrollbar directly; make sure a glide
            # left over from voice mode cannot fight it.
            self._scroll_moving = False
            if bar.value() < bar.maximum():
                interval_sec = self._scroll_timer.interval() / 1000.0
                pixels_per_tick = (self._manual_wpm / 60.0) * 12.0 * interval_sec
                self._manual_scroll_accum += max(0.0, pixels_per_tick)
                step = int(self._manual_scroll_accum)
                if step > 0:
                    self._manual_scroll_accum -= step
                    bar.setValue(bar.value() + step)
            else:
                # End of script: stop the clock so replay is explicit.
                self._manual_running = False

    # ═══════════════════════════════════════════════════════════════════
    #  Audio Integration
    # ═══════════════════════════════════════════════════════════════════

    def _start_listening(self):
        self._stop_listening()
        self._stop_listening_sync()  # Wait for old worker before starting new one (only blocks when restarting)
        print("[Notch] Starting audio worker...", flush=True)
        self._audio_worker = AudioWorker(
            self._device_index,
            self._noise_gate,
            self._input_gain,
            self.MODEL_PATH,
        )
        self._audio_worker.speaking_signal.connect(self._on_speaking_changed)
        self._audio_worker.volume_level_signal.connect(self._on_volume_level)
        self._audio_worker.speech_text_signal.connect(self._on_speech_text)
        self._audio_worker.error_signal.connect(self._on_audio_error)
        self._audio_worker.start()
        print("[Notch] Audio worker started", flush=True)

    def _stop_listening(self):
        """Stop audio worker. Non-blocking: worker exits in background. Use _stop_listening_sync to wait."""
        worker = self._audio_worker
        self._audio_worker = None
        if not worker:
            self._is_speaking = False
            self._target_glow_alpha = 28
            self._smoothed_volume_level = 0.0
            return
        try:
            worker.blockSignals(True)
            worker.speaking_signal.disconnect()
            worker.volume_level_signal.disconnect()
            worker.speech_text_signal.disconnect()
            worker.error_signal.disconnect()
        except Exception:
            pass
        worker.stop()
        self._stopping_worker = worker  # so _start_listening can wait before starting new one
        self._is_speaking = False
        self._target_glow_alpha = 28
        self._smoothed_volume_level = 0.0

    def _stop_listening_sync(self):
        """Block until any stopping worker has exited. Use before starting new worker."""
        w = getattr(self, "_stopping_worker", None)
        if not w or not w.isRunning():
            self._stopping_worker = None
            return
        et = QElapsedTimer()
        et.start()
        while w.isRunning() and et.elapsed() < 3000:
            QApplication.processEvents()
            w.wait(50)
        self._stopping_worker = None

    def _log_install_paths(self):
        """When running as installed exe, write data path and existence to a log for debugging."""
        try:
            log_path = os.path.join(os.path.dirname(sys.executable), "notch_log.txt")
            exists = os.path.isdir(self.MODEL_PATH)
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(f"data_path={self.MODEL_PATH}\n")
                f.write(f"data_exists={exists}\n")
        except Exception:
            pass

    @pyqtSlot(str)
    def _on_audio_error(self, message: str):
        """Show mic/speech errors to the user (installed exe has no console)."""
        QMessageBox.warning(self, "Notch – Audio", message)

    @pyqtSlot(bool)
    def _on_speaking_changed(self, is_speaking):
        self._is_speaking = is_speaking

    @pyqtSlot(float)
    def _on_volume_level(self, level):
        """
        Receives volume level (0.0 to 1.0 approx) from audio worker.
        Smooth with EMA so the glow bar isn't jittery, then update target.
        """
        if self._voice_mode:
            # Exponential moving average: smooth over many updates
            self._smoothed_volume_level = self._smoothed_volume_level * 0.75 + level * 0.25
            intensity = min(1.0, self._smoothed_volume_level * 5.0)
            self._target_glow_alpha = 28 + int(140 * intensity)
            # The peek pill animates from this level directly; the glow spring
            # is parked while the reader is put away.
            if self._peeked:
                self.update()

    def _toggle_mic_mute(self):
        self._muted = not self._muted
        self._mic_btn.set_muted(self._muted)
        if self._audio_worker:
            self._audio_worker.set_muted(self._muted)  # worker skips emitting when muted; resets recognizer on unmute
        self._mic_btn.setToolTip("Unmute mic" if self._muted else "Mute mic (stop voice from advancing script)")
        self._mic_btn.setAccessibleName("Unmute microphone" if self._muted else "Mute microphone")

    # Filler words often recognised from ambient noise when nothing is said
    _SPEECH_IGNORE_WORDS = frozenset({"huh", "uh", "um", "hmm", "hm", "eh", "ah"})

    # Voice commands: "Notch X" — checked case-insensitive, final results only
    # Include "not" variant (common misrecognition of "notch")
    _VOICE_COMMANDS = {
        "notch restart": lambda s: s._reset_position(),
        "not restart": lambda s: s._reset_position(),
        "notch expand": lambda s: s._toggle_edit_mode(),
        "not expand": lambda s: s._toggle_edit_mode(),
        "notch mute": lambda s: s._toggle_mic_mute(),
        "not mute": lambda s: s._toggle_mic_mute(),
        "notch close": lambda s: s.close(),
        "not close": lambda s: s.close(),
    }

    @pyqtSlot(str, bool)
    def _on_speech_text(self, text: str, is_final: bool):
        if self._muted:
            return
        txt = " ".join(text.strip().lower().split())  # normalize spaces
        if txt in self._SPEECH_IGNORE_WORDS and not self._is_speaking:
            return  # Skip filler false positives when not actively speaking
        if text.strip():
            print(f"[Notch speech] {'(final)' if is_final else '(partial)'}: {text!r}", flush=True)

        # Voice commands (final only, work even without script)
        if is_final and txt in self._VOICE_COMMANDS:
            self._VOICE_COMMANDS[txt](self)
            return

        if not self._script_text:
            return

        if is_final:
            self._apply_confirmed_speech(text)
        else:
            self._apply_partial_speech(text)

    # ═══════════════════════════════════════════════════════════════════
    #  Voice follow (line based)
    # ═══════════════════════════════════════════════════════════════════

    def _align_speech(self, text: str, start_word: int, end_word: int,
                      resync_skip: Optional[int] = None,
                      reanchor: bool = False) -> Optional[int]:
        """Furthest script word reached by a recognised phrase, or None."""
        aligned = self._matching.align_forward(
            text,
            start_word,
            end_word,
            min_score=self.WORD_MATCH_SCORE,
            max_skip=self.WORD_SKIP_TOLERANCE,
            resync_skip=resync_skip,
            reanchor=reanchor,
        )
        return aligned.last_word if aligned is not None else None

    def _display_word(self) -> int:
        """How far to shade the script, never past the line being read.

        The tentative word counts as well: partial results drive the live
        highlight, and re-applying the format for any reason must not pull
        the shading back to the last confirmed word and flicker the boundary.
        """
        position = max(self._confirmed_word, self._partial_word)
        if not self._line_spans or position < 0:
            return position
        line = self._line_spans[self._current_line]
        return min(position, line.last_word)

    def _apply_partial_speech(self, text: str):
        """Shade words as they are heard, and step at the line's last word.

        A partial is not confirmation, so it may only move the highlight
        inside the line on screen. The exception is the line's last word: once
        a close match for it arrives, that word has been said and waiting for
        Vosk's final would leave the scroll sitting through the pause at the
        end of the sentence. Confirming just that word keeps the step at the
        boundary and never moves past anything not yet spoken.
        """
        if not self._line_spans:
            return
        line = self._line_spans[self._current_line]
        start = max(line.first_word, self._confirmed_word + 1)
        if start > line.last_word:
            return  # line already fully spoken; waiting for a final result
        last = self._align_speech(
            text, start, line.last_word + 1,
            resync_skip=self.RESYNC_SKIP_TOLERANCE)
        if last is None or last <= self._confirmed_word:
            return
        self._partial_word = last
        if last >= line.last_word:
            spoken = text.split()[-1] if text.split() else ""
            if (self._matching.word_score(spoken, line.last_word)
                    >= self.PARTIAL_FINISH_SCORE):
                self._confirmed_word = line.last_word
                self._partial_word = -1
                self._stalled_finals = 0
                self._matching.set_position(self._confirmed_word)
                self._update_voice_speed(self._confirmed_word, False)
                moved = self._step_reading_line(force=True)
                self._apply_highlight(self._display_word())
                if moved:
                    self._speech_active_until = time.monotonic() + 1.0
                return
        self._apply_highlight(min(last, line.last_word))

    def _apply_confirmed_speech(self, text: str):
        """Confirm words from a final result, then step lines forward.

        A line is only complete when its last word is among the confirmed
        words. If one final covers several lines, they are stepped through
        one at a time; the view is only re-anchored when the reader actually
        changes line.

        A final that matches nothing where the reader stands may still be
        recovery: once two finals in a row have confirmed nothing, the reader
        has lost its place, and if the phrase contains a strong run of words
        that lands further on it moves there instead of stalling for the rest
        of the session. Partials never do this - they are tentative, and a
        guess must not move the pointer.
        """
        if not self._line_spans:
            self._refresh_lines()
        if not self._line_spans:
            return

        # Match from the first word that has not been confirmed yet. Words at
        # the start of a final that were already confirmed are treated as
        # noise, which keeps repeated words from matching behind the cursor.
        start = max(0, self._confirmed_word + 1)
        last = self._align_speech(
            text, start, self._matching.word_count,
            resync_skip=self.RESYNC_SKIP_TOLERANCE,
            reanchor=self._stalled_finals >= 1)
        if last is None or last <= self._confirmed_word:
            # The final still replaces any tentative shading from partials.
            self._stalled_finals += 1
            self._partial_word = -1
            self._apply_highlight(self._display_word())
            return

        self._stalled_finals = 0
        self._confirmed_word = last
        self._partial_word = -1
        self._matching.set_position(last)
        self._update_voice_speed(last, True)

        # Step immediately when the line just finished, then let _tick pace
        # any further lines this result also confirmed.
        moved = self._step_reading_line(force=True)
        self._apply_highlight(self._display_word())
        if moved:
            self._speech_active_until = time.monotonic() + 1.0

    def _reading_line_limit(self) -> int:
        """Highest rendered line the reader may stand on.

        That is the line after the last fully confirmed one. A line whose
        last word has not been confirmed is never behind the limit, so the
        reader cannot step past a line that is still being spoken.
        """
        if not self._line_spans or self._confirmed_word < 0:
            return 0
        index = self._line_index_for_word(self._confirmed_word)
        if self._line_spans[index].last_word <= self._confirmed_word:
            limit = index + 1
        else:
            limit = index
        return max(0, min(limit, len(self._line_spans) - 1))

    def _step_reading_line(self, force: bool = False) -> bool:
        """Move the reader forward one rendered line, at most one per step.

        Returns True when the line changed. A confirmed final steps straight
        away; lines confirmed by the same result follow on the scroll timer so
        the view glides a line at a time instead of jumping.
        """
        if not self._line_spans:
            return False
        if self._current_line >= len(self._line_spans) - 1:
            return False
        if self._current_line >= self._reading_line_limit():
            return False
        now = time.monotonic()
        if not force and now < self._next_line_step_at:
            return False
        self._current_line += 1
        self._next_line_step_at = now + self.LINE_STEP_INTERVAL
        self._scroll_to_reading_line(self._current_line)
        return True

    # ═══════════════════════════════════════════════════════════════════
    #  Context Menu
    # ═══════════════════════════════════════════════════════════════════

    def _build_menu(self) -> QMenu:
        """The menu is a surface, not a stack of system widgets.

        Everything follows the card: same material, same type ramp, same
        hairline. The mode is one segmented control instead of three separate
        readings of the same state, the font size shows its current value, and
        appearance and actions are grouped by what they change.
        """
        dark = self._dark_theme
        s = self._scale

        def px(value: float) -> int:
            return int(round(value * s))

        menu = RoundedMenu(self, dark=dark)
        menu._radius = px(14)
        menu.setFixedWidth(px(236))
        menu.setAccessibleName("Reader menu")

        fg = "#EDEDEF" if dark else "#151517"
        fg2 = "#9C9CA2" if dark else "#6E6E73"
        line = "#343436" if dark else "#E4E4E6"
        hover = "#2C2C2E" if dark else "#ECECEE"
        track = "#2A2A2C" if dark else "#EDEDEF"
        thumb = "#48484A" if dark else "#FFFFFF"
        panel = "#1E1E1E" if dark else "#F7F7F8"
        # The slider needs a ladder the shared tokens do not provide: a rail
        # that is quiet but still a rail, a mid fill, and a knob above both.
        # On the old colours the remaining track was 1.16:1 against the panel
        # (all but invisible) and the knob sat darker than its own fill, so
        # the control read as a bar with a hole punched in it.
        rail = "#3E3E41" if dark else "#D8D8DD"
        fill = "#6E6E73"

        # ── One control language ─────────────────────────────────────
        # The panel had five button dialects: filled and borderless, bare
        # text, two different radii, three heights. Now a control is either a
        # field (a container that holds a choice: the mode pair, the size
        # stepper, the typeface) or a plate (a lone action), and both wear the
        # same hairline, radius and press materials. The hairline is what
        # separates a control from the panel it floats on.
        field_style = f"background: {track}; border: 1px solid {line};"
        field_pad = px(1)          # the border's thickness comes back here
        field_height = px(28)      # one height for every control in the panel
        control_radius = px(8)     # and one radius
        segment_radius = control_radius - px(2)
        # One vertical rhythm: every row keeps the same air above and below
        # it, so the panel reads as spaced sections instead of a stack.
        row_gap = px(5)

        menu.setStyleSheet(f"""
            QMenu {{
                background: transparent;
                border: none;
                padding: {px(8)}px;
            }}
            QMenu::item {{
                color: {fg};
                padding: {px(7)}px {px(10)}px;
                border-radius: {px(8)}px;
                font-family: "Segoe UI", system-ui, sans-serif;
                font-size: {px(13)}px;
                margin: 1px 0;
            }}
            QMenu::item:selected {{ background: {hover}; }}
            QMenu::separator {{
                height: 1px;
                background: {line};
                margin: {px(4)}px {px(8)}px;
            }}
            QToolTip {{
                background: {panel};
                color: {fg};
                border: 1px solid {line};
                padding: 4px 7px;
            }}
        """)

        def label(text, size=13, color=fg, weight=400):
            w = QLabel(text)
            w.setStyleSheet(
                f"color: {color}; background: transparent;"
                f" font-family: 'Segoe UI', system-ui, sans-serif;"
                f" font-size: {px(size)}px; font-weight: {weight};"
            )
            return w

        def add_widget(inner, top=0, bottom=0):
            holder = QWidget(menu)
            lay = QHBoxLayout(holder)
            lay.setContentsMargins(0, px(top), 0, px(bottom))
            lay.setSpacing(0)
            lay.addWidget(inner)
            action = QWidgetAction(menu)
            action.setDefaultWidget(holder)
            menu.addAction(action)
            return action

        # ── Mode: one control, one state ─────────────────────────────
        segmented = QWidget(menu)
        segmented.setStyleSheet(
            f"{field_style} border-radius: {control_radius}px;")
        segmented_layout = QHBoxLayout(segmented)
        segmented_layout.setContentsMargins(field_pad, field_pad,
                                            field_pad, field_pad)
        segmented_layout.setSpacing(px(2))
        mode_group = QButtonGroup(menu)
        mode_group.setExclusive(True)

        def segment(text):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            # A stylesheet border is painted inside the widget, so it does
            # not add to a layout-driven height: the inside gets the padding.
            btn.setFixedHeight(field_height - 2 * field_pad)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {fg2};
                    border: none;
                    border-radius: {segment_radius}px;
                    padding: 0 {px(6)}px;
                    font-family: "Segoe UI", system-ui, sans-serif;
                    font-size: {px(12)}px;
                    font-weight: 500;
                }}
                QPushButton:hover {{ color: {fg}; }}
                QPushButton[picked="true"]:!checked {{
                    background: {hover};
                    color: {fg};
                }}
                QPushButton:checked {{ background: {thumb}; color: {fg}; }}
            """)
            mode_group.addButton(btn)
            segmented_layout.addWidget(btn)
            return btn

        voice_btn = segment("Voice")
        manual_btn = segment("Manual")
        voice_btn.setChecked(self._voice_mode)
        manual_btn.setChecked(not self._voice_mode)
        voice_btn.setToolTip("Follow your speech")
        manual_btn.setToolTip("Scroll at a fixed pace")
        voice_btn.setAccessibleName("Voice mode")
        manual_btn.setAccessibleName("Manual mode")
        menu._voice_btn = voice_btn
        menu._manual_btn = manual_btn

        def choose_mode(voice: bool):
            if voice != self._voice_mode:
                self._toggle_mode(voice)
            self._refresh_menu_mode_display(menu)

        voice_btn.clicked.connect(lambda: choose_mode(True))
        manual_btn.clicked.connect(lambda: choose_mode(False))

        # ── Status: what the reader is doing right now ───────────────
        status_row = QWidget(menu)
        status_layout = QHBoxLayout(status_row)
        status_layout.setContentsMargins(px(10), 0, px(10), 0)
        status_layout.setSpacing(px(7))
        dot = QLabel()
        dot.setFixedSize(px(6), px(6))
        status_layout.addWidget(dot)
        status = label("", size=11, color=fg2)
        status_layout.addWidget(status, 1)
        menu._status_dot = dot
        menu._status_label = status

        # The mode and what it is doing are one section: the status line is
        # text, so giving it its own row would make arrow keys stop twice on
        # a line that has nothing to press.
        mode_section = QWidget(menu)
        mode_layout = QVBoxLayout(mode_section)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(px(7))
        mode_layout.addWidget(segmented)
        mode_layout.addWidget(status_row)
        mode_action = add_widget(mode_section, top=2, bottom=row_gap)

        # ── Speed (manual mode only) ─────────────────────────────────
        speed_row = QWidget(menu)
        speed_layout = QHBoxLayout(speed_row)
        # Right inset matches the other value rows: their controls close on
        # the same edge as the stepper and the typeface buttons.
        speed_layout.setContentsMargins(px(10), 0, px(8), 0)
        speed_layout.setSpacing(px(8))
        speed_layout.addWidget(label("Speed", size=13))
        slider = QSlider(Qt.Horizontal)
        slider.setRange(1, 400)
        # Two single steps per arrow press, matching the reader's own keys.
        slider.setSingleStep(5)
        slider.setValue(self._manual_wpm)
        slider.setAccessibleName("Scroll speed")
        # The knob is a circle only if its box is square. Qt adds the border
        # to the handle's height but not to its width, so the old 1 px ring
        # drew an oval; the knob now carries no border and is sized exactly.
        # The slider also has to grow with the interface: Qt sizes it from the
        # style's fixed metrics, so at 1.5x and 2x the knob was cut off top
        # and bottom. Its height follows the row instead.
        slider.setFixedHeight(px(22))
        slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                height: {px(4)}px;
                background: {rail};
                border-radius: {px(2)}px;
            }}
            QSlider::sub-page:horizontal {{
                background: {fill};
                border-radius: {px(2)}px;
            }}
            QSlider::handle:horizontal {{
                width: {px(14)}px;
                height: {px(14)}px;
                margin: {px(-5)}px 0;
                background: {fg};
                border-radius: {px(7)}px;
            }}
        """)
        speed_value = label(str(self._manual_wpm), size=11, color=fg2)
        speed_value.setFixedWidth(px(30))
        speed_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        speed_layout.addWidget(slider, 1)
        speed_layout.addWidget(speed_value)
        menu._speed_slider = slider
        menu._speed_value = speed_value

        def on_speed(value):
            speed_value.setText(str(value))
            self._set_speed(value, "manual")
            status.setText(self._mode_status_text())
            self._schedule_save()

        slider.valueChanged.connect(on_speed)
        speed_action = add_widget(speed_row, top=row_gap, bottom=row_gap)
        menu._speed_action = speed_action

        menu.addSeparator()

        # ── Text: size shows its value, typeface shows its own face ──
        size_row = QWidget(menu)
        size_layout = QHBoxLayout(size_row)
        size_layout.setContentsMargins(px(10), 0, px(8), 0)
        size_layout.setSpacing(px(8))
        size_layout.addWidget(label("Font size", size=13))
        size_layout.addStretch(1)

        stepper = QWidget(menu)
        stepper.setStyleSheet(
            f"{field_style} border-radius: {control_radius}px;")
        stepper_layout = QHBoxLayout(stepper)
        stepper_layout.setContentsMargins(field_pad, field_pad,
                                          field_pad, field_pad)
        stepper_layout.setSpacing(0)
        size_value = label(str(self._font_size), size=11, color=fg)
        size_value.setAlignment(Qt.AlignCenter)
        size_value.setFixedWidth(px(24))

        def step_button(text, handler, tip):
            btn = QPushButton(text)
            btn.setCursor(Qt.PointingHandCursor)
            side = field_height - 2 * field_pad
            btn.setFixedSize(side, side)
            btn.setToolTip(tip)
            btn.setAccessibleName(tip)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {fg};
                    border: none;
                    border-radius: {segment_radius}px;
                    font-size: {px(14)}px;
                }}
                QPushButton:hover, QPushButton[picked="true"] {{
                    background: {hover};
                }}
                QPushButton:pressed {{ background: {thumb}; }}
            """)
            btn.clicked.connect(handler)
            stepper_layout.addWidget(btn)
            return btn

        def bump_size(direction: int):
            if direction < 0:
                self._font_decrease()
            else:
                self._font_increase()
            size_value.setText(str(self._font_size))

        step_down = step_button("−", lambda: bump_size(-1), "Smaller script")
        stepper_layout.addWidget(size_value)
        step_up = step_button("+", lambda: bump_size(1), "Larger script")
        size_layout.addWidget(stepper)
        size_action = add_widget(size_row, top=row_gap, bottom=row_gap)

        font_row = QWidget(menu)
        font_layout = QHBoxLayout(font_row)
        font_layout.setContentsMargins(px(10), 0, px(8), 0)
        font_layout.setSpacing(px(8))
        font_layout.addWidget(label("Typeface", size=13))
        font_layout.addStretch(1)
        family_btn = QPushButton()
        family_btn.setCursor(Qt.PointingHandCursor)
        family_btn.setFixedHeight(field_height)
        family_btn.setMinimumWidth(px(104))
        family_btn.setToolTip("Click to try the next typeface")
        family_btn.setAccessibleName("Typeface")

        def style_family_btn():
            display, family = next(
                ((d, f) for d, f in self.FONT_OPTIONS if f == self._font_family),
                self.FONT_OPTIONS[0],
            )
            family_btn.setText(f"{display}  ▾")
            family_btn.setStyleSheet(f"""
                QPushButton {{
                    {field_style}
                    color: {fg};
                    border-radius: {control_radius}px;
                    padding: 0 {px(8)}px;
                    text-align: left;
                    font-family: "{family}", "Segoe UI", sans-serif;
                    font-size: {px(12)}px;
                }}
                QPushButton:hover, QPushButton[picked="true"] {{
                    background: {thumb};
                }}
            """)

        def cycle_family():
            families = [f for _, f in self.FONT_OPTIONS]
            try:
                index = families.index(self._font_family)
            except ValueError:
                index = 0
            self._font_family = families[(index + 1) % len(families)]
            self._apply_typography()
            style_family_btn()
            self._schedule_save()

        family_btn.clicked.connect(cycle_family)
        style_family_btn()
        font_layout.addWidget(family_btn)
        font_action = add_widget(font_row, top=row_gap, bottom=row_gap)

        # ── Appearance ───────────────────────────────────────────────
        theme_row = QWidget(menu)
        theme_layout = QHBoxLayout(theme_row)
        theme_layout.setContentsMargins(px(10), 0, px(8), 0)
        theme_layout.setSpacing(px(8))
        theme_layout.addWidget(label("Appearance", size=13))
        theme_layout.addStretch(1)
        theme_seg = QWidget(menu)
        theme_seg.setStyleSheet(
            f"{field_style} border-radius: {control_radius}px;")
        theme_seg_layout = QHBoxLayout(theme_seg)
        theme_seg_layout.setContentsMargins(field_pad, field_pad,
                                            field_pad, field_pad)
        theme_seg_layout.setSpacing(px(2))
        theme_group = QButtonGroup(menu)
        theme_group.setExclusive(True)

        def theme_button(text):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedSize(px(46), field_height - 2 * field_pad)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {fg2};
                    border: none;
                    border-radius: {segment_radius}px;
                    font-family: "Segoe UI", system-ui, sans-serif;
                    font-size: {px(11)}px;
                }}
                QPushButton:hover {{ color: {fg}; }}
                QPushButton[picked="true"]:!checked {{
                    background: {hover};
                    color: {fg};
                }}
                QPushButton:checked {{ background: {thumb}; color: {fg}; }}
            """)
            theme_group.addButton(btn)
            theme_seg_layout.addWidget(btn)
            return btn

        dark_btn = theme_button("Dark")
        light_btn = theme_button("Light")
        dark_btn.setAccessibleName("Dark appearance")
        light_btn.setAccessibleName("Light appearance")
        dark_btn.setChecked(self._dark_theme)
        light_btn.setChecked(not self._dark_theme)

        def choose_theme(dark_theme: bool):
            if dark_theme == self._dark_theme:
                return
            self._dark_theme = dark_theme
            self._apply_read_mode_style()
            self.update()
            for note in self._notes:
                note.apply_theme(self._dark_theme)
            self._apply_control_theme()
            self._apply_highlight(self._display_word())
            self._schedule_save()
            # The popup is a material too: close it rather than leave a dark
            # panel floating over a card that just turned white.
            menu.close()

        dark_btn.clicked.connect(lambda: choose_theme(True))
        light_btn.clicked.connect(lambda: choose_theme(False))
        theme_layout.addWidget(theme_seg)
        theme_action = add_widget(theme_row, top=row_gap, bottom=row_gap)

        menu.addSeparator()

        menu.addAction("Load Script…", self._load_file)

        # ── Help, Setup and Quit: one row, not three ─────────────────
        # Three stacked items spent three rows of panel height plus a
        # separator on three short words. One row of equal plates keeps the
        # item's material and order and gives both back.
        action_row = QWidget(menu)
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(px(6))

        def action_button(text, handler):
            btn = QPushButton(text, action_row)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(field_height)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {fg};
                    border: 1px solid {line};
                    border-radius: {control_radius}px;
                    font-family: "Segoe UI", system-ui, sans-serif;
                    font-size: {px(13)}px;
                }}
                QPushButton:hover, QPushButton[picked="true"] {{
                    background: {hover};
                }}
                QPushButton:pressed {{ background: {thumb}; }}
            """)

            def run():
                # A widget action does not dismiss the popup the way a menu
                # item does, and Help and Setup open on top of it.
                menu.close()
                handler()

            btn.clicked.connect(run)
            action_layout.addWidget(btn, 1)
            return btn

        menu._help_btn = action_button("Help", self._show_help)
        menu._setup_btn = action_button("Setup…", self._open_settings)
        menu._quit_btn = action_button("Quit", self.close)
        for plate, name in ((menu._help_btn, "Help"),
                            (menu._setup_btn, "Setup"),
                            (menu._quit_btn, "Quit")):
            plate.setAccessibleName(name)
        menu._action_row = action_row
        row_action = add_widget(action_row, top=row_gap)

        # ── Keyboard: every widget row answers Left/Right/Return ─────
        # Without this a widget row is a place the menu's own navigation stops
        # and Return silently closes the panel instead of running anything.
        rows = MenuRowNav(menu)
        rows.row(mode_action, [voice_btn, manual_btn])
        rows.row(speed_action, slider=slider)
        rows.row(size_action, [step_down, step_up])
        rows.row(font_action, [family_btn])
        rows.row(theme_action, [dark_btn, light_btn])
        rows.row(row_action, [menu._help_btn, menu._setup_btn, menu._quit_btn])
        menu._rows = rows

        self._refresh_menu_mode_display(menu)
        return menu

    def _fit_menu_to_screen(self, menu: QMenu, pos: QPoint) -> QPoint:
        """Keep the whole panel on the usable part of the screen.

        Qt places a popup against the full screen, taskbar included, so a
        right click near the bottom edge used to put the last row - the one
        with Help, Setup and Quit - underneath it.
        """
        screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        size = menu.sizeHint()
        x = min(max(pos.x(), area.left()), area.right() - size.width() + 1)
        y = min(max(pos.y(), area.top()), area.bottom() - size.height() + 1)
        return QPoint(int(x), int(y))

    def _present_menu(self, menu: QMenu, pos: QPoint):
        """Show the menu as a material arriving, not as a system popup."""
        menu.adjustSize()
        if system_allows_motion():
            menu.setWindowOpacity(0.0)

            def fade_in():
                anim = QPropertyAnimation(menu, b"windowOpacity", menu)
                anim.setDuration(120)
                anim.setStartValue(0.0)
                anim.setEndValue(1.0)
                anim.setEasingCurve(QEasingCurve.OutCubic)
                menu._fade_anim = anim
                anim.start()

            QTimer.singleShot(0, fade_in)
        menu.exec_(self._fit_menu_to_screen(menu, pos))

    def _show_menu(self, global_pos):
        if self._edit_mode:
            return
        menu = self._build_menu()
        try:
            self._present_menu(menu, global_pos)
        finally:
            # The menu is parented to the overlay, so it outlives exec_().
            # Without this every right-click leaves a full menu widget tree
            # behind (about 60 objects each) for the life of the process.
            menu.deleteLater()

    def contextMenuEvent(self, event):
        self._show_menu(event.globalPos())

    # ═══════════════════════════════════════════════════════════════════
    #  Standard Logic (Load, Save, Drag)
    # ═══════════════════════════════════════════════════════════════════

    def enterEvent(self, event):
        self._wake_chrome()
        super().enterEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if self._peeked:
            # A resting pill: clicking it brings the reader back.
            self._restore_from_peek()
            return
        self._wake_chrome()
        if event.button() == Qt.RightButton:
            self._show_menu(event.globalPos())
            return
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        self._wake_chrome()
        if self._drag_pos and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPos() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._drag_pos is not None:
            self._drag_pos = None
            # Persist the new spot once the drag settles.
            self._schedule_save()

    def dragEnterEvent(self, event: QDragEnterEvent):
        urls = event.mimeData().urls()
        if any(url.isLocalFile()
               and url.toLocalFile().lower().endswith(self.SCRIPT_SUFFIXES)
               for url in urls):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(self.SCRIPT_SUFFIXES):
                self._load_script_from_file(path)
                break

    def _load_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Script", "", "Text Files (*.txt)")
        if path:
            self._load_script_from_file(path)

    @staticmethod
    def _decode_script(raw: bytes) -> str:
        """Decode a script without losing a Windows-native file.

        Scripts written by Notepad on older Windows builds are cp1252, not
        UTF-8, and a strict UTF-8 read failed for the whole file. BOM-aware
        UTF-8/UTF-16 come first, then the two Windows encodings; latin-1 is
        the final lossless fallback because it maps every byte.
        """
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            return raw.decode("utf-16")
        for encoding in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def _report_import_error(self, path: str, error: Exception):
        """Tell the reader why a script did not open. Silence is a bug."""
        name = os.path.basename(path) or path
        print(f"[Notch] Could not open {path}: {error}", flush=True)
        QMessageBox.warning(
            self, "Notch – Open Script",
            f"Could not open “{name}”.\n\n{error}\n\n"
            "Notch reads plain text (.txt) scripts.",
        )

    def _load_script_from_file(self, path):
        """Load a script, reporting read failures instead of swallowing them."""
        try:
            with open(path, "rb") as handle:
                text = self._decode_script(handle.read())
            print(f"[Notch] Script loaded: {path}", flush=True)
            self._text_edit.setPlainText(text)
            # Replacing the text resets every block's format; the spacing key
            # must not pretend the new document is already typeset.
            self._spaced_key = None
            self._text_edit.setAlignment(Qt.AlignCenter) 
            self._script_text = text
            if not text.strip():
                # An empty file is a clear, exactly like emptying the editor.
                self._discard_script_notes()
            self._matching.load_script(text)
            self._highlight_key = None  # a new document: old shading is stale
            self._focus_span = None
            self._last_match_time = None
            self._last_match_pos = -1
            self._voice_wpm = 60
            self._manual_scroll_accum = 0.0
            self._voice_scroll_accum = 0.0
            self._voice_target_scroll = None
            self._checkpoints = {}
            self._line_spans = []
            self._current_line = 0
            self._confirmed_word = -1
            self._partial_word = -1
            self._apply_highlight(-1)
            self._refresh_lines()
            self._text_edit.moveCursor(QTextCursor.Start)
            self._text_edit.verticalScrollBar().setValue(0)
            if self._voice_mode:
                self._scroll_to_reading_line(0)
                self._start_listening()
            else:
                # Manual mode reads from the top of the column, not the
                # voice-follow anchor, so the script starts at the first line.
                self._scroll_word_to_top(0)
            return True
        except (OSError, ValueError) as error:
            self._report_import_error(path, error)
            return False

    def _toggle_mode(self, checked):
        self._voice_mode = checked
        # The card's indicator colour and the meter belong to the mode.
        self.update()
        self._schedule_save()
        if self._voice_mode:
            self._start_listening()
        else:
            self._stop_listening()
            self._target_glow_alpha = 28  # Dim glow in manual mode
            self._smoothed_volume_level = 0.0
            self._manual_running = False
            self._manual_scroll_accum = 0.0
            self._voice_scroll_accum = 0.0
            self._voice_target_scroll = None
            self._sync_scroll_spring()

    def _mode_status_text(self) -> str:
        """One line of state, without repeating the number in the row below."""
        if self._voice_mode:
            return "Following your voice"
        return "Timed scroll · running" if self._manual_running else "Timed scroll · paused"

    def _refresh_menu_mode_display(self, menu):
        """Keep an open menu honest about the mode it is showing."""
        accent = "#0A84FF" if self._voice_mode else "#30D158"
        radius = max(2, int(3 * self._scale))
        if hasattr(menu, "_status_dot"):
            menu._status_dot.setStyleSheet(
                f"background: {accent}; border-radius: {radius}px;")
        if hasattr(menu, "_status_label"):
            menu._status_label.setText(self._mode_status_text())
        for btn, checked in (
            (getattr(menu, "_voice_btn", None), self._voice_mode),
            (getattr(menu, "_manual_btn", None), not self._voice_mode),
        ):
            if btn is not None:
                btn.blockSignals(True)
                btn.setChecked(checked)
                btn.blockSignals(False)
        # A hidden QAction leaves the menu layout instead of leaving a gap.
        # Its widget does not follow: hiding an action while the menu is open
        # only unlists it, so the row would stay painted where the layout
        # used to put it - a speed slider floating over the rows below it in
        # voice mode. The widget has to be hidden with it.
        if hasattr(menu, "_speed_action"):
            shown = not self._voice_mode
            menu._speed_action.setVisible(shown)
            row = menu._speed_action.defaultWidget()
            if row is not None:
                row.setVisible(shown)
        if hasattr(menu, "_speed_slider"):
            menu._speed_slider.blockSignals(True)
            menu._speed_slider.setValue(self._manual_wpm)
            menu._speed_slider.blockSignals(False)
            menu._speed_value.setText(str(self._manual_wpm))

    def _set_top_bar_icon_size(self, glyph: int):
        """Icon design size, and a round hit box that still fits the rail."""
        hit = max(int(glyph + int(10 * self._scale)), int(22 * self._scale))
        for btn in (self._close_btn, self._note_btn, self._mic_btn,
                    self._compact_btn):
            if btn is None:
                continue
            if hasattr(btn, "set_glyph_size"):
                btn.set_glyph_size(glyph)
            btn.setFixedSize(hit, hit)

    # ═══════════════════════════════════════════════════════════════════
    #  Chrome, theme and put-away
    # ═══════════════════════════════════════════════════════════════════

    def _apply_control_theme(self):
        """Keep every custom-painted control on the card's theme."""
        dark = self._dark_theme
        for btn in (self._close_btn, self._note_btn, self._mic_btn,
                    self._compact_btn, getattr(self, "_done_btn", None)):
            if btn is not None and hasattr(btn, "set_dark"):
                btn.set_dark(dark)

    def _wake_chrome(self):
        """Full chrome, and restart the countdown to it receding."""
        if not hasattr(self, "_chrome_fade"):
            return
        if self._idle_timer.isActive():
            self._idle_timer.start()
        else:
            self._idle_timer.start()
        if self._chrome_opacity.opacity() < 1.0:
            self._fade_chrome(1.0)

    def _recede_chrome(self):
        """Let the toolbar dissolve while the reader is just reading."""
        if self._edit_target or self._peeked:
            return
        hovering = self._chrome.underMouse() or any(
            getattr(btn, "_hover", False) for btn in
            (self._close_btn, self._note_btn, self._mic_btn,
             self._compact_btn)
        )
        if hovering:
            # Pointer is resting on the controls: do not pull them away.
            self._idle_timer.start()
            return
        self._fade_chrome(0.0)

    def _fade_chrome(self, target: float):
        self._chrome_fade.stop()
        self._chrome_fade.setStartValue(self._chrome_opacity.opacity())
        self._chrome_fade.setEndValue(target)
        self._chrome_fade.setDuration(170 if target < 1.0 else 150)
        if not system_allows_motion():
            self._chrome_opacity.setOpacity(target)
            return
        self._chrome_fade.start()

    def _on_escape(self):
        """Escape backs out one level at a time; it is not a quit key."""
        if self._peeked:
            self._restore_from_peek()
        elif self._edit_target:
            self._toggle_edit_mode()
        elif self._voice_mode is False and self._manual_running:
            self._manual_running = False
        else:
            self.close()

    def _minimize(self):
        """Put the card away without quitting it.

        The minus used to close the app, which made the most familiar
        control in the window the most destructive one. Quitting lives in
        the menu; this shrinks to a resting pill that comes back on click.
        """
        if self._edit_target:
            self._toggle_edit_mode()
            return
        if self._peeked:
            return
        s = self._scale
        self._peek_restore = QRect(self.geometry())
        self._peeked = True
        self._motion_intent = "peek"
        self._text_edit.hide()
        self._chrome.hide()
        self._fade_chrome(1.0)
        self._glow.set_target(20.0, carry_velocity=False)
        # The reader's minimum size is its normal size; a resting pill is
        # smaller than that, so the floor has to come off first.
        self.setMinimumSize(1, 1)
        card_w, h = int(132 * s), int(34 * s)
        w = card_w + self.RAIL_W
        # The rail is hidden while the card rests, but the window still owns
        # its strip; centre the visible pill, not the transparent remainder.
        x = self.x() + (self._card_width() - card_w) // 2
        self._animate_geometry_to(QRect(x, self.y(), w, h), response=0.30)

    def _restore_from_peek(self):
        if not self._peeked:
            return
        self._peeked = False
        self._motion_intent = "restore"
        self._chrome.show()
        self._text_edit.show()
        self._glow.set_target(float(self._target_glow_alpha), carry_velocity=False)
        target = self._peek_restore or QRect(self.x(), self.y(), self.DEFAULT_W, self.DEFAULT_H)
        self._peek_restore = None
        self.setMinimumSize(self.MIN_W, self.MIN_H)
        self._animate_geometry_to(QRect(target), response=0.30)
        self._wake_chrome()

    def _reserve_editor_footer(self, reserve: bool):
        """Keep the last line of the script out from under the Done button.

        The button floats inside the editor's bottom-right corner, so without
        a reserved strip a long script scrolls its final line underneath it.
        In the reader the same margin is the anchor room below the last line,
        so the final lines can sit on the reading line like every other line.
        """
        frame = self._text_edit.document().rootFrame()
        fmt = frame.frameFormat()
        bottom = float(int(44 * self._scale)) if reserve else float(
            self._text_edit.anchor_padding())
        if abs(fmt.bottomMargin() - bottom) < 0.5:
            return
        fmt.setBottomMargin(bottom)
        frame.setFrameFormat(fmt)

    # ═══════════════════════════════════════════════════════════════════
    #  Floating layout: the controls sit over the script, not beside it
    # ═══════════════════════════════════════════════════════════════════

    def _chrome_strip(self) -> int:
        """Top breathing room above the reading line, in window pixels.

        The controls moved to the side, so this is no longer occupied by a
        toolbar. It remains the reading anchor's inset from the top edge, and
        keeping it stable is what stops the active line from moving when the
        rail changed sides.
        """
        return int((4 + 32) * self._scale)

    def _chrome_margin(self) -> int:
        """Side inset the text keeps inside the card."""
        return int((12 if self._compact_mode else 18) * self._scale)

    def _card_width(self) -> int:
        """Width of the reading surface; the rail lives outside it."""
        return max(1, self.width() - self.RAIL_W)

    def _layout_chrome(self):
        """Centre the vertical rail just outside the card's right edge."""
        chrome = getattr(self, "_chrome", None)
        if chrome is None:
            return
        buttons = (self._close_btn, self._note_btn, self._mic_btn,
                   self._compact_btn)
        width = max(btn.width() for btn in buttons)
        height = sum(btn.height() for btn in buttons) + \
            self.RAIL_SPACING * (len(buttons) - 1)
        x = self._card_width() + self.RAIL_GAP
        y = max(0, (self.height() - height) // 2)
        chrome.setGeometry(x, y, width, height)
        chrome.raise_()

    def _apply_root_margins(self):
        """Type owns the card; the control rail owns the strip beside it.

        The editor keeps its own top breathing room, but the right margin is
        always the rail width: the script never runs under the controls and
        the controls never sit on a word.
        """
        s = self._scale
        side = self._chrome_margin()
        right = side + self.RAIL_W
        if self._edit_mode:
            self._root_layout.setContentsMargins(
                side, self._chrome_strip(), right, int(10 * s))
        else:
            self._root_layout.setContentsMargins(side, 0, right, 0)
        # The leads define the reading area, and the document margins are
        # derived from them, so the leads have to be current first.
        self._sync_reading_leads()
        self._apply_document_lead()
        self._layout_chrome()

    def _apply_document_lead(self):
        """Reserve the opening lead above the script and the anchor room below.

        The reader opens with its first line 30% down the card, below the
        floating chrome, and keeps blank space below its last line so the end
        of a script can still be scrolled up onto the reading line. Both
        strips belong to the document, so they move with the type: once
        reading starts, the lines already read travel up behind the controls
        instead of being held still by a fixed inset.

        The editor reserves neither; its type sits below the toolbar the usual
        way. Re-applied after every document load and every resize, because
        replacing the text resets the root frame's format and both leads are
        derived from the viewport height.
        """
        frame = self._text_edit.document().rootFrame()
        fmt = frame.frameFormat()
        if self._edit_mode:
            top = 0.0
            bottom = None  # the Done button's strip is owned by the footer
        else:
            top = float(self._text_edit.opening_lead())
            bottom = float(self._text_edit.anchor_padding())
        changed = abs(fmt.topMargin() - top) >= 0.5
        if bottom is not None and abs(fmt.bottomMargin() - bottom) >= 0.5:
            changed = True
        if not changed:
            return
        fmt.setTopMargin(top)
        if bottom is not None:
            fmt.setBottomMargin(bottom)
        frame.setFrameFormat(fmt)

    def _sync_reading_leads(self):
        """Keep the reading line where it was when the viewport grew."""
        if not hasattr(self, "_text_edit"):
            return
        if self._edit_mode:
            self._text_edit.set_reading_leads(0.0, 0.0)
        else:
            # The top inset is breathing room, not a toolbar strip: the
            # controls are outside the card now.
            self._text_edit.set_reading_leads(self._chrome_strip(), 10 * self._scale)

    def _layout_done_button(self):
        """The editor needs a visible way out that is not a hidden key."""
        btn = getattr(self, "_done_btn", None)
        if btn is None:
            return
        s = self._scale
        w, h = int(62 * s), int(26 * s)
        btn.setFixedSize(w, h)
        btn.move(self._card_width() - w - int(16 * s),
                 self.height() - h - int(14 * s))
        btn.raise_()

    def _on_compact_motion_finished(self, narrow: bool):
        """Called when the narrow/wide spring lands."""
        if narrow:
            self._center_top()
            self._motion.jump(x=float(self.x()), y=float(self.y()),
                              w=float(self.width()), h=float(self.height()))
            self._reflow_timer.start()
        else:
            self.setMinimumSize(self.MIN_W, self.MIN_H)  # restore min size
            self._font_size = self._font_size_before_compact
            self._apply_typography()
            self._size_before_compact = None
            self._center_top()
            self._motion.jump(x=float(self.x()), y=float(self.y()),
                              w=float(self.width()), h=float(self.height()))
            self._reflow_timer.start()

    def _toggle_compact_mode(self):
        """Switch between the normal reader and a ~2-word narrow reader."""
        screen = (self._screen_for(QRect(self.geometry()))
                  or QApplication.primaryScreen()).availableGeometry()
        s = self._scale
        y = screen.y() + int(self.TOP_INSET * s)
        center_x = screen.x() + screen.width() // 2

        if self._compact_mode:
            self._compact_mode = False
            self._motion_intent = "wide"
            self._set_top_bar_icon_size(int(18 * s))
            self._compact_btn.set_compact(False)
            self._compact_btn.setToolTip("Narrow reader (about two words per line)")
            self._compact_btn.setAccessibleName("Switch to narrow reader")
            if self._size_before_compact is None:
                return
            self._apply_root_margins()
            target_w = self._size_before_compact.width()
            target_h = self._size_before_compact.height()
            card_w = target_w - self.RAIL_W
            self._animate_geometry_to(
                QRect(center_x - card_w // 2, y, target_w, target_h), response=0.32)
        else:
            self._compact_mode = True
            self._motion_intent = "narrow"
            # Font first, so the text has already reflowed when the width
            # arrives; animating width against old metrics makes the text jump.
            # The rail is vertical, so all four controls still fit outside a
            # 100 px card.
            self._set_top_bar_icon_size(int(16 * s))
            self._compact_btn.set_compact(True)
            self._compact_btn.setToolTip("Wide reader")
            self._compact_btn.setAccessibleName("Switch to wide reader")
            self._size_before_compact = self.size()
            self._font_size_before_compact = self._font_size
            # Two words per line need the width the 18 px margins were using.
            self._apply_root_margins()
            self.setMinimumSize(self.COMPACT_W, self.MIN_H)
            self._font_size = self.COMPACT_FONT
            self._apply_typography()
            card_w = self.COMPACT_W - self.RAIL_W
            self._animate_geometry_to(
                QRect(center_x - card_w // 2, y, self.COMPACT_W, self.height()),
                response=0.32)

    def _reset_position(self):
        if self._edit_target:
            return  # Ctrl+R is the reader's reset, not an editor command
        self._text_edit.moveCursor(QTextCursor.Start)
        self._matching.reset()
        self._last_match_time = None
        self._last_match_pos = -1
        self._stalled_finals = 0
        self._voice_wpm = 60
        self._manual_scroll_accum = 0.0
        self._voice_scroll_accum = 0.0
        self._voice_target_scroll = None
        self._checkpoints = {}
        self._line_spans = []
        self._current_line = 0
        self._confirmed_word = -1
        self._partial_word = -1
        self._apply_highlight(-1)
        self._refresh_lines()
        if self._voice_mode:
            self._scroll_to_reading_line(0)
        else:
            # Back at the document's first line: the view has to follow it,
            # or the words at the top stay behind where the reader is.
            self._scroll_word_to_top(0)

    def _font_increase(self):
        # 24 pt is the ceiling the saved preference and the reflow checks use;
        # growing past it used to be silently clamped on the next launch.
        self._font_size = min(24, self._font_size + max(1, int(2 * self._scale)))
        self._apply_typography()
        self._schedule_save()

    def _font_decrease(self):
        self._font_size = max(self.COMPACT_FONT, self._font_size - max(1, int(2 * self._scale)))
        self._apply_typography()
        self._schedule_save()

    def _set_speed(self, wpm, mode: str = "manual"):
        value = max(1, wpm)
        if mode == "voice":
            self._voice_wpm = value
        else:
            self._manual_wpm = value

    def _speed_step_for(self, value: int) -> int:
        # 5 levels of step size based on current speed
        if value < 10:
            return 1
        if value < 30:
            return 2
        if value < 60:
            return 5
        if value < 120:
            return 10
        return 20

    def _adjust_speed_dir(self, direction: int):
        if self._edit_target:
            return  # arrow keys belong to the caret while editing
        if self._voice_mode:
            step = self._speed_step_for(self._voice_wpm)
            self._voice_wpm = max(1, self._voice_wpm + (step * direction))
        else:
            step = 10
            prev = self._manual_wpm
            self._manual_wpm = max(1, self._manual_wpm + (step * direction))
            delta = self._manual_wpm - prev
            if delta != 0:
                self._show_speed_feedback(delta)
        self._schedule_save()

    def _toggle_manual_playback(self):
        """Space in the reader: start or stop the timed scroll clock.

        Voice mode owns the read position, so Space is a no-op there until the
        reader switches to Manual. At the end of the script the clock stops;
        pressing Space again replays from the top.
        """
        if self._edit_target or self._voice_mode or not self._script_text:
            return
        if self._manual_running:
            self._manual_running = False
            return
        bar = self._text_edit.verticalScrollBar()
        if bar.value() >= bar.maximum():
            bar.setValue(0)
            self._matching.set_position(0)
            self._confirmed_word = -1
            self._partial_word = -1
            self._last_match_pos = -1
            self._current_line = 0
            self._apply_highlight(-1)
        self._manual_scroll_accum = 0.0
        self._manual_running = True

    def _start_speed_feedback_fade_out(self):
        self._speed_feedback_fade_out.start()

    def _show_speed_feedback(self, delta: int):
        """Show a short popup: bottom right, less opaque, fade in/out 0.1s."""
        self._speed_feedback_timer.stop()
        self._speed_feedback_fade_in.stop()
        self._speed_feedback_fade_out.stop()
        self._speed_feedback_lbl.setText(f"Speed {delta:+d}")
        self._speed_feedback_lbl.adjustSize()
        margin = 8
        x = self._card_width() - self._speed_feedback_lbl.width() - margin
        y = self.height() - self._speed_feedback_lbl.height() - margin
        self._speed_feedback_lbl.move(x, y)
        self._speed_feedback_opacity.setOpacity(0.0)
        self._speed_feedback_lbl.show()
        self._speed_feedback_lbl.raise_()
        self._speed_feedback_fade_in.start()
        self._speed_feedback_timer.start(500)  # hold then fade out (total ~700ms)

    def _update_voice_speed(self, position: int, is_final: bool):
        now = time.monotonic()
        if self._last_match_time is not None and position > self._last_match_pos:
            delta_words = position - self._last_match_pos
            delta_time = now - self._last_match_time
            if delta_time > 0.2 and delta_words > 0:
                wpm = (delta_words / delta_time) * 60.0
                wpm = max(60.0, min(wpm, 300.0))
                # Track both directions: a reader who slows down should not
                # keep an old fast estimate from a previous sentence.
                weight = 0.2 if is_final else 0.1
                self._voice_wpm = int(round(
                    self._voice_wpm + (wpm - self._voice_wpm) * weight))
        self._last_match_time = now
        self._last_match_pos = position

    # ── Rendered lines ───────────────────────────────────────────────

    def _measure_lines(self) -> List[_RenderedLine]:
        """Resolve every visual line to the script words rendered on it.

        The document layout already knows where the text wraps for the
        current font, style and viewport width, so it is the source of truth
        for line breaks rather than the source formatting of the script.
        """
        if not self._script_text or not self._matching.word_count:
            return []
        doc = self._text_edit.document()
        # Reading the size forces any pending font/width relayout to finish
        # before the lines are measured.
        doc.documentLayout().documentSize()

        offsets = self._matching.char_offsets
        total = len(offsets)
        spans: List[_RenderedLine] = []
        word = 0
        block = doc.begin()
        while block.isValid() and word < total:
            # blockBoundingRect is already in document coordinates, which
            # include the document margin.
            block_top = doc.documentLayout().blockBoundingRect(block).top()
            layout = block.layout()
            if layout is None:
                block_end = block.position() + block.length()
                while word < total and offsets[word] < block_end:
                    word += 1
                block = block.next()
                continue

            for line_index in range(layout.lineCount()):
                line = layout.lineAt(line_index)
                start = block.position() + line.textStart()
                end = start + line.textLength()
                first = None
                last = None
                while word < total and offsets[word] < end:
                    if offsets[word] >= start:
                        if first is None:
                            first = word
                        last = word
                    word += 1
                if first is not None:
                    spans.append(_RenderedLine(
                        first_word=first,
                        last_word=last,
                        top=block_top + line.position().y(),
                        height=max(1.0, float(line.height())),
                    ))
            block = block.next()
        return spans

    def _refresh_lines(self):
        """Rebuild the line map, keeping the reader on the same words.

        The current word position is the anchor across a reflow: line breaks
        may move around it, but the reader stays on the text it was reading.
        The current line is preferred over the confirmed word so a reader
        sitting at the start of a fresh line is not pulled back to the end of
        the previous one.
        """
        anchor = None
        if self._line_spans and 0 <= self._current_line < len(self._line_spans):
            anchor = self._line_spans[self._current_line].first_word
        # Loading a script replaces the document, which drops the reserved
        # strip; put it back before the lines are measured.
        self._apply_document_lead()
        self._line_spans = self._measure_lines()
        if not self._line_spans:
            self._current_line = 0
            return
        focus = 0 if anchor is None else anchor
        if self._confirmed_word >= 0:
            focus = max(focus, self._confirmed_word)
        self._current_line = self._line_index_for_word(focus)

    def _line_index_for_word(self, word: int) -> int:
        """Index of the rendered line containing a script word."""
        if not self._line_spans:
            return 0
        word = max(0, word)
        low, high = 0, len(self._line_spans) - 1
        while low < high:
            mid = (low + high) // 2
            if self._line_spans[mid].last_word >= word:
                high = mid
            else:
                low = mid + 1
        return low

    def _retarget_scroll(self, target: float):
        """Glide the script to a new pixel offset, or snap when motion is off.

        A retarget keeps the spring's current value (and velocity where it
        has one), so a line advance continues from the pixel already on
        screen instead of restarting. A recent wheel scroll wins for its
        hold period; the next voice line picks the scroll back up afterwards.
        """
        bar = self._text_edit.verticalScrollBar()
        target = max(0.0, min(float(bar.maximum()), float(target)))
        if (time.monotonic() - self._last_manual_scroll_time < 2.5
                and self._voice_target_scroll is not None):
            self._sync_scroll_spring()
            return
        if not system_allows_motion():
            self._scroll_spring.jump_to(target)
            self._scroll_moving = False
            bar.setValue(int(round(target)))
            return
        # Start from the pixel on screen. If the bar was moved by something
        # other than this spring (a Home key, a restored checkpoint), the
        # spring's old value is stale and must not be resumed.
        if (not self._scroll_moving
                or abs(self._scroll_spring.value - bar.value()) > 1.5):
            self._scroll_spring.jump_to(float(bar.value()))
        self._scroll_last_time = 0.0
        self._scroll_spring.set_target(target)
        self._scroll_moving = True

    def _sync_scroll_spring(self):
        """A direct scroll (wheel, checkpoint) is the new resting point."""
        self._scroll_spring.jump_to(float(self._text_edit.verticalScrollBar().value()))
        self._scroll_moving = False
        self._scroll_last_time = 0.0

    def _advance_scroll(self):
        """Step the scroll spring and write the rounded pixel to the bar."""
        if not self._scroll_moving:
            return
        now = time.monotonic()
        if not self._scroll_last_time:
            # First frame only establishes the clock; stepping it with an
            # unknown dt is what produced one-frame jumps in the old loop.
            self._scroll_last_time = now
            return
        dt = min(0.064, max(0.0, now - self._scroll_last_time))
        self._scroll_last_time = now
        if dt <= 0.0:
            return
        self._scroll_spring.step(dt)
        if self._scroll_spring.settled:
            self._scroll_spring.land()
            self._scroll_moving = False
        bar = self._text_edit.verticalScrollBar()
        value = int(round(self._scroll_spring.value))
        if bar.value() != value:
            bar.setValue(value)

    def _scroll_target_for_line(self, line_index: int) -> float:
        """Scroll offset that keeps ~30% of the visible words behind the line.

        The line being read is not parked at a fixed height: the anchor is the
        number of whole rendered lines above it whose words come closest to
        30% of the words on screen. A dense line therefore pushes the page
        further than a sparse one, which is what keeps the split true for
        words rather than for rows.

        At the opening line the lead above the document is shown instead of
        being scrolled away, so the first line sits 30% down the card and the
        rest of the page is upcoming text. After that the lead scrolls off
        with the lines already read. Near the end the bar's own limit takes
        over and the remaining text is shown as it is. The line being read
        also keeps its breathing room at the top, because a 30% share of a
        five-line card is one and a half lines and half of one would sit
        against the edge.
        """
        lines = self._line_spans
        edit = self._text_edit
        bar = edit.verticalScrollBar()
        index = max(0, min(line_index, len(lines) - 1))
        if index == 0:
            # The opening lead belongs to the document, so the first line's
            # target is its top minus that lead: the bar stays at zero and
            # the lead is what puts the line 30% down the card. Scrolling to
            # the line's raw top would eat the lead before anything is read.
            return 0.0
        height = float(edit.viewport().height())
        words = [line.last_word - line.first_word + 1 for line in lines]

        # Words of the reading line already heard count as spoken too.
        spoken = 0
        position = self._display_word()
        if index == self._current_line and position >= lines[index].first_word:
            spoken = min(words[index], position - lines[index].first_word + 1)

        # Candidate anchors are whole lines, never a multiple of the pitch:
        # blank lines between paragraphs are not rendered, so consecutive
        # spans can be two pitches apart and counting rows would drift.
        best = None
        capacity = max(1, int(height // max(1.0, edit.line_grid())))
        for first in range(max(0, index - capacity), index + 1):
            # The topmost line starts on the card's top edge: the line above it
            # has left the view instead of leaving a slice of descenders.
            top = lines[first].top
            reading_top = lines[index].top - top
            visible = 0
            for j in range(first, len(lines)):
                y = lines[j].top - top
                if y >= height:
                    break
                # A line only counts once half of it is on screen.
                if y + lines[j].height / 2.0 >= 0.0:
                    visible += words[j]
            above = sum(words[first:index]) + spoken
            error = abs(above - self.READING_SHARE * visible)
            # A share that pushes the reading line into the top edge is only
            # used when nothing else is available (opening).
            if reading_top < self._chrome_strip():
                error += 1000.0
            if best is None or error < best[0]:
                best = (error, top)

        target = lines[index].top if best is None else best[1]
        return max(0.0, min(float(bar.maximum()), target))

    def _scroll_to_reading_line(self, line_index: int):
        """Anchor the given line so ~30% of the words on screen are behind it.

        The view is only re-anchored when the reader changes line, never on a
        partial result, so pausing mid-line leaves it perfectly still. It is
        also recalculated whenever the layout changes, which is what keeps the
        word balance across font and width changes.
        """
        if not self._line_spans:
            return
        anchored = self._scroll_target_for_line(line_index)
        self._voice_target_scroll = anchored
        self._retarget_scroll(anchored)

    def _set_reading_position(self, word: int, update_target: bool = True):
        """Move the reading position directly (wheel, checkpoints)."""
        if not self._script_text or not self._matching.word_count:
            return
        word = max(0, min(word, self._matching.word_count - 1))
        self._matching.set_position(word)
        self._confirmed_word = word
        self._partial_word = -1
        self._last_match_pos = word
        if self._line_spans:
            self._current_line = self._line_index_for_word(word)
        self._apply_highlight(word)
        if update_target and self._voice_mode:
            self._scroll_to_reading_line(self._current_line)

    # ── Reflow (font, style, width) ──────────────────────────────────

    def _on_reflow_timeout(self):
        """Rebuild line breaks after a resize settles and restore the view."""
        top_word = self._reflow_pending_top
        self._reflow_pending_top = None
        if not self._script_text or self._edit_mode:
            return
        self._refresh_lines()
        self._restore_reading_view(top_word)

    def _restore_reading_view(self, top_word: Optional[int]):
        """Put the reader back on the same words after the layout changed."""
        if not self._script_text or self._edit_mode or not self._line_spans:
            return
        if self._voice_mode:
            self._scroll_to_reading_line(self._current_line)
        elif top_word is not None:
            self._scroll_word_to_top(top_word)

    def _text_font(self) -> QFont:
        """The reader's font, including size-dependent tracking.

        Type that is set larger needs less space between letters, and type
        set small needs a little more; a single tracking value is what makes
        a teleprompter look untypeset at one end of its size range.
        """
        font = QFont(self._font_family, self._font_size)
        # Bold. At 8-12 pt on a dark card the regular face reads as hairlines,
        # and the weight costs no line height: the family's ascent, descent and
        # leading are the same at every weight, so only the stroke changes.
        font.setWeight(QFont.Bold)
        tracking = (14.0 - float(self._font_size)) * 0.05 * self._scale
        font.setLetterSpacing(QFont.AbsoluteSpacing, tracking)
        return font

    def _line_pitch(self) -> int:
        """Distance between baselines, derived from the font in use.

        This used to be a fixed 24 px, which the largest sizes outgrew:
        24 pt Segoe UI is 33 px tall, so the lines overlapped and the last
        line was clipped. Deriving the pitch from the metrics keeps the
        reference rhythm at the default size and makes it impossible for
        two lines to collide at any size.

        The multiplier is set so the default card holds four whole lines at
        the default size: 21 px of type at 1.40 is a 29 px pitch, and four of
        those fit the 122 px card. Tighter than that starts to close the gap
        between ascenders and the descenders above them.
        """
        metrics = QFontMetrics(self._text_font())
        return max(int(round(metrics.height() * 1.40)), metrics.height() + 4)

    def _apply_typography(self):
        """Apply the current font and recompute line breaks for the new layout."""
        top_word = None
        if not self._voice_mode and not self._edit_mode:
            top_word = self._word_at_scroll_top()
        self._text_edit.setFont(self._text_font())
        self._spaced_key = None          # the pitch may have changed with the size
        self._apply_line_spacing()
        self._refresh_lines()
        self._restore_reading_view(top_word)

    def _word_at_scroll_top(self) -> Optional[int]:
        """First script word of the line currently at the top of the view."""
        if not self._line_spans:
            return None
        scroll = float(self._text_edit.verticalScrollBar().value())
        # The reading line is the one the reader is looking at, so the
        # threshold includes the anchor: without it the line just scrolled
        # out above it would count as the top one.
        reach = scroll + self._text_edit.reading_inset() + 1.0
        top = self._line_spans[0]
        for line in self._line_spans:
            if line.top <= reach:
                top = line
            else:
                break
        return top.first_word

    def _scroll_word_to_top(self, word: int):
        """Scroll so the line containing a word sits at the top of the view."""
        if not self._line_spans:
            return
        line = self._line_spans[self._line_index_for_word(word)]
        inset = self._text_edit.reading_inset()
        bar = self._text_edit.verticalScrollBar()
        target = line.top - inset
        value = max(0, min(bar.maximum(), int(round(target))))
        bar.setValue(value)
        self._sync_scroll_spring()

    def _scroll_to_word(self, position: int):
        start, _ = self._matching.word_span(position)
        cursor = QTextCursor(self._text_edit.document())
        cursor.setPosition(start)
        self._text_edit.setTextCursor(cursor)
        self._text_edit.ensureCursorVisible()

    def _apply_read_mode_style(self):
        """Set text edit stylesheet for read mode (Notch/Keys & Scroll). Dark = white text, light = dark text."""
        color = "#111111" if not self._dark_theme else "#fafafa"
        self._text_edit.setStyleSheet(
            "QTextEdit {"
            "  background: transparent;"
            f"  color: {color};"
            "  border: none;"
            "}"
        )
        if hasattr(self, "_close_btn"):
            self._apply_control_theme()

    def _apply_highlight_end(self, position: int) -> int:
        """Character boundary of the spoken run for a word position."""
        if position < 0 or not self._matching.word_count:
            return 0
        return self._matching.word_span(position)[1]

    def _apply_highlight(self, position: int):
        if not self._script_text:
            return
        if self._apply_line_spacing():
            # Line spacing is part of the layout: measure the lines again.
            self._refresh_lines()
        doc = self._text_edit.document()

        # Resting text is the reference's near-white. Text already spoken
        # recedes, so the words still to come stay the brightest thing.
        base_hex = "#111111" if not self._dark_theme else "#fafafa"
        # Already-spoken type recedes, but not below reading contrast: on
        # paper the old #a0a0a0 was 2.5:1, which is faint even for text the
        # eye has already left. #767676 keeps it clearly secondary at 4.5:1.
        spoken_hex = "#767676" if not self._dark_theme else "#8a8a8a"
        base_format = QTextCharFormat()
        base_format.setForeground(QColor(base_hex))
        spoken_format = QTextCharFormat()
        spoken_format.setForeground(QColor(spoken_hex))
        # The words being spoken come back to full brightness on one quiet
        # band, so the eye can find its place without the line moving. The
        # band is the accent pre-blended against the card: QTextEdit paints a
        # character background opaquely, so a translucent colour would land
        # as a solid block of accent.
        current_format = QTextCharFormat(base_format)
        current_format.setBackground(QColor("#0A1940") if self._dark_theme
                                     else QColor("#E1EFFF"))

        end = self._apply_highlight_end(position)
        focus = None
        if position >= 0 and end > 0:
            first = max(0, min(position, self._matching.word_count - 1) - (self.FOCUS_WORDS - 1))
            start = self._matching.word_span(first)[0]
            focus = (start, end)
        previous_focus = self._focus_span
        # Do not key on id(document()): PyQt hands back a fresh wrapper for
        # the same QTextDocument on every call, so the id changes and every
        # apply would look like a new document. Content changes invalidate
        # this key explicitly (load, restore, editor collapse).
        key = (len(self._script_text), self._font_family,
               self._font_size, self._dark_theme, base_hex, spoken_hex)
        previous = self._highlight_end
        # A stale key means the document, theme or type changed under the
        # shading; every range has to be repainted from the base.
        full = key != self._highlight_key or previous is None
        self._highlight_key = key
        self._highlight_end = end
        if not full and end == previous and focus == previous_focus:
            return

        cursor = QTextCursor(doc)
        if full:
            # Rare (load, theme, type change): repaint both tiers in one undo
            # step. The reader is read-only, so this grouping is only about
            # making the document settle once.
            cursor.beginEditBlock()
            cursor.select(QTextCursor.Document)
            cursor.setCharFormat(base_format)
            if end > 0:
                cursor.setPosition(0)
                cursor.setPosition(end, QTextCursor.KeepAnchor)
                cursor.setCharFormat(spoken_format)
            cursor.endEditBlock()
        elif end > previous:
            # The reader moved forward: the run before `previous` is already
            # shaded, so only the new words need the spoken colour. No edit
            # block here: grouping a single commit cost more than the commit
            # itself, and there is no undo history to protect in a reader.
            cursor.setPosition(previous)
            cursor.setPosition(end, QTextCursor.KeepAnchor)
            cursor.setCharFormat(spoken_format)
        else:
            # It moved back (wheel, checkpoint, reset): un-shade the run the
            # reader has returned to.
            cursor.setPosition(end)
            cursor.setPosition(previous, QTextCursor.KeepAnchor)
            cursor.setCharFormat(base_format)

        # Only the words that entered or left the band are touched: moving the
        # band one word is two tiny formats, not a pass over the script.
        if focus != previous_focus:
            if previous_focus:
                start, stop = previous_focus
                cursor.setPosition(start)
                cursor.setPosition(stop, QTextCursor.KeepAnchor)
                cursor.setCharFormat(spoken_format if stop <= end else base_format)
            if focus:
                cursor.setPosition(focus[0])
                cursor.setPosition(focus[1], QTextCursor.KeepAnchor)
                cursor.setCharFormat(current_format)
            self._focus_span = focus

    def _apply_line_spacing(self) -> bool:
        """Reference rhythm, at whatever size the reader is set to.

        Reflowed only when the script or the font changes, so caret moves
        and recognizer updates do not touch block layout on every tick.
        Returns True when the layout was changed.
        """
        line_px = self._line_pitch()
        # A replaced document keeps the same length and font but loses its
        # block formats. Checking the first block catches that even if the
        # key matches, so a reload can never skip its leading.
        first = self._text_edit.document().begin()
        block_format = first.blockFormat()
        typeset = (block_format.lineHeightType() == QTextBlockFormat.FixedHeight
                   and abs(block_format.lineHeight() - line_px) <= 0.5)
        key = (len(self._script_text), self._font_family, self._font_size)
        if typeset and getattr(self, "_spaced_key", None) == key:
            return False
        self._spaced_key = key
        cursor = QTextCursor(self._text_edit.document())
        cursor.select(QTextCursor.Document)
        block_format = QTextBlockFormat()
        block_format.setLineHeight(line_px, QTextBlockFormat.FixedHeight)
        cursor.mergeBlockFormat(block_format)
        return True

    @staticmethod
    def _screen_for(rect: QRect):
        """The screen a rectangle belongs to, by overlap then proximity.

        ``QApplication.primaryScreen()`` is the wrong answer for a card on a
        second monitor: centring every transition on the primary made opening
        the editor drag the reader onto another display.
        """
        screens = QApplication.screens()
        if not screens:
            return None

        def overlap(screen):
            area = screen.availableGeometry().intersected(rect)
            return max(0, area.width()) * max(0, area.height())

        best = max(screens, key=overlap)
        if overlap(best) > 0:
            return best
        centre = rect.center()
        return min(screens, key=lambda screen: (
            screen.availableGeometry().center() - centre).manhattanLength())

    def _center_top(self):
        # Before the first show the window still sits at the toolkit's default
        # origin, which on a multi-monitor desktop can overlap a secondary
        # display. Initial placement belongs on the primary; only a transition
        # from a visible window follows the screen the card is already on.
        screen = (self._screen_for(QRect(self.geometry()))
                  if self.isVisible() else QApplication.primaryScreen())
        screen_geom = (screen or QApplication.primaryScreen()).availableGeometry()
        # Centre the card, not the window: the rail is extra width on the
        # right and would otherwise pull the reading surface left of centre.
        x = screen_geom.x() + (screen_geom.width() - self._card_width()) // 2
        # Reference inset from the top work-area edge
        y = screen_geom.y() + int(self.TOP_INSET * self._scale)
        self.move(x, y)

    def _show_help(self):
        """Open the How to use Notch guide (same as first-time popup)."""
        HelpDialog(self, dark=self._dark_theme).exec_()

    def _open_settings(self):
        self._stop_listening()
        dlg = SettingsDialog(self._device_index, self._noise_gate, self._input_gain,
                             self.MODEL_PATH, self, dark=self._dark_theme)
        if dlg.exec_():
            self._device_index = dlg.selected_device
            self._noise_gate = dlg.noise_gate
            self._input_gain = dlg.input_gain
            self._schedule_save()
        if self._voice_mode: self._start_listening()

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        mods = event.modifiers()

        if key == Qt.Key_Escape:
            self._on_escape()
            return

        if mods == Qt.ControlModifier and key == Qt.Key_N:
            self._open_note()
            return

        if mods == Qt.ControlModifier and key == Qt.Key_R:
            self._reset_position()
            return

        if mods == Qt.ControlModifier and key in (Qt.Key_1, Qt.Key_2, Qt.Key_3, Qt.Key_4):
            self._set_checkpoint(int(chr(key)))
            return

        if mods == (Qt.ControlModifier | Qt.ShiftModifier) and key in (Qt.Key_1, Qt.Key_2, Qt.Key_3, Qt.Key_4):
            self._go_to_checkpoint(int(chr(key)))
            return

        super().keyPressEvent(event)

    def _set_checkpoint(self, idx: int):
        if self._edit_target:
            return
        bar = self._text_edit.verticalScrollBar()
        self._checkpoints[idx] = {
            "scroll": bar.value(),
            "match": self._last_match_pos,
        }

    def _toggle_manual_run(self):
        if self._edit_target:
            return  # Space is a space while typing
        if not self._voice_mode:
            self._manual_running = not self._manual_running

    def _go_to_checkpoint(self, idx: int):
        if self._edit_target:
            return
        cp = self._checkpoints.get(idx)
        if not cp:
            return
        if self._voice_mode:
            self._voice_mode = False
            self._stop_listening()
            self._target_glow_alpha = 28
            self._smoothed_volume_level = 0.0
            self._schedule_save()
        self._manual_running = False
        bar = self._text_edit.verticalScrollBar()
        bar.setValue(cp["scroll"])
        self._sync_scroll_spring()
        match = cp["match"] if cp["match"] is not None else -1
        self._confirmed_word = match
        self._partial_word = -1
        if self._line_spans:
            self._current_line = self._line_index_for_word(max(0, match))
        self._apply_highlight(match)

    def eventFilter(self, obj, event):
        if obj in (getattr(self, "_close_btn", None),
                   getattr(self, "_note_btn", None),
                   getattr(self, "_mic_btn", None),
                   getattr(self, "_compact_btn", None)):
            if event.type() in (QEvent.Enter, QEvent.MouseMove):
                # A rail that has faded out must come back under the pointer,
                # not stay invisible until the pointer re-enters the card.
                self._wake_chrome()
        if hasattr(self, "_text_edit") and (
            obj == self._text_edit or obj == self._text_edit.viewport()
        ):
            if event.type() in (QEvent.MouseMove, QEvent.Enter):
                # Returning the chrome is the point of the idle recede: any
                # movement, even over bare type, brings the controls back.
                self._wake_chrome()
            if event.type() == QEvent.ContextMenu:
                self._show_menu(event.globalPos())
                return True
            if event.type() == QEvent.MouseButtonDblClick:
                self._toggle_edit_mode()
                return True
            if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
                # The editor owns the keyboard while editing, so a window
                # shortcut alone never saw this key. Escape is the way out
                # of the editor, and it must work from inside the text.
                self._on_escape()
                return True
            if event.type() == QEvent.KeyPress and not self._edit_mode:
                # Reader-only keys. The editor keeps normal typing because this
                # branch is skipped there.
                if event.key() == Qt.Key_Space:
                    self._toggle_manual_playback()
                    return True
                if event.key() in (Qt.Key_Left, Qt.Key_Right):
                    self._adjust_speed_dir(-1 if event.key() == Qt.Key_Left else 1)
                    return True
            if event.type() == QEvent.Wheel and isinstance(event, QWheelEvent):
                bar = self._text_edit.verticalScrollBar()
                delta_y = event.angleDelta().y()
                if delta_y != 0:
                    self._last_manual_scroll_time = time.monotonic()  # so voice doesn't override for 2.5s
                    line_h = self._text_edit.fontMetrics().lineSpacing()
                    step = max(1, int(line_h * 0.45))  # ~0.45 lines per tick (less sensitive)
                    step = step * (1 if delta_y > 0 else -1)
                    bar.setValue(bar.value() - step)
                    self._sync_scroll_spring()
                    # Move read position with scroll: up = undo words, down = complete words (skip back / skip ahead)
                    if self._script_text and self._matching.word_count > 0:
                        SCROLL_WORDS = 2
                        current = self._last_match_pos if self._last_match_pos >= 0 else 0
                        if delta_y > 0:  # scroll up -> move position back
                            self._set_reading_position(max(0, current - SCROLL_WORDS))
                        else:  # scroll down -> move position forward
                            self._set_reading_position(current + SCROLL_WORDS)
                return True
        return super().eventFilter(obj, event)

    def _clear_edit_mode_char_formats(self):
        """Clear character formats so all text uses the editor's plain text color.

        Must follow the theme: the editor is a dark card in dark mode and a
        paper surface in light mode. Deferred so the expand animation starts
        without a pause.
        """
        doc = self._text_edit.document()
        cursor = QTextCursor(doc)
        cursor.select(QTextCursor.Document)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#111111" if not self._dark_theme else "#fafafa"))
        cursor.setCharFormat(fmt)

    def _on_edit_motion_finished(self):
        """The card has landed. Styling was swapped during the text fade."""
        if self._edit_target:
            return
        self._chrome.show()
        self._wake_chrome()
        self.clearMask()
        self.resize(self._collapsed_size)
        self._center_top()
        self._motion.jump(x=float(self.x()), y=float(self.y()),
                          w=float(self.width()), h=float(self.height()))
        self._text_fade_in.start()
        if self._prev_voice_mode and not self._edit_target:
            self._voice_mode = True
            self._start_listening()

    def _toggle_edit_mode(self):
        """Switch between the reader pill and the editor card.

        Reversible at any point: the springs start from the geometry on
        screen and keep their velocity, so a second click mid-flight bends
        the transition back instead of being swallowed.
        """
        if self._peeked:
            # Editing always starts from the full card, never from the
            # resting pill: the two states are not composable.
            self._peeked = False
            self._peek_restore = None
            self._chrome.show()
            self._text_edit.show()
        self._edit_target = not self._edit_target
        # Space and the arrow keys are playback controls in the reader and
        # text input in the editor: the shortcuts have to stand down while
        # the editor holds the keyboard, or Qt consumes the keystroke before
        # the text field can insert it.
        for shortcut in (getattr(self, "_sc_space", None),
                         getattr(self, "_sc_left", None),
                         getattr(self, "_sc_right", None)):
            if shortcut is not None:
                shortcut.setEnabled(not self._edit_target)
        screen = (self._screen_for(QRect(self.geometry()))
                  or QApplication.primaryScreen()).availableGeometry()
        s = self._scale

        if self._edit_target:
            self.clearMask()
            self._motion_intent = "expand"
            self._prev_voice_mode = self._voice_mode
            self._manual_running = False
            self._stop_listening()  # non-blocking; the worker stops in background
            if self.width() < int(500 * s) and self.height() < int(280 * s):
                self._collapsed_size = self.size()
            self._text_fade_for_expand = True
            # Only fade if there is something visible to fade, so reversing
            # after the swap does not flash the text back on.
            if self._text_opacity.opacity() > 0.01:
                self._text_fade_out.start()
            card_w, th = int(860 * s), int(320 * s)
            tw = card_w + self.RAIL_W
            x = screen.x() + (screen.width() - card_w) // 2
            y = screen.y() + int(self.TOP_INSET * s)
            self._animate_geometry_to(QRect(x, y, tw, th), response=0.42)
            self._glow.set_target(0.0, carry_velocity=False)
            return

        self._motion_intent = "collapse"
        self._text_fade_for_expand = False
        w = self._collapsed_size.width()
        h = self._collapsed_size.height()
        # A size saved mid-animation would be the expanded card; fall back.
        if w >= int(500 * s) or h >= int(280 * s):
            w, h = self.DEFAULT_W, self.DEFAULT_H
            self._collapsed_size = QSize(w, h)
        x = screen.x() + (screen.width() - (w - self.RAIL_W)) // 2
        y = screen.y() + int(self.TOP_INSET * s)
        if self._text_opacity.opacity() > 0.01:
            self._text_fade_out.start()
        self._animate_geometry_to(QRect(x, y, w, h), response=0.36)
        self._glow.set_target(28.0, carry_velocity=False)

    def _apply_capture_exclusion(self):
        """Keep the reader out of screen shares, like the menu and notes."""
        exclude_from_capture(self)

    def _apply_blur_effect(self):
        """
        Blur effect disabled - Windows Acrylic/blur applies to the entire
        rectangular window, causing visible borders around the rounded pill.
        The solid dark background with 95% opacity already looks good.
        """
        pass

    def showEvent(self, event):
        super().showEvent(event)
        # First-time guide (persist with QSettings so we only show once per machine)
        settings = QSettings("Notch", "Notch")
        guide_seen = settings.value("guide_seen", False, type=bool)
        # Restore on every launch, not just the first one: the script, notes and
        # reader preferences are expected to survive close and relaunch.
        self._restore_settings()
        self._restore_state()
        if not guide_seen:
            settings.setValue("guide_seen", True)
            QTimer.singleShot(300, self._show_help)  # Slight delay so overlay is visible first
        # Start fade-in animation
        if self._fade_anim.state() != QAbstractAnimation.Running:
            self._fade_anim.start()
        if self._voice_mode:
            print("[Notch] Voice mode on — starting mic + speech recognition", flush=True)
            self._start_listening()
        # Without this the toolbar only recedes after the pointer has moved;
        # launching with the cursor elsewhere left it on screen for good.
        self._idle_timer.start()

    # ═══════════════════════════════════════════════════════════════════
    #  Session persistence (script + sticky notes)
    # ═══════════════════════════════════════════════════════════════════


    def _open_note(self):
        """Open a sticky note, offset from the overlay or from the last note."""
        note = NoteWindow(scale=self._scale, dark=self._dark_theme)
        if self._notes:
            last = self._notes[-1]
            wanted = QRect(last.x() + int(24 * self._scale),
                           last.y() + int(24 * self._scale),
                           note.width(), note.height())
        else:
            wanted = QRect(self.x() + self.width() + int(12 * self._scale),
                           self.y(), note.width(), note.height())
        note.move(self._clamp_to_available(wanted).topLeft())
        note.changed.connect(self._schedule_save)
        note.closed.connect(self._on_note_closed)
        self._notes.append(note)
        note.show()
        note.raise_()
        note.activateWindow()
        return note

    @staticmethod
    def _clamp_to_available(rect: QRect) -> QRect:
        """Keep a window fully inside one screen after a layout change.

        Monitors get unplugged, resolutions change, and a note saved on the
        second display would otherwise restore into coordinates that no
        longer exist: still running, but impossible to see or click. The
        returned rectangle is both moved inside the screen and shrunk to fit
        it, so a card saved on a larger display still comes back whole.
        """
        screens = QApplication.screens()
        if not screens:
            return rect
        target = None
        best = -1
        for screen in screens:
            available = screen.availableGeometry()
            overlap = available.intersected(rect)
            area = overlap.width() * overlap.height()
            if area > best:
                best, target = area, available
        if best <= 0:
            # Nothing overlaps: the display the window was left on is gone.
            # Land on the screen nearest those saved coordinates instead of
            # whichever screen Qt happens to list first, so a laptop docked
            # at home opens where the user is now looking.
            centre = rect.center()
            target = min(
                (screen.availableGeometry() for screen in screens),
                key=lambda area: (area.center() - centre).manhattanLength())
        if target is None:
            target = QApplication.primaryScreen().availableGeometry()
        width = max(1, min(rect.width(), target.width()))
        height = max(1, min(rect.height(), target.height()))
        x = max(target.x(), min(rect.x(), target.x() + target.width() - width))
        y = max(target.y(), min(rect.y(), target.y() + target.height() - height))
        return QRect(x, y, width, height)

    def _on_note_closed(self, note_id: str):
        for note in list(self._notes):
            if note.note_id == note_id:
                self._notes.remove(note)
                # Dropping the last reference would destroy the widget in the
                # middle of the event that closed it, and the rest of that
                # handler (the close glyph's own mouse-release, or the note's
                # closeEvent) then touches a deleted object. Deleting is
                # deferred to the event loop instead.
                note.deleteLater()
        # A closed note is a deliberate deletion, not a state the backup may
        # undo if the primary file is later unreadable.
        self._save_state(purge_backup=True)

    def _detach_notes(self, persist: bool = False) -> int:
        """Close every note, optionally removing its saved copy as well.

        ``closed`` is blocked per note: it is the signal that would otherwise
        save the state once per window, and the window is gone either way.
        """
        closed = len(self._notes)
        for note in list(self._notes):
            note.blockSignals(True)
            note.close()
            # Deferred, like a note closed from its own corner: dropping the
            # last reference here would destroy the window while Qt may still
            # be delivering the mouse event that closed the app.
            note.deleteLater()
        self._notes.clear()
        if persist:
            self._save_state(purge_backup=True)
        return closed

    def _discard_script_notes(self):
        """Drop the notes that belong to a script that is being cleared.

        Notes are written against the script on screen. Keeping them once
        that script is gone would leave talking points hanging over an empty
        reader, and the state file would hand them back on the next launch as
        if they still belonged to something.
        """
        if self._detach_notes(persist=True):
            print("[Notch] Cleared the notes for the cleared script", flush=True)

    def _close_all_notes(self):
        self._detach_notes()

    def _note_state(self) -> list:
        """Only written notes are kept; blank ones disappear on close."""
        state = []
        for note in self._notes:
            if note.text().strip():
                state.append(note.to_state())
        return state

    def _persisted_card_rect(self) -> QRect:
        """The reader's normal-card rectangle, whatever state it is in now.

        A position saved while peeked, narrow or expanded into the editor
        would restore the wrong card shape on the next launch, so those states
        map back to the reading card first. This is also the rectangle the
        reader comes back to.
        """
        if self._peeked and self._peek_restore is not None:
            return QRect(self._peek_restore)
        if self._compact_mode and self._size_before_compact is not None:
            width = self._size_before_compact.width()
            height = self._size_before_compact.height()
            return QRect(self.x() + (self.width() - width) // 2,
                         self.y(), width, height)
        collapsed = self._collapsed_size
        if self._edit_target and collapsed is not None:
            # Closing with the editor open would otherwise save the 860x320
            # editing card as the reader's size, and the next launch would
            # come back as an editor with no text in it.
            width, height = collapsed.width(), collapsed.height()
            if (width < int(500 * self._scale)
                    and height < int(280 * self._scale)):
                screen = (self._screen_for(QRect(self.geometry()))
                          or QApplication.primaryScreen()).availableGeometry()
                card_w = width - self.RAIL_W
                return QRect(screen.x() + (screen.width() - card_w) // 2,
                             self.y(), width, height)
        return QRect(self.geometry())

    def _settings_state(self) -> dict:
        """Reader and microphone preferences that survive a relaunch."""
        card = self._persisted_card_rect()
        return {
            "font_family": self._font_family,
            "font_size": self._font_size,
            "dark_theme": self._dark_theme,
            "voice_mode": self._voice_mode,
            "manual_wpm": self._manual_wpm,
            "voice_wpm": self._voice_wpm,
            "window_x": int(card.x()),
            "window_y": int(card.y()),
            "window_w": int(card.width()),
            "window_h": int(card.height()),
            # Microphone setup is a user preference too: reopening Setup on
            # every launch re-selected the system default and undid the
            # calibration the user had already done.
            "device_index": (int(self._device_index)
                             if self._device_index is not None else None),
            "noise_gate": float(self._noise_gate),
            "input_gain": float(self._input_gain),
        }

    def _restore_settings(self):
        """Apply saved preferences; unknown or invalid values are ignored.

        Runs once per launch. A second call would overwrite whatever the user
        has changed since the file was read, so a window that is hidden and
        shown again keeps its live state instead of the saved one.
        """
        if self._prefs_restored:
            return
        self._prefs_restored = True
        data = self._store.load().get("settings") or {}
        if not isinstance(data, dict):
            data = {}

        family = data.get("font_family")
        if isinstance(family, str) and family in {fam for _, fam in self.FONT_OPTIONS}:
            self._font_family = family
        size = sane_coord(data.get("font_size"))
        if size is not None and 6 <= size <= 24:
            self._font_size = size
        if isinstance(data.get("dark_theme"), bool):
            self._dark_theme = data["dark_theme"]
        for key, attr, low, high in (
            ("manual_wpm", "_manual_wpm", 10, 600),
            ("voice_wpm", "_voice_wpm", 10, 600),
        ):
            value = sane_coord(data.get(key))
            if value is not None:
                setattr(self, attr, max(low, min(high, value)))
        if isinstance(data.get("voice_mode"), bool):
            self._voice_mode = data["voice_mode"]

        # Setup values are validated independently: a hand-edited gate of
        # "loud", a NaN gain or a negative device index would otherwise reach
        # the audio stack and fail at the first stream open.
        if "device_index" in data:
            device = data.get("device_index")
            if device is None:
                self._device_index = None
            else:
                index = sane_coord(device)
                if index is not None and index >= 0:
                    self._device_index = index
        gate = sane_number(data.get("noise_gate"), 0.0, 1.0)
        if gate is not None:
            self._noise_gate = gate
        gain = sane_number(data.get("input_gain"), 0.5, 5.0)
        if gain is not None:
            self._input_gain = gain

        # Put the card back where the user left it, at the size they left it,
        # if that spot still exists. A size is only trusted within the range
        # the reader can actually be in; anything else keeps the default, and
        # the clamp shrinks a card saved on a display that is now smaller.
        x = sane_coord(data.get("window_x"))
        y = sane_coord(data.get("window_y"))
        width = sane_coord(data.get("window_w"))
        height = sane_coord(data.get("window_h"))
        if x is not None and y is not None:
            # The reader only ever occupies the reading card or the expanded
            # editor, so those are the real bounds of a saved size. A value
            # outside them is corrupt rather than a window the user chose,
            # and falling back to the default keeps a bad number from turning
            # the card into a full-screen panel.
            max_w = int(860 * self._scale) + self.RAIL_W
            max_h = int(320 * self._scale)
            if width is None or not self.MIN_W <= width <= max_w:
                width = self.DEFAULT_W
            if height is None or not self.MIN_H <= height <= max_h:
                height = self.DEFAULT_H
            card = self._clamp_to_available(QRect(x, y, width, height))
            self.resize(card.size())
            self.move(card.topLeft())
            # The card the reader collapses back to is the restored one, or
            # leaving the editor would snap it to the launch default.
            self._collapsed_size = QSize(card.width(), card.height())
            # The springs start at the restored rectangle too, or the next
            # animation would snap the window back to its launch position.
            self._motion.jump(x=float(card.x()), y=float(card.y()),
                              w=float(card.width()), h=float(card.height()))

        self._text_edit.setFont(self._text_font())
        self._apply_read_mode_style()
        if self._script_text:
            self._refresh_lines()
            self._restore_reading_view(None)
        self.update()

    def _connect_lifecycle(self) -> None:
        """Flush state on every exit path and react to display changes."""
        app = QApplication.instance()
        if app is None:
            return
        app.aboutToQuit.connect(self._save_state)
        self._watched_screens = []
        self._watch_screens()
        # Windows sends a commit request for logoff and shutdown, before the
        # process is torn down. That is the last chance to flush the debounce.
        for name, slot in (
            ("commitDataRequest", self._on_commit_data_request),
            ("saveStateRequest", self._on_save_state_request),
            ("screenAdded", self._on_screen_added),
            ("screenRemoved", self._on_screens_changed),
        ):
            signal = getattr(app, name, None)
            if signal is not None:
                signal.connect(slot)

    def _watch_screens(self) -> None:
        """Follow work-area changes too, not just monitors being plugged in.

        A resolution or taskbar change does not add or remove a screen, so the
        application-level signals never fire for it. Each existing screen has
        to be watched directly, and every newly added screen as well.
        """
        for screen in QApplication.screens():
            if screen in self._watched_screens:
                continue
            self._watched_screens.append(screen)
            for name in ("availableGeometryChanged", "geometryChanged"):
                signal = getattr(screen, name, None)
                if signal is not None:
                    signal.connect(self._on_screens_changed)

    def _on_screen_added(self, screen=None) -> None:
        self._watch_screens()
        self._on_screens_changed()

    def _on_commit_data_request(self, manager=None) -> None:
        self._save_state()

    def _on_save_state_request(self, manager=None) -> None:
        self._save_state()

    def _on_screens_changed(self, *_: object) -> None:
        """Pull windows back after a monitor is added, removed or resized."""
        if not self.isVisible():
            return
        current = QRect(self.geometry())
        safe = self._clamp_to_available(current)
        if safe != current:
            self.setGeometry(safe)
            self._motion.jump(x=float(safe.x()), y=float(safe.y()),
                              w=float(safe.width()), h=float(safe.height()))
            if self._peeked and self._peek_restore is not None:
                self._peek_restore = self._clamp_to_available(
                    QRect(safe.topLeft(), self._peek_restore.size()))
            # The shapes the reader returns to have to fit the new display
            # too, or leaving the editor or the narrow reader lands off-screen.
            if self._size_before_compact is not None:
                self._size_before_compact = self._clamp_to_available(
                    QRect(safe.topLeft(), self._size_before_compact)).size()
            if self._collapsed_size is not None:
                self._collapsed_size = self._clamp_to_available(
                    QRect(safe.topLeft(), self._collapsed_size)).size()
        for note in list(self._notes):
            rect = QRect(note.pos(), note.size())
            safe_note = self._clamp_to_available(rect)
            if safe_note != rect:
                note.move(safe_note.topLeft())
        self._schedule_save()

    def _schedule_save(self):
        """Write the state once the user pauses, not on every keystroke."""
        self._save_timer.start()

    def _save_state(self, purge_backup: bool = False):
        """Flush the current state to disk. Safe to call at any time.

        ``purge_backup`` is passed when a note was removed on purpose: the
        crash-recovery copy is written from this state too, so a note the
        reader closed cannot come back if the primary file later fails to
        parse.
        """
        if not self._save_enabled:
            return
        self._save_timer.stop()
        try:
            self._store.save(self._script_text, self._note_state(),
                             self._settings_state(), purge_backup=purge_backup)
        except OSError as exc:
            print(f"[Notch] Could not save state: {exc}", flush=True)

    def _restore_state(self):
        if self._state_restored:
            return
        self._state_restored = True
        state = self._store.load()

        script = state.get("script") or ""
        if script:
            self._text_edit.setPlainText(script)
            self._spaced_key = None
            self._text_edit.setAlignment(Qt.AlignCenter)
            self._script_text = script
            self._matching.load_script(script)
            self._highlight_key = None
            self._focus_span = None
            self._line_spans = []
            self._current_line = 0
            self._confirmed_word = -1
            self._partial_word = -1
            self._apply_highlight(-1)
            self._refresh_lines()
            self._text_edit.moveCursor(QTextCursor.Start)
            self._text_edit.verticalScrollBar().setValue(0)
            if self._voice_mode:
                self._scroll_to_reading_line(0)
            print(f"[Notch] Restored saved script ({len(script)} chars)", flush=True)

        for entry in state.get("notes") or []:
            # A container where the text belongs is corrupt data, not a note.
            # Coercing it with str() used to restore "{'a': 1}" as if the user
            # had typed it.
            text = entry.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            raw_id = entry.get("id")
            note = NoteWindow(text=text,
                              note_id=(raw_id if isinstance(raw_id, str) and raw_id
                                       else None),
                              scale=self._scale, dark=self._dark_theme)
            # Coordinates go through sane_coord: JSON can hold a float, a
            # boolean or a number past QRect's 32-bit range, and the last one
            # raises OverflowError, which is not a ValueError and used to take
            # the whole launch down.
            note_x = sane_coord(entry.get("x"))
            note_y = sane_coord(entry.get("y"))
            if note_x is None or note_y is None:
                saved = QRect(self.x() + self.width() + int(12 * self._scale),
                              self.y(), note.width(), note.height())
            else:
                saved = QRect(note_x, note_y, note.width(), note.height())
            note.move(self._clamp_to_available(saved).topLeft())
            note.changed.connect(self._schedule_save)
            note.closed.connect(self._on_note_closed)
            self._notes.append(note)
            note.show()

        if self._notes:
            print(f"[Notch] Restored {len(self._notes)} note(s)", flush=True)

    def closeEvent(self, event):
        self._save_state()
        # Notes are about to be cleared. A later shutdown flush must not write
        # that empty list over the state just saved.
        self._save_enabled = False
        self._close_all_notes()
        self._scroll_timer.stop()
        self._stop_listening()
        super().closeEvent(event)
        QApplication.quit()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = NotchOverlay()
    window.show()
    sys.exit(app.exec_())