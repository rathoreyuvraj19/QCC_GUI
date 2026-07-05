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
import sys

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from packet import (
    QTRMSlot, QCCHeaderRx, QTRM_SLOT_SIZE, NUM_QTRM, FIXED_HEADER_SIZE, QCC_HEADER_SIZE,
    TOTAL_PACKET_SIZE, CMD_RESERVED, CMD_SOFT_RESET,
    STATUS_TYPE_ACK, STATUS_TYPE_LINK, STATUS_TYPE_HEALTH,
    STATUS_TYPE_ERR_LOG, STATUS_TYPE_MFG, STATUS_TYPE_DIAGNOSTIC,
    DIAGNOSTIC_TYPE_DETAILED_HEALTH, LINK_SENTINEL,
)

# Command types that never reply, regardless of Status Type bits: Reserved
# (0x00, also what an untouched/zero-filled individual-target slot looks
# like) and Soft Reset (fire-and-forget by design). Every other command
# type replies based on its Status Type alone - see module docstring.
_NO_REPLY_COMMAND_TYPES = (CMD_RESERVED, CMD_SOFT_RESET)
from spin_field import SpinField
from theme import STYLESHEET

_STATUS_TYPE_NAMES = {
    STATUS_TYPE_ACK: "ACK",
    STATUS_TYPE_LINK: "LINK",
    STATUS_TYPE_HEALTH: "HEALTH",
    STATUS_TYPE_ERR_LOG: "TRM Err. Log",
    STATUS_TYPE_MFG: "TRM Mfg. Details",
    STATUS_TYPE_DIAGNOSTIC: "DIAGNOSTIC",
}


