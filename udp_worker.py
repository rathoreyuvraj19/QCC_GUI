"""
udp_worker.py

Background UDP send/receive so the GUI thread never blocks on socket I/O.

Usage:
    worker = UdpWorker(local_port=50000, qcc_ip="192.168.1.50", qcc_port=50001)
    worker.frame_received.connect(on_frame_received)   # bytes
    worker.frame_sent.connect(on_frame_sent)           # bytes - fires on every successful sendto
    worker.error.connect(on_error)                     # str
    worker.start()
    ...
    worker.send_frame(some_2970_byte_bytes_object)
    ...
    worker.stop()
"""

import socket

from PySide6.QtCore import QThread, Signal

from packet import TOTAL_PACKET_SIZE


class UdpWorker(QThread):
    frame_received = Signal(bytes)
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

            self.frame_received.emit(data)

        if self._sock:
            self._sock.close()
        self.status.emit("Stopped")

    def send_frame(self, frame: bytes):
        if len(frame) != TOTAL_PACKET_SIZE:
            self.error.emit(f"Refusing to send {len(frame)}-byte frame, expected {TOTAL_PACKET_SIZE}")
            return
        if self._sock is None:
            self.error.emit("Cannot send - socket not open yet")
            return
        try:
            self._sock.sendto(frame, (self.qcc_ip, self.qcc_port))
            self.frame_sent.emit(frame)
        except OSError as e:
            self.error.emit(f"Failed to send frame: {e}")

    def stop(self):
        self._running = False
        self.wait(2000)
