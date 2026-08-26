"""
frame_logger.py

Streams every query/response pair the main GUI sends/receives into a CSV
file, one row per query, for long (hours/days) burn tests - the analysis
goal is "which packets went missing, how did the response delay behave,
and which LRUs answered", so a row whose response columns are empty IS a
missing packet.

Burn tests run Link Test frames: for those rows the logger additionally
validates each queried LRU's reply slot and marks it OK/NOT_OK in per-LRU
columns (lru_00..lru_95 for the default 96-LRU array, plus lru_ok_count/
lru_not_ok_list summaries). Other commands still get the timing/result/
raw-hex columns, just with the lru_* columns empty - Link Test is the only
command whose per-LRU reply format this logger understands.

How many lru_* columns a file has, and how wide their index is, depends on
the configured array shape (core/lru_config.py) - which is why the column
list is built when a log is opened rather than at import.

Pairing: the header's MESSAGE_NUMBER (bytes 7-10, 1-indexed) increments on
every RC send and QCC echoes it back verbatim in its response (per
docs/idd/packet_spec.yaml), so it's the join key. The GUI's link is strictly
one-command-in-flight (main_window's _check_not_busy), so at most one query
is ever pending here.

Rows are appended and flushed to disk every _FLUSH_EVERY_N_ROWS rows (see
below) - a crash loses at most that many trailing rows, and memory use
stays flat no matter how long the run is. Usage:

    logger = FrameLogger(parent)
    logger.stats_changed.connect(on_stats)   # (rows, ok, missing, errors)
    logger.error.connect(on_error)           # str - logging already stopped
    err = logger.start("/path/to/log.csv")   # None on success, message on failure
    logger.log_tx(raw_frame)                 # from the worker's frame_sent
    logger.log_rx(raw_frame, elapsed_us)     # from the worker's frame_received
    logger.stop()
"""

import csv
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal

from core.packet import (
    HEADER_SIZE, QCCHeaderRx, QCCHeaderTx, build_link_query_slot,
    lru_slot_size, num_lru, parse_link_test_response, total_packet_size,
)

_HEADER_SIZE = HEADER_SIZE  # 90, fixed regardless of the array's shape

LRU_OK = "OK"
LRU_NOT_OK = "NOT_OK"

# How long a sent query may sit unanswered (with no newer query flushing it
# out first) before its row is written as TIMEOUT. Deliberately longer than
# main_window's RESPONSE_TIMEOUT_MS (1 s) so a response that arrives after
# the GUI already gave up still pairs with its query in the log instead of
# splitting into a TIMEOUT row + an UNSOLICITED row.
PENDING_FLUSH_MS = 5000

RESULT_OK = "OK"
RESULT_TIMEOUT = "TIMEOUT"
RESULT_CRC_FAIL = "CRC_FAIL"
RESULT_MSG_NUM_MISMATCH = "MSG_NUM_MISMATCH"
RESULT_UNSOLICITED = "UNSOLICITED"

# Rows between explicit fsyncs. A crash loses at most this many rows
# instead of one - fine for a burn-test log - in exchange for not making
# every single frame at a fast resend rate (e.g. Dwell tab's 0.01s
# auto-resend) pay for a synchronous disk flush, which was slow enough to
# make the GUI thread fall behind the worker thread's frame_received
# signals and back them up in Qt's event queue (unbounded RAM growth,
# apparent hang after ~2e5 frames).
_FLUSH_EVERY_N_ROWS = 50

# lru_ok_count/lru_not_ok_list summarize the per-LRU columns for quick
# filtering (a non-empty lru_not_ok_list cell = at least one LRU failed
# that Link Test). lru_00..lru_95 (at the default 96-LRU shape) are OK /
# NOT_OK per queried LRU (0-indexed, matching the Link Test tab's LED
# labels), empty where that LRU wasn't queried, the row isn't a Link Test,
# or the whole frame timed out (a TIMEOUT row means NO LRU answered - count
# those separately rather than as N individual failures).
# No tx_raw_hex column: the query frame is not stored at all (burn-test
# Link Test queries are byte-identical every send, ~6 KB/row of dead
# weight) - tx_timestamp/msg_number/qcc_command capture the send side.

_FIXED_COLUMNS = [
    "msg_number", "tx_timestamp", "rx_timestamp", "delay_us",
    "qcc_command", "result", "lru_ok_count", "lru_not_ok_list",
]


