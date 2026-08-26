"""
burn_test_tab.py

"Burn Test" - fire-and-forget UDP stress test at a fixed, sustained
interval down to 1ms, independent of the app's normal one-command-in-
flight model (every other tab waits for a response before its Send button
can be used again). This is a different, faster kind of "burn test" than
the long/slow gated Link-Test-resend-and-log workflow core/frame_logger.py
already calls by that name - this one hammers the link at a configured
rate and reports achieved throughput/loss/latency live.

This tab is a dumb view, same shape as every other command tab: it emits
start_requested/stop_requested and main_window.py owns the actual
BurnTestWorker/BurnTestLogger lifecycle (safety confirmation, connection
gating, etc.) - see main_window._on_burn_test_start/_on_burn_test_stop.
"""

import os
from datetime import datetime

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from core.command_style import (
    IDLE_MATRIX_RGB, PENDING_RGB, WRITE_COLOR, WRITE_HOVER_COLOR,
    WRITE_PRESSED_COLOR, send_button_style,
)
from tabs.link_test_tab import LedMatrix
from widgets.segmented_control import SegmentedControl
from widgets.spin_field import SpinField

_PENDING_COLOR = QColor(*PENDING_RGB)
_IDLE_COLOR = QColor(*IDLE_MATRIX_RGB)
_START_BTN_STYLE = send_button_style()
_STOP_BTN_STYLE = send_button_style(color=WRITE_COLOR, hover=WRITE_HOVER_COLOR,
                                     pressed=WRITE_PRESSED_COLOR)


class BurnTestTab(QWidget):
    # {"payload": "link_test"|"qcc_status", "interval_s": float, "log_path": str|None}
    start_requested = Signal(dict)
    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        config_row = QHBoxLayout()
        self.payload_control = SegmentedControl("Link Test", "QCC Status (No-Op)")
        self.payload_control.toggled.connect(self._on_payload_toggled)
        config_row.addWidget(self.payload_control)

        config_row.addWidget(QLabel("Interval (ms):"))
        self.interval_spin = SpinField(1, 60000, 10, field_width=80)
        config_row.addWidget(self.interval_spin)
        config_row.addStretch(1)
        layout.addLayout(config_row)

        log_row = QHBoxLayout()
        self.log_checkbox = QCheckBox("Log to CSV")
        self.log_checkbox.setChecked(True)
        self.log_checkbox.toggled.connect(self._on_log_checkbox_toggled)
        log_row.addWidget(self.log_checkbox)

        self.log_path_edit = QLineEdit()
        self.log_path_edit.setReadOnly(True)
        self.log_path_edit.setPlaceholderText("Choose a CSV file…")
        log_row.addWidget(self.log_path_edit, 1)

        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.clicked.connect(self._on_browse_clicked)
        log_row.addWidget(self.browse_btn)
        layout.addLayout(log_row)
        self._set_default_log_path()

        start_row = QHBoxLayout()
        self.start_stop_btn = QPushButton("Start Burn Test")
        self.start_stop_btn.setStyleSheet(_START_BTN_STYLE)
        self.start_stop_btn.clicked.connect(self._on_start_stop_clicked)
        start_row.addWidget(self.start_stop_btn)

        self.stats_label = QLabel("Not running")
        self.stats_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        start_row.addWidget(self.stats_label, 1)
        layout.addLayout(start_row)

        self.led_matrix = LedMatrix(clickable=False)
        layout.addWidget(self.led_matrix, 1)

        # Same QScrollArea-wrapping pattern as link_test_tab.py, so this
        # tab's minimum size stays bounded by the scroll area rather than
        # the 96-cell matrix's natural size.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # -- config helpers ------------------------------------------------------

    def _set_default_log_path(self):
        default_name = datetime.now().strftime("burn_test_%Y%m%d_%H%M%S.csv")
        self.log_path_edit.setText(os.path.join(os.path.expanduser("~"), default_name))

    def _on_payload_toggled(self, is_qcc_status: bool):
        # The LED matrix only means anything for Link Test - QCC Status is
        # a pure no-op with no per-LRU data to show.
        self.led_matrix.setVisible(not is_qcc_status)

    def _on_log_checkbox_toggled(self, checked: bool):
        self.log_path_edit.setEnabled(checked)
        self.browse_btn.setEnabled(checked)

    def _on_browse_clicked(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Burn Test log as", self.log_path_edit.text(),
            "CSV files (*.csv);;All files (*)",
        )
        if path:
            self.log_path_edit.setText(path)

    # -- start/stop ----------------------------------------------------------

    def _on_start_stop_clicked(self):
        if self._running:
            self.stop_requested.emit()
            return
        config = {
            "payload": "qcc_status" if self.payload_control.isChecked() else "link_test",
            "interval_s": self.interval_spin.value() / 1000.0,
            "log_path": self.log_path_edit.text() if self.log_checkbox.isChecked() else None,
        }
        self.start_requested.emit(config)

    def mark_running(self):
        self._running = True
        self.payload_control.setEnabled(False)
        self.interval_spin.setEnabled(False)
        self.log_checkbox.setEnabled(False)
        self.log_path_edit.setEnabled(False)
        self.browse_btn.setEnabled(False)
        self.start_stop_btn.setText("Stop Burn Test")
        self.start_stop_btn.setStyleSheet(_STOP_BTN_STYLE)
        self.stats_label.setText("Starting…")
        self.led_matrix.set_all(_PENDING_COLOR)

    def reset_to_idle(self):
        """Safe to call unconditionally (e.g. on disconnect/close) even when
        no burn test is running - mirrors stop_auto_resend()'s pattern
        elsewhere in the app."""
        self._running = False
        self.payload_control.setEnabled(True)
        self.interval_spin.setEnabled(True)
        self.log_checkbox.setEnabled(True)
        log_enabled = self.log_checkbox.isChecked()
        self.log_path_edit.setEnabled(log_enabled)
        self.browse_btn.setEnabled(log_enabled)
        self.start_stop_btn.setText("Start Burn Test")
        self.start_stop_btn.setStyleSheet(_START_BTN_STYLE)
        self.led_matrix.set_all(_IDLE_COLOR)

    # -- live updates (throttled ~10 Hz by BurnTestWorker) --------------------

    def show_stats(self, stats: dict):
        sent = stats["sent"]
        loss_pct = (100.0 * stats["timeouts"] / sent) if sent else 0.0
        self.stats_label.setText(
            f"Sent: {sent} | OK: {stats['ok']} | Timeout: {stats['timeouts']} | "
            f"Errors: {stats['errors']} | Loss: {loss_pct:.2f}% | "
            f"Rate: {stats['achieved_hz']:.0f} Hz (target {stats['target_hz']:.0f} Hz)"
        )

    def show_link_results(self, flags: list):
        self.led_matrix.set_results(flags)
