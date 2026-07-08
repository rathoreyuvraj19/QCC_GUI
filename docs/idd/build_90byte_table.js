const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  WidthType, ShadingType, AlignmentType, VerticalAlign, BorderStyle,
  Header, Footer, PageNumber, PageBreak, VerticalMergeType, HeadingLevel,
} = require("docx");
const fs = require("fs");

const THIN = { style: BorderStyle.SINGLE, size: 2, color: "999999" };
const THICK = { style: BorderStyle.SINGLE, size: 16, color: "000000" };

// Landscape US Letter, 0.5in margins -> usable width 14400 DXA
const FULL_WIDTH = 14400;
const COL_WIDTHS = [600, 2400, 650, 650, 650, 650, 650, 650, 650, 650, 1000, 5200];
// Byte No | Field Name | b7 b6 b5 b4 b3 b2 b1 b0 | Value in Hex | Remarks

function cell(text, opts = {}) {
  const { width, bold = false, shade = null, align = AlignmentType.CENTER, size = 16, italic = false, color = null, topBorder = null, verticalMerge = null } = opts;
  return new TableCell({
    width: { size: width, type: WidthType.DXA },
    shading: shade ? { type: ShadingType.CLEAR, fill: shade } : undefined,
    verticalAlign: VerticalAlign.CENTER,
    verticalMerge: verticalMerge || undefined,
    margins: { top: 30, bottom: 30, left: 60, right: 60 },
    borders: { top: topBorder || THIN, bottom: THIN, left: THIN, right: THIN },
    children: verticalMerge === VerticalMergeType.CONTINUE ? [] : [new Paragraph({ alignment: align, children: [new TextRun({ text, bold, size, italics: italic, color: color || undefined })] })],
  });
}

function headerRow() {
  const labels = ["Byte No.", "Field Name", "b7", "b6", "b5", "b4", "b3", "b2", "b1", "b0", "Value in Hex", "Remarks"];
  return new TableRow({
    tableHeader: true,
    children: labels.map((l, i) => cell(l, { width: COL_WIDTHS[i], bold: true, shade: "D9E2F3", size: 16, align: AlignmentType.LEFT })),
  });
}

function dataRow(r) {
  const vals = [String(r.byte), r.field, ...r.bits, r.value, r.remarks];
  return new TableRow({
    children: vals.map((v, i) => {
      const align = i === 1 || i === 11 ? AlignmentType.LEFT : AlignmentType.CENTER;
      const italic = i === 11 && r.tbd;
      const color = i === 11 && r.tbd ? "C0504D" : null;
      const vMerge = i === 11 ? r.remarkMerge : null;
      return cell(v, { width: COL_WIDTHS[i], shade: r.shade, align, italic, color, topBorder: r.newGroup ? THICK : null, verticalMerge: vMerge });
    }),
  });
}

// ---- Per-field color palette - each distinct field gets its own color,
// consistently applied across all of that field's own bytes. ----
const PALETTE = [
  "E1D5E7", "FCE4D6", "F8CBAD", "FFE699", "C9DAF8", "A9C4EB", "D5A6BD", "A9CCE3", "A3E4D7", "D2B4DE",
  "B6D7A8", "F4CCCC", "D9D2E9", "FCE5CD", "D0E0E3", "EAD1DC", "C6E2FF", "E6B8AF", "B4A7D6", "D9EAD3",
  "FFE5B4", "CFE2F3", "F9CB9C", "B4E7CE", "E4C1F9", "F6DDCC", "C9E4DE", "F2C6DE", "D5E8D4", "FFDAB9",
];
const SHADE_RESV = "F2F2F2";      // gray - Reserved (always, not part of rotation)
const SHADE_CHK = "FFF2CC";       // pale yellow - Checksum (always, not part of rotation)

function bitLabels(byteIndexInField, fieldByteLen) {
  if (fieldByteLen === 1) return ["b7", "b6", "b5", "b4", "b3", "b2", "b1", "b0"];
  const hi = byteIndexInField * 8 + 7;
  const out = [];
  for (let b = hi; b >= hi - 7; b--) out.push("b" + String(b).padStart(2, "0"));
  return out;
}

