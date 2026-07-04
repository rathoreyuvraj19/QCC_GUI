# QCC / 96x QTRM Control GUI

Skeleton PySide6 desktop app for building, sending, and receiving the
2970-byte QCC UDP frame (32-byte reserved header + 58-byte QCC header +
96x30-byte QTRM data block), per the IDDs discussed.

## Setup (Windows)

1. Install Python 3.10+ from python.org (check "Add to PATH" during install).
2. Open a terminal in this folder and install dependencies:
   ```
   pip install PySide6
   ```
3. Run it:
   ```
   python main.py
   ```

## What's here

- `packet.py` - byte-exact struct definitions for the QCC RX/TX headers and
  the 30-byte QTRM slot, CRC-8 (QCC header) and XOR checksum (QTRM slot),
  and full 2970-byte frame build/parse functions. No GUI dependency - can be
  unit tested standalone.
- `udp_worker.py` - background `QThread` that owns the UDP socket, so the
  GUI never blocks on send/recv.
- `qtrm_model.py` - `QAbstractTableModel` backing the 96-row QTRM grid
  (QTRM ID is positional - row index + 1 - not a field inside the 30 bytes).
- `main_window.py` - connection bar, QCC MODE/MSG_ID controls, last-response
  summary, and the 96-row editable table.
- `main.py` - entry point.

## Current state / what's still open

- **Top 32 fixed bytes**: sent as all-zero placeholder until that section is
  defined.
- **RX COMMAND_DATA (bytes 2-56 of the QCC header)**: not populated by the
  GUI yet since per-mode field definitions are still TBD.
- **QTRM per-slot editing**: the table lets you set Command Type, ACK
  fields, Dwell/Frequency ID, and all 4 channels' Control/Phase/Attenuation
  bytes per QTRM row. Values aren't validated against per-command-type
  rules yet (e.g. Dwell vs Status field meaning) - that can be added once
  we lock down which command types the GUI needs to support.
- **No plotting** - deferred, per your call.
- No packaging/exe build set up yet (PyInstaller would be the standard
  choice when you're ready to distribute a `.exe`).

## Byte order

Everything is little-endian (multi-byte QCC header fields use `<` struct
format). QTRM slot fields are all single bytes, so no endianness concern
there.
