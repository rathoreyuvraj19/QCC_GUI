"""
remote_prog_controller.py

Session state machine for the Remote Programming tab - the one part of the
GUI whose commands do NOT fit main_window.py's single-shot _awaiting_kind
dispatch (one send -> exactly one response frame -> done): firmware-update
operations get many response frames over long windows (per-QTRM staggered
replies during a 30 s Authenticate/Verify poll, one ack frame per streamed
bitstream chunk), so main_window routes EVERY received frame here for as
long as a session is active and only clears its busy-lock when
session_finished fires.

Lives on the GUI thread; all timing is QTimer-based (sends are already
non-blocking via udp_worker's socket thread), so no extra threads.

Operation flow (per the IDD + decisions with Yuvraj 2026-07-08/18):
  1. Mode Step 1 - send mode_change_command(MSS_CONTROL) to the targeted
     QTRM(s) ("switch to the low-speed 115200 bootloader link") - decoded
     by the fabric RTL (control_to_mss_proc), which raises the MUX flag
     the MSS polls to take the UART. Broadcast session: the command is
     replicated into all 96 slots; single-QTRM session (target_qtrm
     0-95): only the target's slot carries it, the other 95 slots are
     all-zero so only that QTRM drops to low-speed.
  2. Mode Step 2 - tell QCC itself to switch to low-speed: a bare 90-byte
     header-only frame (RE-DECIDED 2026-07-19 - was a 2970-byte
     header-only frame before) with byte 34 (SubCommand) =
     QCC_BODY_SWITCH_LOW_SPEED (0x01). Byte 34 of every Remote Programming
     frame is a SubCommand selector QCC itself reads and acts on - 0x00
     Broadcast (fan the rest of the frame out to all 96 QTRMs), 0x01 QCC
     -> Low-Speed, 0x02 QCC -> High-Speed. Byte 35 (QTRM_SELECT) rides
     along in the 0x01/0x02 frames: QCC latches it into its LRU-select
     mux for the whole low-speed session, so every subsequent SubCommand
     0x00 frame reaches only the selected QTRM (0-95) or all 96 (0xFF),
     and QCC zero-fills the non-selected slots in its 2970-byte responses.
  3. Link Check - broadcast a Link Request (0x30); each QTRM's processor
     answers with its 0x34-tagged link response (B1 B2 B3 B4 body).
  4. Get LRU Info / Upload bitstream / Authenticate / Program / Verify -
     the image-scoped operations all carry the tab's Golden/Current flag.
  5. QTRM -> High Speed - broadcast the bootloader's Mode Change
     MSS->Fabric command (0x32, CT_MODE_CHANGE_MSS_TO_FAB) to all 96
     QTRMs via the normal SubCommand 0x00 broadcast path (100-byte frame,
     same shape as Link Check/LRU Info). QTRMs already auto-return to high
     speed on their own after Programming completes, but this button lets
     the operator force it explicitly (e.g. after an aborted session).
     Does not touch the gate - QCC itself is still on the low-speed link
     until step 6.
  6. QCC -> High Speed - QCC's own self-directed UART switch back to
     high speed (RE-DECIDED 2026-07-19: mirrors Mode Step 2, NOT the
     QTRM-targeted 0x32 bootloader command - that's now step 5's separate
     button), same bare 90-byte frame shape with byte 34 (SubCommand) =
     QCC_BODY_SWITCH_HIGH_SPEED (0x02); the gate re-locks (steps 1-2 must
     be redone to return).

Upload vs Program (split 2026-07-18 to match the firmware): Upload is the
bitstream TRANSFER - one Bitstream Receive announce (0x33: golden flag,
packet size 4096, packet count) followed by the 0x34 data chunks, each
acked per-QTRM into the ack matrix (firmware recieve_bit_stream()).
Program is a standalone one-shot IAP command (0x36 mode 2) telling the
SmartFusion2 to flash itself from the ALREADY-uploaded SPI image
(firmware iap_program()) - it does not stream anything, and the firmware
busy-waits without replying, so no response is treated as normal.

Wire framing (RE-DECIDED 2026-07-19 per Yuvraj): once QTRMs+QCC are in
low-speed mode, every RP command except the bitstream DATA chunks sends
just [90-byte header][10-byte inner command] = 100 bytes, no payload
padding - see _send_rp() and core/packet.py's build_remote_programming_cmd_frame().
Only the actual 0x34 DATA chunks (the real file-upload payload) still use
the full [90-byte header][10-byte command][4096-byte payload] = 4196-byte
frame via build_remote_programming_frame().

Upload chunk-advance rule: send the next 4096-byte chunk as soon as ANY
ONE of the 96 QTRMs acks the current one - never wait for all 96. A full
96 x N ack matrix is maintained in the background from every ack that does
arrive (including late acks for earlier chunks), and after the transfer a
"retry stragglers" pass re-broadcasts just the chunks some QTRM is missing.
Backfill is broadcast-only by construction - QCC's fabric fans every frame
out to all 96 identically, so a gap chunk goes to everyone and QTRMs that
already have it are expected to re-ack or ignore it.
"""

