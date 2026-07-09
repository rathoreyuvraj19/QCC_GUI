"""
status_responder_app.py

Standalone test responder - simulates the QTRM side answering Status Type
requests (Section 10 of the QTRM Message Format IDD), so the main GUI can
be tested end-to-end without real hardware. Doesn't do much: listens on a
UDP port, and for every QTRM slot in an incoming frame, replies based on
byte4's Status Type (and, for DIAGNOSTIC, Sub Status Type) alone -
independent of the slot's Command Type. This matches real QTRM behavior:
any command (Dwell, Cal, Isolation, Memory Write, ...) can request a
status reply via that same byte, not just the dedicated Status Command
(0x21) - a real QTRM doesn't care which command carried the request, only
what Status Type it asked for.

The two exceptions that never reply, regardless of what Status Type bits
happen to be set: Command Type 0x00 (Reserved - also what an untouched/
zero-filled individual-target slot looks like) and 0x20 (Soft Reset,
which is fire-and-forget by design). Every other command type replies
normally based on its Status Type.

Unlike udp_worker.py's UdpWorker (which always sends to one fixed
configured destination), this replies directly to whichever address the
query actually came from, so it works regardless of what local port the
main GUI happens to be using.

Run directly:  python status_responder_app.py
"""

import socket
import struct
import sys

from PySide6.QtCore import QRegularExpression, Qt, QThread, Signal
from PySide6.QtGui import QGuiApplication, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from core.packet import (
    QTRMSlot, QCCHeaderTx, QTRM_SLOT_SIZE, NUM_QTRM, FIXED_HEADER_SIZE, QCC_HEADER_SIZE,
    TOTAL_PACKET_SIZE, CMD_RESERVED, CMD_SOFT_RESET, crc8,
    STATUS_TYPE_ACK, STATUS_TYPE_LINK, STATUS_TYPE_HEALTH,
    STATUS_TYPE_ERR_LOG, STATUS_TYPE_MFG, STATUS_TYPE_DIAGNOSTIC,
    DIAGNOSTIC_TYPE_DETAILED_HEALTH, LINK_SENTINEL,
)

# Command types that never reply, regardless of Status Type bits: Reserved
# (0x00, also what an untouched/zero-filled individual-target slot looks
# like) and Soft Reset (fire-and-forget by design). Every other command
# type replies based on its Status Type alone - see module docstring.
_NO_REPLY_COMMAND_TYPES = (CMD_RESERVED, CMD_SOFT_RESET)
from widgets.segmented_control import SegmentedControl
from widgets.spin_field import SpinField
from theme import STYLESHEET
from widgets.titled_group import titled_group_box

_STATUS_TYPE_NAMES = {
    STATUS_TYPE_ACK: "ACK",
    STATUS_TYPE_LINK: "LINK",
    STATUS_TYPE_HEALTH: "HEALTH",
    STATUS_TYPE_ERR_LOG: "TRM Err. Log",
    STATUS_TYPE_MFG: "TRM Mfg. Details",
    STATUS_TYPE_DIAGNOSTIC: "DIAGNOSTIC",
}


# ---------------------------------------------------------------------------
# Mock reply builders - one per Status Type. Values are plausible dummy data
# (varied a little per QTRM index so the Status tab's per-QTRM field display
# actually shows something distinguishable) rather than real measurements -
# this tool only exists to exercise the wire format, not simulate real TRM
# behavior.
# ---------------------------------------------------------------------------


def _checksum(data: bytes) -> int:
    chk = 0
    for b in data:
        chk ^= b
    return chk


def _finish_10_byte(body: bytearray) -> bytes:
    body[9] = _checksum(bytes(body[:9]))
    return bytes(body)


def _mock_link_reply(qtrm_index: int, query_slot: bytes) -> bytes:
    body = bytearray(QTRM_SLOT_SIZE)
    body[0] = QTRMSlot.HEADER_BYTE
    body[1] = 0x00
    body[2] = query_slot[2]
    body[3] = STATUS_TYPE_LINK
    body[4:9] = LINK_SENTINEL
    return _finish_10_byte(body)


def _mock_ack_reply(qtrm_index: int, query_slot: bytes) -> bytes:
    body = bytearray(QTRM_SLOT_SIZE)
    body[0] = QTRMSlot.HEADER_BYTE
    body[1] = 0x00
    body[2] = query_slot[2]
    body[3] = query_slot[3]       # echo Sub Status/Status Type
    body[4] = query_slot[4]       # echo Message/Dwell ID
    body[5:9] = query_slot[5:9]   # echo the rest of the query (ACK Message Format, Section 10.1.1.2)
    return _finish_10_byte(body)


