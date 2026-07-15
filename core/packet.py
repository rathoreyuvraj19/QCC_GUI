"""
packet.py

Byte-exact packet builder/parser for the QCC Ethernet UDP frame.

Overall frame layout (little-endian throughout):
    [ 90 bytes ]   Header          - defined below (QCCHeaderTx for QCC->RC
                                      responses, QCCHeaderRx for RC->QCC
                                      commands - both match the unified
                                      90-byte layout in docs/idd/packet_spec.yaml,
                                      the source of truth for this frame)
    [ 2880 bytes ] QTRM data block - 96 x 30-byte QTRM slots (QTRMSlot)
    -------------------------------
    Total: 2970 bytes

QTRM 30-byte slot checksum (XOR of bytes 0-28) is generated/verified on the
QTRM/GUI side per the QTRM Message Format IDD - QCC does not touch it.
QCCHeaderTx's checksum is CRC-8/CCITT (poly 0x07, init 0x00, no reflection,
xorout 0x00) over the whole 90-byte header's bytes 0-88, stored in byte 89 -
NOT split into separately-checksummed 32+58 byte pieces (that was the
pre-2026-07-05 design). FIXED_HEADER_SIZE/QCC_HEADER_SIZE (32/58) still
correctly describe the header's total byte layout (32+58=90), just not an
internal (de)serialization boundary for QCCHeaderTx anymore.
"""

import struct

FIXED_HEADER_SIZE = 32
QCC_HEADER_SIZE = 58
QTRM_SLOT_SIZE = 30
NUM_QTRM = 96
QTRM_BLOCK_SIZE = QTRM_SLOT_SIZE * NUM_QTRM          # 2880
TOTAL_PACKET_SIZE = FIXED_HEADER_SIZE + QCC_HEADER_SIZE + QTRM_BLOCK_SIZE  # 2970

# Remote Programming (Mode 5) TX frame - the one frame shape in the system
# that is NOT 2970 bytes: [90-byte header][4096-byte payload][10-byte inner
# bootloader command] = 4196, always, for every Remote Programming
# operation (payload zero-filled except during Program chunk streaming).
# The RX side stays the standard 2970-byte frame. See bootloader_packet.py
# for the inner command set.
RP_PAYLOAD_SIZE = 4096
RP_INNER_CMD_SIZE = 10
RP_FRAME_SIZE = FIXED_HEADER_SIZE + QCC_HEADER_SIZE + RP_PAYLOAD_SIZE + RP_INNER_CMD_SIZE  # 4196

# ---------------------------------------------------------------------------
# CRC-8 / CCITT  (poly 0x07, init 0x00, no reflect, xorout 0x00)
# Check value: crc8(b"123456789") == 0xF4
# ---------------------------------------------------------------------------

_CRC8_TABLE = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = ((_c << 1) ^ 0x07) & 0xFF if (_c & 0x80) else (_c << 1) & 0xFF
    _CRC8_TABLE.append(_c)


def crc8(data: bytes) -> int:
    crc = 0x00
    for b in data:
        crc = _CRC8_TABLE[crc ^ b]
    return crc


assert crc8(b"123456789") == 0xF4, "CRC-8 table generation is wrong"


def _make_header_bytes(header: bytes = None) -> bytearray:
    """
    header, when given, is a real 90-byte RC->QCC header (built by
    rc_settings.build_header() with the actual COMMAND_ID for whatever's
    being sent) - callers that don't pass one keep the old all-zero
    behavior, so existing tests/dead code paths are unaffected.
    """
    if header is None:
        return bytearray(FIXED_HEADER_SIZE + QCC_HEADER_SIZE)
    assert len(header) == FIXED_HEADER_SIZE + QCC_HEADER_SIZE
    return bytearray(header)

# ---------------------------------------------------------------------------
# QTRM command types (Section 3 of the QTRM Message Format IDD)
# ---------------------------------------------------------------------------

CMD_RESERVED = 0x00
CMD_DWELL = 0x01
CMD_RX_CAL = 0x02
CMD_TX_CAL = 0x03
CMD_RX_ISOLATION = 0x04
CMD_TX_ISOLATION = 0x05
CMD_RX_PATTERN = 0x06
CMD_TX_PATTERN = 0x07
CMD_SOFT_RESET = 0x20
CMD_STATUS = 0x21
CMD_DATA_STORAGE = 0x22
CMD_DC_CONTROL = 0x23
CMD_TIMING_SIGNAL_GEN = 0x40

QTRM_PACKET_SIZE_ID = 0x04  # per Table 6, QTRM = 4 channels

# ---------------------------------------------------------------------------
# Status Types (Section 10.1 of the QTRM Message Format IDD) - byte 4 low
# nibble of any command requests what kind of response the TRM sends back.
# ---------------------------------------------------------------------------

STATUS_TYPE_NONE = 0x0
STATUS_TYPE_ACK = 0x1
STATUS_TYPE_LINK = 0x2
STATUS_TYPE_HEALTH = 0x3
STATUS_TYPE_ERR_LOG = 0x4
STATUS_TYPE_MFG = 0x5
STATUS_TYPE_DIAGNOSTIC = 0x6

# Diagnostic Status Type IDs (Section 10.1.5.1) - only meaningful when
# STATUS_TYPE_DIAGNOSTIC is requested; carried in the Sub Status Type nibble.
DIAGNOSTIC_TYPE_DETAILED_HEALTH = 0x0
DIAGNOSTIC_TYPE_FUTURE_BUFFER = 0x1
DIAGNOSTIC_TYPE_PRESENT_BUFFER = 0x2
DIAGNOSTIC_TYPE_ADAR_STATUS = 0x3

# Link Status Response sentinel bytes (Section 10.1.2), confirmed against
# STATUS_MODULE.vhd - a live QTRM echoes these 5 bytes verbatim.
LINK_SENTINEL = bytes([0xA1, 0xA2, 0xA3, 0xA4, 0xA5])


def message_length(packet_size_id: int) -> int:
    """Total message size for a given Packet Size Identifier: id*5 + 10 (IDD Section 4)."""
    return packet_size_id * 5 + 10


def build_link_query_slot(command_type: int = CMD_STATUS) -> bytes:
    """
    30-byte wire slot requesting a Link status response from one QTRM.
    Packet Size Identifier = 0x00 -> 10-byte message (checksum at byte 10),
    zero-padded to fill the fixed 30-byte slot on the wire.
    """
    return _build_status_family_slot(command_type, STATUS_TYPE_LINK)


def is_link_response_ok(raw_slot: bytes) -> bool:
    """True if a QTRM slot from a response frame is a valid Link-test reply."""
    if len(raw_slot) != QTRM_SLOT_SIZE or raw_slot[0] != QTRMSlot.HEADER_BYTE:
        return False
    msg_len = message_length(raw_slot[1])
    if not (10 <= msg_len <= QTRM_SLOT_SIZE):
        return False
    chk = 0
    for b in raw_slot[: msg_len - 1]:
        chk ^= b
    if chk != raw_slot[msg_len - 1]:
        return False
    if (raw_slot[3] & 0x0F) != STATUS_TYPE_LINK:
        return False
    return raw_slot[4:9] == LINK_SENTINEL


