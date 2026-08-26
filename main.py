import sys

from PySide6.QtWidgets import QApplication, QMessageBox, QProxyStyle, QStyle

from core import lru_config

# Loaded before main_window is imported, so nothing can read the array's
# shape before it has been established. The accessors in core/packet.py
# resolve per call and would cope either way, but keeping the load first
# means import order is never something anyone has to reason about.
_CONFIG_ERROR = None
try:
    lru_config.load()
except (OSError, ValueError) as e:
    # Left at the 96 x 4 default and reported below once there's a
    # QApplication to show a dialog with. Deliberately not silent: running
    # a differently-shaped array than the operator configured would put
    # wrong-length frames on the wire.
    _CONFIG_ERROR = e

from main_window import MainWindow  # noqa: E402 - must follow the config load
from theme import STYLESHEET  # noqa: E402

# Qt's default tooltip wake-up delay (~700ms) made the LRU hover popup on
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
    if _CONFIG_ERROR is not None:
        QMessageBox.critical(
            None, "Array Configuration",
            f"Could not read the saved array configuration:\n\n{_CONFIG_ERROR}\n\n"
            f"Starting with the default {lru_config.config().describe()}. "
            f"Set it under Configuration -> Array Configuration.")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
