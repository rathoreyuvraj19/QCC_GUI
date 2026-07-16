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

Operation flow (per the IDD + decisions with Yuvraj 2026-07-08):
  1. Mode Step 1 - broadcast mode_change_command(MSS_CONTROL) to all QTRMs
     ("switch to the low-speed 115200 bootloader link").
  2. Mode Step 2 - tell QCC itself to switch to low-speed. PROVISIONAL,
     UNCONFIRMED wire format: a standard 2970-byte header-only Mode 5
     frame with message_body[0] = 0x01 - the Mode 5 message body is
     undefined in the doc, this layout is a placeholder agreed with
     Yuvraj until the IDD owner confirms the real one.
  3. Only after both steps: Get LRU Info / Authenticate / Program / Verify.

Program chunk-advance rule: send the next 4096-byte chunk as soon as ANY
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
    NUM_QTRM, RP_FRAME_SIZE, RP_PAYLOAD_SIZE, build_broadcast_bootloader_frame,
    build_header_only_frame, build_remote_programming_frame, extract_rp_slots,
)
from core.rc_settings import COMMAND_ID_REMOTE_PROGRAMMING, rc_settings

# Provisional Mode Step 2 message body sub-command (see module docstring).
QCC_BODY_SWITCH_LOW_SPEED = 0x01

MODE_STEP_TIMEOUT_MS = 3000
LRU_INFO_TIMEOUT_MS = 3000
IAP_POLL_WINDOW_MS = 30_000          # Authenticate / Verify collection window
PROGRAM_START_ACK_MS = 3000          # informational window after IAP PROGRAM
CHUNK_TIMEOUT_MS_DEFAULT = 2000      # per-chunk watchdog (tab exposes a SpinField)
CHUNK_MAX_ATTEMPTS = 3
TRAILING_ACK_GRACE_MS = 3000         # collect late acks after the final chunk

CHUNK_PAD_BYTE = 0xFF  # erased-flash state; final chunk reports its REAL length

