"""
multi_qcc_window.py

Multi-QCC window - a grid of QccTile widgets, one per QCC unit. Each tile
embeds a complete MainWindow instance (its own connection, its own full set
of tabs) - see widgets/qcc_tile.py. Opened from the main window's Tools
menu. Remote Programming is deliberately left out of each embedded window
for now - see MainWindow's enable_remote_programming flag.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout, QMainWindow, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from connection_settings import connection_settings
from widgets.qcc_tile import QccTile

_TILES_PER_ROW = 2
# Each tile embeds its own MainWindow, which binds its own local UDP
# socket - every tile needs a distinct local port regardless of which
# remote QCC IP it targets, since the bind is scoped to this machine.
_PORT_STRIDE = 1


class MultiQccWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Multi-QCC")
        self.resize(1200, 850)

        self._tiles: list[QccTile] = []
        self._next_tile_index = 0

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        add_btn = QPushButton("+ Add QCC")
        add_btn.clicked.connect(self._on_add_tile_clicked)
        root.addWidget(add_btn, alignment=Qt.AlignLeft)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        scroll.setWidget(self._grid_host)
        root.addWidget(scroll)

    def _next_local_port(self) -> int:
        return connection_settings.local_port + 100 + self._next_tile_index * _PORT_STRIDE

    def _on_add_tile_clicked(self):
        index = self._next_tile_index
        self._next_tile_index += 1
        local_port = self._next_local_port()

        tile = QccTile(
            title=f"QCC {index}",
            qcc_ip=connection_settings.qcc_ip,
            qcc_port=connection_settings.qcc_port,
            local_port=local_port,
        )
        tile.remove_requested.connect(self._on_remove_tile)

        self._tiles.append(tile)
        self._reflow_grid()

    def _on_remove_tile(self, tile: QccTile):
        tile.shutdown()
        self._tiles.remove(tile)
        tile.setParent(None)
        tile.deleteLater()
        self._reflow_grid()

    def _reflow_grid(self):
        for i in reversed(range(self._grid.count())):
            self._grid.takeAt(i)
        for i, tile in enumerate(self._tiles):
            self._grid.addWidget(tile, i // _TILES_PER_ROW, i % _TILES_PER_ROW)

    def closeEvent(self, event):
        for tile in self._tiles:
            tile.shutdown()
        super().closeEvent(event)
