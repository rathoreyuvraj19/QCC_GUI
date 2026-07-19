"""
theme.py

App-wide stylesheet (Qt Style Sheet), applied once at startup. Palette from
https://colorhunt.co/palette/222831393e4600adb5eeeeee (per Yuvraj's pick) -
kept unchanged across revisions of this file. Per-widget inline colors (LED
matrix, ping button, response colors) still take priority over this since
they set an explicit background-color directly on the widget - this only
styles the surrounding chrome.
"""

_BG = "#222831"           # window background
_CARD = "#393e46"         # groupbox / tab / field background
_ACCENT = "#00adb5"       # primary accent
_ACCENT_HOVER = "#1fc2ca"
_ACCENT_PRESSED = "#00858c"
_TEXT = "#eeeeee"         # primary text
_TEXT_SECONDARY = "rgba(238, 238, 238, 0.6)"
_BORDER = "#4a515a"

_FONT = (
    '"Satoshi", "Poppins", "Nunito", "Segoe UI Variable Display", "Segoe UI", '
    '"Helvetica Neue", Arial, sans-serif'
)

STYLESHEET = f"""
QWidget {{
    background-color: {_BG};
    color: {_TEXT};
    font-family: {_FONT};
    font-size: 11pt;
}}

QMainWindow {{
    background-color: {_BG};
}}

QMenuBar {{
    background-color: {_BG};
    color: {_TEXT};
    border-bottom: 1px solid {_BORDER};
    padding: 2px 4px;
}}
QMenuBar::item {{
    background: transparent;
    padding: 6px 12px;
    border-radius: 8px;
}}
QMenuBar::item:selected {{
    background-color: {_CARD};
    color: {_ACCENT};
}}
QMenuBar::item:pressed {{
    background-color: {_ACCENT};
    color: {_TEXT};
}}
QMenu {{
    background-color: {_CARD};
    color: {_TEXT};
    border: 1px solid {_BORDER};
    border-radius: 10px;
    padding: 6px;
}}
QMenu::item {{
    padding: 8px 24px 8px 16px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background-color: {_ACCENT};
    color: {_TEXT};
}}
QMenu::separator {{
    height: 1px;
    background-color: {_BORDER};
    margin: 6px 8px;
}}

QGroupBox {{
    background-color: {_CARD};
    border: 1px solid {_BORDER};
    border-radius: 20px;
    margin-top: 18px;
    padding: 22px 20px 20px 20px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 16px;
    padding: 2px 10px 8px 2px;
    color: {_ACCENT};
    font-size: 12pt;
    font-weight: 700;
    letter-spacing: 0.4px;
}}

QPushButton {{
    background-color: {_ACCENT};
    border: none;
    border-radius: 16px;
    padding: 11px 24px;
    color: {_TEXT};
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: {_ACCENT_HOVER};
}}
QPushButton:pressed {{
    background-color: {_ACCENT_PRESSED};
}}
QPushButton:checked {{
    background-color: {_ACCENT_PRESSED};
}}
QPushButton:disabled {{
    color: {_TEXT_SECONDARY};
    background-color: {_CARD};
}}

QLineEdit, QSpinBox, QComboBox {{
    background-color: {_CARD};
    border: 1px solid {_BORDER};
    border-radius: 12px;
    padding: 8px 12px;
    color: {_TEXT};
    selection-background-color: {_ACCENT};
    selection-color: {_TEXT};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border: 1px solid {_ACCENT};
}}
/* Explicit colors above override Qt's own disabled-state dimming, so a
   locked field would otherwise look identical to an editable one - restate
   it here, washed-out against the card background. */
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{
    background-color: {_BG};
    border: 1px solid {_BORDER};
    color: {_TEXT_SECONDARY};
}}
/* Note: QSpinBox's native up/down buttons are unused everywhere in this app -
   every spinbox is wrapped in spin_field.py's SpinField, which hides them
   (QAbstractSpinBox.NoButtons) and draws its own always-visible arrow
   buttons instead, since Qt's native spin arrows don't reliably render
   against a custom dark palette. */

QTabWidget::pane {{
    border: 1px solid {_BORDER};
    border-radius: 20px;
    top: -1px;
    background-color: {_BG};
}}
QTabBar {{
    background: transparent;
}}
QTabBar::tab {{
    background: {_CARD};
    border: none;
    border-top-left-radius: 14px;
    border-top-right-radius: 14px;
    padding: 11px 26px;
    color: {_TEXT_SECONDARY};
    margin-right: 4px;
    font-weight: 600;
}}
QTabBar::tab:selected {{
    background: {_ACCENT};
    color: {_TEXT};
}}
QTabBar::tab:hover:!selected {{
    background: #454d57;
}}

QTableView, QTableWidget {{
    background-color: {_CARD};
    alternate-background-color: #333a42;
    gridline-color: {_BORDER};
    border: 1px solid {_BORDER};
    border-radius: 16px;
    color: {_TEXT};
}}
QHeaderView::section {{
    background-color: #333a42;
    color: {_TEXT};
    padding: 9px;
    border: none;
    border-right: 1px solid {_BORDER};
    font-weight: 600;
}}

QLabel {{
    background: transparent;
    color: {_TEXT};
}}

QScrollBar:vertical, QScrollBar:horizontal {{
    background: {_BG};
    border: none;
    width: 11px;
    height: 11px;
}}
QScrollBar::handle {{
    background: {_BORDER};
    border-radius: 5px;
}}
QScrollBar::handle:hover {{
    background: {_ACCENT};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}
"""
