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
from PySide6.QtGui import QFont, QFontMetrics, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QGridLayout,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

import apps.bootloader_packet as bl
from apps.remote_prog_controller import QCC_BODY_SWITCH_HIGH_SPEED, QCC_BODY_SWITCH_LOW_SPEED
from core.packet import (
    NUM_QTRM, RP_CMD_FRAME_SIZE, RP_FRAME_SIZE, RP_PAYLOAD_SIZE, RP_INNER_CMD_SIZE,
    RP_QCC_LEVEL_FRAME_SIZE, RP_QTRM_SELECT_BROADCAST,
    FIXED_HEADER_SIZE, QCC_HEADER_SIZE, crc8,
    QTRM_SLOT_SIZE, TOTAL_PACKET_SIZE,
)
from core.rc_settings import COMMAND_ID_REMOTE_PROGRAMMING

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
        # Mirror of the real QCC's remote-programming LRU-select mux:
        # latched from byte 35 (QTRM_SELECT) of every SubCommand 0x01/0x02
        # frame. 0xFF = all 96 QTRMs; 0-95 = only that QTRM sees the
        # low-speed traffic, so responses carry its slot alone and the
        # other 95 slots are zero-filled (as the real QCC does).
        self._lru_select = RP_QTRM_SELECT_BROADCAST

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

        Four TX frame shapes (per Yuvraj - see core/packet.py's
        RP_CMD_FRAME_SIZE comment):
        1. Mode Step 1 (2970 bytes): 90-byte header + 96 x 30-byte QTRM
           slots, each slot's first 10 bytes carrying the SAME bootloader
           mode-change command (QTRMs are still individually addressed at
           this point, not yet in the QCC's shared low-speed FIFO).
        2. Mode Step 2 / QCC -> High Speed (90 bytes): bare header, no
           inner command, no payload - RE-DECIDED 2026-07-19, both are
           QCC's own self-directed UART switch,
           distinguished only by the header's byte 34 SubCommand
           (QCC_BODY_SWITCH_LOW_SPEED=0x01 vs QCC_BODY_SWITCH_HIGH_SPEED=0x02;
           0x00 is reserved for the Broadcast SubCommand used by shapes 3/4).
        3. Every QTRM-targeted command once QTRMs+QCC are in low-speed mode
           (100 bytes): 90-byte header + 10-byte inner bootloader command,
           NO payload (RE-DECIDED 2026-07-19 - previously these were sent
           as 4196-byte frames zero-padded out to the full payload size).
        4. Bitstream DATA chunks ONLY (4196 bytes): 90-byte header + 10-byte
           inner command + 4096-byte payload (command BEFORE payload -
           order confirmed 2026-07-18) - the real file-transfer payload.
        """
        if len(query) == TOTAL_PACKET_SIZE:
            query_header = query[:FIXED_HEADER_SIZE + QCC_HEADER_SIZE]
            base = FIXED_HEADER_SIZE + QCC_HEADER_SIZE
            # QTRMs are per-slot addressed here - only the slots that
            # actually carry a Mode Change command respond (a single-QTRM
            # Mode Step 1 fills one slot and leaves the other 95 all-zero,
            # so slot 0 alone is NOT a reliable place to look).
            addressed = []
            bsn_mode = 0
            for i in range(NUM_QTRM):
                slot_cmd = query[base + i * QTRM_SLOT_SIZE:
                                 base + i * QTRM_SLOT_SIZE + RP_INNER_CMD_SIZE]
                if (len(slot_cmd) == RP_INNER_CMD_SIZE
                        and slot_cmd[0] == bl.BL_HEADER
                        and slot_cmd[2] == bl.CT_MODE_CHANGE):
                    addressed.append(i)
                    bsn_mode = slot_cmd[5] & 0x0F
            if addressed:
                mode_name = {
                    0: "INITIALISATION", 1: "OPERATION",
                    2: "MAINTENANCE", 3: "MSS_CONTROL",
                }.get(bsn_mode, f"UNKNOWN({bsn_mode})")
                scope = ("all 96 QTRMs" if len(addressed) == NUM_QTRM
                         else f"QTRM {addressed[0]} only" if len(addressed) == 1
                         else f"{len(addressed)} QTRMs")
                return (self._respond_mode_change(query_header, addressed),
                        f"Mode Step 1 ({mode_name}, {scope})")
            # Unrecognized 2970-byte frame - ignore
            return None, ""

        if len(query) == RP_QCC_LEVEL_FRAME_SIZE:
            # Mode Step 2 / QCC -> High Speed (bare 90-byte QCC-level
            # frame) - qcc_command byte (header index 32) must be
            # REMOTE_PROGRAMMING; byte 34 (index 33) is the SubCommand
            # that says which direction.
            if query[32] != COMMAND_ID_REMOTE_PROGRAMMING:
                return None, ""
            sub_cmd = query[33] if len(query) > 33 else None
            if sub_cmd in (QCC_BODY_SWITCH_LOW_SPEED, QCC_BODY_SWITCH_HIGH_SPEED):
                # Byte 35 (index 34) is QTRM_SELECT - latch it exactly as
                # the real QCC latches its LRU-select mux, so every
                # subsequent SubCommand 0x00 response carries only the
                # selected QTRM's slot (0xFF = all 96).
                self._lru_select = query[34] if len(query) > 34 else RP_QTRM_SELECT_BROADCAST
                target = ("all 96 QTRMs" if self._lru_select == RP_QTRM_SELECT_BROADCAST
                          else f"QTRM {self._lru_select} only")
                direction = ("Mode Step 2 (QCC self low-speed, SubCommand 0x01"
                             if sub_cmd == QCC_BODY_SWITCH_LOW_SPEED
                             else "QCC self mode change -> high-speed (SubCommand 0x02")
                return self._respond_qcc_level(query), f"{direction}, target {target})"
            return None, ""

        if len(query) not in (RP_CMD_FRAME_SIZE, RP_FRAME_SIZE):
            return None, ""

        try:
            # Extract the header and inner command - same offsets whether
            # or not a payload follows (order confirmed 2026-07-18: QCC
            # forwards the command before any payload).
            header_size = FIXED_HEADER_SIZE + QCC_HEADER_SIZE
            query_header = query[:header_size]
            inner_cmd = query[header_size: header_size + RP_INNER_CMD_SIZE]

            if len(inner_cmd) != RP_INNER_CMD_SIZE:
                return None, ""

            cmd_type = inner_cmd[2]  # byte 3 (0-indexed) is command type
            has_payload = len(query) == RP_FRAME_SIZE

            # Bitstream Data (0x34) is the ONLY command that should ever
            # carry the 4096-byte payload; every other command type is only
            # valid in the 100-byte (no-payload) shape.
            if has_payload and cmd_type != bl.CT_BITSTREAM_DATA:
                return None, ""
            if not has_payload and cmd_type == bl.CT_BITSTREAM_DATA:
                return None, ""

            # Try to parse as known commands (see bootloader_packet.py)
            # Bitstream Receive announce (0x33 post-MSS): firmware enters
            # recieve_bit_stream() silently - NO ack for the announce itself
            # (the first chunk's ack is the first reply the GUI sees).
            if cmd_type == bl.CT_BITSTREAM_RECEIVE:
                golden = inner_cmd[3] if len(inner_cmd) > 3 else 0
                count = (inner_cmd[7] | (inner_cmd[8] << 8)) if len(inner_cmd) > 8 else 0
                self.status.emit(
                    f"Bitstream Receive announce ({'GOLDEN' if golden else 'CURRENT'}, "
                    f"{count} chunks) — no ack, as per firmware")
                return None, ""

            # Mode Change MSS->Fabric (0x32): firmware's handler only
            # toggles GPIOs and exits the request loop - no UART reply.
            # Sent by the "QTRM -> High Speed" button
            # (remote_prog_controller.py's start_qtrm_high_speed()) via the
            # normal SubCommand 0x00 broadcast path - QCC -> High Speed
            # (start_mode_back()) is the separate QCC-level switch, not this.
            elif cmd_type == bl.CT_MODE_CHANGE_MSS_TO_FAB:
                self.status.emit("Mode Change MSS->Fabric (0x32) — no reply, as per firmware")
                return None, ""

            # LRU Status Query (0x31)
            elif cmd_type == bl.CT_LRU_STATUS:
                return self._respond_lru_info(query_header), "LRU Info"

            # Firmware Update Command (0x36) - Authenticate/Program/Verify
            elif cmd_type == bl.CT_FW_UPDATE_CMD:
                iap_mode = inner_cmd[5] if len(inner_cmd) > 5 else 0
                golden = inner_cmd[3] if len(inner_cmd) > 3 else 0
                mode_name = bl.IAP_MODE_NAMES.get(iap_mode, f"UNKNOWN({iap_mode})")
                scope = "GOLDEN" if golden else "CURRENT"
                return (self._respond_firmware_update(query_header, iap_mode),
                        f"Firmware Update ({mode_name}, {scope})")

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

    def _respond_qcc_level(self, query: bytes) -> bytes:
        """
        Bare 90-byte QCC-level response for Mode Step 2 / Return to High
        Speed (RE-DECIDED 2026-07-19) - just echo the header back with
        source/destination swapped and the checksum recomputed, same as
        every other response, but with no QTRM data block at all.
        """
        header = bytearray(query[:RP_QCC_LEVEL_FRAME_SIZE])
        header[0], header[1] = query[1], query[0]
        header[RP_QCC_LEVEL_FRAME_SIZE - 1] = crc8(bytes(header[:RP_QCC_LEVEL_FRAME_SIZE - 1]))
        return bytes(header)

    def _build_response_frame(self, query_header: bytes, build_slots_fn,
                              respond_slots=None) -> bytes:
        """
        Build a 2970-byte response frame by echoing the query header and
        filling QTRM slots using build_slots_fn (a callable that returns
        the 30-byte slot data for each QTRM index).

        respond_slots limits which slots are populated: None derives it
        from the latched LRU-select mux (SubCommand 0x00 traffic reaches
        only the selected QTRM, so only its slot answers), an explicit
        iterable overrides that (Mode Step 1's per-slot addressing). Every
        non-responding slot is zero-filled, as the real QCC does.
        """
        if respond_slots is None:
            if self._lru_select == RP_QTRM_SELECT_BROADCAST:
                respond_slots = range(NUM_QTRM)
            else:
                respond_slots = [self._lru_select] if self._lru_select < NUM_QTRM else []
        responding = set(respond_slots)

        # Start with query header, swap source/destination IDs
        header = bytearray(query_header)
        header[0], header[1] = query_header[1], query_header[0]

        # Build response frame
        resp_body = bytearray(header)

        # Fill each of 96 QTRM slots (zeros for non-responding QTRMs)
        for i in range(NUM_QTRM):
            if i in responding:
                resp_body.extend(build_slots_fn(i))
            else:
                resp_body.extend(bytes(QTRM_SLOT_SIZE))

        # Recalculate header checksum (last byte of header)
        header_end = FIXED_HEADER_SIZE + QCC_HEADER_SIZE
        resp_body[header_end - 1] = crc8(resp_body[:header_end - 1])

        return bytes(resp_body)

    def _respond_mode_change(self, query_header: bytes, addressed) -> bytes:
        """Mode Change Command acknowledgment - only the slots that were
        actually addressed in the query answer (per-slot addressing, the
        LRU-select mux plays no part in Mode Step 1)."""
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

        return self._build_response_frame(query_header, build_slot,
                                          respond_slots=addressed)

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
        """
        MSS Link Response, firmware-accurate: sendLinkRes() emits
        AA 00 34 00 00 B1 B2 B3 B4 <xor-checksum> - the response's
        command_type is 0x34 (NOT echoing the 0x30 request; see
        bootloader_packet.py's CT_LINK_RESPONSE).
        """
        def build_slot(i: int) -> bytes:
            slot = bytearray(QTRM_SLOT_SIZE)
            slot[0] = bl.BL_HEADER
            slot[1] = bl.PSI_FIXED
            slot[2] = bl.CT_LINK_RESPONSE  # 0x34, as the real firmware sends
            slot[3] = 0x00  # Status byte
            slot[5:9] = b'\xB1\xB2\xB3\xB4'
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
        self._selected_query: bytes | None = None

        central = QWidget()
        root = QVBoxLayout(central)

        root.addWidget(self._build_listen_group())
        root.addWidget(self._build_options_group())

        columns = QHBoxLayout()
        columns.addWidget(self._build_log_group(), 0)
        columns.addWidget(self._build_header_group(), 0)
        columns.addWidget(self._build_analysis_group(), 1)
        root.addLayout(columns, 1)

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
        box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.log_view = QListWidget()
        # Width follows the longest logged line instead of stretching to
        # share space equally with the analysis column - the log entries
        # are short, so the payload view should get the leftover room.
        self.log_view.setSizeAdjustPolicy(QAbstractItemView.AdjustToContents)
        self.log_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.log_view.currentItemChanged.connect(self._on_log_item_selected)
        layout.addWidget(self.log_view)
        return box

    def _build_header_group(self):
        box, layout = titled_group_box("QCC Header (90 bytes)")
        box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.header_view = QPlainTextEdit()
        self.header_view.setReadOnly(True)
        self.header_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.header_view.setFont(QFont("Consolas", 9))
        self.header_view.setStyleSheet('font-family: "Consolas", "Courier New", monospace;')
        self.header_view.setPlainText("(waiting for a frame)")
        layout.addWidget(self.header_view)
        return box

    def _build_analysis_group(self):
        box, layout = titled_group_box("Sent Packet Analysis (click a packet in the log)")
        box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.analysis_view = QPlainTextEdit()
        self.analysis_view.setReadOnly(True)
        self.analysis_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.analysis_view.setFont(QFont("Consolas", 9))
        # theme.py's app-wide QSS sets font-family on the base QWidget
        # selector, which in Qt's stylesheet cascade silently overrides a
        # plain setFont() call - without this widget-level rule the byte
        # columns above render in the UI's proportional sans-serif font
        # and drift out of alignment despite being padded to an exact
        # character count.
        self.analysis_view.setStyleSheet('font-family: "Consolas", "Courier New", monospace;')
        self.analysis_view.setPlainText("(waiting for a frame)")
        layout.addWidget(self.analysis_view)
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

        # Query size, not response size - the response is always a fixed
        # 2970 bytes, but the query varies per the 2026-07-19 framing change
        # (100 bytes for most commands, 4196 only for bitstream DATA
        # chunks), which is the thing this tester exists to verify.
        item = QListWidgetItem(f"[{self._frame_count}] {desc} - {len(query)} bytes")
        item.setData(Qt.UserRole, query)  # keep the raw frame for later inspection
        self.log_view.addItem(item)
        self.log_view.setCurrentItem(item)  # selects it -> shows this frame's breakdown
        self.log_view.scrollToBottom()

    def _on_log_item_selected(self, current: QListWidgetItem, _previous: QListWidgetItem):
        if current is None:
            return
        self._selected_query = current.data(Qt.UserRole)
        self._render_analysis(self._selected_query)

    def _render_analysis(self, query: bytes):
        header_size = FIXED_HEADER_SIZE + QCC_HEADER_SIZE
        header_bytes = query[:header_size]

        header_lines = [f"  byte {i + 1:2d}: 0x{b:02X} ({b})" for i, b in enumerate(header_bytes)]
        self.header_view.setPlainText(self._paginate_columns(self.header_view, header_lines))

        body = query[header_size:]

        if len(query) == RP_FRAME_SIZE:
            # Broadcast RP command+payload frame: 10-byte inner command
            # then the 4096-byte payload.
            inner_cmd = body[:RP_INNER_CMD_SIZE]
            payload = body[RP_INNER_CMD_SIZE:]

            lines = ["Command (byte 1-10):"]
            for i, b in enumerate(inner_cmd, start=1):
                lines.append(f"  byte {i:2d}: 0x{b:02X} ({b})")
            lines.append("")
            lines.append(f"Payload (byte 0-{len(payload) - 1}):")

            payload_lines = [f"  byte {i:4d}: 0x{b:02X} ({b})" for i, b in enumerate(payload)]
            self.analysis_view.setPlainText(
                "\n".join(lines) + "\n" + self._paginate_columns(self.analysis_view, payload_lines))

        elif len(query) == RP_CMD_FRAME_SIZE:
            # Every RP command except bitstream DATA chunks (decided
            # 2026-07-19): 10-byte inner command only, no payload - this is
            # exactly what gets broadcast to all 96 QTRMs.
            inner_cmd = body[:RP_INNER_CMD_SIZE]
            lines = ["Command (byte 1-10, no payload):"]
            for i, b in enumerate(inner_cmd, start=1):
                lines.append(f"  byte {i:2d}: 0x{b:02X} ({b})")
            self.analysis_view.setPlainText("\n".join(lines))

        elif len(query) == RP_QCC_LEVEL_FRAME_SIZE:
            # Mode Step 2 / QCC -> High Speed: bare header only, no inner
            # command, no payload - QCC's own self-directed UART switch.
            # The SubCommand at byte 34 is the
            # only thing distinguishing which direction - it lives INSIDE
            # the 90-byte header (offset 33), not in `body` (which is empty
            # here since the frame ends exactly at header_size).
            sub_cmd = query[33] if len(query) > 33 else None
            direction = {
                QCC_BODY_SWITCH_LOW_SPEED: "switch to LOW-SPEED (Mode Step 2)",
                QCC_BODY_SWITCH_HIGH_SPEED: "switch to HIGH-SPEED (QCC -> High Speed)",
            }.get(sub_cmd, f"UNKNOWN sub-command 0x{sub_cmd:02X}" if sub_cmd is not None else "(missing)")
            qtrm_select = query[34] if len(query) > 34 else None
            if qtrm_select is None:
                target = "(missing)"
            elif qtrm_select == RP_QTRM_SELECT_BROADCAST:
                target = "0xFF - broadcast, all 96 QTRMs"
            elif qtrm_select < NUM_QTRM:
                target = f"0x{qtrm_select:02X} - QTRM {qtrm_select} only"
            else:
                target = f"0x{qtrm_select:02X} - INVALID (expected 0x00-0x5F or 0xFF)"
            self.analysis_view.setPlainText(
                "QCC-level command only (no inner QTRM command, no payload).\n"
                f"SubCommand (byte 34): {direction}\n"
                f"QTRM_SELECT (byte 35): {target}")

        elif len(query) == TOTAL_PACKET_SIZE:
            # Mode Step 1 / per-QTRM broadcast frame: 96 individually-
            # addressed 30-byte QTRM slots after the header.
            lines = [f"QTRM Slots (byte {header_size}-{len(query) - 1} of frame, "
                     f"{NUM_QTRM} x {QTRM_SLOT_SIZE} bytes):"]
            slot_lines = []
            for s in range(NUM_QTRM):
                slot = body[s * QTRM_SLOT_SIZE:(s + 1) * QTRM_SLOT_SIZE]
                for i, b in enumerate(slot):
                    slot_lines.append(f"  QTRM{s:2d} byte {i:2d}: 0x{b:02X} ({b})")
            self.analysis_view.setPlainText(
                "\n".join(lines) + "\n" + self._paginate_columns(self.analysis_view, slot_lines))

        else:
            self.analysis_view.setPlainText(
                f"Unrecognized frame size ({len(query)} bytes, expected "
                f"{RP_QCC_LEVEL_FRAME_SIZE}, {RP_CMD_FRAME_SIZE}, {RP_FRAME_SIZE}, "
                f"or {TOTAL_PACKET_SIZE}) - no byte-level breakdown.")

    def _paginate_columns(self, view: QPlainTextEdit, items: list) -> str:
        """
        Lay `items` out as fixed-width columns that fill down `view`'s
        visible height first, then wrap into the next column - rather than
        one long vertical list the user has to scroll through.
        """
        if not items:
            return ""

        fm = QFontMetrics(view.font())
        line_height = max(1, fm.lineSpacing())
        rows_avail = max(1, view.viewport().height() // line_height)

        col_width_chars = max(len(s) for s in items) + 2
        char_width = max(1, fm.horizontalAdvance("0"))
        cols_avail = max(1, view.viewport().width() // (col_width_chars * char_width))

        cols_needed = -(-len(items) // rows_avail)  # ceil
        cols = max(1, min(cols_avail, cols_needed))
        rows = -(-len(items) // cols)  # ceil, actual rows per column at this col count

        grid_lines = []
        for r in range(rows):
            cells = []
            for c in range(cols):
                idx = c * rows + r
                cells.append(items[idx].ljust(col_width_chars) if idx < len(items) else "")
            grid_lines.append("".join(cells).rstrip())
        return "\n".join(grid_lines)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._selected_query is not None:
            self._render_analysis(self._selected_query)

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
