"""
qcc_tile.py

One tile in the Multi-QCC window - a thin header (title + Remove) wrapped
around a full embedded MainWindow instance, so every QCC gets its own
complete set of tabs (Dwell, Link Test, Status, RX/TX Cal, Isolation, Soft
Reset, Memory Operation, Timing Generation, RC Settings) with its own
independent connection. Remote Programming is left out for now (see
MainWindow's enable_remote_programming flag).
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

_BOX_STYLE = (
    "QGroupBox { background-color: #2a2f37; border: 1px solid #4a515a;"
    "border-radius: 8px; margin-top: 10px; padding: 4px; }"
    "QGroupBox::title { color: #00adb5; subcontrol-origin: margin; left: 8px; }"
)


class QccTile(QGroupBox):
    remove_requested = Signal(object)  # self

    def __init__(self, title: str, qcc_ip: str, qcc_port: int, local_port: int, parent=None):
        super().__init__(title, parent)
        self.setStyleSheet(_BOX_STYLE)

        # Local import - avoids a circular import (main_window.py imports
        # MultiQccWindow, which imports this module, to open the Multi-QCC
        # Tools-menu action).
        from main_window import MainWindow

        self.main_window = MainWindow(
            enable_remote_programming=False,
            initial_ip=qcc_ip,
            initial_port=qcc_port,
            initial_local_port=local_port,
            auto_connect=True,
            embedded=True,
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        header = QHBoxLayout()
        header.addWidget(QLabel(""), 1)  # keeps Remove pinned right, title is the QGroupBox's own
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.clicked.connect(lambda: self.remove_requested.emit(self))
        header.addWidget(self.remove_btn)
        root.addLayout(header)

        root.addWidget(self.main_window)

    def shutdown(self):
        self.main_window.shutdown()
