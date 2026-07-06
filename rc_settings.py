"""
rc_settings.py

Holds the RC (host/GUI) side values that go into the editable part of the
first 32 bytes of every outgoing 90-byte header - Destination ID, Source
ID, Date/Month/Year, Time of Day, and the 14 reserved bytes. These are set
once (defaulting to the moment the GUI started) rather than per-command.

Deliberately NOT settable here, per Yuvraj's scoping of the RC Settings
tab:
  - PACKET_SIZE - always TOTAL_PACKET_SIZE, whatever's actually being sent.
  - COMMAND_ID / COMMAND_ID_REPEAT - determined by which command is being
    sent (build_header(command_id) takes it as an argument instead).
  - COMMAND_ACK - always 0 in this (RC -> QCC) direction.
  - MESSAGE_NUMBER - a running counter of every command sent this GUI
    session, starting at 1 and incrementing on every build_header() call.
    Not persisted across restarts - "simply the number of msg sent by
    gui, starting with 1,2,3...".

Command tabs' actual COMMAND_ID values (matching QCCHeaderRx.MODE_*, per
Yuvraj): Dwell/Link Test/Status/RX Cal/TX Cal/Isolation all send Normal
(0) - "no separate tab required for normal command". Soft Reset sends QCC
Reset (4). Memory Operation sends Remote Programming (5). Timing
Generation (SOB/PRT/PPS) sends Internal Loopback (1) or External Loopback
(2), whichever the operator picks per-send - no single fixed COMMAND_ID_*
constant for it, unlike every other tab above. The shared HeaderPanel's
"Query QCC Status" button (present on every tab) sends Status/Response
Only (3).
"""

import json
import os
import sys
from datetime import datetime

from packet import QCCHeaderRx

# In a PyInstaller-frozen exe, __file__ resolves to a temporary extraction
# folder (sys._MEIPASS) that's wiped after the process exits - saving there
# would silently lose every setting between runs. Use the real exe's own
# folder when frozen, the script's folder otherwise (dev/source checkout).
if getattr(sys, "frozen", False):
    _APP_DIR = os.path.dirname(sys.executable)
else:
    _APP_DIR = os.path.dirname(os.path.abspath(__file__))
_SAVE_PATH = os.path.join(_APP_DIR, "rc_settings.json")

COMMAND_ID_DWELL = QCCHeaderRx.MODE_NORMAL
COMMAND_ID_LINK_TEST = QCCHeaderRx.MODE_NORMAL
COMMAND_ID_STATUS = QCCHeaderRx.MODE_NORMAL
COMMAND_ID_RX_CAL = QCCHeaderRx.MODE_NORMAL
COMMAND_ID_TX_CAL = QCCHeaderRx.MODE_NORMAL
COMMAND_ID_ISOLATION = QCCHeaderRx.MODE_NORMAL
COMMAND_ID_SOFT_RESET = QCCHeaderRx.MODE_QCC_RESET
COMMAND_ID_MEMORY_OPERATION = QCCHeaderRx.MODE_REMOTE_PROGRAMMING
# The HeaderPanel's "Query QCC Status" button (Mode 3 - "QCC simply returns
# its current response packet, no action taken", per the doc) - distinct
# from COMMAND_ID_STATUS above, which is the per-QTRM Status tab's Normal
# (0) mode command.
COMMAND_ID_QCC_STATUS = QCCHeaderRx.MODE_STATUS_ONLY


class RCSettings:
    def __init__(self):
        now = datetime.now()
        self.destination_id = 1
        self.source_id = 2
        self.date = now.day
        self.month = now.month
        self.year = now.year
        self.time_of_day = now.hour * 3600 + now.minute * 60 + now.second
        self.reserved0 = bytes(14)
        # User-assigned labels for the 14 still-undefined reserved bytes
        # (bytes 19-32) - purely for the operator's own reference, nothing
        # in the protocol reads these; defaults to "RESERVED_19".."RESERVED_32".
        self.reserved_names = [f"RESERVED_{19 + i}" for i in range(14)]
        self._message_number = 0

    def next_message_number(self) -> int:
        self._message_number += 1
        return self._message_number

    def peek_message_number(self) -> int:
        """The number the *next* build_header() call will use, without consuming it."""
        return self._message_number + 1

    def build_header(self, command_id: int, message_body: bytes = b"") -> bytes:
        h = QCCHeaderRx(
            destination_id=self.destination_id,
            source_id=self.source_id,
            command_id=command_id,
            message_number=self.next_message_number(),
            date=self.date,
            month=self.month,
            year=self.year,
            time_of_day=self.time_of_day,
            reserved0=self.reserved0,
            message_body=message_body,
        )
        return h.to_bytes()

    def to_dict(self) -> dict:
        return {
            "destination_id": self.destination_id,
            "source_id": self.source_id,
            "date": self.date,
            "month": self.month,
            "year": self.year,
            "time_of_day": self.time_of_day,
            "reserved0": self.reserved0.hex(),
            "reserved_names": self.reserved_names,
        }

    def load_dict(self, d: dict):
        self.destination_id = d.get("destination_id", self.destination_id) & 0xFF
        self.source_id = d.get("source_id", self.source_id) & 0xFF
        self.date = d.get("date", self.date) & 0xFF
        self.month = d.get("month", self.month) & 0xFF
        self.year = d.get("year", self.year) & 0xFFFF
        self.time_of_day = d.get("time_of_day", self.time_of_day) & 0xFFFFFFFF
        reserved_hex = d.get("reserved0")
        if reserved_hex:
            raw = bytes.fromhex(reserved_hex)
            self.reserved0 = (raw + bytes(14))[:14]
        names = d.get("reserved_names")
        if names:
            self.reserved_names = (list(names) + self.reserved_names)[:14]

    def save(self, path: str = _SAVE_PATH):
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    def load(self, path: str = _SAVE_PATH) -> bool:
        if not os.path.exists(path):
            return False
        with open(path) as f:
            d = json.load(f)
        self.load_dict(d)
        return True


# Single shared instance - the whole app (RC Settings tab + every send
# handler in main_window.py) reads/writes this same object.
rc_settings = RCSettings()
rc_settings.load()  # keeps datetime-now() defaults if no saved file yet
