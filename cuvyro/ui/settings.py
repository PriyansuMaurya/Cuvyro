"""
cuvyro.ui.settings — Microphone and audio configuration dialog.

Provides:
  • Dropdown of available input devices (via sounddevice).
  • Live VU meter showing microphone input level.
  • Noise-gate and input gain sliders.
"""

import os
from typing import Optional

import numpy as np
import sounddevice as sd

from PyQt5.QtCore import Qt, QTimer, QRectF, QRect, QPropertyAnimation, QEasingCurve, QAbstractAnimation
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QCheckBox,
    QSlider, QProgressBar, QPushButton, QGroupBox, QMessageBox,
)

from cuvyro.ui import motion


from cuvyro.platform import hotkeys as hk
from cuvyro.platform.capture import exclude_from_capture
from cuvyro.ui import tooltips

class SettingsDialog(QDialog):
    """Modal dialog for microphone and audio settings."""

    CORNER_RADIUS = 16

    def showEvent(self, event):
        super().showEvent(event)
        # Settings shows device names and the model path; it belongs to the
        # same capture policy as the reader and the notes.
        exclude_from_capture(self)

    def __init__(self, current_device: Optional[int] = None,
                 current_noise_gate: float = 0.01,
                 current_input_gain: float = 2.0,
                 model_path: str = "model",
                 parent=None, dark: bool = True,
                 hotkeys_enabled: bool = False,
                 hotkey_toggle: str = hk.DEFAULT_CHORDS["toggle"],
                 hotkey_status: Optional[dict] = None):
        super().__init__(parent)
        self.setWindowTitle("Cuvyro Settings")
        self.setAccessibleName("Cuvyro settings")
        self._dark = dark
        self.setMinimumSize(320, 200)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet(self._stylesheet(dark))
        # Setup can be built on its own (the robustness checks do), so this
        # surface installs the tooltip layer too; ``install`` is idempotent.
        tooltips.install().dismiss(immediate=True)

        self._selected_device: Optional[int] = current_device
        self._noise_gate = current_noise_gate
        self._input_gain = current_input_gain
        self._model_path = model_path
        self._hotkeys_enabled = bool(hotkeys_enabled)
        self._hotkey_toggle = (hotkey_toggle
                               if hotkey_toggle in hk.TOGGLE_CHOICES
                               else hk.DEFAULT_CHORDS["toggle"])
        self._hotkey_status = dict(hotkey_status or {})
        self._hotkeys_initial = (self._hotkeys_enabled, self._hotkey_toggle)

        # VU meter stream
        self._vu_stream = None
        self._vu_timer = QTimer(self)
        self._vu_timer.setInterval(50)
        self._vu_timer.timeout.connect(self._update_vu)
        self._volume_value = 0.0

        self._drag_pos = None
        self._close_pending = None
        self._opacity_anim = QPropertyAnimation(self, b"windowOpacity")
        self._opacity_anim.setDuration(int(motion.WINDOW_FADE * 1000))
        self._opacity_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._opacity_anim.setStartValue(0.0)
        self._opacity_anim.setEndValue(1.0)
        self._fade_out_anim = QPropertyAnimation(self, b"windowOpacity")
        self._fade_out_anim.setDuration(int(motion.WINDOW_FADE * 1000))
        self._fade_out_anim.setEasingCurve(QEasingCurve.InCubic)
        self._fade_out_anim.setStartValue(1.0)
        self._fade_out_anim.setEndValue(0.0)
        self._fade_out_anim.finished.connect(self._on_fade_out_finished)
        self._build_ui()
        self._populate_devices()
        # Opening a microphone stream can take a few hundred milliseconds.
        # Let the dialog paint first, then start the meter, so Setup appears
        # immediately instead of arriving after the audio stack hesitates.
        self._vu_start_timer = QTimer(self)
        self._vu_start_timer.setSingleShot(True)
        self._vu_start_timer.timeout.connect(self._start_vu_stream)
        self._vu_start_timer.start(0)
        self.adjustSize()
        if parent and parent.isVisible() and parent.width() > 0:
            self.setFixedWidth(parent.width() + 48)

    # ── UI construction ──────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 16, 18, 14)

        # Header
        header = QLabel("Settings")
        header.setStyleSheet(
            "font-size: 13px; font-weight: 600; "
            f"color: {'#ffffff' if self._dark else '#111111'}; background: transparent;")
        layout.addWidget(header)

        # ── Microphone group ─────────────────────────────────────────
        mic_group = QGroupBox("Microphone")
        mic_layout = QVBoxLayout(mic_group)

        lbl = QLabel("Input Device:")
        lbl.setFont(QFont("Segoe UI", 10))
        mic_layout.addWidget(lbl)

        self._device_combo = QComboBox()
        self._device_combo.setAccessibleName("Input device")
        lbl.setBuddy(self._device_combo)
        self._device_combo.currentIndexChanged.connect(self._on_device_changed)
        mic_layout.addWidget(self._device_combo)

        # Device availability is answered when the stream actually opens. The
        # old probe opened and closed a stream on every input device while the
        # dialog was being constructed, which froze Setup for most of a second
        # on a machine with a handful of microphones. Failures now show here
        # instead of disappearing.
        self._device_status = QLabel("")
        self._device_status.setWordWrap(True)
        self._device_status.setStyleSheet(
            f"color: {'#FF6B6B' if self._dark else '#C0392B'}; font-size: 9px;")
        self._device_status.hide()
        mic_layout.addWidget(self._device_status)

        # VU meter
        vu_lbl = QLabel("Level:")
        vu_lbl.setFont(QFont("Segoe UI", 9))
        mic_layout.addWidget(vu_lbl)

        self._vu_bar = QProgressBar()
        self._vu_bar.setAccessibleName("Microphone level")
        vu_lbl.setBuddy(self._vu_bar)
        self._vu_bar.setRange(0, 100)
        self._vu_bar.setTextVisible(False)
        self._vu_bar.setFixedHeight(18)
        mic_layout.addWidget(self._vu_bar)

        # Noise gate
        gate_row = QHBoxLayout()
        gate_lbl = QLabel("Noise Gate:")
        gate_lbl.setFont(QFont("Segoe UI", 9))
        gate_row.addWidget(gate_lbl)

        self._gate_slider = QSlider(Qt.Horizontal)
        self._gate_slider.setRange(0, 100)
        self._gate_slider.setValue(int(self._noise_gate * 100))
        self._gate_slider.setAccessibleName("Noise gate")
        gate_lbl.setBuddy(self._gate_slider)
        self._gate_slider.valueChanged.connect(self._on_gate_changed)
        self._gate_slider.setToolTip(
            "Recommended 0.01–0.02; filters background noise")
        gate_row.addWidget(self._gate_slider)

        self._gate_value_lbl = QLabel(f"{self._noise_gate:.2f}")
        self._gate_value_lbl.setFixedWidth(40)
        gate_row.addWidget(self._gate_value_lbl)
        mic_layout.addLayout(gate_row)

        # Input gain
        gain_row = QHBoxLayout()
        gain_lbl = QLabel("Input Gain:")
        gain_lbl.setFont(QFont("Segoe UI", 9))
        gain_row.addWidget(gain_lbl)

        self._gain_slider = QSlider(Qt.Horizontal)
        self._gain_slider.setRange(50, 500)  # 0.50x - 5.00x
        self._gain_slider.setValue(int(self._input_gain * 100))
        self._gain_slider.setAccessibleName("Input gain")
        gain_lbl.setBuddy(self._gain_slider)
        self._gain_slider.valueChanged.connect(self._on_gain_changed)
        self._gain_slider.setToolTip(
            "Recommended 2.0x; suits most microphones")
        gain_row.addWidget(self._gain_slider)

        self._gain_value_lbl = QLabel(f"{self._input_gain:.2f}x")
        self._gain_value_lbl.setFixedWidth(50)
        gain_row.addWidget(self._gain_value_lbl)
        mic_layout.addLayout(gain_row)

        # Recommended settings (animate-ui style: subtle chip + action)
        rec_row = QHBoxLayout()
        rec_row.addStretch()
        rec_btn = QPushButton("Use recommended")
        rec = "#64B5F6" if self._dark else "#0066CC"
        rec_wash = "rgba(100, 181, 246, 0.15)" if self._dark else "rgba(0, 122, 255, 0.10)"
        rec_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {rec};
                border: 1px solid {rec};
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 10px;
            }}
            QPushButton:hover {{ background: {rec_wash}; }}
        """)
        rec_btn.setCursor(Qt.PointingHandCursor)
        rec_btn.clicked.connect(self._apply_recommended)
        rec_row.addWidget(rec_btn)
        mic_layout.addLayout(rec_row)

        layout.addWidget(mic_group)

        # ── Global keys ──────────────────────────────────────────────
        # Manual mode's system-wide controls. The checkbox is the consent:
        # nothing is registered while it is off, and turning it on here is
        # the same explicit choice as the menu's Global keys row.
        keys_group = QGroupBox("Global keys")
        keys_layout = QVBoxLayout(keys_group)
        keys_layout.setSpacing(6)

        self._hotkeys_check = QCheckBox(
            "Control Cuvyro while another app has focus")
        self._hotkeys_check.setAccessibleName("Global keys")
        self._hotkeys_check.setChecked(self._hotkeys_enabled)
        self._hotkeys_check.setEnabled(hk.available())
        keys_layout.addWidget(self._hotkeys_check)

        key_row = QHBoxLayout()
        key_lbl = QLabel("Start / pause:")
        key_lbl.setFont(QFont("Segoe UI", 9))
        self._hotkey_combo = QComboBox()
        self._hotkey_combo.setAccessibleName("Play pause shortcut")
        for chord in hk.TOGGLE_CHOICES:
            self._hotkey_combo.addItem(chord)
        self._hotkey_combo.setCurrentText(self._hotkey_toggle)
        self._hotkey_combo.setEnabled(self._hotkeys_enabled and hk.available())
        key_lbl.setBuddy(self._hotkey_combo)
        key_row.addWidget(key_lbl)
        key_row.addWidget(self._hotkey_combo, 1)
        keys_layout.addLayout(key_row)

        self._hotkey_status_lbl = QLabel(self._hotkey_status_text())
        self._hotkey_status_lbl.setWordWrap(True)
        self._hotkey_status_lbl.setStyleSheet(
            f"color: {'#888888' if self._dark else '#666666'}; font-size: 9px;")
        keys_layout.addWidget(self._hotkey_status_lbl)
        speed_note = QLabel("Slower / faster: Ctrl+Alt+Left / Right")
        speed_note.setStyleSheet(
            f"color: {'#888888' if self._dark else '#666666'}; font-size: 9px;")
        keys_layout.addWidget(speed_note)

        self._hotkeys_check.toggled.connect(self._on_hotkeys_toggled)
        self._hotkey_combo.currentTextChanged.connect(
            self._on_hotkey_toggle_changed)
        layout.addWidget(keys_group)

        # ── Speech data section (hidden) ──────────────────────────────
        # model_group = QGroupBox("Speech data")
        # model_layout = QVBoxLayout(model_group)
        # model_layout.setContentsMargins(10, 6, 10, 6)
        # model_layout.setSpacing(2)
        # model_found = os.path.isdir(self._model_path)
        # status_text = ("Model found" if model_found
        #                else "Model NOT found — place in ./model")
        # color = "#4caf50" if model_found else "#f44336"
        # self._model_status = QLabel(f'<span style="color:{color}">{status_text}</span>')
        # self._model_status.setFont(QFont("Segoe UI", 9))
        # model_layout.addWidget(self._model_status)
        # hint = QLabel(
        #     "Download the required speech data and extract to <code>./model</code>"
        # )
        # hint.setOpenExternalLinks(True)
        # hint.setWordWrap(True)
        # hint.setFont(QFont("Segoe UI", 8))
        # hint.setMaximumWidth(280)
        # model_layout.addWidget(hint)
        # model_row = QHBoxLayout()
        # model_row.addWidget(model_group, 0, Qt.AlignLeft)
        # model_row.addStretch()
        # layout.addLayout(model_row)

        # ── Buttons ──────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        ok_btn = QPushButton("OK")
        # The dialog has one action that moves the session forward, so it is
        # the accent action, the same blue as the reader's Done. Enter also
        # accepts, which the neutral pair of buttons never made obvious.
        ok_btn.setObjectName("primary")
        ok_btn.setDefault(True)
        ok_btn.setFixedWidth(90)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFixedWidth(90)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        layout.addLayout(btn_row)

    # ── Device list ──────────────────────────────────────────────────

    @staticmethod
    def _is_likely_mic(dev: dict, name: str) -> bool:
        """Exclude system/virtual/output entries; keep real mic inputs."""
        n = name.lower()
        if any(x in n for x in ("speaker", "output", "playback", "stereo mix",
                                "sound mapper", "primary sound capture")):
            return False
        return True

    def _populate_devices(self):
        self._device_combo.blockSignals(True)
        self._device_combo.clear()
        self._device_combo.addItem("System Default", None)
        devices = sd.query_devices()
        candidates = []
        for i, dev in enumerate(devices):
            if dev["max_input_channels"] <= 0:
                continue
            name = dev.get("name", "")
            if not self._is_likely_mic(dev, name):
                continue
            # A dedicated input device reads better than a duplex one, but
            # both are listed: whether the device can open is reported by the
            # VU stream, where the user is already looking.
            if dev["max_output_channels"] == 0:
                candidates.append((i, dev, True))
            else:
                candidates.append((i, dev, False))
        candidates.sort(key=lambda x: (not x[2], x[1].get("name", "").lower()))
        for i, dev, _ in candidates:
            name = f'{dev["name"]}  (#{i})'
            self._device_combo.addItem(name, i)
            if i == self._selected_device:
                self._device_combo.setCurrentIndex(self._device_combo.count() - 1)
        self._device_combo.blockSignals(False)

    def _on_device_changed(self, idx):
        self._selected_device = self._device_combo.currentData()
        self._start_vu_stream()

    # ── Noise gate ───────────────────────────────────────────────────

    def _on_gate_changed(self, value):
        self._noise_gate = value / 100.0
        self._gate_value_lbl.setText(f"{self._noise_gate:.2f}")

    def _apply_recommended(self):
        """Apply recommended Noise Gate (0.01) and Input Gain (2.0x)."""
        self._gate_slider.setValue(1)   # 0.01
        self._gain_slider.setValue(200)  # 2.0x
        self._noise_gate = 0.01
        self._input_gain = 2.0
        self._gate_value_lbl.setText("0.01")
        self._gain_value_lbl.setText("2.00x")

    def _on_gain_changed(self, value):
        self._input_gain = value / 100.0
        self._gain_value_lbl.setText(f"{self._input_gain:.2f}x")

    # ── Live VU meter ────────────────────────────────────────────────

    def _start_vu_stream(self):
        self._stop_vu_stream()
        try:
            self._vu_stream = sd.InputStream(
                samplerate=16000,
                blocksize=1024,
                channels=1,
                dtype="int16",
                device=self._selected_device,
                callback=self._vu_callback,
            )
            self._vu_stream.start()
            self._vu_timer.start()
            self._device_status.hide()
        except Exception:
            self._vu_bar.setValue(0)
            self._device_status.setText(
                "Could not open this microphone. Choose another device.")
            self._device_status.show()

    def _stop_vu_stream(self):
        if hasattr(self, "_vu_start_timer"):
            self._vu_start_timer.stop()
        self._vu_timer.stop()
        if self._vu_stream is not None:
            try:
                self._vu_stream.stop()
                self._vu_stream.close()
            except Exception:
                pass
            self._vu_stream = None

    def _vu_callback(self, indata, frames, time_info, status):
        samples = np.frombuffer(indata, dtype=np.int16).astype(np.float32)
        rms = np.sqrt(np.mean(samples ** 2)) / 32768.0
        rms = min(rms * self._input_gain, 1.0)
        self._volume_value = min(rms * 5.0, 1.0)

    def _update_vu(self):
        self._vu_bar.setValue(int(self._volume_value * 100))

    # ── Results ──────────────────────────────────────────────────────

    @property
    def selected_device(self) -> Optional[int]:
        return self._selected_device

    @property
    def noise_gate(self) -> float:
        return self._noise_gate

    @property
    def input_gain(self) -> float:
        return self._input_gain

    @property
    def hotkeys_enabled(self) -> bool:
        return self._hotkeys_enabled

    @property
    def hotkey_toggle(self) -> str:
        return self._hotkey_toggle

    @property
    def hotkeys_changed(self) -> bool:
        return (self._hotkeys_enabled, self._hotkey_toggle) != self._hotkeys_initial

    def _hotkey_status_text(self) -> str:
        if not hk.available():
            return "System-wide keys are only available on Windows."
        if not self._hotkeys_enabled:
            return "Off. Space and the arrow keys work while Cuvyro has focus."
        if self._hotkey_status.get("toggle"):
            return "Registered. These keys work even when Cuvyro is not focused."
        if not self._hotkey_status:
            return "Applies in Manual mode."
        return ("Could not register; another application may be using the "
                "combination. Choose another one.")

    def _on_hotkeys_toggled(self, checked: bool):
        self._hotkeys_enabled = bool(checked)
        self._hotkey_combo.setEnabled(checked and hk.available())

    def _on_hotkey_toggle_changed(self, chord: str):
        self._hotkey_toggle = chord

    # ── Cleanup ──────────────────────────────────────────────────────

    def _on_fade_out_finished(self):
        pending = self._close_pending
        self._close_pending = None
        if pending == "accept":
            self._stop_vu_stream()
            super(SettingsDialog, self).accept()
        elif pending == "reject":
            self._stop_vu_stream()
            super(SettingsDialog, self).reject()

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

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parent()
        if parent and parent.isVisible():
            # Position centered below Cuvyro overlay (no slide, fade-in only)
            gap = 8
            parent_top_left = parent.mapToGlobal(parent.rect().topLeft())
            parent_w = parent.width()
            parent_h = parent.height()
            x = parent_top_left.x() + (parent_w - self.width()) // 2
            y_final = parent_top_left.y() + parent_h + gap
            self.setGeometry(QRect(x, y_final, self.width(), self.height()))
            self.setWindowOpacity(0.0)
            self._opacity_anim.start()
        else:
            # Fallback: center on screen if no parent
            from PyQt5.QtWidgets import QApplication
            screen = QApplication.primaryScreen().availableGeometry()
            x = screen.center().x() - self.width() // 2
            y = screen.center().y() - self.height() // 2
            self.move(x, y)

    # ── Custom Painting (Rounded Dialog) ─────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Draw rounded background. Settings is a surface of the reader, so it
        # follows the same paper/charcoal material in both themes.
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self.CORNER_RADIUS, self.CORNER_RADIUS)

        painter.setPen(QPen(QColor("#333333" if self._dark else "#DCDCDE"), 1))
        painter.setBrush(QColor("#1e1e1e" if self._dark else "#F7F7F8"))
        painter.drawPath(path)

    def mousePressEvent(self, event):
        # Allow dragging the dialog by clicking anywhere
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if hasattr(self, '_drag_pos') and self._drag_pos and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPos() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    # ── Stylesheet ───────────────────────────────────────────────────

    @staticmethod
    def _stylesheet(dark: bool = True) -> str:
        base = """
        QDialog {
            background: transparent;
            color: #b0b0b0;
        }
        QGroupBox {
            border: 1px solid #333;
            border-radius: 10px;
            margin-top: 10px;
            padding: 14px 10px 10px 10px;
            font-family: "Poppins", sans-serif;
            font-weight: 400;
            font-size: 11px;
            color: #888888;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 6px;
        }
        QLabel {
            color: #b0b0b0;
            font-family: "Poppins", sans-serif;
            font-weight: 400;
            font-size: 10px;
        }
        QComboBox {
            background: #2a2a2a;
            color: #b0b0b0;
            border: 1px solid #444;
            border-radius: 6px;
            padding: 4px 8px;
            font-family: "Poppins", sans-serif;
            font-weight: 400;
            font-size: 10px;
        }
        QComboBox QAbstractItemView {
            background: #2a2a2a;
            color: #b0b0b0;
            selection-background-color: #007aff;
        }
        QSlider::groove:horizontal {
            height: 4px;
            background: #3a3a3c;
            border-radius: 2px;
        }
        QSlider::handle:horizontal {
            width: 14px;
            height: 14px;
            margin: -5px 0;
            background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, 
                fx:0.3, fy:0.3, stop:0 #ffffff, stop:1 #8e8e93);
            border-radius: 7px;
            border: 1px solid #555;
        }
        QSlider::handle:horizontal:hover {
            background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, 
                fx:0.3, fy:0.3, stop:0 #ffffff, stop:1 #007aff);
        }
        QProgressBar {
            background: #2a2a2a;
            border: 1px solid #444;
            border-radius: 4px;
        }
        QProgressBar::chunk {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #4cd964, stop:0.7 #ffcc00, stop:1 #ff3b30);
            border-radius: 3px;
        }
        QPushButton {
            background: #2a2a2a;
            color: #b0b0b0;
            border: 1px solid #444;
            border-radius: 8px;
            padding: 6px 14px;
            font-family: "Poppins", sans-serif;
            font-weight: 400;
            font-size: 10px;
        }
        QPushButton:hover {
            background: #007aff;
            color: #ffffff;
            border-color: #007aff;
        }
        QPushButton#primary {
            background: #0066CC;
            color: #ffffff;
            border: 1px solid #0066CC;
        }
        QPushButton#primary:hover {
            background: #0059B3;
            border-color: #0059B3;
        }
        """
        if dark:
            return base
        # Light theme overrides the charcoal base with the same paper palette
        # as the reader, notes and Help. Later rules win in Qt style sheets.
        return base + """
        QDialog { color: #3A3A3C; }
        QGroupBox { border: 1px solid #DCDCDE; color: #6E6E73; }
        QLabel { color: #3A3A3C; }
        QComboBox {
            background: #F2F2F3;
            color: #1C1C1E;
            border: 1px solid #DCDCDE;
        }
        QComboBox QAbstractItemView {
            background: #FFFFFF;
            color: #1C1C1E;
            selection-background-color: #0066CC;
            selection-color: #FFFFFF;
        }
        QSlider::groove:horizontal { background: #D1D1D6; }
        QSlider::handle:horizontal {
            background: qradialgradient(cx:0.5, cy:0.5, radius:0.5,
                fx:0.3, fy:0.3, stop:0 #FFFFFF, stop:1 #C7C7CC);
            border: 1px solid #B0B0B5;
        }
        QSlider::handle:horizontal:hover {
            background: qradialgradient(cx:0.5, cy:0.5, radius:0.5,
                fx:0.3, fy:0.3, stop:0 #FFFFFF, stop:1 #007AFF);
        }
        QProgressBar { background: #E5E5EA; border: 1px solid #DCDCDE; }
        QPushButton {
            background: #F2F2F3;
            color: #1C1C1E;
            border: 1px solid #DCDCDE;
        }
        QPushButton:hover {
            background: #0066CC;
            color: #FFFFFF;
            border-color: #0066CC;
        }
        """
