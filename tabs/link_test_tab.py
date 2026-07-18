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

from core.command_style import IDLE_MATRIX_RGB, PENDING_RGB, SUCCESS_RGB, FAILURE_RGB
from core.command_style import send_button_style
from widgets.qtrm_layout import NUM_QTRM, MATRIX_COLS, group_grid_positions, groups_top_to_bottom
from widgets.spin_field import DoubleSpinField

# LedMatrix needs real QColor instances (not QSS strings) - same RGB
# triples as command_style.py's shared palette, just converted here since
# this is the one consumer that paints them directly rather than via QSS.
_IDLE_COLOR = QColor(*IDLE_MATRIX_RGB)
_PENDING_COLOR = QColor(*PENDING_RGB)
_LINKED_COLOR = QColor(*SUCCESS_RGB)
_NOT_LINKED_COLOR = QColor(*FAILURE_RGB)
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
# Fixed (not just minimum) row height - without this, a tab with less
# competing vertical content above the matrix (Link Test) stretches its
# cells taller than a tab with more content above it (Dwell's 320px-tall
# table), since both use Expanding size policy + stretch factors and just
# divide up whatever's left. Capping height keeps every tab's matrix
# pixel-identical regardless of what else shares its layout.
_LED_FIXED_HEIGHT = 32

# Send button color/QSS from command_style.py, the single source of truth
# every command tab shares.
_SEND_BTN_STYLE = send_button_style()


class _Led(QLabel):
    """A single rectangular status cell for one QTRM (0-indexed label) - clickable only if the owning LedMatrix says so."""

    clicked = Signal(int)

    def __init__(self, qtrm_index: int, clickable: bool = True, parent=None):
        super().__init__(f"QTRM-{qtrm_index}", parent)
        self.qtrm_index = qtrm_index
        self.clickable = clickable
        # Expanding so the cell fills its whole grid cell (grows/shrinks with
        # the window) rather than staying at its natural size and leaving
        # empty space around it. setMinimumSize (not setFixedSize) keeps a
        # floor so "QTRM-95" never clips, while still letting Qt shrink below
        # that floor without overlap if the window gets extremely small.
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumWidth(_LED_MIN_WIDTH)
        self.setFixedHeight(_LED_FIXED_HEIGHT)
        self.setAlignment(Qt.AlignCenter)
        if clickable:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip(f"QTRM-{qtrm_index} - click to link-test just this QTRM")
        else:
            # No individual-query action wired up wherever this instance is
            # used (e.g. Dwell's results display) - don't imply one via the
            # pointer cursor/tooltip text.
            self.setToolTip(f"QTRM-{qtrm_index}")
        self.set_color(_IDLE_COLOR)

    def mousePressEvent(self, event):
        if self.clickable and event.button() == Qt.LeftButton:
            self.clicked.emit(self.qtrm_index)
        super().mousePressEvent(event)

    def set_color(self, color: QColor):
        self.setStyleSheet(
            # Same 10px roundness as the Soft Reset/Isolation button matrix
            # (command_style.py's matrix_button_style) so both QTRM display
            # arrays share the same shape. Two gotchas learned fixing this:
            # (1) border-radius MUST come after border - Qt's "border"
            # shorthand resets border-radius back to 0 if declared before
            # it, which silently made every cell render square even though
            # this string contained "border-radius: ...". (2) radius must
            # stay under half of _LED_MIN_HEIGHT (24px) - Qt's QSS engine
            # doesn't clamp border-radius to half the box size like CSS
            # does, so a 16px radius on a 24px-tall cell also rendered
            # square; 10px is the value confirmed (via direct render test)
            # to actually round at the real minimum cell size.
            "border: 1px solid rgba(0, 0, 0, 60); border-radius: 10px;"
            f"background-color: rgb({color.red()}, {color.green()}, {color.blue()});"
            f"color: {_TEXT_COLOR}; font-size: 8pt; font-weight: 500; padding: 2px 4px;"
        )