def format_hex_bytes(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def parse_hex_bytes(text: str, expected_len: int) -> bytes:
    """Accepts space/comma-separated hex ("AA BB CC" or "AA,BB,CC" or "AABBCC"). Raises ValueError on bad input or wrong length."""
    cleaned = text.replace(",", " ").split()
    if len(cleaned) == 1 and len(cleaned[0]) == expected_len * 2:
        # One unbroken hex string, e.g. "AABBCC..." - split into byte pairs.
        cleaned = [cleaned[0][i:i + 2] for i in range(0, len(cleaned[0]), 2)]
    data = bytes(int(tok, 16) for tok in cleaned)
    if len(data) != expected_len:
        raise ValueError(f"Expected {expected_len} bytes, got {len(data)}.")
    return data

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

# The real app always sends/expects an all-zero 90-byte header for now (see
# packet.py's build_tx_frame docstring), so there's nothing "real" to mirror
# back here - but an all-zero response header makes the RX/TX HeaderPanel
# sidebars look identically blank for every test, which isn't a useful test
# signal. Filled with a fixed (not re-randomized per run - reproducible
# between test sessions), clearly-non-zero pattern instead, so there's
# always something to actually look at, and QCCHeaderRx.to_bytes() computes
# a real, correct CRC-8 so "CHECKSUM: OK" reports truthfully. These are just
# the defaults shown in the window's editable "Response Header" hex fields -
# start_listening() reads whatever's actually in those fields at Start time,
# not these constants directly, so editing them changes every future
# response's header without touching this file.
_MOCK_FIXED_HEADER = bytes((i + 1) & 0xFF for i in range(FIXED_HEADER_SIZE))
_MOCK_QCC_HEADER = QCCHeaderRx(
    msg_id=0x99,
    mode=QCCHeaderRx.MODE_NORMAL,
    command_data=bytes((i + 1) & 0xFF for i in range(55)),
).to_bytes()


def build_mock_response_frame(query_frame: bytes, fixed_header: bytes = None, qcc_header: bytes = None):
    """
    Returns (response_frame, [(qtrm_index, status_type_name), ...] for every
    QTRM that got a reply). fixed_header/qcc_header default to the fixed
    test-pattern bytes below if not given - pass explicit bytes (e.g. from
    the window's editable hex fields) to send a different 90-byte header.
    """
    if fixed_header is None:
        fixed_header = _MOCK_FIXED_HEADER
    if qcc_header is None:
        qcc_header = _MOCK_QCC_HEADER
    base = FIXED_HEADER_SIZE + QCC_HEADER_SIZE
    out = bytearray(fixed_header + qcc_header)
    replied = []
    for i in range(NUM_QTRM):
        slot = query_frame[base + i * QTRM_SLOT_SIZE: base + (i + 1) * QTRM_SLOT_SIZE]
        valid_header = slot[0] == QTRMSlot.HEADER_BYTE
        command_type = slot[2] if valid_header else None
        status_type = slot[3] & 0x0F if valid_header else None
        no_reply = command_type is None or command_type in _NO_REPLY_COMMAND_TYPES
        builder = _REPLY_BUILDERS.get(status_type) if not no_reply else None
        if builder is not None:
            out.extend(builder(i, slot))
            replied.append((i, _STATUS_TYPE_NAMES.get(status_type, str(status_type))))
        else:
            out.extend(bytes(QTRM_SLOT_SIZE))
    return bytes(out), replied


class ResponderWorker(QThread):
    frame_processed = Signal(bytes, bytes, list, tuple)  # query, response, replied, addr
    error = Signal(str)
    status = Signal(str)

    def __init__(self, local_port: int, fixed_header: bytes = None, qcc_header: bytes = None, parent=None):
        super().__init__(parent)
        self.local_port = local_port
        self.fixed_header = fixed_header if fixed_header is not None else _MOCK_FIXED_HEADER
        self.qcc_header = qcc_header if qcc_header is not None else _MOCK_QCC_HEADER
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

            response, replied = build_mock_response_frame(data, self.fixed_header, self.qcc_header)
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


class StatusResponderWindow(QMainWindow):
    closed = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("QCC Status Responder - Mock QTRM")
        self.resize(900, 600)

        self.worker: ResponderWorker | None = None
        self._frame_count = 0

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        root.addWidget(self._build_listen_group())
        root.addWidget(self._build_response_header_group())
        root.addWidget(self._build_log_group(), 1)

    def _build_response_header_group(self):
        box = QGroupBox("Response Header (90 bytes)")
        layout = QVBoxLayout(box)

        fixed_row = QHBoxLayout()
        fixed_row.addWidget(QLabel(f"Fixed Header ({FIXED_HEADER_SIZE} bytes, hex):"))
        self.fixed_header_edit = QLineEdit(format_hex_bytes(_MOCK_FIXED_HEADER))
        fixed_row.addWidget(self.fixed_header_edit, 1)
        layout.addLayout(fixed_row)

        qcc_row = QHBoxLayout()
        qcc_row.addWidget(QLabel(f"QCC Header ({QCC_HEADER_SIZE} bytes, hex):"))
        self.qcc_header_edit = QLineEdit(format_hex_bytes(_MOCK_QCC_HEADER))
        qcc_row.addWidget(self.qcc_header_edit, 1)
        layout.addLayout(qcc_row)

        reset_row = QHBoxLayout()
        reset_row.addStretch(1)
        self.reset_header_btn = QPushButton("Reset to Default")
        self.reset_header_btn.clicked.connect(self._on_reset_header_clicked)
        reset_row.addWidget(self.reset_header_btn)
        layout.addLayout(reset_row)

        return box

    def _on_reset_header_clicked(self):
        self.fixed_header_edit.setText(format_hex_bytes(_MOCK_FIXED_HEADER))
        self.qcc_header_edit.setText(format_hex_bytes(_MOCK_QCC_HEADER))

    def _build_listen_group(self):
        box = QGroupBox("Listener")
        row = QHBoxLayout(box)

        self.port_spin = SpinField(1, 65535, 5000, field_width=64)
        self.listen_btn = QPushButton("Start Responding")
        self.listen_btn.clicked.connect(self._on_listen_clicked)
        self.status_label = QLabel("Stopped")
        self.count_label = QLabel("Frames processed: 0")

        # Shown so whoever's using this tool knows what to type into the
        # main GUI's "QCC IP" field - it always binds "0.0.0.0" (every
        # local interface), but since this only makes sense for testing on
        # the same machine as the main GUI, 127.0.0.1 (loopback) is the
        # address that actually reaches it.
        ip_label = QLabel("Listen IP: 127.0.0.1 (use this as QCC IP in the main GUI)")
        ip_label.setStyleSheet("color: #00adb5; font-weight: 600;")

        row.addWidget(QLabel("Listen Port:"))
        row.addWidget(self.port_spin)
        row.addWidget(self.listen_btn)
        row.addWidget(self.status_label)
        row.addWidget(ip_label)
        row.addStretch(1)
        row.addWidget(self.count_label)
        return box

    def _build_log_group(self):
        box = QGroupBox("Activity Log")
        layout = QVBoxLayout(box)
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
        try:
            fixed_header = parse_hex_bytes(self.fixed_header_edit.text(), FIXED_HEADER_SIZE)
            qcc_header = parse_hex_bytes(self.qcc_header_edit.text(), QCC_HEADER_SIZE)
        except ValueError as e:
            QMessageBox.warning(self, "Invalid Response Header", f"Could not parse header hex: {e}")
            return

        port = self.port_spin.value()
        self.worker = ResponderWorker(local_port=port, fixed_header=fixed_header, qcc_header=qcc_header)
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
