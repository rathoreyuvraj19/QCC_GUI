"""
remote_prog_tester_app.py

Standalone test responder - simulates bootloader-side responses to Remote
Programming tab operations (Mode Step 1, Mode Step 2, LRU Info, Authenticate,
Program, Verify), so the main GUI's firmware-update flow can be tested
end-to-end without real bootloader hardware.

Unlike status_responder_app.py (which simulates real-time QTRM slot responses
per-frame), this simulates the fixed bootloader command/response sequence:
each operation has a known request frame and expected response(s). The tester
acknowledges receipt and simulates reasonable progress (acks for each chunk
during Program, success/fail status for Authenticate/Verify).

Run directly:  python remote_prog_tester_app.py
"""

import socket
import struct
import sys

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel,
    QMainWindow, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

import apps.bootloader_packet as bl
from core.packet import (
    NUM_QTRM, RP_FRAME_SIZE, RP_PAYLOAD_SIZE, RP_INNER_CMD_SIZE,
    FIXED_HEADER_SIZE, QCC_HEADER_SIZE, crc8,
    QTRM_SLOT_SIZE, TOTAL_PACKET_SIZE,
)

from widgets.segmented_control import SegmentedControl
from widgets.spin_field import SpinField
from widgets.titled_group import titled_group_box
from theme import STYLESHEET


