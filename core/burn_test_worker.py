"""
burn_test_worker.py

Fire-and-forget, fixed-cadence UDP stream for stress-testing the QCC link -
deliberately isolated from UdpWorker/main_window's one-command-in-flight
model (_awaiting_kind/_check_not_busy/_COMMANDS): waiting for each response
before sending the next would cap throughput at round-trip time, not the
configured interval, and UdpWorker's own RTT timing (_last_send_time, a
single scalar) is only meaningful with exactly one frame in flight - never
true here once the interval is anywhere near or below the real RTT.

Owns its own socket and its own send+receive loop on one thread - sending
is perf_counter()-paced, receiving is drained non-blockingly between
sends, and frames are paired by the header's MESSAGE_NUMBER (which
strictly increases on every rc_settings.build_header() call) rather than
by arrival order, since many frames are outstanding at once at any
interval below the real round-trip time.

Binds to the SAME local port the interactive UdpWorker uses, not an
arbitrary ephemeral one - confirmed against real hardware that the QCC
sends responses to the port it's paired with, not to whichever port a
query actually came from (symmetric reply-to-sender, which the mock
responder in status_responder_app.py does, does NOT hold on real
hardware). main_window.py pauses the interactive UdpWorker to free that
port before starting this, and reconnects it after stopping.

Usage:
    worker = BurnTestWorker(qcc_ip, qcc_port, BurnTestWorker.PAYLOAD_LINK_TEST,
                             interval_s=0.001, local_port=local_port)
    worker.stats_ready.connect(...)        # dict, ~10 Hz
    worker.rows_ready.connect(...)         # list of CSV rows (core.frame_logger.CSV_COLUMNS order), ~10 Hz
    worker.link_result_ready.connect(...)  # list of 96 bools, ~10 Hz, Link Test payload only
    worker.error.connect(...)
    worker.start()
    ...
    worker.stop()
"""

import socket
import time
from datetime import datetime

from PySide6.QtCore import QThread, Signal

from core.frame_logger import (
    QTRM_NOT_OK, QTRM_OK, RESULT_CRC_FAIL, RESULT_OK, RESULT_TIMEOUT,
    RESULT_UNSOLICITED, command_name,
)
from core.packet import (
    FIXED_HEADER_SIZE, NUM_QTRM, QCC_HEADER_SIZE, QCCHeaderTx, TOTAL_PACKET_SIZE,
    build_header_only_frame, build_link_test_frame, parse_link_test_response,
)
from core.rc_settings import COMMAND_ID_LINK_TEST, COMMAND_ID_QCC_STATUS, rc_settings

_HEADER_SIZE = FIXED_HEADER_SIZE + QCC_HEADER_SIZE  # 90

# How long a sent frame may sit unanswered before it's counted as lost and
# flushed out of the pending table as a TIMEOUT row. Well above any
# plausible LAN round-trip, short enough that real loss shows up in the
# live stats within a fraction of a second rather than sitting unflushed
# for FrameLogger's 5s PENDING_FLUSH_MS (tuned for a <=10Hz interactive
# context, not a ~1kHz one).
STALE_TIMEOUT_S = 0.3

# GUI-facing signals (stats/rows/LED matrix) are batched and emitted on this
# cadence, never per-frame - at 1kHz, per-frame signal delivery would mean
# per-frame Qt widget updates on the receiving end (HeaderPanel.show_frame()
# alone is ~30-60 widget-mutation calls), which is the actual GUI-thread
# bottleneck, not Qt's signal machinery itself.
_STATS_INTERVAL_S = 0.1

# Upper bound on how long a single loop iteration ever sleeps, so stop()
# (checked once per iteration) responds promptly even when the configured
# interval is much larger than this.
_MAX_LOOP_SLEEP_S = 0.05


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


def _format_row(msg_number, tx_timestamp, rx_timestamp, delay_us, qcc_command,
                 result, link_flags=None, raw_for_hex=None) -> list:
    """CSV row in core.frame_logger.CSV_COLUMNS order. rx_raw_hex is left
    empty unless raw_for_hex is passed - a full 2970-byte frame is ~6KB of
    hex, and at 1kHz sustained that's tens of GB/hour for a feature meant
    for long runs, so only CRC_FAIL/UNSOLICITED rows (the cases actually
    worth inspecting byte-for-byte) get it populated."""
    if link_flags is not None:
        not_ok = [i for i, ok in enumerate(link_flags) if not ok]
        qtrm_ok_count = str(NUM_QTRM - len(not_ok))
        qtrm_not_ok_list = ",".join(str(i) for i in not_ok)
        qtrm_cols = [QTRM_NOT_OK if i in not_ok else QTRM_OK for i in range(NUM_QTRM)]
    else:
        qtrm_ok_count = qtrm_not_ok_list = ""
        qtrm_cols = [""] * NUM_QTRM
    rx_raw_hex = raw_for_hex.hex() if raw_for_hex is not None else ""
    return [msg_number, tx_timestamp, rx_timestamp, delay_us, qcc_command, result,
            qtrm_ok_count, qtrm_not_ok_list, *qtrm_cols, rx_raw_hex]


