---
name: idd-sync
description: Understands the QCC/QTRM Interface Design Documents (QCC_90Byte_Header_BitTable.docx and the QTRM Message Format IDD) and keeps core/packet.py, the GUI tabs/widgets, and README.md/CLAUDE.md in sync with them. Use this whenever the user says the IDD changed, shares a new/updated IDD docx, describes a field/byte/section being added, renamed, resized, or reinterpreted, or asks "update the GUI for the new IDD" / "does the code match the spec" / "why doesn't the GUI show field X". Trigger even if they just paste a changed field description without naming the skill.
---

This app's on-wire format is defined by two external documents: `QCC_90Byte_Header_BitTable.docx` (QCC header) and the "QTRM Message Format IDD" (QTRM command/status bodies, referenced throughout the code as "Section N"). Neither document lives in this repo as a file — the **in-repo mirror of the spec is the `Offset | Field | Size | Type | Notes` docstring tables** on `QCCHeaderRx`, `QCCHeaderTx`, and the per-command builder functions in [core/packet.py](../../../core/packet.py), plus the "Section N" / "Table N" comments scattered through `core/`, `tabs/`, and `widgets/`. Treat those docstrings as the source of truth to diff against — don't invent a parallel YAML/JSON spec file; this codebase already has one and a second copy would just drift.

## Workflow when the IDD changes

**1. Get the change.** Either:
- The user hands you an updated IDD document (`.docx`/`.pdf`) — load `anthropic-skills:docx` (or the pdf skill) to read it, and diff its relevant section against the matching docstring table in `core/packet.py`.
- The user hands you *two* versions (old + new) — diff those directly instead of the code, then apply the delta.
- The user just describes the change in words ("byte 12 is now RESERVED, add OUTPUT_PPS_WIDTH_US at byte 44") — treat that description as the diff; you don't need a file for this.

Whichever path, end up with a precise statement: which byte offset(s)/bit(s), which field name(s), old vs new meaning/size/type.

**2. Find every place the old definition is mirrored.** Don't assume `core/packet.py` is the only place a field lives. Run:

```bash
python .claude/skills/idd-sync/scripts/find_idd_refs.py "<field name or 'Section N' or 'byte NN'>"
```

This greps `.py`/`.md` across the repo (skipping `build/`/`dist/`) and groups hits by file. A field is typically mirrored in up to four places: the `packet.py` docstring table + struct code, a GUI tab or widget that exposes it (e.g. `header_panel.py`, `dwell_tab.py`, `status_tab.py`), `README.md`'s file overview, and `CLAUDE.md`'s open-issues list if it was previously flagged as ambiguous/TBD.

**3. Update in this order** (each layer depends on the one before it):

1. **`core/packet.py`** — the docstring table (keep the `Offset | Field | Size | Type | Notes` column alignment, other docstrings in the file follow it), the `_BODY_FMT` struct format string, `__init__` params/attributes, `to_bytes`/`from_bytes`, and any constant/enum tied to the field. A byte-size or offset change means every field *after* it in the same struct shifts too — check the whole table, not just the one row.
2. **Tabs/widgets** — whatever GUI surface reads or writes that field (a `SpinField`, a `QLineEdit`, a status-panel label). Search results from step 2 tell you which file. If a field is renamed, rename the widget label and any internal variable that shares the name; if resized, check any range validation (min/max) still matches.
3. **Docs** — `README.md`'s "What's here" section if it describes the byte layout, and `CLAUDE.md`'s "Open issues" section if this change resolves or touches one of the numbered items there.

**4. Don't guess past what the IDD actually says.** This codebase has an established convention for spec ambiguity: an inline comment starting `ASSUMPTION:` (see `core/packet.py` lines ~658-660, ~545-547) or a new numbered entry in `CLAUDE.md`'s Open issues, phrased so it's easy for the user to confirm/reject later — not silently picking an interpretation. If the described change is ambiguous (e.g. doesn't say what happens to the byte(s) it displaces), stop and ask, or write the assumption down explicitly rather than both.

**5. Verify the GUI actually changed**, don't just trust the diff. This repo has a companion skill, `run-qcc-qtrm-control` ([.claude/skills/run-qcc-qtrm-control/](../run-qcc-qtrm-control/SKILL.md)), that launches the real `MainWindow` and takes screenshots — use it to click into the affected tab and screenshot the field you just touched.

**6. Summarize.** End with: which byte offsets/fields changed, every file you edited, and any assumptions/open questions you flagged instead of guessing.

## Byte-math reference

- Everything is little-endian (`struct` format strings use `<`).
- QCC header total is 90 bytes (`FIXED_HEADER_SIZE` 32 + `QCC_HEADER_SIZE` 58); `QCCHeaderTx`'s checksum is CRC-8/CCITT (poly 0x07) over bytes 0-88, stored at byte 89. `QCCHeaderRx` (RC->QCC) is the older, not-yet-updated 58-byte-only layout — don't assume the two headers share a byte layout.
- QTRM slot is 30 bytes; its checksum is XOR of bytes 0-28 (not CRC-8), generated/verified GUI-side, not QCC-side.
- `message_length(packet_size_id) == packet_size_id * 5 + 10` (IDD Section 4) — a field whose presence depends on packet size needs this checked, not just the raw struct.
- Full frame is 2970 bytes (90-byte header + 96 x 30-byte QTRM block), **except** Remote Programming (Mode 5) TX frames, which are 4196 bytes (90 + 4096-byte payload + 10-byte inner bootloader command) — if your IDD change is about Mode 5, check `bootloader_packet.py` instead of/in addition to `core/packet.py`.

## Gotchas

- A field's `Notes` column often encodes cross-references ("Same as offset 4", "valid only when mode is External Loopback") — when you resize/move a field, grep for those notes too via `find_idd_refs.py`, since they won't literally contain the field's name.
- Some IDD tables are known-garbled in the source `.docx` (see `core/packet.py` around line 315) — if the user's described change conflicts with what's already flagged as a doc rendering issue, ask which one wins rather than silently overwriting a previously-confirmed workaround.
- `QCCHeaderRx` and `QCCHeaderTx` look like they should mirror each other (same field names) but currently don't share a byte layout — verify which header the change applies to before editing, don't fix both from one description.
