"""
header_panel.py

Small, reusable "dedicated space" showing whatever frame a tab most
recently received back - decoded QCC Header fields (per the IDD's
QCCHeaderTx layout, since every response flowing back to the main GUI is
always QCC -> Host direction) plus the raw hex of both the 32-byte Fixed
Header (still TBD/all-zero per the IDD, no fields defined yet - kept as
raw hex so any unexpected non-zero byte is still visible) and the 58-byte
QCC Header (kept alongside the decoded fields for byte-level
verification). Meant to sit in a fixed-width column on the right of a
tab's main content, not inside its scroll area, so it's always visible
regardless of scroll position.

Wrapped in its own QScrollArea (same reasoning as every tab's main
content, see main_window.py's window-fit history) - 13 decoded fields
plus both raw-hex blocks have real natural height, and this panel sits
outside each tab's own scroll area, so without this its full height would
add directly to the whole window's minimum size.
"""

from PySide6.QtWidgets import (
    QFormLayout, QGroupBox, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from packet import FIXED_HEADER_SIZE, QCC_HEADER_SIZE, QCCHeaderTx

_PANEL_WIDTH = 260

_RESPONSE_FIELDS = [
    "MSG_ID", "MODE", "INPUT_SOB_COUNT", "INPUT_PRT_COUNT", "INPUT_PPS_COUNT",
    "OUTPUT_PRT_COUNT", "OUTPUT_SOB_COUNT", "INPUT_SOB_WIDTH_US",
    "OUTPUT_SOB_WIDTH_US", "INPUT_PRT_WIDTH_US", "OUTPUT_PRT_WIDTH_US",
    "INPUT_PPS_WIDTH_US", "CHECKSUM",
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

        layout.addWidget(QLabel(f"Fixed Header ({FIXED_HEADER_SIZE} bytes, hex):"))
        self.fixed_header_label = QLabel("-")
        self.fixed_header_label.setWordWrap(True)
        self.fixed_header_label.setStyleSheet("color: #00adb5; font-weight: 600;")
        layout.addWidget(self.fixed_header_label)

        layout.addWidget(QLabel(f"QCC Header ({QCC_HEADER_SIZE} bytes, hex):"))
        self.qcc_header_label = QLabel("-")
        self.qcc_header_label.setWordWrap(True)
        self.qcc_header_label.setStyleSheet("color: #00adb5; font-weight: 600;")
        layout.addWidget(self.qcc_header_label)

        layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(box)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def show_frame(self, raw: bytes):
        fixed_header = raw[0:FIXED_HEADER_SIZE]
        qcc_raw = raw[FIXED_HEADER_SIZE:FIXED_HEADER_SIZE + QCC_HEADER_SIZE]
        qcc_header = QCCHeaderTx.from_bytes(qcc_raw)

        self.field_labels["MSG_ID"].setText(str(qcc_header.msg_id))
        self.field_labels["MODE"].setText(str(qcc_header.mode))
        self.field_labels["INPUT_SOB_COUNT"].setText(str(qcc_header.input_sob_count))
        self.field_labels["INPUT_PRT_COUNT"].setText(str(qcc_header.input_prt_count))
        self.field_labels["INPUT_PPS_COUNT"].setText(str(qcc_header.input_pps_count))
        self.field_labels["OUTPUT_PRT_COUNT"].setText(str(qcc_header.output_prt_count))
        self.field_labels["OUTPUT_SOB_COUNT"].setText(str(qcc_header.output_sob_count))
        self.field_labels["INPUT_SOB_WIDTH_US"].setText(str(qcc_header.input_sob_width_us))
        self.field_labels["OUTPUT_SOB_WIDTH_US"].setText(str(qcc_header.output_sob_width_us))
        self.field_labels["INPUT_PRT_WIDTH_US"].setText(str(qcc_header.input_prt_width_us))
        self.field_labels["OUTPUT_PRT_WIDTH_US"].setText(str(qcc_header.output_prt_width_us))
        self.field_labels["INPUT_PPS_WIDTH_US"].setText(str(qcc_header.input_pps_width_us))
        self.field_labels["CHECKSUM"].setText("OK" if qcc_header.checksum_ok else "FAIL")

        self.fixed_header_label.setText(_hex_full(fixed_header))
        self.qcc_header_label.setText(_hex_full(qcc_raw))

    def clear(self):
        for label in self.field_labels.values():
            label.setText("-")
        self.fixed_header_label.setText("-")
        self.qcc_header_label.setText("-")
