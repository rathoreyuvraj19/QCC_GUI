"""
array_config_dialog.py

Tools -> Array Configuration... - how many LRUs the array has and how many
channels each one carries. Everything about the frame's size derives from
those two numbers (see core/lru_config.py), so this dialog is the one place
the wire format's shape is chosen.

Takes effect on restart. Nothing in the GUI rebuilds its grids, tables or
column layouts live, and a tab still sized for the old shape would put
wrong-length frames on the wire - so the dialog saves, tells the operator,
and leaves the running session alone.

The live preview under the fields is the point of the dialog: slot size and
frame size are what actually go on the wire, and 5*channels + 10 isn't
arithmetic anyone should have to do in their head to check they've entered
the right thing.
"""

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QMessageBox, QVBoxLayout,
)

from core import lru_config
from widgets.spin_field import SpinField

_PREVIEW_OK = "#00adb5"
_PREVIEW_BAD = "#d64545"


class ArrayConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Array Configuration")

        current = lru_config.config()
        self._original = current

        layout = QVBoxLayout(self)

        intro = QLabel(
            "How many LRUs the array has, and how many channels each LRU carries.\n"
            "Every frame size below follows from these two numbers."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.num_lru_spin = SpinField(
            1, lru_config.MAX_NUM_LRU, current.num_lru, field_width=90)
        self.channels_spin = SpinField(
            1, lru_config.MAX_CHANNELS_PER_LRU, current.channels_per_lru, field_width=90)
        form.addRow("LRUs:", self.num_lru_spin)
        form.addRow("Channels per LRU:", self.channels_spin)
        layout.addLayout(form)

        self.preview_label = QLabel()
        self.preview_label.setWordWrap(True)
        layout.addWidget(self.preview_label)

        # SpinField wraps the QSpinBox rather than re-exporting its signals,
        # so subscribe to the inner spin - same as every other tab does.
        self.num_lru_spin.spin.valueChanged.connect(self._update_preview)
        self.channels_spin.spin.valueChanged.connect(self._update_preview)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._on_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._update_preview()

    # -- live preview ------------------------------------------------------

    def _entered(self):
        return self.num_lru_spin.value(), self.channels_spin.value()

    def _update_preview(self):
        num_lru, channels = self._entered()
        try:
            lru_config.validate(num_lru, channels)
        except lru_config.ConfigError as e:
            # The spin boxes already bound each field on its own; what they
            # can't catch is the pair together overflowing the header's
            # uint16 PACKET_SIZE.
            self.preview_label.setStyleSheet(f"color: {_PREVIEW_BAD};")
            self.preview_label.setText(str(e))
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
            return

        cfg = lru_config.LRUConfig(num_lru, channels)
        self.preview_label.setStyleSheet(f"color: {_PREVIEW_OK};")
        self.preview_label.setText(
            f"Slot: 5 x {channels} + 10 = {cfg.slot_size} bytes\n"
            f"Data block: {num_lru} x {cfg.slot_size} = {cfg.block_size} bytes\n"
            f"Frame: 90 + {cfg.block_size} = {cfg.total_size} bytes"
        )
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(True)

    # -- save --------------------------------------------------------------

    def _on_accept(self):
        num_lru, channels = self._entered()
        try:
            cfg = lru_config.LRUConfig(num_lru, channels)
        except lru_config.ConfigError as e:
            QMessageBox.warning(self, "Array Configuration", str(e))
            return

        if cfg == self._original:
            self.accept()
            return

        try:
            lru_config.save(cfg)
        except OSError as e:
            QMessageBox.critical(
                self, "Array Configuration",
                f"Could not save the configuration:\n\n{e}")
            return

        # Deliberately NOT lru_config.set_config(cfg) - the running session's
        # tables and grids are already built for the old shape, and switching
        # the frame size out from under them would mean sending frames whose
        # length no longer matches what the tabs think they're filling in.
        QMessageBox.information(
            self, "Array Configuration",
            f"Saved: {cfg.describe()}.\n\n"
            f"Restart the application for this to take effect. "
            f"Until then it keeps running as {self._original.describe()}.")
        self.accept()
