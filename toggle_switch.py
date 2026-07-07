"""
toggle_switch.py

An iOS-style sliding toggle switch (rounded track + animated circular knob) -
used where a checkbox would otherwise read as a plain on/off tickbox but the
control actually represents a real state change worth a more deliberate,
"flip this on" feel (e.g. Timing Generation's Infinite PRT switch).
"""

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractButton, QSizePolicy

_TRACK_OFF = QColor("#4a515a")
_TRACK_ON = QColor("#00adb5")
_KNOB_COLOR = QColor("#eeeeee")
_BORDER = QColor("#2c333a")

_WIDTH = 44
_HEIGHT = 24
_MARGIN = 2
_KNOB_TRAVEL_END = float(_WIDTH - _HEIGHT + _MARGIN)


class ToggleSwitch(QAbstractButton):
    """
    Checkable QAbstractButton - use isChecked()/setChecked()/toggled exactly
    like QCheckBox (toggled(bool) is QAbstractButton's own built-in signal,
    fired on both user clicks and setChecked() calls - not re-declared here).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedSize(_WIDTH, _HEIGHT)

        self._knob_pos = float(_MARGIN)
        self._anim = QPropertyAnimation(self, b"knob_pos", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

        self.toggled.connect(self._animate_to)

    def _animate_to(self, checked: bool):
        end = _KNOB_TRAVEL_END if checked else float(_MARGIN)
        self._anim.stop()
        self._anim.setStartValue(self._knob_pos)
        self._anim.setEndValue(end)
        self._anim.start()

    def _get_knob_pos(self):
        return self._knob_pos

    def _set_knob_pos(self, pos):
        self._knob_pos = pos
        self.update()

    knob_pos = Property(float, _get_knob_pos, _set_knob_pos)

    def sizeHint(self):
        return self.size()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        track_rect = QRectF(0, 0, _WIDTH, _HEIGHT)
        track_color = _TRACK_ON if self.isChecked() else _TRACK_OFF
        painter.setPen(_BORDER)
        painter.setBrush(track_color)
        painter.drawRoundedRect(track_rect, _HEIGHT / 2, _HEIGHT / 2)

        knob_diameter = _HEIGHT - 2 * _MARGIN
        knob_rect = QRectF(self._knob_pos, _MARGIN, knob_diameter, knob_diameter)
        painter.setPen(Qt.NoPen)
        painter.setBrush(_KNOB_COLOR)
        painter.drawEllipse(knob_rect)
