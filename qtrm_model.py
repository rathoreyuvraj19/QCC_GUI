"""
qtrm_model.py

QAbstractTableModel wrapping the 96 QTRMSlot objects for display/editing in
a QTableView. QTRM ID is positional (row index + 1), matching the packet
spec - it is not a field inside the 30-byte slot itself.
"""

from PySide6.QtCore import QAbstractTableModel, Qt, QModelIndex

from packet import QTRMSlot, NUM_QTRM

# Column layout: fixed fields first, then 4 channels x 5 fields each
# Dwell/MSG ID and Frequency ID are not editable - not implemented in the
# QTRM firmware yet, so they're always left at 0.
FIXED_COLUMNS = [
    ("QTRM ID", "qtrm_id", False),
    ("Command Type", "command_type", True),
    ("ACK Type", "ack_type", True),
    ("ACK On/Off", "ack_on_off", True),
    ("Dwell/MSG ID", "dwell_id", False),
    ("Frequency ID", "frequency_id", False),
]

CHANNEL_FIELDS = [
    ("Control", "control"),
    ("Tx Phase", "tx_phase"),
    ("Tx Atten", "tx_atten"),
    ("Rx Phase", "rx_phase"),
    ("Rx Atten", "rx_atten"),
]

STATUS_COLUMNS = [
    ("Checksum OK", "checksum_ok", False),
]


def _build_columns():
    cols = list(FIXED_COLUMNS)
    for ch in range(1, 5):
        for label, attr in CHANNEL_FIELDS:
            cols.append((f"Ch{ch} {label}", (ch - 1, attr), True))
    cols.extend(STATUS_COLUMNS)
    return cols


COLUMNS = _build_columns()


class QTRMTableModel(QAbstractTableModel):
    def __init__(self, slots=None, parent=None):
        super().__init__(parent)
        self.slots = slots if slots is not None else [QTRMSlot(qtrm_id=i + 1) for i in range(NUM_QTRM)]

    # -- required overrides -------------------------------------------------

    def rowCount(self, parent=QModelIndex()):
        return len(self.slots)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return COLUMNS[section][0]
        return str(section + 1)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        slot = self.slots[index.row()]
        _, key, _ = COLUMNS[index.column()]

        if role in (Qt.DisplayRole, Qt.EditRole):
            if isinstance(key, tuple):
                ch_idx, attr = key
                return getattr(slot.channels[ch_idx], attr)
            if key == "checksum_ok":
                val = getattr(slot, "checksum_ok", None)
                return "-" if val is None else ("OK" if val else "FAIL")
            return getattr(slot, key)
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid():
            return False
        slot = self.slots[index.row()]
        _, key, editable = COLUMNS[index.column()]
        if not editable:
            return False
        try:
            ivalue = int(value)
        except (TypeError, ValueError):
            return False

        if isinstance(key, tuple):
            ch_idx, attr = key
            ivalue = max(0, min(255, ivalue))
            setattr(slot.channels[ch_idx], attr, ivalue)
        elif key in ("ack_type", "ack_on_off"):
            ivalue = max(0, min(15, ivalue))  # nibble range
            setattr(slot, key, ivalue)
        else:
            ivalue = max(0, min(255, ivalue))
            setattr(slot, key, ivalue)

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

    # -- convenience ----------------------------------------------------

    def replace_slots(self, new_slots):
        """Used after a response frame is parsed, to refresh the whole grid."""
        self.beginResetModel()
        self.slots = new_slots
        self.endResetModel()

    def get_slots(self):
        return self.slots
