"""
dwell_tab.py

"Dwell" - the main per-QTRM beam command (Command Type 0x01): each of the
96 QTRMs gets its own 4-channel Control/Tx Phase/Tx Atten/Rx Phase/Rx Atten
set, all sent together in one frame (unlike Cal/Isolation/Soft Reset, Dwell
has no single-QTRM-target convention).

Two ways to fill the 96-row table: type values in directly, or import a
CSV file - one row per QTRM, columns "QTRM ID" (0-based, optional, else
row order = QTRM 0-95) and "Ch{1-4} Tx/Rx/Tx Phase/Tx Atten/Rx Phase/Rx
Atten".
Imported data is latched into the table (stays until edited or
re-imported) until Send is pressed. "Save to CSV..." writes the same
layout back out, so a saved file re-imports directly (round-trip), and
the plain-text format is easy to review/diff outside the app.

Every QTRM's Dwell command requests a Link-type status response (per
Yuvraj: every command except Status and Soft Reset does) - the 96-cell LED
matrix (reused from link_test_tab.py) shows which QTRMs acknowledged.

Tx/Rx Phase and Tx/Rx Atten are capped 0-63 (6-bit). Control is a 2-bit
Tx/Rx on-off field (bit1 = Tx enable, bit0 = Rx enable), split into two
always-visible toggle-button columns per channel ("Ch{n} Tx"/"Ch{n} Rx")
rather than a single combined number - each button shows On/Off and
colors green/red, and clicking it flips just that one bit, leaving the
other bit untouched. Default is both on (control = 3). Each Tx/Rx
column's header is itself a toggle-all control (DwellHeaderView) - click
it to set that bit for all 96 QTRMs at once; it shows green/red/grey for
all-on/all-off/mixed.
"""

import csv

from PySide6.QtCore import QAbstractTableModel, QEvent, Qt, QModelIndex, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QScrollArea, QStyledItemDelegate,
    QTableView, QToolTip, QVBoxLayout, QWidget,
)

from header_panel import HeaderPanel
from link_test_tab import LedMatrix, _NOT_LINKED_COLOR, _PENDING_COLOR
from packet import NUM_QTRM, QTRMChannel

PHASE_MAX = 63    # 6-bit phase (frame_type.vhd: No_of_phase_bits = 6)
ATTEN_MAX = 63    # 6-bit attenuation (frame_type.vhd: No_of_Attenuator_bits = 6)

# Send button color - shared across every command tab's primary send button
# so they all read consistently, distinct from the app's default teal (used
# by Import/Save right next to it) and from the green/red/grey status
# colors used elsewhere.
_SEND_COLOR = "#7C3AED"
_SEND_HOVER_COLOR = "#6D28D9"
_SEND_PRESSED_COLOR = "#5B21B6"

_SEND_BTN_STYLE = (
    f"QPushButton {{ background-color: {_SEND_COLOR}; color: #ffffff; border: none;"
    "border-radius: 16px; padding: 11px 24px; font-weight: 700; }"
    f"QPushButton:hover {{ background-color: {_SEND_HOVER_COLOR}; }}"
    f"QPushButton:pressed {{ background-color: {_SEND_PRESSED_COLOR}; }}"
)

# Control is a 2-bit field: bit1 = Tx enable, bit0 = Rx enable. Default is
# both on (matches every other command tab's "on" idle state).
TX_BIT = 0b10
RX_BIT = 0b01
CONTROL_DEFAULT = TX_BIT | RX_BIT

_ON_COLOR = QColor(146, 208, 165)
_OFF_COLOR = QColor(240, 149, 149)
_TOGGLE_TEXT_COLOR = QColor("#1f2328")

# (label, attribute, min, max) - Control is handled separately (see below),
# not part of this uniform numeric-field list.
_NUMERIC_CHANNEL_FIELDS = [
    ("Tx Phase", "tx_phase", 0, PHASE_MAX),
    ("Tx Atten", "tx_atten", 0, ATTEN_MAX),
    ("Rx Phase", "rx_phase", 0, PHASE_MAX),
    ("Rx Atten", "rx_atten", 0, ATTEN_MAX),
]


