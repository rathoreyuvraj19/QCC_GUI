import sys

from PySide6.QtWidgets import QApplication

from main_window import MainWindow
from theme import STYLESHEET


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    # Icon is embedded in the exe via PyInstaller's --icon flag.
    # Windows picks it up automatically for taskbar/window decoration.
    # No need to load it from a file.
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