from PySide6.QtCore import QObject, QTimer, Signal

import apps.bootloader_packet as bl
from core.packet import (
    NUM_QTRM, RP_CMD_FRAME_SIZE, RP_FRAME_SIZE, RP_PAYLOAD_SIZE,
    RP_QCC_LEVEL_FRAME_SIZE, RP_QTRM_SELECT_BROADCAST,
    build_broadcast_bootloader_frame,
    build_qcc_level_frame, build_remote_programming_cmd_frame,
    build_remote_programming_frame, extract_rp_slots,
)
from core.rc_settings import COMMAND_ID_DWELL, COMMAND_ID_REMOTE_PROGRAMMING, rc_settings

# Remote Programming SubCommand values (header byte 34 / message_body offset
# 0, see module docstring). QCC_COMMAND byte 33 = REMOTE_PROGRAMMING selects
# a Remote Programming session; this SubCommand byte tells QCC what to do
# with the rest of the frame.
RP_SUBCMD_BROADCAST = 0x00        # QCC fans the rest of the frame out to all
                                   # 96 QTRMs - the default, since
                                   # rc_settings.build_header() leaves
                                   # message_body all-zero unless overridden.
QCC_BODY_SWITCH_LOW_SPEED = 0x01  # Mode Step 2: QCC -> Low-Speed
QCC_BODY_SWITCH_HIGH_SPEED = 0x02  # QCC -> High-Speed - value 0x02 (not
                                   # 0x00) so 0x00 is reserved exclusively
                                   # for RP_SUBCMD_BROADCAST.
# Byte 35 (message_body offset 1) in the 0x01/0x02 frames is QTRM_SELECT -
# see target_qtrm below and core/packet.py's RP_QTRM_SELECT_BROADCAST.

MODE_STEP_TIMEOUT_MS = 3000
LRU_INFO_TIMEOUT_MS = 3000
LINK_CHECK_WINDOW_MS = 3000          # Link Request response collection window
IAP_POLL_WINDOW_MS = 30_000          # Authenticate/Verify/Program window DEFAULT -
                                     # operator-adjustable via the tab's
                                     # "Op timeout (s)" field (iap_window_ms)
MODE_BACK_WINDOW_MS = 2000           # QCC self return-to-high-speed settle window
                                     # (a reply, if any, is bonus - the settle window
                                     # is what actually gates completion, see on_frame())
QTRM_HIGH_SPEED_WINDOW_MS = 2000     # QTRM -> High Speed settle window - firmware's
                                     # CT_MODE_CHANGE_MSS_TO_FAB handler only toggles
                                     # GPIOs and exits, no UART reply, so (like Mode
                                     # Back) a reply is bonus, not required
CHUNK_TIMEOUT_MS_DEFAULT = 2000      # per-chunk watchdog (tab exposes a SpinField)
CHUNK_MAX_ATTEMPTS = 3
TRAILING_ACK_GRACE_MS = 3000         # collect late acks after the final chunk
BITSTREAM_ANNOUNCE_SETTLE_MS = 20    # gap before chunk 0, after the 0x33 announce -
                                     # user_functions.c's CMD_TYPE_START_BIT_STREAM_REC
                                     # handler calls recieve_bit_stream() in the same
                                     # UART-polling loop that just read the announce's
                                     # 10 bytes: it stack-allocates and memsets two
                                     # packetSize-sized buffers before it starts polling
                                     # MSS_UART_get_rx() again for chunk 0's bytes. If
                                     # chunk 0 lands on the wire before that setup
                                     # finishes, the UART can drop its opening bytes.

CHUNK_PAD_BYTE = 0xFF  # erased-flash state; final chunk reports its REAL length

