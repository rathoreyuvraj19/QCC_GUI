"""
header_panel.py

Small, reusable "dedicated space" showing the raw 90-byte header (32-byte
Fixed Header + 58-byte QCC Header) of whatever frame a tab most recently
received back - raw hex for now, no field-level decoding yet (that's the
"Last Response (QCC header)" panel on the Command/QTRM Grid tab, which
already decodes the QCC header's known fields; every other tab has never
shown any header info at all until now). Meant to sit in a fixed-width
column on the right of a tab's main content, not inside its scroll area, so
it's always visible regardless of scroll position.
"""

from PySide6.QtWidgets import QGroupBox, QLabel, QVBoxLayout, QWidget

from packet import FIXED_HEADER_SIZE, QCC_HEADER_SIZE

_PANEL_WIDTH = 260


def _hex_full(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data) or "-"


class HeaderPanel(QWidget):
    """Call show_frame(raw_2970_byte_frame) whenever a response arrives for the owning tab."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(_PANEL_WIDTH)

        box = QGroupBox("Last Received Header (raw)")
        layout = QVBoxLayout(box)

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

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(box)

    def show_frame(self, raw: bytes):
        fixed_header = raw[0:FIXED_HEADER_SIZE]
        qcc_header = raw[FIXED_HEADER_SIZE:FIXED_HEADER_SIZE + QCC_HEADER_SIZE]
        self.fixed_header_label.setText(_hex_full(fixed_header))
        self.qcc_header_label.setText(_hex_full(qcc_header))

    def clear(self):
        self.fixed_header_label.setText("-")
        self.qcc_header_label.setText("-")