function fixedZeroBits() {
  return ["0", "0", "0", "0", "0", "0", "0", "0"];
}

// ---------------------------------------------------------------------------
// Row builder factory - each call returns its own rows array + byte counter,
// so response/command tables never share state.
// ---------------------------------------------------------------------------

function makeBuilder() {
  const rows = [];
  let byteNo = 1;

  function pushSingle(field, remarks, shade, value, tbd, newGroup) {
    rows.push({ byte: byteNo, field, bits: bitLabels(0, 1), value, remarks, shade, tbd, newGroup });
    byteNo++;
  }

  function pushMultiByte(fieldBase, remarksBase, nBytes, shade, tbd, value) {
    const labels = nBytes === 2
      ? ["Lower Order Byte", "Higher Order Byte"]
      : nBytes === 4
        ? ["Lower Order Byte", "", "", "Higher Order Byte"]
        : Array(nBytes).fill("");
    for (let i = 0; i < nBytes; i++) {
      const suffix = labels[i] ? ` - ${labels[i]}` : "";
      rows.push({
        byte: byteNo,
        field: `${fieldBase}${suffix}`,
        bits: bitLabels(i, nBytes),
        value: value || "0xXX",
        remarks: i === 0 ? remarksBase : "",
        remarkMerge: i === 0 ? VerticalMergeType.RESTART : VerticalMergeType.CONTINUE,
        shade,
        tbd,
        newGroup: i === 0,
      });
      byteNo++;
    }
  }

  function pushReservedRun(count, startIndex, remarks) {
    for (let i = 0; i < count; i++) {
      rows.push({ byte: byteNo, field: `Reserved-${startIndex + i}`, bits: fixedZeroBits(), value: "0x00", remarks: remarks || "Reserved Byte", shade: SHADE_RESV, tbd: false, newGroup: i === 0 });
      byteNo++;
    }
  }

  function pushBlock(field, remarks, shade, size, tbd) {
    const startByte = byteNo;
    const endByte = byteNo + size - 1;
    rows.push({
      byte: `${startByte}-${endByte}`,
      field,
      bits: ["-", "-", "-", "-", "-", "-", "-", "-"],
      value: "0xXX",
      remarks,
      shade,
      tbd,
      newGroup: true,
    });
    byteNo += size;
  }

  return { rows, pushSingle, pushMultiByte, pushReservedRun, pushBlock, getByteNo: () => byteNo };
}

// ---------------------------------------------------------------------------
// Bytes 1-33 are identical in structure for every response table and the
// command table. cmdIdDisplay lets each mode-specific response show its own
// actual Command ID value instead of a generic 0xXX.
// ---------------------------------------------------------------------------