def build_link_test_frame(header: bytes = None) -> bytes:
    """
    Full 2970-byte frame: identical Link query to all 96 QTRMs.
    """
    link_slot = build_link_query_slot()
    out = _make_header_bytes(header)
    for _ in range(NUM_QTRM):
        out.extend(link_slot)
    assert len(out) == TOTAL_PACKET_SIZE
    return bytes(out)


def parse_link_test_response(raw_frame: bytes):
    """Return a list of NUM_QTRM bools: True where that QTRM's Link reply is valid."""
    assert len(raw_frame) == TOTAL_PACKET_SIZE, f"expected {TOTAL_PACKET_SIZE} bytes, got {len(raw_frame)}"
    base = FIXED_HEADER_SIZE + QCC_HEADER_SIZE
    return [
        is_link_response_ok(raw_frame[base + i * QTRM_SLOT_SIZE: base + (i + 1) * QTRM_SLOT_SIZE])
        for i in range(NUM_QTRM)
    ]


def build_individual_link_frame(target_qtrm_index: int, header: bytes = None) -> bytes:
    """
    Full 2970-byte frame: Link query sent to only ONE QTRM (0-based index),
    mirroring the Soft Reset "individual" pattern - every other QTRM's slot
    is left entirely zero-filled (no header, no command at all).
    """
    assert 0 <= target_qtrm_index < NUM_QTRM
    link_slot = build_link_query_slot()
    empty_slot = bytes(QTRM_SLOT_SIZE)
    out = _make_header_bytes(header)
    for i in range(NUM_QTRM):
        out.extend(link_slot if i == target_qtrm_index else empty_slot)
    assert len(out) == TOTAL_PACKET_SIZE
    return bytes(out)


def _build_status_family_slot(command_type: int, status_type: int, payload: bytes = b"",
                               sub_status_type: int = 0) -> bytes:
    """
    Shared builder for the 10-byte (Packet Size ID = 0x00) command family:
    byte0=Header, byte1=0x00, byte2=CommandType, byte3=Sub Status Type (high
    nibble) | Status Type (low nibble), byte4=MSG_ID (always 0 - not
    implemented on the QTRM side), bytes5-8=payload (zero-padded),
    byte9=checksum, rest zero. Sub Status Type's meaning depends on
    status_type (e.g. ACK's 4 ack-phase bits, or Diagnostic's status-type
    selector) - default 0 for status types that don't use it.
    """
    msg_len = message_length(0x00)  # 10
    body = bytearray(QTRM_SLOT_SIZE)
    body[0] = QTRMSlot.HEADER_BYTE
    body[1] = 0x00
    body[2] = command_type
    body[3] = ((sub_status_type & 0x0F) << 4) | (status_type & 0x0F)
    body[4] = 0x00  # MSG_ID - not implemented in QTRM firmware, always zero
    body[5:5 + len(payload)] = payload
    chk = 0
    for b in body[: msg_len - 1]:
        chk ^= b
    body[msg_len - 1] = chk
    return bytes(body)


# ---------------------------------------------------------------------------
# Status Command (Section 10 of the QTRM Message Format IDD) - a single
# "Status Command Format" query (cmd 0x21) covers every Status Type; the
# response's shape (and size) depends on which Status Type was requested.
# Link (handled above, its own dedicated tab) and No Status aren't part of
# this - everything else (ACK, HEALTH, TRM Err. Log, TRM Mfg. Details,
# DIAGNOSTIC) is.
# ---------------------------------------------------------------------------


def build_status_query_slot(status_type: int, sub_status_type: int = 0,
                            beam_register_address: int = 0) -> bytes:
    """
    30-byte wire slot requesting a status response from one QTRM. Always a
    10-byte message (Packet Size Identifier 0x00) regardless of status_type -
    even DIAGNOSTIC, whose *response* comes back as a full 30-byte message.
    byte6 (Beam Data Register Address) only matters for DIAGNOSTIC types
    1/2/3 (Future Buffer/Present Buffer/ADAR Status) - harmlessly ignored by
    the QTRM for every other status type.
    """
    payload = bytes([beam_register_address & 0xFF])
    return _build_status_family_slot(CMD_STATUS, status_type, payload=payload, sub_status_type=sub_status_type)


def build_status_frame(status_type: int, target_qtrm_index: int = None, sub_status_type: int = 0,
                        beam_register_address: int = 0, header: bytes = None) -> bytes:
    """
    Full 2970-byte frame requesting a status response. If target_qtrm_index
    is None, every QTRM gets the same query (mirrors build_link_test_frame).
    Otherwise only that QTRM (0-based index) gets it; every other slot is
    left entirely zero-filled (no header, no command) - same individual-
    target convention as build_individual_link_frame/build_soft_reset_frame.
    """
    status_slot = build_status_query_slot(status_type, sub_status_type, beam_register_address)
    empty_slot = bytes(QTRM_SLOT_SIZE)
    out = _make_header_bytes(header)
    for i in range(NUM_QTRM):
        if target_qtrm_index is None or i == target_qtrm_index:
            out.extend(status_slot)
        else:
            out.extend(empty_slot)
    assert len(out) == TOTAL_PACKET_SIZE
    return bytes(out)


def _valid_status_header(raw_slot: bytes, expected_status_type: int, expected_message_length: int) -> bool:
    """Header/checksum/status-type/length validity shared by every status response parser below."""
    if len(raw_slot) != QTRM_SLOT_SIZE or raw_slot[0] != QTRMSlot.HEADER_BYTE:
        return False
    msg_len = message_length(raw_slot[1])
    if msg_len != expected_message_length:
        return False
    chk = 0
    for b in raw_slot[: msg_len - 1]:
        chk ^= b
    if chk != raw_slot[msg_len - 1]:
        return False
    return (raw_slot[3] & 0x0F) == expected_status_type


def parse_ack_response(raw_slot: bytes):
    """
    ACK Message Format (Section 10.1.1.2) - 10-byte message. Mostly a
    liveness/receipt confirmation (byte4=Message/Dwell ID echo, bytes5-8=the
    querying command's own bytes 6-9 echoed back) rather than new data, so
    only the echoed bytes are surfaced raw - there's no further semantic
    breakdown documented for them.
    """
    if not _valid_status_header(raw_slot, STATUS_TYPE_ACK, 10):
        return None
    return {
        "message_id": raw_slot[4],
        "echoed_bytes": bytes(raw_slot[5:9]),
    }


def parse_health_response(raw_slot: bytes):
    """Health Status Message Format (Section 10.1.3) - 10-byte message, 5 raw status bytes."""
    if not _valid_status_header(raw_slot, STATUS_TYPE_HEALTH, 10):
        return None
    return {
        "dc_voltage_status": raw_slot[4],
        "dc_current_status": raw_slot[5],
        "temperature_status": raw_slot[6],
        "tx_forward_rf_status": raw_slot[7],
        "rx_reverse_rf_status": raw_slot[8],
    }


