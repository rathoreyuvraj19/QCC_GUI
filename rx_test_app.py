"""
rx_test_app.py

Display-only window (opened from the main GUI - has no listener/socket of
its own) showing exactly what the main window's UdpWorker most recently
received from QCC (QCC -> Host direction), fed directly via
MainWindow._on_frame_received calling this window's show_frame(raw) - the
same no-socket pattern tx_test_window.py already uses for outgoing frames.

Previously this ran its own independent UdpWorker bound to a configurable
Listen Port, standing in for the QCC side without real hardware. Dropped
per Yuvraj's explicit ask: a second socket bound to the same port the main
app is receiving on causes the OS to deliver each incoming UDP datagram to
only one of the two sockets, silently stealing traffic from whichever one
loses out - "then dont listen to ports, in both rx test window and tx
test window, should just display me the data what the main gui has sent
or received." Since there's now no independent listener, there's no port
conflict possible - both windows show exactly what the main app itself
sent/received, nothing else.

Raw byte view (via raw_slot_model.RawSlotTableModel) - no per-command
semantic decoding of the QTRM slot bytes past the four fields fixed at
the same position in every slot (Header, Packet Size ID, Command Type,
Status & Sub Status Type) - everything from byte 5 onward is shown
generically, since its meaning depends on which command occupies that
slot.
"""

from PySide6.QtWidgets import (
    QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMainWindow,
    QTableView, QVBoxLayout, QWidget,
)

from packet import FIXED_HEADER_SIZE, QCC_HEADER_SIZE, QTRM_SLOT_SIZE, NUM_QTRM, QCCHeaderTx
from qtrm_filter import FilterBar, QtrmFilterProxyModel
from raw_slot_model import RawSlotTableModel
from segmented_control import SegmentedControl

_RESPONSE_FIELDS = [
    "MSG_ID", "MODE", "INPUT_SOB_COUNT", "INPUT_PRT_COUNT", "INPUT_PPS_COUNT",
    "OUTPUT_PRT_COUNT", "OUTPUT_SOB_COUNT", "INPUT_SOB_WIDTH_US",
    "OUTPUT_SOB_WIDTH_US", "INPUT_PRT_WIDTH_US", "OUTPUT_PRT_WIDTH_US",
    "INPUT_PPS_WIDTH_US", "CHECKSUM",
]


class RxTestWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QCC RX Test - Received Packet Viewer")
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
        self.status_label = QLabel("No frames received yet")
        self.count_label = QLabel("Frames received: 0")
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
        box = QGroupBox("Last Received Header (QCC -> Host)")
        grid = QGridLayout(box)
        self.resp_labels = {}
        wrap_cols = 5
        for i, name in enumerate(_RESPONSE_FIELDS):
            col = QVBoxLayout()
            col.addWidget(QLabel(name))
            value = QLabel("-")
            value.setStyleSheet("color: #00adb5; font-weight: 600;")
            self.resp_labels[name] = value
            col.addWidget(value)
            cell = QWidget()
            cell.setLayout(col)
            grid.addWidget(cell, i // wrap_cols, i % wrap_cols)
        return box

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

    # -- called by the main window on every UdpWorker.frame_received --------

    def show_frame(self, raw: bytes):
        self._frame_count += 1
        self.count_label.setText(f"Frames received: {self._frame_count}")
        self.status_label.setText("Frame received")

        qcc_raw = raw[FIXED_HEADER_SIZE:FIXED_HEADER_SIZE + QCC_HEADER_SIZE]
        qcc_header = QCCHeaderTx.from_bytes(qcc_raw)

        self.resp_labels["MSG_ID"].setText(str(qcc_header.msg_id))
        self.resp_labels["MODE"].setText(str(qcc_header.mode))
        self.resp_labels["INPUT_SOB_COUNT"].setText(str(qcc_header.input_sob_count))
        self.resp_labels["INPUT_PRT_COUNT"].setText(str(qcc_header.input_prt_count))
        self.resp_labels["INPUT_PPS_COUNT"].setText(str(qcc_header.input_pps_count))
        self.resp_labels["OUTPUT_PRT_COUNT"].setText(str(qcc_header.output_prt_count))
        self.resp_labels["OUTPUT_SOB_COUNT"].setText(str(qcc_header.output_sob_count))
        self.resp_labels["INPUT_SOB_WIDTH_US"].setText(str(qcc_header.input_sob_width_us))
        self.resp_labels["OUTPUT_SOB_WIDTH_US"].setText(str(qcc_header.output_sob_width_us))
        self.resp_labels["INPUT_PRT_WIDTH_US"].setText(str(qcc_header.input_prt_width_us))
        self.resp_labels["OUTPUT_PRT_WIDTH_US"].setText(str(qcc_header.output_prt_width_us))
        self.resp_labels["INPUT_PPS_WIDTH_US"].setText(str(qcc_header.input_pps_width_us))
        self.resp_labels["CHECKSUM"].setText("OK" if qcc_header.checksum_ok else "FAIL")

        base = FIXED_HEADER_SIZE + QCC_HEADER_SIZE
        slots = [raw[base + i * QTRM_SLOT_SIZE: base + (i + 1) * QTRM_SLOT_SIZE] for i in range(NUM_QTRM)]
        self.model.replace_slots(slots)
        self.filter_bar.refresh_auto_filter()
