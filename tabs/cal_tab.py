"""
cal_tab.py

Shared "RX Cal" / "TX Cal" tab (Sections 5/6 of the QTRM Message Format IDD).

Both calibration commands target ONE channel (1-4) of a single QTRM at a
time: that channel goes into cal mode using the given TRM Phase/
Attenuation, while the QTRM's other 3 channels drop into isolation
(handled by the QTRM's own firmware). At the array level, the other 95
QTRMs must be told to stand down too - the segmented control below picks
whether they get Rx Isolation or Tx Isolation while the one QTRM under
test is calibrated.

Laid out as a single card: a prominent title + divider, then three labeled
sections (QTRM Selection / Calibration Settings / Isolation Mode) each using
a two-column label-left / spinbox-right grid, ending in a full-width accent
send button.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from core.command_style import PENDING_COLOR as _PENDING_COLOR
from core.command_style import SUCCESS_COLOR as _LINKED_COLOR
from core.command_style import FAILURE_COLOR as _NOT_LINKED_COLOR
from core.command_style import indicator_style as _indicator_style
from core.command_style import send_button_style
from widgets.segmented_control import SegmentedControl
from widgets.spin_field import SpinField

NUM_QTRM = 96
PHASE_MAX = 63        # 6-bit phase (frame_type.vhd: No_of_phase_bits = 6)
ATTEN_MAX = 63         # 6-bit attenuation (frame_type.vhd: No_of_Attenuator_bits = 6)

_CARD_BG = "#393e46"
_BORDER = "#4a515a"
_ACCENT = "#00adb5"
_ACCENT_HOVER = "#1fc2ca"
_ACCENT_PRESSED = "#00858c"
_TEXT = "#eeeeee"
_LABEL_COLOR = "rgba(238, 238, 238, 0.62)"
_MUTED = "rgba(238, 238, 238, 0.45)"

_CARD_MIN_WIDTH = 480
_CARD_MAX_WIDTH = 640

_TITLE_MAP = {"RX Cal": "RX Calibration", "TX Cal": "TX Calibration"}

# Send button: a distinct purple, shared across every command tab's send
# button so they all read consistently - always this color, never changes,
# so its hover/pressed effect always works (matches how Dwell/Memory
# Operation's send buttons behave - they never recolor either; only a
# separate indicator below shows pending/success/failure). Colors/QSS now
# come from command_style.py, the single source of truth every command
# tab shares.
_SEND_BTN_STYLE = send_button_style(radius=12, font_size_px=14)


def _section_header(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color: {_ACCENT}; font-size: 15px; font-weight: 600; background: transparent;"
    )
    return lbl


def _field_row(grid: QGridLayout, row: int, label_text: str, spin: SpinField):
    label = QLabel(label_text)
    label.setStyleSheet(f"color: {_LABEL_COLOR}; font-size: 15px; background: transparent;")
    grid.addWidget(label, row, 0, Qt.AlignLeft | Qt.AlignVCenter)
    grid.addWidget(spin, row, 1, Qt.AlignRight | Qt.AlignVCenter)


class CalTab(QWidget):
    # qtrm_index (0-based), channel (1-4), phase, atten, tx_isolation_for_others
    send_requested = Signal(int, int, int, int, bool)

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self._title = title
        big_title = _TITLE_MAP.get(title, title)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)

        card = QFrame()
        card.setObjectName("CalCard")
        card.setStyleSheet(
            f"#CalCard {{ background-color: {_CARD_BG}; border: 1px solid {_BORDER};"
            "border-radius: 14px; }"
        )
        card.setMinimumWidth(_CARD_MIN_WIDTH)
        card.setMaximumWidth(_CARD_MAX_WIDTH)
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        form = QVBoxLayout(card)
        form.setContentsMargins(28, 26, 28, 26)
        form.setSpacing(22)

        # -- title + divider ------------------------------------------------
        title_label = QLabel(big_title)
        title_label.setStyleSheet(
            f"color: {_TEXT}; font-size: 22px; font-weight: 600; background: transparent;"
        )
        form.addWidget(title_label)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setStyleSheet(f"background-color: {_BORDER}; max-height: 1px; border: none;")
        form.addWidget(divider)

        # -- QTRM Selection / Calibration Settings, side by side -------------
        # Two columns in one row instead of two stacked sections - halves
        # the vertical span these 4 fields used to take, per Yuvraj's ask
        # to reduce the card's overall height.
        two_col_row = QHBoxLayout()
        two_col_row.setSpacing(28)

        selection_col = QVBoxLayout()
        selection_col.setSpacing(14)
        selection_col.addWidget(_section_header("QTRM Selection"))
        selection_grid = QGridLayout()
        selection_grid.setHorizontalSpacing(18)
        selection_grid.setVerticalSpacing(14)
        selection_grid.setColumnStretch(0, 1)
        self.qtrm_spin = SpinField(0, NUM_QTRM - 1, 0, field_width=76)
        self.channel_spin = SpinField(1, 4, 1, field_width=76)
        _field_row(selection_grid, 0, "Target QTRM", self.qtrm_spin)
        _field_row(selection_grid, 1, "Channel", self.channel_spin)
        selection_col.addLayout(selection_grid)

        cal_col = QVBoxLayout()
        cal_col.setSpacing(14)
        cal_col.addWidget(_section_header("Calibration Settings"))
        cal_grid = QGridLayout()
        cal_grid.setHorizontalSpacing(18)
        cal_grid.setVerticalSpacing(14)
        cal_grid.setColumnStretch(0, 1)
        self.phase_spin = SpinField(0, PHASE_MAX, 0, field_width=76)
        self.atten_spin = SpinField(0, ATTEN_MAX, 0, field_width=76)
        _field_row(cal_grid, 0, "TRM Phase", self.phase_spin)
        _field_row(cal_grid, 1, "TRM Attenuation", self.atten_spin)
        cal_col.addLayout(cal_grid)

        two_col_row.addLayout(selection_col, 1)
        two_col_row.addLayout(cal_col, 1)
        form.addLayout(two_col_row)

        # -- Isolation Mode ---------------------------------------------------
        form.addWidget(_section_header("Isolation Mode"))
        iso_caption = QLabel("Other 95 QTRMs receive")
        iso_caption.setStyleSheet(f"color: {_LABEL_COLOR}; font-size: 15px; background: transparent;")
        form.addWidget(iso_caption)
        self.isolation_switch = SegmentedControl("Rx Isolation", "Tx Isolation")
        form.addWidget(self.isolation_switch)

        # -- send button + status indicator + response time --------------------
        self.send_btn = QPushButton(f"Send {big_title}")
        self.send_btn.setFixedHeight(46)
        self.send_btn.setStyleSheet(_SEND_BTN_STYLE)
        self.send_btn.clicked.connect(self._on_send_clicked)
        form.addWidget(self.send_btn)

        self.status_indicator = QLabel("Not sent yet")
        self.status_indicator.setAlignment(Qt.AlignCenter)
        self.status_indicator.setFixedHeight(28)
        self.status_indicator.setStyleSheet(_indicator_style())
        form.addWidget(self.status_indicator)

        self.response_time_label = QLabel("")
        self.response_time_label.setAlignment(Qt.AlignCenter)
        self.response_time_label.setStyleSheet(f"color: {_MUTED}; font-size: 12px; background: transparent;")
        form.addWidget(self.response_time_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        scroll.setWidget(card)

        # HeaderPanel is now a single global full-height sidebar owned by
        # main_window.py, not embedded per-tab - see its module docstring.
        outer.addWidget(scroll)

    def _on_send_clicked(self):
        qtrm_index = self.qtrm_spin.value()
        self.send_requested.emit(
            qtrm_index,
            self.channel_spin.value(),
            self.phase_spin.value(),
            self.atten_spin.value(),
            self.isolation_switch.isChecked(),
        )

    def reset_to_idle(self):
        # Without this, the indicator is left showing whatever
        # pending/linked/not-linked state its last send ended in, forever,
        # even after switching tabs away and back - nothing else ever
        # restores the idle look.
        self.response_time_label.setText("")
        self.status_indicator.setText("Not sent yet")
        self.status_indicator.setStyleSheet(_indicator_style())

    def mark_pending(self):
        self.response_time_label.setText("Sending...")
        self.status_indicator.setText("Sending...")
        self.status_indicator.setStyleSheet(_indicator_style(_PENDING_COLOR))

    def show_response_time(self, microseconds: float):
        self.response_time_label.setText(f"{microseconds:.0f} µs")

    def show_result(self, linked: bool):
        self.status_indicator.setText("Linked" if linked else "Not Linked")
        self.status_indicator.setStyleSheet(_indicator_style(_LINKED_COLOR if linked else _NOT_LINKED_COLOR))

    def show_no_response(self):
        self.response_time_label.setText("No response")
        self.status_indicator.setText("No Response")
        self.status_indicator.setStyleSheet(_indicator_style(_NOT_LINKED_COLOR))