def lru_index_width() -> int:
    """
    Zero-padding width for the lru_NN column names. Two digits for anything
    up to 100 LRUs, so the default array keeps the familiar lru_00..lru_95;
    wider only when the indices actually need it.
    """
    return max(2, len(str(num_lru() - 1)))


def lru_column_names() -> list:
    width = lru_index_width()
    return [f"lru_{i:0{width}d}" for i in range(num_lru())]


def csv_columns() -> list:
    """
    The header row for a new log. Built per call rather than once at import:
    how many lru_* columns there are depends on the configured array shape.
    """
    return [*_FIXED_COLUMNS, *lru_column_names(), "rx_raw_hex"]


def _empty_lru_cols() -> list:
    """Blank cells for lru_ok_count, lru_not_ok_list, and every lru_NN."""
    return [""] * (2 + num_lru())


def _link_queried_indices(raw: bytes) -> tuple:
    """0-based LRU indices whose TX slot is the canonical Link query."""
    if len(raw) != total_packet_size():
        return ()
    # A TX frame is recognized as a Link Test by byte-exact slot comparison
    # against the canonical query, so the "which LRUs were queried" set
    # falls out for free (every LRU for Send Link Test, one for an
    # individual-LED click). Per-LRU OK/NOT_OK analysis is only defined for
    # Link Test frames; every other command's rows leave the lru_* columns
    # empty (the response hex still captures the frame for offline
    # re-analysis).
    link_slot = build_link_query_slot()
    slot_size = lru_slot_size()
    return tuple(
        i for i in range(num_lru())
        if raw[_HEADER_SIZE + i * slot_size:
               _HEADER_SIZE + (i + 1) * slot_size] == link_slot
    )

_COMMAND_NAMES = {
    QCCHeaderRx.QCC_COMMAND_DATA_DISTRIBUTION: "DATA_DISTRIBUTION",
    QCCHeaderRx.QCC_COMMAND_QCC_STATUS: "QCC_STATUS",
    QCCHeaderRx.QCC_COMMAND_QCC_RESET: "QCC_RESET",
    QCCHeaderRx.QCC_COMMAND_PRT_BYPASS: "PRT_BYPASS",
    QCCHeaderRx.QCC_COMMAND_SOB_BYPASS: "SOB_BYPASS",
    QCCHeaderRx.QCC_COMMAND_PRT_INTERNAL_GEN: "PRT_INTERNAL_GEN",
    QCCHeaderRx.QCC_COMMAND_SOB_INTERNAL_GEN: "SOB_INTERNAL_GEN",
    QCCHeaderRx.QCC_COMMAND_PPS_INTERNAL_GEN: "PPS_INTERNAL_GEN",
    QCCHeaderRx.QCC_COMMAND_REMOTE_PROGRAMMING: "REMOTE_PROGRAMMING",
}


def command_name(value: int) -> str:
    return f"{_COMMAND_NAMES.get(value, 'UNKNOWN')} (0x{value:02X})"


