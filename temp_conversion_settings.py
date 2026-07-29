"""
temp_conversion_settings.py

Persists the raw-byte -> Deg C conversion formula applied to Health's
temperature_status field on the Status tab (Details panel + hover popup):
deg_c = raw * multiplier + offset. Defaults (0.9768 / -50.0) are Yuvraj's
supplied formula. Editable via Tools -> Temperature Conversion Formula...
Same frozen-exe-safe save path pattern as connection_settings.py - see that
module's docstring for why __file__ isn't safe to use directly once
packaged.
"""

import json
import os
import sys

if getattr(sys, "frozen", False):
    _APP_DIR = os.path.dirname(sys.executable)
else:
    _APP_DIR = os.path.dirname(os.path.abspath(__file__))
_SAVE_PATH = os.path.join(_APP_DIR, "temp_conversion_settings.json")

DEFAULT_MULTIPLIER = 0.9768
DEFAULT_OFFSET = -50.0


class TempConversionSettings:
    def __init__(self):
        self.multiplier = DEFAULT_MULTIPLIER
        self.offset = DEFAULT_OFFSET

    def convert(self, raw_value: int) -> float:
        return raw_value * self.multiplier + self.offset

    def to_dict(self) -> dict:
        return {"multiplier": self.multiplier, "offset": self.offset}

    def load_dict(self, d: dict):
        self.multiplier = float(d.get("multiplier", self.multiplier))
        self.offset = float(d.get("offset", self.offset))

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


# Single shared instance - status_tab.py reads it to convert temperature_status
# on display; the Tools menu dialog edits it directly and saves.
temp_conversion_settings = TempConversionSettings()
temp_conversion_settings.load()
