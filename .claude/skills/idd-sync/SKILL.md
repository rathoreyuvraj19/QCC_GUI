---
name: idd-sync
description: Understands the QCC/QTRM Interface Design Documents (QCC_90Byte_Header_BitTable.docx, docs/idd/packet_spec.yaml, and the QTRM Message Format IDD) and keeps core/packet.py, the GUI tabs/widgets, and README.md/CLAUDE.md in sync with them. Use this whenever the user says the IDD changed, hands you a YAML describing a new/changed byte layout, shares a new/updated IDD docx, describes a field/byte/section being added, renamed, resized, or reinterpreted, or asks "update the GUI for the new IDD" / "does the code match the spec" / "why doesn't the GUI show field X". Trigger even if they just paste a changed field description or a YAML snippet without naming the skill.
---

This app's on-wire format is defined by two external documents: `QCC_90Byte_Header_BitTable.docx` (QCC header) and the "QTRM Message Format IDD" (QTRM command/status bodies, referenced throughout the code as "Section N"). The docx itself lives at [docs/idd/QCC_90Byte_Header_BitTable.docx](../../../docs/idd/QCC_90Byte_Header_BitTable.docx). Its structured mirror is [docs/idd/packet_spec.yaml](../../../docs/idd/packet_spec.yaml) — **treat this YAML as the source of truth to diff against**, not the docx directly (the docx is the human-readable/presentation artifact; the YAML is what actually drives the code sync). From there it mirrors down into the `Offset | Field | Size | Type | Notes` docstring tables on `QCCHeaderRx`, `QCCHeaderTx`, and the per-command builder functions in [core/packet.py](../../../core/packet.py), plus the "Section N" / "Table N" comments scattered through `core/`, `tabs/`, and `widgets/`.

## Workflow when the IDD changes

**1. Get the change.** In order of preference:
- **The user hands you a YAML file** describing the new/changed layout (this is now the preferred handoff format — cheap for both sides to read/write, unambiguous, no OOXML parsing needed). Read it directly, diff it against the current [docs/idd/packet_spec.yaml](../../../docs/idd/packet_spec.yaml), and treat that diff as the change to apply. A YAML handoff typically looks like the shape used for the QCC_COMMAND redesign: a byte offset for the command selector, a `commands:` map of value → `{name, body}`, and a `bodies:` map of body-kind → ordered list of `{offset, size, name, type, note}` field entries. Don't assume the shape is fixed — read whatever structure the user actually sends.
- The user hands you an updated IDD document (`.docx`/`.pdf`) — load `anthropic-skills:docx` (or the pdf skill) to read it, and diff its relevant section against `packet_spec.yaml`. Prefer asking the user for a YAML instead if the change is nontrivial — docx table extraction is more error-prone and burns more of your context than a YAML diff.
- The user hands you *two* versions (old + new, in whatever format) — diff those directly, then apply the delta.
- The user just describes the change in words ("byte 12 is now RESERVED, add OUTPUT_PPS_WIDTH_US at byte 44") — treat that description as the diff; you don't need a file for this.

Whichever path, end up with a precise statement: which byte offset(s)/bit(s), which field name(s), old vs new meaning/size/type.

**2. Find every place the old definition is mirrored.** Don't assume `core/packet.py` is the only place a field lives. Run:

```bash
python .claude/skills/idd-sync/scripts/find_idd_refs.py "<field name or 'Section N' or 'byte NN'>"
```

This greps `.py`/`.md` across the repo (skipping `build/`/`dist/`) and groups hits by file. A field is typically mirrored in up to four places: the `packet.py` docstring table + struct code, a GUI tab or widget that exposes it (e.g. `header_panel.py`, `dwell_tab.py`, `status_tab.py`), `README.md`'s file overview, and `CLAUDE.md`'s open-issues list if it was previously flagged as ambiguous/TBD.

**3. Update in this order** (each layer depends on the one before it):

1. **[docs/idd/packet_spec.yaml](../../../docs/idd/packet_spec.yaml)** — merge the user's YAML diff in here first. This is the single source of truth everything else derives from; get it right before touching code.
2. **`core/packet.py`** — the docstring table (keep the `Offset | Field | Size | Type | Notes` column alignment, other docstrings in the file follow it), the `_BODY_FMT` struct format string, `__init__` params/attributes, `to_bytes`/`from_bytes`, and any constant/enum tied to the field. A byte-size or offset change means every field *after* it in the same struct shifts too — check the whole table, not just the one row. If the change introduces command-dependent body layouts (like the QCC_COMMAND redesign's PRT/SOB/PPS bodies), each body kind needs its own pack/unpack path, not one struct format trying to cover every case.
3. **Tabs/widgets** — whatever GUI surface reads or writes that field (a `SpinField`, a `QLineEdit`, a status-panel label). Search results from step 2 (`find_idd_refs.py`) tell you which file. If a field is renamed, rename the widget label and any internal variable that shares the name; if resized, check any range validation (min/max) still matches.
4. **Docs** — `README.md`'s "What's here" section if it describes the byte layout, `CLAUDE.md`'s "Open issues" section if this change resolves or touches one of the numbered items there, and the `QCC_90Byte_Header_BitTable.docx` if the user wants the human-readable doc regenerated to match (see the docx-polish note in Gotchas below).

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
- **Don't hand-build/edit the `.docx` bit-table in this environment for visual polish.** There's no LibreOffice/renderer available here, so formatting changes to `QCC_90Byte_Header_BitTable.docx` can only be verified blind (via XML inspection) or by round-tripping screenshots with the user — both are slow and error-prone (this bit us hard once: a naive `cell.merge()` left stray leftover text, and row-cloning carried stray `w:vMerge` continuation markers into unrelated rows). If the user wants the docx's visual formatting refined, point them to general chat (claude.ai), where the `docx` skill can actually render-and-check its own output. This environment's job is turning a finalized YAML/docx into working code — not perfecting the document's layout.