class FrameLogger(QObject):
    # (rows_written, ok, missing, errors, lru_fails) - "missing" is TIMEOUT
    # rows, "errors" is CRC_FAIL + MSG_NUM_MISMATCH + UNSOLICITED,
    # "lru_fails" is the running total of NOT_OK marks across all Link
    # Test rows that DID get a response (whole-frame timeouts count under
    # "missing", not here).
    stats_changed = Signal(int, int, int, int, int)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._file = None
        self._writer = None
        self.path = None
        self._pending = None
        self._unflushed = 0
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self._on_pending_expired)
        self.rows = self.ok = self.missing = self.errors = self.lru_fails = 0

    @property
    def active(self) -> bool:
        return self._file is not None

    def start(self, path: str):
        """Open path and begin logging. Returns None, or an error message."""
        self.stop()
        try:
            f = open(path, "w", newline="")
            writer = csv.writer(f)
            writer.writerow(csv_columns())
            f.flush()
        except OSError as e:
            return str(e)
        self._file, self._writer, self.path = f, writer, path
        self._pending = None
        self._unflushed = 0
        self.rows = self.ok = self.missing = self.errors = self.lru_fails = 0
        return None

    def stop(self):
        """Flush any still-pending query as TIMEOUT and close the file."""
        if self._file is None:
            return
        if self._pending is not None:
            self._flush_pending(RESULT_TIMEOUT)
        self._flush_timer.stop()
        try:
            self._file.close()
        except OSError:
            pass
        self._file = self._writer = None

    # -- feed points -------------------------------------------------------

    def log_tx(self, raw: bytes):
        if not self.active:
            return
        # A new query going out while the previous one never got a response
        # means that previous one is a missing packet - write it out now.
        if self._pending is not None:
            self._flush_pending(RESULT_TIMEOUT)
        header = QCCHeaderRx.from_bytes(raw[:_HEADER_SIZE])
        # Wall-clock stamp is taken here on the GUI thread (a hair after the
        # actual sendto in the worker thread) - good to well under a
        # millisecond, and only used for the human-readable timeline. The
        # microsecond-accurate round-trip number is elapsed_us in log_rx,
        # measured at the real socket calls in udp_worker.py.
        self._pending = {
            "msg_number": header.message_number,
            "tx_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
            "qcc_command": command_name(header.qcc_command),
            # Empty tuple = not a Link Test - the row's lru_* columns stay
            # blank. Non-empty = which LRUs this query addressed, so the
            # response can be marked OK/NOT_OK per queried LRU.
            "link_queried": _link_queried_indices(raw),
        }
        self._flush_timer.start(PENDING_FLUSH_MS)

    def log_rx(self, raw: bytes, elapsed_us: float):
        if not self.active:
            return
        rx_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        header = QCCHeaderTx.from_bytes(raw[:_HEADER_SIZE])
        delay = f"{elapsed_us:.1f}" if elapsed_us >= 0 else ""

        if self._pending is None:
            # A response with nothing in flight (e.g. arrived after its
            # query was already flushed as TIMEOUT) - its own row, so the
            # frame is still in the log and can be re-paired by msg_number
            # during analysis. No lru_* marks: without the query we don't
            # know which LRUs were addressed.
            self._write_row([
                header.message_number, "", rx_timestamp, delay,
                command_name(header.qcc_command), RESULT_UNSOLICITED,
                *_empty_lru_cols(), raw.hex(),
            ])
            self.errors += 1
            self._emit_stats()
            return

        pending, self._pending = self._pending, None
        self._flush_timer.stop()
        if not header.checksum_ok:
            result = RESULT_CRC_FAIL
            self.errors += 1
        elif header.message_number != pending["msg_number"]:
            result = RESULT_MSG_NUM_MISMATCH
            self.errors += 1
        else:
            result = RESULT_OK
            self.ok += 1
        self._write_row([
            pending["msg_number"], pending["tx_timestamp"], rx_timestamp,
            delay, pending["qcc_command"], result,
            *self._lru_columns(pending["link_queried"], raw),
            raw.hex(),
        ])
        self._emit_stats()

    # -- internals ---------------------------------------------------------

    def _lru_columns(self, queried: tuple, rx_raw: bytes) -> list:
        """
        [lru_ok_count, lru_not_ok_list, one column per LRU] for a paired
        response. All-empty unless the query was a Link Test and the
        response is a full frame; then each queried LRU's slot is checked
        with the same validity rules the Link Test tab's LEDs use (header
        byte + XOR checksum + link sentinel).
        """
        if not queried or len(rx_raw) != total_packet_size():
            return _empty_lru_cols()
        slot_ok = parse_link_test_response(rx_raw)
        cols = [""] * num_lru()
        not_ok = []
        for i in queried:
            if slot_ok[i]:
                cols[i] = LRU_OK
            else:
                cols[i] = LRU_NOT_OK
                not_ok.append(i)
        self.lru_fails += len(not_ok)
        return [str(len(queried) - len(not_ok)),
                ",".join(str(i) for i in not_ok), *cols]

    def _on_pending_expired(self):
        if self.active and self._pending is not None:
            self._flush_pending(RESULT_TIMEOUT)
            self._emit_stats()

    def _flush_pending(self, result: str):
        pending, self._pending = self._pending, None
        self._flush_timer.stop()
        self.missing += 1
        self._write_row([
            pending["msg_number"], pending["tx_timestamp"], "", "",
            pending["qcc_command"], result, *_empty_lru_cols(), "",
        ])

    def _write_row(self, row):
        if self._writer is None:
            return
        try:
            self._writer.writerow(row)
            self._unflushed += 1
            if self._unflushed >= _FLUSH_EVERY_N_ROWS:
                self._file.flush()
                self._unflushed = 0
        except OSError as e:
            # Disk full / file yanked mid-run - stop cleanly rather than
            # erroring on every subsequent frame.
            self._file = self._writer = None
            self._flush_timer.stop()
            self._pending = None
            self.error.emit(f"Data logging stopped - could not write to {self.path}: {e}")
            return
        self.rows += 1

    def _emit_stats(self):
        self.stats_changed.emit(self.rows, self.ok, self.missing, self.errors,
                                self.lru_fails)