class BurnTestWorker(QThread):
    PAYLOAD_LINK_TEST = "link_test"
    PAYLOAD_QCC_STATUS = "qcc_status"

    error = Signal(str)
    status = Signal(str)
    # object, not dict/list - a queued cross-thread connection (this worker
    # lives on its own QThread) silently fails to deliver plain dict/list
    # signal arguments unless registered with qRegisterMetaType; Signal(object)
    # is the standard way to pass an arbitrary Python object across threads
    # without that registration step.
    stats_ready = Signal(object)          # dict: sent/ok/timeouts/errors/achieved_hz/target_hz
    rows_ready = Signal(object)           # list: batched CSV-ready rows
    link_result_ready = Signal(object)    # list: 96 bools, most recent Link Test reply

    def __init__(self, qcc_ip: str, qcc_port: int, payload_kind: str,
                 interval_s: float, local_port: int, parent=None):
        super().__init__(parent)
        self.qcc_ip = qcc_ip
        self.qcc_port = qcc_port
        self.payload_kind = payload_kind
        self.interval_s = interval_s
        self.local_port = local_port
        self._running = False

    def run(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # Real QCC hardware sends its responses to the port it's
            # actually paired with (the interactive connection's Local
            # Port), not to whichever port a query happened to come from -
            # confirmed against real hardware, where binding to an
            # arbitrary ephemeral port (like UdpWorker does for a normal
            # connect) got 100% timeouts even though the interactive link
            # kept working fine on its own port. main_window.py pauses the
            # interactive UdpWorker and hands this exact port over for the
            # run's duration, then reconnects it afterward.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", self.local_port))
            sock.connect((self.qcc_ip, self.qcc_port))
            sock.setblocking(False)
        except OSError as e:
            self.error.emit(f"Burn Test could not open a UDP socket: {e}")
            # Always emit the "stopped" status exactly once per run(), on
            # every code path - main_window.py only closes BurnTestLogger in
            # response to this, so the caller can rely on it to know when
            # it's safe to close the log file (see status.emit at the very
            # end of the normal path below).
            self.status.emit("Burn Test stopped")
            return

        is_link_test = self.payload_kind == self.PAYLOAD_LINK_TEST
        command_id = COMMAND_ID_LINK_TEST if is_link_test else COMMAND_ID_QCC_STATUS
        build_frame = build_link_test_frame if is_link_test else build_header_only_frame

        self._running = True
        local_ip, local_port = sock.getsockname()
        self.status.emit(
            f"Burn Test running from {local_ip}:{local_port} to {self.qcc_ip}:{self.qcc_port}")

        pending = {}   # msg_number -> {"send_perf", "tx_timestamp", "qcc_command"}, insertion-ordered
        rows = []
        sent = ok = timeouts = errors = 0
        last_link_flags = None
        next_send = time.perf_counter()
        last_stats_tick = next_send
        sent_at_last_tick = 0

        while self._running:
            # 1. Drain every response currently available, never blocking.
            while True:
                try:
                    data = sock.recv(65536)
                except BlockingIOError:
                    break
                except OSError as e:
                    errors += 1
                    self.error.emit(f"Burn Test receive error: {e}")
                    break
                recv_perf = time.perf_counter()
                row, link_flags = self._handle_response(data, recv_perf, pending, is_link_test)
                if row is not None:
                    rows.append(row)
                    if row[5] == RESULT_OK:
                        ok += 1
                    else:
                        errors += 1
                if link_flags is not None:
                    last_link_flags = link_flags

            # 2. Send the next frame once its deadline has passed.
            now = time.perf_counter()
            if now >= next_send:
                msg_number = rc_settings.peek_message_number()
                header = rc_settings.build_header(command_id)
                try:
                    sock.send(build_frame(header))
                    pending[msg_number] = {
                        "send_perf": time.perf_counter(),
                        "tx_timestamp": _timestamp(),
                        "qcc_command": command_name(command_id),
                    }
                    sent += 1
                except OSError as e:
                    errors += 1
                    self.error.emit(f"Burn Test send error: {e}")
                next_send += self.interval_s
                if next_send < now:
                    # Fell behind by more than one interval - snap forward
                    # instead of bursting out a catch-up backlog.
                    next_send = now + self.interval_s

            # 3. Evict frames that have sat unanswered too long - lost
            # packets, logged as TIMEOUT. `pending` is insertion-ordered and
            # msg_number strictly increases, so the oldest entries are
            # always at the front; stop at the first still-fresh one.
            now = time.perf_counter()
            stale = []
            for msg_number, info in pending.items():
                if now - info["send_perf"] > STALE_TIMEOUT_S:
                    stale.append(msg_number)
                else:
                    break
            for msg_number in stale:
                info = pending.pop(msg_number)
                rows.append(_format_row(msg_number, info["tx_timestamp"], "", "",
                                         info["qcc_command"], RESULT_TIMEOUT))
                timeouts += 1

            # 4. Throttled aggregation - never per-frame.
            now = time.perf_counter()
            if now - last_stats_tick >= _STATS_INTERVAL_S:
                elapsed = now - last_stats_tick
                achieved_hz = (sent - sent_at_last_tick) / elapsed if elapsed > 0 else 0.0
                self.stats_ready.emit({
                    "sent": sent, "ok": ok, "timeouts": timeouts, "errors": errors,
                    "achieved_hz": achieved_hz,
                    "target_hz": (1.0 / self.interval_s) if self.interval_s > 0 else 0.0,
                })
                if rows:
                    self.rows_ready.emit(rows)
                    rows = []
                if last_link_flags is not None:
                    self.link_result_ready.emit(last_link_flags)
                    last_link_flags = None
                last_stats_tick = now
                sent_at_last_tick = sent

            # 5. Wait for the next deadline without overshooting it or
            # burning a full core spinning.
            sleep_for = min(next_send - time.perf_counter(), _MAX_LOOP_SLEEP_S)
            if sleep_for > 0.0005:
                time.sleep(sleep_for)

        # Flush anything still outstanding as TIMEOUT rows, same spirit as
        # FrameLogger.stop()'s "a stop loses at most the in-flight row(s)".
        for msg_number, info in pending.items():
            rows.append(_format_row(msg_number, info["tx_timestamp"], "", "",
                                     info["qcc_command"], RESULT_TIMEOUT))
            timeouts += 1
        if rows:
            self.rows_ready.emit(rows)
        self.stats_ready.emit({
            "sent": sent, "ok": ok, "timeouts": timeouts, "errors": errors,
            "achieved_hz": 0.0,
            "target_hz": (1.0 / self.interval_s) if self.interval_s > 0 else 0.0,
        })

        sock.close()
        # Queued signals from this thread are delivered to the GUI thread in
        # emission order, so by the time main_window.py's handler for THIS
        # status message runs, the rows_ready/stats_ready above (this
        # thread's very last ones) are already delivered - that's what lets
        # the caller close the log file here without losing the final batch.
        self.status.emit("Burn Test stopped")

    @staticmethod
    def _handle_response(data: bytes, recv_perf: float, pending: dict, is_link_test: bool):
        """Pair one received frame against `pending` by MESSAGE_NUMBER.
        Returns (csv_row_or_None, link_flags_or_None)."""
        if len(data) != TOTAL_PACKET_SIZE:
            return None, None
        header = QCCHeaderTx.from_bytes(data[:_HEADER_SIZE])
        rx_timestamp = _timestamp()
        link_flags = parse_link_test_response(data) if is_link_test else None

        info = pending.pop(header.message_number, None)
        if info is None:
            # No matching send (already evicted as TIMEOUT, or a genuinely
            # stray frame) - still worth a row so it isn't silently dropped.
            row = _format_row(header.message_number, "", rx_timestamp, "",
                               command_name(header.qcc_command), RESULT_UNSOLICITED,
                               link_flags, data)
            return row, link_flags

        delay_us = (recv_perf - info["send_perf"]) * 1_000_000
        result = RESULT_CRC_FAIL if not header.checksum_ok else RESULT_OK
        row = _format_row(header.message_number, info["tx_timestamp"], rx_timestamp,
                           f"{delay_us:.1f}", info["qcc_command"], result,
                           link_flags, data if result != RESULT_OK else None)
        return row, link_flags

    def stop(self):
        self._running = False
        self.wait(2000)
