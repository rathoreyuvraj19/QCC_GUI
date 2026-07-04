"""
spin_field.py

A spin box (integer or decimal) paired with two custom, always-visible
up/down arrow buttons.

Qt's native spin arrows are drawn via a palette-based style primitive that
doesn't reliably contrast against a custom dark stylesheet - even explicit
QSS border-triangle tricks on ::up-arrow/::down-arrow don't render for this
particular subcontrol on Windows/Fusion. Real Unicode-glyph QToolButtons
sidestep that entirely since QSS `color` does apply reliably to them.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox, QDoubleSpinBox, QHBoxLayout, QSizePolicy, QSpinBox,
    QToolButton, QVBoxLayout, QWidget,
)

_FIELD_BG = "#393e46"
_FIELD_BORDER = "#4a515a"
_ARROW_BG = "#333a42"
_ACCENT = "#00adb5"
_ACCENT_PRESSED = "#00858c"
_TEXT = "#eeeeee"

_RADIUS = 12
_ARROW_WIDTH = 24


class _BaseSpinField(QWidget):
    """Shared arrow-button chrome around an inner QAbstractSpinBox subclass."""

    def __init__(self, spin: QAbstractSpinBox, field_width: int, parent=None):
        super().__init__(parent)

        self.spin = spin
        self.spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.spin.setFocusPolicy(Qt.WheelFocus)
        self.spin.setFixedWidth(field_width)
        self.spin.setStyleSheet(
            f"QAbstractSpinBox {{ background-color: {_FIELD_BG}; color: {_TEXT};"
            f"border: 1px solid {_FIELD_BORDER}; border-right: none;"
            f"border-top-left-radius: {_RADIUS}px; border-bottom-left-radius: {_RADIUS}px;"
            "border-top-right-radius: 0px; border-bottom-right-radius: 0px;"
            "padding: 8px 10px; }"
            f"QAbstractSpinBox:focus {{ border-color: {_ACCENT}; }}"
        )

        self.up_btn = QToolButton()
        self.up_btn.setText("▲")
        self.down_btn = QToolButton()
        self.down_btn.setText("▼")
        for btn, is_top in ((self.up_btn, True), (self.down_btn, False)):
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedWidth(_ARROW_WIDTH)
            btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
            corner = (
                f"border-top-right-radius: {_RADIUS}px; border-bottom-right-radius: 0px;"
                if is_top else
                f"border-bottom-right-radius: {_RADIUS}px; border-top-right-radius: 0px;"
            )
            btn.setStyleSheet(
                f"QToolButton {{ background-color: {_ARROW_BG}; color: {_TEXT};"
                f"border: 1px solid {_FIELD_BORDER}; border-left: none; {corner}"
                "font-size: 7pt; padding: 0px; }"
                f"QToolButton:hover {{ background-color: {_ACCENT}; }}"
                f"QToolButton:pressed {{ background-color: {_ACCENT_PRESSED}; }}"
            )
        self.up_btn.clicked.connect(self.spin.stepUp)
        self.down_btn.clicked.connect(self.spin.stepDown)

        arrows = QVBoxLayout()
        arrows.setSpacing(0)
        arrows.setContentsMargins(0, 0, 0, 0)
        arrows.addWidget(self.up_btn)
        arrows.addWidget(self.down_btn)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.spin)
        row.addLayout(arrows)

        # Lock the whole composite to the spin box's natural height - without
        # this, a parent layout can stretch this widget taller than intended,
        # and since the arrow buttons are vertically Expanding, they'd blow up
        # to fill that leftover space instead of hugging the text field.
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedHeight(self.spin.sizeHint().height())

    def value(self):
        return self.spin.value()

    def setValue(self, value):
        self.spin.setValue(value)


class SpinField(_BaseSpinField):
    """Integer spin field. Behaves like a QSpinBox for value/setValue/setRange."""

    def __init__(self, minimum: int = 0, maximum: int = 99, value: int = 0,
                 field_width: int = 76, parent=None):
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        super().__init__(spin, field_width, parent)

    def setRange(self, minimum: int, maximum: int):
        self.spin.setRange(minimum, maximum)


class DoubleSpinField(_BaseSpinField):
    """Decimal spin field (e.g. a resend interval in seconds, 0.1 granularity)."""

    def __init__(self, minimum: float = 0.0, maximum: float = 60.0, value: float = 0.0,
                 step: float = 0.1, decimals: int = 1, field_width: int = 70, parent=None):
        spin = QDoubleSpinBox()
        spin.setDecimals(decimals)
        spin.setSingleStep(step)
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        super().__init__(spin, field_width, parent)

    def setRange(self, minimum: float, maximum: float):
        self.spin.setRange(minimum, maximum)
