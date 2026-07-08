"""
raw_byte_grid.py

Shared read-only per-byte display - byte index to the left, hex value to
the right, grouped together as one unit, 1-based indexing (matching every
other byte-numbered view in this app, e.g. status_responder_app.py's
ByteGrid / rc_settings_tab.py's NamedByteGrid). Used by both
tx_test_window.py and rx_test_app.py to show a sent/received frame's
90-byte header as raw bytes with no per-command semantic decoding - a
combined hex string was hard to correlate back to a specific byte, and
tying it to decoded field names assumes the header's actual layout matches
QCCHeaderRx/QCCHeaderTx exactly, which isn't something either test window
should have to assume just to show what was literally sent/received.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QWidget

_VALUE_WIDTH = 32
_INDEX_WIDTH = 22


def build_raw_byte_grid(num_bytes: int, wrap_cols: int = 15) -> tuple:
    """
    Returns (container_widget, [value_label, ...]) - add container_widget to
    your layout, then on each new frame set value_label[i].setText(f"{b:02X}")
    for the corresponding byte.
    """
    container = QWidget()
    grid = QGridLayout(container)
    grid.setHorizontalSpacing(8)
    grid.setVerticalSpacing(4)
    labels = []
    for i in range(num_bytes):
        index_label = QLabel(str(i + 1))
        index_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        index_label.setFixedWidth(_INDEX_WIDTH)
        index_label.setStyleSheet("font-size: 9pt; font-weight: 600; color: rgba(238, 238, 238, 0.65);")

        value_label = QLabel("00")
        value_label.setAlignment(Qt.AlignCenter)
        value_label.setFixedWidth(_VALUE_WIDTH)
        value_label.setStyleSheet(
            "color: #00adb5; font-weight: 600; background-color: #393e46;"
            "border: 1px solid #4a515a; border-radius: 6px; padding: 3px;"
        )
        value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        value_label.setCursor(Qt.IBeamCursor)

        # Index and value side by side (not stacked) - grouped as one
        # visual unit, index reads clearly at a larger size instead of a
        # tiny muted number floating above the box.
        cell_row = QHBoxLayout()
        cell_row.setSpacing(4)
        cell_row.setContentsMargins(0, 0, 0, 0)
        cell_row.addWidget(index_label)
        cell_row.addWidget(value_label)
        cell_widget = QWidget()
        cell_widget.setLayout(cell_row)

        labels.append(value_label)
        grid.addWidget(cell_widget, i // wrap_cols, i % wrap_cols)

    return container, labels