def parse_err_log_response(raw_slot: bytes):
    """
    Error MSG format (Section 10.1's Error MSG table) - 10-byte message.
    byte5's individual TRM-shutdown-cause bit flags are ambiguously tabled in
    the IDD (garbled cell layout in the source doc) so it's surfaced as a
    raw byte rather than guessed apart; everything else is clean.
    """
    if not _valid_status_header(raw_slot, STATUS_TYPE_ERR_LOG, 10):
        return None
    return {
        "trm_shutdown_flags": raw_slot[4],
        "header_error": raw_slot[5],
        "footer_crc_error": raw_slot[6],
        "timeout_error": raw_slot[7],
        "prt_duty_violation_count": (raw_slot[8] >> 4) & 0x0F,
        "prt_width_violation_count": raw_slot[8] & 0x0F,
    }


def parse_mfg_response(raw_slot: bytes):
    """Mfg Status Response Message Format (Section 10.1.4) - 10-byte message."""
    if not _valid_status_header(raw_slot, STATUS_TYPE_MFG, 10):
        return None
    return {
        "mfg_agency_id": (raw_slot[4] >> 4) & 0x0F,
        "firmware_version": raw_slot[4] & 0x0F,
        "serial_number": raw_slot[5] | (raw_slot[6] << 8),
        "on_time_hours": raw_slot[7] | (raw_slot[8] << 8),
    }


def parse_diagnostic_response(raw_slot: bytes, diagnostic_type: int):
    """
    Diagnostic Status response format (Section 10.1.5.2) - a full 30-byte
    message ("same as Dwell message size"), unlike every other status type's
    10-byte reply. Layout depends on diagnostic_type:
      - DETAILED_HEALTH: per-channel Temp/DC/RF status + Tx/Rx control counts.
      - FUTURE_BUFFER/PRESENT_BUFFER/ADAR_STATUS: per-channel OP Mode|Control
        nibble byte + the same Tx/Rx Phase/Attenuation layout as a Dwell
        message (the IDD tables these three identically).
    """
    if not _valid_status_header(raw_slot, STATUS_TYPE_DIAGNOSTIC, 30):
        return None

    common = {
        "total_prt_count": raw_slot[5],
        "processed_prt_count": raw_slot[6],
        "dwell_prt_count": raw_slot[7],
        "total_sob_count": raw_slot[8],
    }

    if diagnostic_type == DIAGNOSTIC_TYPE_DETAILED_HEALTH:
        common["operation_command_type"] = raw_slot[4]
        channels = []
        for ch in range(4):
            off = 9 + ch * 5
            channels.append({
                "temperature_status": raw_slot[off],
                "dc_status": raw_slot[off + 1],
                "rf_status": raw_slot[off + 2],
                "tx_control_count": raw_slot[off + 3],
                "rx_control_count": raw_slot[off + 4],
            })
        common["channels"] = channels
        return common

    # FUTURE_BUFFER / PRESENT_BUFFER / ADAR_STATUS all share this layout.
    common["beam_data_register_address"] = raw_slot[4]
    channels = []
    for ch in range(4):
        off = 9 + ch * 5
        op_mode_control = raw_slot[off]
        channels.append({
            "op_mode": (op_mode_control >> 4) & 0x0F,
            "control": op_mode_control & 0x0F,
            "tx_phase": raw_slot[off + 1],
            "tx_atten": raw_slot[off + 2],
            "rx_phase": raw_slot[off + 3],
            "rx_atten": raw_slot[off + 4],
        })
    common["channels"] = channels
    return common


_STATUS_PARSERS = {
    STATUS_TYPE_ACK: lambda raw_slot, diagnostic_type: parse_ack_response(raw_slot),
    STATUS_TYPE_HEALTH: lambda raw_slot, diagnostic_type: parse_health_response(raw_slot),
    STATUS_TYPE_ERR_LOG: lambda raw_slot, diagnostic_type: parse_err_log_response(raw_slot),
    STATUS_TYPE_MFG: lambda raw_slot, diagnostic_type: parse_mfg_response(raw_slot),
    STATUS_TYPE_DIAGNOSTIC: parse_diagnostic_response,
}


def parse_status_frame(raw_frame: bytes, status_type: int, diagnostic_type: int = 0):
    """
    Return a list of NUM_QTRM decoded-dict-or-None: None where that QTRM's
    reply didn't validate for the requested status_type (no reply, wrong
    status type echoed back, bad checksum, wrong message length, etc.),
    otherwise the parsed fields for that status type.
    """
    assert len(raw_frame) == TOTAL_PACKET_SIZE, f"expected {TOTAL_PACKET_SIZE} bytes, got {len(raw_frame)}"
    parser = _STATUS_PARSERS[status_type]
    base = FIXED_HEADER_SIZE + QCC_HEADER_SIZE
    return [
        parser(raw_frame[base + i * QTRM_SLOT_SIZE: base + (i + 1) * QTRM_SLOT_SIZE], diagnostic_type)
        for i in range(NUM_QTRM)
    ]


def build_cal_slot(tx_cal: bool, channel: int, phase: int, atten: int) -> bytes:
    """
    30-byte wire slot: RX or TX Calibration command (Section 5/6) selecting
    one of this QTRM's 1-4 channels for calibration; requests a Link-type
    status response back (every command except Soft Reset does, per
    Yuvraj's spec - lets the GUI confirm the targeted QTRM actually replied).
    """
    command_type = CMD_TX_CAL if tx_cal else CMD_RX_CAL
    payload = bytes([channel & 0xFF, (channel >> 8) & 0xFF, phase & 0xFF, atten & 0xFF])
    return _build_status_family_slot(command_type, STATUS_TYPE_LINK, payload)


def build_isolation_slot(tx_isolation: bool) -> bytes:
    """30-byte wire slot: Rx or Tx Isolation command (Section 7/8), requests a Link-type status response."""
    command_type = CMD_TX_ISOLATION if tx_isolation else CMD_RX_ISOLATION
    return _build_status_family_slot(command_type, STATUS_TYPE_LINK)


def build_cal_frame(tx_cal: bool, target_qtrm_index: int, channel: int, phase: int, atten: int,
                     tx_isolation_for_others: bool = False, header: bytes = None) -> bytes:
    """
    Full 2970-byte frame: one QTRM (0-based index) gets an RX or TX
    Calibration command (per tx_cal) for the given channel, all other 95
    QTRMs get an Isolation command (Rx or Tx, per tx_isolation_for_others) so
    they don't interfere with the calibration measurement.
    """
    assert 0 <= target_qtrm_index < NUM_QTRM
    cal_slot = build_cal_slot(tx_cal, channel, phase, atten)
    iso_slot = build_isolation_slot(tx_isolation_for_others)
    out = _make_header_bytes(header)
    for i in range(NUM_QTRM):
        out.extend(cal_slot if i == target_qtrm_index else iso_slot)
    assert len(out) == TOTAL_PACKET_SIZE
    return bytes(out)