function buildCommonBytes(b, nextShade, isResponse, cmdIdDisplay) {
  b.pushSingle("Destination ID", isResponse
    ? "QCC fills this by swapping - echoes the command's Source ID back as this response's Destination ID."
    : "Filled by RC (GUI). QCC will swap this into Source ID when it builds its response.",
    nextShade(), "0xXX", false, true);

  b.pushSingle("Source ID", isResponse
    ? "QCC fills this by swapping - echoes the command's Destination ID back as this response's Source ID."
    : "Filled by RC (GUI). QCC will swap this into Destination ID when it builds its response.",
    nextShade(), "0xXX", false, true);

  b.pushMultiByte("Packet Size", "Total packet size = 2970 bytes (90-byte header + 2880-byte QTRM data block). Same fixed value in both the command and the response - QCC does not compute a different size, it writes the same constant.", 2, nextShade(), false);

  b.pushSingle("Command ID", isResponse
    ? `QCC's operating mode for this response, echoed from the command. This table: ${cmdIdDisplay}.`
    : "Selects QCC operating mode: 0=Normal, 1=Internal Loopback, 2=External Loopback, 3=Status/Response Only, 4=QCC Reset, 5=Remote Programming",
    nextShade(), cmdIdDisplay || "0xXX", false, true);

  b.pushSingle("Command/Acknowledgement", isResponse
    ? "QCC fills 0x01, indicating this packet is QCC's response back to RC."
    : "RC fills 0x00, indicating this packet is a command sent by RC to QCC.",
    nextShade(), isResponse ? "0x01" : "0x00", false, true);

  b.pushMultiByte("Message Number", isResponse
    ? "Counter value echoed back from the command, so RC can identify which message this response corresponds to."
    : "Counter of messages sent by RC to QCC, incremented per message.",
    4, nextShade(), false);

  b.pushSingle("DATE", isResponse
    ? "Copied unchanged from the command by QCC. Decimal value (not hex), 01-31."
    : "Filled by RC. Decimal value (not hex), 01-31.",
    nextShade(), "01-31 (dec)", false, true);

  b.pushSingle("MONTH", isResponse
    ? "Copied unchanged from the command by QCC. Decimal value (not hex), 01-12."
    : "Filled by RC. Decimal value (not hex), 01-12.",
    nextShade(), "01-12 (dec)", false, true);

  b.pushMultiByte("YEAR", (isResponse
    ? "Copied unchanged from the command by QCC. "
    : "Filled by RC. ") + "Current year, decimal (not hex). Lower Order Byte and Higher Order Byte combine per byte order (little-endian) to form the full decimal year value.",
    2, nextShade(), false, "XX (dec)");

  b.pushMultiByte("Time of the day", isResponse
    ? "Copied exactly from the command by QCC, unchanged. Exact format/units still TBD."
    : "Filled by RC (GUI). Exact format/units (e.g. ms-since-midnight vs packed HHMMSS) still TBD.",
    4, nextShade(), true);

  b.pushReservedRun(13, 0);
  b.pushReservedRun(1, 13, "Reserved Byte (previously a standalone checksum - removed; single CRC-8 at byte 90 now covers the whole header)");

  b.pushSingle("Command ID (same as byte 5)", isResponse
    ? "Same value as Command ID (byte 5); QCC copies it back unchanged here too."
    : "Same value as Command ID (byte 5); repeated here by RC for framing/validation.",
    nextShade(), cmdIdDisplay || "0xXX", false, true);
}

function pushTelemetryBody(b, nextShade) {
  b.pushMultiByte("FPGA_TEMPERATURE", "FPGA die temperature. 10 usable bits, 2's complement signed value in bits 0-9; bits 10-15 = 0 (fixed).", 2, nextShade(), false);
  b.rows[b.rows.length - 1].bits = ["0", "0", "0", "0", "0", "0", "b09", "b08"];
  b.pushMultiByte("BOARD_TEMPERATURE", "Board temperature sensor reading, 16-bit.", 2, nextShade(), false);
  b.pushMultiByte("BOARD_HUMIDITY", "Board humidity sensor reading, 16-bit.", 2, nextShade(), false);

  b.pushMultiByte("INPUT_SOB_COUNT", "SOB count measured on input.", 4, nextShade(), false);
  b.pushMultiByte("INPUT_PRT_COUNT", "PRT count measured on input.", 4, nextShade(), false);
  b.pushMultiByte("INPUT_PPS_COUNT", "PPS count measured on input.", 4, nextShade(), false);
  b.pushMultiByte("OUTPUT_PRT_COUNT", "PRT count measured on output.", 4, nextShade(), false);
  b.pushMultiByte("OUTPUT_SOB_COUNT", "SOB count measured on output.", 4, nextShade(), false);

  b.pushMultiByte("INPUT_SOB_WIDTH_US", "Last SOB pulse width, input, microseconds.", 2, nextShade(), false);
  b.pushMultiByte("OUTPUT_SOB_WIDTH_US", "Last SOB pulse width, output, microseconds.", 2, nextShade(), false);
  b.pushMultiByte("INPUT_PRT_WIDTH_US", "Last PRT pulse width, input, microseconds.", 2, nextShade(), false);
  b.pushMultiByte("OUTPUT_PRT_WIDTH_US", "Last PRT pulse width, output, microseconds.", 2, nextShade(), false);
  b.pushMultiByte("INPUT_PRT_PRI", "PRT PRI (Pulse Repetition Interval) measured on input, microseconds (32-bit).", 4, nextShade(), false);
  b.pushMultiByte("OUTPUT_PRT_PRI", "PRT PRI (Pulse Repetition Interval) measured on output, microseconds (32-bit).", 4, nextShade(), false);
  b.pushMultiByte("INPUT_PPS_WIDTH_US", "Last PPS pulse width, input, microseconds.", 2, nextShade(), false);

  b.pushMultiByte("PPS_COUNTER", "Separate 32-bit PPS counter (distinct from INPUT_PPS_COUNT above).", 4, nextShade(), false);

  b.pushReservedRun(4, 14);

  b.pushMultiByte("CHIP_ID", "Lower 32 bits of the 64-bit chip ID, positioned immediately before the Checksum.", 4, nextShade(), false);
}

