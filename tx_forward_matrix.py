"""
tx_forward_matrix.py

"TX Forward RF Status" visualization for status_tab.py's HEALTH Status Type.

Per the FPGA's TX Forward RF status register (r_tx_data(7) <=
x"0" & r_channel_TX_RF_logic), the lower 4 bits of the decoded
"tx_forward_rf_status" byte are one flag per TX channel of a QTRM:
bit 3 = CH1, bit 2 = CH2, bit 1 = CH3, bit 0 = CH4.

Shown as a shrunk per-QTRM response indicator (communication status -
responded/no response/pending, independent of channel state) with 4 small
channel LEDs directly below it (green = channel active, red = channel
inactive, grey = unknown because no response was received for that QTRM).
Same 6 Cold-Plate-group layout as link_test_tab.py's LedMatrix, via the
same qtrm_layout.py helpers, so the two visually line up when toggled.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from command_style import IDLE_MATRIX_RGB, PENDING_RGB, SUCCESS_RGB, FAILURE_RGB, rgb_css
from qtrm_layout import NUM_QTRM, MATRIX_COLS, group_grid_positions, groups_top_to_bottom

# Same borderless CP group box look as LedMatrix's _CP_BOX_STYLE.
_CP_BOX_STYLE = (
    "QGroupBox { border: none; background: transparent; margin-top: 6px; padding: 4px 0px 0px 0px; }"
    "QGroupBox::title { subcontrol-origin: margin; left: 2px; padding: 0 2px; }"
)

_TEXT_COLOR = "#1f2328"

# Response-indicator cell (shrunk vs. LedMatrix's _Led, to leave room for
# the 4 channel LEDs below it) - radius kept well under half of
# _RESP_MIN_HEIGHT, per this codebase's established QSS gotcha: Qt's QSS
# engine doesn't clamp border-radius to half the box size like CSS does,
# a radius too close to (or over) half the height/width renders square
# instead of rounded (verified via render tests elsewhere in this app).
_RESP_MIN_WIDTH = 46
_RESP_MIN_HEIGHT = 16
_RESP_RADIUS = 5

# Small square channel LEDs (CH1-CH4) under each response cell.
_CH_LED_SIZE = 12
_CH_LED_RADIUS = 4

_IDLE_RGB = IDLE_MATRIX_RGB
_PENDING_RGB = PENDING_RGB
_RESPONDED_RGB = SUCCESS_RGB
_NO_RESPONSE_RGB = FAILURE_RGB
_CH_ACTIVE_RGB = SUCCESS_RGB
_CH_INACTIVE_RGB = FAILURE_RGB
_CH_UNKNOWN_RGB = IDLE_MATRIX_RGB


def _resp_style(rgb) -> str:
    return (
        f"border: 1px solid rgba(0, 0, 0, 60); border-radius: {_RESP_RADIUS}px;"
        f"background-color: {rgb_css(rgb)}; color: {_TEXT_COLOR};"
        "font-size: 7pt; font-weight: 600; padding: 1px 2px;"
    )


def _ch_led_style(rgb) -> str:
    return (
        f"border: 1px solid rgba(0, 0, 0, 60); border-radius: {_CH_LED_RADIUS}px;"
        f"background-color: {rgb_css(rgb)};"
    )


class _TxForwardCell(QWidget):
    """One QTRM's shrunk response indicator + its 4 CH1-CH4 channel LEDs."""

    clicked = Signal(int)

    def __init__(self, qtrm_index: int, parent=None):
        super().__init__(parent)
        self.qtrm_index = qtrm_index
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        self.resp_label = QLabel(f"QTRM-{qtrm_index}")
        self.resp_label.setAlignment(Qt.AlignCenter)
        self.resp_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.resp_label.setMinimumSize(_RESP_MIN_WIDTH, _RESP_MIN_HEIGHT)
        self.resp_label.setCursor(Qt.PointingHandCursor)
        self.resp_label.setToolTip(f"QTRM-{qtrm_index} - click to query just this QTRM")
        outer.addWidget(self.resp_label)

        ch_row = QHBoxLayout()
        ch_row.setSpacing(2)
        self.ch_leds = []
        for ch_num in range(1, 5):
            led = QLabel()
            led.setFixedSize(_CH_LED_SIZE, _CH_LED_SIZE)
            led.setToolTip(f"CH{ch_num}")
            ch_row.addWidget(led)
            self.ch_leds.append(led)
        outer.addLayout(ch_row)

        self.set_unqueried()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.qtrm_index)
        super().mousePressEvent(event)

    def _set_response(self, rgb):
        self.resp_label.setStyleSheet(_resp_style(rgb))

    def _set_channels(self, bits):
        """bits: a 4-item iterable of 0/1 (CH1..CH4), or None for all-unknown."""
        for i, led in enumerate(self.ch_leds):
            if bits is None:
                led.setStyleSheet(_ch_led_style(_CH_UNKNOWN_RGB))
            else:
                led.setStyleSheet(_ch_led_style(_CH_ACTIVE_RGB if bits[i] else _CH_INACTIVE_RGB))

    def set_unqueried(self):
        self._set_response(_IDLE_RGB)
        self._set_channels(None)

    def set_pending(self):
        self._set_response(_PENDING_RGB)
        self._set_channels(None)

    def set_result(self, decoded):
        """decoded: the QTRM's decoded HEALTH fields dict, or None if it didn't respond."""
        if decoded is None:
            self._set_response(_NO_RESPONSE_RGB)
            self._set_channels(None)
            return
        self._set_response(_RESPONDED_RGB)
        value = decoded.get("tx_forward_rf_status", 0) & 0x0F
        # bit 3 = CH1, bit 2 = CH2, bit 1 = CH3, bit 0 = CH4
        bits = [(value >> 3) & 1, (value >> 2) & 1, (value >> 1) & 1, value & 1]
        self._set_channels(bits)


class TxForwardMatrix(QWidget):
    """
    Same 6 Cold-Plate-group layout as link_test_tab.py's LedMatrix, but each
    cell is a _TxForwardCell (shrunk response indicator + 4 channel LEDs)
    instead of a single LED - status_tab.py toggles between the two
    (the plain LedMatrix and this) based on whether "Tx Forward RF Status"
    is the currently-selected "Show Field".
    """

    cell_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        self._cells = [None] * NUM_QTRM
        for group in groups_top_to_bottom():
            cp_box = QGroupBox(f"CP{group}")
            cp_box.setStyleSheet(_CP_BOX_STYLE)
            cp_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            grid = QGridLayout(cp_box)
            grid.setSpacing(4)
            for col in range(MATRIX_COLS):
                grid.setColumnStretch(col, 1)
            for local_row in range(2):
                grid.setRowStretch(local_row, 1)

            for qtrm_index, local_row, local_col in group_grid_positions(group):
                cell = _TxForwardCell(qtrm_index)
                cell.clicked.connect(self.cell_clicked.emit)
                self._cells[qtrm_index] = cell
                grid.addWidget(cell, local_row, local_col)

            outer.addWidget(cp_box, 1)

    def set_all_state(self, state: str):
        """state: 'idle' | 'pending' | 'no_response' - applies to every cell."""
        for cell in self._cells:
            if state == "pending":
                cell.set_pending()
            elif state == "no_response":
                cell.set_result(None)
            else:
                cell.set_unqueried()

    def set_results(self, results):
        """results: length-NUM_QTRM list of decoded-dict-or-None, per QTRM."""
        for cell, decoded in zip(self._cells, results):
            cell.set_result(decoded)

    def set_one_result(self, qtrm_index: int, decoded):
        self._cells[qtrm_index].set_result(decoded)
