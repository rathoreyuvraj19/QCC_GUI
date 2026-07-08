"""
raw_slot_model.py

Generic byte-for-byte view of the 96 x 30-byte QTRM slots in a frame - no
per-command semantic decoding (no Dwell ID / channel names / etc.), just the
raw byte values. Only the four fields fixed at the same position in every
30-byte slot regardless of which command occupies it get a real name
(Header, Packet Size ID, Command Type, Status & Sub Status Type). Everything
else is shown generically ("Byte 5".."Byte 30") since its meaning depends on
which command is in that slot. Used by both the TX and RX raw packet test
windows so what actually went on the wire can be checked byte-by-byte,
independent of interpretation.

Not every one of the 30 bytes in a slot is necessarily part of the message -
per the IDD's message_length formula (packet_size_id * 5 + 10), a Packet
Size ID of 0x00 (the status-family commands: Link query, Cal, Isolation,
Soft Reset) only uses the first 10 bytes; the rest is zero padding. A Packet
Size ID of 0x04 (the full Dwell message, used by the Command/QTRM Grid tab)
uses all 30. Rather than a separate "Checksum" column (which only ever named
one specific byte and didn't convey this), each row's actual message bytes
are highlighted (Qt.BackgroundRole) up to message_length(packet_size_id),
leaving the trailing padding bytes (if any) at the default background - so
which bytes are real is visible at a glance, per row, regardless of that
row's own packet size.

The row itself IS the QTRM identifier - no separate "QTRM ID" data column
(that was a redundant duplicate of the row number, and worse, it was
1-indexed while every other QTRM matrix/LED in the app (Link Test, Soft
Reset, Isolation) labels QTRMs 0-95, which caused row 3 to visibly light up
when "QTRM-2" was clicked). The vertical header shows the row number
0-indexed instead, so it lines up with those "QTRM-N" labels directly.
"""

from PySide6.QtCore import QAbstractTableModel, Qt, QModelIndex
from PySide6.QtGui import QColor

from core.packet import QTRM_SLOT_SIZE, NUM_QTRM, message_length

_NAMED_HEAD = ["Header", "Packet Size ID", "Command Type", "Status & Sub Status"]
_GENERIC_START = 4                # byte index (0-based) where generic naming starts
_GENERIC_END = QTRM_SLOT_SIZE     # exclusive - every remaining raw byte is shown generically

_MESSAGE_HIGHLIGHT = QColor(0, 173, 181, 55)  # accent teal, low alpha - marks real message bytes


def _build_columns():
    cols = list(_NAMED_HEAD)
    for i in range(_GENERIC_START, _GENERIC_END):
        cols.append(f"Byte {i + 1}")  # 1-indexed byte number, per Yuvraj's numbering
    return cols


COLUMNS = _build_columns()


class RawSlotTableModel(QAbstractTableModel):
    """Read-only. slots is a list of NUM_QTRM raw 30-byte bytes-like objects."""

    def __init__(self, slots=None, parent=None):
        super().__init__(parent)
        self.slots = slots if slots is not None else [bytes(QTRM_SLOT_SIZE) for _ in range(NUM_QTRM)]
        self._hex_mode = False

    def rowCount(self, parent=QModelIndex()):
        return len(self.slots)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return COLUMNS[section]
        return f"QTRM-{section}"  # 0-indexed, matching Link Test/Soft Reset/Isolation labeling

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        slot = self.slots[row]

        if role == Qt.DisplayRole:
            value = slot[col]  # columns 0..29 map 1:1 onto slot bytes 0..29
            return f"{value:02X}" if self._hex_mode else value

        if role == Qt.BackgroundRole:
            if col < message_length(slot[1]):
                return _MESSAGE_HIGHLIGHT
            return None  # trailing padding byte for a shorter message - default background

        return None

    def set_hex_mode(self, enabled: bool):
        if enabled == self._hex_mode:
            return
        self._hex_mode = enabled
        if self.rowCount() and self.columnCount():
            self.dataChanged.emit(
                self.index(0, 0), self.index(self.rowCount() - 1, self.columnCount() - 1), [Qt.DisplayRole],
            )

    def replace_slots(self, new_slots):
        self.beginResetModel()
        self.slots = new_slots
        self.endResetModel()
