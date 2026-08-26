"""
lru_config.py

The array's shape - how many LRUs, and how many channels each one has - as
a single runtime configuration instead of the hardcoded 96 x 4 this app
started out as.

Everything about the frame's size derives from these two numbers via the
IDD's own message-length formula (Section 4):

    slot_size  = 5 * channels_per_lru + 10
    block_size = num_lru * slot_size
    total_size = 90 + block_size

The "+10" is the per-slot overhead the formula already accounts for: the
9-byte preamble (0xAA header, packet size identifier, command type, status
nibbles, message/dwell id, frequency id, 3 reserved) plus the trailing XOR
checksum byte. The "5" is one channel's Control/Tx Phase/Tx Atten/Rx
Phase/Rx Atten. The original 96x4 array is just the case that lands on
5*4+10 = 30 and 90 + 96*30 = 2970.

The slot's Packet Size Identifier byte IS channels_per_lru - that's what
makes message_length(id) = id*5 + 10 work on the receiving side, and it's
why a 4-channel LRU sends 0x04 there.

Consumers read these through core.packet's num_lru()/lru_slot_size()/
total_packet_size() accessors rather than importing values, because
`from core.packet import NUM_LRU` would snapshot whatever was configured
at import time - which depends on module import order and would silently
disagree with the rest of the app.

Changing the configuration takes effect on restart (main_window.py's
Configuration dialog writes the file and prompts); nothing in the GUI
rebuilds its grids live.
"""

import json
import os
import sys

# --- Wire-format constants that do NOT vary with the configuration --------

HEADER_BYTES = 90            # the QCC header is a fixed 90 bytes either way
CHANNEL_BYTES = 5            # Control, Tx Phase, Tx Atten, Rx Phase, Rx Atten
SLOT_OVERHEAD_BYTES = 10     # 9-byte slot preamble + 1 trailing XOR checksum

# --- Hard limits imposed by the wire format itself ------------------------
#
# channels_per_lru rides in the slot's Packet Size Identifier, one byte.
# num_lru is bounded by Remote Programming's LRU_SELECT byte, which reserves
# 0xFF for "broadcast to all" - so a real LRU index has to stay below 0xFF.
# The whole frame's size rides in the header's uint16 PACKET_SIZE field.
MAX_CHANNELS_PER_LRU = 255
MAX_NUM_LRU = 255
MAX_FRAME_BYTES = 0xFFFF
MAX_BLOCK_BYTES = MAX_FRAME_BYTES - HEADER_BYTES   # 65445

DEFAULT_NUM_LRU = 96
DEFAULT_CHANNELS_PER_LRU = 4

if getattr(sys, "frozen", False):
    _APP_DIR = os.path.dirname(sys.executable)
else:
    # core/ -> repo root, so the file sits next to connection_settings.json
    # and rc_settings.json rather than inside the package.
    _APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SAVE_PATH = os.path.join(_APP_DIR, "lru_config.json")


class ConfigError(ValueError):
    """A requested LRU/channel configuration doesn't fit the wire format."""


def slot_size_for(channels_per_lru: int) -> int:
    """The IDD's message-length formula: id*5 + 10 (Section 4)."""
    return channels_per_lru * CHANNEL_BYTES + SLOT_OVERHEAD_BYTES


def validate(num_lru: int, channels_per_lru: int) -> None:
    """Raise ConfigError with an operator-readable reason, or return None."""
    if not (1 <= channels_per_lru <= MAX_CHANNELS_PER_LRU):
        raise ConfigError(
            f"Channels per LRU must be 1-{MAX_CHANNELS_PER_LRU} "
            f"(the slot's Packet Size Identifier is a single byte), got {channels_per_lru}."
        )
    if not (1 <= num_lru <= MAX_NUM_LRU):
        raise ConfigError(
            f"LRU count must be 1-{MAX_NUM_LRU} "
            f"(0xFF is reserved for Remote Programming's broadcast LRU_SELECT), got {num_lru}."
        )
    block = num_lru * slot_size_for(channels_per_lru)
    if block > MAX_BLOCK_BYTES:
        raise ConfigError(
            f"{num_lru} LRUs x {channels_per_lru} channels is a "
            f"{HEADER_BYTES + block}-byte frame; the header's PACKET_SIZE field is "
            f"a uint16, so the frame can't exceed {MAX_FRAME_BYTES} bytes "
            f"(at {channels_per_lru} channels that's at most "
            f"{MAX_BLOCK_BYTES // slot_size_for(channels_per_lru)} LRUs)."
        )


class LRUConfig:
    """One array shape. Immutable in practice - replaced, not mutated."""

    __slots__ = ("num_lru", "channels_per_lru")

    def __init__(self, num_lru: int = DEFAULT_NUM_LRU,
                 channels_per_lru: int = DEFAULT_CHANNELS_PER_LRU):
        num_lru = int(num_lru)
        channels_per_lru = int(channels_per_lru)
        validate(num_lru, channels_per_lru)
        self.num_lru = num_lru
        self.channels_per_lru = channels_per_lru

    @property
    def slot_size(self) -> int:
        return slot_size_for(self.channels_per_lru)

    @property
    def block_size(self) -> int:
        return self.num_lru * self.slot_size

    @property
    def total_size(self) -> int:
        return HEADER_BYTES + self.block_size

    @property
    def is_default_array(self) -> bool:
        """True for the original 96 x 4-channel QTRM array."""
        return (self.num_lru == DEFAULT_NUM_LRU
                and self.channels_per_lru == DEFAULT_CHANNELS_PER_LRU)

    def to_dict(self) -> dict:
        return {"num_lru": self.num_lru, "channels_per_lru": self.channels_per_lru}

    def describe(self) -> str:
        return (f"{self.num_lru} LRUs x {self.channels_per_lru} ch "
                f"({self.slot_size}-byte slot, {self.total_size}-byte frame)")

    def __eq__(self, other):
        if not isinstance(other, LRUConfig):
            return NotImplemented
        return (self.num_lru == other.num_lru
                and self.channels_per_lru == other.channels_per_lru)

    def __repr__(self):
        return f"LRUConfig(num_lru={self.num_lru}, channels_per_lru={self.channels_per_lru})"


def from_dict(d: dict) -> LRUConfig:
    return LRUConfig(
        num_lru=d.get("num_lru", DEFAULT_NUM_LRU),
        channels_per_lru=d.get("channels_per_lru", DEFAULT_CHANNELS_PER_LRU),
    )


# --- The active configuration --------------------------------------------
#
# Read via config(); replaced via set_config(). Deliberately a module-level
# singleton rather than something threaded through every call site: the
# frame layout is a property of the hardware the app is pointed at, not of
# any one operation, and every builder/parser in core.packet needs it.

_active = LRUConfig()


def config() -> LRUConfig:
    return _active


def set_config(cfg: LRUConfig) -> None:
    """Swap the active configuration. Nothing already built rebuilds itself."""
    global _active
    _active = cfg


def save(cfg: LRUConfig = None, path: str = _SAVE_PATH) -> None:
    with open(path, "w") as f:
        json.dump((cfg or _active).to_dict(), f, indent=2)


def load(path: str = _SAVE_PATH) -> bool:
    """
    Load the saved configuration into the active singleton. Returns False if
    there's no file yet (first run - the 96x4 default stands). A file that
    exists but is corrupt or out of range is a hard failure rather than a
    silent fallback: quietly running a 2970-byte layout when the operator
    configured something else would put wrong-shaped frames on the wire.
    """
    if not os.path.exists(path):
        return False
    with open(path) as f:
        d = json.load(f)
    set_config(from_dict(d))
    return True
