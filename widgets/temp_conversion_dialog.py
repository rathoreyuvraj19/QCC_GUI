"""
temp_conversion_dialog.py

Tools -> Temperature Conversion Formula... - lets Yuvraj edit the
raw-byte -> Deg C formula (deg_c = raw * multiplier + offset) applied to
Health's temperature_status field on the Status tab, instead of it being
hardcoded. Saved to temp_conversion_settings.py's JSON file on OK.
"""

from PySide6.QtWidgets import QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout, QDialog

from temp_conversion_settings import temp_conversion_settings
from widgets.spin_field import DoubleSpinField


class TempConversionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Temperature Conversion Formula")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Deg C = raw temperature_status byte * Multiplier + Offset"))

        form = QFormLayout()
        self.multiplier_spin = DoubleSpinField(-100.0, 100.0, temp_conversion_settings.multiplier,
                                                step=0.0001, decimals=4, field_width=100)
        self.offset_spin = DoubleSpinField(-500.0, 500.0, temp_conversion_settings.offset,
                                            step=0.1, decimals=2, field_width=100)
        form.addRow("Multiplier:", self.multiplier_spin)
        form.addRow("Offset:", self.offset_spin)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        temp_conversion_settings.multiplier = self.multiplier_spin.value()
        temp_conversion_settings.offset = self.offset_spin.value()
        temp_conversion_settings.save()
        self.accept()