def build_soft_reset_slot() -> bytes:
    """
    30-byte wire slot: Soft Reset command (Section 9). No response is
    expected. byte5 (payload[0]) is fixed at 0x01 - confirmed against a
    real-hardware reference frame (AA 00 20 00 00 01 00 00 00 8B); this
    used to be sent as 0x00 (assumed "no payload"), which didn't match
    what real hardware actually expects. The field's exact meaning isn't
    documented/confirmed yet (possibly a fixed reset-delay-units value the
    firmware requires even though it's not user-configurable) - Yuvraj to
    confirm; treat as a required fixed constant until then, not something
    to make configurable.
    """
    return _build_status_family_slot(CMD_SOFT_RESET, STATUS_TYPE_NONE, payload=bytes([0x01]))


def build_soft_reset_frame(target_qtrm_index: int = None, header: bytes = None) -> bytes:
    """
    Full 2970-byte Soft Reset frame. If target_qtrm_index is None, every QTRM
    gets the Soft Reset command. Otherwise only that QTRM (0-based index) gets
    it; every other slot is left entirely zero-filled (no header, no command).
    """
    reset_slot = build_soft_reset_slot()
    empty_slot = bytes(QTRM_SLOT_SIZE)
    out = _make_header_bytes(header)
    for i in range(NUM_QTRM):
        if target_qtrm_index is None or i == target_qtrm_index:
            out.extend(reset_slot)
        else:
            out.extend(empty_slot)
    assert len(out) == TOTAL_PACKET_SIZE
    return bytes(out)


def build_isolation_frame(tx_isolation: bool, target_qtrm_index: int = None, header: bytes = None) -> bytes:
    """
    Full 2970-byte frame: Rx or Tx Isolation command (Section 7/8), no
    response expected - fire and forget, same as Soft Reset. If
    target_qtrm_index is None, every QTRM gets the isolation command.
    Otherwise only that QTRM (0-based index) gets it; every other slot is
    left entirely zero-filled (no header, no command).
    """
    iso_slot = build_isolation_slot(tx_isolation)
    empty_slot = bytes(QTRM_SLOT_SIZE)
    out = _make_header_bytes(header)
    for i in range(NUM_QTRM):
        if target_qtrm_index is None or i == target_qtrm_index:
            out.extend(iso_slot)
        else:
            out.extend(empty_slot)
    assert len(out) == TOTAL_PACKET_SIZE
    return bytes(out)


def build_dwell_slot(channels) -> bytes:
    """
    30-byte Dwell wire slot (Section 4, Table 43): this QTRM's 4 channels'
    Control/Tx Phase/Tx Atten/Rx Phase/Rx Atten. Requests a Link-type status
    response - per Yuvraj, every command except Status and Soft Reset does -
    by packing STATUS_TYPE_LINK into the low nibble of byte3 the same way
    build_cal_slot/build_isolation_slot do for the 10-byte status-family
    messages (QTRMSlot's ack_type/ack_on_off nibble split is bit-identical
    to that byte position, just relabeled in the IDD for this message type).
    """
    return QTRMSlot(
        qtrm_id=0, command_type=CMD_DWELL,
        ack_type=0, ack_on_off=STATUS_TYPE_LINK,
        channels=channels,
    ).to_bytes()


def build_dwell_frame(qtrm_channels, header: bytes = None) -> bytes:
    """
    Full 2970-byte Dwell frame. qtrm_channels is a list of NUM_QTRM items,
    each a list of 4 QTRMChannel objects (that QTRM's channels 1-4). Unlike
    Cal/Isolation/Soft Reset, Dwell has no single-QTRM-target convention -
    every QTRM gets its own Dwell command, all in the same send.
    """
    assert len(qtrm_channels) == NUM_QTRM
    out = _make_header_bytes(header)
    for channels in qtrm_channels:
        out.extend(build_dwell_slot(channels))
    assert len(out) == TOTAL_PACKET_SIZE
    return bytes(out)


# ---------------------------------------------------------------------------
# Data Storage / Memory Read-Write (Section 11 of the IDD) - persists data to
# the QTRM's on-board flash. Data Type IDs and the operation-code nibble
# below follow flash_spi.vhd (E:\Downloads\a10_soc_devkit_ghrd_pro), NOT the
# IDD doc's own numbering - the doc says Manufacturing=1/TRM Config=3, but
# the actual FPGA code (which explicitly flags itself as WIP - "needs to be
# changed as per updated IDD" - in its own comments) uses Manufacturing=1/
# TRM Config=2, and only these two data types are implemented at all
# (Positional Address/Calibration/Factory Cal are commented-out stubs).
# Trusting the firmware over the doc here since it's what actually runs.
# ---------------------------------------------------------------------------

MEM_DATA_TYPE_MANUFACTURING = 0x1
MEM_DATA_TYPE_TRM_CONFIGURATION = 0x2

# High nibble of byte6 (1-indexed) / payload[0] - operation selector. Only
# Flash Write actually persists to non-volatile memory (flash_spi.vhd's
# s6_write_data_to_bram only proceeds to the erase/program-flash sequence
# when this nibble is Flash Write); BRAM Write only touches the volatile
# staging buffer.
MEM_OP_BRAM_WRITE = 0x1
MEM_OP_FLASH_WRITE = 0x3
MEM_OP_FLASH_READ = 0x4


def build_memory_write_slot(data_type: int, payload: bytes, mem_op: int = MEM_OP_FLASH_WRITE) -> bytes:
    """
    30-byte wire slot: Data Storage / Memory Write command (Section 11),
    requesting a Link-type status response like every command except Status
    and Soft Reset. byte6 (1-indexed) = mem_op (hi nibble) | data_type (lo
    nibble); bytes7-9 = payload (data-type-specific, zero-padded).
    """
    op_byte = ((mem_op & 0x0F) << 4) | (data_type & 0x0F)
    full_payload = bytes([op_byte]) + payload
    return _build_status_family_slot(CMD_DATA_STORAGE, STATUS_TYPE_LINK, full_payload)


def build_memory_write_frame(data_type: int, payload: bytes, target_qtrm_index: int = None,
                              mem_op: int = MEM_OP_FLASH_WRITE, header: bytes = None) -> bytes:
    """
    Full 2970-byte frame: Memory Write. If target_qtrm_index is None, every
    QTRM gets the same write (e.g. a uniform Temp Cutoff setting across the
    whole array) - same "all 96" convention as Isolation/Status. Otherwise
    only that QTRM (0-based index) gets it; every other slot is left
    entirely zero-filled (no header, no command) - same convention as
    Cal/Isolation/Soft Reset individual sends.
    """
    assert target_qtrm_index is None or 0 <= target_qtrm_index < NUM_QTRM
    write_slot = build_memory_write_slot(data_type, payload, mem_op)
    empty_slot = bytes(QTRM_SLOT_SIZE)
    out = _make_header_bytes(header)
    for i in range(NUM_QTRM):
        if target_qtrm_index is None or i == target_qtrm_index:
            out.extend(write_slot)
        else:
            out.extend(empty_slot)
    assert len(out) == TOTAL_PACKET_SIZE
    return bytes(out)