def _mock_health_reply(qtrm_index: int, query_slot: bytes) -> bytes:
    body = bytearray(QTRM_SLOT_SIZE)
    body[0] = QTRMSlot.HEADER_BYTE
    body[1] = 0x00
    body[2] = query_slot[2]
    body[3] = STATUS_TYPE_HEALTH
    body[4] = 200 + (qtrm_index % 20)   # DC Voltage Status
    body[5] = 50 + (qtrm_index % 10)    # DC Current Status
    body[6] = 30 + (qtrm_index % 40)    # Temperature Status
    body[7] = 80 + (qtrm_index % 15)    # Tx Forward RF Status
    body[8] = 90 + (qtrm_index % 5)     # Rx/Reverse RF Status
    return _finish_10_byte(body)


def _mock_err_log_reply(qtrm_index: int, query_slot: bytes) -> bytes:
    body = bytearray(QTRM_SLOT_SIZE)
    body[0] = QTRMSlot.HEADER_BYTE
    body[1] = 0x00
    body[2] = query_slot[2]
    body[3] = STATUS_TYPE_ERR_LOG
    body[4] = 0   # TRM shutdown flags - none
    body[5] = 0   # Header Error
    body[6] = 0   # Footer/CRC Error
    body[7] = 0   # Timeout Error
    body[8] = ((qtrm_index % 3) << 4) | (qtrm_index % 4)  # PRT duty/width violation counts
    return _finish_10_byte(body)


def _mock_mfg_reply(qtrm_index: int, query_slot: bytes) -> bytes:
    body = bytearray(QTRM_SLOT_SIZE)
    body[0] = QTRMSlot.HEADER_BYTE
    body[1] = 0x00
    body[2] = query_slot[2]
    body[3] = STATUS_TYPE_MFG
    body[4] = (0 << 4) | 1  # Mfg Agency ID 0 (LRDE), firmware version 1
    serial = 1000 + qtrm_index
    body[5] = serial & 0xFF
    body[6] = (serial >> 8) & 0xFF
    on_time = 100 + qtrm_index
    body[7] = on_time & 0xFF
    body[8] = (on_time >> 8) & 0xFF
    return _finish_10_byte(body)


def _mock_diagnostic_reply(qtrm_index: int, query_slot: bytes) -> bytes:
    diagnostic_type = (query_slot[3] >> 4) & 0x0F
    msg_len = 30  # Diagnostic responses are always full Dwell-size, regardless of the 10-byte query
    body = bytearray(QTRM_SLOT_SIZE)
    body[0] = QTRMSlot.HEADER_BYTE
    body[1] = 0x04
    body[2] = query_slot[2]
    body[3] = (diagnostic_type << 4) | STATUS_TYPE_DIAGNOSTIC
    body[5] = 200 + (qtrm_index % 10)  # Total PRT Count
    body[6] = 198 + (qtrm_index % 10)  # Processed PRT Count
    body[7] = 2                        # Dwell PRT Count
    body[8] = 5 + (qtrm_index % 3)      # Total SOB Count

    if diagnostic_type == DIAGNOSTIC_TYPE_DETAILED_HEALTH:
        body[4] = 1  # Operation Command Type
        for ch in range(4):
            off = 9 + ch * 5
            body[off] = 30 + ((qtrm_index + ch) % 40)       # Temp
            body[off + 1] = 200 + ((qtrm_index + ch) % 20)  # DC Status
            body[off + 2] = 80 + ((qtrm_index + ch) % 15)   # RF Status
            body[off + 3] = ch                               # Tx Control Count
            body[off + 4] = ch                               # Rx Control Count
    else:
        body[4] = 0  # Beam Data Register Address - not implemented, always 0
        for ch in range(4):
            off = 9 + ch * 5
            body[off] = (1 << 4) | 3       # Op Mode | Control
            body[off + 1] = 10 + ch        # Tx Phase
            body[off + 2] = 20 + ch        # Tx Atten
            body[off + 3] = 30 + ch        # Rx Phase
            body[off + 4] = 40 + ch        # Rx Atten

    body[msg_len - 1] = _checksum(bytes(body[:msg_len - 1]))
    return bytes(body)


_REPLY_BUILDERS = {
    STATUS_TYPE_LINK: _mock_link_reply,
    STATUS_TYPE_ACK: _mock_ack_reply,
    STATUS_TYPE_HEALTH: _mock_health_reply,
    STATUS_TYPE_ERR_LOG: _mock_err_log_reply,
    STATUS_TYPE_MFG: _mock_mfg_reply,
    STATUS_TYPE_DIAGNOSTIC: _mock_diagnostic_reply,
}

