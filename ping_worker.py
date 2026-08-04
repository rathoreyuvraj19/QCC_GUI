"""
ping_worker.py

Background ICMP ping check (Windows `ping.exe`) so the GUI thread never
blocks waiting for a reply or timeout.
"""

import re
import subprocess

from PySide6.QtCore import QThread, Signal

_TIME_RE = re.compile(r"time[<=]\d+ms", re.IGNORECASE)


class PingWorker(QThread):
    result = Signal(bool, str)  # (success, latency text e.g. "time<1ms")

    def __init__(self, host: str, parent=None):
        super().__init__(parent)
        self.host = host

    def run(self):
        try:
            proc = subprocess.run(
                ["ping", "-n", "1", "-w", "1000", self.host],
                capture_output=True, text=True, timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            self.result.emit(False, "ping timed out")
            return
        except OSError as e:
            # ping.exe missing or not launchable - report why rather than
            # collapsing every failure into one opaque message.
            self.result.emit(False, f"ping failed to run: {e.strerror or e}")
            return

        match = _TIME_RE.search(proc.stdout)
        if proc.returncode == 0 and match:
            self.result.emit(True, match.group(0))
        else:
            self.result.emit(False, "no reply")
