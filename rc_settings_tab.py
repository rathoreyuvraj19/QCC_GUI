"""
rc_settings_tab.py

"RC Settings" - lets the operator set the values that go into the editable
part of every outgoing command's 90-byte header (Destination ID, Source
ID, Date/Month/Year+Time of Day, and the 14 reserved bytes), backed by the
shared rc_settings.RCSettings instance (see rc_settings.py) that every
send handler in main_window.py reads from.

Date/Time default to the moment this tab is constructed (i.e. GUI
startup), editable afterward via the QDateTimeEdit. Packet Size, Command
ID, and Command/Ack aren't editable here - per Yuvraj, they're determined
automatically (frame size, whichever command is actually being sent, and
always 0 for this direction respectively). Message Number is a live
read-only counter, refreshed via refresh_message_number() whenever this
tab becomes visible.

Every field pushes into the shared rc_settings instance as soon as it's
edited (not only when "Save Settings" is clicked) - the very next command
sent from any other tab picks it up immediately. "Save Settings" only
additionally persists the values to rc_settings.json so they survive a
GUI restart.
"""

from PySide6.QtCore import QDate, QDateTime, QRegularExpression, QTime, Qt, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QDateTimeEdit, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from packet import TOTAL_PACKET_SIZE
from rc_settings import rc_settings
from spin_field import SpinField

_SAVE_BTN_STYLE = (
    "QPushButton { background-color: #00adb5; color: #eeeeee; border: none;"
    "border-radius: 16px; padding: 11px 24px; font-weight: 600; }"
    "QPushButton:hover { background-color: #00959c; }"
    "QPushButton:pressed { background-color: #007b81; }"
)

_AUTO_STYLE = "color: rgba(238, 238, 238, 0.55); font-style: italic;"

_HEX_VALIDATOR_PATTERN = "[0-9A-Fa-f]{0,2}"