# ---------------------------------------------------------------------------
# QCC RX header (RC -> QCC, command) - full 90-byte header, per
# docs/idd/packet_spec.yaml (redesigned 2026-07-09: flat QCC_COMMAND enum
# at byte 32/33 replaces the old mode-based COMMAND_ID at byte 4/5).
#
# Display/decode only for now - deliberately NOT wired into any of the
# actual build_*_frame functions below, which still all send an all-zero
# 90-byte header ("first 90 bytes zero for now" - unchanged, separate
# decision). This class exists so tx_test_window.py can show the correct
# field names for what's actually being sent (all zero) rather than the
# old MSG_ID/MODE/COMMAND_DATA layout, which no longer matches the current
# spec. Bytes 0-32 share the exact same structure as QCCHeaderTx's (the
# response); the 56-byte Message Body (bytes 33-88) is command-dependent:
# DATA_DISTRIBUTION/QCC_STATUS/QCC_RESET/REMOTE_PROGRAMMING need none;
# PRT_BYPASS/PRT_INTERNAL_GEN, SOB_BYPASS/SOB_INTERNAL_GEN, and
# PPS_INTERNAL_GEN each have their own fixed body layout, built by
# build_prt_body/build_sob_body/build_pps_body and sent via
# build_header_only_frame below.
# ---------------------------------------------------------------------------


class QCCHeaderRx:
    """
    Offset  Field                  Size  Type    Notes
    0       DESTINATION_ID         1     byte    RC fills - QCC swaps this into its response's Source ID
    1       SOURCE_ID              1     byte    RC fills - QCC swaps this into its response's Destination ID
    2-3     PACKET_SIZE            2     uint16  Fixed 2970
    4       ECHO_BYTE              1     byte    RC may send any value; QCC no longer interprets this byte
                                                   and echoes it back unchanged in the Response (byte 4)
    5       COMMAND_ACK            1     byte    0x00 for a command (vs 0x01 for a response)
    6-9     MESSAGE_NUMBER         4     uint32  Counter of messages sent by RC to QCC, incremented per message
    10      DATE                   1     byte    Decimal 1-31
    11      MONTH                  1     byte    Decimal 1-12
    12-13   YEAR                   2     uint16  Decimal
    14-17   TIME_OF_DAY            4     uint32  Format still TBD
    18-31   RESERVED0              14    byte[14]
    32      QCC_COMMAND            1     byte    Selects the command to execute - see QCC_COMMAND_* below
    33-88   MESSAGE_BODY           56    byte[56] Command-dependent, see build_*_body() functions below
    89      CHECKSUM               1     byte    CRC-8/CCITT over bytes 0-88
    """

    # Flat QCC_COMMAND enum (redesigned 2026-07-09, replaces the old
    # MODE_NORMAL..MODE_REMOTE_PROGRAMMING mode-based scheme). Selected via
    # the qcc_command field (byte 32/33 1-indexed) instead of byte 4/5.
    QCC_COMMAND_DATA_DISTRIBUTION = 0x00
    QCC_COMMAND_QCC_STATUS = 0x01
    QCC_COMMAND_QCC_RESET = 0x02
    QCC_COMMAND_PRT_BYPASS = 0x03
    QCC_COMMAND_SOB_BYPASS = 0x04
    QCC_COMMAND_PRT_INTERNAL_GEN = 0x05
    QCC_COMMAND_SOB_INTERNAL_GEN = 0x06
    QCC_COMMAND_PPS_INTERNAL_GEN = 0x07
    QCC_COMMAND_REMOTE_PROGRAMMING = 0xFF

    # Everything except the final checksum byte (89 bytes).
    _BODY_FMT = "<BBHBBIBBHI14sB56s"

    def __init__(self, destination_id: int = 0, source_id: int = 0, echo_byte: int = 0,
                 qcc_command: int = 0, message_number: int = 0, date: int = 0, month: int = 0,
                 year: int = 0, time_of_day: int = 0, message_body: bytes = b"", reserved0: bytes = b"",
                 packet_size: int = TOTAL_PACKET_SIZE):
        self.destination_id = destination_id & 0xFF
        self.source_id = source_id & 0xFF
        # 2970 for every standard frame; 4196 (RP_FRAME_SIZE) for Remote
        # Programming TX frames - ASSUMPTION: the doc doesn't say what
        # PACKET_SIZE should read in the 4196-byte frame, actual size chosen.
        self.packet_size = packet_size & 0xFFFF
        self.echo_byte = echo_byte & 0xFF
        self.command_ack = 0
        self.message_number = message_number & 0xFFFFFFFF
        self.date = date & 0xFF
        self.month = month & 0xFF
        self.year = year & 0xFFFF
        self.time_of_day = time_of_day & 0xFFFFFFFF
        self.qcc_command = qcc_command & 0xFF
        body = bytearray(56)
        if message_body:
            body[: len(message_body)] = message_body[:56]
        self.message_body = bytes(body)
        reserved = bytearray(14)
        if reserved0:
            reserved[: len(reserved0)] = reserved0[:14]
        self.reserved0 = bytes(reserved)
        self.checksum_ok = None

    def to_bytes(self) -> bytes:
        body = struct.pack(
            self._BODY_FMT,
            self.destination_id, self.source_id, self.packet_size,
            self.echo_byte, self.command_ack, self.message_number,
            self.date, self.month, self.year, self.time_of_day,
            self.reserved0,
            self.qcc_command,
            self.message_body,
        )
        assert len(body) == FIXED_HEADER_SIZE + QCC_HEADER_SIZE - 1
        return body + struct.pack("<B", crc8(body))

    @classmethod
    def from_bytes(cls, raw: bytes) -> "QCCHeaderRx":
        assert len(raw) == FIXED_HEADER_SIZE + QCC_HEADER_SIZE
        (
            destination_id, source_id, packet_size,
            echo_byte, command_ack, message_number,
            date, month, year, time_of_day,
            reserved0,
            qcc_command,
            message_body,
            chk,
        ) = struct.unpack(cls._BODY_FMT + "B", raw)

        obj = cls()
        obj.destination_id = destination_id
        obj.source_id = source_id
        obj.packet_size = packet_size
        obj.echo_byte = echo_byte
        obj.command_ack = command_ack
        obj.message_number = message_number
        obj.date = date
        obj.month = month
        obj.year = year
        obj.reserved0 = reserved0
        obj.time_of_day = time_of_day
        obj.qcc_command = qcc_command
        obj.message_body = message_body
        obj.checksum_ok = crc8(raw[:-1]) == chk
        return obj


# ---------------------------------------------------------------------------
# PRT_BYPASS/PRT_INTERNAL_GEN, SOB_BYPASS/SOB_INTERNAL_GEN, and
# PPS_INTERNAL_GEN Message Body layouts - per docs/idd/packet_spec.yaml.
# Each command now selects its body shape via qcc_command (byte 32/33)
# rather than an in-body selector byte, so PRT_BYPASS and PRT_INTERNAL_GEN
# share build_prt_body (Bypass vs Internal Gen is which qcc_command value
# the caller passes to QCCHeaderRx, not a body field); same for SOB.
# ---------------------------------------------------------------------------