# Fixed (not re-randomized per run - reproducible between test sessions),
# plausible non-zero default response header, so there's always something
# to actually look at in the RX/TX/tab HeaderPanel sidebars, and
# QCCHeaderTx.to_bytes() computes a real, correct CRC-8 so "CHECKSUM: OK"
# reports truthfully. Built as one real 90-byte QCCHeaderTx (per
# QCC_90Byte_Header_BitTable.docx, 2026-07-05) and then split into the two
# 32/58-byte pieces the window's editable byte grids use - the checksum
# (the header's very last byte) covers the whole 90 bytes, so it stays
# valid as long as both pieces are sent back-to-back unchanged, which
# build_mock_response_frame already does. These are just the *defaults*
# shown in the window's editable byte grids - start_listening() reads
# whatever's actually in those grids at Start time, not these constants
# directly, so editing them changes every future response's header
# without touching this file.
_mock_header = QCCHeaderTx()
_mock_header.destination_id = 0x01
_mock_header.source_id = 0x02
_mock_header.packet_size = TOTAL_PACKET_SIZE
_mock_header.echo_byte = 0
_mock_header.command_ack = 1
_mock_header.message_number = 1
_mock_header.date = 5
_mock_header.month = 7
_mock_header.year = 2026
_mock_header.time_of_day = 0
_mock_header.qcc_command = QCCHeaderTx.QCC_COMMAND_DATA_DISTRIBUTION
_mock_header.fpga_temperature = 42
_mock_header.board_temperature = 350
_mock_header.board_humidity = 550
_mock_header.input_sob_count = 100
_mock_header.input_prt_count = 200
_mock_header.input_pps_count = 300
_mock_header.output_prt_count = 400
_mock_header.output_sob_count = 500
_mock_header.input_sob_width_us = 10
_mock_header.output_sob_width_us = 20
_mock_header.input_prt_width_us = 30
_mock_header.output_prt_width_us = 40
_mock_header.input_pps_width_us = 50
_mock_header.pps_counter = 600
_mock_header.set_generator_state(sob_internal=True, prt_internal=False)
_mock_header.chip_id = 0x12345678
_mock_header_bytes = _mock_header.to_bytes()

_MOCK_FIXED_HEADER = _mock_header_bytes[:FIXED_HEADER_SIZE]
_MOCK_QCC_HEADER = _mock_header_bytes[FIXED_HEADER_SIZE:]


# Per-QTRM fault modes, injected on top of whatever reply would normally be
# built - lets the main GUI's error-handling paths (dropped-reply timeouts,
# checksum-reject logic, degraded-health display) be exercised without real
# faulty hardware. Not part of the IDD itself; these are test-harness-only
# corruptions layered on an otherwise IDD-correct reply.
FAULT_NONE = "none"
FAULT_NO_REPLY = "no_reply"
FAULT_BAD_CHECKSUM = "bad_checksum"
FAULT_FORCE_ERROR = "force_error"

_FAULT_MODE_NAMES = {
    FAULT_NO_REPLY: "No Reply",
    FAULT_BAD_CHECKSUM: "Bad Checksum",
    FAULT_FORCE_ERROR: "Forced Error/Fault Flags",
}


def parse_qtrm_index_spec(text: str) -> set:
    """
    "3,7-9, 12" -> {3, 7, 8, 9, 12}. Blank/unparseable tokens are ignored
    (this only feeds a best-effort test fixture, not a protocol field), out-
    of-range indices are silently dropped by the caller since they'd never
    match a real slot anyway.
    """
    indices = set()
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            lo, _, hi = token.partition("-")
            try:
                lo, hi = int(lo), int(hi)
            except ValueError:
                continue
            indices.update(range(min(lo, hi), max(lo, hi) + 1))
        else:
            try:
                indices.add(int(token))
            except ValueError:
                continue
    return indices


def _apply_force_error(status_type: int, reply: bytes) -> bytes:
    """
    Overwrite a reply already built for status_type with worst-case values,
    then re-stamp its checksum so it still validates as a (badly-behaved)
    QTRM rather than a corrupt one. Only HEALTH/ERR_LOG have a documented
    "fault" shape to force; other status types have no fault concept in the
    IDD, so they're left as their normal (already-valid) reply.
    """
    body = bytearray(reply)
    if status_type == STATUS_TYPE_HEALTH:
        body[4:9] = bytes([255, 255, 255, 0, 0])  # DC/current/temp pegged, RF dead
    elif status_type == STATUS_TYPE_ERR_LOG:
        body[4] = 0xFF  # every TRM shutdown flag set
        body[5] = 1     # Header Error
        body[6] = 1     # Footer/CRC Error
        body[7] = 1     # Timeout Error
        body[8] = 0xFF  # PRT duty/width violation counts maxed
    else:
        return reply
    body[9] = _checksum(bytes(body[:9]))
    return bytes(body)