// The Response (QCC -> RC) packet is always the same, single structure,
// regardless of which mode the command that triggered it was in.
function buildResponseTable() {
  const b = makeBuilder();
  let colorIdx = 0;
  const nextShade = () => PALETTE[colorIdx++ % PALETTE.length];

  buildCommonBytes(b, nextShade, true, null);
  pushTelemetryBody(b, nextShade);

  b.pushSingle("Checksum", "CRC-8/CCITT over bytes 1-89 (poly 0x07, init 0x00, no reflection, xorout 0x00)", SHADE_CHK, "0xXX", false, true);

  if (b.getByteNo() - 1 !== 90) throw new Error(`response table byte count mismatch: ended at ${b.getByteNo() - 1}, expected 90`);
  if (b.rows.length !== 90) throw new Error(`response table row count mismatch: ${b.rows.length}, expected 90`);

  return b.rows;
}

// The TX packet (RC -> QCC, i.e. what we've also called the Command packet)
// DOES vary by mode - bytes 1-32 are shared across every variant, but the
// Message Body differs per Command ID.
function buildTxModeTable(cmdIdDisplay, bodyNote) {
  const b = makeBuilder();
  let colorIdx = 0;
  const nextShade = () => PALETTE[colorIdx++ % PALETTE.length];

  buildCommonBytes(b, nextShade, false, cmdIdDisplay);

  b.pushBlock("Message Body", bodyNote, SHADE_RESV, 56, true);

  b.pushSingle("Checksum", "CRC-8/CCITT over bytes 1-89 (poly 0x07, init 0x00, no reflection, xorout 0x00)", SHADE_CHK, "0xXX", false, true);

  if (b.getByteNo() - 1 !== 90) throw new Error(`TX table (${cmdIdDisplay}) byte count mismatch: ended at ${b.getByteNo() - 1}, expected 90`);

  return b.rows;
}

// Mode 1/2 (Internal/External Loopback) TX table - byte 34 selects SOB/PRT/PPS
// command type, with the rest of the Message Body varying accordingly. Full
// per-command breakdown tables are appended separately after this table.
function buildLoopbackTxTable() {
  const b = makeBuilder();
  let colorIdx = 0;
  const nextShade = () => PALETTE[colorIdx++ % PALETTE.length];

  buildCommonBytes(b, nextShade, false, "0x01 / 0x02");

  b.pushSingle("COMMAND_TYPE", "0x00 = SOB command (Internal + External Loopback). 0x01 = PRT command (Internal + External Loopback). 0x02 = PPS command (External Loopback only). See SOB/PRT/PPS Command breakdown tables below.", nextShade(), "0xXX", false, true);

  b.pushBlock("Command-Specific Fields", "Content depends on COMMAND_TYPE (byte 34) - see SOB/PRT/PPS Command breakdown tables below.", SHADE_RESV, 55, true);

  b.pushSingle("Checksum", "CRC-8/CCITT over bytes 1-89 (poly 0x07, init 0x00, no reflection, xorout 0x00)", SHADE_CHK, "0xXX", false, true);

  if (b.getByteNo() - 1 !== 90) throw new Error(`Loopback TX table byte count mismatch: ended at ${b.getByteNo() - 1}, expected 90`);

  return b.rows;
}

