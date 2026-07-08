"""
memory_tab.py

"Memory Operation" - Data Storage command (Section 11 of the IDD, cmd
0x22), NVM Write only, for exactly the two data types actually implemented
in the real FPGA (flash_spi.vhd): Manufacturing data and TRM Configuration.

This writes to a QTRM's non-volatile flash. Manufacturing data is
inherently per-unit (agency ID/serial number), so it's single-QTRM-target
only. TRM Configuration's Temp Cutoff is a uniform array-wide setting, so
it additionally gets a "Write to All 96 QTRMs" button alongside the
single-target one, per explicit ask. Two safety gates apply regardless of
target: this tab requires a password to open (see main_window.py's
tab-change handler), and every Write - individual or all - is behind its
own confirmation dialog naming exactly what will be overwritten.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QFormLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from core.command_style import PENDING_COLOR as _PENDING_COLOR
from core.command_style import SUCCESS_COLOR as _ACKED_COLOR
from core.command_style import FAILURE_COLOR as _NOT_ACKED_COLOR
from core.command_style import WRITE_COLOR, WRITE_HOVER_COLOR, WRITE_PRESSED_COLOR
from core.command_style import indicator_style as _indicator_style
from core.command_style import send_button_style
from core.packet import MEM_DATA_TYPE_MANUFACTURING, MEM_DATA_TYPE_TRM_CONFIGURATION
from widgets.segmented_control import SegmentedControl
from widgets.spin_field import SpinField
from widgets.titled_group import titled_group_box

# Deliberately red, not the shared purple every other command tab's send
# button uses - this writes permanently to real hardware's flash memory,
# so it should never look like "just another send". The button itself
# never recolors to reflect a result (that's what the separate status
# indicators below it are for) - matches every other command tab fixed
# after this file used to recolor the button directly and get stuck
# green/red forever with no working hover effect.
_WRITE_BTN_STYLE = send_button_style(
    color=WRITE_COLOR, hover=WRITE_HOVER_COLOR, pressed=WRITE_PRESSED_COLOR,
    radius=12, font_size_px=14, padding="10px",
)


class MemoryTab(QWidget):
    # data_type (int), target_qtrm_index (0-based), payload (bytes)
    write_requested = Signal(int, int, bytes)
    # data_type (int), payload (bytes) - all 96 QTRMs
    write_all_requested = Signal(int, bytes)

    def __init__(self, parent=None):
        super().__init__(parent)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        warning = QLabel(
            "This writes permanently to the target QTRM's non-volatile flash memory."
        )
        warning.setStyleSheet("color: #d64545; font-weight: 600;")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        target_box, target_outer = titled_group_box("Target")
        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("QTRM:"))
        self.qtrm_spin = SpinField(0, 95, 0, field_width=76)
        target_row.addWidget(self.qtrm_spin)
        target_row.addStretch(1)
        target_outer.addLayout(target_row)
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
        self.send_btn.setStyleSheet(_WRITE_BTN_STYLE)
        self.send_btn.clicked.connect(self._on_send_clicked)
        send_row.addWidget(self.send_btn)

        self.write_all_btn = QPushButton("Write to All 96 QTRMs")
        self.write_all_btn.setStyleSheet(_WRITE_BTN_STYLE)
        self.write_all_btn.clicked.connect(self._on_write_all_clicked)
        self.write_all_btn.setVisible(False)
        send_row.addWidget(self.write_all_btn)

        self.send_indicator = QLabel("Not sent yet")
        self.send_indicator.setStyleSheet(_indicator_style())
        send_row.addWidget(self.send_indicator)

        self.write_all_indicator = QLabel("Not sent yet")
        self.write_all_indicator.setStyleSheet(_indicator_style())
        self.write_all_indicator.setVisible(False)
        send_row.addWidget(self.write_all_indicator)

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

        # HeaderPanel is now a single global full-height sidebar owned by
        # main_window.py, not embedded per-tab - see its module docstring.
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _build_mfg_group(self):
        box, outer = titled_group_box("Manufacturing Data")
        form = QFormLayout()
        self.agency_id_spin = SpinField(0, 15, 0, field_width=64)
        self.fw_version_spin = SpinField(0, 15, 0, field_width=64)
        self.serial_number_spin = SpinField(0, 65535, 0, field_width=80)
        form.addRow("Mfg. Agency ID (0-15):", self.agency_id_spin)
        form.addRow("Firmware Version (0-15):", self.fw_version_spin)
        form.addRow("Serial Number:", self.serial_number_spin)
        outer.addLayout(form)
        return box

    def _build_trm_config_group(self):
        box, outer = titled_group_box("TRM Configuration")
        form = QFormLayout()
        self.temp_cutoff_en_check = QCheckBox("Temp Cutoff Enable")
        self.temp_cutoff_spin = SpinField(0, 255, 0, field_width=64)
        form.addRow(self.temp_cutoff_en_check)
        form.addRow("Temp Cutoff (deg C, 0-255):", self.temp_cutoff_spin)
        outer.addLayout(form)
        return box

    def _on_data_type_toggled(self, is_trm_config: bool):
        self.mfg_box.setVisible(not is_trm_config)
        self.trm_config_box.setVisible(is_trm_config)
        self.write_all_btn.setVisible(is_trm_config)
        self.write_all_indicator.setVisible(is_trm_config)
        self.send_btn.setText("Write to Selected QTRM" if is_trm_config else "Write to NVM")

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
        qtrm_index = self.qtrm_spin.value()
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

    def _current_description_all(self) -> str:
        return (
            f"ALL 96 QTRMs: TRM Configuration - "
            f"Temp Cutoff {'Enabled' if self.temp_cutoff_en_check.isChecked() else 'Disabled'}, "
            f"{self.temp_cutoff_spin.value()} deg C"
        )

    def _on_send_clicked(self):
        reply = QMessageBox.warning(
            self, "Confirm NVM Write",
            f"This permanently overwrites flash memory on real hardware:\n\n{self._current_description()}\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        qtrm_index = self.qtrm_spin.value()
        self.write_requested.emit(self._current_data_type(), qtrm_index, self._current_payload())

    def _on_write_all_clicked(self):
        reply = QMessageBox.warning(
            self, "Confirm NVM Write - ALL 96 QTRMs",
            f"This permanently overwrites flash memory on ALL 96 QTRMs:\n\n{self._current_description_all()}\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.write_all_requested.emit(self._current_data_type(), self._current_payload())

    def _set_buttons_enabled(self, enabled: bool):
        self.send_btn.setEnabled(enabled)
        self.write_all_btn.setEnabled(enabled)

    def reset_to_idle(self):
        # Without this, an indicator is left showing whatever
        # pending/acked/not-acked state its last send ended in, forever,
        # even after switching tabs away and back.
        self.send_indicator.setText("Not sent yet")
        self.send_indicator.setStyleSheet(_indicator_style())
        self.write_all_indicator.setText("Not sent yet")
        self.write_all_indicator.setStyleSheet(_indicator_style())

    def mark_pending(self):
        self.status_label.setText("Sent - waiting for response...")
        self.response_time_label.setText("")
        self._set_buttons_enabled(False)
        self.send_indicator.setText("Sending...")
        self.send_indicator.setStyleSheet(_indicator_style(_PENDING_COLOR))

    def show_result(self, qtrm_index: int, acked: bool):
        self._set_buttons_enabled(True)
        self.send_indicator.setText("Acknowledged" if acked else "Not Acknowledged")
        self.send_indicator.setStyleSheet(_indicator_style(_ACKED_COLOR if acked else _NOT_ACKED_COLOR))
        self.status_label.setText(
            f"QTRM-{qtrm_index}: {'Acknowledged' if acked else 'Not acknowledged'}"
        )

    def show_response_time(self, microseconds: float):
        self.response_time_label.setText(f"{microseconds:.0f} µs")

    def show_no_response(self, qtrm_index: int):
        self._set_buttons_enabled(True)
        self.send_indicator.setText("No Response")
        self.send_indicator.setStyleSheet(_indicator_style(_NOT_ACKED_COLOR))
        self.status_label.setText(f"QTRM-{qtrm_index}: No response")
        self.response_time_label.setText("")

    def mark_all_pending(self):
        self.status_label.setText("Sent to all 96 - waiting for response...")
        self.response_time_label.setText("")
        self._set_buttons_enabled(False)
        self.write_all_indicator.setText("Sending...")
        self.write_all_indicator.setStyleSheet(_indicator_style(_PENDING_COLOR))

    def show_all_results(self, acked_flags):
        self._set_buttons_enabled(True)
        acked_count = sum(1 for v in acked_flags if v)
        all_acked = acked_count == len(acked_flags)
        self.write_all_indicator.setText(f"{acked_count}/{len(acked_flags)} Acknowledged")
        self.write_all_indicator.setStyleSheet(_indicator_style(_ACKED_COLOR if all_acked else _NOT_ACKED_COLOR))
        self.status_label.setText(f"{acked_count}/{len(acked_flags)} QTRMs acknowledged")

    def show_all_no_response(self):
        self._set_buttons_enabled(True)
        self.write_all_indicator.setText("No Response")
        self.write_all_indicator.setStyleSheet(_indicator_style(_NOT_ACKED_COLOR))
        self.status_label.setText("No response")
        self.response_time_label.setText("")
