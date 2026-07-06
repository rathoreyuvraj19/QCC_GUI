"""
soft_reset_tab.py

"Soft Reset" - Soft Reset command (Section 9 of the QTRM Message Format IDD).

Fixed command, no configurable delay (not implemented in the QTRM firmware).
No response is expected either, so unlike the other tabs there's no
response-time tracking here - it's fire-and-forget. "Reset All" sends the
Soft Reset command to every QTRM. Clicking one button in the matrix resets
only that QTRM - every other QTRM's slot is left entirely zero-filled (no
header, no command at all), not just re-sent with a no-op command.

Buttons fill their whole grid cell (Expanding size policy, no alignment
override on addWidget) so they grow/shrink with the window instead of
staying at a fixed compact size with growing gaps around them. A
setMinimumSize floor keeps "QTRM-95" from clipping at very small sizes.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGridLayout, QGroupBox, QHBoxLayout, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from command_style import matrix_button_style, send_button_style
from qtrm_layout import NUM_QTRM, MATRIX_COLS, group_grid_positions, groups_top_to_bottom

# Matches Link Test's LED / Isolation's matrix button idle color exactly -
# these 96 buttons are per-QTRM identifiers just like those, so they should
# look the same. "Reset All" gets the shared purple send-button color
# instead (see _SEND_BTN_STYLE) - it's an action button, not a per-QTRM
# status indicator. Colors/QSS now come from command_style.py, the single
# source of truth every command tab shares.
_BUTTON_STYLE = matrix_button_style()
_BUTTON_MIN_WIDTH = 46
_BUTTON_MIN_HEIGHT = 24

_SEND_BTN_STYLE = send_button_style()

# See link_test_tab.py's _CP_BOX_STYLE for why this override exists - no
# drawn box/border, just the "CP{n}" title text above each group so the
# buttons can use the full width instead of being boxed into a bordered card.
_CP_BOX_STYLE = (
    "QGroupBox { border: none; background: transparent; margin-top: 6px; padding: 4px 0px 0px 0px; }"
    "QGroupBox::title { subcontrol-origin: margin; left: 2px; padding: 0 2px; }"
)


class SoftResetTab(QWidget):
    reset_all_requested = Signal()
    reset_one_requested = Signal(int)     # qtrm_index (0-based)

    def __init__(self, parent=None):
        super().__init__(parent)

        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)

        top_row = QHBoxLayout()
        top_row.addStretch(1)
        self.reset_all_btn = QPushButton("Reset All")
        self.reset_all_btn.setStyleSheet(_SEND_BTN_STYLE)
        self.reset_all_btn.clicked.connect(self.reset_all_requested.emit)
        top_row.addWidget(self.reset_all_btn)
        top_row.addStretch(1)
        root.addLayout(top_row)

        # Six Cold Plate (CP0-CP5) group boxes, stacked CP5 at top to CP0 at
        # bottom, each holding its 16 QTRMs - matches the real array layout.
        self._buttons = [None] * NUM_QTRM
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
                btn = QPushButton(f"QTRM-{qtrm_index}")
                # Expanding so the button fills its whole grid cell instead
                # of staying at its natural size with empty space around it.
                # setMinimumSize (not setFixedSize) keeps a floor so the
                # label never clips, while still letting Qt shrink below that
                # floor without overlap if the window gets extremely small.
                btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                btn.setMinimumSize(_BUTTON_MIN_WIDTH, _BUTTON_MIN_HEIGHT)
                btn.setStyleSheet(_BUTTON_STYLE)
                btn.clicked.connect(self._make_reset_one_handler(qtrm_index))
                self._buttons[qtrm_index] = btn
                grid.addWidget(btn, local_row, local_col)

            root.addWidget(cp_box, 1)

        # Wrapped in a QScrollArea so this tab's minimumSizeHint stays small
        # (bounded by the scroll area itself, not the 96-button matrix's
        # natural size) - lets the whole window shrink to fit any screen,
        # with scrollbars appearing instead of the window refusing to
        # shrink. Same pattern already used by cal_tab.py.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)

        # HeaderPanel is now a single global full-height sidebar owned by
        # main_window.py, not embedded per-tab - see its module docstring.
        # Soft Reset never gets a response (fire-and-forget), so it stays
        # at its placeholder "-" whenever this tab is active.
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _make_reset_one_handler(self, qtrm_index: int):
        def handler():
            self.reset_one_requested.emit(qtrm_index)
        return handler
