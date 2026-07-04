"""
link_test_tab.py

"Link Test" - one-click liveness check across all 96 QTRMs, plus a
Soft-Reset-style individual per-QTRM link check.

Per the QTRM Message Format IDD (Status Type = LINK), a live QTRM replies
with a fixed 10-byte message ending in the sentinel bytes A1 A2 A3 A4 A5
before its checksum (confirmed against STATUS_MODULE.vhd). This tab sends
that query either to every QTRM in one frame, or (by clicking one LED) to
just that one QTRM - mirroring Soft Reset's individual-target pattern.

The LED matrix gives an at-a-glance view: every cell turns grey the moment
Send (or an individual LED) is clicked, then after a short reveal delay
turns green (linked) or light red (not linked / no reply yet). For an
individual click, only the clicked QTRM's cell is revealed - the rest of
the array is left at pending grey, since it was never actually queried.
"""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from header_panel import HeaderPanel
from qtrm_layout import NUM_QTRM, MATRIX_COLS, group_grid_positions, groups_top_to_bottom
from spin_field import DoubleSpinField

REVEAL_DELAY_MS = 1000

_IDLE_COLOR = QColor(222, 224, 227)
_PENDING_COLOR = QColor(160, 165, 172)
_LINKED_COLOR = QColor(146, 208, 165)
_NOT_LINKED_COLOR = QColor(240, 149, 149)
_TEXT_COLOR = "#1f2328"

# No drawn box/border/background - just the "CP{n}" title text sits above
# each group so the QTRM cells themselves can use the full width instead of
# being boxed into a bordered card with padding around them.
_CP_BOX_STYLE = (
    "QGroupBox { border: none; background: transparent; margin-top: 6px; padding: 4px 0px 0px 0px; }"
    "QGroupBox::title { subcontrol-origin: margin; left: 2px; padding: 0 2px; }"
)

_LED_MIN_WIDTH = 46
_LED_MIN_HEIGHT = 24


class _Led(QLabel):
    """A single clickable rectangular status cell for one QTRM (0-indexed label)."""

    clicked = Signal(int)

    def __init__(self, qtrm_index: int, parent=None):
        super().__init__(f"QTRM-{qtrm_index}", parent)
        self.qtrm_index = qtrm_index
        # Expanding so the cell fills its whole grid cell (grows/shrinks with
        # the window) rather than staying at its natural size and leaving
        # empty space around it. setMinimumSize (not setFixedSize) keeps a
        # floor so "QTRM-95" never clips, while still letting Qt shrink below
        # that floor without overlap if the window gets extremely small.
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(_LED_MIN_WIDTH, _LED_MIN_HEIGHT)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"QTRM-{qtrm_index} - click to link-test just this QTRM")
        self.set_color(_IDLE_COLOR)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.qtrm_index)
        super().mousePressEvent(event)

    def set_color(self, color: QColor):
        self.setStyleSheet(
            # Same 16px roundness as the Soft Reset button matrix (inherited
            # there from the global QPushButton rule) so both QTRM display
            # arrays share the same shape.
            "border-radius: 16px; border: 1px solid rgba(0, 0, 0, 60);"
            f"background-color: rgb({color.red()}, {color.green()}, {color.blue()});"
            f"color: {_TEXT_COLOR}; font-size: 8pt; font-weight: 500; padding: 2px 4px;"
        )


class LedMatrix(QWidget):
    """
    Six 'CP' (Cold Plate) group boxes, each holding the 16 QTRMs on that
    connector (2 rows x 8 columns), stacked to match the real array -
    CP5 at the top down to CP0 at the bottom.
    """

    led_clicked = Signal(int)  # qtrm_index

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        self._leds = [None] * NUM_QTRM
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
                led = _Led(qtrm_index)
                led.clicked.connect(self.led_clicked.emit)
                self._leds[qtrm_index] = led
                grid.addWidget(led, local_row, local_col)

            outer.addWidget(cp_box, 1)

    def set_all(self, color: QColor):
        for led in self._leds:
            led.set_color(color)

    def set_one(self, qtrm_index: int, color: QColor):
        self._leds[qtrm_index].set_color(color)

    def set_results(self, linked_flags):
        for led, linked in zip(self._leds, linked_flags):
            led.set_color(_LINKED_COLOR if linked else _NOT_LINKED_COLOR)