# Session/operation identifiers (also used by the tab for display switching)
OP_MODE_STEP1 = "mode_step1"
OP_MODE_STEP2 = "mode_step2"
OP_LINK_CHECK = "link_check"
OP_LRU_INFO = "lru_info"
OP_AUTHENTICATE = "authenticate"
OP_VERIFY = "verify"
OP_UPLOAD = "upload"                 # bitstream transfer (0x33 announce + 0x34 chunks)
OP_PROGRAM = "program"               # one-shot IAP PROGRAM (0x36 mode 2)
OP_QTRM_HIGH_SPEED = "qtrm_high_speed"  # broadcast bootloader 0x32 to all QTRMs
OP_MODE_BACK = "mode_back"           # QCC -> High Speed (SubCommand 0x02)


def split_chunks(image: bytes) -> list:
    """
    (data, real_length) per 4096-byte chunk; the final short chunk is
    0xFF-padded to 4096 but keeps its real length for fw_packet_length.
    """
    chunks = []
    for off in range(0, len(image), RP_PAYLOAD_SIZE):
        piece = image[off: off + RP_PAYLOAD_SIZE]
        real_len = len(piece)
        if real_len < RP_PAYLOAD_SIZE:
            piece = piece + bytes([CHUNK_PAD_BYTE]) * (RP_PAYLOAD_SIZE - real_len)
        chunks.append((piece, real_len))
    return chunks


