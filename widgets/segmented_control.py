"""
segmented_control.py

A two-option segmented control (pill container with two mutually-exclusive
buttons) - a more modern, self-labeled alternative to a bare sliding switch
when both options already need a text label next to it (e.g. "Rx Isolation"
/ "Tx Isolation"): the labels ARE the control here instead of sitting beside
it.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QSizePolicy, QWidget

_TRACK_BG = "#2c333a"
_BORDER = "#4a515a"
_ACCENT = "#00adb5"
_TEXT = "#eeeeee"
_TEXT_MUTED = "rgba(238, 238, 238, 0.55)"

_SEGMENT_RADIUS = 10


class SegmentedControl(QWidget):
    """toggled(bool): False = left option selected, True = right option selected."""

    toggled = Signal(bool)

    def __init__(self, left_text: str, right_text: str, parent=None):
        super().__init__(parent)
        self._checked = False
        self.setFixedHeight(44)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setStyleSheet(
            f"SegmentedControl {{ background-color: {_TRACK_BG};"
            f"border: 1px solid {_BORDER}; border-radius: {_SEGMENT_RADIUS + 3}px; }}"
        )

        outer = QHBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        self.left_btn = QPushButton(left_text)
        self.right_btn = QPushButton(right_text)
        for btn in (self.left_btn, self.right_btn):
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            outer.addWidget(btn)

        self.left_btn.clicked.connect(lambda: self._select(False))
        self.right_btn.clicked.connect(lambda: self._select(True))
        self._select(False, emit=False)

    def _select(self, right: bool, emit: bool = True):
        self._checked = right
        self.left_btn.setChecked(not right)
        self.right_btn.setChecked(right)
        self._restyle(self.left_btn, selected=not right)
        self._restyle(self.right_btn, selected=right)
        if emit:
            self.toggled.emit(right)

    @staticmethod
    def _restyle(btn: QPushButton, selected: bool):
        if selected:
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {_ACCENT}; color: {_TEXT};"
                f"border: none; border-radius: {_SEGMENT_RADIUS}px; font-weight: 600; padding: 8px 12px; }}"
            )
        else:
            btn.setStyleSheet(
                f"QPushButton {{ background-color: transparent; color: {_TEXT_MUTED};"
                f"border: none; border-radius: {_SEGMENT_RADIUS}px; font-weight: 600; padding: 8px 12px; }}"
                f"QPushButton:hover {{ background-color: rgba(238, 238, 238, 0.06); }}"
            )

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, right: bool):
        if right != self._checked:
            self._select(right)