PRT_COUNT_INFINITE = 0xFFFFFFFF


def build_sob_body(sob_width_us: int) -> bytes:
    """SOB body (bytes 0-1 = SOB_WIDTH, u16, rest reserved). Used by SOB_BYPASS and SOB_INTERNAL_GEN."""
    body = bytearray(56)
    struct.pack_into("<H", body, 0, sob_width_us & 0xFFFF)
    return bytes(body)


def build_prt_body(prt_count: int, pri_width_us: int, prt_width_us: int) -> bytes:
    """
    PRT body (bytes 0-3 = PRT_COUNT u32, 4-7 = PRI_WIDTH_US u32, 8-9 =
    PRT_WIDTH_US u16, rest reserved). Used by PRT_BYPASS and
    PRT_INTERNAL_GEN. prt_count = PRT_COUNT_INFINITE (0xFFFFFFFF) generates
    infinite PRTs.
    """
    body = bytearray(56)
    struct.pack_into("<I", body, 0, prt_count & 0xFFFFFFFF)
    struct.pack_into("<I", body, 4, pri_width_us & 0xFFFFFFFF)
    struct.pack_into("<H", body, 8, prt_width_us & 0xFFFF)
    return bytes(body)


def build_pps_body(pps_width_us: int) -> bytes:
    """PPS body (bytes 0-1 = PPS_WIDTH, u16, rest reserved). Used by PPS_INTERNAL_GEN."""
    body = bytearray(56)
    struct.pack_into("<H", body, 0, pps_width_us & 0xFFFF)
    return bytes(body)


def build_header_only_frame(header: bytes) -> bytes:
    """
    Full 2970-byte frame for a command that's entirely described by its
    90-byte header, with the QTRM data block simply zero-filled since it
    doesn't touch any individual QTRM - the PRT/SOB/PPS timing commands
    (header's qcc_command selects Bypass vs Internal Gen, message_body
    carries the fields, see build_prt_body/build_sob_body/build_pps_body
    above) and QCC_STATUS ("QCC simply returns its current response
    packet, no action taken", per the doc) both fit this shape.
    """
    assert len(header) == FIXED_HEADER_SIZE + QCC_HEADER_SIZE
    out = bytearray(header)
    out.extend(bytes(QTRM_BLOCK_SIZE))
    assert len(out) == TOTAL_PACKET_SIZE
    return bytes(out)


# ---------------------------------------------------------------------------
# REMOTE_PROGRAMMING (qcc_command 0xFF) - 4196-byte TX frame and RX slot
# demux. QCC is a dumb relay for this command: it strips the 90-byte header
# and broadcasts [payload + inner command] identically to all 96 QTRMs over
# the low-speed (115200) link. The inner 10-byte command set lives in
# bootloader_packet.py. Distinct from the Memory Operation tab, which sends
# a standard 2970-byte DATA_DISTRIBUTION frame (see rc_settings.py) -
# no longer sharing a qcc_command value with real Remote Programming now
# that each command has its own dedicated enum value.
# ---------------------------------------------------------------------------


def build_remote_programming_frame(header: bytes, inner_cmd: bytes,
                                   payload: bytes = b"") -> bytes:
    """
    [90-byte header][4096-byte payload][10-byte inner bootloader command].

    payload shorter than 4096 is zero-filled to the right; Program chunk
    callers pass their own 0xFF-padded final chunk instead (padding byte is
    the caller's choice, this function never re-pads a full-size payload).
    """
    assert len(header) == FIXED_HEADER_SIZE + QCC_HEADER_SIZE
    assert len(inner_cmd) == RP_INNER_CMD_SIZE
    assert len(payload) <= RP_PAYLOAD_SIZE
    out = bytearray(header)
    out.extend(payload)
    out.extend(bytes(RP_PAYLOAD_SIZE - len(payload)))
    out.extend(inner_cmd)
    assert len(out) == RP_FRAME_SIZE
    return bytes(out)


def extract_rp_slots(raw: bytes) -> list:
    """
    Per-QTRM bootloader responses from a standard 2970-byte RX frame: the
    FIRST 10 bytes of each 30-byte QTRM slot (the remaining 20 are
    reserved/unused for Remote Programming). Deliberately does NOT go
    through QTRMSlot.from_bytes - that validates the normal-mode 30-byte
    slot format (0xAA header + XOR over all 30), which doesn't apply here;
    decode the returned slices with bootloader_packet.parse_slot instead.
    """
    assert len(raw) == TOTAL_PACKET_SIZE
    base = FIXED_HEADER_SIZE + QCC_HEADER_SIZE
    return [
        raw[base + i * QTRM_SLOT_SIZE: base + i * QTRM_SLOT_SIZE + RP_INNER_CMD_SIZE]
        for i in range(NUM_QTRM)
    ]


# ---------------------------------------------------------------------------
# QCC TX header (QCC -> RC, response) - full 90-byte header, fixed format.
# Per docs/idd/packet_spec.yaml (redesigned 2026-07-09: byte 32/33 is now
# QCC_COMMAND, echoing back whichever of the 9 flat commands produced this
# response, replacing the old mode-based COMMAND_ID/COMMAND_ID_REPEAT at
# byte 4/5 and 32/33). The single CRC-8 at the very last byte covers all 89
# bytes before it, so encode/decode must happen on the full 90-byte block
# as one unit. FIXED_HEADER_SIZE/QCC_HEADER_SIZE (32/58) still correctly
# describe the frame's overall byte layout (32+58=90, unchanged), just not
# a boundary this class's own (de)serialization respects internally.
# ---------------------------------------------------------------------------


