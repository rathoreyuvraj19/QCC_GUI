"""
frame_logger.py

Streams every query/response pair the main GUI sends/receives into a CSV
file, one row per query, for long (hours/days) burn tests - the analysis
goal is "which packets went missing and how did the response delay behave",
so a row whose response columns are empty IS a missing packet.

Pairing: the header's MESSAGE_NUMBER (bytes 7-10, 1-indexed) increments on
every RC send and QCC echoes it back verbatim in its response (per
docs/idd/packet_spec.yaml), so it's the join key. The GUI's link is strictly
one-command-in-flight (main_window's _check_not_busy), so at most one query
is ever pending here.

Rows are appended and flushed to disk as they happen - a crash at hour 30
of a burn test loses at most the one in-flight row, and memory use stays
flat no matter how long the run is. Usage:

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
    FIXED_HEADER_SIZE, QCC_HEADER_SIZE, QCCHeaderRx, QCCHeaderTx,
)

_HEADER_SIZE = FIXED_HEADER_SIZE + QCC_HEADER_SIZE  # 90

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

CSV_COLUMNS = [
    "msg_number", "tx_timestamp", "rx_timestamp", "delay_us",
    "qcc_command", "result", "tx_raw_hex", "rx_raw_hex",
]

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
    # (rows_written, ok, missing, errors) - "missing" is TIMEOUT rows,
    # "errors" is CRC_FAIL + MSG_NUM_MISMATCH + UNSOLICITED.
    stats_changed = Signal(int, int, int, int)
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._file = None
        self._writer = None
        self.path = None
        self._pending = None
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self._on_pending_expired)
        self.rows = self.ok = self.missing = self.errors = 0

    @property
    def active(self) -> bool:
        return self._file is not None

    def start(self, path: str):
        """Open path and begin logging. Returns None, or an error message."""
        self.stop()
        try:
            f = open(path, "w", newline="")
            writer = csv.writer(f)
            writer.writerow(CSV_COLUMNS)
            f.flush()
        except OSError as e:
            return str(e)
        self._file, self._writer, self.path = f, writer, path
        self._pending = None
        self.rows = self.ok = self.missing = self.errors = 0
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
            "tx_raw_hex": raw.hex(),
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
            # during analysis.
            self._write_row([
                header.message_number, "", rx_timestamp, delay,
                command_name(header.qcc_command), RESULT_UNSOLICITED,
                "", raw.hex(),
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
            pending["tx_raw_hex"], raw.hex(),
        ])
        self._emit_stats()

    # -- internals ---------------------------------------------------------

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
            pending["qcc_command"], result, pending["tx_raw_hex"], "",
        ])

    def _write_row(self, row):
        if self._writer is None:
            return
        try:
            self._writer.writerow(row)
            self._file.flush()
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
        self.stats_changed.emit(self.rows, self.ok, self.missing, self.errors)
