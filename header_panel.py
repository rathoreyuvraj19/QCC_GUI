"""
header_panel.py

Small, reusable "dedicated space" showing whatever frame a tab most
recently received back - decoded QCC Header fields (per
QCC_90Byte_Header_BitTable.docx, 2026-07-05: the full 90-byte response
header, since every response flowing back to the main GUI is QCC -> RC
direction) plus the raw hex of the whole 90-byte header (kept alongside
the decoded fields for byte-level verification, e.g. spotting an
unexpected non-zero reserved byte). Meant to sit in a fixed-width column
on the right of a tab's main content, not inside its scroll area, so it's
always visible regardless of scroll position.

Wrapped in its own QScrollArea (same reasoning as every tab's main
content, see main_window.py's window-fit history) - 26 decoded fields
plus the raw-hex block have real natural height, and this panel sits
outside each tab's own scroll area, so without this its full height would
add directly to the whole window's minimum size.
"""

from PySide6.QtWidgets import (
    QFormLayout, QGroupBox, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from packet import FIXED_HEADER_SIZE, QCC_HEADER_SIZE, QCCHeaderTx

_PANEL_WIDTH = 260
_HEADER_TOTAL_SIZE = FIXED_HEADER_SIZE + QCC_HEADER_SIZE

_RESPONSE_FIELDS = [
    "DESTINATION_ID", "SOURCE_ID", "PACKET_SIZE", "COMMAND_ID", "COMMAND_ACK",
    "MESSAGE_NUMBER", "DATE", "MONTH", "YEAR", "TIME_OF_DAY",
    "COMMAND_ID_REPEAT", "FPGA_TEMPERATURE", "BOARD_TEMPERATURE", "BOARD_HUMIDITY",
    "INPUT_SOB_COUNT", "INPUT_PRT_COUNT", "INPUT_PPS_COUNT",
    "OUTPUT_PRT_COUNT", "OUTPUT_SOB_COUNT",
    "INPUT_SOB_WIDTH_US", "OUTPUT_SOB_WIDTH_US",
    "INPUT_PRT_WIDTH_US", "OUTPUT_PRT_WIDTH_US", "INPUT_PPS_WIDTH_US",
    "PPS_COUNTER", "CHIP_ID", "CHECKSUM",
]


def _hex_full(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data) or "-"


class HeaderPanel(QWidget):
    """Call show_frame(raw_2970_byte_frame) whenever a response arrives for the owning tab."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(_PANEL_WIDTH)

        box = QGroupBox("Last Received Header")
        layout = QVBoxLayout(box)

        layout.addWidget(QLabel("QCC Header (decoded):"))
        form = QFormLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(4)
        self.field_labels = {}
        for name in _RESPONSE_FIELDS:
            name_label = QLabel(name)
            name_label.setStyleSheet("font-size: 8pt; color: rgba(238, 238, 238, 0.6);")
            value_label = QLabel("-")
            value_label.setStyleSheet("color: #00adb5; font-weight: 600; font-size: 9pt;")
            self.field_labels[name] = value_label
            form.addRow(name_label, value_label)
        layout.addLayout(form)

        layout.addWidget(QLabel(f"Full Header ({_HEADER_TOTAL_SIZE} bytes, hex):"))
        self.header_hex_label = QLabel("-")
        self.header_hex_label.setWordWrap(True)
        self.header_hex_label.setStyleSheet("color: #00adb5; font-weight: 600;")
        layout.addWidget(self.header_hex_label)

        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(box)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def show_frame(self, raw: bytes):
        header_raw = raw[0:_HEADER_TOTAL_SIZE]
        h = QCCHeaderTx.from_bytes(header_raw)

        self.field_labels["DESTINATION_ID"].setText(str(h.destination_id))
        self.field_labels["SOURCE_ID"].setText(str(h.source_id))
        self.field_labels["PACKET_SIZE"].setText(str(h.packet_size))
        self.field_labels["COMMAND_ID"].setText(str(h.command_id))
        self.field_labels["COMMAND_ACK"].setText(str(h.command_ack))
        self.field_labels["MESSAGE_NUMBER"].setText(str(h.message_number))
        self.field_labels["DATE"].setText(str(h.date))
        self.field_labels["MONTH"].setText(str(h.month))
        self.field_labels["YEAR"].setText(str(h.year))
        self.field_labels["TIME_OF_DAY"].setText(str(h.time_of_day))
        self.field_labels["COMMAND_ID_REPEAT"].setText(str(h.command_id_repeat))
        self.field_labels["FPGA_TEMPERATURE"].setText(str(h.fpga_temperature))
        self.field_labels["BOARD_TEMPERATURE"].setText(str(h.board_temperature))
        self.field_labels["BOARD_HUMIDITY"].setText(str(h.board_humidity))
        self.field_labels["INPUT_SOB_COUNT"].setText(str(h.input_sob_count))
        self.field_labels["INPUT_PRT_COUNT"].setText(str(h.input_prt_count))
        self.field_labels["INPUT_PPS_COUNT"].setText(str(h.input_pps_count))
        self.field_labels["OUTPUT_PRT_COUNT"].setText(str(h.output_prt_count))
        self.field_labels["OUTPUT_SOB_COUNT"].setText(str(h.output_sob_count))
        self.field_labels["INPUT_SOB_WIDTH_US"].setText(str(h.input_sob_width_us))
        self.field_labels["OUTPUT_SOB_WIDTH_US"].setText(str(h.output_sob_width_us))
        self.field_labels["INPUT_PRT_WIDTH_US"].setText(str(h.input_prt_width_us))
        self.field_labels["OUTPUT_PRT_WIDTH_US"].setText(str(h.output_prt_width_us))
        self.field_labels["INPUT_PPS_WIDTH_US"].setText(str(h.input_pps_width_us))
        self.field_labels["PPS_COUNTER"].setText(str(h.pps_counter))
        self.field_labels["CHIP_ID"].setText(f"0x{h.chip_id:08X}")
        self.field_labels["CHECKSUM"].setText("OK" if h.checksum_ok else "FAIL")

        self.header_hex_label.setText(_hex_full(header_raw))

    def clear(self):
        for label in self.field_labels.values():
            label.setText("-")
        self.header_hex_label.setText("-")
