# QCC / LRU Control GUI

PySide6 desktop app for building, sending, and receiving the QCC UDP frame
(90-byte header + an LRU data block), per
[docs/idd/packet_spec.yaml](docs/idd/packet_spec.yaml) - the in-repo source
of truth for the packet layout (derived from `QCC_90Byte_Header_BitTable.docx`
and the QTRM Message Format IDD). See [README.md](README.md) for setup/run
instructions and a file-by-file overview.

## Tests

```bash
python -m unittest discover -s tests -t .
```

Stdlib `unittest`, headless, no extra install. **Run these after any IDD
change.** `tests/test_packet.py` checks every field offset in
`core/packet.py` against `docs/idd/packet_spec.yaml` instead of against
hand-copied constants, so the two can't drift apart unnoticed - which is the
failure mode this protocol actually has (a field that moves still produces a
right-length frame that passes its own checksum and means something else
entirely by the time it reaches the QCC). `tests/test_dispatch.py` checks
`main_window.py`'s `_COMMANDS` table against its `_begin_wait` call sites.
`TestArrayShape` in `tests/test_packet.py` covers what has to hold at any
array shape (see the invariant below); the offset tests deliberately pin the
reference 96x4 shape, which is what the spec's concrete byte numbers
describe.

Parsers in `core/packet.py` raise `FrameError` (a `ValueError`) on anything
that came off the wire; `assert` is reserved for the builders' own
invariants, since `python -O` strips asserts and would otherwise silently
disable every malformed-frame check.

## Invariants to preserve