class QCCHeaderTx:
    """
    Offset  Field                  Size  Type    Notes
    0       DESTINATION_ID         1     byte    Echo of the command's Source ID
    1       SOURCE_ID              1     byte    Echo of the command's Destination ID
    2-3     PACKET_SIZE            2     uint16  Fixed 2970 (whole-frame size)
    4       ECHO_BYTE              1     byte    Echoed unchanged from the command's byte 4 (no longer interpreted)
    5       COMMAND_ACK            1     byte    0x01 for a response (vs 0x00 for a command)
    6-9     MESSAGE_NUMBER         4     uint32  Echoed command message counter
    10      DATE                   1     byte    Decimal 1-31, echoed
    11      MONTH                  1     byte    Decimal 1-12, echoed
    12-13   YEAR                   2     uint16  Decimal (not hex), echoed
    14-17   TIME_OF_DAY            4     uint32  Echoed, exact format still TBD
    18-21   QCC_QUERY_COUNT        4     uint32  Response-only: count of messages received by QCC (queries)
    22-25   QCC_RESPONSE_COUNT     4     uint32  Response-only: count of messages QCC has responded to
    26      QCC_FIRMWARE_NO        1     byte    Response-only: QCC firmware version/build number
    27-30   RESERVED0              4     byte[4]
    31      RESERVED_B             1     byte
    32      QCC_COMMAND            1     byte    Echoes which command (see QCC_COMMAND_* below) produced this response
    33-34   FPGA_TEMPERATURE       2     int16   10-bit 2's complement in bits 0-9, bits 10-15 = 0
    35-36   BOARD_TEMPERATURE      2     uint16
    37-38   BOARD_HUMIDITY         2     uint16
    39-42   INPUT_SOB_COUNT        4     uint32
    43-46   INPUT_PRT_COUNT        4     uint32
    47-50   INPUT_PPS_COUNT        4     uint32
    51-54   OUTPUT_PRT_COUNT       4     uint32
    55-58   OUTPUT_SOB_COUNT       4     uint32
    59-60   INPUT_SOB_WIDTH_US     2     uint16
    61-62   OUTPUT_SOB_WIDTH_US    2     uint16
    63-64   INPUT_PRT_WIDTH_US     2     uint16
    65-66   OUTPUT_PRT_WIDTH_US    2     uint16
    67-70   INPUT_PRT_PRI          4     uint32  PRT PRI (Pulse Repetition Interval) measured on input, us
    71-74   OUTPUT_PRT_PRI         4     uint32  PRT PRI (Pulse Repetition Interval) measured on output, us
    75-76   INPUT_PPS_WIDTH_US     2     uint16
    77-80   PPS_COUNTER            4     uint32  Separate 32-bit counter, distinct from INPUT_PPS_COUNT
    81      GENERATOR_STATUS       1     byte    Bit 0: SOB_STATE (0=bypass, 1=internal). Bit 1: PRT_STATE (0=bypass, 1=internal). Bits 7-2: reserved.
    82-84   RESERVED1              3     byte[3]
    85-88   CHIP_ID                4     uint32  Lower 32 bits of a 64-bit chip ID
    89      CHECKSUM               1     byte    CRC-8/CCITT over bytes 0-88
    """

    # Same flat QCC_COMMAND enum as QCCHeaderRx - kept duplicated on this
    # class (rather than importing) so callers can write QCCHeaderTx.QCC_COMMAND_*
    # without depending on QCCHeaderRx, matching the pre-existing MODE_* pattern.
    QCC_COMMAND_DATA_DISTRIBUTION = 0x00
    QCC_COMMAND_QCC_STATUS = 0x01
    QCC_COMMAND_QCC_RESET = 0x02
    QCC_COMMAND_PRT_BYPASS = 0x03
    QCC_COMMAND_SOB_BYPASS = 0x04
    QCC_COMMAND_PRT_INTERNAL_GEN = 0x05
    QCC_COMMAND_SOB_INTERNAL_GEN = 0x06
    QCC_COMMAND_PPS_INTERNAL_GEN = 0x07
    QCC_COMMAND_REMOTE_PROGRAMMING = 0xFF

    # Everything except the final checksum byte (89 bytes).
    _BODY_FMT = "<BBHBBIBBHIIIB4sBBHHHIIIIIHHHHIIHIB3sI"

    def __init__(self):
        self.destination_id = 0
        self.source_id = 0
        self.packet_size = TOTAL_PACKET_SIZE
        self.echo_byte = 0
        self.command_ack = 1
        self.message_number = 0
        self.date = 0
        self.month = 0
        self.year = 0
        self.time_of_day = 0
        self.qcc_query_count = 0
        self.qcc_response_count = 0
        self.qcc_firmware_no = 0
        self.qcc_command = 0
        self.fpga_temperature = 0  # signed, -512..511 (10-bit 2's complement)
        self.board_temperature = 0
        self.board_humidity = 0
        self.input_sob_count = 0
        self.input_prt_count = 0
        self.input_pps_count = 0
        self.output_prt_count = 0
        self.output_sob_count = 0
        self.input_sob_width_us = 0
        self.output_sob_width_us = 0
        self.input_prt_width_us = 0
        self.output_prt_width_us = 0
        self.input_prt_pri = 0
        self.output_prt_pri = 0
        self.input_pps_width_us = 0
        self.pps_counter = 0
        self.generator_status = 0  # Bit 0: SOB_STATE, Bit 1: PRT_STATE
        self.chip_id = 0
        self.checksum_ok = None

    def sob_is_internal(self) -> bool:
        """Return True if SOB is internal generator, False if bypass."""
        return bool(self.generator_status & 0x01)

    def prt_is_internal(self) -> bool:
        """Return True if PRT is internal generator, False if bypass."""
        return bool((self.generator_status >> 1) & 0x01)

    def set_generator_state(self, sob_internal: bool, prt_internal: bool) -> None:
        """Set SOB and PRT state bits in generator_status."""
        self.generator_status = (int(sob_internal) & 0x01) | ((int(prt_internal) & 0x01) << 1)

    def to_bytes(self) -> bytes:
        body = struct.pack(
            self._BODY_FMT,
            self.destination_id, self.source_id, self.packet_size,
            self.echo_byte, self.command_ack, self.message_number,
            self.date, self.month, self.year, self.time_of_day,
            self.qcc_query_count, self.qcc_response_count,
            self.qcc_firmware_no, bytes(4), 0,
            self.qcc_command,
            (self.fpga_temperature & 0x3FF), self.board_temperature, self.board_humidity,
            self.input_sob_count, self.input_prt_count, self.input_pps_count,
            self.output_prt_count, self.output_sob_count,
            self.input_sob_width_us, self.output_sob_width_us,
            self.input_prt_width_us, self.output_prt_width_us,
            self.input_prt_pri, self.output_prt_pri,
            self.input_pps_width_us,
            self.pps_counter,
            self.generator_status,
            bytes(3),
            self.chip_id,
        )
        assert len(body) == FIXED_HEADER_SIZE + QCC_HEADER_SIZE - 1
        return body + struct.pack("<B", crc8(body))

    @classmethod
    def from_bytes(cls, raw: bytes) -> "QCCHeaderTx":
        assert len(raw) == FIXED_HEADER_SIZE + QCC_HEADER_SIZE
        (
            destination_id, source_id, packet_size,
            echo_byte, command_ack, message_number,
            date, month, year, time_of_day,
            qcc_query_count, qcc_response_count,
            qcc_firmware_no, _reserved0, _reserved_b,
            qcc_command,
            fpga_temp_raw, board_temperature, board_humidity,
            input_sob_count, input_prt_count, input_pps_count,
            output_prt_count, output_sob_count,
            input_sob_width_us, output_sob_width_us,
            input_prt_width_us, output_prt_width_us,
            input_prt_pri, output_prt_pri,
            input_pps_width_us,
            pps_counter,
            generator_status,
            _reserved1,
            chip_id,
            chk,
        ) = struct.unpack(cls._BODY_FMT + "B", raw)

        obj = cls()
        obj.destination_id = destination_id
        obj.source_id = source_id
        obj.packet_size = packet_size
        obj.echo_byte = echo_byte
        obj.command_ack = command_ack
        obj.message_number = message_number
        obj.date = date
        obj.month = month
        obj.year = year
        obj.time_of_day = time_of_day
        obj.qcc_query_count = qcc_query_count
        obj.qcc_response_count = qcc_response_count
        obj.qcc_firmware_no = qcc_firmware_no
        obj.qcc_command = qcc_command
        fpga_temp_raw &= 0x3FF
        obj.fpga_temperature = fpga_temp_raw - 1024 if fpga_temp_raw >= 512 else fpga_temp_raw
        obj.board_temperature = board_temperature
        obj.board_humidity = board_humidity
        obj.input_sob_count = input_sob_count
        obj.input_prt_count = input_prt_count
        obj.input_pps_count = input_pps_count
        obj.output_prt_count = output_prt_count
        obj.output_sob_count = output_sob_count
        obj.input_sob_width_us = input_sob_width_us
        obj.output_sob_width_us = output_sob_width_us
        obj.input_prt_width_us = input_prt_width_us
        obj.output_prt_width_us = output_prt_width_us
        obj.input_prt_pri = input_prt_pri
        obj.output_prt_pri = output_prt_pri
        obj.input_pps_width_us = input_pps_width_us
        obj.pps_counter = pps_counter
        obj.generator_status = generator_status & 0xFF
        obj.chip_id = chip_id
        obj.checksum_ok = crc8(raw[:-1]) == chk
        return obj