class RemoteProgController(QObject):
    # (operation, ok, text) - result of a one-shot step (mode steps, and
    # Program's initial IAP command window closing).
    step_result = Signal(str, bool, str)
    # Both mode steps completed -> operations unlocked (or lock re-applied).
    gate_changed = Signal(bool)
    # (qtrm_index, LruStatusResponse)
    lru_row_updated = Signal(int, object)
    # (operation, qtrm_index, parsed-slot object) during Link Check /
    # Authenticate / Program / Verify
    op_row_updated = Signal(str, int, object)
    # (operation,) - collection window expired; rows still pending are No Response
    op_window_closed = Signal(str)
    # (chunk_index, chunk_count, attempt) - chunk was (re)sent
    chunk_progress = Signal(int, int, int)
    # (qtrm_index, chunk_index, ok) - one ack recorded into the matrix
    ack_recorded = Signal(int, int, bool)
    # (missing_count, failed_count) - upload transfer pass done, gaps computed
    upload_finished = Signal(int, int)
    # (operation, ok, text) fired exactly once when the whole session ends -
    # main_window clears its busy-lock on this.
    session_finished = Signal(str, bool, str)
    # (raw_frame, is_tx, summary) for the tab's frame log
    log_frame = Signal(bytes, bool, str)

    def __init__(self, send_fn, parent=None):
        super().__init__(parent)
        self._send_fn = send_fn          # main_window._send_frame
        self.chunk_timeout_ms = CHUNK_TIMEOUT_MS_DEFAULT
        # Authenticate/Verify/Program reply-collection window - the tab
        # exposes a seconds SpinField for it (per Yuvraj 2026-07-18).
        self.iap_window_ms = IAP_POLL_WINDOW_MS
        # Which QTRM(s) the next low-speed session targets: 0-95 = one
        # QTRM, RP_QTRM_SELECT_BROADCAST (0xFF) = all 96. Drives Mode Step
        # 1's slot filling and rides in byte 35 of the Mode Step 2 / QCC ->
        # High Speed frames; the tab locks its selector while the gate is
        # open, so the value can't drift mid-session.
        self.target_qtrm = RP_QTRM_SELECT_BROADCAST

        self.mode_step1_done = False
        self.mode_step2_done = False

        self._op = None                  # active operation, None = idle
        self._got_any_frame = False
        # One reusable single-shot timer for whatever the active operation is
        # waiting on (mode-step window, 30 s poll, per-chunk watchdog,
        # trailing-ack grace) - each call site (_start/_send_current_chunk/
        # _enter_grace) disconnects any previous handler and connects its own.
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer_connected = False

        # Upload (bitstream transfer) state
        self._chunks = []                # [(padded_data, real_len), ...]
        self._chunk_count = 0
        self._current_chunk = None       # index being streamed, None outside streaming
        self._chunk_attempt = 0
        self._retry_queue = []           # chunk indices for the stragglers pass
        self._in_retry_pass = False
        self._upload_phase = None        # "stream" | "grace"
        self._image_is_golden = False    # tab toggle at upload start
        # 96 x N matrix: per QTRM, {chunk_index: True(pass)/False(TRANSFER_FAILED)}
        self.ack_matrix = [dict() for _ in range(NUM_QTRM)]

    # -- helpers ------------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._op is not None

    @property
    def gate_open(self) -> bool:
        return self.mode_step1_done and self.mode_step2_done

    def _rp_header(self, packet_size: int) -> bytes:
        return rc_settings.build_header(
            COMMAND_ID_REMOTE_PROGRAMMING, packet_size=packet_size
        )

    def _send_rp(self, inner_cmd: bytes, payload: bytes = b"", summary: str = ""):
        # Only the bitstream DATA chunks carry a real payload - everything
        # else (Link Check, Get LRU Info, Mode Back, Authenticate/Verify/
        # Program, Bitstream Receive announce) sends just its 10-byte
        # command with no payload padding (decided 2026-07-19, see
        # core/packet.py's RP_CMD_FRAME_SIZE comment).
        if payload:
            frame = build_remote_programming_frame(
                self._rp_header(RP_FRAME_SIZE), inner_cmd, payload)
        else:
            frame = build_remote_programming_cmd_frame(
                self._rp_header(RP_CMD_FRAME_SIZE), inner_cmd)
        self._send_fn(frame)
        self.log_frame.emit(frame, True, summary)

    def _retarget_timer(self, on_timeout, timeout_ms: int):
        """Swap the reusable timer's handler and (re)start it - tracks
        connection state itself rather than calling disconnect() blind,
        which prints a libpyside warning the very first time (nothing
        connected yet)."""
        self._timer.stop()
        if self._timer_connected:
            self._timer.timeout.disconnect()
        self._timer.timeout.connect(on_timeout)
        self._timer_connected = True
        self._timer.start(timeout_ms)

    def _start(self, op: str, timeout_ms: int, on_timeout):
        self._op = op
        self._got_any_frame = False
        self._retarget_timer(on_timeout, timeout_ms)

    def _finish(self, ok: bool, text: str):
        op, self._op = self._op, None
        self._timer.stop()
        self._upload_phase = None
        self._current_chunk = None
        self.session_finished.emit(op or "", ok, text)

    def cancel(self):
        """Operator abort - stop all timers, end the session cleanly."""
        if self._op is None:
            return
        self._finish(False, "Cancelled by operator")

    # -- mode-change sequence (two separate steps, mandatory order) ---------

    def target_desc(self) -> str:
        """Human-readable form of target_qtrm for log summaries and UI."""
        if self.target_qtrm == RP_QTRM_SELECT_BROADCAST:
            return "all 96 QTRMs"
        return f"QTRM {self.target_qtrm} only"

    def start_mode_step1(self):
        if self.busy:
            return
        self._start(OP_MODE_STEP1, MODE_STEP_TIMEOUT_MS, self._on_simple_timeout)
        # Standard 2970-byte frame, NOT the 4196-byte RP frame - see
        # build_broadcast_bootloader_frame's docstring. QTRMs are still in
        # normal per-QTRM-addressed mode at this point, so the mode-change
        # command rides in the addressed slot(s) rather than being sent
        # once for the QCC to broadcast (that broadcast path only exists
        # once QTRMs are already in low-speed mode): every slot when
        # targeting all 96, only the target's slot (others all-zero) for a
        # single-QTRM session. Uses QCC_COMMAND=DATA_DISTRIBUTION (0x00),
        # NOT REMOTE_PROGRAMMING (0xFF): per Yuvraj, Mode Step 1 rides the
        # existing DMA data-pipeline path on the QCC side (same as normal
        # dwell/dbf traffic) - QCC just moves the 2880-byte payload to the
        # fabric unmodified, and it's the QTRM bootloader that interprets
        # its own slot's first 10 bytes as a mode-change command.
        header = rc_settings.build_header(COMMAND_ID_DWELL)
        frame = build_broadcast_bootloader_frame(
            header, bl.build_mode_change_command(bl.BSN_MSS_CONTROL),
            target_qtrm=self.target_qtrm,
        )
        self._send_fn(frame)
        self.log_frame.emit(frame, True,
                            f"Mode Change -> {self.target_desc()} to low-speed (bsn_mode=MSS_CONTROL)")

    def start_mode_step2(self):
        if self.busy:
            return
        self._start(OP_MODE_STEP2, MODE_STEP_TIMEOUT_MS, self._on_simple_timeout)
        # See module docstring. Bare 90-byte header, no inner
        # command/payload (RE-DECIDED 2026-07-19: this is QCC's own
        # self-directed UART switch, not a QTRM-targeted bootloader
        # command); byte 34 carries the SubCommand "QCC: switch your own
        # UART to 115200" and byte 35 the QTRM_SELECT QCC latches into its
        # LRU-select mux for the whole low-speed session.
        body = bytes([QCC_BODY_SWITCH_LOW_SPEED, self.target_qtrm & 0xFF])
        header = rc_settings.build_header(
            COMMAND_ID_REMOTE_PROGRAMMING, message_body=body,
            packet_size=RP_QCC_LEVEL_FRAME_SIZE,
        )
        frame = build_qcc_level_frame(header)
        self._send_fn(frame)
        self.log_frame.emit(frame, True,
                            f"QCC self mode change -> low-speed (SubCommand 0x01, target {self.target_desc()})")

    def _on_simple_timeout(self):
        # Shared by the mode steps, Link Check, Get LRU Info, and Return to
        # High Speed: the collection window closed.
        op = self._op
        if op in (OP_MODE_STEP1, OP_MODE_STEP2):
            if self._got_any_frame:
                if op == OP_MODE_STEP1:
                    self.mode_step1_done = True
                else:
                    self.mode_step2_done = True
                self.step_result.emit(op, True, "Response received")
                self.gate_changed.emit(self.gate_open)
                self._finish(True, "Step complete")
            else:
                self.step_result.emit(op, False, "No response — link may be down")
                self._finish(False, "No response")
        elif op == OP_LINK_CHECK:
            self.op_window_closed.emit(op)
            if self._got_any_frame:
                self._finish(True, "Link check window closed")
            else:
                self.step_result.emit(op, False, "No response — link may be down")
                self._finish(False, "No response")
        elif op == OP_LRU_INFO:
            if self._got_any_frame:
                self._finish(True, "LRU info received")
            else:
                self.step_result.emit(op, False, "No response — link may be down")
                self._finish(False, "No response")
        elif op == OP_QTRM_HIGH_SPEED:
            # QCC should have acked the DMA write well before the window
            # closed - on_frame() finishes early the moment that arrives.
            # Reaching here means it never showed up. Doesn't touch the
            # gate; QCC itself is still on the low-speed link.
            self.step_result.emit(op, False, "No ack from QCC — link may be down")
            self._finish(False, "No response")
        elif op == OP_MODE_BACK:
            # Same - on_frame() finishes early on the DMA-write ack. No ack
            # by the window closing still re-locks the gate (the QTRMs are
            # back at high speed either way) but is flagged as a failure.
            self.mode_step1_done = False
            self.mode_step2_done = False
            self.gate_changed.emit(False)
            self.step_result.emit(op, False, "No ack from QCC — link may be down")
            self._finish(False, "No response")

    def reset_gate(self):
        """E.g. after a link change - operations re-lock until redone."""
        self.mode_step1_done = False
        self.mode_step2_done = False
        self.gate_changed.emit(False)

    # -- Link Check (step 3) -------------------------------------------------

    def start_link_check(self):
        """Broadcast a Link Request (0x30); every QTRM's processor answers
        with its 0x34-tagged link response (B1 B2 B3 B4 body)."""
        if self.busy or not self.gate_open:
            return
        self._start(OP_LINK_CHECK, LINK_CHECK_WINDOW_MS, self._on_simple_timeout)
        self._send_rp(bl.build_link_request(), summary="Link Request (0x30) broadcast")

    # -- Get LRU Info --------------------------------------------------------

    def start_lru_info(self):
        if self.busy or not self.gate_open:
            return
        self._start(OP_LRU_INFO, LRU_INFO_TIMEOUT_MS, self._on_simple_timeout)
        self._send_rp(bl.build_lru_info_request(),
                      summary="Get LRU Info request (layout ASSUMED, 0x31)")

    # -- QTRM -> High Speed / QCC -> High Speed (return-to-normal pair) -----

    def start_qtrm_high_speed(self):
        """QTRM -> High Speed: broadcasts the bootloader's Mode Change
        MSS->Fabric command (0x32, CT_MODE_CHANGE_MSS_TO_FAB) to all 96
        QTRMs via the normal SubCommand 0x00 broadcast path - same 100-byte
        frame shape as Link Check/Get LRU Info. QTRMs already auto-return
        to high speed on their own after Programming completes, but this
        lets the operator force it explicitly (e.g. after an aborted
        session). Firmware's handler for 0x32 only toggles GPIOs and exits
        - no UART reply, so (like QCC -> High Speed) a reply is bonus, not
        required. Does not touch the gate; QCC itself is still on the
        low-speed link until QCC -> High Speed is sent separately."""
        if self.busy or not self.gate_open:
            return
        self._start(OP_QTRM_HIGH_SPEED, QTRM_HIGH_SPEED_WINDOW_MS, self._on_simple_timeout)
        self._send_rp(bl.build_mode_change_mss_to_fab(),
                      summary=f"Mode Change MSS->Fabric (0x32) -> {self.target_desc()} to high-speed")

    def start_mode_back(self):
        """QCC -> High Speed: mirrors Mode Step 2, QCC's own self-directed
        UART switch back to high speed - RE-DECIDED 2026-07-19 per Yuvraj,
        NOT the QTRM-targeted 0x32 bootloader command (that's now the
        separate QTRM -> High Speed button/start_qtrm_high_speed()). Bare
        90-byte header, same shape as Mode Step 2 (see
        build_qcc_level_frame's docstring); the gate re-locks when the
        settle window closes."""
        if self.busy or not self.gate_open:
            return
        self._start(OP_MODE_BACK, MODE_BACK_WINDOW_MS, self._on_simple_timeout)
        # Byte 35 carries the same QTRM_SELECT the session was opened with
        # (the tab locks its selector while the gate is open).
        body = bytes([QCC_BODY_SWITCH_HIGH_SPEED, self.target_qtrm & 0xFF])
        header = rc_settings.build_header(
            COMMAND_ID_REMOTE_PROGRAMMING, message_body=body,
            packet_size=RP_QCC_LEVEL_FRAME_SIZE,
        )
        frame = build_qcc_level_frame(header)
        self._send_fn(frame)
        self.log_frame.emit(frame, True,
                            f"QCC self mode change -> high-speed (SubCommand 0x02, target {self.target_desc()})")

    # -- Authenticate / Verify (30 s live-grid polls) + one-shot Program ------

    def start_authenticate(self, image_is_golden: bool = False):
        self._start_iap_poll(OP_AUTHENTICATE, bl.IAP_AUTHENTICATE, image_is_golden,
                             self.iap_window_ms)

    def start_verify(self, image_is_golden: bool = False):
        self._start_iap_poll(OP_VERIFY, bl.IAP_VERIFY, image_is_golden,
                             self.iap_window_ms)

    def start_program(self, image_is_golden: bool = False):
        """One-shot IAP PROGRAM (0x36 mode 2) - tells the SmartFusion2 to
        flash itself from the already-uploaded SPI image. The firmware
        busy-waits without replying, so a silent window is NOT a failure."""
        self._start_iap_poll(OP_PROGRAM, bl.IAP_PROGRAM, image_is_golden,
                             self.iap_window_ms)

    def _start_iap_poll(self, op: str, iap_mode: int, image_is_golden: bool,
                        window_ms: int):
        if self.busy or not self.gate_open:
            return
        self._start(op, window_ms, self._on_iap_window_closed)
        self._send_rp(
            bl.build_firmware_update_command(iap_mode, image_is_golden),
            summary=f"Firmware Update Command IAP_MODE={bl.IAP_MODE_NAMES[iap_mode]}"
                    f" ({'GOLDEN' if image_is_golden else 'CURRENT'} image)",
        )

    def _on_iap_window_closed(self):
        op = self._op
        self.op_window_closed.emit(op)
        if self._got_any_frame:
            self._finish(True, "Poll window closed")
        elif op == OP_PROGRAM:
            # iap_program() never replies (busy-wait loop; the FPGA
            # reprograms and reboots) - silence is the expected outcome.
            self._finish(True, "PROGRAM sent — no replies (normal: devices reprogram)")
        else:
            self.step_result.emit(op, False, "No response — link may be down")
            self._finish(False, "No response")

    # -- Upload (bitstream transfer: 0x33 announce + 0x34 chunk streaming) ----

    def start_upload(self, image: bytes, image_is_golden: bool = False):
        if self.busy or not self.gate_open or not image:
            return
        self._chunks = split_chunks(image)
        self._chunk_count = len(self._chunks)
        self.ack_matrix = [dict() for _ in range(NUM_QTRM)]
        self._in_retry_pass = False
        self._retry_queue = []
        self._image_is_golden = image_is_golden
        self._op = OP_UPLOAD
        self._got_any_frame = False
        self._upload_phase = "stream"
        # Bitstream Receive announce (0x33): the QTRM enters
        # recieve_bit_stream(count, 4096, golden) and starts reading
        # packets. It sends no ack for the announce itself. Chunk 0 is
        # delayed by BITSTREAM_ANNOUNCE_SETTLE_MS rather than sent
        # immediately - see that constant's comment - the watchdog still
        # covers a lost announce (no acks -> retries -> abort).
        self._send_rp(
            bl.build_bitstream_receive_command(
                RP_PAYLOAD_SIZE, self._chunk_count, image_is_golden),
            summary=f"Bitstream Receive announce (0x33, "
                    f"{'GOLDEN' if image_is_golden else 'CURRENT'} image, "
                    f"{self._chunk_count} x {RP_PAYLOAD_SIZE}B)",
        )
        self._current_chunk = None
        QTimer.singleShot(BITSTREAM_ANNOUNCE_SETTLE_MS, lambda: self._send_next_chunk(first=True))

    def start_retry_pass(self):
        """Re-broadcast only the chunks some QTRM is still missing."""
        if self.busy or not self.gate_open or not self._chunks:
            return
        gaps = self.missing_chunk_indices()
        if not gaps:
            return
        self._op = OP_UPLOAD
        self._got_any_frame = False
        self._in_retry_pass = True
        self._retry_queue = gaps
        self._upload_phase = "stream"
        self._send_next_chunk(first=True)

    def _on_upload_phase_timeout(self):
        if self._upload_phase == "stream":
            # Per-chunk watchdog: nobody acked the current chunk in time.
            if self._chunk_attempt < CHUNK_MAX_ATTEMPTS:
                self._send_current_chunk(retry=True)
            else:
                idx = self._retry_queue[0] if self._in_retry_pass else self._current_chunk
                self._finish(
                    False,
                    f"Chunk {idx} got no ack from any QTRM after "
                    f"{CHUNK_MAX_ATTEMPTS} attempts — aborted",
                )
        elif self._upload_phase == "grace":
            self._close_upload_pass()

    def _send_next_chunk(self, first: bool = False):
        if self._in_retry_pass:
            if not first:
                self._retry_queue.pop(0)
            if not self._retry_queue:
                self._enter_grace()
                return
            idx = self._retry_queue[0]
        else:
            idx = 0 if first or self._current_chunk is None else self._current_chunk + 1
            if idx >= self._chunk_count:
                self._enter_grace()
                return
            self._current_chunk = idx
        self._chunk_attempt = 0
        self._send_current_chunk()

    def _send_current_chunk(self, retry: bool = False):
        idx = self._retry_queue[0] if self._in_retry_pass else self._current_chunk
        data, real_len = self._chunks[idx]
        self._chunk_attempt += 1
        inner = bl.build_bitstream_data_header(real_len, self._chunk_count, idx)
        self._send_rp(
            inner, payload=data,
            summary=f"Bitstream chunk {idx + 1}/{self._chunk_count}"
                    f" ({real_len} bytes{', retry' if retry else ''})",
        )
        self.chunk_progress.emit(idx, self._chunk_count, self._chunk_attempt)
        self._retarget_timer(self._on_upload_phase_timeout, self.chunk_timeout_ms)

    def _enter_grace(self):
        self._upload_phase = "grace"
        self._retarget_timer(self._on_upload_phase_timeout, TRAILING_ACK_GRACE_MS)

    def _close_upload_pass(self):
        missing = self.missing_chunk_indices()
        failed = self.failed_pairs()
        self.upload_finished.emit(len(missing), len(failed))
        self._finish(
            not missing and not failed,
            "Transfer complete — all QTRMs acked every chunk" if not missing and not failed
            else f"Transfer pass done — {len(missing)} chunk(s) missing acks, "
                 f"{len(failed)} failure(s) reported",
        )

    # -- gap queries (drive the tab's gaps table / Retry button) --------------

    def missing_chunk_indices(self) -> list:
        """Chunk indices at least one QTRM never successfully acked."""
        missing = set()
        for i in range(self._chunk_count):
            for q in range(NUM_QTRM):
                if self.ack_matrix[q].get(i) is not True:
                    missing.add(i)
                    break
        return sorted(missing)

    def qtrm_gaps(self, qtrm_index: int) -> tuple:
        """(acked_count, sorted missing chunk list, sorted failed chunk list)."""
        row = self.ack_matrix[qtrm_index]
        acked = sum(1 for v in row.values() if v is True)
        failed = sorted(i for i, v in row.items() if v is False)
        missing = sorted(i for i in range(self._chunk_count) if row.get(i) is not True)
        return acked, missing, failed

    def failed_pairs(self) -> list:
        return [(q, i) for q in range(NUM_QTRM)
                for i, v in self.ack_matrix[q].items() if v is False]

    @property
    def chunk_count(self) -> int:
        return self._chunk_count

    # -- RX routing (called by main_window for every frame while busy) --------

    def on_frame(self, raw: bytes, elapsed_us=None):
        if self._op is None:
            return
        self._got_any_frame = True

        if self._op == OP_MODE_STEP1:
            # Mode Step 1 - latch on first response, don't wait for timeout
            self.log_frame.emit(raw, False, "QCC mode-change response")
            self.mode_step1_done = True
            self.step_result.emit(OP_MODE_STEP1, True, "Response received")
            self.gate_changed.emit(self.gate_open)
            self._finish(True, "Step complete")
            return

        if self._op == OP_MODE_STEP2:
            # QCC's own response - latch immediately, don't wait for timeout
            self.log_frame.emit(raw, False, "QCC mode-change response")
            self.mode_step2_done = True
            self.step_result.emit(OP_MODE_STEP2, True, "Response received")
            self.gate_changed.emit(self.gate_open)
            self._finish(True, "Step complete")
            return

        if self._op == OP_QTRM_HIGH_SPEED:
            # QTRMs themselves never reply to the 0x32 broadcast (GPIO
            # toggle + exit), but QCC acks with a bare 90-byte response once
            # it's written the command to the DMA/fabric bus - latch on
            # that immediately instead of sitting out the full settle
            # window. Doesn't touch the gate; QCC itself is still low-speed.
            self.log_frame.emit(raw, False, "QCC DMA-write ack")
            self._finish(True, "QTRM high-speed broadcast acked")
            return

        if self._op == OP_MODE_BACK:
            # QCC's own bare 90-byte ack to its self-directed high-speed
            # switch, sent once it's written the command to DMA - same
            # shape as Mode Step 2. Latch on it immediately and re-lock the
            # gate; the QTRMs are back at high speed either way.
            self.log_frame.emit(raw, False, "QCC DMA-write ack")
            self.mode_step1_done = False
            self.mode_step2_done = False
            self.gate_changed.emit(False)
            self._finish(True, "Returned to high speed — gate re-locked")
            return

        context = (bl.CONTEXT_BITSTREAM
                   if self._op == OP_UPLOAD and self._upload_phase in ("stream", "grace")
                   else bl.CONTEXT_FW_UPDATE)
        slots = extract_rp_slots(raw)
        decoded_any = False
        for q, raw10 in enumerate(slots):
            try:
                parsed = bl.parse_slot(raw10, context)
            except ValueError:
                continue
            if parsed is None:
                continue
            decoded_any = True
            self._dispatch_slot(q, parsed)
        self.log_frame.emit(
            raw, False,
            f"{self._op} response frame" + ("" if decoded_any else " (no populated slots)"),
        )

        if self._op == OP_LINK_CHECK:
            # QCC replies with exactly one frame carrying every targeted
            # QTRM's slot already populated (or zero-filled) - no need to
            # sit out the rest of LINK_CHECK_WINDOW_MS collecting more
            # frames that will never arrive.
            self.op_window_closed.emit(OP_LINK_CHECK)
            self._finish(True, "Link check response received")
        elif self._op == OP_LRU_INFO:
            # Same shape as Link Check - QCC answers with exactly one frame
            # carrying every targeted QTRM's LRU slot, so there's nothing
            # left to collect once it lands.
            self._finish(True, "LRU info received")

    def _dispatch_slot(self, q: int, parsed):
        op = self._op
        if op == OP_MODE_STEP1:
            self.op_row_updated.emit(op, q, parsed)
        elif op == OP_LRU_INFO:
            if isinstance(parsed, bl.LruStatusResponse):
                self.lru_row_updated.emit(q, parsed)
            else:
                self.op_row_updated.emit(op, q, parsed)
        elif op in (OP_LINK_CHECK, OP_AUTHENTICATE, OP_VERIFY, OP_PROGRAM):
            self.op_row_updated.emit(op, q, parsed)
        elif op == OP_UPLOAD:
            if not isinstance(parsed, bl.BitstreamAck):
                self.op_row_updated.emit(op, q, parsed)
                return
            idx = parsed.ith_packet
            if idx >= self._chunk_count:
                return  # nonsense index - ignore rather than corrupt the matrix
            ok = not parsed.transfer_failed
            # A pass never downgrades to fail from a stale duplicate, but a
            # fail is upgraded by a later successful re-ack (retry pass).
            prev = self.ack_matrix[q].get(idx)
            if prev is not True:
                self.ack_matrix[q][idx] = ok
                self.ack_recorded.emit(q, idx, ok)
            # Advance rule: ANY ONE successful ack of the in-flight chunk
            # moves streaming forward - never wait for all 96.
            current = (self._retry_queue[0] if self._in_retry_pass and self._retry_queue
                       else self._current_chunk)
            if (self._upload_phase == "stream" and ok and idx == current):
                self._send_next_chunk()
