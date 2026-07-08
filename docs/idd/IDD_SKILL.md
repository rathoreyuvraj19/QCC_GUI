# IDD Generation Skill — QCC/QTRM Packet Documentation

This documents the approach developed for generating byte-level Interface
Design Documents (IDDs) as Word (.docx) tables, so it can be extended
consistently in Claude Code (or by hand) as the packet spec evolves.

## Files in this package

- `packet_spec.yaml` — the single source of truth for the packet layout.
  Every generated table/doc should be derived from this file, not
  hand-typed separately. Change the field here first, then regenerate.
- `build_90byte_table.js` — working Node.js generator (uses the `docx`
  npm package) that produces the full bit-level IDD tables: the Response
  Packet (QCC → RC, fixed) and the 5 mode-specific TX Packet tables
  (RC → QCC), including the SOB/PRT/PPS command breakdown for Loopback
  modes.

## Setup

```bash
npm install docx
node build_90byte_table.js
# outputs QCC_90Byte_Header_BitTable.docx in the same directory
```

## Conventions established (keep these when extending)

### 1. Table format
Landscape orientation, 12 columns: `Byte No.` | `Field Name` | `b7 b6 b5
b4 b3 b2 b1 b0` | `Value in Hex` | `Remarks`. This mirrors the original
reference IDD format the person supplied (bit-position labels per byte,
not literal bit values, except for Reserved bytes where the value truly
is always 0).

### 2. Per-field color grading
Every **distinct field** gets its own color from a rotating palette
(`PALETTE` array in the script), applied consistently across all bytes
belonging to that field. Adjacent fields must look visually different —
this was an explicit, repeated request. Reserved bytes and the Checksum
keep their own fixed colors (`SHADE_RESV`, `SHADE_CHK`) outside the
rotation, since they aren't "different data," just unused/terminal bytes.

Colors reset per table (`colorIdx = 0` at the top of each
`buildXTable()` call) — since bytes 1-33 are built via the same
`buildCommonBytes()` call order in every table, this naturally produces
matching colors for the shared header fields across all tables without
needing a lookup map.

### 3. Multi-byte fields: vertical-merged Remarks
For a field spanning multiple bytes (e.g. a 4-byte counter), each byte
gets its own row (with correct per-byte bit labels, e.g. `b31-b24` for
the high byte), but the **Remarks** cell is vertically merged
(`VerticalMergeType.RESTART` / `CONTINUE` from the `docx` package) into
one cell spanning all of that field's rows — don't repeat the same
remark text N times.

### 4. Collapsing genuinely-undefined spans
When a large byte range has no real per-byte meaning yet (e.g. a 56-byte
Message Body that's entirely TBD for a given mode), collapse it into a
**single row** spanning the whole range (`pushBlock()` helper) rather
than N individual placeholder rows. Don't do this for Reserved bytes
that have individually-numbered names (`Reserved-0`, `Reserved-1`, ...)
— only for spans that are one undifferentiated "TBD" blob.

### 5. Direction split: Response vs TX
The Response packet (QCC → RC) is **always the same structure**
regardless of mode — one table. The TX packet (RC → QCC) **varies by
mode** — built as separate tables per mode (or per group of modes that
share identical body content, e.g. Internal + External Loopback share
one table since their body structure is identical). Bytes 1-33 are
repeated in full in every table, not factored out, since each table
needs to be self-contained and readable on its own.

### 6. Verification discipline
Every table-building function ends with an assertion that the byte
count actually reached the expected total (90 for the header, or the
appropriate sub-total for a breakdown table). This has caught real bugs
during development (off-by-one field sizes, forgotten reserved-byte
adjustments after inserting a new field). Keep this pattern — never
ship a table without this check passing.

### 7. Sub-command breakdown tables
When one byte acts as a selector for further sub-structure (e.g.
`COMMAND_TYPE` at byte 34 selecting SOB/PRT/PPS), show that byte as its
own row in the main table, collapse the remaining bytes into one
"see breakdown tables below" row, then append separate field-level
tables (not bit-exploded — one row per field, not per byte) for each
sub-command variant right after the main table.

## Open items as of this handoff

See `open_items` in `packet_spec.yaml`. As of this export:
- Time of Day (header bytes 15-18) format/units undefined
- Remote Programming (Mode 5) needs a full redesign — likely a chunked
  firmware-transfer structure (packet index, status/failure reporting),
  modeled on the `INF_XWARE_BITSTREAM` / `IAP_STATUS` pattern from the
  reference QTRM Firmware Upgrade Messages section
- SOB_WIDTH is specified as 16-bit in the packet spec, but the actual
  VHDL (`sob_gen.i_sob_width`) port is still 8-bit as of the last RTL
  review — needs reconciliation before implementation
- `qtrm_data_block_state` (populated/empty) confirmed only for Normal
  and Status modes; Loopback and Reset modes still TBD

## Suggested next step for Claude Code

Write a small codegen script that reads `packet_spec.yaml` directly and
emits: the Python `struct` format strings for the GUI's `packet.py`, C
struct/register definitions for the HPS side, and VHDL address-decode
constants — so all three (plus this IDD) stay locked to one file instead
of drifting independently. This was the original plan when the YAML was
created; only the IDD generator has been connected to it so far.
