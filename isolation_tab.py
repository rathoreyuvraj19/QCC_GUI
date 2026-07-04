"""
isolation_tab.py

"Isolation" - Rx/Tx Isolation command (Section 7/8 of the QTRM Message
Format IDD). Unlike Soft Reset, this command now requests a Link-type
status response (per Yuvraj's spec: every command except Soft Reset does),
so the GUI waits up to RESPONSE_TIMEOUT_MS for it and colors the relevant
button(s) - grey while pending, green if the QTRM replied, red if not.

A segmented control picks whether "Send All" or an individual QTRM click
sends Rx Isolation or Tx Isolation. "Send All" sends the chosen isolation
command to every QTRM and colors both the button itself (green only if
every QTRM responded) and each of the 96 matrix buttons individually.
Clicking one matrix button sends it to only that QTRM - every other QTRM's
slot is left entirely zero-filled (no header, no command at all, matching
Soft Reset's individual-target convention). Only the clicked button greys
out and then reveals green/red - every other button resets to its idle
color, since only the clicked QTRM is actually being queried by that send
(unlike Link Test's individual LEDs, which grey the whole array).
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from header_panel import HeaderPanel
from qtrm_layout import NUM_QTRM, MATRIX_COLS, group_grid_positions, groups_top_to_bottom
from segmented_control import SegmentedControl

_BUTTON_BASE = "padding: 2px 4px; font-size: 8pt; font-weight: 500;"
_BUTTON_MIN_WIDTH = 46
_BUTTON_MIN_HEIGHT = 24

# Same borderless compact style as link_test_tab.py / soft_reset_tab.py - the
# global QGroupBox card padding/margin is sized for one large standalone
# card, not six stacked Cold Plate boxes.
_CP_BOX_STYLE = (
    "QGroupBox { border: none; background: transparent; margin-top: 6px; padding: 4px 0px 0px 0px; }"
    "QGroupBox::title { subcontrol-origin: margin; left: 2px; padding: 0 2px; }"
)

# Matrix button states match Link Test's LED palette exactly (light
# grey/idle, darker grey/pending, green/linked, red/not-linked) - each of
# these 96 buttons is a per-QTRM status indicator, same role as one of Link
# Test's LEDs, so it should look like one. This is distinct from "Send
# All", which stays a fixed purple always (matching every other command
# tab's send button) since it represents an action, not a single QTRM's
# status.
_IDLE_COLOR = "rgb(222, 224, 227)"
_IDLE_HOVER_COLOR = "rgb(200, 203, 208)"
_IDLE_PRESSED_COLOR = "rgb(180, 184, 190)"
_PENDING_COLOR = "rgb(160, 165, 172)"
_LINKED_COLOR = "rgb(146, 208, 165)"
_NOT_LINKED_COLOR = "rgb(240, 149, 149)"
_STATE_TEXT_COLOR = "#1f2328"

# Send button color - shared across every command tab's primary send button
# so they all read consistently, distinct from the app's default teal.
_SEND_BTN_STYLE = (
    "QPushButton { background-color: #7C3AED; color: #eeeeee; border: none;"
    "border-radius: 16px; padding: 11px 24px; font-weight: 600; }"
    "QPushButton:hover { background-color: #6D28D9; }"
    "QPushButton:pressed { background-color: #5B21B6; }"
)


def _matrix_button_style(bg_color: str = None) -> str:
    # Full "QPushButton { ... }" selector form (not the flat property-only
    # form) - QSS pseudo-states like :hover/:pressed only apply inside a
    # selector block, otherwise the button looks flat/unresponsive on click
    # even though it's still functionally clickable either way. Idle gets
    # real hover/pressed shading since it's meant to be clicked; the
    # pending/linked/not-linked states stay flat - they're status snapshots.
    if bg_color is None:
        return (
            f"QPushButton {{ {_BUTTON_BASE} background-color: {_IDLE_COLOR}; color: {_STATE_TEXT_COLOR}; }}"
            f"QPushButton:hover {{ background-color: {_IDLE_HOVER_COLOR}; }}"
            f"QPushButton:pressed {{ background-color: {_IDLE_PRESSED_COLOR}; }}"
        )
    return (
        f"QPushButton {{ {_BUTTON_BASE} background-color: {bg_color}; color: {_STATE_TEXT_COLOR}; }}"
        f"QPushButton:hover {{ background-color: {bg_color}; }}"
        f"QPushButton:pressed {{ background-color: {bg_color}; }}"
    )


class IsolationTab(QWidget):
    send_all_requested = Signal(bool)       # tx_isolation
    send_one_requested = Signal(int, bool)  # qtrm_index (0-based), tx_isolation

    def __init__(self, parent=None):
        super().__init__(parent)

        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)

        top_row = QHBoxLayout()
        top_row.addStretch(1)
        top_row.addWidget(QLabel("Mode:"))
        self.mode_switch = SegmentedControl("Rx Isolation", "Tx Isolation")
        top_row.addWidget(self.mode_switch)
        # Always the app's default teal look - unlike the 96 matrix buttons,
        # this button doesn't represent any single QTRM's result, so it never
        # gets recolored by mark_all_pending/show_all_results/show_all_no_response.
        self.send_all_btn = QPushButton("Send All")
        self.send_all_btn.setStyleSheet(_SEND_BTN_STYLE)
        self.send_all_btn.clicked.connect(self._on_send_all_clicked)
        top_row.addWidget(self.send_all_btn)
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
                # Expanding + a minimumSize floor: fills its grid cell and
                # grows/shrinks with the window, but never clips its label.
                btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                btn.setMinimumSize(_BUTTON_MIN_WIDTH, _BUTTON_MIN_HEIGHT)
                btn.setStyleSheet(_matrix_button_style())
                btn.clicked.connect(self._make_send_one_handler(qtrm_index))
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

        # Dedicated right-side space for the raw 90-byte header of whatever
        # frame this tab most recently received - outside the scroll area so
        # it's always visible regardless of scroll position.
        self.header_panel = HeaderPanel()

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll, 1)
        outer.addWidget(self.header_panel)

    def _on_send_all_clicked(self):
        self.send_all_requested.emit(self.mode_switch.isChecked())

    def _make_send_one_handler(self, qtrm_index: int):
        def handler():
            self.send_one_requested.emit(qtrm_index, self.mode_switch.isChecked())
        return handler

    # -- coloring, driven by main_window.py's send/response/timeout handlers --

    def _set_matrix_color(self, qtrm_index: int, color: str):
        self._buttons[qtrm_index].setStyleSheet(_matrix_button_style(color))

    def _set_all_matrix_color(self, color: str):
        for btn in self._buttons:
            btn.setStyleSheet(_matrix_button_style(color))

    def mark_all_pending(self):
        # Send All itself always stays its idle purple - only the 96 matrix
        # buttons (each representing one QTRM's actual result) change color.
        self._set_all_matrix_color(_PENDING_COLOR)

    def show_all_results(self, linked_flags):
        for btn, linked in zip(self._buttons, linked_flags):
            btn.setStyleSheet(_matrix_button_style(_LINKED_COLOR if linked else _NOT_LINKED_COLOR))

    def show_all_no_response(self):
        self._set_all_matrix_color(_NOT_LINKED_COLOR)

    def mark_individual_pending(self, qtrm_index: int):
        # Only the clicked QTRM's button greys out - every other button
        # resets to its idle color, since only this one QTRM is actually
        # being queried by this send (unlike Link Test's individual mode,
        # which greys the whole array).
        for i, btn in enumerate(self._buttons):
            btn.setStyleSheet(_matrix_button_style(_PENDING_COLOR if i == qtrm_index else None))

    def show_individual_result(self, qtrm_index: int, linked: bool):
        self._set_matrix_color(qtrm_index, _LINKED_COLOR if linked else _NOT_LINKED_COLOR)

    def show_individual_no_response(self, qtrm_index: int):
        self._set_matrix_color(qtrm_index, _NOT_LINKED_COLOR)
