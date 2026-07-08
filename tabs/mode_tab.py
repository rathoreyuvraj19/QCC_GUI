"""
mode_tab.py

Reusable "one QCC operation" tab, one instance per MODE value (0-5). Each
tab exposes the raw 55-byte COMMAND_DATA as hex text, since per-mode field
layouts are still TBD (see README "Current state"). Sending combines this
tab's MODE + COMMAND_DATA with whatever is currently in the shared 96-row
QTRM grid.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QMessageBox,
)


class ModeTab(QWidget):
    send_requested = Signal(int, bytes)  # mode_value, command_data (<=55 bytes)

    def __init__(self, mode_value: int, title: str, description: str, confirm: bool = False, parent=None):
        super().__init__(parent)
        self.mode_value = mode_value
        self.confirm = confirm

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>{title}</b>  (MODE = {mode_value})"))

        desc_label = QLabel(description)
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        cmd_row = QHBoxLayout()
        cmd_row.addWidget(QLabel("COMMAND_DATA (hex, ≤ 55 bytes, fields TBD):"))
        self.command_data_edit = QLineEdit()
        self.command_data_edit.setPlaceholderText("e.g. AA 01 FF (leave blank for all-zero)")
        cmd_row.addWidget(self.command_data_edit)
        layout.addLayout(cmd_row)

        self.send_btn = QPushButton(f"Send {title}")
        self.send_btn.clicked.connect(self._on_send_clicked)
        layout.addWidget(self.send_btn)
        layout.addStretch(1)

    def _on_send_clicked(self):
        text = self.command_data_edit.text().strip().replace(" ", "")
        try:
            command_data = bytes.fromhex(text) if text else b""
        except ValueError:
            QMessageBox.warning(
                self, "Invalid COMMAND_DATA",
                "Enter valid hex bytes (e.g. 'AA 01 FF') or leave blank.",
            )
            return
        if len(command_data) > 55:
            QMessageBox.warning(self, "Invalid COMMAND_DATA", "COMMAND_DATA must be at most 55 bytes.")
            return

        if self.confirm:
            resp = QMessageBox.question(
                self, "Confirm", f"{self.send_btn.text()} - send to QCC now?",
            )
            if resp != QMessageBox.Yes:
                return

        self.send_requested.emit(self.mode_value, command_data)