class MockBootloaderResponder(QThread):
    frame_processed = Signal(bytes, bytes, str)  # query, response, description
    error = Signal(str)
    status = Signal(str)

    def __init__(self, local_port: int, parent=None):
        super().__init__(parent)
        self.local_port = local_port
        self._sock = None
        self._running = False
        self._simulate_failures = False
        self._chunk_ack_delay_pct = 0  # 0-100% of QTRMs delay each chunk ack

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

            response, desc = self._build_response(data)
            if response is None:
                continue

            try:
                self._sock.sendto(response, addr)
            except OSError as e:
                self.error.emit(f"Failed to send response: {e}")
                continue
            self.frame_processed.emit(data, response, desc)

        if self._sock:
            self._sock.close()
        self.status.emit("Stopped")

    def _build_response(self, query: bytes) -> tuple:
        """
        Returns (response_bytes, description_string) for a bootloader query,
        or (None, "") if the frame doesn't match a known sequence.

        Two TX frame shapes (per Yuvraj 2026-07-16 - see
        core/packet.py's build_broadcast_bootloader_frame docstring):
        1. Mode Step 1 (2970 bytes): 90-byte header + 96 x 30-byte QTRM
           slots, each slot's first 10 bytes carrying the SAME bootloader
           mode-change command (QTRMs are still individually addressed at
           this point, not yet in the QCC's shared low-speed FIFO).
        2. Every other RP operation (4196 bytes): 90-byte header + 4096-byte
           payload + one 10-byte inner bootloader command, broadcast by the
           QCC to all QTRMs once they're already in low-speed mode.

        Mode Step 2 (QCC -> Low-Speed) is a separate, not-yet-confirmed wire
        format - deliberately NOT guessed here; a 2970-byte frame whose
        slot 0 doesn't decode as a Mode Change command falls through to the
        unrecognized case below rather than being misidentified.
        """
        if len(query) == TOTAL_PACKET_SIZE:
            query_header = query[:FIXED_HEADER_SIZE + QCC_HEADER_SIZE]
            base = FIXED_HEADER_SIZE + QCC_HEADER_SIZE
            slot0_cmd = query[base: base + RP_INNER_CMD_SIZE]
            if len(slot0_cmd) == RP_INNER_CMD_SIZE and slot0_cmd[2] == bl.CT_MODE_CHANGE:
                bsn_mode = slot0_cmd[5] & 0x0F if len(slot0_cmd) > 5 else 0
                mode_name = {
                    0: "INITIALISATION", 1: "OPERATION",
                    2: "MAINTENANCE", 3: "MSS_CONTROL",
                }.get(bsn_mode, f"UNKNOWN({bsn_mode})")
                return self._respond_mode_change(query_header), f"Mode Step 1 ({mode_name})"
            # Unrecognized 2970-byte frame (e.g. Mode Step 2, format TBD) - ignore
            return None, ""

        if len(query) != RP_FRAME_SIZE:
            return None, ""

        try:
            # Extract the header and inner command from the RP frame
            query_header = query[:FIXED_HEADER_SIZE + QCC_HEADER_SIZE]
            inner_cmd = query[FIXED_HEADER_SIZE + QCC_HEADER_SIZE + RP_PAYLOAD_SIZE:
                              FIXED_HEADER_SIZE + QCC_HEADER_SIZE + RP_PAYLOAD_SIZE + RP_INNER_CMD_SIZE]

            if len(inner_cmd) != RP_INNER_CMD_SIZE:
                return None, ""

            cmd_type = inner_cmd[2]  # byte 3 (0-indexed) is command type

            # Try to parse as known commands (see bootloader_packet.py)
            # Mode Change Command (0x32)
            if cmd_type == bl.CT_MODE_CHANGE:
                bsn_mode = inner_cmd[5] & 0x0F if len(inner_cmd) > 5 else 0
                mode_name = {
                    0: "INITIALISATION",
                    1: "OPERATION",
                    2: "MAINTENANCE",
                    3: "MSS_CONTROL"
                }.get(bsn_mode, f"UNKNOWN({bsn_mode})")
                return self._respond_mode_change(query_header), f"Mode Change ({mode_name})"

            # LRU Status Query (0x31)
            elif cmd_type == bl.CT_LRU_STATUS:
                return self._respond_lru_info(query_header), "LRU Info"

            # Firmware Update Command (0x36) - Authenticate/Program/Verify
            elif cmd_type == bl.CT_FW_UPDATE_CMD:
                iap_mode = inner_cmd[5] if len(inner_cmd) > 5 else 0
                mode_name = bl.IAP_MODE_NAMES.get(iap_mode, f"UNKNOWN({iap_mode})")
                return self._respond_firmware_update(query_header, iap_mode), f"Firmware Update ({mode_name})"

            # Bitstream Data Packet (0x34) - Program chunk streaming
            elif cmd_type == bl.CT_BITSTREAM_DATA:
                chunk_idx = inner_cmd[8] | (inner_cmd[9] << 8) if len(inner_cmd) > 9 else 0
                return self._respond_bitstream_ack(query_header, chunk_idx), f"Bitstream Data (chunk {chunk_idx})"

            # Link Request (0x30)
            elif cmd_type == bl.CT_LINK:
                return self._respond_link(query_header), "Link Request"

            else:
                # Unknown command type, silently ignore
                return None, ""

        except IndexError as e:
            self.error.emit(f"Failed to parse: index error at frame size {len(query)} - {str(e)}")
            return None, ""
        except Exception as e:
            self.error.emit(f"Failed to parse bootloader command: {type(e).__name__}: {str(e)}")
            return None, ""

    def _build_response_frame(self, query_header: bytes, build_slots_fn) -> bytes:
        """
        Build a 2970-byte response frame by echoing the query header and
        filling QTRM slots using build_slots_fn (a callable that returns
        the 30-byte slot data for each QTRM index).
        """
        # Start with query header, swap source/destination IDs
        header = bytearray(query_header)
        header[0], header[1] = query_header[1], query_header[0]

        # Build response frame
        resp_body = bytearray(header)

        # Fill each of 96 QTRM slots
        for i in range(NUM_QTRM):
            slot_data = build_slots_fn(i)
            resp_body.extend(slot_data)

        # Recalculate header checksum (last byte of header)
        header_end = FIXED_HEADER_SIZE + QCC_HEADER_SIZE
        resp_body[header_end - 1] = crc8(resp_body[:header_end - 1])

        return bytes(resp_body)

    def _respond_mode_change(self, query_header: bytes) -> bytes:
        """Mode Change Command acknowledgment."""
        def build_slot(i: int) -> bytes:
            slot = bytearray(QTRM_SLOT_SIZE)
            slot[0] = bl.BL_HEADER
            slot[1] = bl.PSI_FIXED
            slot[2] = bl.CT_MODE_CHANGE  # Echo the command type
            slot[3] = 0x00  # Status byte (success)
            # Checksum for first 10 bytes
            slot[9] = bl.bootloader_checksum(slot[:9])
            # Remaining 20 bytes are reserved/unused
            return bytes(slot)

        return self._build_response_frame(query_header, build_slot)

    def _respond_lru_info(self, query_header: bytes) -> bytes:
        """
        LRU Info response with plausible firmware version info.

        Byte layout confirmed 2026-07-16 against firmware's
        LRU_info_response_type_def (user_functions.h): byte index 5 packs
        (mfg_id<<4)|part_no into ONE byte - there is no separate lm_id byte,
        and index 4 is msg_counter, not part of the payload.
        """
        def build_slot(i: int) -> bytes:
            slot = bytearray(QTRM_SLOT_SIZE)
            slot[0] = bl.BL_HEADER
            slot[1] = bl.PSI_FIXED
            slot[2] = bl.CT_LRU_STATUS  # Response command type
            slot[3] = 0x00  # Status byte (success)
            slot[4] = 0x00  # msg_counter (not modeled)
            mfg_id, part_no = 0x02, 0x01
            slot[5] = ((mfg_id & 0x0F) << 4) | (part_no & 0x0F)
            # Serial number (little-endian u16)
            serial = 1000 + i
            slot[6] = serial & 0xFF
            slot[7] = (serial >> 8) & 0xFF
            slot[8] = 0x02  # FW version
            slot[9] = bl.bootloader_checksum(slot[:9])
            # Remaining 20 bytes are reserved/unused
            return bytes(slot)

        return self._build_response_frame(query_header, build_slot)

    def _respond_firmware_update(self, query_header: bytes, iap_mode: int) -> bytes:
        """Firmware Update Command response (Authenticate/Program/Verify)."""
        def build_slot(i: int) -> bytes:
            slot = bytearray(QTRM_SLOT_SIZE)
            slot[0] = bl.BL_HEADER
            slot[1] = bl.PSI_FIXED
            slot[2] = bl.CT_FW_UPDATE_OR_BS_ACK  # Response command type
            slot[3] = 0x00  # Status byte
            slot[5] = iap_mode  # Echo IAP mode
            # IAP status code (0=success)
            if self._simulate_failures and i % 10 == 0:
                slot[6] = 0x01  # CHAINING_MISMATCH (simulated failure)
            else:
                slot[6] = 0x00  # SUCCESS
            slot[9] = bl.bootloader_checksum(slot[:9])
            # Remaining 20 bytes are reserved/unused
            return bytes(slot)

        return self._build_response_frame(query_header, build_slot)

    def _respond_bitstream_ack(self, query_header: bytes, chunk_index: int) -> bytes:
        """Bitstream Data Packet acknowledgment."""
        def build_slot(i: int) -> bytes:
            slot = bytearray(QTRM_SLOT_SIZE)
            slot[0] = bl.BL_HEADER
            slot[1] = bl.PSI_FIXED
            slot[2] = bl.CT_FW_UPDATE_OR_BS_ACK  # Dual-use command type
            slot[3] = 0x00  # Status byte
            # ith_packet (little-endian u16) at bytes 5-6
            slot[5] = chunk_index & 0xFF
            slot[6] = (chunk_index >> 8) & 0xFF
            # pass_fail byte (bit 0 = success, bit 1 = failure)
            if self._simulate_failures and i % 20 == 0:
                slot[7] = bl.BS_ACK_TRANSFER_FAILED_BIT
            else:
                slot[7] = bl.BS_ACK_TRANSFER_SUCCESSFUL_BIT
            slot[9] = bl.bootloader_checksum(slot[:9])
            # Remaining 20 bytes are reserved/unused
            return bytes(slot)

        return self._build_response_frame(query_header, build_slot)

    def _respond_link(self, query_header: bytes) -> bytes:
        """Link Request acknowledgment (MSS Link Response)."""
        def build_slot(i: int) -> bytes:
            slot = bytearray(QTRM_SLOT_SIZE)
            slot[0] = bl.BL_HEADER
            slot[1] = bl.PSI_FIXED
            slot[2] = bl.CT_LINK  # Response command type
            slot[3] = 0x00  # Status byte
            slot[5:9] = b'\xB1\xB2\xB3\xB4'  # Per spec (open item 5)
            slot[9] = bl.bootloader_checksum(slot[:9])
            # Remaining 20 bytes are reserved/unused
            return bytes(slot)

        return self._build_response_frame(query_header, build_slot)

    def stop(self):
        self._running = False
        self.wait(2000)


