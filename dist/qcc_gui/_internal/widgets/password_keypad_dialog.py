"""
password_keypad_dialog.py

Numeric keypad dialog for entering the Memory Operation / Remote
Programming tab-lock password (see main_window.py's MEMORY_TAB_PASSWORD).
Password is a short digit string, so a mouse/touch-friendly 0-9 keypad
beats typing into a QLineEdit - main_window.py used QInputDialog.getText
with a text keyboard requirement before this.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLineEdit, QPushButton, QLabel,
)

from core.command_style import send_button_style, SEND_COLOR, SEND_HOVER_COLOR, SEND_PRESSED_COLOR

_KEYPAD_BUTTON_STYLE = (
    "QPushButton { background-color: rgb(222, 224, 227); color: #1f2328; border: none;"
    "border-radius: 8px; padding: 12px; font-size: 14pt; font-weight: 600; }"
    "QPushButton:hover { background-color: rgb(200, 203, 208); }"
    "QPushButton:pressed { background-color: rgb(180, 184, 190); }"
)

_CLEAR_BUTTON_STYLE = (
    "QPushButton { background-color: rgb(222, 224, 227); color: #1f2328; border: none;"
    "border-radius: 8px; padding: 12px; font-size: 11pt; font-weight: 600; }"
    "QPushButton:hover { background-color: rgb(200, 203, 208); }"
    "QPushButton:pressed { background-color: rgb(180, 184, 190); }"
)


class PasswordKeypadDialog(QDialog):
    """Modal dialog: masked display + 0-9/Backspace/Clear keypad + OK/Cancel."""

    def __init__(self, parent, title: str, prompt: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._entered = ""

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(prompt))

        self._display = QLineEdit()
        self._display.setEchoMode(QLineEdit.Password)
        self._display.setReadOnly(True)
        self._display.setAlignment(Qt.AlignCenter)
        self._display.setStyleSheet("QLineEdit { font-size: 16pt; padding: 8px; }")
        layout.addWidget(self._display)

        grid = QGridLayout()
        grid.setSpacing(6)
        positions = [(0, 0), (0, 1), (0, 2),
                     (1, 0), (1, 1), (1, 2),
                     (2, 0), (2, 1), (2, 2)]
        for digit, (row, col) in zip("123456789", positions):
            btn = QPushButton(digit)
            btn.setStyleSheet(_KEYPAD_BUTTON_STYLE)
            btn.clicked.connect(lambda _checked, d=digit: self._append_digit(d))
            grid.addWidget(btn, row, col)

        backspace_btn = QPushButton("⌫")
        backspace_btn.setStyleSheet(_CLEAR_BUTTON_STYLE)
        backspace_btn.clicked.connect(self._backspace)
        grid.addWidget(backspace_btn, 3, 0)

        zero_btn = QPushButton("0")
        zero_btn.setStyleSheet(_KEYPAD_BUTTON_STYLE)
        zero_btn.clicked.connect(lambda: self._append_digit("0"))
        grid.addWidget(zero_btn, 3, 1)

        clear_btn = QPushButton("C")
        clear_btn.setStyleSheet(_CLEAR_BUTTON_STYLE)
        clear_btn.clicked.connect(self._clear)
        grid.addWidget(clear_btn, 3, 2)

        layout.addLayout(grid)

        button_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("OK")
        ok_btn.setStyleSheet(send_button_style(SEND_COLOR, SEND_HOVER_COLOR, SEND_PRESSED_COLOR))
        ok_btn.clicked.connect(self.accept)
        button_row.addWidget(cancel_btn)
        button_row.addWidget(ok_btn)
        layout.addLayout(button_row)

    def _append_digit(self, digit: str):
        self._entered += digit
        self._display.setText(self._entered)

    def _backspace(self):
        self._entered = self._entered[:-1]
        self._display.setText(self._entered)

    def _clear(self):
        self._entered = ""
        self._display.setText(self._entered)

    def keyPressEvent(self, event):
        # Physical keyboard still works alongside the on-screen keypad.
        key = event.key()
        if Qt.Key_0 <= key <= Qt.Key_9:
            self._append_digit(chr(key))
        elif key == Qt.Key_Backspace:
            self._backspace()
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self.accept()
            return
        else:
            super().keyPressEvent(event)

    def entered_password(self) -> str:
        return self._entered

    @staticmethod
    def get_password(parent, title: str, prompt: str):
        """Returns (password: str, accepted: bool), mirroring QInputDialog.getText."""
        dialog = PasswordKeypadDialog(parent, title, prompt)
        accepted = dialog.exec() == QDialog.Accepted
        return dialog.entered_password(), accepted
