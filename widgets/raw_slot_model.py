"""
raw_slot_model.py

Generic byte-for-byte view of a frame's LRU slots - no per-command
semantic decoding (no Dwell ID / channel names / etc.), just the raw byte
values. Only the four fields fixed at the same position in every slot
regardless of which command occupies it get a real name (Header, Packet
Size ID, Command Type, Status & Sub Status Type). Everything else is shown
generically ("Byte 5" onward) since its meaning depends on which command is
in that slot. Used by both the TX and RX raw packet test windows so what
actually went on the wire can be checked byte-by-byte, independent of
interpretation.

Not every byte in a slot is necessarily part of the message - per the IDD's
message_length formula (packet_size_id * 5 + 10), a Packet Size ID of 0x00
(the status-family commands: Link query, Cal, Isolation, Soft Reset) only
uses the first 10 bytes; the rest is zero padding. A Packet Size ID equal to
the array's channel count (the full Dwell message, used by the Command/LRU
Grid tab) uses the whole slot. Rather than a separate "Checksum" column
(which only ever named one specific byte and didn't convey this), each row's
actual message bytes are highlighted (Qt.BackgroundRole) up to
message_length(packet_size_id), leaving the trailing padding bytes (if any)
at the default background - so which bytes are real is visible at a glance,
per row, regardless of that row's own packet size.

How many byte columns there are follows the configured slot size (5*channels
+ 10, see core/lru_config.py), so the column list is built per view rather
than once at import.

The row itself IS the LRU identifier - no separate "LRU ID" data column
(that was a redundant duplicate of the row number, and worse, it was
1-indexed while every other LRU matrix/LED in the app (Link Test, Soft
Reset, Isolation) labels LRUs from 0, which caused row 3 to visibly light up
when "LRU-2" was clicked). The vertical header shows the row number
0-indexed instead, so it lines up with those "LRU-N" labels directly.
"""

from PySide6.QtCore import QAbstractTableModel, Qt, QModelIndex
from PySide6.QtGui import QColor

from core.packet import lru_slot_size, message_length, num_lru

_NAMED_HEAD = ["Header", "Packet Size ID", "Command Type", "Status & Sub Status"]
_GENERIC_START = 4                # byte index (0-based) where generic naming starts

_MESSAGE_HIGHLIGHT = QColor(0, 173, 181, 55)  # accent teal, low alpha - marks real message bytes


def build_columns():
    """Column headers for one LRU slot - four named fields, then one per remaining byte."""
    cols = list(_NAMED_HEAD)
    for i in range(_GENERIC_START, lru_slot_size()):
        cols.append(f"Byte {i + 1}")  # 1-indexed byte number, per Yuvraj's numbering
    return cols


class RawSlotTableModel(QAbstractTableModel):
    """Read-only. slots is a list of num_lru() raw slot-sized bytes-like objects."""

    def __init__(self, slots=None, parent=None):
        super().__init__(parent)
        if slots is None:
            slots = [bytes(lru_slot_size()) for _ in range(num_lru())]
        self.slots = slots
        # Captured once per model rather than per cell lookup - the columns
        # can't change under a live model, only across a restart.
        self.columns = build_columns()
        self._hex_mode = False

    def rowCount(self, parent=QModelIndex()):
        return len(self.slots)

    def columnCount(self, parent=QModelIndex()):
        return len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self.columns[section]
        return f"LRU-{section}"  # 0-indexed, matching Link Test/Soft Reset/Isolation labeling

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        slot = self.slots[row]

        # A received frame isn't always the full 2970 bytes - core/udp_worker.py
        # also accepts the 90-byte QCC-header-only reply (Mode Step 1/2, Mode
        # Back), and the TX side additionally sends 100-byte Remote Programming
        # command frames. Slicing those into 96 x 30-byte slots yields short or
        # entirely empty slots (Python slicing pads nothing and raises nothing),
        # while columnCount() stays fixed at 30 - so every byte access here has
        # to be bounds-checked or Qt logs an IndexError per repaint per cell.
        if col >= len(slot):
            return None

        if role == Qt.DisplayRole:
            value = slot[col]  # columns 0..29 map 1:1 onto slot bytes 0..29
            return f"{value:02X}" if self._hex_mode else value

        if role == Qt.BackgroundRole:
            # byte 1 is the Packet Size ID the message length derives from -
            # a 1-byte slot has no such byte to read.
            if len(slot) > 1 and col < message_length(slot[1]):
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