# Per QCCHeaderRx/QCCHeaderTx's docstrings (docs/idd/packet_spec.yaml), these
# first 33 bytes of a response (Destination/Source ID swapped, Packet Size
# fixed, Echo Byte/Message Number/Date/Month/Year/Time Of Day/Reserved0 all
# echoed unchanged, Command ACK forced to 0x01, QCC_COMMAND echoed) are
# entirely determined by the command that prompted them - a real QCC has no
# freedom here. Only bytes 33-89 (the telemetry fields + checksum) are
# actually up to the QCC to fill in, which is what the editable byte grids
# are for.
ECHOED_PREFIX_SIZE = 33


def _build_echoed_header_prefix(query_frame: bytes) -> bytes:
    q = query_frame[:ECHOED_PREFIX_SIZE]
    out = bytearray(q)
    out[0], out[1] = q[1], q[0]                      # Destination/Source ID swap
    out[2:4] = struct.pack("<H", TOTAL_PACKET_SIZE)   # Packet Size - fixed
    out[5] = 1                                        # Command ACK - 0x01 for a response
    # bytes 4 (Echo Byte), 6-9 (Message Number), 10-31 (Date/Month/Year/Time
    # Of Day/Reserved0), 32 (QCC_COMMAND) are already correct via the q copy.
    return bytes(out)


def build_mock_response_frame(query_frame: bytes, fixed_header: bytes = None, qcc_header: bytes = None,
                               fault_indices: set = None, fault_mode: str = FAULT_NONE,
                               auto_echo_header: bool = True):
    """
    Returns (response_frame, [(qtrm_index, status_type_name), ...] for every
    QTRM that got a reply). fixed_header/qcc_header default to the fixed
    test-pattern bytes below if not given - pass explicit bytes (e.g. from
    the window's editable hex fields) to send a different 90-byte header.
    fault_indices/fault_mode apply one of the FAULT_* corruptions above to
    just those QTRM indices' replies - every other QTRM behaves normally.

    auto_echo_header (default True, matches real QCC behavior): overwrites
    the response header's first 33 bytes with values derived from the query
    per the IDD (see _build_echoed_header_prefix) and re-stamps the
    checksum, rather than sending whatever's in the editable grids for that
    span - those 33 bytes aren't actually free-form on real hardware. Set
    False to send the grids' bytes completely as-is (e.g. to deliberately
    test the main GUI's handling of a QCC that gets these wrong).
    """
    if fixed_header is None:
        fixed_header = _MOCK_FIXED_HEADER
    if qcc_header is None:
        qcc_header = _MOCK_QCC_HEADER
    if fault_indices is None:
        fault_indices = ()
    base = FIXED_HEADER_SIZE + QCC_HEADER_SIZE
    out = bytearray(fixed_header + qcc_header)
    if auto_echo_header:
        out[:ECHOED_PREFIX_SIZE] = _build_echoed_header_prefix(query_frame)
        out[base - 1] = crc8(bytes(out[: base - 1]))
    replied = []
    for i in range(NUM_QTRM):
        slot = query_frame[base + i * QTRM_SLOT_SIZE: base + (i + 1) * QTRM_SLOT_SIZE]
        valid_header = slot[0] == QTRMSlot.HEADER_BYTE
        command_type = slot[2] if valid_header else None
        status_type = slot[3] & 0x0F if valid_header else None
        no_reply = command_type is None or command_type in _NO_REPLY_COMMAND_TYPES
        builder = _REPLY_BUILDERS.get(status_type) if not no_reply else None

        faulty = i in fault_indices and fault_mode != FAULT_NONE
        if faulty and fault_mode == FAULT_NO_REPLY:
            out.extend(bytes(QTRM_SLOT_SIZE))
            continue

        if builder is not None:
            reply = builder(i, slot)
            status_name = _STATUS_TYPE_NAMES.get(status_type, str(status_type))
            if faulty and fault_mode == FAULT_BAD_CHECKSUM:
                reply = bytes(reply[:-1]) + bytes([reply[-1] ^ 0xFF])
                status_name += " [bad checksum]"
            elif faulty and fault_mode == FAULT_FORCE_ERROR:
                reply = _apply_force_error(status_type, reply)
                status_name += " [forced error]"
            out.extend(reply)
            replied.append((i, status_name))
        else:
            out.extend(bytes(QTRM_SLOT_SIZE))
    return bytes(out), replied


