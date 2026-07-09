"""
rc_settings.py

Holds the RC (host/GUI) side values that go into the editable part of the
first 32 bytes of every outgoing 90-byte header - Destination ID, Source
ID, Date/Month/Year, Time of Day, and the 14 reserved bytes. These are set
once (defaulting to the moment the GUI started) rather than per-command.

Deliberately NOT settable here, per Yuvraj's scoping of the RC Settings
tab:
  - PACKET_SIZE - always TOTAL_PACKET_SIZE, whatever's actually being sent.
  - ECHO_BYTE / QCC_COMMAND - determined by which command is being sent
    (build_header(command_id) takes the QCC_COMMAND value as an argument;
    ECHO_BYTE is no longer interpreted by QCC at all, always sent as 0).
  - COMMAND_ACK - always 0 in this (RC -> QCC) direction.
  - MESSAGE_NUMBER - a running counter of every command sent this GUI
    session, starting at 1 and incrementing on every build_header() call.
    Not persisted across restarts - "simply the number of msg sent by
    gui, starting with 1,2,3...".

Command tabs' actual QCC_COMMAND values (matching QCCHeaderRx.QCC_COMMAND_*,
redesigned 2026-07-09 - the old mode-based COMMAND_ID scheme is gone).
Dwell/Link Test/Status/RX Cal/TX Cal/Isolation all send DATA_DISTRIBUTION
(0x00) - they work by DMA'ing the 2880-byte QTRM data block, and
DATA_DISTRIBUTION is the only command that triggers that DMA write, per
the IDD ("no separate tab required for normal command" carries over
unchanged from the old Normal/0 mapping). Timing Generation (SOB/PRT/PPS)
picks Bypass vs Internal Gen per-send based on the operator's Loopback
switch - no single fixed COMMAND_ID_* constant for it, unlike every other
tab above. The shared HeaderPanel's "Query QCC Status" button (present on
every tab) sends QCC_STATUS (0x01).

ASSUMPTION (flagged for Yuvraj to confirm - see CLAUDE.md open issues):
Soft Reset and Memory Operation also work by delivering a QTRM-targeted
command in the data block (CMD_SOFT_RESET / CMD_DATA_STORAGE in
core/packet.py), so both are mapped to DATA_DISTRIBUTION (0x00) here too -
previously they used MODE_QCC_RESET(4)/MODE_REMOTE_PROGRAMMING(5)
respectively, which don't have a clean equivalent in the new 9-command
enum (QCC_RESET now means "reset FPGA-side buffers/counters via PIO pin",
a QCC-level action unrelated to sending a QTRM soft-reset command; and
REMOTE_PROGRAMMING is now reserved for the actual 4196-byte bootloader
protocol, distinct in framing from Memory Operation's standard 2970-byte
frames). Confirm this mapping is correct before relying on it against
real hardware.
"""

import json
import os
import sys
from datetime import datetime

from core.packet import QCCHeaderRx

# In a PyInstaller-frozen exe, __file__ resolves to a temporary extraction
# folder (sys._MEIPASS) that's wiped after the process exits - saving there
# would silently lose every setting between runs. Use the real exe's own
# folder when frozen, the script's folder otherwise (dev/source checkout).
if getattr(sys, "frozen", False):
    _APP_DIR = os.path.dirname(sys.executable)
else:
    _APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SAVE_PATH = os.path.join(_APP_DIR, "rc_settings.json")

COMMAND_ID_DWELL = QCCHeaderRx.QCC_COMMAND_DATA_DISTRIBUTION
COMMAND_ID_LINK_TEST = QCCHeaderRx.QCC_COMMAND_DATA_DISTRIBUTION
COMMAND_ID_STATUS = QCCHeaderRx.QCC_COMMAND_DATA_DISTRIBUTION
COMMAND_ID_RX_CAL = QCCHeaderRx.QCC_COMMAND_DATA_DISTRIBUTION
COMMAND_ID_TX_CAL = QCCHeaderRx.QCC_COMMAND_DATA_DISTRIBUTION
COMMAND_ID_ISOLATION = QCCHeaderRx.QCC_COMMAND_DATA_DISTRIBUTION
# ASSUMPTION: changed from the old MODE_QCC_RESET(4) - see module docstring above.
COMMAND_ID_SOFT_RESET = QCCHeaderRx.QCC_COMMAND_DATA_DISTRIBUTION
# ASSUMPTION: changed from the old MODE_REMOTE_PROGRAMMING(5) - see module docstring above.
COMMAND_ID_MEMORY_OPERATION = QCCHeaderRx.QCC_COMMAND_DATA_DISTRIBUTION
# The Remote Programming tab proper (firmware update over the bootloader
# link, 4196-byte frames) - now has its own dedicated command value,
# distinct from Memory Operation above (which no longer shares a command
# value with it, unlike the pre-redesign scheme).
COMMAND_ID_REMOTE_PROGRAMMING = QCCHeaderRx.QCC_COMMAND_REMOTE_PROGRAMMING
# The HeaderPanel's "Query QCC Status" button ("QCC simply returns its
# current response packet, no action taken", per the doc) - distinct from
# COMMAND_ID_STATUS above, which is the per-QTRM Status tab's
# DATA_DISTRIBUTION command.
COMMAND_ID_QCC_STATUS = QCCHeaderRx.QCC_COMMAND_QCC_STATUS


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

    def build_header(self, command_id: int, message_body: bytes = b"",
                     packet_size: int = None) -> bytes:
        h = QCCHeaderRx(
            destination_id=self.destination_id,
            source_id=self.source_id,
            qcc_command=command_id,
            message_number=self.next_message_number(),
            date=self.date,
            month=self.month,
            year=self.year,
            time_of_day=self.time_of_day,
            reserved0=self.reserved0,
            message_body=message_body,
        )
        if packet_size is not None:
            # Only the Remote Programming tab's 4196-byte frames override
            # this; everything else keeps the fixed 2970.
            h.packet_size = packet_size & 0xFFFF
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
