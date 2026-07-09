# QCC / 96x QTRM Control GUI

PySide6 desktop app for building, sending, and receiving the 2970-byte QCC
UDP frame (90-byte header + 96x30-byte QTRM data block), per
[docs/idd/packet_spec.yaml](docs/idd/packet_spec.yaml) - the in-repo source
of truth for the packet layout (derived from `QCC_90Byte_Header_BitTable.docx`
and the QTRM Message Format IDD). See [README.md](README.md) for setup/run
instructions and a file-by-file overview.

## Open issues (flagged 2026-07-06, not yet started)

1. **Ping button stuck / no hover-click feedback** - after one ping it stays
   green; re-clicking should flash grey briefly then resolve, but it updates
   too fast to see, and the button has no hover/pressed effect at all.
   Likely in `main_window.py`'s `_on_ping_clicked`/`_on_ping_result` and/or
   `ping_worker.py` - probably needs the full `QPushButton { ... }`
   selector-block QSS pattern (other buttons in this app hit this same issue
   before) plus a deliberate minimum "pending" display duration.

2. **Status Responder should respond on whatever port the main GUI is
   listening on**, not a fixed port of its own - if the user changes the
   main GUI's Local Port (Connection bar), the responder currently can't be
   told to match it, so the main GUI stops receiving responses. Needs
   `status_responder_app.py`'s send-target port to derive from
   `main_window.py`'s `local_port_edit` value rather than being
   independently configured.

3. ~~**"PPS width not in GUI"**~~ - RESOLVED 2026-07-08, corrected same day:
   an earlier pass at this added a speculative `OUTPUT_PPS_WIDTH_US` field
   that turned out not to exist in the real IDD. Once
   `docs/idd/packet_spec.yaml` (the actual source of truth) landed in the
   repo, the real gap was that `QCCHeaderTx` was missing `INPUT_PRT_PRI`/
   `OUTPUT_PRT_PRI` (uint32 each, bytes 67-70/71-74) entirely, which pushed
   `INPUT_PPS_WIDTH_US` and `PPS_COUNTER` to the wrong offsets and left
   `RESERVED1` oversized. `core/packet.py`'s `QCCHeaderTx` and
   `widgets/header_panel.py` (new "PRT PRI (µs)" group) now match the spec.

4. **QCC_COMMAND redesign synced 2026-07-09, mapping needs confirmation** -
   the mode-based `COMMAND_ID` scheme (byte 5, 0=Normal..5=Remote
   Programming) was replaced by a flat `QCC_COMMAND` enum at byte 33
   (`DATA_DISTRIBUTION`/`QCC_STATUS`/`QCC_RESET`/`PRT_BYPASS`/`SOB_BYPASS`/
   `PRT_INTERNAL_GEN`/`SOB_INTERNAL_GEN`/`PPS_INTERNAL_GEN`/
   `REMOTE_PROGRAMMING`); byte 5 is now an unused `ECHO_BYTE`.
   `docs/idd/packet_spec.yaml`, `core/packet.py`, `core/rc_settings.py`,
   `main_window.py`'s Timing tab handlers, `widgets/header_panel.py`, and
   the standalone test apps (`tx_test_window.py`/`rx_test_app.py`/
   `status_responder_app.py`) are all updated to match. Two mappings were
   **assumed, not stated in the new IDD, and need Yuvraj's confirmation**:
   - Soft Reset and Memory Operation are now mapped to `DATA_DISTRIBUTION`
     (0x00) in `core/rc_settings.py`, on the reasoning that both deliver a
     QTRM-targeted command via the DMA'd 2880-byte data block. They
     previously used `QCC_RESET`(4)/`REMOTE_PROGRAMMING`(5) under the old
     scheme, neither of which has a clean equivalent now (`QCC_RESET` is
     now a QCC-level PIO-pin reset unrelated to QTRM soft-reset; real
     `REMOTE_PROGRAMMING` is now reserved for the 4196-byte bootloader
     protocol).
   - PPS has no Bypass counterpart in the new enum (only
     `PPS_INTERNAL_GEN` exists) - `main_window.py`'s Timing tab sends
     `PPS_INTERNAL_GEN` unconditionally now; confirm this is intended
     rather than a missing `PPS_BYPASS` command.
   See `docs/idd/packet_spec.yaml`'s `open_items` for the full detail.
