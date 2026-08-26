"""
remote_programming_tab.py

"Remote Programming" - pushes a firmware bitstream (.spi) from the host PC
to every LRU via QCC and drives the firmware-update state machine
(mode change -> link check -> LRU info -> upload -> authenticate ->
program -> verify -> return to high speed). Pure view: every action is a
Signal out to main_window (which delegates to RemoteProgController, the
session state machine in remote_prog_controller.py), every display change
is a slot the controller's signals feed.

Layout mirrors timing_tab.py's three side-by-side section columns
(Link Setup | Firmware Image | Operations), with the per-operation results
area (Link grid / LRU table / live IAP grid / Upload ack matrix) and a
collapsible raw-frame log underneath.

The whole tab is gated: nothing but the two mode-change steps is enabled
until BOTH steps have completed, per the IDD's mandatory sequence (first
switch every LRU to the low-speed 115200 link, then switch QCC itself).

A Golden/Current segmented toggle scopes every image operation (Upload /
Authenticate / Program / Verify) to the golden or current-image flash
region - it drives the image_is_golden flag on the wire, and recolors
Upload/Authenticate/Verify amber-gold while Golden is selected so the
target region is always visually unmistakable (Program keeps its
destructive red in both modes).
"""

import csv
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

import apps.bootloader_packet as bl
from core.command_style import (
    FAILURE_COLOR as _FAIL_COLOR,
    FAILURE_RGB,
    PENDING_COLOR as _PENDING_COLOR,
    PENDING_RGB,
    SUCCESS_COLOR as _OK_COLOR,
    SUCCESS_RGB,
    IDLE_MATRIX_RGB,
    WRITE_COLOR, WRITE_HOVER_COLOR, WRITE_PRESSED_COLOR,
    indicator_style as _indicator_style_base,
    send_button_style,
)
from tabs.link_test_tab import LedMatrix
from core.packet import RP_LRU_SELECT_BROADCAST, RP_PAYLOAD_SIZE, num_lru
from apps.remote_prog_controller import (
    OP_AUTHENTICATE, OP_LINK_CHECK, OP_LRU_INFO, OP_MODE_BACK, OP_MODE_STEP1,
    OP_MODE_STEP2, OP_PROGRAM, OP_LRU_HIGH_SPEED, OP_UPLOAD, OP_VERIFY,
)
from widgets.segmented_control import SegmentedControl
from widgets.spin_field import SpinField

_ACCENT = "#00adb5"
_TEXT = "#eeeeee"
_LABEL_COLOR = "rgba(238, 238, 238, 0.62)"
_MUTED = "rgba(238, 238, 238, 0.45)"
_BORDER = "#4a515a"
_CARD_BG = "#393e46"

_SEND_BTN_STYLE = send_button_style(radius=12, font_size_px=14, padding="10px")
# Program writes flash - use Memory Operation's destructive-action red so it
# never reads as a routine send. Deliberately NOT swapped to golden when the
# Golden toggle is on (per Yuvraj's list: Upload/Authenticate/Verify recolor;
# the destructive-red cue on Program is kept in both modes).
_PROGRAM_BTN_STYLE = send_button_style(
    color=WRITE_COLOR, hover=WRITE_HOVER_COLOR, pressed=WRITE_PRESSED_COLOR,
    radius=12, font_size_px=14, padding="10px",
)
# Golden-image mode restyle for the image-scoped buttons (Upload /
# Authenticate / Verify) - an unmistakable amber/gold so the operator always
# sees which flash region the click targets. Same full selector-block
# pattern as send_button_style (hover/pressed/disabled all restated).
_GOLDEN_COLOR = "#d4a017"
_GOLDEN_HOVER = "#c0910f"
_GOLDEN_PRESSED = "#a87d0e"
_GOLDEN_BTN_STYLE = send_button_style(
    color=_GOLDEN_COLOR, hover=_GOLDEN_HOVER, pressed=_GOLDEN_PRESSED,
    radius=12, font_size_px=14, padding="10px", text_color="#1f2328",
)

_WARNING_COLOR = "#e2a33d"

_IDLE_QCOLOR = QColor(*IDLE_MATRIX_RGB)
_PENDING_QCOLOR = QColor(*PENDING_RGB)
_OK_QCOLOR = QColor(*SUCCESS_RGB)
_FAIL_QCOLOR = QColor(*FAILURE_RGB)

_PROGRESS_STYLE = (
    f"QProgressBar {{ background-color: #333a42; border: 1px solid {_BORDER};"
    "border-radius: 8px; text-align: center; color: #eeeeee;"
    "font-size: 11px; font-weight: 600; min-height: 18px; }"
    f"QProgressBar::chunk {{ background-color: {_ACCENT}; border-radius: 7px; }}"
)

_HEXDUMP_STYLE = (
    f"QPlainTextEdit {{ background-color: #2b2f35; color: {_TEXT};"
    f"border: 1px solid {_BORDER}; border-radius: 8px; padding: 6px; }}"
)

# LRU Info "Show Field" filter row - which decoded attribute (label, attr
# name on the parsed LruStatusResponse) gets shown directly on the LRU
# grid cells, mirroring status_tab.py's field-filter buttons.
_LRU_FIELDS = [
    ("MFG ID", "mfg_id"),
    ("Part No", "part_no"),
    ("Serial", "serial_num"),
    ("FW Version", "fw_version"),
]

_FILTER_BTN_STYLE_OFF = (
    f"QPushButton {{ background-color: {_CARD_BG}; color: rgba(238, 238, 238, 0.7);"
    f"border: 1px solid {_BORDER}; border-radius: 10px; padding: 6px 12px; font-weight: 600; }}"
    "QPushButton:hover { background-color: #454d57; }"
)
_FILTER_BTN_STYLE_ON = (
    f"QPushButton {{ background-color: {_ACCENT}; color: #1f2328;"
    f"border: 1px solid {_ACCENT}; border-radius: 10px; padding: 6px 12px; font-weight: 600; }}"
    "QPushButton:hover { background-color: #1fc2ca; }"
)


def _indicator_style(bg_color: str = None) -> str:
    return _indicator_style_base(bg_color, radius=12, border_color=_BORDER)


def _vertical_divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.VLine)
    line.setStyleSheet(f"background-color: {_BORDER}; max-width: 1px; border: none;")
    return line


def _section_box(title: str) -> tuple:
    """Same banner-heading column card as timing_tab.py's _section_box."""
    box = QFrame()
    box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    box.setMinimumWidth(300)

    outer = QVBoxLayout(box)
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
    label.setStyleSheet(f"color: {_LABEL_COLOR}; font-size: 15px; background: transparent;")
    grid.addWidget(label, row, 0, Qt.AlignLeft | Qt.AlignVCenter)
    grid.addWidget(field, row, 1, Qt.AlignRight | Qt.AlignVCenter)


