"""
timing_tab.py

"Timing Generation" - the Mode 1/2 (Internal/External Loopback) SOB/PRT/PPS
sub-commands from QCC_90Byte_Header_BitTable.docx's "TX PACKET (RC -> QCC) -
MODE 1 / 2" tables. Unlike every other command tab (which targets individual
QTRMs via the 2880-byte QTRM data block), these three commands live entirely
in the 90-byte header's Message Body (byte 34 = COMMAND_TYPE selecting
SOB/PRT/PPS, bytes 35-89 = that command's fields) - the QTRM data block is
unused, per build_header_only_frame in packet.py.

Page is split into three vertical partitions side by side (SOB | PRT |
PPS), one per timing signal, each with its own settings and its own
dedicated Send button - SOB and PRT can run in either Internal or External
Loopback (a segmented control per section); PPS is External Loopback only,
per the doc, so it has no such switch. main_window.py's single global
HeaderPanel sidebar shows whichever command's response arrived most
recently, same as every other tab.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from command_style import PENDING_COLOR as _PENDING_COLOR
from command_style import SUCCESS_COLOR as _OK_COLOR
from command_style import FAILURE_COLOR as _FAIL_COLOR
from command_style import indicator_style as _indicator_style_base
from command_style import send_button_style
from packet import PRT_COUNT_INFINITE
from segmented_control import SegmentedControl
from spin_field import SpinField

_ACCENT = "#00adb5"
_TEXT = "#eeeeee"
_LABEL_COLOR = "rgba(238, 238, 238, 0.62)"
_MUTED = "rgba(238, 238, 238, 0.45)"
_BORDER = "#4a515a"

# u32 field ceiling - QSpinBox is backed by a 32-bit signed int, so the true
# max u32 (0xFFFFFFFF) isn't representable; PRT_COUNT's all-ones "infinite"
# sentinel is handled separately via the Infinite checkbox instead.
_U32_SPIN_MAX = 2_147_483_647

# Colors/QSS from command_style.py, the single source of truth every
# command tab shares - this file only picks its own radius/padding.
_SEND_BTN_STYLE = send_button_style(radius=12, font_size_px=14, padding="10px")


def _indicator_style(bg_color: str = None) -> str:
    return _indicator_style_base(bg_color, radius=12, border_color=_BORDER)


_COLUMN_MIN_WIDTH = 300


def _vertical_divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.VLine)
    line.setStyleSheet(f"background-color: {_BORDER}; max-width: 1px; border: none;")
    return line


def _section_box(title: str) -> tuple:
    """
    A column's outer QFrame plus the QVBoxLayout the rest of its content goes
    into - already seeded with a centered banner-style heading (bigger,
    bold, accent-colored, full column width) and a divider underneath, the
    same title+divider pairing cal_tab.py uses for its single card, so each
    of these three side-by-side columns reads as its own clearly headed
    section rather than a cramped QGroupBox corner label.
    """
    box = QFrame()
    box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    box.setMinimumWidth(_COLUMN_MIN_WIDTH)

    outer = QVBoxLayout(box)
    # Standard 18px inner padding on all sides, uniform across all 3
    # section columns (was asymmetric before: 14/6/14/10).
    outer.setContentsMargins(18, 18, 18, 18)
    outer.setSpacing(10)

    heading = QLabel(title)
    heading.setAlignment(Qt.AlignCenter)
    heading.setStyleSheet(
        f"color: {_ACCENT}; font-size: 17px; font-weight: 700; background: transparent; padding: 4px 0px;"
    )
    outer.addWidget(heading)

    divider = QFrame()
    divider.setFrameShape(QFrame.HLine)
    divider.setStyleSheet(f"background-color: {_BORDER}; max-height: 1px; border: none;")
    outer.addWidget(divider)

    form = QVBoxLayout()
    form.setSpacing(10)
    outer.addLayout(form, 1)

    return box, form


def _field_row(grid: QGridLayout, row: int, label_text: str, field) -> None:
    label = QLabel(label_text)
    label.setStyleSheet(f"color: {_LABEL_COLOR}; font-size: 13px; background: transparent;")
    grid.addWidget(label, row, 0, Qt.AlignLeft | Qt.AlignVCenter)
    grid.addWidget(field, row, 1, Qt.AlignRight | Qt.AlignVCenter)


class TimingTab(QWidget):
    # external_loopback (bool), sob_width_us
    sob_send_requested = Signal(bool, int)
    # external_loopback (bool), prt_count, pri_width_us, prt_width_us. prt_count
    # is declared 'uint' (not the default signed int) - PRT_COUNT_INFINITE
    # (0xFFFFFFFF) overflows a signed 32-bit int and raises OverflowError at
    # emit time otherwise.
    prt_send_requested = Signal(bool, 'uint', int, int)
    # pps_width_us - always External Loopback
    pps_send_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)

        content = QWidget()
        # Three vertical partitions side by side (SOB | PRT | PPS), each its
        # own column with a dedicated Send button - not stacked rows - so all
        # three signals are visible and comparable at once, separated by an
        # explicit divider line rather than relying on each box's own border.
        root = QHBoxLayout(content)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(0)

        root.addWidget(self._build_sob_section(), 1)
        root.addWidget(_vertical_divider())
        root.addWidget(self._build_prt_section(), 1)
        root.addWidget(_vertical_divider())
        root.addWidget(self._build_pps_section(), 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)

        # HeaderPanel is now a single global full-height sidebar owned by
        # main_window.py, not embedded per-tab.
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # -- section builders ---------------------------------------------------

    def _build_sob_section(self):
        box, form = _section_box("SOB (Start of Burst)")

        self.sob_loopback_switch = SegmentedControl("Internal Loopback", "External Loopback")
        form.addWidget(self.sob_loopback_switch)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(0, 1)
        self.sob_width_spin = SpinField(0, 65535, 0, field_width=90)
        _field_row(grid, 0, "SOB Width (µs)", self.sob_width_spin)
        form.addLayout(grid)

        # Stretch BEFORE the button block (not after) - this pushes the
        # Send button + its status/response labels down to the bottom of
        # the column, so all 3 sections' buttons land at the same Y
        # position regardless of how many fields are above them (SOB/PPS
        # have 1 field, PRT has 4) - every column shares the same total
        # height (Expanding size policy, same QHBoxLayout row), so the
        # stretch absorbs exactly the difference.
        form.addStretch(1)

        self.sob_send_btn = QPushButton("Send SOB Command")
        self.sob_send_btn.setFixedHeight(38)
        self.sob_send_btn.setStyleSheet(_SEND_BTN_STYLE)
        self.sob_send_btn.clicked.connect(self._on_sob_send_clicked)
        form.addWidget(self.sob_send_btn)
        self.sob_status = QLabel("Not sent yet")
        self.sob_status.setAlignment(Qt.AlignCenter)
        self.sob_status.setFixedHeight(28)
        self.sob_status.setStyleSheet(_indicator_style())
        form.addWidget(self.sob_status)

        self.sob_response_time_label = QLabel("")
        self.sob_response_time_label.setAlignment(Qt.AlignCenter)
        self.sob_response_time_label.setStyleSheet(f"color: {_MUTED}; font-size: 11px; background: transparent;")
        form.addWidget(self.sob_response_time_label)

        return box

    def _build_prt_section(self):
        box, form = _section_box("PRT (Pulse Repetition Train)")

        self.prt_loopback_switch = SegmentedControl("Internal Loopback", "External Loopback")
        form.addWidget(self.prt_loopback_switch)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(0, 1)
        self.prt_count_spin = SpinField(0, _U32_SPIN_MAX, 0, field_width=110)
        _field_row(grid, 0, "PRT Count", self.prt_count_spin)
        self.prt_infinite_check = QCheckBox("Infinite (0xFFFFFFFF)")
        self.prt_infinite_check.setStyleSheet(f"color: {_LABEL_COLOR}; font-size: 12px;")
        self.prt_infinite_check.toggled.connect(self._on_prt_infinite_toggled)
        grid.addWidget(self.prt_infinite_check, 1, 1, Qt.AlignRight | Qt.AlignVCenter)
        self.prt_pri_spin = SpinField(0, _U32_SPIN_MAX, 0, field_width=110)
        _field_row(grid, 2, "PRI Width (µs)", self.prt_pri_spin)
        self.prt_width_spin = SpinField(0, 65535, 0, field_width=110)
        _field_row(grid, 3, "PRT Width (µs)", self.prt_width_spin)
        form.addLayout(grid)

        form.addStretch(1)

        self.prt_send_btn = QPushButton("Send PRT Command")
        self.prt_send_btn.setFixedHeight(38)
        self.prt_send_btn.setStyleSheet(_SEND_BTN_STYLE)
        self.prt_send_btn.clicked.connect(self._on_prt_send_clicked)
        form.addWidget(self.prt_send_btn)
        self.prt_status = QLabel("Not sent yet")
        self.prt_status.setAlignment(Qt.AlignCenter)
        self.prt_status.setFixedHeight(28)
        self.prt_status.setStyleSheet(_indicator_style())
        form.addWidget(self.prt_status)

        self.prt_response_time_label = QLabel("")
        self.prt_response_time_label.setAlignment(Qt.AlignCenter)
        self.prt_response_time_label.setStyleSheet(f"color: {_MUTED}; font-size: 11px; background: transparent;")
        form.addWidget(self.prt_response_time_label)

        return box

    def _build_pps_section(self):
        box, form = _section_box("PPS (Pulse Per Second)")

        note = QLabel("External Loopback only, per the IDD")
        note.setStyleSheet(f"color: {_MUTED}; font-size: 11px; font-style: italic; background: transparent;")
        form.addWidget(note)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(0, 1)
        self.pps_width_spin = SpinField(0, 65535, 0, field_width=90)
        _field_row(grid, 0, "PPS Width (µs)", self.pps_width_spin)
        form.addLayout(grid)

        form.addStretch(1)

        self.pps_send_btn = QPushButton("Send PPS Command")
        self.pps_send_btn.setFixedHeight(38)
        self.pps_send_btn.setStyleSheet(_SEND_BTN_STYLE)
        self.pps_send_btn.clicked.connect(self._on_pps_send_clicked)
        form.addWidget(self.pps_send_btn)
        self.pps_status = QLabel("Not sent yet")
        self.pps_status.setAlignment(Qt.AlignCenter)
        self.pps_status.setFixedHeight(28)
        self.pps_status.setStyleSheet(_indicator_style())
        form.addWidget(self.pps_status)

        self.pps_response_time_label = QLabel("")
        self.pps_response_time_label.setAlignment(Qt.AlignCenter)
        self.pps_response_time_label.setStyleSheet(f"color: {_MUTED}; font-size: 11px; background: transparent;")
        form.addWidget(self.pps_response_time_label)

        return box

    # -- send handlers -------------------------------------------------------

    def _on_prt_infinite_toggled(self, checked: bool):
        self.prt_count_spin.spin.setEnabled(not checked)

    def _on_sob_send_clicked(self):
        self.sob_send_requested.emit(self.sob_loopback_switch.isChecked(), self.sob_width_spin.value())

    def _on_prt_send_clicked(self):
        prt_count = PRT_COUNT_INFINITE if self.prt_infinite_check.isChecked() else self.prt_count_spin.value()
        self.prt_send_requested.emit(
            self.prt_loopback_switch.isChecked(), prt_count,
            self.prt_pri_spin.value(), self.prt_width_spin.value(),
        )

    def _on_pps_send_clicked(self):
        self.pps_send_requested.emit(self.pps_width_spin.value())

    # -- status indicators, driven by main_window.py's send/response/timeout handlers --

    def mark_sob_pending(self):
        self.sob_response_time_label.setText("Sending...")
        self.sob_status.setText("Sending...")
        self.sob_status.setStyleSheet(_indicator_style(_PENDING_COLOR))

    def show_sob_response_time(self, microseconds: float):
        self.sob_response_time_label.setText(f"{microseconds:.0f} µs")

    def show_sob_result(self, checksum_ok: bool):
        self.sob_status.setText("Response OK" if checksum_ok else "Checksum FAIL")
        self.sob_status.setStyleSheet(_indicator_style(_OK_COLOR if checksum_ok else _FAIL_COLOR))

    def show_sob_no_response(self):
        self.sob_response_time_label.setText("No response")
        self.sob_status.setText("No Response")
        self.sob_status.setStyleSheet(_indicator_style(_FAIL_COLOR))

    def mark_prt_pending(self):
        self.prt_response_time_label.setText("Sending...")
        self.prt_status.setText("Sending...")
        self.prt_status.setStyleSheet(_indicator_style(_PENDING_COLOR))

    def show_prt_response_time(self, microseconds: float):
        self.prt_response_time_label.setText(f"{microseconds:.0f} µs")

    def show_prt_result(self, checksum_ok: bool):
        self.prt_status.setText("Response OK" if checksum_ok else "Checksum FAIL")
        self.prt_status.setStyleSheet(_indicator_style(_OK_COLOR if checksum_ok else _FAIL_COLOR))

    def show_prt_no_response(self):
        self.prt_response_time_label.setText("No response")
        self.prt_status.setText("No Response")
        self.prt_status.setStyleSheet(_indicator_style(_FAIL_COLOR))

    def mark_pps_pending(self):
        self.pps_response_time_label.setText("Sending...")
        self.pps_status.setText("Sending...")
        self.pps_status.setStyleSheet(_indicator_style(_PENDING_COLOR))

    def show_pps_response_time(self, microseconds: float):
        self.pps_response_time_label.setText(f"{microseconds:.0f} µs")

    def show_pps_result(self, checksum_ok: bool):
        self.pps_status.setText("Response OK" if checksum_ok else "Checksum FAIL")
        self.pps_status.setStyleSheet(_indicator_style(_OK_COLOR if checksum_ok else _FAIL_COLOR))

    def show_pps_no_response(self):
        self.pps_response_time_label.setText("No response")
        self.pps_status.setText("No Response")
        self.pps_status.setStyleSheet(_indicator_style(_FAIL_COLOR))

    def reset_to_idle(self):
        for status, resp_label in (
            (self.sob_status, self.sob_response_time_label),
            (self.prt_status, self.prt_response_time_label),
            (self.pps_status, self.pps_response_time_label),
        ):
            status.setText("Not sent yet")
            status.setStyleSheet(_indicator_style())
            resp_label.setText("")
