# QCC / LRU Control GUI

PySide6 desktop app for building, sending, and receiving the QCC UDP frame
(90-byte header + an LRU data block), per
[docs/idd/packet_spec.yaml](docs/idd/packet_spec.yaml) — the in-repo source
of truth for the packet layout.

## Setup (Windows)

1. Install Python 3.10+ from python.org (check "Add to PATH" during install).
2. Open a terminal in this folder and install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Run it:
   ```
   python main.py
   ```

## Tests

```
python -m unittest discover -s tests -t .
```

Stdlib `unittest`, no pip install needed beyond `requirements.txt`; `pytest
tests/` also works if you have it. [tests/test_packet.py](tests/test_packet.py)
is mostly about **offsets** — it checks every field position in
`core/packet.py` against `docs/idd/packet_spec.yaml` rather than against
hand-copied constants, so a struct format string and the spec can't drift
apart unnoticed. That's the failure mode this protocol actually has: a field
that moves still produces a frame of the right length that passes its own
checksum and means something completely different by the time it reaches the
QCC. Run these after any IDD change.

## Build Standalone .exe (Windows)

To distribute the app without requiring users to install Python:

1. Install PyInstaller:
   ```
   pip install pyinstaller
   ```
2. Build the package:
   ```
   python build.py              # creates dist/qcc_gui/ folder
   python build.py --onefile    # creates single dist/qcc_gui.exe file
   ```

The folder mode (`--onedir`, default) is recommended for end-user
distribution — smaller and faster to run. The single-file mode
(`--onefile`) is slower on first launch but easier to copy around.

`build/` and `dist/` are build scratch and are **not** tracked in git.
Executables meant to be handed out get copied into `releases/` and committed
from there — see [releases/README.md](releases/README.md).

See [.claude/skills/build-qcc-gui/SKILL.md](.claude/skills/build-qcc-gui/SKILL.md)
for detailed build options and troubleshooting.

## What's here

### Core (no GUI dependency)

