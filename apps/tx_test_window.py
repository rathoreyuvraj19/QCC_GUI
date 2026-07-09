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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QMainWindow,
    QTableView, QVBoxLayout, QWidget,
)

from core.packet import FIXED_HEADER_SIZE, QCC_HEADER_SIZE, QTRM_SLOT_SIZE, NUM_QTRM, QCCHeaderRx
from core.qtrm_filter import FilterBar, QtrmFilterProxyModel
from widgets.raw_byte_grid import build_raw_byte_grid
from widgets.raw_slot_model import RawSlotTableModel
from widgets.segmented_control import SegmentedControl
from widgets.titled_group import collapsible_group_box, titled_group_box

_QCC_COMMAND_NAMES = {
    QCCHeaderRx.QCC_COMMAND_DATA_DISTRIBUTION: "DATA_DISTRIBUTION",
    QCCHeaderRx.QCC_COMMAND_QCC_STATUS: "QCC_STATUS",
    QCCHeaderRx.QCC_COMMAND_QCC_RESET: "QCC_RESET",
    QCCHeaderRx.QCC_COMMAND_PRT_BYPASS: "PRT_BYPASS",
    QCCHeaderRx.QCC_COMMAND_SOB_BYPASS: "SOB_BYPASS",
    QCCHeaderRx.QCC_COMMAND_PRT_INTERNAL_GEN: "PRT_INTERNAL_GEN",
    QCCHeaderRx.QCC_COMMAND_SOB_INTERNAL_GEN: "SOB_INTERNAL_GEN",
    QCCHeaderRx.QCC_COMMAND_PPS_INTERNAL_GEN: "PPS_INTERNAL_GEN",
    QCCHeaderRx.QCC_COMMAND_REMOTE_PROGRAMMING: "REMOTE_PROGRAMMING",
}

_COMMAND_FIELDS = [
    "DESTINATION_ID", "SOURCE_ID", "PACKET_SIZE", "ECHO_BYTE", "COMMAND_ACK",
    "MESSAGE_NUMBER", "DATE", "MONTH", "YEAR", "TIME_OF_DAY",
    "QCC_COMMAND", "CHECKSUM",
]


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
        box, outer = collapsible_group_box("Status")
        row = QHBoxLayout()
        outer.addLayout(row)
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
        box, outer = titled_group_box("Last Sent Header (Host -> QCC)")

        row1 = QHBoxLayout()
        self.field_labels = {}
        for name in _COMMAND_FIELDS:
            self.field_labels[name] = self._add_field(row1, name)
        row1.addStretch(1)
        outer.addLayout(row1)

        total_size = FIXED_HEADER_SIZE + QCC_HEADER_SIZE
        outer.addWidget(QLabel(f"Full Header ({total_size} bytes) - each byte, indexed:"))
        byte_grid, self.header_byte_labels = build_raw_byte_grid(total_size)
        outer.addWidget(byte_grid)

        return box

    @staticmethod
    def _add_field(row_layout: QHBoxLayout, title: str, stretch: int = 0) -> QLabel:
        col = QVBoxLayout()
        col.addWidget(QLabel(title))
        value = QLabel("-")
        value.setStyleSheet("color: #00adb5; font-weight: 600;")
        value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        value.setCursor(Qt.IBeamCursor)
        col.addWidget(value)
        wrapper = QWidget()
        wrapper.setLayout(col)
        row_layout.addWidget(wrapper, stretch)
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

        header_raw = raw[0:FIXED_HEADER_SIZE + QCC_HEADER_SIZE]
        h = QCCHeaderRx.from_bytes(header_raw)

        self.field_labels["DESTINATION_ID"].setText(str(h.destination_id))
        self.field_labels["SOURCE_ID"].setText(str(h.source_id))
        self.field_labels["PACKET_SIZE"].setText(str(h.packet_size))
        self.field_labels["ECHO_BYTE"].setText(str(h.echo_byte))
        self.field_labels["COMMAND_ACK"].setText(str(h.command_ack))
        self.field_labels["MESSAGE_NUMBER"].setText(str(h.message_number))
        self.field_labels["DATE"].setText(str(h.date))
        self.field_labels["MONTH"].setText(str(h.month))
        self.field_labels["YEAR"].setText(str(h.year))
        self.field_labels["TIME_OF_DAY"].setText(str(h.time_of_day))
        self.field_labels["QCC_COMMAND"].setText(_QCC_COMMAND_NAMES.get(h.qcc_command, f"0x{h.qcc_command:02X}"))
        self.field_labels["CHECKSUM"].setText("OK" if h.checksum_ok else "FAIL")

        for i, b in enumerate(header_raw):
            self.header_byte_labels[i].setText(f"{b:02X}")

        base = FIXED_HEADER_SIZE + QCC_HEADER_SIZE
        slots = [raw[base + i * QTRM_SLOT_SIZE: base + (i + 1) * QTRM_SLOT_SIZE] for i in range(NUM_QTRM)]
        self.model.replace_slots(slots)
        self.filter_bar.refresh_auto_filter()