class LinkTestTab(QWidget):
    send_requested = Signal(bool)          # is_auto_resend
    individual_send_requested = Signal(int)  # qtrm_index (0-based)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._link_results = None
        self._revealed = True
        self._individual_target = None
        self._individual_result = None
        self._individual_revealed = True
        self._auto_resending = False
        self._resend_timer = QTimer(self)
        self._resend_timer.timeout.connect(lambda: self.send_requested.emit(True))

        content = QWidget()
        layout = QVBoxLayout(content)

        top_row = QHBoxLayout()
        self.send_btn = QPushButton("Send Link Test")
        self.send_btn.clicked.connect(self._on_send_btn_clicked)
        top_row.addWidget(self.send_btn)

        top_row.addWidget(QLabel("Resend every (s):"))
        self.resend_spin = DoubleSpinField(0.0, 300.0, 0.0, step=0.1, decimals=1, field_width=64)
        top_row.addWidget(self.resend_spin)

        self.summary_label = QLabel("Not yet run")
        self.response_time_label = QLabel("")
        top_row.addWidget(self.summary_label)
        top_row.addWidget(self.response_time_label)
        top_row.addStretch(1)
        layout.addLayout(top_row)

        self.led_matrix = LedMatrix()
        self.led_matrix.led_clicked.connect(self._on_led_clicked)
        layout.addWidget(self.led_matrix, 1)

        # Wrapped in a QScrollArea so this tab's minimumSizeHint stays small
        # (bounded by the scroll area itself, not the 96-cell matrix's
        # natural size) - lets the whole window shrink to fit any screen,
        # with scrollbars appearing instead of the window refusing to
        # shrink. Same pattern already used by cal_tab.py.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)

        # Dedicated right-side space for the raw 90-byte header of whatever
        # frame this tab most recently received - outside the scroll area so
        # it's always visible regardless of scroll position.
        self.header_panel = HeaderPanel()

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll, 1)
        outer.addWidget(self.header_panel)

    # -- full-array test (send button, with optional auto-resend) ---------

    def _on_send_btn_clicked(self):
        if self._auto_resending:
            self._resend_timer.stop()
            self._auto_resending = False
            self.send_btn.setText("Send Link Test")
            return

        interval_s = self.resend_spin.value()
        self.send_requested.emit(False)
        if interval_s > 0:
            self._auto_resending = True
            self.send_btn.setText("Stop")
            self._resend_timer.start(int(interval_s * 1000))

    def mark_pending(self):
        self.summary_label.setText("Sent - waiting for response...")
        self.response_time_label.setText("")
        self._link_results = None
        self._revealed = False
        self.led_matrix.set_all(_PENDING_COLOR)
        QTimer.singleShot(REVEAL_DELAY_MS, self._reveal)

    def show_results(self, linked_flags):
        self._link_results = linked_flags
        linked_count = sum(1 for v in linked_flags if v)
        self.summary_label.setText(f"{linked_count}/{NUM_QTRM} QTRMs linked")
        if self._revealed:
            # Reveal delay already elapsed (late response) - reflect it now.
            self.led_matrix.set_results(linked_flags)

    def show_response_time(self, microseconds: float):
        self.response_time_label.setText(f"{microseconds:.0f} µs")

    def show_no_response(self):
        self.summary_label.setText("No response")
        self.response_time_label.setText("")

    def _reveal(self):
        self._revealed = True
        if self._link_results is not None:
            self.led_matrix.set_results(self._link_results)
        else:
            self.led_matrix.set_all(_NOT_LINKED_COLOR)

    # -- individual QTRM test (click one LED) ------------------------------

    def _on_led_clicked(self, qtrm_index: int):
        self.individual_send_requested.emit(qtrm_index)

    def mark_individual_pending(self, qtrm_index: int):
        self.summary_label.setText(f"QTRM-{qtrm_index}: waiting for response...")
        self.response_time_label.setText("")
        self._individual_target = qtrm_index
        self._individual_result = None
        self._individual_revealed = False
        self.led_matrix.set_all(_PENDING_COLOR)
        QTimer.singleShot(REVEAL_DELAY_MS, self._reveal_individual)

    def show_individual_result(self, qtrm_index: int, linked: bool):
        self._individual_result = linked
        if self._individual_revealed:
            # Reveal delay already elapsed (late response) - reflect it now.
            self.led_matrix.set_one(qtrm_index, _LINKED_COLOR if linked else _NOT_LINKED_COLOR)
            self.summary_label.setText(f"QTRM-{qtrm_index}: {'Linked' if linked else 'Not linked'}")

    def show_individual_response_time(self, microseconds: float):
        self.response_time_label.setText(f"{microseconds:.0f} µs")

    def show_individual_no_response(self, qtrm_index: int):
        self.summary_label.setText(f"QTRM-{qtrm_index}: No response")
        self.led_matrix.set_one(qtrm_index, _NOT_LINKED_COLOR)

    def _reveal_individual(self):
        self._individual_revealed = True
        if self._individual_target is None:
            return
        if self._individual_result is not None:
            color = _LINKED_COLOR if self._individual_result else _NOT_LINKED_COLOR
            self.led_matrix.set_one(self._individual_target, color)
            self.summary_label.setText(
                f"QTRM-{self._individual_target}: {'Linked' if self._individual_result else 'Not linked'}"
            )
        # else: leave it pending grey - the real response (or timeout) hasn't
        # arrived yet and will update it directly when it does.