# Session/operation identifiers (also used by the tab for display switching)
OP_MODE_STEP1 = "mode_step1"
OP_MODE_STEP2 = "mode_step2"
OP_LRU_INFO = "lru_info"
OP_AUTHENTICATE = "authenticate"
OP_VERIFY = "verify"
OP_PROGRAM = "program"


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
    # (operation, qtrm_index, parsed-slot object) during Authenticate/Verify
    op_row_updated = Signal(str, int, object)
    # (operation,) - 30s window expired; rows still pending are No Response
    op_window_closed = Signal(str)
    # (chunk_index, chunk_count, attempt) - chunk was (re)sent
    chunk_progress = Signal(int, int, int)
    # (qtrm_index, chunk_index, ok) - one ack recorded into the matrix
    ack_recorded = Signal(int, int, bool)
    # (missing_count, failed_count) - transfer pass done, gaps computed
    program_finished = Signal(int, int)
    # (operation, ok, text) fired exactly once when the whole session ends -
    # main_window clears its busy-lock on this.
    session_finished = Signal(str, bool, str)
    # (raw_frame, is_tx, summary) for the tab's frame log
    log_frame = Signal(bytes, bool, str)

    def __init__(self, send_fn, parent=None):
        super().__init__(parent)
        self._send_fn = send_fn          # main_window._send_frame
        self.chunk_timeout_ms = CHUNK_TIMEOUT_MS_DEFAULT

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

        # Program state
        self._chunks = []                # [(padded_data, real_len), ...]
        self._chunk_count = 0
        self._current_chunk = None       # index being streamed, None outside streaming
        self._chunk_attempt = 0
        self._retry_queue = []           # chunk indices for the stragglers pass
        self._in_retry_pass = False
        self._program_phase = None       # "iap" | "stream" | "grace"
        # 96 x N matrix: per QTRM, {chunk_index: True(pass)/False(TRANSFER_FAILED)}
        self.ack_matrix = [dict() for _ in range(NUM_QTRM)]

    # -- helpers ------------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._op is not None

    @property
    def gate_open(self) -> bool:
        return self.mode_step1_done and self.mode_step2_done

    def _rp_header(self) -> bytes:
        return rc_settings.build_header(
            COMMAND_ID_REMOTE_PROGRAMMING, packet_size=RP_FRAME_SIZE
        )

    def _send_rp(self, inner_cmd: bytes, payload: bytes = b"", summary: str = ""):
        frame = build_remote_programming_frame(self._rp_header(), inner_cmd, payload)
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
        self._program_phase = None
        self._current_chunk = None
        self.session_finished.emit(op or "", ok, text)

    def cancel(self):
        """Operator abort - stop all timers, end the session cleanly."""
        if self._op is None:
            return
        self._finish(False, "Cancelled by operator")

    # -- mode-change sequence (two separate steps, mandatory order) ---------

    def start_mode_step1(self):
        if self.busy:
            return
        self._start(OP_MODE_STEP1, MODE_STEP_TIMEOUT_MS, self._on_simple_timeout)
        # Standard 2970-byte frame, NOT the 4196-byte RP frame - see
        # build_broadcast_bootloader_frame's docstring. QTRMs are still in
        # normal per-QTRM-addressed mode at this point, so the mode-change
        # command is replicated into every one of the 96 30-byte slots
        # rather than sent once for the QCC to broadcast (that broadcast
        # path only exists once QTRMs are already in low-speed mode).
        header = rc_settings.build_header(COMMAND_ID_REMOTE_PROGRAMMING)
        frame = build_broadcast_bootloader_frame(
            header, bl.build_mode_change_command(bl.BSN_MSS_CONTROL)
        )
        self._send_fn(frame)
        self.log_frame.emit(frame, True, "Mode Change -> QTRMs to low-speed (bsn_mode=MSS_CONTROL)")

    def start_mode_step2(self):
        if self.busy:
            return
        self._start(OP_MODE_STEP2, MODE_STEP_TIMEOUT_MS, self._on_simple_timeout)
        # PROVISIONAL wire format - see module docstring. Standard 2970-byte
        # header-only frame; the Mode 5 body's first byte carries the
        # placeholder "QCC: switch your own UART to 115200" sub-command.
        body = bytes([QCC_BODY_SWITCH_LOW_SPEED])
        header = rc_settings.build_header(COMMAND_ID_REMOTE_PROGRAMMING, message_body=body)
        frame = build_header_only_frame(header)
        self._send_fn(frame)
        self.log_frame.emit(frame, True, "QCC self mode change -> low-speed (PROVISIONAL format)")

    def _on_simple_timeout(self):
        # Shared by both mode steps and Get LRU Info: the window closed.
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
        elif op == OP_LRU_INFO:
            if self._got_any_frame:
                self._finish(True, "LRU info received")
            else:
                self.step_result.emit(op, False, "No response — link may be down")
                self._finish(False, "No response")

    def reset_gate(self):
        """E.g. after a link change - operations re-lock until redone."""
        self.mode_step1_done = False
        self.mode_step2_done = False
        self.gate_changed.emit(False)

    # -- Get LRU Info --------------------------------------------------------

    def start_lru_info(self):
        if self.busy or not self.gate_open:
            return
        self._start(OP_LRU_INFO, LRU_INFO_TIMEOUT_MS, self._on_simple_timeout)
        self._send_rp(bl.build_lru_info_request(),
                      summary="Get LRU Info request (layout ASSUMED, 0x31)")

    # -- Authenticate / Verify (30 s live-grid polls) -------------------------

    def start_authenticate(self):
        self._start_iap_poll(OP_AUTHENTICATE, bl.IAP_AUTHENTICATE)

    def start_verify(self):
        self._start_iap_poll(OP_VERIFY, bl.IAP_VERIFY)

    def _start_iap_poll(self, op: str, iap_mode: int):
        if self.busy or not self.gate_open:
            return
        self._start(op, IAP_POLL_WINDOW_MS, self._on_iap_window_closed)
        self._send_rp(
            bl.build_firmware_update_command(iap_mode),
            summary=f"Firmware Update Command IAP_MODE={bl.IAP_MODE_NAMES[iap_mode]}",
        )

    def _on_iap_window_closed(self):
        op = self._op
        self.op_window_closed.emit(op)
        if self._got_any_frame:
            self._finish(True, "Poll window closed")
        else:
            self.step_result.emit(op, False, "No response — link may be down")
            self._finish(False, "No response")

    # -- Program (chunk streaming) --------------------------------------------

    def start_program(self, image: bytes):
        if self.busy or not self.gate_open or not image:
            return
        self._chunks = split_chunks(image)
        self._chunk_count = len(self._chunks)
        self.ack_matrix = [dict() for _ in range(NUM_QTRM)]
        self._in_retry_pass = False
        self._retry_queue = []
        self._program_phase = "iap"
        self._start(OP_PROGRAM, PROGRAM_START_ACK_MS, self._on_program_phase_timeout)
        self._send_rp(
            bl.build_firmware_update_command(bl.IAP_PROGRAM),
            summary="Firmware Update Command IAP_MODE=PROGRAM",
        )

    def start_retry_pass(self):
        """Re-broadcast only the chunks some QTRM is still missing."""
        if self.busy or not self.gate_open or not self._chunks:
            return
        gaps = self.missing_chunk_indices()
        if not gaps:
            return
        self._op = OP_PROGRAM
        self._got_any_frame = False
        self._in_retry_pass = True
        self._retry_queue = gaps
        self._program_phase = "stream"
        self._send_next_chunk(first=True)

    def _on_program_phase_timeout(self):
        if self._program_phase == "iap":
            # Informational only - QTRMs may or may not ack the IAP PROGRAM
            # command itself before the bitstream starts; begin streaming
            # either way, but tell the operator what happened.
            self.step_result.emit(
                OP_PROGRAM, self._got_any_frame,
                "PROGRAM command acknowledged" if self._got_any_frame
                else "No ack to PROGRAM command — streaming anyway",
            )
            self._program_phase = "stream"
            self._current_chunk = None
            self._send_next_chunk(first=True)
        elif self._program_phase == "stream":
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
        elif self._program_phase == "grace":
            self._close_program_pass()

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
        self._retarget_timer(self._on_program_phase_timeout, self.chunk_timeout_ms)

    def _enter_grace(self):
        self._program_phase = "grace"
        self._retarget_timer(self._on_program_phase_timeout, TRAILING_ACK_GRACE_MS)

    def _close_program_pass(self):
        missing = self.missing_chunk_indices()
        failed = self.failed_pairs()
        self.program_finished.emit(len(missing), len(failed))
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

        context = (bl.CONTEXT_BITSTREAM
                   if self._op == OP_PROGRAM and self._program_phase in ("stream", "grace")
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

    def _dispatch_slot(self, q: int, parsed):
        op = self._op
        if op == OP_MODE_STEP1:
            self.op_row_updated.emit(op, q, parsed)
        elif op == OP_LRU_INFO:
            if isinstance(parsed, bl.LruStatusResponse):
                self.lru_row_updated.emit(q, parsed)
            else:
                self.op_row_updated.emit(op, q, parsed)
        elif op in (OP_AUTHENTICATE, OP_VERIFY):
            self.op_row_updated.emit(op, q, parsed)
        elif op == OP_PROGRAM:
            if self._program_phase == "iap":
                self.op_row_updated.emit(op, q, parsed)
                return
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
            if (self._program_phase == "stream" and ok and idx == current):
                self._send_next_chunk()