# ---------------------------------------------------------------------------
# QTRM 30-byte slot (per QTRM Message Format IDD, Dwell layout, Table 43)
# ---------------------------------------------------------------------------


class QTRMChannel:
    __slots__ = ("control", "tx_phase", "tx_atten", "rx_phase", "rx_atten")

    def __init__(self, control=0, tx_phase=0, tx_atten=0, rx_phase=0, rx_atten=0):
        self.control = control & 0xFF
        self.tx_phase = tx_phase & 0xFF
        self.tx_atten = tx_atten & 0xFF
        self.rx_phase = rx_phase & 0xFF
        self.rx_atten = rx_atten & 0xFF

    def to_bytes(self) -> bytes:
        return bytes([self.control, self.tx_phase, self.tx_atten, self.rx_phase, self.rx_atten])

    @classmethod
    def from_bytes(cls, raw: bytes) -> "QTRMChannel":
        control, tx_phase, tx_atten, rx_phase, rx_atten = raw
        return cls(control, tx_phase, tx_atten, rx_phase, rx_atten)


class QTRMSlot:
    """
    One 30-byte slot, always sent in full even for commands smaller than 30
    bytes (unused trailing bytes are zero-padded).

    Offset  Field                       Size
    0       Header (0xAA)               1
    1       Packet Size Identifier      1   (0x04 for QTRM)
    2       Command Type                1
    3       ACK_TYPE(hi nibble) / ACK_ON_OFF(lo nibble)   1
    4       Dwell/MSG ID                1
    5       Frequency ID                1
    6-8     Reserved                    3
    9-13    Channel 1 (control,txph,txatt,rxph,rxatt)     5
    14-18   Channel 2                                     5
    19-23   Channel 3                                     5
    24-28   Channel 4                                     5
    29      XOR Checksum (bytes 0-28)   1
    """

    HEADER_BYTE = 0xAA

    def __init__(self, qtrm_id: int, command_type: int = CMD_RESERVED,
                 ack_type: int = 0, ack_on_off: int = 0,
                 dwell_id: int = 0, frequency_id: int = 0,
                 channels=None):
        self.qtrm_id = qtrm_id  # 1..96, positional only - not encoded in the bytes
        self.command_type = command_type & 0xFF
        self.ack_type = ack_type & 0x0F
        self.ack_on_off = ack_on_off & 0x0F
        self.dwell_id = dwell_id & 0xFF
        self.frequency_id = frequency_id & 0xFF
        self.channels = channels or [QTRMChannel() for _ in range(4)]

    def to_bytes(self) -> bytes:
        status_byte = ((self.ack_type & 0x0F) << 4) | (self.ack_on_off & 0x0F)
        body = bytearray()
        body.append(self.HEADER_BYTE)
        body.append(QTRM_PACKET_SIZE_ID)
        body.append(self.command_type)
        body.append(status_byte)
        body.append(self.dwell_id)
        body.append(self.frequency_id)
        body.extend(b"\x00\x00\x00")  # reserved
        for ch in self.channels[:4]:
            body.extend(ch.to_bytes())
        assert len(body) == QTRM_SLOT_SIZE - 1
        chk = 0
        for b in body:
            chk ^= b
        body.append(chk)
        assert len(body) == QTRM_SLOT_SIZE
        return bytes(body)

    @classmethod
    def from_bytes(cls, qtrm_id: int, raw: bytes) -> "QTRMSlot":
        assert len(raw) == QTRM_SLOT_SIZE
        header, size_id, cmd_type, status_byte, dwell_id, freq_id = raw[0:6]
        channels = []
        for i in range(4):
            off = 9 + i * 5
            channels.append(QTRMChannel.from_bytes(raw[off:off + 5]))
        obj = cls(
            qtrm_id=qtrm_id,
            command_type=cmd_type,
            ack_type=(status_byte >> 4) & 0x0F,
            ack_on_off=status_byte & 0x0F,
            dwell_id=dwell_id,
            frequency_id=freq_id,
            channels=channels,
        )
        chk = 0
        for b in raw[:-1]:
            chk ^= b
        obj.checksum_ok = (chk == raw[-1])
        obj.header_ok = (header == cls.HEADER_BYTE)
        return obj


# ---------------------------------------------------------------------------
# Full frame builder / parser
# ---------------------------------------------------------------------------


def build_tx_frame(qtrm_slots) -> bytes:
    """
    Build the full 2970-byte frame to send to QCC (GUI -> QCC direction).
    First 90 bytes (fixed header + QCC header) are zero for now - MSG_ID/MODE
    aren't implemented on the QCC/QTRM side yet.
    """
    assert len(qtrm_slots) == NUM_QTRM
    out = bytearray(FIXED_HEADER_SIZE + QCC_HEADER_SIZE)
    for slot in qtrm_slots:
        out.extend(slot.to_bytes())
    assert len(out) == TOTAL_PACKET_SIZE
    return bytes(out)


def parse_rx_frame(raw: bytes):
    """Parse a full 2970-byte frame received from QCC (QCC -> GUI direction)."""
    assert len(raw) == TOTAL_PACKET_SIZE, f"expected {TOTAL_PACKET_SIZE} bytes, got {len(raw)}"
    header_raw = raw[0:FIXED_HEADER_SIZE + QCC_HEADER_SIZE]
    qcc_header = QCCHeaderTx.from_bytes(header_raw)

    qtrm_slots = []
    base = FIXED_HEADER_SIZE + QCC_HEADER_SIZE
    for i in range(NUM_QTRM):
        off = base + i * QTRM_SLOT_SIZE
        slot_raw = raw[off:off + QTRM_SLOT_SIZE]
        qtrm_slots.append(QTRMSlot.from_bytes(i + 1, slot_raw))

    return qcc_header, qtrm_slots


def default_qtrm_slots():
    """96 blank QTRM slots (command type = Reserved), ready to be edited."""
    return [QTRMSlot(qtrm_id=i + 1) for i in range(NUM_QTRM)]
