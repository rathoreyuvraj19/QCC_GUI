"""
remote_programming_tab.py

"Remote Programming" - pushes a firmware bitstream from the host PC to all
96 QTRMs via QCC and drives the firmware-update state machine (mode change
-> LRU info -> authenticate -> program -> verify). Pure view: every action
is a Signal out to main_window (which delegates to RemoteProgController,
the session state machine in remote_prog_controller.py), every display
change is a slot the controller's signals feed.

Layout mirrors timing_tab.py's three side-by-side section columns
(Link Setup | Firmware Image | Operations), with the per-operation results
area (LRU table / live IAP grid / Program ack matrix) and a collapsible
raw-frame log underneath.

The whole tab is gated: nothing but the two mode-change steps is enabled
until BOTH steps have completed, per the IDD's mandatory sequence (first
switch all 96 QTRMs to the low-speed 115200 link, then switch QCC itself).
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
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
from core.packet import NUM_QTRM, RP_PAYLOAD_SIZE
from apps.remote_prog_controller import (
    OP_AUTHENTICATE, OP_LRU_INFO, OP_MODE_STEP1, OP_MODE_STEP2, OP_PROGRAM,
    OP_VERIFY,
)
from widgets.spin_field import SpinField

_ACCENT = "#00adb5"
_TEXT = "#eeeeee"
_LABEL_COLOR = "rgba(238, 238, 238, 0.62)"
_MUTED = "rgba(238, 238, 238, 0.45)"
_BORDER = "#4a515a"
_CARD_BG = "#393e46"

_SEND_BTN_STYLE = send_button_style(radius=12, font_size_px=14, padding="10px")
# Program writes flash - use Memory Operation's destructive-action red so it
# never reads as a routine send.
_PROGRAM_BTN_STYLE = send_button_style(
    color=WRITE_COLOR, hover=WRITE_HOVER_COLOR, pressed=WRITE_PRESSED_COLOR,
    radius=12, font_size_px=14, padding="10px",
)

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


def parse_intel_hex(text: str) -> bytes:
    """
    Minimal Intel HEX reader (record types 00 data / 01 EOF / 04 extended
    linear address / 02 extended segment address; 03/05 start-address
    records are ignored - they don't contribute image bytes). Returns the
    contiguous image from the lowest used address, gaps filled 0xFF (the
    erased-flash value, same as the final-chunk padding).
    """
    mem = {}
    upper = 0
    for line_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise ValueError(f"line {line_no}: missing ':' record mark")
        try:
            rec = bytes.fromhex(line[1:])
        except ValueError:
            raise ValueError(f"line {line_no}: invalid hex characters")
        if len(rec) < 5:
            raise ValueError(f"line {line_no}: record too short")
        count, addr_hi, addr_lo, rtype = rec[0], rec[1], rec[2], rec[3]
        data, csum = rec[4:-1], rec[-1]
        if len(data) != count:
            raise ValueError(f"line {line_no}: length mismatch")
        if (sum(rec[:-1]) + csum) & 0xFF != 0:
            raise ValueError(f"line {line_no}: bad record checksum")
        if rtype == 0x00:
            base = upper + (addr_hi << 8) + addr_lo
            for i, b in enumerate(data):
                mem[base + i] = b
        elif rtype == 0x01:
            break
        elif rtype == 0x04:
            upper = ((data[0] << 8) | data[1]) << 16
        elif rtype == 0x02:
            upper = ((data[0] << 8) | data[1]) << 4
        # 0x03/0x05 start-address records: no image bytes, skip
    if not mem:
        raise ValueError("no data records found")
    lo, hi = min(mem), max(mem)
    return bytes(mem.get(a, 0xFF) for a in range(lo, hi + 1))


class RemoteProgrammingTab(QWidget):
    mode_step1_requested = Signal()
    mode_step2_requested = Signal()
    lru_info_requested = Signal()
    authenticate_requested = Signal()
    verify_requested = Signal()
    program_requested = Signal(bytes)     # the full firmware image
    retry_requested = Signal()
    cancel_requested = Signal()
    chunk_timeout_changed = Signal(int)   # milliseconds

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image = b""                  # parsed firmware image bytes
        self._raw_file = b""               # file content as loaded from disk
        self._file_name = ""
        self._gate_open = False
        self._session_active = False
        self._chunks_dispatched = 0
        self._qtrm_acked = [0] * NUM_QTRM  # successful-ack count per QTRM
        self._qtrm_failed = [False] * NUM_QTRM
        self._have_gaps = False
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
        """Read-only handle for gap queries (qtrm_gaps/missing_chunk_indices)."""
        self._controller = controller

    # -- section builders -----------------------------------------------------

    def _build_link_section(self):
        box, form = _section_box("Link Setup")

        form.addWidget(_muted_note(
            "Mandatory order: broadcast low-speed mode to all 96 QTRMs first, "
            "then switch QCC itself. Operations unlock once both complete."
        ))

        self.step1_btn = QPushButton("1.  QTRMs → Low-Speed (115200)")
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
            "Step 2 wire format is provisional — Mode 5 message body byte 0 = 0x01; "
            "unconfirmed against the IDD."
        ))

        form.addStretch(1)

        self.gate_label = QLabel("Complete both steps to unlock operations")
        self.gate_label.setAlignment(Qt.AlignCenter)
        self.gate_label.setWordWrap(True)
        self.gate_label.setStyleSheet(f"color: {_MUTED}; font-size: 12px; background: transparent;")
        form.addWidget(self.gate_label)

        return box

    def _build_firmware_section(self):
        box, form = _section_box("Firmware Image")

        self.choose_file_btn = QPushButton("Choose File…")
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

        self.format_label = QLabel("—")
        _field_row(grid, 1, "Format", self.format_label)
        self.format_label.setStyleSheet(f"color: {_TEXT}; font-size: 13px; background: transparent;")
        self.size_label = QLabel("—")
        _field_row(grid, 2, "Total size", self.size_label)
        self.size_label.setStyleSheet(f"color: {_TEXT}; font-size: 13px; background: transparent;")
        self.chunk_count_label = QLabel("—")
        _field_row(grid, 3, "4K chunks", self.chunk_count_label)
        self.chunk_count_label.setStyleSheet(f"color: {_TEXT}; font-size: 13px; background: transparent;")

        self.chunk_timeout_spin = SpinField(200, 30_000, 2000, field_width=90)
        _field_row(grid, 4, "Chunk ack timeout (ms)", self.chunk_timeout_spin)
        self.chunk_timeout_spin.spin.valueChanged.connect(self.chunk_timeout_changed.emit)
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

        self.program_btn = QPushButton("Program")
        self.program_btn.setFixedHeight(38)
        self.program_btn.setStyleSheet(_PROGRAM_BTN_STYLE)
        self.program_btn.clicked.connect(self._on_program_clicked)
        form.addWidget(self.program_btn)

        row = QHBoxLayout()
        self.retry_btn = QPushButton("Retry Stragglers")
        self.retry_btn.setFixedHeight(32)
        self.retry_btn.setStyleSheet(_SEND_BTN_STYLE)
        self.retry_btn.setToolTip(
            "Re-broadcasts only the chunks some QTRM is still missing. "
            "Broadcast-only by design: QCC fans every frame out to all 96 "
            "identically, so QTRMs that already have a chunk re-ack or ignore it."
        )
        self.retry_btn.clicked.connect(self.retry_requested.emit)
        row.addWidget(self.retry_btn)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFixedHeight(32)
        self.cancel_btn.setStyleSheet(_SEND_BTN_STYLE)
        self.cancel_btn.clicked.connect(self.cancel_requested.emit)
        row.addWidget(self.cancel_btn)
        form.addLayout(row)

        self.program_status = _status_pill()
        form.addWidget(self.program_status)

        return box

    def _build_operations_section(self):
        box, form = _section_box("Operations")

        self.lru_btn = QPushButton("Get LRU Info")
        self.auth_btn = QPushButton("Authenticate")
        self.verify_btn = QPushButton("Verify")
        for btn, sig in ((self.lru_btn, self.lru_info_requested),
                         (self.auth_btn, self.authenticate_requested),
                         (self.verify_btn, self.verify_requested)):
            btn.setFixedHeight(38)
            btn.setStyleSheet(_SEND_BTN_STYLE)
            btn.clicked.connect(sig.emit)
            form.addWidget(btn)

        form.addWidget(_muted_note(
            "Authenticate / Verify poll for 30 s — replies arrive per-QTRM "
            "and light up the grid below as they land."
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
        table = QTableWidget(NUM_QTRM, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionMode(QTableWidget.NoSelection)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setMinimumHeight(320)
        for q in range(NUM_QTRM):
            item = QTableWidgetItem(f"QTRM-{q}")
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
        placeholder = QLabel("Run an operation to see per-QTRM results here")
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setStyleSheet(f"color: {_MUTED}; font-size: 13px; background: transparent;")
        self.results_stack.addWidget(placeholder)

        # Page 1: LRU Info table
        self.lru_table = self._make_table(["QTRM", "MFG_ID", "Part No", "Serial", "FW Version"])
        self.results_stack.addWidget(self.lru_table)

        # Page 2: Authenticate/Verify - LedMatrix + detail table side by side
        iap_page = QWidget()
        iap_row = QHBoxLayout(iap_page)
        iap_row.setContentsMargins(0, 0, 0, 0)
        iap_row.setSpacing(12)
        self.iap_matrix = LedMatrix(clickable=False)
        iap_row.addWidget(self.iap_matrix, 1)
        self.iap_table = self._make_table(["QTRM", "State", "IAP Status"])
        iap_row.addWidget(self.iap_table, 1)
        self.results_stack.addWidget(iap_page)

        # Page 3: Program - LedMatrix + gaps table
        prog_page = QWidget()
        prog_row = QHBoxLayout(prog_page)
        prog_row.setContentsMargins(0, 0, 0, 0)
        prog_row.setSpacing(12)
        self.prog_matrix = LedMatrix(clickable=False)
        prog_row.addWidget(self.prog_matrix, 1)
        self.gaps_table = self._make_table(["QTRM", "Acked", "Failed chunks", "Missing chunks"])
        prog_row.addWidget(self.gaps_table, 1)
        self.results_stack.addWidget(prog_page)

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

    # -- file handling -----------------------------------------------------------

    def _on_choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Firmware Image", "",
            "Firmware Images (*.bin *.hex);;All Files (*)",
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

        self._raw_file = raw
        self._file_name = path.split("/")[-1].split("\\")[-1]

        # Auto-detect Intel HEX: every non-blank line starts with ':' and the
        # first record parses. Anything else is treated as raw binary.
        is_hex = False
        try:
            text = raw.decode("ascii")
            stripped = [ln for ln in text.splitlines() if ln.strip()]
            is_hex = bool(stripped) and all(ln.strip().startswith(":") for ln in stripped)
        except UnicodeDecodeError:
            pass

        if is_hex:
            try:
                self._image = parse_intel_hex(text)
                self.format_label.setText("Intel HEX")
            except ValueError as e:
                QMessageBox.warning(self, "Intel HEX error", f"Failed to parse HEX file:\n{e}")
                self._image = b""
                return
        else:
            self._image = raw
            self.format_label.setText("Binary")

        n_chunks = (len(self._image) + RP_PAYLOAD_SIZE - 1) // RP_PAYLOAD_SIZE
        self.file_label.setText(self._file_name)
        self.size_label.setText(f"{len(self._image):,} bytes")
        self.chunk_count_label.setText(str(n_chunks))
        self.progress_bar.setRange(0, max(n_chunks, 1))
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("ready")
        self._apply_gate()

    def _on_program_clicked(self):
        if not self._image:
            QMessageBox.warning(self, "No image", "Choose a firmware image first.")
            return
        n_chunks = (len(self._image) + RP_PAYLOAD_SIZE - 1) // RP_PAYLOAD_SIZE
        confirm = QMessageBox.question(
            self, "Program firmware",
            f"Broadcast {self._file_name} ({len(self._image):,} bytes, "
            f"{n_chunks} chunks) to all 96 QTRMs?\n\nThis writes flash on every QTRM.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm == QMessageBox.Yes:
            self.program_requested.emit(self._image)

    # -- gate / enable state -------------------------------------------------------

    def _apply_gate(self):
        ops_ok = self._gate_open and not self._session_active
        for btn in (self.lru_btn, self.auth_btn, self.verify_btn):
            btn.setEnabled(ops_ok)
        self.program_btn.setEnabled(ops_ok and bool(self._image))
        self.retry_btn.setEnabled(ops_ok and self._have_gaps)
        self.cancel_btn.setEnabled(self._session_active)
        self.step1_btn.setEnabled(not self._session_active)
        self.step2_btn.setEnabled(not self._session_active)
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
            self.step1_status.setText("Not sent yet")
            self.step1_status.setStyleSheet(_indicator_style())
            self.step2_status.setText("Not sent yet")
            self.step2_status.setStyleSheet(_indicator_style())
        self._apply_gate()

    # -- session lifecycle (driven by main_window / controller) ---------------------

    def mark_session_started(self, op: str):
        self._session_active = True
        pill_map = {
            OP_MODE_STEP1: self.step1_status,
            OP_MODE_STEP2: self.step2_status,
            OP_LRU_INFO: self.op_status,
            OP_AUTHENTICATE: self.op_status,
            OP_VERIFY: self.op_status,
            OP_PROGRAM: self.program_status,
        }
        pill = pill_map.get(op)
        if pill is not None:
            pill.setText("Sending...")
            pill.setStyleSheet(_indicator_style(_PENDING_COLOR))
        self.op_response_time_label.setText("")

        if op == OP_LRU_INFO:
            self.results_stack.setCurrentIndex(1)
            self._reset_lru_table()
        elif op in (OP_AUTHENTICATE, OP_VERIFY):
            self.results_stack.setCurrentIndex(2)
            self._reset_iap_grid()
        elif op == OP_PROGRAM:
            self.results_stack.setCurrentIndex(3)
            self._reset_program_view()
        self._apply_gate()

    def mark_retry_started(self):
        """Like mark_session_started(OP_PROGRAM) but keeps the ack matrix -
        the stragglers pass fills gaps in the existing state, it doesn't
        start a fresh transfer."""
        self._session_active = True
        self.results_stack.setCurrentIndex(3)
        self.program_status.setText("Retrying stragglers...")
        self.program_status.setStyleSheet(_indicator_style(_PENDING_COLOR))
        self.progress_bar.setFormat("retrying… %v / %m")
        self._apply_gate()

    def on_session_finished(self, op: str, ok: bool, text: str):
        self._session_active = False
        pill_map = {
            OP_MODE_STEP1: self.step1_status,
            OP_MODE_STEP2: self.step2_status,
            OP_LRU_INFO: self.op_status,
            OP_AUTHENTICATE: self.op_status,
            OP_VERIFY: self.op_status,
            OP_PROGRAM: self.program_status,
        }
        pill = pill_map.get(op)
        if pill is not None:
            pill.setText(text)
            pill.setStyleSheet(_indicator_style(_OK_COLOR if ok else _FAIL_COLOR))
        self._apply_gate()

    def on_step_result(self, op: str, ok: bool, text: str):
        # Interim per-step feedback (e.g. Program's IAP window closing) -
        # the final pill state still comes from on_session_finished.
        if op == OP_MODE_STEP1:
            self.step1_status.setText(text)
            self.step1_status.setStyleSheet(_indicator_style(_OK_COLOR if ok else _FAIL_COLOR))
        elif op == OP_MODE_STEP2:
            self.step2_status.setText(text)
            self.step2_status.setStyleSheet(_indicator_style(_OK_COLOR if ok else _FAIL_COLOR))
        elif op == OP_PROGRAM:
            self.program_status.setText(text)
            self.program_status.setStyleSheet(
                _indicator_style(_PENDING_COLOR if ok else _FAIL_COLOR))
        else:
            self.op_status.setText(text)
            self.op_status.setStyleSheet(_indicator_style(_OK_COLOR if ok else _FAIL_COLOR))

    def show_response_time(self, microseconds: float):
        self.op_response_time_label.setText(f"{microseconds:.0f} µs")

    # -- LRU Info ---------------------------------------------------------------------

    def _reset_lru_table(self):
        for q in range(NUM_QTRM):
            for c in range(1, 5):
                self.lru_table.item(q, c).setText("—")

    def on_lru_row(self, q: int, resp):
        self.lru_table.item(q, 1).setText(str(resp.mfg_id))
        self.lru_table.item(q, 2).setText(str(resp.part_no))
        self.lru_table.item(q, 3).setText(str(resp.serial_num))
        self.lru_table.item(q, 4).setText(str(resp.fw_version))

    # -- Authenticate / Verify live grid -------------------------------------------------

    def _reset_iap_grid(self):
        self.iap_matrix.set_all(_PENDING_QCOLOR)
        for q in range(NUM_QTRM):
            self.iap_table.item(q, 1).setText("Pending")
            self.iap_table.item(q, 2).setText("—")

    def on_op_row(self, op: str, q: int, parsed):
        if op not in (OP_AUTHENTICATE, OP_VERIFY):
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

    def on_op_window_closed(self, op: str):
        if op not in (OP_AUTHENTICATE, OP_VERIFY):
            return
        for q in range(NUM_QTRM):
            if self.iap_table.item(q, 1).text() == "Pending":
                self.iap_table.item(q, 1).setText("No Response")
                self.iap_matrix.set_one(q, _FAIL_QCOLOR)

    # -- Program ---------------------------------------------------------------------------

    def _reset_program_view(self):
        self.prog_matrix.set_all(_PENDING_QCOLOR)
        self._chunks_dispatched = 0
        self._qtrm_acked = [0] * NUM_QTRM
        self._qtrm_failed = [False] * NUM_QTRM
        self._have_gaps = False
        for q in range(NUM_QTRM):
            self.gaps_table.item(q, 1).setText("0")
            self.gaps_table.item(q, 2).setText("—")
            self.gaps_table.item(q, 3).setText("—")
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("programming… %v / %m")
        self.chunk_label.setText("")

    def on_chunk_progress(self, idx: int, count: int, attempt: int):
        self._chunks_dispatched = max(self._chunks_dispatched, idx + 1)
        self.progress_bar.setRange(0, count)
        self.progress_bar.setValue(idx + 1)
        retry_note = f"  (attempt {attempt})" if attempt > 1 else ""
        self.chunk_label.setText(f"chunk {idx + 1} / {count}{retry_note}")

    def on_ack_recorded(self, q: int, idx: int, ok: bool):
        if ok:
            self._qtrm_acked[q] += 1
        else:
            self._qtrm_failed[q] = True
        # Cheap live coloring: red is sticky on any reported failure; green
        # while the QTRM is keeping pace with what's been dispatched so far,
        # pending-grey when it's lagging. Exact gap detail lands in the
        # table at pass end.
        if self._qtrm_failed[q]:
            self.prog_matrix.set_one(q, _FAIL_QCOLOR)
        elif self._qtrm_acked[q] >= self._chunks_dispatched:
            self.prog_matrix.set_one(q, _OK_QCOLOR)
        else:
            self.prog_matrix.set_one(q, _PENDING_QCOLOR)
        self.gaps_table.item(q, 1).setText(str(self._qtrm_acked[q]))

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

    def on_program_finished(self, missing_count: int, failed_count: int):
        self.progress_bar.setFormat("done — %v / %m")
        if self._controller is None:
            return
        total = self._controller.chunk_count
        self._have_gaps = missing_count > 0
        for q in range(NUM_QTRM):
            acked, missing, failed = self._controller.qtrm_gaps(q)
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
        for pill in (self.op_status, self.program_status):
            pill.setText("Not sent yet")
            pill.setStyleSheet(_indicator_style())
        self.op_response_time_label.setText("")
        # Mode-step pills deliberately keep their state - the low-speed link
        # survives tab switches, and re-showing "Not sent yet" would imply
        # the sequence must be redone when it doesn't.