class ResponderWorker(QThread):
    frame_processed = Signal(bytes, bytes, list, tuple)  # query, response, replied, addr
    error = Signal(str)
    status = Signal(str)

    def __init__(self, local_port: int, fixed_header: bytes = None, qcc_header: bytes = None,
                 fault_indices: set = None, fault_mode: str = FAULT_NONE, auto_echo_header: bool = True,
                 parent=None):
        super().__init__(parent)
        self.local_port = local_port
        self.fixed_header = fixed_header if fixed_header is not None else _MOCK_FIXED_HEADER
        self.qcc_header = qcc_header if qcc_header is not None else _MOCK_QCC_HEADER
        self.fault_indices = fault_indices if fault_indices is not None else set()
        self.fault_mode = fault_mode
        self.auto_echo_header = auto_echo_header
        self._sock = None
        self._running = False

    def run(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", self.local_port))
            self._sock.settimeout(0.5)
        except OSError as e:
            self.error.emit(f"Failed to bind local UDP port {self.local_port}: {e}")
            return

        self._running = True
        self.status.emit(f"Listening on 0.0.0.0:{self.local_port}")

        while self._running:
            try:
                data, addr = self._sock.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError as e:
                if self._running:
                    self.error.emit(f"Socket error while receiving: {e}")
                break

            if len(data) != TOTAL_PACKET_SIZE:
                self.error.emit(f"Received {len(data)} bytes from {addr}, expected {TOTAL_PACKET_SIZE} - dropped")
                continue

            response, replied = build_mock_response_frame(
                data, self.fixed_header, self.qcc_header, self.fault_indices, self.fault_mode,
                self.auto_echo_header,
            )
            try:
                self._sock.sendto(response, addr)
            except OSError as e:
                self.error.emit(f"Failed to send response: {e}")
                continue
            self.frame_processed.emit(data, response, replied, addr)

        if self._sock:
            self._sock.close()
        self.status.emit("Stopped")

    def stop(self):
        self._running = False
        self.wait(2000)


_HEX_VALIDATOR_PATTERN = "[0-9A-Fa-f]{0,2}"
# Incremental-typing-safe: 0-2 digit prefixes are always allowed (covers
# every partial state while typing), a full 3-digit value must be 100-255.
_DEC_VALIDATOR_PATTERN = r"[0-9]{0,2}|1[0-9]{2}|2[0-4][0-9]|25[0-5]"


class ByteGrid(QWidget):
    """
    One small editable cell per byte, wrapped into a grid, each labeled
    with its byte index above it - lets every byte of a header be set
    individually instead of only as one long pasted hex string.
    Hex/decimal entry is toggled via set_hex_mode(bool) - switching
    re-renders every cell's current value in the new base without
    changing the underlying byte values at all.
    """

    bytes_changed = Signal()

    def __init__(self, num_bytes: int, wrap_cols: int = 14, start_index: int = 1, parent=None):
        super().__init__(parent)
        self._hex_mode = True
        self._hex_validator = QRegularExpressionValidator(QRegularExpression(_HEX_VALIDATOR_PATTERN))
        self._dec_validator = QRegularExpressionValidator(QRegularExpression(_DEC_VALIDATOR_PATTERN))
        self._cells = []
        grid = QGridLayout(self)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        for i in range(num_bytes):
            label_index = start_index + i
            index_label = QLabel(str(label_index))
            index_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            # Fixed width - without this, the index number stretches to
            # fill however wide the grid column ends up being and its
            # centered text drifts away from the (narrower, fixed-width)
            # cell it's meant to label.
            index_label.setFixedWidth(22)
            index_label.setStyleSheet("font-size: 9pt; font-weight: 600; color: rgba(238, 238, 238, 0.65);")

            cell = QLineEdit("00")
            cell.setMaxLength(2)
            cell.setFixedWidth(30)
            # Fixed height too - the global QLineEdit rule's normal 8px/12px
            # padding was what gave fields their vertical size; overriding
            # padding down to fit a 30px-wide cell shrank the computed
            # sizeHint enough to clip glyphs top/bottom, leaving only their
            # middle strokes visible (looked like unreadable dashes).
            cell.setFixedHeight(26)
            cell.setAlignment(Qt.AlignCenter)
            cell.setValidator(self._hex_validator)
            cell.setToolTip(f"Byte {label_index}")
            # The app's global QLineEdit style pads 8px/12px, meant for
            # normal-width fields - on a 30px-wide cell that leaves almost
            # no room for the digits themselves, making them invisible.
            # :disabled is set explicitly too - the global stylesheet has no
            # rule for it, so Qt's default near-black-on-dark disabled text
            # was unreadable against this app's dark cards.
            # border-radius overridden too - the global QLineEdit rule's
            # 12px radius (meant for normal-width fields) eats most of a
            # 30px-wide cell's corners, squeezing the two digits down to an
            # unreadable sliver.
            cell.setStyleSheet(
                "QLineEdit { padding: 2px; border-radius: 4px; }"
                "QLineEdit:disabled { color: rgba(238, 238, 238, 0.55); background-color: rgba(255, 255, 255, 0.05); }"
            )
            cell.textChanged.connect(self.bytes_changed)

            # Index and cell side by side (not stacked) - grouped as one
            # visual unit, index reads clearly at a larger size instead of
            # a tiny muted number floating above the box.
            cell_row = QHBoxLayout()
            cell_row.setSpacing(4)
            cell_row.setContentsMargins(0, 0, 0, 0)
            cell_row.addWidget(index_label)
            cell_row.addWidget(cell)
            cell_widget = QWidget()
            cell_widget.setLayout(cell_row)

            self._cells.append(cell)
            grid.addWidget(cell_widget, i // wrap_cols, i % wrap_cols)

    def get_bytes(self) -> bytes:
        base = 16 if self._hex_mode else 10
        return bytes(int(cell.text(), base) if cell.text() else 0 for cell in self._cells)

    def set_bytes(self, data: bytes):
        # Signals blocked: this is a programmatic bulk rewrite, not a user
        # edit, and letting bytes_changed fire mid-loop lets a consumer
        # read a torn state (some cells already in the new base/value,
        # others not yet) - this is exactly what caused a ValueError
        # overflow when switching hex/decimal mode while a worker was
        # listening (see status_responder_app.py commit history).
        for cell, b in zip(self._cells, data):
            cell.blockSignals(True)
            cell.setText(f"{b:02X}" if self._hex_mode else str(b))
            cell.blockSignals(False)

    def set_cells_enabled(self, count: int, enabled: bool):
        """Enable/disable the first `count` cells - used to grey out the IDD-mandated echoed prefix when auto-echo is on."""
        for cell in self._cells[:count]:
            cell.setEnabled(enabled)

    def set_hex_mode(self, hex_mode: bool):
        if hex_mode == self._hex_mode:
            return
        current = self.get_bytes()  # read using the OLD mode before switching
        self._hex_mode = hex_mode
        for cell in self._cells:
            cell.blockSignals(True)
            cell.setValidator(self._hex_validator if hex_mode else self._dec_validator)
            cell.setMaxLength(2 if hex_mode else 3)
            cell.setFixedWidth(30 if hex_mode else 36)
            cell.blockSignals(False)
        self.set_bytes(current)  # rewrite using the NEW mode


class StatusResponderWindow(QMainWindow):
    closed = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("QCC Status Responder - Mock QTRM")
        # Cap to the actual available screen so Qt never requests a window
        # minimum larger than fits (see main_window.py's identical fix for
        # the "QWindowsWindow::setGeometry: Unable to set geometry" spam).
        self.setMinimumSize(600, 400)
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        width = 900 if avail is None else min(900, avail.width() - 40)
        height = 600 if avail is None else min(600, avail.height() - 60)
        self.resize(width, height)

        self.worker: ResponderWorker | None = None
        self._frame_count = 0

        # The byte grid (90+ small fixed-size cells) doesn't compress - on a
        # window shorter than its natural height it used to get squeezed by
        # the plain QVBoxLayout instead, clipping/overlapping every group
        # below the Listener. Scrolling the whole body lets the window
        # shrink freely while every group keeps its natural, readable size.
        central = QWidget()
        root = QVBoxLayout(central)

        root.addWidget(self._build_listen_group())
        root.addWidget(self._build_fault_injection_group())
        root.addWidget(self._build_response_header_group())
        root.addWidget(self._build_log_group(), 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(central)
        self.setCentralWidget(scroll)

    def _build_fault_injection_group(self):
        box, outer = titled_group_box("Fault Injection (test-harness only, not part of the IDD)")
        row = QHBoxLayout()
        outer.addLayout(row)

        row.addWidget(QLabel("QTRM indices:"))
        self.fault_indices_edit = QLineEdit()
        self.fault_indices_edit.setPlaceholderText("e.g. 3,7-9,12")
        self.fault_indices_edit.setFixedWidth(160)
        self.fault_indices_edit.textChanged.connect(self._on_fault_settings_changed)
        row.addWidget(self.fault_indices_edit)

        row.addWidget(QLabel("Mode:"))
        self.fault_mode_combo = QComboBox()
        self.fault_mode_combo.addItem("None", FAULT_NONE)
        self.fault_mode_combo.addItem(_FAULT_MODE_NAMES[FAULT_NO_REPLY], FAULT_NO_REPLY)
        self.fault_mode_combo.addItem(_FAULT_MODE_NAMES[FAULT_BAD_CHECKSUM], FAULT_BAD_CHECKSUM)
        self.fault_mode_combo.addItem(_FAULT_MODE_NAMES[FAULT_FORCE_ERROR], FAULT_FORCE_ERROR)
        self.fault_mode_combo.currentIndexChanged.connect(self._on_fault_settings_changed)
        row.addWidget(self.fault_mode_combo)
        row.addStretch(1)

        return box

    def _current_fault_indices(self) -> set:
        return parse_qtrm_index_spec(self.fault_indices_edit.text())

    def _current_fault_mode(self) -> str:
        return self.fault_mode_combo.currentData()

    def _on_fault_settings_changed(self, *_args):
        # Push edits straight into the already-running worker (if any), same
        # pattern as _on_header_bytes_changed - takes effect on the very
        # next reply instead of only at the next Start.
        if self.worker is not None:
            self.worker.fault_indices = self._current_fault_indices()
            self.worker.fault_mode = self._current_fault_mode()

    def _build_response_header_group(self):
        box, layout = titled_group_box("Response Header (90 bytes) - edit any byte individually")

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Byte entry:"))
        self.byte_mode_switch = SegmentedControl("Decimal", "Hex")
        self.byte_mode_switch.setChecked(True)  # Hex by default, matching every other raw-byte view in this app
        self.byte_mode_switch.toggled.connect(self._on_byte_mode_toggled)
        top_row.addWidget(self.byte_mode_switch)
        top_row.addStretch(1)
        layout.addLayout(top_row)

        self.auto_echo_checkbox = QCheckBox(
            f"Auto-echo first {ECHOED_PREFIX_SIZE} bytes from the query (per IDD) - "
            "greyed cells below aren't actually free-form on real hardware"
        )
        self.auto_echo_checkbox.setChecked(True)
        self.auto_echo_checkbox.toggled.connect(self._on_auto_echo_toggled)
        layout.addWidget(self.auto_echo_checkbox)

        layout.addWidget(QLabel(f"Fixed Header ({FIXED_HEADER_SIZE} bytes):"))
        self.fixed_header_grid = ByteGrid(FIXED_HEADER_SIZE, start_index=1)
        self.fixed_header_grid.set_bytes(_MOCK_FIXED_HEADER)
        layout.addWidget(self.fixed_header_grid)

        layout.addWidget(QLabel(f"QCC Header ({QCC_HEADER_SIZE} bytes):"))
        self.qcc_header_grid = ByteGrid(QCC_HEADER_SIZE, start_index=FIXED_HEADER_SIZE + 1)
        self.qcc_header_grid.set_bytes(_MOCK_QCC_HEADER)
        layout.addWidget(self.qcc_header_grid)

        self.fixed_header_grid.bytes_changed.connect(self._on_header_bytes_changed)
        self.qcc_header_grid.bytes_changed.connect(self._on_header_bytes_changed)

        reset_row = QHBoxLayout()
        reset_row.addStretch(1)
        self.reset_header_btn = QPushButton("Reset to Default")
        self.reset_header_btn.clicked.connect(self._on_reset_header_clicked)
        reset_row.addWidget(self.reset_header_btn)
        layout.addLayout(reset_row)

        self._update_echoed_prefix_enabled()

        return box

    def _update_echoed_prefix_enabled(self):
        # Whole Fixed Header (32 bytes) + QCC_COMMAND (byte 33, the QCC
        # Header grid's own byte 0) make up the IDD-mandated echoed prefix.
        auto = self.auto_echo_checkbox.isChecked()
        self.fixed_header_grid.set_cells_enabled(FIXED_HEADER_SIZE, not auto)
        self.qcc_header_grid.set_cells_enabled(1, not auto)

    def _on_auto_echo_toggled(self, *_args):
        self._update_echoed_prefix_enabled()
        if self.worker is not None:
            self.worker.auto_echo_header = self.auto_echo_checkbox.isChecked()

    def _on_byte_mode_toggled(self, is_hex: bool):
        self.fixed_header_grid.set_hex_mode(is_hex)
        self.qcc_header_grid.set_hex_mode(is_hex)

    def _on_reset_header_clicked(self):
        self.fixed_header_grid.set_bytes(_MOCK_FIXED_HEADER)
        self.qcc_header_grid.set_bytes(_MOCK_QCC_HEADER)

    def _on_header_bytes_changed(self):
        # Push edits straight into the already-running worker (if any) so
        # they take effect on the very next reply, instead of only being
        # picked up the next time listening is (re)started.
        if self.worker is not None:
            self.worker.fixed_header = self.fixed_header_grid.get_bytes()
            self.worker.qcc_header = self.qcc_header_grid.get_bytes()

    def _build_listen_group(self):
        box, outer = titled_group_box("Listener")
        row = QHBoxLayout()
        outer.addLayout(row)

        self.port_spin = SpinField(1, 65535, 5000, field_width=64)
        self.port_spin.spin.valueChanged.connect(self._update_endpoint_label)
        self.listen_btn = QPushButton("Start Responding")
        self.listen_btn.clicked.connect(self._on_listen_clicked)
        self.status_label = QLabel("Stopped")
        self.count_label = QLabel("Frames processed: 0")

        # Shown so whoever's using this tool knows exactly what to type
        # into the main GUI's Connection bar - combines the fixed loopback
        # IP with the actual current port in one place (it always binds
        # "0.0.0.0"/every local interface, but since this only makes sense
        # for testing on the same machine as the main GUI, 127.0.0.1/the
        # port shown here is what actually reaches it) - updates live as
        # Listen Port changes, so it never goes stale.
        self.endpoint_label = QLabel()
        self.endpoint_label.setStyleSheet("color: #00adb5; font-weight: 600;")
        self._update_endpoint_label()

        row.addWidget(QLabel("Listen Port:"))
        row.addWidget(self.port_spin)
        row.addWidget(self.listen_btn)
        row.addWidget(self.status_label)
        row.addWidget(self.endpoint_label)
        row.addStretch(1)
        row.addWidget(self.count_label)
        return box

    def _update_endpoint_label(self, *_args):
        port = self.port_spin.value()
        self.endpoint_label.setText(
            f"Responding at 127.0.0.1:{port} (use this as QCC IP + QCC Port in the main GUI)"
        )

    def _build_log_group(self):
        box, layout = titled_group_box("Activity Log")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view)
        return box

    def _on_listen_clicked(self):
        if self.worker is not None:
            self.stop_listening()
        else:
            self.start_listening()

    def start_listening(self):
        if self.worker is not None:
            return
        # Each cell is already constrained to 0-2 valid hex digits by its
        # own validator, so there's nothing that can fail to parse here -
        # an empty cell just reads as 0x00.
        fixed_header = self.fixed_header_grid.get_bytes()
        qcc_header = self.qcc_header_grid.get_bytes()

        port = self.port_spin.value()
        self.worker = ResponderWorker(
            local_port=port, fixed_header=fixed_header, qcc_header=qcc_header,
            fault_indices=self._current_fault_indices(), fault_mode=self._current_fault_mode(),
            auto_echo_header=self.auto_echo_checkbox.isChecked(),
        )
        self.worker.frame_processed.connect(self._on_frame_processed)
        self.worker.error.connect(self._on_error)
        self.worker.status.connect(self.status_label.setText)
        self.worker.start()
        self.listen_btn.setText("Stop Responding")

    def stop_listening(self):
        if self.worker is None:
            return
        self.worker.stop()
        self.worker = None
        self.listen_btn.setText("Start Responding")
        self.status_label.setText("Stopped")

    def set_listen_port(self, port: int):
        """
        Update the Listen Port field, and if currently listening, restart
        the worker on the new port immediately - lets the main GUI keep
        this responder's port in sync with its own QCC Port field (see
        main_window.py's _on_qcc_port_changed) without the user having to
        stop/retype/restart by hand every time they change it.
        """
        if self.port_spin.value() == port:
            return
        self.port_spin.setValue(port)
        if self.worker is not None:
            self.stop_listening()
            self.start_listening()

    def _on_error(self, msg: str):
        self.log_view.appendPlainText(f"[error] {msg}")

    def _on_frame_processed(self, query: bytes, response: bytes, replied: list, addr: tuple):
        self._frame_count += 1
        self.count_label.setText(f"Frames processed: {self._frame_count}")

        host, port = addr
        if not replied:
            self.log_view.appendPlainText(f"From {host}:{port} - no reply-eligible slot found in any of the 96 slots")
            return

        summary = ", ".join(f"QTRM-{i}={name}" for i, name in replied)
        self.log_view.appendPlainText(f"From {host}:{port} - replied to {len(replied)} QTRM(s): {summary}")

    def closeEvent(self, event):
        self.stop_listening()
        super().closeEvent(event)
        self.closed.emit()


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    window = StatusResponderWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
