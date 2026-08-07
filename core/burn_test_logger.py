"""
burn_test_logger.py

CSV writer for the Burn Test tab's fire-and-forget, sustained (~1ms/frame)
UDP stream - a separate write path from FrameLogger (core/frame_logger.py),
which assumes at most one query in flight at a time and can't correctly
pair frames once several are outstanding (its single `_pending` slot gets
stomped by the very next send). Burn Test frames are instead paired by
MESSAGE_NUMBER inside core/burn_test_worker.py itself; this class only
turns already-paired rows into CSV rows on disk, batched (one
writerows()+flush() per call) rather than FrameLogger's per-row flush,
which would be a syscall-per-frame bottleneck at ~1kHz.

Reuses FrameLogger's exact CSV_COLUMNS (read-only import, never mutated)
so Burn Test CSVs load into the existing apps/plot_qcc_log.py /
widgets/plot_log_dialog.py analysis tool unmodified.

Usage:
    logger = BurnTestLogger(parent)
    logger.error.connect(on_error)         # str - logging already stopped
    err = logger.start("/path/to/log.csv")  # None on success, message on failure
    logger.append_rows(rows)                # from BurnTestWorker.rows_ready
    logger.stop()
"""

import csv

from PySide6.QtCore import QObject, Signal

from core.frame_logger import CSV_COLUMNS


class BurnTestLogger(QObject):
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._file = None
        self._writer = None
        self.path = None

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
        return None

    def append_rows(self, rows: list):
        """One writerows()+flush() per batch, not per-row - FrameLogger's
        per-row flush is fine at its <=10Hz interactive cadence but would be
        a syscall-per-frame bottleneck at Burn Test's ~1kHz."""
        if self._writer is None or not rows:
            return
        try:
            self._writer.writerows(rows)
            self._file.flush()
        except OSError as e:
            self._file = self._writer = None
            self.error.emit(f"Burn Test logging stopped - could not write to {self.path}: {e}")

    def stop(self):
        if self._file is None:
            return
        try:
            self._file.close()
        except OSError:
            pass
        self._file = self._writer = None