class LedMatrix(QWidget):
    """
    Six 'CP' (Cold Plate) group boxes, each holding the 16 QTRMs on that
    connector (2 rows x 8 columns), stacked to match the real array -
    CP5 at the top down to CP0 at the bottom.

    clickable=False for a purely read-only results display (e.g. Dwell's
    matrix, which only ever shows the outcome of a single "Send Dwell" to
    all 96 QTRMs at once - there's no per-QTRM individual query to trigger
    from it) - cells then have no pointer cursor/click tooltip, since
    nothing happens on click. Link Test/Status keep the default
    clickable=True, since clicking one of their cells genuinely queries
    just that QTRM.
    """

    led_clicked = Signal(int)  # qtrm_index

    def __init__(self, clickable: bool = True, parent=None):
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
                led = _Led(qtrm_index, clickable=clickable)
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
        self._auto_resending = False
        self._resend_timer = QTimer(self)
        self._resend_timer.timeout.connect(lambda: self.send_requested.emit(True))

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        top_row = QHBoxLayout()
        self.send_btn = QPushButton("Send Link Test")
        self.send_btn.setStyleSheet(_SEND_BTN_STYLE)
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

        # HeaderPanel is now a single global full-height sidebar owned by
        # main_window.py, not embedded per-tab - see its module docstring.
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # -- full-array test (send button, with optional auto-resend) ---------

    def _on_send_btn_clicked(self):
        if self._auto_resending:
            self.stop_auto_resend()
            return

        interval_s = self.resend_spin.value()
        self.send_requested.emit(False)
        if interval_s > 0:
            self._auto_resending = True
            self.send_btn.setText("Stop")
            self._resend_timer.start(int(interval_s * 1000))

    def stop_auto_resend(self):
        """Stop the auto-resend timer if active - safe to call unconditionally
        (e.g. on disconnect) even when no resend is in progress."""
        self._resend_timer.stop()
        self._auto_resending = False
        self.send_btn.setText("Send Link Test")

    def mark_pending(self):
        # No artificial reveal delay - LEDs turn green/red the instant a
        # real response arrives (show_results), or red on an actual
        # timeout (show_no_response, driven by main_window.py's real
        # RESPONSE_TIMEOUT_MS wait) - "delay is only there if i dont
        # recieve a command", per Yuvraj. A fixed cosmetic delay here used
        # to hold results back for a full second even when the response
        # had already arrived.
        self.summary_label.setText("Sent - waiting for response...")
        self.response_time_label.setText("")
        self.led_matrix.set_all(_PENDING_COLOR)

    def show_results(self, linked_flags):
        linked_count = sum(1 for v in linked_flags if v)
        self.summary_label.setText(f"{linked_count}/{NUM_QTRM} QTRMs linked")
        self.led_matrix.set_results(linked_flags)

    def show_response_time(self, microseconds: float):
        self.response_time_label.setText(f"{microseconds:.0f} µs")

    def show_no_response(self):
        self.summary_label.setText("No response")
        self.response_time_label.setText("")
        self.led_matrix.set_all(_NOT_LINKED_COLOR)

    # -- individual QTRM test (click one LED) ------------------------------

    def _on_led_clicked(self, qtrm_index: int):
        self.individual_send_requested.emit(qtrm_index)

    def mark_individual_pending(self, qtrm_index: int):
        self.summary_label.setText(f"QTRM-{qtrm_index}: waiting for response...")
        self.response_time_label.setText("")
        self.led_matrix.set_all(_PENDING_COLOR)

    def show_individual_result(self, qtrm_index: int, linked: bool):
        self.led_matrix.set_one(qtrm_index, _LINKED_COLOR if linked else _NOT_LINKED_COLOR)
        self.summary_label.setText(f"QTRM-{qtrm_index}: {'Linked' if linked else 'Not linked'}")

    def show_individual_response_time(self, microseconds: float):
        self.response_time_label.setText(f"{microseconds:.0f} µs")

    def show_individual_no_response(self, qtrm_index: int):
        self.summary_label.setText(f"QTRM-{qtrm_index}: No response")
        self.led_matrix.set_one(qtrm_index, _NOT_LINKED_COLOR)
