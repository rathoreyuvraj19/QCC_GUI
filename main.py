import sys

from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle

from main_window import MainWindow
from theme import STYLESHEET

# Qt's default tooltip wake-up delay (~700ms) made the QTRM hover popup on
# the Status tab feel sluggish - this shortens it app-wide rather than
# hacking a per-widget QToolTip.showText() override, which would also lose
# the automatic re-show/hide-on-leave behavior every other tooltip in the
# app already relies on.
_TOOLTIP_DELAY_MS = 150


class _FastTooltipStyle(QProxyStyle):
    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.SH_ToolTip_WakeUpDelay:
            return _TOOLTIP_DELAY_MS
        return super().styleHint(hint, option, widget, returnData)


def main():
    app = QApplication(sys.argv)
    app.setStyle(_FastTooltipStyle(app.style()))
    app.setStyleSheet(STYLESHEET)
    # Icon is embedded in the exe via PyInstaller's --icon flag.
    # Windows picks it up automatically for taskbar/window decoration.
    # No need to load it from a file.
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
