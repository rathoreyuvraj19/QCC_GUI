"""
rx_test_app.py

Standalone test receiver - NOT part of the main QCC control GUI. Stands in
for the QCC/QTRM side: listens on a UDP port for the 2970-byte frame the
main window sends out (Host -> QCC direction), and displays exactly what
arrived - the 58-byte QCC header (QCCHeaderRx layout: MSG_ID, MODE,
COMMAND_DATA, CHECKSUM) plus a raw byte-for-byte 96-row QTRM grid (see
raw_slot_model.py - no per-command semantic decoding, just the bytes) - so
the actual bytes the GUI puts on the wire can be checked without real
QCC/QTRM hardware.

Run directly:  python rx_test_app.py
"""

import sys

from PySide6.QtWidgets import (
    QApplication, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMainWindow,
    QMessageBox, QPushButton, QTableView, QVBoxLayout, QWidget,
)

from packet import FIXED_HEADER_SIZE, QCC_HEADER_SIZE, QTRM_SLOT_SIZE, NUM_QTRM, QCCHeaderRx
from qtrm_filter import FilterBar, QtrmFilterProxyModel
from raw_slot_model import RawSlotTableModel
from segmented_control import SegmentedControl
from spin_field import SpinField
from theme import STYLESHEET
from udp_worker import UdpWorker

_MODE_NAMES = {
    QCCHeaderRx.MODE_NORMAL: "Normal",
    QCCHeaderRx.MODE_INTERNAL_LOOPBACK: "Internal Loopback",
    QCCHeaderRx.MODE_EXTERNAL_LOOPBACK: "External Loopback",
    QCCHeaderRx.MODE_STATUS_ONLY: "Status/Response Only",
    QCCHeaderRx.MODE_QCC_RESET: "QCC Reset",
    QCCHeaderRx.MODE_REMOTE_PROGRAMMING: "Remote Programming",
}


def _hex_full(data: bytes) -> str:
    """Every byte, space-separated, capitalized (e.g. 'AA 00 02 ...') - no truncation."""
    return " ".join(f"{b:02X}" for b in data) or "-"


class RxTestWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QCC RX Test - Receiver")
        self.resize(1300, 750)

        self.worker: UdpWorker | None = None
        self._frame_count = 0
        self.model = RawSlotTableModel()
        self.proxy_model = QtrmFilterProxyModel()
        self.proxy_model.setSourceModel(self.model)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        root.addWidget(self._build_listen_group())
        root.addWidget(self._build_header_group())
        root.addWidget(self._build_filter_bar())
        root.addWidget(self._build_table(), 1)

    # -- UI construction ---------------------------------------------------

    def _build_listen_group(self):
        box = QGroupBox("Listener")
        row = QHBoxLayout(box)

        self.port_spin = SpinField(1, 65535, 5000, field_width=64)
        self.listen_btn = QPushButton("Start Listening")
        self.listen_btn.clicked.connect(self._on_listen_clicked)
        self.status_label = QLabel("Stopped")
        self.count_label = QLabel("Frames received: 0")

        row.addWidget(QLabel("Listen Port:"))
        row.addWidget(self.port_spin)
        row.addWidget(self.listen_btn)
        row.addWidget(self.status_label)
        row.addStretch(1)
        row.addWidget(QLabel("QTRM data:"))
        self.display_mode_switch = SegmentedControl("Decimal", "Hex")
        self.display_mode_switch.toggled.connect(self._on_display_mode_toggled)
        self.display_mode_switch.setChecked(True)  # Hex by default
        row.addWidget(self.display_mode_switch)
        row.addWidget(self.count_label)
        return box

    def _build_header_group(self):
        box = QGroupBox("Last Received Header (Host -> QCC)")
        outer = QVBoxLayout(box)

        row1 = QHBoxLayout()
        self.msg_id_label = self._add_field(row1, "MSG_ID")
        self.mode_label = self._add_field(row1, "MODE")
        self.checksum_label = self._add_field(row1, "CHECKSUM")
        row1.addStretch(1)
        outer.addLayout(row1)

        self.fixed_header_label = self._add_full_width_field(outer, "Fixed Header (hex)")
        self.command_data_label = self._add_full_width_field(outer, "COMMAND_DATA (hex)")

        return box

    @staticmethod
    def _add_field(row_layout: QHBoxLayout, title: str, stretch: int = 0) -> QLabel:
        col = QVBoxLayout()
        col.addWidget(QLabel(title))
        value = QLabel("-")
        value.setStyleSheet("color: #00adb5; font-weight: 600;")
        col.addWidget(value)
        wrapper = QWidget()
        wrapper.setLayout(col)
        row_layout.addWidget(wrapper, stretch)
        return value

    @staticmethod
    def _add_full_width_field(outer_layout: QVBoxLayout, title: str) -> QLabel:
        # Own row, full width, word-wrapped - so the complete byte sequence
        # (32 bytes for the Fixed Header, 55 for COMMAND_DATA) is visible at
        # once instead of being truncated next to the small MSG_ID/MODE/
        # CHECKSUM fields.
        outer_layout.addWidget(QLabel(title))
        value = QLabel("-")
        value.setWordWrap(True)
        value.setStyleSheet("color: #00adb5; font-weight: 600;")
        outer_layout.addWidget(value)
        return value

    def _build_filter_bar(self):
        self.filter_bar = FilterBar(self.proxy_model, self.model)
        return self.filter_bar

    def _build_table(self):
        self.table = QTableView()
        self.table.setModel(self.proxy_model)
        self.table.setEditTriggers(QTableView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.setAlternatingRowColors(True)
        return self.table

    def _on_display_mode_toggled(self, hex_mode: bool):
        self.model.set_hex_mode(hex_mode)

    # -- listening -----------------------------------------------------------

    def _on_listen_clicked(self):
        if self.worker is not None:
            self.worker.stop()
            self.worker = None
            self.listen_btn.setText("Start Listening")
            self.status_label.setText("Stopped")
            return

        port = self.port_spin.value()
        # qcc_ip/qcc_port are unused here - this app only ever receives.
        self.worker = UdpWorker(local_port=port, qcc_ip="0.0.0.0", qcc_port=0)
        self.worker.frame_received.connect(self._on_frame_received)
        self.worker.error.connect(self._on_error)
        self.worker.status.connect(self.status_label.setText)
        self.worker.start()
        self.listen_btn.setText("Stop Listening")

    def _on_error(self, msg: str):
        self.status_label.setText(msg)
        QMessageBox.warning(self, "UDP Error", msg)

    def _on_frame_received(self, raw: bytes):
        self._frame_count += 1
        self.count_label.setText(f"Frames received: {self._frame_count}")

        fixed_header = raw[0:FIXED_HEADER_SIZE]
        qcc_raw = raw[FIXED_HEADER_SIZE:FIXED_HEADER_SIZE + QCC_HEADER_SIZE]
        qcc_header = QCCHeaderRx.from_bytes(qcc_raw)

        self.msg_id_label.setText(str(qcc_header.msg_id))
        self.mode_label.setText(_MODE_NAMES.get(qcc_header.mode, str(qcc_header.mode)))
        self.checksum_label.setText("OK" if qcc_header.checksum_ok else "FAIL")
        self.fixed_header_label.setText(_hex_full(fixed_header))
        self.command_data_label.setText(_hex_full(qcc_header.command_data))

        base = FIXED_HEADER_SIZE + QCC_HEADER_SIZE
        slots = [raw[base + i * QTRM_SLOT_SIZE: base + (i + 1) * QTRM_SLOT_SIZE] for i in range(NUM_QTRM)]
        self.model.replace_slots(slots)
        self.filter_bar.refresh_auto_filter()

    def closeEvent(self, event):
        # Closing (the window X button, or the main app's launcher re-showing
        # this same hidden instance later) should leave the listener state
        # consistent with a manual "Stop Listening" click, not just kill the
        # socket thread while the button/status still claim it's running.
        if self.worker is not None:
            self.worker.stop()
            self.worker = None
            self.listen_btn.setText("Start Listening")
            self.status_label.setText("Stopped")
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    window = RxTestWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
