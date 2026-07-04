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

from header_panel import HeaderPanel
from segmented_control import SegmentedControl
from spin_field import SpinField

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

_CARD_MIN_WIDTH = 400
_CARD_MAX_WIDTH = 540

_TITLE_MAP = {"RX Cal": "RX Calibration", "TX Cal": "TX Calibration"}

# Send-button states: idle (a distinct purple, shared across every command
# tab's send button so they all read consistently), grey while waiting on
# the targeted QTRM's Link-type response, green if it replied, red if the
# 1s timeout elapsed without one.
_SEND_COLOR = "#7C3AED"
_SEND_HOVER_COLOR = "#6D28D9"
_SEND_PRESSED_COLOR = "#5B21B6"
_PENDING_COLOR = "rgb(160, 165, 172)"
_LINKED_COLOR = "rgb(146, 208, 165)"
_NOT_LINKED_COLOR = "rgb(240, 149, 149)"
_STATE_TEXT_COLOR = "#1f2328"


def _send_button_style(bg_color: str = None) -> str:
    if bg_color is None:
        return (
            f"QPushButton {{ background-color: {_SEND_COLOR}; color: {_TEXT}; border: none;"
            "border-radius: 12px; font-size: 14px; font-weight: 600; }"
            f"QPushButton:hover {{ background-color: {_SEND_HOVER_COLOR}; }}"
            f"QPushButton:pressed {{ background-color: {_SEND_PRESSED_COLOR}; }}"
        )
    return (
        f"QPushButton {{ background-color: {bg_color}; color: {_STATE_TEXT_COLOR}; border: none;"
        "border-radius: 12px; font-size: 14px; font-weight: 600; }"
        f"QPushButton:hover {{ background-color: {bg_color}; }}"
        f"QPushButton:pressed {{ background-color: {bg_color}; }}"
    )


def _section_header(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color: {_ACCENT}; font-size: 15px; font-weight: 600; background: transparent;"
    )
    return lbl


def _field_row(grid: QGridLayout, row: int, label_text: str, spin: SpinField):
    label = QLabel(label_text)
    label.setStyleSheet(f"color: {_LABEL_COLOR}; font-size: 13px; background: transparent;")
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
        outer.setContentsMargins(0, 0, 0, 0)

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

        # -- QTRM Selection ---------------------------------------------------
        form.addWidget(_section_header("QTRM Selection"))
        selection_grid = QGridLayout()
        selection_grid.setHorizontalSpacing(18)
        selection_grid.setVerticalSpacing(14)
        selection_grid.setColumnStretch(0, 1)
        self.qtrm_spin = SpinField(0, NUM_QTRM - 1, 0, field_width=76)
        self.channel_spin = SpinField(1, 4, 1, field_width=76)
        _field_row(selection_grid, 0, "Target QTRM", self.qtrm_spin)
        _field_row(selection_grid, 1, "Channel", self.channel_spin)
        form.addLayout(selection_grid)

        # -- Calibration Settings --------------------------------------------
        form.addWidget(_section_header("Calibration Settings"))
        cal_grid = QGridLayout()
        cal_grid.setHorizontalSpacing(18)
        cal_grid.setVerticalSpacing(14)
        cal_grid.setColumnStretch(0, 1)
        self.phase_spin = SpinField(0, PHASE_MAX, 0, field_width=76)
        self.atten_spin = SpinField(0, ATTEN_MAX, 0, field_width=76)
        _field_row(cal_grid, 0, "TRM Phase", self.phase_spin)
        _field_row(cal_grid, 1, "TRM Attenuation", self.atten_spin)
        form.addLayout(cal_grid)

        # -- Isolation Mode ---------------------------------------------------
        form.addWidget(_section_header("Isolation Mode"))
        iso_caption = QLabel("Other 95 QTRMs receive")
        iso_caption.setStyleSheet(f"color: {_LABEL_COLOR}; font-size: 13px; background: transparent;")
        form.addWidget(iso_caption)
        self.isolation_switch = SegmentedControl("Rx Isolation", "Tx Isolation")
        form.addWidget(self.isolation_switch)

        # -- send button + response time --------------------------------------
        self.send_btn = QPushButton(f"Send {big_title}")
        self.send_btn.setFixedHeight(46)
        self.send_btn.setStyleSheet(_send_button_style())
        self.send_btn.clicked.connect(self._on_send_clicked)
        form.addWidget(self.send_btn)

        self.response_time_label = QLabel("")
        self.response_time_label.setAlignment(Qt.AlignCenter)
        self.response_time_label.setStyleSheet(f"color: {_MUTED}; font-size: 12px; background: transparent;")
        form.addWidget(self.response_time_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        scroll.setWidget(card)

        # Dedicated right-side space for the raw 90-byte header of whatever
        # frame this tab most recently received - outside the scroll area so
        # it's always visible regardless of scroll position.
        self.header_panel = HeaderPanel()

        outer.addWidget(scroll, 1)
        outer.addWidget(self.header_panel)

    def _on_send_clicked(self):
        qtrm_index = self.qtrm_spin.value()
        self.send_requested.emit(
            qtrm_index,
            self.channel_spin.value(),
            self.phase_spin.value(),
            self.atten_spin.value(),
            self.isolation_switch.isChecked(),
        )

    def mark_pending(self):
        self.response_time_label.setText("Sending...")
        self.send_btn.setStyleSheet(_send_button_style(_PENDING_COLOR))

    def show_response_time(self, microseconds: float):
        self.response_time_label.setText(f"{microseconds:.0f} µs")

    def show_result(self, linked: bool):
        self.send_btn.setStyleSheet(_send_button_style(_LINKED_COLOR if linked else _NOT_LINKED_COLOR))

    def show_no_response(self):
        self.response_time_label.setText("No response")
        self.send_btn.setStyleSheet(_send_button_style(_NOT_LINKED_COLOR))