- `core/lru_config.py` — the array's shape (`num_lru`, `channels_per_lru`)
  and every size derived from it, plus the wire-format limits and the
  `lru_config.json` load/save. See [Array shape](#array-shape).
- `core/packet.py` — byte-exact struct definitions for the QCC RX/TX headers
  (`QCCHeaderRx` / `QCCHeaderTx`), the LRU slot, and the standalone
  10-byte `CHIP_ID_READ` response; CRC-8/CCITT (QCC header) and XOR checksum
  (LRU slot); and every frame build/parse function. Builders use `assert`
  for their own invariants; parsers raise `FrameError` (a `ValueError`) on
  anything that came off the wire, so validation survives `python -O`.
- `core/udp_worker.py` — background `QThread` owning the UDP socket, so the
  GUI never blocks on send/recv. Also where the query→response delay is
  timestamped (see "Response timing" below).
- `core/rc_settings.py` — the RC-side header field values (IDs, date/time,
  message counter) and `build_header()`, used by every command send.
- `core/frame_logger.py` — burn-test data logger (Tools → Start Data Logging
  (CSV)… in the main window). Streams one CSV row per query with its paired
  response side by side: MESSAGE_NUMBER, tx/rx wall-clock timestamps,
  socket-level round-trip delay in µs, command name, a result classification
  (OK / TIMEOUT / CRC_FAIL / MSG_NUM_MISMATCH / UNSOLICITED), and the raw
  response frame as hex (the query frame is not stored — burn-test Link Test
  queries are identical every send). Link Test rows additionally get
  per-LRU OK/NOT_OK columns (`lru_00`…, validated the same way as the Link
  Test tab's LEDs) plus `lru_ok_count`/`lru_not_ok_list` summaries —
  Link Test is the intended burn-test command; other commands log with those
  columns empty. Rows are flushed to disk as they happen so a multi-day run
  survives a crash; a red indicator in the connection bar shows the live
  pair/missing/LRU-fail counts while active.
- `core/lru_filter.py`, `core/command_style.py` — LRU selection/filtering
  helper and the shared send-button styling.

### GUI

- `main.py` — entry point.
- `main_window.py` — connection bar, the global "Last Received Header"
  sidebar, the command tabs, and the single-command-in-flight dispatch. Each
  command kind has one entry in the `_COMMANDS` table at the top of the file
  that drives its timeout, its response parsing, and its result display;
  adding a command means adding one entry, not editing three places.
- `tabs/` — one module per command tab (Dwell, Link Test, Status, RX/TX Cal,
  Isolation, Soft Reset, Memory Operation, Timing, Remote Programming, RC
  Settings, Mode).
- `widgets/header_panel.py` — the decoded "Last Received Header" sidebar,
  plus the Query QCC Status / QCC Reset / Read Chip ID buttons.
- `widgets/raw_slot_model.py` — `QAbstractTableModel` giving a byte-for-byte
  view of the LRU slots with no per-command decoding, used by the TX and RX
  raw packet test windows. LRU ID is positional (row index +
  1), not a field inside the 30 bytes.
- `widgets/` also holds the small shared controls (`spin_field.py`,
  `toggle_switch.py`, `segmented_control.py`, `titled_group.py`,
  `tx_forward_matrix.py`, `lru_layout.py`) and the dialogs
  (`plot_log_dialog.py`, `password_keypad_dialog.py`,
  `temp_conversion_dialog.py`).
- `ping_worker.py` — background ICMP ping for the connection bar's Ping Test.
- `theme.py` — app-wide palette/stylesheet.

### Standalone helper apps (`apps/`)

- `apps/status_responder_app.py` — mock QCC that answers queries, so the GUI
  can be exercised without hardware. Note it does not yet special-case
  `QCC_COMMAND`, so it always replies with the same fixed-shape frame
  (including for `CHIP_ID_READ`, which on real hardware returns a 10-byte
  frame instead).
- `apps/rx_test_app.py` / `apps/tx_test_window.py` — raw received/sent packet
  inspectors.
- `apps/remote_prog_controller.py` — drives the multi-frame Remote
  Programming session (mode changes, link check, LRU info, authenticate,
  bitstream upload, program).
- `apps/remote_prog_tester_app.py` — mock LRU bootloader responder for
  exercising Remote Programming end to end without hardware.
- `apps/bootloader_packet.py` — the inner 10-byte bootloader command set.
- `apps/plot_qcc_log.py` — offline analysis for the burn-test CSVs
  (`python apps/plot_qcc_log.py <log.csv>`, needs `pip install matplotlib`):
  prints loss %/delay percentiles/msg_number gaps/per-LRU failure ranking
  and plots delay-vs-time with timeouts marked, rolling loss %, LRU NOT_OK
  events vs time, the delay histogram, and NOT_OK count per LRU. Also
  reachable from inside the GUI via Tools → Plot Log File (CSV)…
  (`widgets/plot_log_dialog.py`), which picks a CSV and shows the same figure
  and summary text embedded in a dialog — no terminal needed. Several plot
  dialogs can be open at once to compare runs. Saved images default to a
  `plots/` folder next to wherever the app/script is run from.

## Array shape

The frame's size is not fixed. It derives from two numbers — how many LRUs
the array has, and how many channels each LRU carries — via the IDD's own
message-length formula:

```
slot  = 5 * channels + 10        # 4ch → 30, 6ch → 40, 24ch → 130
block = num_lru * slot
frame = 90 + block               # 96 x 4 → 2970
```

The `+10` is the slot's 9-byte preamble plus its trailing XOR checksum; the
`5` is one channel's Control/Tx Phase/Tx Atten/Rx Phase/Rx Atten. The slot's
Packet Size Identifier byte *is* the channel count, which is what makes the
receiver's `message_length(id) = id*5 + 10` resolve to the same slot size the
sender used.

Set it under **Configuration → Array Configuration**. It's saved to
`lru_config.json` and takes effect on restart — nothing rebuilds its grids or
tables live, and a tab still sized for the old shape would put wrong-length
frames on the wire.

Limits come from the wire format: 1–255 channels (the size identifier is one
byte), 1–255 LRUs (Remote Programming's `LRU_SELECT` reserves `0xFF` for
broadcast), and a frame no larger than 65535 bytes (`PACKET_SIZE` is a
uint16). The dialog previews the resulting sizes and refuses a pair that
doesn't fit.

The original 96 LRU × 4-channel array is the case that lands on a 30-byte
slot and a 2970-byte frame, and is the default. It's also the one shape with
a real physical layout in the app: the per-LRU grids draw it as the 6 Cold
Plate groups of 16 the hardware actually has, and fall back to a plain
sequential grid at any other count. See `core/lru_config.py`.

## Response timing

The query→response delay logged by `core/frame_logger.py` (and shown in the
tabs) is timestamped with `perf_counter()` immediately at the
`sendto`/`recvfrom` socket calls inside `core/udp_worker.py`'s thread —
never in GUI code or a Qt signal handler. Verified against tcpdump on
loopback: the logged value reads a consistent +0.14 to +0.26 ms above the
kernel/pcap wire delta — one-sided and stable, dominated by OS thread wake-up
after `recvfrom` — so it stays comparable to Wireshark. Anything that adds
Python work between `recvfrom` and the timestamp breaks that property.

## Byte order

Everything is little-endian (multi-byte QCC header fields use `<` struct
format). LRU slot fields are all single bytes, so no endianness concern
there.

## Current state / what's still open

Open protocol questions and pending confirmations are tracked in
[CLAUDE.md](CLAUDE.md) ("Open issues") and in `open_items` at the bottom of
[docs/idd/packet_spec.yaml](docs/idd/packet_spec.yaml). The main ones:

- **Phase shifter full scale** — whether raw code 63 means 354.375° (360/64
  per LSB, what's implemented) or a literal 360° (360/63). Attenuator is
  settled at 0.5 dB/LSB, 31.5 dB full scale.
- **`TIME_OF_DAY` format** (header bytes 15-18) is still TBD.
- **Which `QCC_COMMAND` value each tab should send** is a code-level judgment
  call for Soft Reset and Memory Operation; both currently use
  `DATA_DISTRIBUTION`.
- **PPS has no Bypass counterpart** in the current command enum — the Timing
  tab sends `PPS_INTERNAL_GEN` unconditionally.