- **The frame layout is configurable, so nothing may snapshot it at import
  time.** `core/lru_config.py` holds the array's shape - `num_lru` and
  `channels_per_lru` - and everything about the frame's size derives from
  those two via the IDD's formula, `slot = 5*channels + 10`. Read them
  through `core/packet.py`'s `num_lru()` / `channels_per_lru()` /
  `lru_slot_size()` / `total_packet_size()` **functions**. A
  `from core.packet import NUM_LRU`-style constant would freeze whatever was
  configured when that module happened to be imported and then quietly
  disagree with everything imported after; the same trap applies to default
  arguments (`def f(size=total_packet_size())`), module-level column/tuple
  tables, and anything else evaluated once at import. Where a value is
  genuinely expensive to recompute per call, cache it keyed on the shape
  (see `tabs/dwell_tab.py`'s `columns()`), don't hoist it to module scope.
  The 96 LRU x 4-channel default is byte-identical to the fixed 2970-byte
  layout this app started with, so a regression here shows up only at other
  shapes - which is what `TestArrayShape` exists for.

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
     LRU-targeted command via the DMA'd 2880-byte data block. They
     previously used `QCC_RESET`(4)/`REMOTE_PROGRAMMING`(5) under the old
     scheme, neither of which has a clean equivalent now (`QCC_RESET` is
     now a QCC-level PIO-pin reset unrelated to LRU soft-reset; real
     `REMOTE_PROGRAMMING` is now reserved for the 4196-byte bootloader
     protocol).
   - PPS has no Bypass counterpart in the new enum (only
     `PPS_INTERNAL_GEN` exists) - `main_window.py`'s Timing tab sends
     `PPS_INTERNAL_GEN` unconditionally now; confirm this is intended
     rather than a missing `PPS_BYPASS` command.
   See `docs/idd/packet_spec.yaml`'s `open_items` for the full detail.

5. **Remote Programming low-speed command framing RE-DECIDED 2026-07-19** -
   per Yuvraj: once LRUs+QCC are in low-speed mode, every RP command
   EXCEPT the bitstream DATA chunks sends `[90-byte header][10-byte inner
   command]` = 100 bytes, no payload padding - previously these were sent
   as 4196-byte frames zero-padded out to the full payload size. Only the
   actual bitstream DATA chunks (`CT_BITSTREAM_DATA`, 0x34 - the real
   file-upload payload) still use the full `[90-byte header][10-byte
   command][4096-byte payload]` = 4196-byte shape. Mode Step 1 (still
   per-LRU-addressed, not yet in low-speed mode) is unaffected - it keeps
   the 2970-byte replicated-slot frame.

   Header byte 34 (message_body offset 0, `QCC_COMMAND=0xFF`/
   `REMOTE_PROGRAMMING` only) is a **SubCommand** QCC itself reads and acts
   on, per Yuvraj: `0x00` = Broadcast (QCC fans the rest of the frame out
   to all 96 LRUs unmodified - the path every LRU-targeted RP command
   above already uses, since `rc_settings.build_header()` leaves
   `message_body` all-zero by default), `0x01` = QCC -> Low-Speed (Mode
   Step 2), `0x02` = QCC -> High-Speed (**value changed from `0x00`** to
   make room for Broadcast at `0x00`). See
   `RP_SUBCMD_BROADCAST`/`QCC_BODY_SWITCH_LOW_SPEED`/
   `QCC_BODY_SWITCH_HIGH_SPEED` in `apps/remote_prog_controller.py` and
   `remote_programming_subcommands`/`remote_programming_framing` in
   `docs/idd/packet_spec.yaml`.

   Also new: a **LRU -> High Speed** command/button
   (`OP_LRU_HIGH_SPEED`, `RemoteProgController.start_lru_high_speed()`),
   broadcasting the bootloader's existing `build_mode_change_mss_to_fab()`
   (command_type 0x32) via the normal SubCommand 0x00 broadcast path.
   LRUs already auto-return to high speed on their own after Programming
   completes, but this lets the operator force it explicitly; it doesn't
   touch the gate. The Remote Programming tab's former "Return to High
   Speed" button is renamed **"QCC -> High Speed"** and sits alongside the
   new **"LRU -> High Speed"** button as a small return-to-normal pair -
   LRU first, then QCC.

   `core/packet.py` (`RP_CMD_FRAME_SIZE`, `build_remote_programming_cmd_frame`),
   `apps/remote_prog_controller.py` (`_send_rp`, `_on_simple_timeout`),
   `apps/remote_prog_tester_app.py`'s mock responder, `tabs/remote_programming_tab.py`,
   `main_window.py`, and `core/udp_worker.py`'s `_VALID_TX_SIZES` are all
   updated to match. `docs/idd/QCC_Protocol.docx`'s Remote Programming
   table (byte 33/34 remarks) reflects the SubCommand scheme.

   **Single-LRU targeting (added 2026-07-19 per Yuvraj):** header byte 35
   (`LRU_SELECT`, message_body offset 1) in the SubCommand `0x01`/`0x02`
   frames picks which LRU(s) the low-speed session addresses - `0x00`-`0x5F`
   = one LRU (0-based id 0-95), `0xFF` = broadcast to all 96. QCC latches
   it into its `remote_prog_LRU_select` mux at mode-change time, so every
   subsequent SubCommand `0x00` frame reaches only the selected LRU, and
   QCC zero-fills the 95 non-selected slots in its 2970-byte responses.
   Mode Step 1 mirrors this GUI-side (LRUs are still per-slot addressed
   there): a single-LRU session puts the mode-change command in only the
   target's 30-byte slot, the other 95 slots all-zero. The Remote
   Programming tab's "Target" combo drives it
   (`RemoteProgController.target_lru`,
   `core/packet.py`'s `RP_LRU_SELECT_BROADCAST`); the selector locks
   while the gate is open so the value can't drift mid-session, and the
   mock responder latches/zero-fills the same way the real QCC will.

6. **Mode Step 1 confirmed to use QCC_COMMAND=DATA_DISTRIBUTION, not
   REMOTE_PROGRAMMING (2026-07-19 per Yuvraj)** - the 2970-byte
   replicated-slot frame (item 5 above) rides the QCC's existing DMA
   data-pipeline path (`QCC_COMMAND` byte 33 = `0x00`), not `0xFF`: QCC
   just moves the 2880-byte payload to the fabric unmodified, and it's the
   LRU bootloader firmware that interprets its own slot's first 10 bytes
   as a mode-change command, not QCC itself. `apps/remote_prog_controller.py`'s
   `start_mode_step1()` builds its header with `COMMAND_ID_DWELL` (the
   existing `DATA_DISTRIBUTION` alias) instead of
   `COMMAND_ID_REMOTE_PROGRAMMING`. `core/packet.py`'s framing comment and
   `docs/idd/packet_spec.yaml`'s `remote_programming_framing` no longer
   list the 2970-byte shape under `REMOTE_PROGRAMMING`'s SubCommands - it
   was removed from there since it never carried a SubCommand byte to
   begin with. No change needed on the mock responder
   (`apps/remote_prog_tester_app.py`) or `main_window.py`'s RX routing -
   neither gates on the query's `QCC_COMMAND` byte for this frame shape.

7. **GENERATOR_STATUS bit 2 (QCC Mode)** - header byte 82
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

8. **Mode Step 1's response is a bare 90-byte QCC header, not a 2970-byte
   per-LRU-slot echo** - only the QCC itself replies to the mode-change
   query; LRUs don't originate a reply to their own mode-change slot,
   same as Mode Step 2. `apps/remote_prog_tester_app.py`'s
   `_respond_mode_change()` builds this the same way
   `_respond_qcc_level()` does (echo header, swap source/destination,
   recompute checksum, no LRU slots) instead of the previous
   `_build_response_frame()`-based per-slot echo. GUI-side, this needed no
   change: `remote_prog_controller.py`'s `on_frame()` already treats a
   Mode Step 1 reply as an opaque frame it just logs, and
   `core/udp_worker.py`'s RX size check already accepts the 90-byte shape
   (`RP_QCC_LEVEL_FRAME_SIZE`) alongside the standard 2970-byte one.

9. **Tester app now logs no-ack commands, not just no-reply ones** -
   `apps/remote_prog_tester_app.py`'s Bitstream Receive announce (0x33
   post-MSS) and Mode Change MSS->Fabric (0x32) handlers previously only
   called `self.status.emit(...)` (a transient status-bar line) and
   returned an empty description, so `run()`'s `if response is None:
   continue` dropped the frame entirely - it never reached the Activity
   Log or Sent Packet Analysis panel, even though the command itself is a
   real 100-byte frame broadcast to all 96 LRUs and worth inspecting.
   Both handlers now return `(None, desc)` with a non-empty `desc`, and
   `run()` distinguishes "recognized, no reply" (`response is None` but
   `desc` set - still logged) from "not a recognized command at all"
   (`response is None` and `desc` empty - still dropped).

10. **OPEN - phase shifter full scale: does code 63 mean 354.375 deg or a
    literal 360 deg?** (raised 2026-07-28, to be discussed) - the GUI now
    shows the physical value in brackets next to every raw 6-bit
    phase/atten code (Dwell table, RX/TX Cal inputs, Status tab Details +
    hover popup), via `describe_phase()`/`describe_atten()` in
    `core/packet.py`. The **attenuator is settled**: 31.5 dB across 63
    steps = 0.5 dB per LSB, code 63 = exactly 31.5 dB full scale.
    The **phase shifter is not**. Two statements from Yuvraj conflict at
    the top end:
    - "0 means 0 degree phase shift and 63 means max phase shift 360" -
      implies 360/63 = 5.714... deg per LSB, code 63 = 360.000 deg.
    - "360/64 is 5.625" - implies 5.625 deg per LSB, code 63 = 354.375
      deg, with the (unreachable) 64th step being 360 deg = 0 deg.

    Currently implemented as **360/64 = 5.625 deg per LSB** (code 63 ->
    354.375 deg), on the reasoning that phase is periodic - a 64th step of
    360 deg would wrap to the same physical setting as code 0, so 64
    distinct steps of 5.625 covers the circle exactly once, which is also
    the standard 6-bit phase-shifter convention. That reading treats "63
    means max phase shift" as "63 is the maximum *code*", not "63 equals
    literally 360.000 deg". **Needs Yuvraj's confirmation against the
    actual phase-shifter part/RTL** - if code 63 really is a literal 360
    deg, change `PHASE_STEP_DEG` in `core/packet.py` back to `360.0 / 63`
    (that's the only line that needs to move; every display derives from
    it). Display precision is 3 decimals, which is lossless for 5.625
    steps but not for 360/63.

11. **Dip switch value shown as a GUI status - RESOLVED 2026-07-30** - per
    Yuvraj, response header byte 83 (`message_body_response` offset 49,
    previously the first byte of the 3-byte `reserved` field at offset
    [49,51]) is now `DIP_SWITCH`, an 8-bit field with no defined per-bit
    meaning - shown raw. The reserved run shrinks to 2 bytes (offset
    [50,51], absolute bytes 84-85). `docs/idd/packet_spec.yaml` and
    `core/packet.py`'s `QCCHeaderTx` (`_BODY_FMT`, `to_bytes`/`from_bytes`,
    byte-layout docstring, new `dip_switch_bit()` helper mirroring
    `sob_is_internal()`/`prt_is_internal()`) are updated to match. The
    "Last Received Header" sidebar (`widgets/header_panel.py`) shows a new
    **DIP_SWITCH** row in the "Misc" section, alongside `GENERATOR_STATUS`,
    displaying all 8 bits individually (`Bits[7..0]: b7 b6 ... b0`)
    plus the decimal value - same two-line label style already used for
    `GENERATOR_STATUS`'s SOB/PRT text. This is QCC-header-level, not
    per-LRU, so `tabs/status_tab.py`'s Details panel/hover popup (which
    only decodes per-LRU phase/atten codes) is unaffected.
    `docs/idd/QCC_Protocol.docx` byte 83 row is now updated too (Field
    Name -> `DIP_SWITCH`, bit columns restored to generic `b7..b0` labels
    like other unstructured-byte fields (not zeroed like `GENERATOR_STATUS`'s
    row, since DIP_SWITCH has no defined per-bit meaning), Value in Hex ->
    `0xXX`, Remarks noting all 8 bits are significant with no per-bit
    meaning). Rows 82 (`GENERATOR_STATUS`)/84/85 (`Reserved-16`/`Reserved-17`)
    untouched. Note also that
    `docs/idd/QCC_Protocol.yaml` (a separate raw docx-extraction dump,
    distinct from the hand-maintained `packet_spec.yaml`) is already stale
    on byte 82 (still lists `Reserved-14` instead of `GENERATOR_STATUS`
    from item 7) - it was left alone since it isn't part of the code-sync
    chain, but flagging in case it's meant to be regenerated.

12. **QCC chip ID moved to its own CHIP_ID_READ command/response pair, out
    of the standard response header** - per Yuvraj, the chip ID is 64 bits,
    not 32, so it doesn't fit the response header's old 4-byte `CHIP_ID`
    field (bytes 85-88). Those 4 bytes are reserved again
    (`RESERVED2`/`reserved_c` in `core/packet.py`/`packet_spec.yaml`).
    Reading the chip ID is now a separate exchange: TX is `QCC_COMMAND`
    `0x08` (`CHIP_ID_READ`), header-only, same shape as `QCC_STATUS`/
    `QCC_RESET`. The Response is NOT the standard 90-byte header - it's a
    standalone 10-byte frame (`[1-byte command echo][8-byte chip
    ID][1-byte CRC-8]`, see `ChipIdResponse`/`build_chip_id_response()`/
    `CHIP_ID_RESPONSE_SIZE` in `core/packet.py`). `core/udp_worker.py`
    accepts this size on RX alongside the standard 2970/90-byte shapes.
    The HeaderPanel (`widgets/header_panel.py`) gained a "Read Chip ID"
    button next to Query QCC Status/QCC Reset, and its own `CHIP_ID` row
    in a new "Chip ID (separate query)" section, populated by
    `show_chip_id_response()` rather than the usual `show_frame()` path
    (`main_window.py`'s `_on_frame_received` special-cases the
    `chip_id_read` kind before the normal header dispatch, the same way it
    already does for Remote Programming's multi-frame sessions).
    **Not yet covered**: `apps/status_responder_app.py`'s mock QCC
    responder doesn't special-case any `QCC_COMMAND` value (it always
    replies with the same fixed-shape frame regardless of which command
    triggered the query, a pre-existing simplification that already
    applied to `QCC_STATUS`/`QCC_RESET` too) - it won't return a real
    10-byte reply to a `CHIP_ID_READ` query, so this can't be exercised
    end-to-end against the mock yet.