// ---------------------------------------------------------------------------
// SOB / PRT / PPS command breakdown tables (bytes 35-89, field-level, not
// bit-exploded - same style as the earlier TX Message Body appendix table).
// ---------------------------------------------------------------------------

function fieldLevelTable(fields, totalCheck) {
  const w = [1200, 2200, 900, 1100, 9000];
  function fcell(text, opts = {}) {
    const { width, bold = false, shade = null, align = AlignmentType.LEFT, size = 18 } = opts;
    return new TableCell({
      width: { size: width, type: WidthType.DXA },
      shading: shade ? { type: ShadingType.CLEAR, fill: shade } : undefined,
      verticalAlign: VerticalAlign.CENTER,
      margins: { top: 40, bottom: 40, left: 80, right: 80 },
      borders: { top: THIN, bottom: THIN, left: THIN, right: THIN },
      children: [new Paragraph({ alignment: align, children: [new TextRun({ text, bold, size })] })],
    });
  }
  function frow(vals, shade) {
    return new TableRow({ children: vals.map((v, i) => fcell(String(v), { width: w[i], shade })) });
  }
  const total = fields.reduce((s, f) => s + f[2], 0);
  if (total !== totalCheck) throw new Error(`field-level table total ${total} != expected ${totalCheck}`);
  return new Table({
    width: { size: FULL_WIDTH, type: WidthType.DXA },
    columnWidths: w,
    rows: [
      new TableRow({ tableHeader: true, children: ["Byte (abs.)", "Field Name", "Size", "Type", "Description"].map((l, i) => fcell(l, { width: w[i], bold: true, shade: "D9E2F3" })) }),
      ...fields.map((f, i) => frow(f, i % 2 === 1 ? "F2F2F2" : null)),
    ],
  });
}

function sobCommandTable() {
  return fieldLevelTable([
    ["35-36", "SOB_WIDTH", 2, "u16", "SOB pulse width."],
    ["37-89", "Reserved", 53, "byte[53]", "Unused in this command."],
  ], 55);
}

function prtCommandTable() {
  return fieldLevelTable([
    ["35-38", "PRT_COUNT", 4, "u32", "Number of PRTs to generate. 0xFFFFFFFF = generate infinite PRTs."],
    ["39-42", "PRI_WIDTH_US", 4, "u32", "Pulse Repetition Interval, microseconds."],
    ["43-44", "PRT_WIDTH_US", 2, "u16", "PRT pulse width, microseconds."],
    ["45-89", "Reserved", 45, "byte[45]", "Unused in this command."],
  ], 55);
}

function ppsCommandTable() {
  return fieldLevelTable([
    ["35-36", "PPS_WIDTH", 2, "u16", "PPS pulse width."],
    ["37-89", "Reserved", 53, "byte[53]", "Unused in this command. Valid only when operating mode is External Loopback."],
  ], 55);
}

const responseRows = buildResponseTable();