def _build_columns():
    cols = [("QTRM ID", None, False)]
    for ch in range(1, 5):
        cols.append((f"Ch{ch} Tx", (ch - 1, "tx_toggle"), True))
        cols.append((f"Ch{ch} Rx", (ch - 1, "rx_toggle"), True))
        for label, attr, lo, hi in _NUMERIC_CHANNEL_FIELDS:
            cols.append((f"Ch{ch} {label}", (ch - 1, attr, lo, hi), True))
    return cols


COLUMNS = _build_columns()


def _toggle_columns(kind: str):
    return [
        i for i, (_, key, _) in enumerate(COLUMNS)
        if isinstance(key, tuple) and len(key) == 2 and key[1] == kind
    ]


TX_TOGGLE_COLUMNS = _toggle_columns("tx_toggle")
RX_TOGGLE_COLUMNS = _toggle_columns("rx_toggle")


def _default_channels():
    return [[QTRMChannel(control=CONTROL_DEFAULT) for _ in range(4)] for _ in range(NUM_QTRM)]


class DwellTableModel(QAbstractTableModel):
    invalid_data = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.channels = _default_channels()

    def rowCount(self, parent=QModelIndex()):
        return NUM_QTRM

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return COLUMNS[section][0]
        return str(section)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        _, key, _ = COLUMNS[index.column()]
        if role in (Qt.DisplayRole, Qt.EditRole):
            if key is None:
                return index.row()
            channel = self.channels[index.row()][key[0]]
            if len(key) == 2:
                bit = TX_BIT if key[1] == "tx_toggle" else RX_BIT
                return bool(channel.control & bit)
            _, attr, _, _ = key
            return getattr(channel, attr)
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid():
            return False
        _, key, editable = COLUMNS[index.column()]
        if not editable:
            return False
        channel = self.channels[index.row()][key[0]]

        if len(key) == 2:
            bit = TX_BIT if key[1] == "tx_toggle" else RX_BIT
            channel.control = (channel.control | bit) if value else (channel.control & ~bit)
            self.dataChanged.emit(index, index, [role])
            return True

        ch_idx, attr, lo, hi = key
        try:
            ivalue = int(value)
        except (TypeError, ValueError):
            return False
        clamped = max(lo, min(hi, ivalue))
        if clamped != ivalue:
            label = attr.replace("_", " ").title()
            self.invalid_data.emit(
                f"Ch{ch_idx + 1} {label}: {ivalue} exceeds {lo}-{hi}, clamped to {clamped}."
            )
        setattr(channel, attr, clamped)
        self.dataChanged.emit(index, index, [role])
        return True

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        _, _, editable = COLUMNS[index.column()]
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if editable:
            base |= Qt.ItemIsEditable
        return base

    def load_channels(self, channels):
        self.beginResetModel()
        self.channels = channels
        self.endResetModel()

    def get_channels(self):
        return self.channels


class ToggleDelegate(QStyledItemDelegate):
    """
    Always-visible toggle button for one Control bit (Tx or Rx) - not a
    real editor widget (would need re-creating on every model reset, e.g.
    CSV import); paints its own On/Off pill and toggles directly on click
    via editorEvent, the same mechanism Qt's own built-in checkbox
    delegate uses.
    """

    def __init__(self, label_prefix: str, parent=None):
        super().__init__(parent)
        self.label_prefix = label_prefix

    def paint(self, painter, option, index):
        value = bool(index.data(Qt.DisplayRole))
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(4, 4, -4, -4)
        painter.setPen(Qt.NoPen)
        painter.setBrush(_ON_COLOR if value else _OFF_COLOR)
        painter.drawRoundedRect(rect, 8, 8)
        painter.setPen(_TOGGLE_TEXT_COLOR)
        painter.drawText(rect, Qt.AlignCenter, f"{self.label_prefix} {'On' if value else 'Off'}")
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.MouseButtonRelease and (index.flags() & Qt.ItemIsEditable):
            current = bool(index.data(Qt.DisplayRole))
            model.setData(index, not current, Qt.EditRole)
            return True
        return False

    def createEditor(self, parent, option, index):
        return None

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), 28))
        return size


