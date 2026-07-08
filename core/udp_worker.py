"""
udp_worker.py

Background UDP send/receive so the GUI thread never blocks on socket I/O.

Usage:
    worker = UdpWorker(local_port=50000, qcc_ip="192.168.1.50", qcc_port=50001)
    worker.frame_received.connect(on_frame_received)   # (bytes, elapsed_us: float)
    worker.frame_sent.connect(on_frame_sent)           # bytes - fires on every successful sendto
    worker.error.connect(on_error)                     # str
    worker.start()
    ...
    worker.send_frame(some_2970_byte_bytes_object)
    ...
    worker.stop()
"""

import socket
import time

from PySide6.QtCore import QThread, Signal

from core.packet import RP_FRAME_SIZE, TOTAL_PACKET_SIZE

# Every TX frame is 2970 bytes except Remote Programming's 4196-byte
# [header + 4096 payload + 10 inner command] frames. RX is always 2970.
_VALID_TX_SIZES = (TOTAL_PACKET_SIZE, RP_FRAME_SIZE)


class UdpWorker(QThread):
    # (raw frame, elapsed microseconds since the most recent send_frame()
    # call, or -1.0 if there wasn't one to time against - e.g. a stray/
    # unsolicited frame). Timestamped right at the actual socket calls,
    # inside this worker thread - not by whichever GUI code eventually
    # handles the signal - so it reflects real wire time, not GUI
    # processing time or Qt's cross-thread signal-dispatch latency.
    frame_received = Signal(bytes, float)
    frame_sent = Signal(bytes)
    error = Signal(str)
    status = Signal(str)

    def __init__(self, local_port: int, qcc_ip: str, qcc_port: int, parent=None):
        super().__init__(parent)
        self.local_port = local_port
        self.qcc_ip = qcc_ip
        self.qcc_port = qcc_port
        self._sock = None
        self._running = False
        self._last_send_time = None

    def run(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", self.local_port))
            self._sock.settimeout(0.5)  # allow periodic check of self._running
        except OSError as e:
            self.error.emit(f"Failed to bind local UDP port {self.local_port}: {e}")
            return

        self._running = True
        self.status.emit(f"Listening on 0.0.0.0:{self.local_port}")

        while self._running:
            try:
                data, addr = self._sock.recvfrom(65536)
                recv_time = time.perf_counter()
            except socket.timeout:
                continue
            except OSError as e:
                if self._running:
                    self.error.emit(f"Socket error while receiving: {e}")
                break

            if len(data) != TOTAL_PACKET_SIZE:
                self.error.emit(
                    f"Received {len(data)} bytes from {addr}, expected {TOTAL_PACKET_SIZE} - dropped"
                )
                continue

            if self._last_send_time is not None:
                elapsed_us = (recv_time - self._last_send_time) * 1_000_000
                self._last_send_time = None  # consumed - a later stray frame shouldn't reuse it
            else:
                elapsed_us = -1.0
            self.frame_received.emit(data, elapsed_us)

        if self._sock:
            self._sock.close()
        self.status.emit("Stopped")

    def send_frame(self, frame: bytes):
        if len(frame) not in _VALID_TX_SIZES:
            self.error.emit(
                f"Refusing to send {len(frame)}-byte frame, expected one of {_VALID_TX_SIZES}"
            )
            return
        if self._sock is None:
            self.error.emit("Cannot send - socket not open yet")
            return
        try:
            self._last_send_time = time.perf_counter()
            self._sock.sendto(frame, (self.qcc_ip, self.qcc_port))
            self.frame_sent.emit(frame)
        except OSError as e:
            self._last_send_time = None
            self.error.emit(f"Failed to send frame: {e}")

    def stop(self):
        self._running = False
        self.wait(2000)
