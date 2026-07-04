"""
memory_tab.py

"Memory Operation" - Data Storage command (Section 11 of the IDD, cmd
0x22), NVM Write only, for exactly the two data types actually implemented
in the real FPGA (flash_spi.vhd): Manufacturing data and TRM Configuration.

This writes to a QTRM's non-volatile flash - permanent, one QTRM at a time
(deliberately no "send to all 96" option, unlike Dwell). Two safety gates
per Yuvraj's explicit ask: this tab requires a password to open (see
main_window.py's tab-change handler), and every Write is behind its own
confirmation dialog naming exactly what will be overwritten.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from header_panel import HeaderPanel
from packet import MEM_DATA_TYPE_MANUFACTURING, MEM_DATA_TYPE_TRM_CONFIGURATION
from segmented_control import SegmentedControl
from spin_field import SpinField

_WRITE_COLOR = "#d64545"
_WRITE_HOVER_COLOR = "#e15b5b"
_WRITE_PRESSED_COLOR = "#b83a3a"
_PENDING_COLOR = "rgb(160, 165, 172)"
_ACKED_COLOR = "rgb(146, 208, 165)"
_NOT_ACKED_COLOR = "rgb(240, 149, 149)"
_STATE_TEXT_COLOR = "#1f2328"


def _send_button_style(bg_color: str = None) -> str:
    if bg_color is None:
        return (
            f"QPushButton {{ background-color: {_WRITE_COLOR}; color: #ffffff; border: none;"
            "border-radius: 12px; font-size: 14px; font-weight: 600; padding: 10px; }"
            f"QPushButton:hover {{ background-color: {_WRITE_HOVER_COLOR}; }}"
            f"QPushButton:pressed {{ background-color: {_WRITE_PRESSED_COLOR}; }}"
        )
    return (
        f"QPushButton {{ background-color: {bg_color}; color: {_STATE_TEXT_COLOR}; border: none;"
        "border-radius: 12px; font-size: 14px; font-weight: 600; padding: 10px; }"
        f"QPushButton:hover {{ background-color: {bg_color}; }}"
        f"QPushButton:pressed {{ background-color: {bg_color}; }}"
    )


class MemoryTab(QWidget):
    # data_type (int), target_qtrm_index (0-based), payload (bytes)
    write_requested = Signal(int, int, bytes)

    def __init__(self, parent=None):
        super().__init__(parent)

        content = QWidget()
        layout = QVBoxLayout(content)

        warning = QLabel(
            "This writes permanently to the target QTRM's non-volatile flash memory."
        )
        warning.setStyleSheet("color: #d64545; font-weight: 600;")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        target_box = QGroupBox("Target")
        target_row = QHBoxLayout(target_box)
        target_row.addWidget(QLabel("QTRM:"))
        self.qtrm_spin = SpinField(1, 96, 1, field_width=76)
        target_row.addWidget(self.qtrm_spin)
        target_row.addStretch(1)
        layout.addWidget(target_box)

        self.data_type_switch = SegmentedControl("Manufacturing", "TRM Configuration")
        self.data_type_switch.toggled.connect(self._on_data_type_toggled)
        layout.addWidget(self.data_type_switch)

        self.mfg_box = self._build_mfg_group()
        layout.addWidget(self.mfg_box)

        self.trm_config_box = self._build_trm_config_group()
        layout.addWidget(self.trm_config_box)
        self.trm_config_box.setVisible(False)

        send_row = QHBoxLayout()
        self.send_btn = QPushButton("Write to NVM")
        self.send_btn.setStyleSheet(_send_button_style())
        self.send_btn.clicked.connect(self._on_send_clicked)
        send_row.addWidget(self.send_btn)

        self.status_label = QLabel("Not yet sent")
        self.response_time_label = QLabel("")
        send_row.addWidget(self.status_label)
        send_row.addWidget(self.response_time_label)
        send_row.addStretch(1)
        layout.addLayout(send_row)
        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)

        self.header_panel = HeaderPanel()

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll, 1)
        outer.addWidget(self.header_panel)

    def _build_mfg_group(self):
        box = QGroupBox("Manufacturing Data")
        form = QFormLayout(box)
        self.agency_id_spin = SpinField(0, 15, 0, field_width=64)
        self.fw_version_spin = SpinField(0, 15, 0, field_width=64)
        self.serial_number_spin = SpinField(0, 65535, 0, field_width=80)
        form.addRow("Mfg. Agency ID (0-15):", self.agency_id_spin)
        form.addRow("Firmware Version (0-15):", self.fw_version_spin)
        form.addRow("Serial Number:", self.serial_number_spin)
        return box

    def _build_trm_config_group(self):
        box = QGroupBox("TRM Configuration")
        form = QFormLayout(box)
        self.temp_cutoff_en_check = QCheckBox("Temp Cutoff Enable")
        self.temp_cutoff_spin = SpinField(0, 255, 0, field_width=64)
        form.addRow(self.temp_cutoff_en_check)
        form.addRow("Temp Cutoff (deg C, 0-255):", self.temp_cutoff_spin)
        return box

    def _on_data_type_toggled(self, is_trm_config: bool):
        self.mfg_box.setVisible(not is_trm_config)
        self.trm_config_box.setVisible(is_trm_config)

    def _current_data_type(self) -> int:
        return MEM_DATA_TYPE_TRM_CONFIGURATION if self.data_type_switch.isChecked() else MEM_DATA_TYPE_MANUFACTURING

    def _current_payload(self) -> bytes:
        if self.data_type_switch.isChecked():
            en_byte = 1 if self.temp_cutoff_en_check.isChecked() else 0
            return bytes([en_byte, self.temp_cutoff_spin.value()])
        agency_fw_byte = ((self.agency_id_spin.value() & 0x0F) << 4) | (self.fw_version_spin.value() & 0x0F)
        serial = self.serial_number_spin.value()
        return bytes([agency_fw_byte, serial & 0xFF, (serial >> 8) & 0xFF])

    def _current_description(self) -> str:
        qtrm_index = self.qtrm_spin.value() - 1
        if self.data_type_switch.isChecked():
            return (
                f"QTRM-{qtrm_index}: TRM Configuration - "
                f"Temp Cutoff {'Enabled' if self.temp_cutoff_en_check.isChecked() else 'Disabled'}, "
                f"{self.temp_cutoff_spin.value()} deg C"
            )
        return (
            f"QTRM-{qtrm_index}: Manufacturing Data - Agency ID {self.agency_id_spin.value()}, "
            f"FW Version {self.fw_version_spin.value()}, Serial {self.serial_number_spin.value()}"
        )

    def _on_send_clicked(self):
        reply = QMessageBox.warning(
            self, "Confirm NVM Write",
            f"This permanently overwrites flash memory on real hardware:\n\n{self._current_description()}\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        qtrm_index = self.qtrm_spin.value() - 1
        self.write_requested.emit(self._current_data_type(), qtrm_index, self._current_payload())

    def mark_pending(self):
        self.status_label.setText("Sent - waiting for response...")
        self.response_time_label.setText("")
        self.send_btn.setEnabled(False)
        self.send_btn.setStyleSheet(_send_button_style(_PENDING_COLOR))

    def show_result(self, qtrm_index: int, acked: bool):
        self.send_btn.setEnabled(True)
        self.send_btn.setStyleSheet(_send_button_style(_ACKED_COLOR if acked else _NOT_ACKED_COLOR))
        self.status_label.setText(
            f"QTRM-{qtrm_index}: {'Acknowledged' if acked else 'Not acknowledged'}"
        )

    def show_response_time(self, microseconds: float):
        self.response_time_label.setText(f"{microseconds:.0f} µs")

    def show_no_response(self, qtrm_index: int):
        self.send_btn.setEnabled(True)
        self.send_btn.setStyleSheet(_send_button_style(_NOT_ACKED_COLOR))
        self.status_label.setText(f"QTRM-{qtrm_index}: No response")
        self.response_time_label.setText("")