def _status_pill(text: str = "Not sent yet") -> QLabel:
    pill = QLabel(text)
    pill.setAlignment(Qt.AlignCenter)
    pill.setFixedHeight(28)
    pill.setStyleSheet(_indicator_style())
    return pill


def _muted_note(text: str) -> QLabel:
    note = QLabel(text)
    note.setWordWrap(True)
    note.setStyleSheet(f"color: {_MUTED}; font-size: 11px; font-style: italic; background: transparent;")
    return note


def hex_dump(raw: bytes, bytes_per_row: int = 16) -> str:
    """Offset-prefixed hex lines, matching the header-panel hex aesthetic."""
    lines = []
    for off in range(0, len(raw), bytes_per_row):
        chunk = raw[off: off + bytes_per_row]
        lines.append(f"{off:04X}  " + " ".join(f"{b:02X}" for b in chunk))
    return "\n".join(lines)


class RemoteProgrammingTab(QWidget):
    # A 0-based index = single LRU, RP_LRU_SELECT_BROADCAST (0xFF) = all of them
    target_lru_changed = Signal(int)
    mode_step1_requested = Signal()
    mode_step2_requested = Signal()
    link_check_requested = Signal()
    lru_info_requested = Signal()
    lru_high_speed_requested = Signal()  # bootloader 0x32 broadcast -> LRUs to high speed
    mode_back_requested = Signal()        # QCC -> High Speed (SubCommand 0x02)
    authenticate_requested = Signal(bool)  # image_is_golden
    verify_requested = Signal(bool)        # image_is_golden
    upload_requested = Signal(bytes, bool)  # (.spi image bytes, image_is_golden)
    program_requested = Signal(bool)       # image_is_golden (IAP from uploaded image)
    cancel_requested = Signal()
    iap_timeout_changed = Signal(int)     # seconds (Authenticate/Verify/Program window)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image = b""                  # parsed firmware image bytes
        self._raw_file = b""               # file content as loaded from disk
        self._file_name = ""
        self._gate_open = False
        self._step1_done = False
        self._session_active = False
        self._programmed = False           # Program was sent; locks Step 1/2,
                                             # Link Check and LRU -> High Speed
                                             # until QCC -> High Speed is sent

        self._active_iap_op = None         # which of Authenticate/Verify/Program
                                             # is running, so its own button can
                                             # flip to "Stop" instead of disabling
        self._lru_acked = [0] * num_lru()  # successful-ack count per LRU
        self._lru_failed = [False] * num_lru()
        self._lru_has_data = False         # gates the Export CSV button
        self._lru_results = [None] * num_lru()  # per-LRU LruStatusResponse, for the filter/grid
        self._controller = None            # read-only gap queries, set by main_window

        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        columns = QHBoxLayout()
        columns.setSpacing(0)
        columns.addWidget(self._build_link_section(), 1)
        columns.addWidget(_vertical_divider())
        columns.addWidget(self._build_firmware_section(), 1)
        columns.addWidget(_vertical_divider())
        columns.addWidget(self._build_operations_section(), 1)
        root.addLayout(columns)

        root.addWidget(self._build_results_area(), 1)
        root.addWidget(self._build_frame_log())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self._apply_gate()

    def set_controller(self, controller):
        """Read-only handle for gap queries (lru_gaps/missing_chunk_indices)."""
        self._controller = controller

    # -- section builders -----------------------------------------------------

    def _build_link_section(self):
        box, form = _section_box("Link Setup")

        form.addWidget(_muted_note(
            "Mandatory order: send low-speed mode to the target LRU(s) "
            "first, then switch QCC itself. Operations unlock once both "
            "complete."
        ))

        # Target selector: which LRU(s) the whole low-speed session
        # addresses. Sent as byte 35 (LRU_SELECT) of the Mode Step 2 /
        # QCC -> High Speed frames and latched by QCC, so it locks while
        # the gate is open - change it only between sessions.
        target_grid = QGridLayout()
        target_grid.setHorizontalSpacing(18)
        target_grid.setColumnStretch(0, 1)
        self.target_combo = QComboBox()
        self.target_combo.addItem(f"All {num_lru()} LRUs (broadcast)")
        for q in range(num_lru()):
            self.target_combo.addItem(f"LRU {q} only")
        self.target_combo.currentIndexChanged.connect(self._on_target_changed)
        _field_row(target_grid, 0, "Target", self.target_combo)
        form.addLayout(target_grid)

        self.target_lock_note = QLabel(
            "Target locked — Step 1 already sent it to the LRU(s). Send "
            "\"LRU → High Speed\" then \"QCC → High Speed\" below to "
            "unlock it before changing the target."
        )
        self.target_lock_note.setWordWrap(True)
        self.target_lock_note.setStyleSheet(
            f"color: {_WARNING_COLOR}; font-size: 11px; font-weight: 600; background: transparent;"
        )
        self.target_lock_note.setVisible(False)
        form.addWidget(self.target_lock_note)

        self.step1_btn = QPushButton("1.  LRUs → Low-Speed (115200)")
        self.step1_btn.setFixedHeight(38)
        self.step1_btn.setStyleSheet(_SEND_BTN_STYLE)
        self.step1_btn.clicked.connect(self.mode_step1_requested.emit)
        form.addWidget(self.step1_btn)
        self.step1_status = _status_pill()
        form.addWidget(self.step1_status)

        self.step2_btn = QPushButton("2.  QCC → Low-Speed (115200)")
        self.step2_btn.setFixedHeight(38)
        self.step2_btn.setStyleSheet(_SEND_BTN_STYLE)
        self.step2_btn.clicked.connect(self.mode_step2_requested.emit)
        form.addWidget(self.step2_btn)
        self.step2_status = _status_pill()
        form.addWidget(self.step2_status)

        form.addWidget(_muted_note(
            "Byte 34 (SubCommand) selects the action: 0x00 = Broadcast, "
            "0x01 = QCC → Low-Speed, 0x02 = QCC → High-Speed. Byte 35 "
            "(LRU_SELECT, in the 0x01/0x02 frames) picks the target: "
            f"0–{num_lru() - 1} = one LRU, 0xFF = all of them — QCC latches it "
            "for the whole "
            "session."
        ))

        self.link_check_btn = QPushButton(f"3.  Check Link (all {num_lru()} LRUs)")
        self.link_check_btn.setFixedHeight(38)
        self.link_check_btn.setStyleSheet(_SEND_BTN_STYLE)
        self.link_check_btn.clicked.connect(self.link_check_requested.emit)
        form.addWidget(self.link_check_btn)
        self.link_status = _status_pill()
        form.addWidget(self.link_status)

        form.addStretch(1)

        self.gate_label = QLabel("Complete both steps to unlock operations")
        self.gate_label.setAlignment(Qt.AlignCenter)
        self.gate_label.setWordWrap(True)
        self.gate_label.setStyleSheet(f"color: {_MUTED}; font-size: 12px; background: transparent;")
        form.addWidget(self.gate_label)

        form.addWidget(_muted_note(
            "Return to Normal — LRUs auto-return to high speed on their own "
            "after Programming completes, but this can force it explicitly. "
            "QCC always requires the manual step below."
        ))

        self.lru_high_speed_btn = QPushButton("LRU → High Speed")
        self.lru_high_speed_btn.setFixedHeight(34)
        self.lru_high_speed_btn.setStyleSheet(_SEND_BTN_STYLE)
        self.lru_high_speed_btn.setToolTip(
            "Broadcasts the bootloader's Mode Change MSS->Fabric command "
            "(0x32) to all 96 LRUs (SubCommand 0x00 broadcast). LRUs "
            "already do this automatically after Programming — use this to "
            "force it (e.g. after an aborted session). Doesn't touch the "
            "gate; QCC itself stays on the low-speed link until QCC → High "
            "Speed below is sent."
        )
        self.lru_high_speed_btn.clicked.connect(self.lru_high_speed_requested.emit)
        form.addWidget(self.lru_high_speed_btn)
        self.lru_high_speed_status = _status_pill()
        form.addWidget(self.lru_high_speed_status)

        self.mode_back_btn = QPushButton("QCC → High Speed")
        self.mode_back_btn.setFixedHeight(34)
        self.mode_back_btn.setStyleSheet(_SEND_BTN_STYLE)
        self.mode_back_btn.setToolTip(
            "QCC's own self-directed UART switch back to high speed (mirrors "
            "Mode Step 2, SubCommand 0x02). The gate re-locks; redo steps "
            "1–2 to come back."
        )
        self.mode_back_btn.clicked.connect(self.mode_back_requested.emit)
        form.addWidget(self.mode_back_btn)
        self.mode_back_status = _status_pill()
        form.addWidget(self.mode_back_status)

        return box

    def _build_firmware_section(self):
        box, form = _section_box("Firmware Image")

        # Golden/Current scope toggle - drives image_is_golden on every
        # image operation and the amber restyle of Upload/Auth/Verify.
        self.image_toggle = SegmentedControl("Current Image", "Golden Image")
        self.image_toggle.toggled.connect(self._on_image_toggle)
        form.addWidget(self.image_toggle)

        self.choose_file_btn = QPushButton("Choose Current .spi File…")
        self.choose_file_btn.setFixedHeight(34)
        self.choose_file_btn.setStyleSheet(_SEND_BTN_STYLE)
        self.choose_file_btn.clicked.connect(self._on_choose_file)
        form.addWidget(self.choose_file_btn)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(0, 1)

        self.file_label = QLabel("No file selected")
        self.file_label.setStyleSheet(f"color: {_TEXT}; font-size: 13px; background: transparent;")
        self.file_label.setWordWrap(True)
        grid.addWidget(self.file_label, 0, 0, 1, 2)

        self.size_label = QLabel("—")
        _field_row(grid, 1, "Total size", self.size_label)
        self.size_label.setStyleSheet(f"color: {_TEXT}; font-size: 13px; background: transparent;")
        self.chunk_count_label = QLabel("—")
        _field_row(grid, 2, "4K chunks", self.chunk_count_label)
        self.chunk_count_label.setStyleSheet(f"color: {_TEXT}; font-size: 13px; background: transparent;")

        form.addLayout(grid)

        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet(_PROGRESS_STYLE)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("idle")
        form.addWidget(self.progress_bar)

        self.chunk_label = QLabel("")
        self.chunk_label.setAlignment(Qt.AlignCenter)
        self.chunk_label.setStyleSheet(f"color: {_MUTED}; font-size: 11px; background: transparent;")
        form.addWidget(self.chunk_label)

        form.addStretch(1)

        self.upload_btn = QPushButton("Upload Current Image")
        self.upload_btn.setFixedHeight(38)
        self.upload_btn.setStyleSheet(_SEND_BTN_STYLE)
        self.upload_btn.clicked.connect(self._on_upload_clicked)
        form.addWidget(self.upload_btn)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFixedHeight(32)
        self.cancel_btn.setStyleSheet(_SEND_BTN_STYLE)
        self.cancel_btn.clicked.connect(self.cancel_requested.emit)
        form.addWidget(self.cancel_btn)

        self.upload_status = _status_pill()
        form.addWidget(self.upload_status)

        return box

    def _build_operations_section(self):
        box, form = _section_box("Operations")

        lru_row = QHBoxLayout()
        self.lru_btn = QPushButton("Get LRU Info")
        self.lru_btn.setFixedHeight(38)
        self.lru_btn.setStyleSheet(_SEND_BTN_STYLE)
        self.lru_btn.clicked.connect(self.lru_info_requested.emit)
        lru_row.addWidget(self.lru_btn, 1)
        self.export_lru_btn = QPushButton("Export CSV")
        self.export_lru_btn.setFixedHeight(38)
        self.export_lru_btn.setStyleSheet(_SEND_BTN_STYLE)
        self.export_lru_btn.setToolTip("Save the LRU Info table below to a CSV file")
        self.export_lru_btn.clicked.connect(self._on_export_lru_clicked)
        lru_row.addWidget(self.export_lru_btn)
        form.addLayout(lru_row)

        self.auth_btn = QPushButton("Authenticate Current Image")
        self.auth_btn.setFixedHeight(38)
        self.auth_btn.setStyleSheet(_SEND_BTN_STYLE)
        self.auth_btn.clicked.connect(self._on_authenticate_clicked)
        form.addWidget(self.auth_btn)

        self.program_btn = QPushButton("Program Current Image")
        self.program_btn.setFixedHeight(38)
        self.program_btn.setStyleSheet(_PROGRAM_BTN_STYLE)
        self.program_btn.setToolTip(
            "One-shot IAP PROGRAM (0x36): each SmartFusion2 flashes itself "
            "from the SPI image already uploaded. Devices reprogram and may "
            "not reply — silence is normal."
        )
        self.program_btn.clicked.connect(self._on_program_clicked)
        form.addWidget(self.program_btn)

        self.verify_btn = QPushButton("Verify Current Image")
        self.verify_btn.setFixedHeight(38)
        self.verify_btn.setStyleSheet(_SEND_BTN_STYLE)
        self.verify_btn.clicked.connect(self._on_verify_clicked)
        form.addWidget(self.verify_btn)

        form.addWidget(_muted_note(
            "Sequence: Authenticate checks the staged image (signature/header/"
            "chaining) before anything is committed — non-destructive. Program "
            "commits it to eNVM/fabric — destructive. Verify reads back and "
            "confirms the now-programmed image matches what was intended."
        ))

        timeout_grid = QGridLayout()
        timeout_grid.setHorizontalSpacing(18)
        timeout_grid.setColumnStretch(0, 1)
        self.iap_timeout_spin = SpinField(1, 600, 45, field_width=90)
        _field_row(timeout_grid, 0, "Op timeout (s)", self.iap_timeout_spin)
        self.iap_timeout_spin.spin.valueChanged.connect(self.iap_timeout_changed.emit)
        form.addLayout(timeout_grid)

        form.addWidget(_muted_note(
            "Authenticate / Verify / Program poll for the timeout above — "
            "replies arrive per-LRU and light up the grid below as they "
            "land. Program flashes from the already-uploaded image and "
            "expects no replies."
        ))

        form.addStretch(1)

        self.op_status = _status_pill()
        form.addWidget(self.op_status)

        self.op_response_time_label = QLabel("")
        self.op_response_time_label.setAlignment(Qt.AlignCenter)
        self.op_response_time_label.setStyleSheet(
            f"color: {_MUTED}; font-size: 11px; background: transparent;")
        form.addWidget(self.op_response_time_label)

        return box

    # -- results area ----------------------------------------------------------

    def _make_table(self, headers) -> QTableWidget:
        table = QTableWidget(num_lru(), len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionMode(QTableWidget.NoSelection)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setMinimumHeight(320)
        for q in range(num_lru()):
            item = QTableWidgetItem(f"LRU-{q}")
            item.setTextAlignment(Qt.AlignCenter)
            table.setItem(q, 0, item)
            for c in range(1, len(headers)):
                blank = QTableWidgetItem("—")
                blank.setTextAlignment(Qt.AlignCenter)
                table.setItem(q, c, blank)
        return table

    def _build_results_area(self):
        self.results_stack = QStackedWidget()

        # Page 0: placeholder before any operation
        placeholder = QLabel("Run an operation to see per-LRU results here")
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setStyleSheet(f"color: {_MUTED}; font-size: 13px; background: transparent;")
        self.results_stack.addWidget(placeholder)

        # Page 1: LRU Info - "Show Field" filter row, then LedMatrix (grid
        # cells can display any one filterable field directly, like
        # status_tab.py's Detailed Health view) + detail table side by side.
        lru_page = QWidget()
        lru_col = QVBoxLayout(lru_page)
        lru_col.setContentsMargins(0, 0, 0, 0)
        lru_col.setSpacing(8)

        lru_filter_row = QHBoxLayout()
        lru_filter_row.addWidget(QLabel("Show Field:"))
        self.lru_filter_group = QButtonGroup(self)
        # Not exclusive=True - same reasoning as status_tab.py's filter
        # group: "toggle one at a time" needs a genuine none-checked state
        # too (reverts every cell back to plain "LRU-N"), which Qt's
        # built-in exclusive mode doesn't allow once anything is checked.
        self.lru_filter_group.setExclusive(False)
        self.lru_filter_group.buttonToggled.connect(self._on_lru_filter_toggled)
        for label, attr in _LRU_FIELDS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setProperty("field_key", attr)
            btn.setStyleSheet(_FILTER_BTN_STYLE_OFF)
            self.lru_filter_group.addButton(btn)
            lru_filter_row.addWidget(btn)
        lru_filter_row.addStretch(1)
        lru_col.addLayout(lru_filter_row)

        lru_row = QHBoxLayout()
        lru_row.setSpacing(12)
        self.lru_matrix = LedMatrix(clickable=False)
        lru_row.addWidget(self.lru_matrix, 1)
        self.lru_table = self._make_table(["LRU", "MFG_ID", "Part No", "Serial", "FW Version"])
        lru_row.addWidget(self.lru_table, 1)
        lru_col.addLayout(lru_row, 1)

        self.results_stack.addWidget(lru_page)

        # Page 2: Authenticate/Verify - LedMatrix + detail table side by side
        iap_page = QWidget()
        iap_row = QHBoxLayout(iap_page)
        iap_row.setContentsMargins(0, 0, 0, 0)
        iap_row.setSpacing(12)
        self.iap_matrix = LedMatrix(clickable=False)
        iap_row.addWidget(self.iap_matrix, 1)
        self.iap_table = self._make_table(["LRU", "State", "IAP Status"])
        iap_row.addWidget(self.iap_table, 1)
        self.results_stack.addWidget(iap_page)

        # Page 3: Upload - LedMatrix + gaps table
        prog_page = QWidget()
        prog_row = QHBoxLayout(prog_page)
        prog_row.setContentsMargins(0, 0, 0, 0)
        prog_row.setSpacing(12)
        self.prog_matrix = LedMatrix(clickable=False)
        prog_row.addWidget(self.prog_matrix, 1)
        self.gaps_table = self._make_table(["LRU", "Acked", "Failed chunks", "Missing chunks"])
        prog_row.addWidget(self.gaps_table, 1)
        self.results_stack.addWidget(prog_page)

        # Page 4: Link Check - LedMatrix + per-LRU response bytes
        link_page = QWidget()
        link_row = QHBoxLayout(link_page)
        link_row.setContentsMargins(0, 0, 0, 0)
        link_row.setSpacing(12)
        self.link_matrix = LedMatrix(clickable=False)
        link_row.addWidget(self.link_matrix, 1)
        self.link_table = self._make_table(["LRU", "State", "Response"])
        link_row.addWidget(self.link_table, 1)
        self.results_stack.addWidget(link_page)

        return self.results_stack

    def _build_frame_log(self):
        # Local import keeps the tab importable standalone in tests even if
        # titled_group grows dependencies later.
        from widgets.titled_group import collapsible_group_box

        box, layout = collapsible_group_box("Raw Frame Log", start_expanded=False)

        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)

        row = QHBoxLayout()
        for title_text, attr in (("Last sent (TX)", "tx_hex_view"),
                                 ("Last received (RX)", "rx_hex_view")):
            col = QVBoxLayout()
            title = QLabel(title_text)
            title.setStyleSheet(f"color: {_LABEL_COLOR}; font-size: 12px; background: transparent;")
            col.addWidget(title)
            view = QPlainTextEdit()
            view.setReadOnly(True)
            view.setFont(mono)
            view.setStyleSheet(_HEXDUMP_STYLE)
            view.setMinimumHeight(180)
            view.setLineWrapMode(QPlainTextEdit.NoWrap)
            setattr(self, attr, view)
            col.addWidget(view)
            row.addLayout(col, 1)
        layout.addLayout(row)

        self.log_summary_label = QLabel("")
        self.log_summary_label.setStyleSheet(
            f"color: {_MUTED}; font-size: 11px; background: transparent;")
        self.log_summary_label.setWordWrap(True)
        layout.addWidget(self.log_summary_label)

        return box

    # -- Target LRU selector ----------------------------------------------------

    @property
    def target_lru(self) -> int:
        """0-95 for a single LRU, RP_LRU_SELECT_BROADCAST for all 96."""
        idx = self.target_combo.currentIndex()
        return RP_LRU_SELECT_BROADCAST if idx <= 0 else idx - 1

    def _target_scope_text(self) -> str:
        return ("all 96 LRUs" if self.target_lru == RP_LRU_SELECT_BROADCAST
                else f"LRU {self.target_lru} only")

    def _on_target_changed(self, _idx: int):
        # Retitle the target-scoped buttons so the addressed LRU(s) are
        # always visible right on the action itself.
        if self.target_lru == RP_LRU_SELECT_BROADCAST:
            self.step1_btn.setText("1.  LRUs → Low-Speed (115200)")
            self.link_check_btn.setText("3.  Check Link (all 96 LRUs)")
            self.lru_high_speed_btn.setText("LRU → High Speed")
        else:
            q = self.target_lru
            self.step1_btn.setText(f"1.  LRU {q} → Low-Speed (115200)")
            self.link_check_btn.setText(f"3.  Check Link (LRU {q})")
            self.lru_high_speed_btn.setText(f"LRU {q} → High Speed")
        self.target_lru_changed.emit(self.target_lru)

    # -- Golden/Current toggle ---------------------------------------------------

    @property
    def image_is_golden(self) -> bool:
        return self.image_toggle.isChecked()

    def _on_image_toggle(self, golden: bool):
        """Recolor the image-scoped buttons (Upload/Auth/Verify go amber-gold
        in Golden mode) and rename them so the target region is explicit.
        Program keeps its destructive red but is renamed too."""
        scope = "Golden" if golden else "Current"
        style = _GOLDEN_BTN_STYLE if golden else _SEND_BTN_STYLE
        self.choose_file_btn.setText(f"Choose {scope} .spi File…")
        self.upload_btn.setText(f"Upload {scope} Image")
        # Skip Authenticate/Verify/Program's own text+style while it's the
        # active op showing "Stop X" - toggling Golden/Current mid-run
        # shouldn't clobber that back to its normal label.
        if self._active_iap_op != OP_AUTHENTICATE:
            self.auth_btn.setText(f"Authenticate {scope} Image")
            self.auth_btn.setStyleSheet(style)
        if self._active_iap_op != OP_VERIFY:
            self.verify_btn.setText(f"Verify {scope} Image")
            self.verify_btn.setStyleSheet(style)
        if self._active_iap_op != OP_PROGRAM:
            self.program_btn.setText(f"Program {scope} Image")
        for btn in (self.choose_file_btn, self.upload_btn):
            btn.setStyleSheet(style)
        if golden:
            # The toggle's own selected segment goes gold too - runs after
            # SegmentedControl._select's default restyle, so this wins.
            self.image_toggle.right_btn.setStyleSheet(
                f"QPushButton {{ background-color: {_GOLDEN_COLOR}; color: #1f2328;"
                "border: none; border-radius: 10px; font-weight: 600; padding: 8px 12px; }"
            )

    # -- file handling -----------------------------------------------------------

    def _on_choose_file(self):
        scope = "Golden" if self.image_is_golden else "Current"
        path, _ = QFileDialog.getOpenFileName(
            self, f"Choose {scope} SPI Bitstream", "",
            "SPI Bitstream (*.spi)",
        )
        if not path:
            return
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError as e:
            QMessageBox.warning(self, "File error", f"Could not read file:\n{e}")
            return
        if not raw:
            QMessageBox.warning(self, "File error", "File is empty.")
            return

        # .spi is the raw SPI-flash programming image (Libero export) -
        # loaded verbatim, no container parsing.
        self._raw_file = raw
        self._file_name = path.split("/")[-1].split("\\")[-1]
        self._image = raw

        n_chunks = (len(self._image) + RP_PAYLOAD_SIZE - 1) // RP_PAYLOAD_SIZE
        self.file_label.setText(self._file_name)
        self.size_label.setText(f"{len(self._image):,} bytes")
        self.chunk_count_label.setText(str(n_chunks))
        self.progress_bar.setRange(0, max(n_chunks, 1))
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("ready")
        self._apply_gate()

    def _on_upload_clicked(self):
        if not self._image:
            QMessageBox.warning(self, "No image", "Choose a .spi bitstream file first.")
            return
        golden = self.image_is_golden
        scope = "GOLDEN" if golden else "CURRENT"
        n_chunks = (len(self._image) + RP_PAYLOAD_SIZE - 1) // RP_PAYLOAD_SIZE
        confirm = QMessageBox.question(
            self, "Upload bitstream",
            f"Send {self._file_name} ({len(self._image):,} bytes, "
            f"{n_chunks} chunks) to {self._target_scope_text()}?\n\n"
            f"Target: {scope} image region (SPI flash write on each "
            "addressed LRU).",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm == QMessageBox.Yes:
            self.upload_requested.emit(self._image, golden)

    def _on_authenticate_clicked(self):
        if self._active_iap_op == OP_AUTHENTICATE:
            self.cancel_requested.emit()
            return
        self.authenticate_requested.emit(self.image_is_golden)

    def _on_verify_clicked(self):
        if self._active_iap_op == OP_VERIFY:
            self.cancel_requested.emit()
            return
        self.verify_requested.emit(self.image_is_golden)

    def _on_program_clicked(self):
        if self._active_iap_op == OP_PROGRAM:
            self.cancel_requested.emit()
            return
        golden = self.image_is_golden
        scope = "GOLDEN" if golden else "CURRENT"
        # Deliberately Warning (not Question) - this commits firmware to
        # flash, the same destructive class of action as memory_tab.py's NVM
        # write, so it gets the warning icon rather than a neutral prompt.
        # Built as an instance rather than the static QMessageBox.warning()
        # helper so the informative label can be reached and flashed.
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Program firmware")
        box.setText(
            f"Command {self._target_scope_text()} to reprogram from the "
            f"already-uploaded {scope} SPI image?\n\n"
            "Each addressed SmartFusion2 flashes itself and may reboot — "
            "no replies are expected while devices reprogram."
        )
        box.setInformativeText(
            "⚠  WARNING: Authenticate first, then Program.\n"
            "Authenticate is the non-destructive check that the staged image "
            "is valid — Program commits it to flash whether or not that "
            "check has been run."
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)

        # Pulse the warning so it can't be skimmed past. Deliberately a slow
        # ~1 Hz toggle, not a rapid strobe - fast flashing (>3 Hz) is a
        # photosensitivity hazard, and this still reads as "emergency".
        warning_label = box.findChild(QLabel, "qt_msgbox_informativelabel")
        if warning_label is not None:
            flash_timer = QTimer(box)
            flash_state = {"on": True}

            def _flash():
                flash_state["on"] = not flash_state["on"]
                warning_label.setStyleSheet(
                    f"color: {_FAIL_COLOR}; font-weight: 700;" if flash_state["on"]
                    else "color: rgba(238, 238, 238, 0.45); font-weight: 700;"
                )

            _flash()
            flash_timer.timeout.connect(_flash)
            flash_timer.start(500)

        confirm = box.exec()
        if confirm == QMessageBox.Yes:
            self.program_requested.emit(golden)

    # -- gate / enable state -------------------------------------------------------

    def _apply_gate(self):
        ops_ok = self._gate_open and not self._session_active
        # Once Program has been sent, lock Link Check / LRU -> High Speed
        # (and Step 1/2 below) until QCC -> High Speed is sent - Program
        # keeps the two devices' modes diverging (LRUs auto-return to high
        # speed, QCC doesn't) so nothing else on this link should run until
        # the operator explicitly resyncs them.
        for btn in (self.link_check_btn, self.lru_btn, self.lru_high_speed_btn):
            btn.setEnabled(ops_ok and not self._programmed)
        self.mode_back_btn.setEnabled(ops_ok)
        # Authenticate/Verify/Program: the button belonging to whichever one
        # is currently running stays enabled (as "Stop X") instead of
        # disabling along with the rest of the gated ops, so a click can
        # cancel it - see _set_iap_button_stop/_restore_iap_button.
        for op, btn in ((OP_AUTHENTICATE, self.auth_btn),
                        (OP_VERIFY, self.verify_btn),
                        (OP_PROGRAM, self.program_btn)):
            btn.setEnabled((ops_ok and not self._programmed) or self._active_iap_op == op)
        self.upload_btn.setEnabled(ops_ok and not self._programmed and bool(self._image))
        # Export needs actual LRU data, not the gate - the table stays
        # valid after Return to High Speed re-locks operations.
        self.export_lru_btn.setEnabled(self._lru_has_data and not self._session_active)
        self.cancel_btn.setEnabled(self._session_active)
        self.step1_btn.setEnabled(not self._session_active and not self._programmed)
        self.step2_btn.setEnabled(not self._session_active and not self._programmed)
        # Step 1 already sends LRU_SELECT to the addressed LRU(s), and
        # Step 2 latches the same value into QCC - so the target locks as
        # soon as Step 1 succeeds, not just once the full gate (both steps)
        # is open. Changing it after Step 1 but before Step 2 would send
        # Step 2 with a target Step 1 never used.
        self.target_combo.setEnabled(not self._session_active and not self._step1_done)
        self.target_lock_note.setVisible(self._step1_done)
        self.gate_label.setText(
            "Link ready — operations unlocked" if self._gate_open
            else "Complete both steps to unlock operations"
        )
        self.gate_label.setStyleSheet(
            f"color: {_ACCENT if self._gate_open else _MUTED}; font-size: 12px;"
            "background: transparent;"
        )

    def on_gate_changed(self, open_: bool):
        self._gate_open = open_
        if not open_:
            self._step1_done = False
            self._programmed = False
            self.step1_status.setText("Not sent yet")
            self.step1_status.setStyleSheet(_indicator_style())
            self.step2_status.setText("Not sent yet")
            self.step2_status.setStyleSheet(_indicator_style())
        self._apply_gate()

    # -- session lifecycle (driven by main_window / controller) ---------------------

    def _pill_for_op(self, op: str):
        return {
            OP_MODE_STEP1: self.step1_status,
            OP_MODE_STEP2: self.step2_status,
            OP_LINK_CHECK: self.link_status,
            OP_LRU_HIGH_SPEED: self.lru_high_speed_status,
            OP_MODE_BACK: self.mode_back_status,
            OP_LRU_INFO: self.op_status,
            OP_AUTHENTICATE: self.op_status,
            OP_VERIFY: self.op_status,
            OP_PROGRAM: self.op_status,
            OP_UPLOAD: self.upload_status,
        }.get(op)

    # -- Authenticate/Verify/Program "Stop" button toggle -----------------------------

    def _iap_btn_for_op(self, op: str):
        return {OP_AUTHENTICATE: self.auth_btn, OP_VERIFY: self.verify_btn,
                OP_PROGRAM: self.program_btn}.get(op)

    def _set_iap_button_stop(self, op: str):
        btn = self._iap_btn_for_op(op)
        if btn is None:
            return
        label = {OP_AUTHENTICATE: "Stop Authenticate", OP_VERIFY: "Stop Verify",
                 OP_PROGRAM: "Stop Program"}[op]
        btn.setText(label)
        btn.setStyleSheet(_PROGRAM_BTN_STYLE)

    def _restore_iap_button(self, op: str):
        btn = self._iap_btn_for_op(op)
        if btn is None:
            return
        scope = "Golden" if self.image_is_golden else "Current"
        label = {OP_AUTHENTICATE: f"Authenticate {scope} Image",
                 OP_VERIFY: f"Verify {scope} Image",
                 OP_PROGRAM: f"Program {scope} Image"}[op]
        btn.setText(label)
        btn.setStyleSheet(_PROGRAM_BTN_STYLE if op == OP_PROGRAM
                          else (_GOLDEN_BTN_STYLE if self.image_is_golden else _SEND_BTN_STYLE))

    def mark_session_started(self, op: str):
        self._session_active = True
        pill = self._pill_for_op(op)
        if pill is not None:
            pill.setText("Sending...")
            pill.setStyleSheet(_indicator_style(_PENDING_COLOR))
        self.op_response_time_label.setText("")

        if op == OP_LRU_INFO:
            self.results_stack.setCurrentIndex(1)
            self._reset_lru_table()
        elif op in (OP_AUTHENTICATE, OP_VERIFY, OP_PROGRAM):
            self.results_stack.setCurrentIndex(2)
            self._reset_iap_grid()
            self._active_iap_op = op
            self._set_iap_button_stop(op)
            if op == OP_PROGRAM:
                self._programmed = True
        elif op == OP_UPLOAD:
            self.results_stack.setCurrentIndex(3)
            self._reset_program_view()
        elif op == OP_LINK_CHECK:
            self.results_stack.setCurrentIndex(4)
            self._reset_link_grid()
        self._apply_gate()

    def on_session_finished(self, op: str, ok: bool, text: str):
        self._session_active = False
        if op == OP_MODE_STEP1 and ok:
            self._step1_done = True
        if op == OP_MODE_BACK and ok:
            self._programmed = False
        if op in (OP_AUTHENTICATE, OP_VERIFY, OP_PROGRAM):
            self._active_iap_op = None
            self._restore_iap_button(op)
        pill = self._pill_for_op(op)
        if pill is not None:
            pill.setText(text)
            pill.setStyleSheet(_indicator_style(_OK_COLOR if ok else _FAIL_COLOR))
        self._apply_gate()

    def on_step_result(self, op: str, ok: bool, text: str):
        # Interim per-step feedback (e.g. a window closing with no replies) -
        # the final pill state still comes from on_session_finished.
        pill = self._pill_for_op(op)
        if pill is None:
            return
        if op == OP_UPLOAD:
            pill.setText(text)
            pill.setStyleSheet(_indicator_style(_PENDING_COLOR if ok else _FAIL_COLOR))
        else:
            pill.setText(text)
            pill.setStyleSheet(_indicator_style(_OK_COLOR if ok else _FAIL_COLOR))

    def show_response_time(self, microseconds: float):
        self.op_response_time_label.setText(f"{microseconds:.0f} µs")

    # -- LRU Info ---------------------------------------------------------------------

    def _reset_lru_table(self):
        self._lru_has_data = False
        self._lru_results = [None] * num_lru()
        self.lru_matrix.set_all(_PENDING_QCOLOR)
        for q in range(num_lru()):
            self.lru_matrix._leds[q].setText(f"LRU-{q}")
            for c in range(1, 5):
                self.lru_table.item(q, c).setText("—")

    def on_lru_row(self, q: int, resp):
        self._lru_has_data = True
        self._lru_results[q] = resp
        self.lru_table.item(q, 1).setText(str(resp.mfg_id))
        self.lru_table.item(q, 2).setText(str(resp.part_no))
        self.lru_table.item(q, 3).setText(str(resp.serial_num))
        self.lru_table.item(q, 4).setText(str(resp.fw_version))
        self.lru_matrix.set_one(q, _OK_QCOLOR)
        self._set_lru_cell_text(q, resp)

    # -- LRU Info "Show Field" filter (grid cells display one field directly) ---------

    def _current_lru_field(self):
        checked = self.lru_filter_group.checkedButton()
        return checked.property("field_key") if checked is not None else None

    def _set_lru_cell_text(self, lru_index: int, resp, field=None):
        if field is None:
            field = self._current_lru_field()
        led = self.lru_matrix._leds[lru_index]
        if field is not None and resp is not None:
            led.setText(f"LRU-{lru_index}: {getattr(resp, field)}")
        else:
            led.setText(f"LRU-{lru_index}")

    def _on_lru_filter_toggled(self, button, checked: bool):
        if checked:
            # Manual exclusivity, same pattern as status_tab.py: selecting
            # one deselects every other one, but the active one can still be
            # clicked again to deselect (reverts to plain "LRU-N" labels).
            for other in self.lru_filter_group.buttons():
                if other is not button and other.isChecked():
                    other.blockSignals(True)
                    other.setChecked(False)
                    other.blockSignals(False)
        for btn in self.lru_filter_group.buttons():
            btn.setStyleSheet(_FILTER_BTN_STYLE_ON if btn.isChecked() else _FILTER_BTN_STYLE_OFF)
        field = self._current_lru_field()
        for q, resp in enumerate(self._lru_results):
            self._set_lru_cell_text(q, resp, field)

    def _on_export_lru_clicked(self):
        """Dump the LRU Info table exactly as displayed ('—' rows included,
        so a gap in the export is visible rather than silently dropped)."""
        default_name = f"lru_info_{datetime.now():%Y%m%d_%H%M%S}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export LRU Info", default_name, "CSV Files (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        headers = [self.lru_table.horizontalHeaderItem(c).text()
                   for c in range(self.lru_table.columnCount())]
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                for q in range(num_lru()):
                    writer.writerow([self.lru_table.item(q, c).text()
                                     for c in range(self.lru_table.columnCount())])
        except OSError as e:
            QMessageBox.warning(self, "Export failed", f"Could not write '{path}':\n{e}")
            return
        self.op_response_time_label.setText(
            f"Exported to {path.split('/')[-1].split(chr(92))[-1]}")

    # -- Link Check live grid --------------------------------------------------------

    def _reset_link_grid(self):
        self.link_matrix.set_all(_PENDING_QCOLOR)
        for q in range(num_lru()):
            self.link_table.item(q, 1).setText("Pending")
            self.link_table.item(q, 2).setText("—")

    # -- Authenticate / Verify / Program live grid -----------------------------------

    def _reset_iap_grid(self):
        self.iap_matrix.set_all(_PENDING_QCOLOR)
        for q in range(num_lru()):
            self.iap_table.item(q, 1).setText("Pending")
            self.iap_table.item(q, 2).setText("—")

    def on_op_row(self, op: str, q: int, parsed):
        if op == OP_LINK_CHECK:
            if isinstance(parsed, bl.MssLinkResponse) and not parsed.checksum_ok:
                # A slot whose bytes happen to start with BL_HEADER (0xAA)
                # and carry CT_LINK/CT_LINK_RESPONSE in byte 2 is not proof
                # a real LRU answered - bl.parse_slot() only returns None
                # for a fully all-zero slice, so stale/garbage bytes left
                # in QCC's DMA buffer for a non-responding LRU can still
                # coincidentally parse as an MssLinkResponse. checksum_ok
                # (computed the same way as every other checksum check in
                # this app - header_panel/frame_logger/timing_tab all gate
                # on it) is what actually distinguishes a real reply from
                # that - this branch used to skip it entirely and mark
                # such slots "Linked" with whatever garbage b1_b4 came
                # along for the ride.
                self.link_matrix.set_one(q, _FAIL_QCOLOR)
                self.link_table.item(q, 1).setText("Bad Checksum")
                self.link_table.item(q, 2).setText(
                    " ".join(f"{b:02X}" for b in parsed.b1_b4))
            elif isinstance(parsed, bl.MssLinkResponse):
                self.link_matrix.set_one(q, _OK_QCOLOR)
                self.link_table.item(q, 1).setText("Linked")
                self.link_table.item(q, 2).setText(
                    " ".join(f"{b:02X}" for b in parsed.b1_b4))
            else:
                self.link_matrix.set_one(q, _FAIL_QCOLOR)
                self.link_table.item(q, 1).setText("Unexpected")
                self.link_table.item(q, 2).setText(
                    f"cmd_type=0x{parsed.command_type:02X}")
            return
        if op not in (OP_AUTHENTICATE, OP_VERIFY, OP_PROGRAM):
            return
        if isinstance(parsed, bl.FwUpdateResponse):
            ok = parsed.iap_status == 0
            self.iap_matrix.set_one(q, _OK_QCOLOR if ok else _FAIL_QCOLOR)
            self.iap_table.item(q, 1).setText("Responded")
            self.iap_table.item(q, 2).setText(parsed.iap_status_name)
        elif isinstance(parsed, bl.ErrorMsg):
            self.iap_matrix.set_one(q, _FAIL_QCOLOR)
            self.iap_table.item(q, 1).setText("Error")
            self.iap_table.item(q, 2).setText(
                f"hdr={parsed.header_error} crc={parsed.crc_error} tmo={parsed.timeout_error}")
        elif op == OP_PROGRAM and isinstance(parsed, bl.MssLinkResponse):
            # Program-only (per Yuvraj 2026-07-22): iap_program() in the
            # firmware (user_functions.c) never transmits a 0x35
            # FwUpdateResponse - it busy-waits on the IAP result then resets
            # the device, unlike iap_authenticate()/iap_verify() which do
            # send one. What actually lands on the wire during a Program
            # poll is a 0x34 MSS Link Response (B1 B2 B3 B4), same shape
            # Link Check uses. Authenticate/Verify are untouched - they
            # still only handle FwUpdateResponse/ErrorMsg above. Without
            # this branch the row silently stayed "Pending" and was later
            # mislabeled "No Reply (normal)" by on_op_window_closed() even
            # though a real packet had arrived.
            self.iap_matrix.set_one(q, _OK_QCOLOR)
            self.iap_table.item(q, 1).setText("Responded")
            self.iap_table.item(q, 2).setText(
                "Link " + " ".join(f"{b:02X}" for b in parsed.b1_b4))

    def on_op_window_closed(self, op: str):
        if op == OP_LINK_CHECK:
            for q in range(num_lru()):
                if self.link_table.item(q, 1).text() == "Pending":
                    self.link_table.item(q, 1).setText("No Response")
                    self.link_matrix.set_one(q, _FAIL_QCOLOR)
            return
        if op == OP_LRU_INFO:
            # Single aggregated response frame, not staggered like
            # Authenticate/Verify - any LRU whose slot never populated
            # (row still None) gets no further reply, so mark it red now
            # rather than leaving it pending grey forever.
            for q, resp in enumerate(self._lru_results):
                if resp is None:
                    self.lru_matrix.set_one(q, _FAIL_QCOLOR)
            return
        if op not in (OP_AUTHENTICATE, OP_VERIFY, OP_PROGRAM):
            return
        no_reply_ok = op == OP_PROGRAM  # devices reprogram, silence is normal
        for q in range(num_lru()):
            if self.iap_table.item(q, 1).text() == "Pending":
                self.iap_table.item(q, 1).setText(
                    "No Reply (normal)" if no_reply_ok else "No Response")
                if not no_reply_ok:
                    self.iap_matrix.set_one(q, _FAIL_QCOLOR)

    # -- Program ---------------------------------------------------------------------------

    def _reset_program_view(self):
        self.prog_matrix.set_all(_PENDING_QCOLOR)
        self._lru_acked = [0] * num_lru()
        self._lru_failed = [False] * num_lru()
        for q in range(num_lru()):
            self.gaps_table.item(q, 1).setText("0")
            self.gaps_table.item(q, 2).setText("—")
            self.gaps_table.item(q, 3).setText("—")
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("uploading… %v / %m")
        self.chunk_label.setText("")

    def on_chunk_progress(self, idx: int, count: int):
        self.progress_bar.setRange(0, count)
        self.progress_bar.setValue(idx + 1)
        self.chunk_label.setText(f"chunk {idx + 1} / {count}")

    def on_ack_recorded(self, q: int, idx: int, ok: bool):
        if ok:
            self._lru_acked[q] += 1
        else:
            self._lru_failed[q] = True
        # Live coloring: red is sticky on any reported failure. Otherwise
        # green the moment THIS LRU successfully acks anything - streaming
        # advances the instant ANY ONE of the 96 acks, so most LRUs are
        # always a chunk or more behind the fastest responder; green here
        # means "acking fine", not "caught up with dispatch". Grey (the
        # reset-time default) only means "no ack recorded yet at all".
        # Exact per-chunk gap detail lands in the table at pass end.
        if self._lru_failed[q]:
            self.prog_matrix.set_one(q, _FAIL_QCOLOR)
        elif ok:
            self.prog_matrix.set_one(q, _OK_QCOLOR)
        self.gaps_table.item(q, 1).setText(str(self._lru_acked[q]))

    @staticmethod
    def _summarize_indices(indices) -> str:
        """[17,18,19,240] -> '17-19, 240' (keeps the gaps table readable)."""
        if not indices:
            return "—"
        parts = []
        start = prev = indices[0]
        for i in indices[1:]:
            if i == prev + 1:
                prev = i
                continue
            parts.append(f"{start}-{prev}" if prev > start else str(start))
            start = prev = i
        parts.append(f"{start}-{prev}" if prev > start else str(start))
        return ", ".join(parts)

    def on_upload_finished(self, missing_count: int, failed_count: int):
        self.progress_bar.setFormat("done — %v / %m")
        if self._controller is None:
            return
        total = self._controller.chunk_count
        for q in range(num_lru()):
            acked, missing, failed = self._controller.lru_gaps(q)
            self.gaps_table.item(q, 1).setText(f"{acked} / {total}")
            self.gaps_table.item(q, 2).setText(self._summarize_indices(failed))
            self.gaps_table.item(q, 3).setText(self._summarize_indices(missing))
            if failed:
                self.prog_matrix.set_one(q, _FAIL_QCOLOR)
            elif missing:
                self.prog_matrix.set_one(q, _PENDING_QCOLOR)
            else:
                self.prog_matrix.set_one(q, _OK_QCOLOR)
        self._apply_gate()

    # -- frame log ------------------------------------------------------------------------------

    def on_log_frame(self, raw: bytes, is_tx: bool, summary: str):
        view = self.tx_hex_view if is_tx else self.rx_hex_view
        view.setPlainText(hex_dump(raw))
        direction = "TX" if is_tx else "RX"
        if summary:
            self.log_summary_label.setText(f"{direction}: {summary}  ({len(raw)} bytes)")

    # -- tab-change reset (only called when no session is active) ---------------------------------

    def reset_to_idle(self):
        for pill in (self.op_status, self.upload_status,
                     self.link_status, self.lru_high_speed_status,
                     self.mode_back_status):
            pill.setText("Not sent yet")
            pill.setStyleSheet(_indicator_style())
        self.op_response_time_label.setText("")
        # Mode-step pills deliberately keep their state - the low-speed link
        # survives tab switches, and re-showing "Not sent yet" would imply
        # the sequence must be redone when it doesn't.
