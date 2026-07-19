import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from main_window import MainWindow
from theme import STYLESHEET

_ICON_PATH = Path(__file__).resolve().parent / "app.ico"


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    if _ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(_ICON_PATH)))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
