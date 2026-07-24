"""
plot_log_dialog.py

Tools -> Plot Log File (CSV)... in main_window.py. Loads a burn-test CSV
written by core/frame_logger.py and renders the same figure
apps/plot_qcc_log.py produces (delay-vs-time, rolling loss %, QTRM
NOT_OK events, delay histogram, per-QTRM NOT_OK ranking for Link Test
logs), embedded directly in the GUI instead of a separate matplotlib
window - so a burn test can be reviewed without dropping to a terminal.

matplotlib is only imported when this dialog is actually opened (not a
hard GUI dependency at startup) - see _import_matplotlib_qt().
"""

import os

from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QMessageBox, QPushButton,
    QTextEdit, QVBoxLayout,
)

from apps.plot_qcc_log import DEFAULT_PLOTS_DIR, build_figure, load_rows, summarize


def _import_matplotlib_qt():
    import matplotlib
    matplotlib.use("QtAgg")
    from matplotlib.backends.backend_qtagg import (
        FigureCanvasQTAgg, NavigationToolbar2QT,
    )
    from matplotlib.figure import Figure
    return FigureCanvasQTAgg, NavigationToolbar2QT, Figure


class PlotLogDialog(QDialog):
    """Non-modal so the user can keep driving the main window (e.g. watch
    a live burn test) while a plot of an earlier stretch of the log stays
    open alongside it."""

    def __init__(self, csv_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Burn-test plot - {csv_path}")
        self.resize(1150, 980)
        self._fig = None
        self._csv_path = csv_path

        FigureCanvasQTAgg, NavigationToolbar2QT, Figure = _import_matplotlib_qt()

        rows = load_rows(csv_path)
        queries, summary_text = summarize(rows, csv_path)

        layout = QVBoxLayout(self)

        summary_box = QTextEdit(readOnly=True)
        summary_box.setPlainText(summary_text)
        summary_box.setMaximumHeight(170)
        layout.addWidget(summary_box)

        if not queries:
            layout.addWidget(QTextEdit("No query rows in this log - nothing to plot.",
                                        readOnly=True))
            return

        fig = Figure(constrained_layout=True)
        build_figure(fig, queries, csv_path, window=200)
        self._fig = fig
        canvas = FigureCanvasQTAgg(fig)
        toolbar = NavigationToolbar2QT(canvas, self)

        save_row = QHBoxLayout()
        save_row.addWidget(toolbar, 1)
        save_btn = QPushButton("Save Image…")
        save_btn.clicked.connect(self._on_save_clicked)
        save_row.addWidget(save_btn)

        layout.addLayout(save_row)
        layout.addWidget(canvas, 1)

    def _on_save_clicked(self):
        os.makedirs(DEFAULT_PLOTS_DIR, exist_ok=True)
        default_name = os.path.splitext(os.path.basename(self._csv_path))[0] + ".png"
        default = os.path.join(DEFAULT_PLOTS_DIR, default_name)
        path, _ = QFileDialog.getSaveFileName(
            self, "Save plot image", default,
            "PNG image (*.png);;All files (*)",
        )
        if not path:
            return
        try:
            self._fig.savefig(path, dpi=130)
        except OSError as e:
            QMessageBox.warning(self, "Save plot image", f"Could not save image:\n{e}")