class RemoteProgTesterWindow(QMainWindow):
    closed = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("QCC Remote Programming Tester - Mock Bootloader")
        self.setMinimumSize(600, 400)
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        width = 900 if avail is None else min(900, avail.width() - 40)
        height = 600 if avail is None else min(600, avail.height() - 60)
        self.resize(width, height)

        self.worker: MockBootloaderResponder | None = None
        self._frame_count = 0

        central = QWidget()
        root = QVBoxLayout(central)

        root.addWidget(self._build_listen_group())
        root.addWidget(self._build_options_group())
        root.addWidget(self._build_log_group(), 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(central)
        self.setCentralWidget(scroll)

    def _build_listen_group(self):
        box, outer = titled_group_box("Listener")
        row = QHBoxLayout()
        outer.addLayout(row)

        self.port_spin = SpinField(1, 65535, 5000, field_width=64)
        self.listen_btn = QPushButton("Start Listening")
        self.listen_btn.clicked.connect(self._on_listen_clicked)
        self.status_label = QLabel("Stopped")
        self.count_label = QLabel("Frames processed: 0")

        self.endpoint_label = QLabel()
        self.endpoint_label.setStyleSheet("color: #00adb5; font-weight: 600;")
        self.port_spin.spin.valueChanged.connect(self._update_endpoint_label)
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

    def _build_options_group(self):
        box, outer = titled_group_box("Mock Behavior Options")
        row = QHBoxLayout()
        outer.addLayout(row)

        self.failure_checkbox = QCheckBox("Simulate random failures (some QTRMs fail auth/verify)")
        self.failure_checkbox.setChecked(False)
        row.addWidget(self.failure_checkbox)

        row.addWidget(QLabel("Chunk ACK delay:"))
        self.chunk_delay_spin = SpinField(0, 100, 0, field_width=50)
        self.chunk_delay_spin.spin.setValue(0)
        row.addWidget(self.chunk_delay_spin)
        row.addWidget(QLabel("% of QTRMs"))

        row.addStretch(1)
        return box

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

        port = self.port_spin.value()
        self.worker = MockBootloaderResponder(local_port=port)
        self.worker.frame_processed.connect(self._on_frame_processed)
        self.worker.error.connect(self._on_error)
        self.worker.status.connect(self.status_label.setText)
        self.worker.start()
        self.listen_btn.setText("Stop Listening")

    def stop_listening(self):
        if self.worker is None:
            return
        self.worker.stop()
        self.worker = None
        self.listen_btn.setText("Start Listening")
        self.status_label.setText("Stopped")

    def set_listen_port(self, port: int):
        """
        Update the Listen Port field, and if currently listening, restart
        the worker on the new port immediately.
        """
        if self.port_spin.value() == port:
            return
        self.port_spin.setValue(port)
        if self.worker is not None:
            self.stop_listening()
            self.start_listening()

    def _on_error(self, msg: str):
        self.log_view.appendPlainText(f"[error] {msg}")

    def _on_frame_processed(self, query: bytes, response: bytes, desc: str):
        self._frame_count += 1
        self.count_label.setText(f"Frames processed: {self._frame_count}")
        self.log_view.appendPlainText(f"[{self._frame_count}] {desc} - {len(response)} bytes")

    def closeEvent(self, event):
        self.stop_listening()
        super().closeEvent(event)
        self.closed.emit()


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    window = RemoteProgTesterWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
