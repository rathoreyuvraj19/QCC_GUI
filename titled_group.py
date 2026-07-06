"""
titled_group.py

Qt's QGroupBox::title subcontrol often ignores font-weight/size set via
stylesheet (the style engine renders it from the widget's actual font, not
the QSS text properties) - a real QLabel as the heading, styled normally,
is the reliable way to get a bold/larger section title instead of the flat
native one. Shared here since the same pattern was getting copy-pasted into
every file that needed a properly-weighted QGroupBox heading.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

_TITLE_STYLE = (
    "color: #00adb5; font-size: 13pt; font-weight: 700; letter-spacing: 0.6px; background: transparent;"
)

_TOGGLE_BTN_STYLE = (
    "QToolButton { background: transparent; border: none; color: #00adb5; font-size: 11pt; }"
    "QToolButton:hover { color: #1fc2ca; }"
)


def titled_group_box(title: str) -> tuple:
    """
    Returns (box, outer) - box is a QGroupBox with no native title text
    (blanked out on purpose) and outer is its QVBoxLayout, already seeded
    with the styled heading label as the first item. Keep adding your own
    content to outer afterward.
    """
    box = QGroupBox("")
    box.setStyleSheet("QGroupBox { padding-top: 14px; }")
    outer = QVBoxLayout(box)
    title_label = QLabel(title.upper())
    title_label.setStyleSheet(_TITLE_STYLE)
    outer.addWidget(title_label)
    return box, outer


def collapsible_group_box(title: str, start_expanded: bool = True) -> tuple:
    """
    Like titled_group_box, but the heading also carries a collapse/expand
    arrow (clicking either the arrow or the title itself toggles it) - for
    sections that are useful to check but don't need to stay taking up
    space once you've seen them (e.g. a test window's "Status" row).

    Returns (box, content_layout) - box is the QGroupBox to add to your
    window; content_layout is a QVBoxLayout you add your section's actual
    content to (already installed inside the widget that gets shown/hidden -
    do NOT add content directly to box's own layout, or it won't collapse).
    """
    box = QGroupBox("")
    box.setStyleSheet("QGroupBox { padding-top: 14px; }")
    outer = QVBoxLayout(box)

    header_row = QHBoxLayout()
    toggle_btn = QToolButton()
    toggle_btn.setStyleSheet(_TOGGLE_BTN_STYLE)
    toggle_btn.setCursor(Qt.PointingHandCursor)
    toggle_btn.setArrowType(Qt.DownArrow if start_expanded else Qt.RightArrow)
    header_row.addWidget(toggle_btn)

    title_label = QLabel(title.upper())
    title_label.setStyleSheet(_TITLE_STYLE + " QLabel:hover { color: #1fc2ca; }")
    title_label.setCursor(Qt.PointingHandCursor)
    header_row.addWidget(title_label)
    header_row.addStretch(1)
    outer.addLayout(header_row)

    content = QWidget()
    content.setVisible(start_expanded)
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(content)

    def _toggle(*_args):
        expanded = not content.isVisible()
        content.setVisible(expanded)
        toggle_btn.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)

    toggle_btn.clicked.connect(_toggle)
    title_label.mousePressEvent = _toggle

    return box, content_layout
