# QCC / 96x QTRM Control GUI

PySide6 desktop app for building, sending, and receiving the 2970-byte QCC
UDP frame (90-byte header + 96x30-byte QTRM data block), per
[docs/idd/packet_spec.yaml](docs/idd/packet_spec.yaml) - the in-repo source
of truth for the packet layout (derived from `QCC_90Byte_Header_BitTable.docx`
and the QTRM Message Format IDD). See [README.md](README.md) for setup/run
instructions and a file-by-file overview.

## Invariants to preserve

- **`delay_us` must stay comparable to Wireshark** (Yuvraj's explicit
  requirement, 2026-07-12). The query->response delay logged by
  `core/frame_logger.py` (and shown in the tabs) comes from
  `core/udp_worker.py`'s `perf_counter()` stamps taken immediately at the
  `sendto`/`recvfrom` socket calls - never move this timing into GUI code,
  Qt signal handlers, or anywhere downstream of the worker thread.
  Verified against tcpdump on loopback (20-pair run, separate-process
  responder): the logged value reads a consistent +0.14 to +0.26 ms above
  the kernel/pcap wire delta - one-sided (never below wire time) and
  stable, dominated by OS thread wake-up after `recvfrom`. That residual
  is the userland floor; anything that grows it (extra Python work between
  `recvfrom` and the timestamp, timing in a GUI slot) breaks the
  requirement.

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

5. **Remote Programming low-speed command framing RE-DECIDED 2026-07-19** -
   per Yuvraj: once QTRMs+QCC are in low-speed mode, every RP command
   EXCEPT the bitstream DATA chunks sends `[90-byte header][10-byte inner
   command]` = 100 bytes, no payload padding - previously these were sent
   as 4196-byte frames zero-padded out to the full payload size. Only the
   actual bitstream DATA chunks (`CT_BITSTREAM_DATA`, 0x34 - the real
   file-upload payload) still use the full `[90-byte header][10-byte
   command][4096-byte payload]` = 4196-byte shape. Mode Step 1 (still
   per-QTRM-addressed, not yet in low-speed mode) is unaffected - it keeps
   the 2970-byte replicated-slot frame.

   Header byte 34 (message_body offset 0, `QCC_COMMAND=0xFF`/
   `REMOTE_PROGRAMMING` only) is a **SubCommand** QCC itself reads and acts
   on, per Yuvraj: `0x00` = Broadcast (QCC fans the rest of the frame out
   to all 96 QTRMs unmodified - the path every QTRM-targeted RP command
   above already uses, since `rc_settings.build_header()` leaves
   `message_body` all-zero by default), `0x01` = QCC -> Low-Speed (Mode
   Step 2), `0x02` = QCC -> High-Speed (**value changed from `0x00`** to
   make room for Broadcast at `0x00`). See
   `RP_SUBCMD_BROADCAST`/`QCC_BODY_SWITCH_LOW_SPEED`/
   `QCC_BODY_SWITCH_HIGH_SPEED` in `apps/remote_prog_controller.py` and
   `remote_programming_subcommands`/`remote_programming_framing` in
   `docs/idd/packet_spec.yaml`.

   Also new: a **QTRM -> High Speed** command/button
   (`OP_QTRM_HIGH_SPEED`, `RemoteProgController.start_qtrm_high_speed()`),
   broadcasting the bootloader's existing `build_mode_change_mss_to_fab()`
   (command_type 0x32) via the normal SubCommand 0x00 broadcast path.
   QTRMs already auto-return to high speed on their own after Programming
   completes, but this lets the operator force it explicitly; it doesn't
   touch the gate. The Remote Programming tab's former "Return to High
   Speed" button is renamed **"QCC -> High Speed"** and sits alongside the
   new **"QTRM -> High Speed"** button as a small return-to-normal pair -
   QTRM first, then QCC.

   `core/packet.py` (`RP_CMD_FRAME_SIZE`, `build_remote_programming_cmd_frame`),
   `apps/remote_prog_controller.py` (`_send_rp`, `_on_simple_timeout`),
   `apps/remote_prog_tester_app.py`'s mock responder, `tabs/remote_programming_tab.py`,
   `main_window.py`, and `core/udp_worker.py`'s `_VALID_TX_SIZES` are all
   updated to match. `docs/idd/QCC_Protocol.docx`'s Remote Programming
   table (byte 33/34 remarks) reflects the SubCommand scheme.

6. **GENERATOR_STATUS bit 2 (QCC Mode)** - header byte 82
   (`GENERATOR_STATUS`, response direction) gained a third status bit per
   Yuvraj: bit 0 `SOB_STATE`, bit 1 `PRT_STATE` (unchanged), and now bit 2
   `QCC_MODE`/speed-toggle status (0 = normal high-speed mode, 1 =
   low-speed remote-programming mode). `core/packet.py`'s `QCCHeaderTx`
   gained `qcc_mode_low_speed()` (mirroring `sob_is_internal()`/
   `prt_is_internal()`) and `set_generator_state()` gained an optional
   `qcc_mode_low_speed` parameter. The "Last Received Header" sidebar
   (`widgets/header_panel.py`) shows this as a new **"QCC Mode"** section -
   deliberately the FIRST section in the panel, above "Routing / Command" -
   so the operator sees at a glance whether QCC is on the low-speed
   remote-programming link before anything else. `docs/idd/QCC_Protocol.docx`'s
   byte 82 GENERATOR_STATUS row reflects the bit 2 remark.
