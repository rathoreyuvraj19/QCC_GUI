"""
tx_test_window.py

Display-only window (opened from the main GUI - it has no listener of its
own, unlike rx_test_app.py) showing exactly what the main window's
UdpWorker actually put on the wire for the most recent send, regardless of
which tab/button triggered it - the Command tab's Send button, Link Test,
RX/TX Cal, Soft Reset, and Isolation all route through the same
UdpWorker.send_frame, which now emits a frame_sent signal on every
successful sendto().

Raw byte view (via raw_slot_model.RawSlotTableModel), matching the RX test
window: no per-command semantic decoding of the QTRM slot bytes past the
four fields fixed at the same position in every slot (Header, Packet Size
ID, Command Type, Status & Sub Status Type) - everything from byte 5 onward
is shown generically, since its meaning depends on which command occupies
that slot.
"""

from PySide6.QtWidgets import (
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QTableView,
    QVBoxLayout, QWidget,
)

from packet import FIXED_HEADER_SIZE, QCC_HEADER_SIZE, QTRM_SLOT_SIZE, NUM_QTRM, QCCHeaderRx
from qtrm_filter import FilterBar, QtrmFilterProxyModel
from raw_slot_model import RawSlotTableModel
from segmented_control import SegmentedControl

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


class TxTestWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QCC TX Test - Sent Packet Viewer")
        self.resize(1300, 750)

        self._frame_count = 0
        self.model = RawSlotTableModel()
        self.proxy_model = QtrmFilterProxyModel()
        self.proxy_model.setSourceModel(self.model)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        root.addWidget(self._build_status_group())
        root.addWidget(self._build_header_group())
        root.addWidget(self._build_filter_bar())
        root.addWidget(self._build_table(), 1)

    # -- UI construction ---------------------------------------------------

    def _build_status_group(self):
        box = QGroupBox("Status")
        row = QHBoxLayout(box)
        self.status_label = QLabel("No frames sent yet")
        self.count_label = QLabel("Frames sent: 0")
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
        box = QGroupBox("Last Sent Header (Host -> QCC)")
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

    # -- called by the main window on every UdpWorker.frame_sent -------------

    def show_frame(self, raw: bytes):
        self._frame_count += 1
        self.count_label.setText(f"Frames sent: {self._frame_count}")
        self.status_label.setText("Frame sent")

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