const modeTables = [
  { title: "TX PACKET (RC -> QCC) - MODE 0 (Normal), Command ID = 0x00", rows: buildTxModeTable("0x00", "No command data needed - SOB/PRT/PPS pass through automatically from input to output. QCC responds with the full 90-byte Response header plus the full, populated 2880-byte QTRM data block. Only this mode triggers a DMA write (mSGDMA) - no other mode does.") },
  { title: "TX PACKET (RC -> QCC) - MODE 1 / 2 (Internal Loopback / External Loopback), Command ID = 0x01 / 0x02", rows: buildLoopbackTxTable(), hasBreakdown: true },
  { title: "TX PACKET (RC -> QCC) - MODE 3 (Status/Response Only), Command ID = 0x03", rows: buildTxModeTable("0x03", "No command data needed - no action taken. QCC responds with the 90-byte Response header (current telemetry); the following 2880-byte QTRM data block is left empty (not populated), since this is a status-only check. Does not trigger a DMA write.") },
  { title: "TX PACKET (RC -> QCC) - MODE 4 (QCC Reset), Command ID = 0x04", rows: buildTxModeTable("0x04", "No command data needed - triggers QCC to reset all FPGA-side buffers, counters, and internal state via a PIO pin. QCC then responds with the 90-byte Response header reporting the reset (zeroed) values. Does not trigger a DMA write.") },
  { title: "TX PACKET (RC -> QCC) - MODE 5 (Remote Programming), Command ID = 0x05", rows: buildTxModeTable("0x05", "TBD - Remote Programming likely needs a different, larger structure (address/length/data) than the 56-byte Message Body allows. To be defined separately.") },
];

function buildTable(rows) {
  return new Table({
    width: { size: FULL_WIDTH, type: WidthType.DXA },
    columnWidths: COL_WIDTHS,
    rows: [headerRow(), ...rows.map(dataRow)],
  });
}

function legend() {
  return new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 200 },
    children: [
      new TextRun({
        text: "Each distinct field has its own color, applied consistently across all bytes belonging to that field (e.g. a 2-byte field's two bytes share one color, a 4-byte field's four bytes share a different color, and so on). Reserved bytes and the Checksum keep their own fixed colors throughout.",
        italics: true,
        size: 18,
      }),
    ],
  });
}

const doc = new Document({
  styles: { default: { document: { run: { font: "Calibri", size: 20 } } } },
  sections: [
    {
      properties: {
        page: {
          size: { width: 15840, height: 12240 },
          margin: { top: 720, bottom: 720, left: 720, right: 720 },
        },
      },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            alignment: AlignmentType.CENTER,
            children: [
              new TextRun({ text: "Page ", size: 16 }),
              new TextRun({ children: [PageNumber.CURRENT], size: 16 }),
              new TextRun({ text: " of ", size: 16 }),
              new TextRun({ children: [PageNumber.TOTAL_PAGES], size: 16 }),
            ],
          })],
        }),
      },
      children: [
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { after: 200 },
          children: [new TextRun({ text: "RESPONSE PACKET (QCC -> RC) - FULL 90-BYTE BIT-LEVEL TABLE", bold: true, size: 28 })],
        }),
        buildTable(responseRows),
        new Paragraph({ children: [new PageBreak()] }),

        ...modeTables.flatMap((m, i) => [
          new Paragraph({
            alignment: AlignmentType.CENTER,
            spacing: { after: 200 },
            children: [new TextRun({ text: m.title, bold: true, size: 28 })],
          }),
          buildTable(m.rows),
          ...(m.hasBreakdown ? [
            new Paragraph({ heading: HeadingLevel.HEADING_2, spacing: { before: 300, after: 100 }, children: [new TextRun("SOB Command (COMMAND_TYPE = 0x00) - bytes 35-89")] }),
            sobCommandTable(),
            new Paragraph({ heading: HeadingLevel.HEADING_2, spacing: { before: 300, after: 100 }, children: [new TextRun("PRT Command (COMMAND_TYPE = 0x01) - bytes 35-89")] }),
            prtCommandTable(),
            new Paragraph({ heading: HeadingLevel.HEADING_2, spacing: { before: 300, after: 100 }, children: [new TextRun("PPS Command (COMMAND_TYPE = 0x02, External Loopback only) - bytes 35-89")] }),
            ppsCommandTable(),
          ] : []),
          ...(i < modeTables.length - 1 ? [new Paragraph({ children: [new PageBreak()] })] : []),
        ]),
      ],
    },
  ],
});

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync("/home/claude/idd/QCC_90Byte_Header_BitTable.docx", buf);
  console.log("done. response rows:", responseRows.length, "mode tables:", modeTables.map(m => m.rows.length));
});