class NamedByteGrid(QWidget):
    """
    Like status_responder_app.ByteGrid, but each cell also carries a
    user-editable name (defaulting to "RESERVED_<n>") above the byte index -
    these 14 bytes have no defined meaning yet, so unlike every other header
    field this tab exposes, there's no real field name to show; the operator
    can label them for their own reference instead.
    """

    changed = Signal()

    def __init__(self, num_bytes: int, wrap_cols: int = 14, start_index: int = 1, parent=None):
        super().__init__(parent)
        self._validator = QRegularExpressionValidator(QRegularExpression(_HEX_VALIDATOR_PATTERN))
        self._name_edits = []
        self._cells = []
        grid = QGridLayout(self)
        grid.setSpacing(3)
        for i in range(num_bytes):
            label_index = start_index + i

            name_edit = QLineEdit(f"RESERVED_{label_index}")
            name_edit.setFixedWidth(84)
            name_edit.setStyleSheet("font-size: 7pt; padding: 2px;")
            name_edit.setToolTip(f"Name for byte {label_index}")
            name_edit.textChanged.connect(self.changed)

            index_label = QLabel(str(label_index))
            index_label.setAlignment(Qt.AlignCenter)
            index_label.setStyleSheet("font-size: 7pt; color: rgba(238, 238, 238, 0.5);")
            # Without a fixed width matching name_edit/cell, this label
            # stretched to fill the whole (much wider) grid column once
            # embedded in a QFormLayout row - its centered text then drifted
            # away from the narrower name/value boxes above and below it.
            index_label.setFixedWidth(84)

            cell = QLineEdit("00")
            cell.setMaxLength(2)
            cell.setFixedWidth(84)
            cell.setAlignment(Qt.AlignCenter)
            cell.setValidator(self._validator)
            cell.setToolTip(f"Byte {label_index}")
            cell.setStyleSheet("padding: 2px;")
            cell.textChanged.connect(self.changed)

            cell_col = QVBoxLayout()
            cell_col.setSpacing(0)
            cell_col.setContentsMargins(0, 0, 0, 0)
            cell_col.addWidget(name_edit)
            cell_col.addWidget(index_label)
            cell_col.addWidget(cell)
            cell_widget = QWidget()
            cell_widget.setLayout(cell_col)

            self._name_edits.append(name_edit)
            self._cells.append(cell)
            grid.addWidget(cell_widget, i // wrap_cols, i % wrap_cols)

    def get_bytes(self) -> bytes:
        return bytes(int(cell.text(), 16) if cell.text() else 0 for cell in self._cells)

    def set_bytes(self, data: bytes):
        for cell, b in zip(self._cells, data):
            cell.blockSignals(True)
            cell.setText(f"{b:02X}")
            cell.blockSignals(False)

    def get_names(self) -> list:
        return [edit.text() for edit in self._name_edits]

    def set_names(self, names):
        for edit, name in zip(self._name_edits, names):
            edit.blockSignals(True)
            edit.setText(name)
            edit.blockSignals(False)


class RCSettingsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)

        root.addWidget(self._build_editable_group())
        root.addWidget(self._build_auto_group())
        root.addStretch(1)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.save_btn = QPushButton("Save Settings")
        self.save_btn.setStyleSheet(_SAVE_BTN_STYLE)
        self.save_btn.clicked.connect(self._on_save_clicked)
        save_row.addWidget(self.save_btn)
        self.save_status_label = QLabel("")
        self.save_status_label.setStyleSheet("color: #00adb5;")
        save_row.addWidget(self.save_status_label)
        root.addLayout(save_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self._load_from_settings()

    # -- UI construction ---------------------------------------------------

    def _build_editable_group(self):
        # Qt's QGroupBox::title subcontrol often ignores font-weight/size
        # set via stylesheet (the style engine renders it from the
        # widget's actual font, not the QSS text properties) - a real
        # QLabel as the heading, styled normally, is the reliable way to
        # get a bold/larger section title instead of the flat native one.
        box = QGroupBox("")
        box.setStyleSheet("QGroupBox { padding-top: 14px; }")
        outer = QVBoxLayout(box)
        title_label = QLabel("EDITABLE HEADER FIELDS (BYTES 1-32)")
        title_label.setStyleSheet(
            "color: #00adb5; font-size: 13pt; font-weight: 700; letter-spacing: 0.6px; background: transparent;"
        )
        outer.addWidget(title_label)

        form_widget = QWidget()
        form = QFormLayout(form_widget)
        outer.addWidget(form_widget)

        self.destination_id_spin = SpinField(0, 255, rc_settings.destination_id)
        self.destination_id_spin.spin.valueChanged.connect(self._on_fields_changed)
        form.addRow("DESTINATION_ID", self.destination_id_spin)

        self.source_id_spin = SpinField(0, 255, rc_settings.source_id)
        self.source_id_spin.spin.valueChanged.connect(self._on_fields_changed)
        form.addRow("SOURCE_ID", self.source_id_spin)

        now = QDateTime.currentDateTime()
        self.datetime_edit = QDateTimeEdit(now)
        self.datetime_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.datetime_edit.setCalendarPopup(True)
        self.datetime_edit.dateTimeChanged.connect(self._on_fields_changed)
        form.addRow("DATE / MONTH / YEAR / TIME_OF_DAY", self.datetime_edit)

        form.addRow(QLabel("RESERVED0 (bytes 19-32, editable names + values):"))
        self.reserved_grid = NamedByteGrid(14, wrap_cols=7, start_index=19)
        self.reserved_grid.changed.connect(self._on_fields_changed)
        form.addRow(self.reserved_grid)

        return box

    def _build_auto_group(self):
        box = QGroupBox("")
        box.setStyleSheet("QGroupBox { padding-top: 14px; }")
        outer = QVBoxLayout(box)
        title_label = QLabel("AUTOMATIC HEADER FIELDS (NOT EDITABLE HERE)")
        title_label.setStyleSheet(
            "color: #00adb5; font-size: 13pt; font-weight: 700; letter-spacing: 0.6px; background: transparent;"
        )
        outer.addWidget(title_label)

        form_widget = QWidget()
        form = QFormLayout(form_widget)
        outer.addWidget(form_widget)

        self.packet_size_label = QLabel(f"{TOTAL_PACKET_SIZE} (fixed frame size)")
        self.packet_size_label.setStyleSheet(_AUTO_STYLE)
        form.addRow("PACKET_SIZE", self.packet_size_label)

        command_id_label = QLabel("Set automatically per command sent")
        command_id_label.setStyleSheet(_AUTO_STYLE)
        form.addRow("COMMAND_ID / COMMAND_ID_REPEAT", command_id_label)

        command_ack_label = QLabel("0 (fixed - this is always the command direction)")
        command_ack_label.setStyleSheet(_AUTO_STYLE)
        form.addRow("COMMAND_ACK", command_ack_label)

        self.message_number_label = QLabel()
        self.message_number_label.setStyleSheet(_AUTO_STYLE)
        form.addRow("MESSAGE_NUMBER", self.message_number_label)
        self.refresh_message_number()

        return box

    # -- state ---------------------------------------------------------

    def _load_from_settings(self):
        self.destination_id_spin.setValue(rc_settings.destination_id)
        self.source_id_spin.setValue(rc_settings.source_id)
        secs = rc_settings.time_of_day
        time = QTime(0, 0, 0).addSecs(secs)
        date = QDate(rc_settings.year, rc_settings.month, rc_settings.date)
        self.datetime_edit.setDateTime(QDateTime(date, time))
        self.reserved_grid.set_bytes(rc_settings.reserved0)
        self.reserved_grid.set_names(rc_settings.reserved_names)

    def refresh_message_number(self):
        self.message_number_label.setText(f"{rc_settings.peek_message_number()} (increments with every command sent)")

    def _on_fields_changed(self, *_args):
        # Apply straight into the shared rc_settings instance so the very
        # next command sent from any tab uses the new values, AND persist
        # to disk immediately - per Yuvraj's ask, edits (especially the
        # reserved-byte names) need to survive closing the GUI without
        # requiring a separate "Save Settings" click first. That button is
        # kept only as an explicit "Saved." confirmation, not the only way
        # to actually persist.
        dt = self.datetime_edit.dateTime()
        d = dt.date()
        t = dt.time()
        rc_settings.destination_id = self.destination_id_spin.value()
        rc_settings.source_id = self.source_id_spin.value()
        rc_settings.date = d.day()
        rc_settings.month = d.month()
        rc_settings.year = d.year()
        rc_settings.time_of_day = t.hour() * 3600 + t.minute() * 60 + t.second()
        rc_settings.reserved0 = self.reserved_grid.get_bytes()
        rc_settings.reserved_names = self.reserved_grid.get_names()
        rc_settings.save()
        self.save_status_label.setText("")

    def _on_save_clicked(self):
        self._on_fields_changed()
        self.save_status_label.setText("Saved.")