_MIXED_COLOR = QColor(160, 165, 172)


_HEADER_HEIGHT = 48


class DwellHeaderView(QHeaderView):
    """
    Two-row header for each Tx/Rx column: "Ch{n} Control" on top (which
    field this button controls), a toggle-all pill below it. Click
    anywhere in the section to set that bit for all 96 QTRMs at once.
    Shows green "All Tx/Rx On" if every row already has it on, red "All
    Tx/Rx Off" if every row has it off, grey "Mixed" otherwise; clicking
    always sets every row to the opposite of "all on" (so a mixed or
    all-off column turns fully on, an all-on column turns fully off).
    """

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setSectionsClickable(True)
        self.setFixedHeight(_HEADER_HEIGHT)
        self.sectionClicked.connect(self._on_section_clicked)

    def _toggle_key(self, logical_index):
        _, key, _ = COLUMNS[logical_index]
        if isinstance(key, tuple) and len(key) == 2:
            return key
        return None

    def _aggregate_state(self, ch_idx, bit):
        model = self.model()
        values = [bool(model.channels[row][ch_idx].control & bit) for row in range(NUM_QTRM)]
        if all(values):
            return True
        if not any(values):
            return False
        return None

    def paintSection(self, painter, rect, logical_index):
        key = self._toggle_key(logical_index)
        if key is None:
            super().paintSection(painter, rect, logical_index)
            return

        ch_idx, kind = key
        bit = TX_BIT if kind == "tx_toggle" else RX_BIT
        label_prefix = "Tx" if kind == "tx_toggle" else "Rx"
        state = self._aggregate_state(ch_idx, bit)

        painter.save()
        painter.fillRect(rect, QColor("#333a42"))
        painter.setRenderHint(QPainter.Antialiasing)

        mid = rect.top() + rect.height() // 2
        label_rect = rect.adjusted(2, 2, -2, 0)
        label_rect.setBottom(mid)
        painter.setPen(QColor("#eeeeee"))
        painter.drawText(label_rect, Qt.AlignCenter, f"Ch{ch_idx + 1} Control")

        if state is True:
            color, text = _ON_COLOR, f"All {label_prefix} On"
        elif state is False:
            color, text = _OFF_COLOR, f"All {label_prefix} Off"
        else:
            color, text = _MIXED_COLOR, f"{label_prefix}: Mixed"
        pill_rect = rect.adjusted(4, 0, -4, -4)
        pill_rect.setTop(mid + 2)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(pill_rect, 6, 6)
        painter.setPen(_TOGGLE_TEXT_COLOR)
        painter.drawText(pill_rect, Qt.AlignCenter, text)
        painter.restore()

    def _on_section_clicked(self, logical_index):
        key = self._toggle_key(logical_index)
        if key is None:
            return
        ch_idx, kind = key
        bit = TX_BIT if kind == "tx_toggle" else RX_BIT
        model = self.model()
        turn_on = self._aggregate_state(ch_idx, bit) is not True
        for row in range(NUM_QTRM):
            model.setData(model.index(row, logical_index), turn_on, Qt.EditRole)
        self.viewport().update()


def _clamp(value, lo, hi):
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, ivalue))


def _csv_header():
    header = ["QTRM ID"]
    for ch in range(1, 5):
        header.append(f"Ch{ch} Tx")
        header.append(f"Ch{ch} Rx")
        for label, _, _, _ in _NUMERIC_CHANNEL_FIELDS:
            header.append(f"Ch{ch} {label}")
    return header


