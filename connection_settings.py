"""
connection_settings.py

Persists the Connection bar's Local Port / QCC IP / QCC Port across
restarts, per Yuvraj: "Local Port address, ip, QCC port section should
remember their latest value." Same frozen-exe-safe save path pattern as
rc_settings.py - see that module's docstring for why __file__ isn't safe
to use directly once packaged (PyInstaller onefile extracts to a wiped
temp folder each run).
"""

import json
import os
import sys

if getattr(sys, "frozen", False):
    _APP_DIR = os.path.dirname(sys.executable)
else:
    _APP_DIR = os.path.dirname(os.path.abspath(__file__))
_SAVE_PATH = os.path.join(_APP_DIR, "connection_settings.json")

DEFAULT_LOCAL_PORT = 5010
DEFAULT_QCC_IP = "192.168.1.10"
DEFAULT_QCC_PORT = 5000
# Defaults to the same value as the listen port - preserves the app's
# original single-socket behavior (bind once, send and receive on that same
# port) for anyone who never touches the new field. Only diverges from
# local_port when the user explicitly sets a different GUI Source Port.
DEFAULT_SOURCE_PORT = DEFAULT_LOCAL_PORT


class ConnectionSettings:
    def __init__(self):
        self.local_port = DEFAULT_LOCAL_PORT
        self.qcc_ip = DEFAULT_QCC_IP
        self.qcc_port = DEFAULT_QCC_PORT
        self.source_port = DEFAULT_SOURCE_PORT

    def to_dict(self) -> dict:
        return {
            "local_port": self.local_port,
            "qcc_ip": self.qcc_ip,
            "qcc_port": self.qcc_port,
            "source_port": self.source_port,
        }

    def load_dict(self, d: dict):
        self.local_port = int(d.get("local_port", self.local_port))
        self.qcc_ip = str(d.get("qcc_ip", self.qcc_ip))
        self.qcc_port = int(d.get("qcc_port", self.qcc_port))
        # Falls back to whatever local_port just loaded to (not the module
        # default) when reading an older settings file that predates this
        # field - keeps old single-socket behavior for existing users
        # instead of silently introducing a port mismatch.
        self.source_port = int(d.get("source_port", self.local_port))

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


# Single shared instance - main_window.py reads it for the Connection bar's
# initial values and writes to it on every field edit.
connection_settings = ConnectionSettings()
connection_settings.load()