def load_channels_from_csv(path: str):
    """
    Parse a CSV file into a NUM_QTRM-length list of 4 QTRMChannel each, plus
    a list of warning strings for any invalid Tx/Rx flag encountered
    (defaulted to on, since these are only ever 0 or 1). First row is
    headers; "QTRM ID" (0-based) is optional - if absent, row order is
    taken as QTRM 0..95. Missing columns default to Tx/Rx on, 0 for
    Phase/Atten.
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise ValueError("CSV file has no rows.")

    header = [c.strip() for c in rows[0]]
    col_index = {name: idx for idx, name in enumerate(header)}
    has_id_col = "QTRM ID" in col_index

    channels = _default_channels()
    warnings = []
    for row_i, row in enumerate(rows[1:]):
        if not row:
            continue
        if has_id_col:
            id_col = col_index["QTRM ID"]
            if id_col >= len(row) or not row[id_col].strip():
                continue
            qtrm_index = _clamp(row[id_col], 0, NUM_QTRM - 1)
        else:
            qtrm_index = row_i
        if not (0 <= qtrm_index < NUM_QTRM):
            continue

        row_channels = []
        for ch in range(1, 5):
            values = {"control": 0}
            for bit_name, bit_value in (("Tx", TX_BIT), ("Rx", RX_BIT)):
                col = col_index.get(f"Ch{ch} {bit_name}")
                raw = row[col] if col is not None and col < len(row) and row[col].strip() != "" else 1
                try:
                    flag = int(raw)
                except (TypeError, ValueError):
                    flag = 1
                if flag not in (0, 1):
                    warnings.append(
                        f"QTRM-{qtrm_index}, Ch{ch}: {bit_name} flag {flag} invalid (must be 0 or 1) - defaulted to on."
                    )
                    flag = 1
                if flag:
                    values["control"] |= bit_value
            for label, attr, lo, hi in _NUMERIC_CHANNEL_FIELDS:
                col = col_index.get(f"Ch{ch} {label}")
                raw = row[col] if col is not None and col < len(row) and row[col].strip() != "" else 0
                values[attr] = _clamp(raw, lo, hi)
            row_channels.append(QTRMChannel(**values))
        channels[qtrm_index] = row_channels

    return channels, warnings


def save_channels_to_csv(path: str, channels):
    """
    Write the current NUM_QTRM x 4-channel data to a CSV file with the same
    header layout load_channels_from_csv expects ("QTRM ID" + "Ch{1-4}
    Tx/Rx/<numeric field>") - a saved file re-imports directly, round-trip.
    """
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(_csv_header())
        for qtrm_index, row_channels in enumerate(channels):
            row = [qtrm_index]
            for channel in row_channels:
                row.append(1 if channel.control & TX_BIT else 0)
                row.append(1 if channel.control & RX_BIT else 0)
                for _, attr, _, _ in _NUMERIC_CHANNEL_FIELDS:
                    row.append(getattr(channel, attr))
            writer.writerow(row)


class DwellTab(QWidget):
    send_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        content = QWidget()
        layout = QVBoxLayout(content)

        top_row = QHBoxLayout()
        self.import_btn = QPushButton("Import from CSV...")
        self.import_btn.clicked.connect(self._on_import_clicked)
        top_row.addWidget(self.import_btn)

        self.save_btn = QPushButton("Save to CSV...")
        self.save_btn.clicked.connect(self._on_save_clicked)
        top_row.addWidget(self.save_btn)

        self.send_btn = QPushButton("Send Dwell")
        self.send_btn.setStyleSheet(_SEND_BTN_STYLE)
        self.send_btn.clicked.connect(self.send_requested.emit)
        top_row.addWidget(self.send_btn)

        self.summary_label = QLabel("Not yet run")
        self.response_time_label = QLabel("")
        top_row.addWidget(self.summary_label)
        top_row.addWidget(self.response_time_label)
        top_row.addStretch(1)
        layout.addLayout(top_row)

        self.model = DwellTableModel()
        self.model.invalid_data.connect(self._on_invalid_data)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setHorizontalHeader(DwellHeaderView(self.table))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        # The header's toggle-all pills reflect current aggregate state but
        # only repaint on their own click or a model reset (CSV import) -
        # a per-cell toggle also needs to refresh them (e.g. going from
        # "all on" to "mixed" after one row's button is clicked).
        self.model.dataChanged.connect(lambda *a: self.table.horizontalHeader().viewport().update())
        self.table.setAlternatingRowColors(True)
        # CurrentChanged (not just DoubleClicked) so a single click on any
        # editable cell opens its editor immediately - no double-click
        # needed for the numeric fields (Tx/Rx toggle columns already
        # respond to a single click via ToggleDelegate.editorEvent,
        # independent of this setting).
        self.table.setEditTriggers(
            QAbstractItemView.CurrentChanged | QAbstractItemView.DoubleClicked
            | QAbstractItemView.EditKeyPressed
        )
        self.table.setMinimumHeight(320)
        self._tx_delegate = ToggleDelegate("Tx", self.table)
        self._rx_delegate = ToggleDelegate("Rx", self.table)
        for col in TX_TOGGLE_COLUMNS:
            self.table.setItemDelegateForColumn(col, self._tx_delegate)
        for col in RX_TOGGLE_COLUMNS:
            self.table.setItemDelegateForColumn(col, self._rx_delegate)

        # Size every column to fit its own header text by default (several
        # "Ch{n} <field>" headers were getting truncated with "..." at
        # whatever width Qt guessed initially).
        self.table.resizeColumnsToContents()
        # resizeColumnsToContents sizes the Tx/Rx toggle columns off their
        # raw bool value ("True"/"False"), not the "Tx On"/"Tx Off" text
        # ToggleDelegate actually paints, so re-set those explicitly to a
        # width that comfortably fits the real label.
        for col in TX_TOGGLE_COLUMNS + RX_TOGGLE_COLUMNS:
            self.table.setColumnWidth(col, 90)
        self.table.verticalHeader().setMinimumWidth(30)
        layout.addWidget(self.table)

        self.led_matrix = LedMatrix()
        layout.addWidget(self.led_matrix, 1)

        # Wrapped in a QScrollArea so this tab's minimumSizeHint stays small,
        # matching every other tab in this app.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)

        self.header_panel = HeaderPanel()

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll, 1)
        outer.addWidget(self.header_panel)

    def _on_invalid_data(self, message: str):
        # Non-modal, positioned right at the cell that was just edited,
        # rather than a dialog the user has to dismiss - this fires on
        # every out-of-range keystroke commit, so it shouldn't block.
        index = self.table.currentIndex()
        rect = self.table.visualRect(index)
        pos = self.table.viewport().mapToGlobal(rect.bottomLeft())
        QToolTip.showText(pos, message, self.table)

    def _on_import_clicked(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Dwell Data", "", "CSV Files (*.csv)")
        if not path:
            return
        try:
            channels, warnings = load_channels_from_csv(path)
        except Exception as e:
            QMessageBox.warning(self, "Import failed", f"Could not read '{path}':\n{e}")
            return
        self.model.load_channels(channels)
        self.summary_label.setText("Imported from CSV - not yet sent")
        if warnings:
            preview = "\n".join(warnings[:20])
            if len(warnings) > 20:
                preview += f"\n... and {len(warnings) - 20} more"
            QMessageBox.warning(self, "Invalid Data", f"{len(warnings)} invalid Tx/Rx flag(s) found:\n\n{preview}")

    def _on_save_clicked(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Dwell Data", "", "CSV Files (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            save_channels_to_csv(path, self.model.get_channels())
        except Exception as e:
            QMessageBox.warning(self, "Save failed", f"Could not write '{path}':\n{e}")
            return
        self.summary_label.setText(f"Saved to {path}")

    def get_channels(self):
        return self.model.get_channels()

    def mark_pending(self):
        self.summary_label.setText("Sent - waiting for response...")
        self.response_time_label.setText("")
        self.led_matrix.set_all(_PENDING_COLOR)

    def show_results(self, linked_flags):
        acked_count = sum(1 for v in linked_flags if v)
        self.summary_label.setText(f"{acked_count}/{NUM_QTRM} QTRMs acknowledged")
        self.led_matrix.set_results(linked_flags)

    def show_response_time(self, microseconds: float):
        self.response_time_label.setText(f"{microseconds:.0f} µs")

    def show_no_response(self):
        self.summary_label.setText("No response")
        self.response_time_label.setText("")
        self.led_matrix.set_all(_NOT_LINKED_COLOR)
