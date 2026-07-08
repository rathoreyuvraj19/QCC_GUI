"""
bootloader_packet.py

Single source of truth for the RC <-> RF_LRU bootloader / firmware-update
link protocol (the INNER 10-byte command set carried inside the Remote
Programming outer frame - see packet.py's build_remote_programming_frame).
Transcribed from bootloader_packet_spec.yaml (itself a first-pass
digitization of photographed Word-doc tables, NOT yet verified against the
original source document).

This is a SEPARATE protocol from packet.py's QCC/QTRM 2970-byte frame - do
not conflate the two: different framing, different sizes, different Command
Type space. QCC never parses these 10 bytes; it strips the outer 90-byte
header and broadcasts [payload + command] identically to all 96 QTRMs over
the low-speed (115200) link.

Framing: every packet the GUI sends or receives here is a FIXED 10 bytes.
The one variable-length packet type in the spec (Bitstream Data Packet,
command_type 0x34) still only needs 10 header bytes on our side - its DATA
block rides in the outer frame's separate 4096-byte payload region, and its
spec table shows no trailing checksum after DATA (see open items below).

Byte order: little-endian for the u16 fields - INFERRED from the spec's
LSB/MSB field naming (consistent with packet_spec's convention), not
explicitly stated in the source.

Byte numbering below follows the spec's 1-based convention (byte 1 =
index 0 of the Python bytes object).

=== OPEN ITEMS carried over verbatim from bootloader_packet_spec.yaml ===
 1. Command Type 0x35 is used for BOTH the Firmware Update Command
    (RC->LRU) and the Bitstream Packet Acknowledgement (LRU->RC). No field
    observed in either table distinguishes which meaning applies --
    direction alone might disambiguate in practice (RC never receives its
    own commands echoed), but this needs explicit confirmation from
    whoever owns the original document, not an assumption baked into a
    parser. [This module disambiguates on receive purely by which
    operation the GUI has in flight - see parse_slot(context=...).]
 2. Checksum algorithm is never specified anywhere in the photographed
    source (unlike packet_spec, which explicitly names CRC-8/CCITT with
    poly/init/check-value). Every packet ends with a 'Footer/Check Sum'
    byte at byte 10, but HOW it's computed is unknown. [Provisionally XOR
    here per Yuvraj 2026-07-08 - see bootloader_checksum().]
 3. Bitstream Data Packet (0x34): the visible table ends at
    DATA[fw_packet_length-1] with no checksum byte shown afterward.
    Unclear whether a trailing checksum exists for this packet type.
 4. packet_size_identifier (byte 2): fixed x"00" for all 10-byte packets,
    x"BB" for the variable-length Bitstream Data Packet. Interpreted as a
    packet-class/framing marker rather than a literal size value - this
    interpretation is inferred, not stated.
 5. MSS Link Response (0x30) bytes 6-9 are labeled x"B1"/x"B2"/x"B3"/
    x"B4" in the source table - unclear whether fixed constants, a
    field-naming artifact, or something else. Transcribed verbatim
    without confident interpretation.
 6. The orphaned 'Status Type' table (0/1/2 = command-to-receive /
    data-packet / ack) does not obviously attach to any specific packet.
    Possibly a leftover from an earlier protocol revision. Not used here.
 7. Byte order (little-endian) is inferred from LSB/MSB naming, not
    explicitly stated.
 8. Physical transport layer is not stated in the photographed pages
    (irrelevant GUI-side: QCC relays over its own UDP link regardless).
 9. fw_update_status_code_bits' association with
    bitstream_packet_acknowledgement's pass_fail byte is inferred purely
    from layout proximity in the source document.
10. [GUI-side] Get LRU Info's request layout is NOT in the spec at all -
    build_lru_info_request() mirrors link_request's minimal shape as an
    ASSUMPTION flagged with Yuvraj.
"""

from dataclasses import dataclass, field

BL_PACKET_SIZE = 10

BL_HEADER = 0xAA        # byte 1 of every packet
PSI_FIXED = 0x00        # byte 2 for all fixed 10-byte packets
PSI_BITSTREAM = 0xBB    # byte 2 for the Bitstream Data Packet only

# Command Type values (byte 3 of every packet)
CT_LINK = 0x30                   # Link Request (RC->LRU) / MSS Link Response (LRU->RC)
CT_LRU_STATUS = 0x31             # RF LRU Status Response (LRU->RC)
CT_MODE_CHANGE = 0x32            # Mode Change Command (RC->LRU)
CT_BITSTREAM_RECEIVE = 0x33      # Bitstream Receive Command (RC->LRU)
CT_BITSTREAM_DATA = 0x34         # Bitstream Data Packet (RC->LRU)
CT_FW_UPDATE_OR_BS_ACK = 0x35    # AMBIGUOUS in spec - see open item 1
CT_ERROR = 0x3F                  # Error Msg Format (LRU->RC)

# bsn_mode values (mode_change_command byte 6, bits B06-B01)
BSN_INITIALISATION = 0
BSN_OPERATION = 1
BSN_MAINTENANCE = 2
BSN_MSS_CONTROL = 3

# IAP modes (firmware_update_command byte 6)
IAP_INVALID = 0
IAP_AUTHENTICATE = 1
IAP_PROGRAM = 2
IAP_VERIFY = 3

IAP_MODE_NAMES = {
    IAP_INVALID: "INVALID",
    IAP_AUTHENTICATE: "AUTHENTICATE",
    IAP_PROGRAM: "PROGRAM",
    IAP_VERIFY: "VERIFY",
}

# IAP status codes (firmware_update_command_response byte 7) - these
# values/names exactly match Microsemi/Microchip's SmartFusion2 IAP status
# enum, which gives higher confidence in the spec transcription's accuracy.
IAP_STATUS_NAMES = {
    0: "SUCCESS",
    1: "CHAINING_MISMATCH",
    2: "UNEXPECTED_DATA_RECEIVED",
    3: "INVALID_ENCRYPTION_KEY",
    4: "INVALID_COMPONENT_HEADER",
    5: "BACK_LEVEL_NOT_SATISFIED",
    7: "DSN_BINDING_MISMATCH",
    8: "ILLEGAL_COMPONENT_SEQUENCE",
    9: "INSUFFICIENT_DEV_CAPABILITIES",
    10: "INCORRECT_DEVICE_ID",
    11: "UNSUPPORTED_BITSTREAM",
    12: "VERIFY_NOT_PERMITTED_ON_BITSTREAM",
    127: "ABORT",
    129: "FABRIC_PROGRAM_VERIFY_FAILED",
    130: "DEVICE_SECURITY_PROTECTED",
    131: "PROGRAMMING_MODE_NOT_ENABLED",
    132: "ENVM_PROGRAM_FAILED",
    133: "ENVM_VERIFY_FAILED",
    255: "SERVICE_PROTECTED",
}

# bitstream_packet_acknowledgement pass/fail bits (byte 8) - association
# with this field is inferred from spec layout proximity (open item 9).
BS_ACK_TRANSFER_SUCCESSFUL_BIT = 0x01  # bit 0
BS_ACK_TRANSFER_FAILED_BIT = 0x02      # bit 1


def bootloader_checksum(packet_first_9: bytes) -> int:
    """
    Byte 10 (Footer/Check Sum) of every fixed 10-byte packet.

    TODO(UNCONFIRMED ALGORITHM): the source document never specifies how
    this byte is computed - XOR of bytes 1-9 is a PROVISIONAL choice
    (per Yuvraj, 2026-07-08), picked because it matches the XOR checksum
    the 30-byte QTRM slots already use in this project's main protocol.
    If the document owner confirms a different algorithm (CRC-8? sum?),
    swap it HERE and nowhere else - every builder and parser in this
    module funnels through this one function.
    """
    x = 0
    for b in packet_first_9:
        x ^= b
    return x


def _finish(pkt9: bytearray) -> bytes:
    assert len(pkt9) == BL_PACKET_SIZE - 1
    return bytes(pkt9) + bytes([bootloader_checksum(bytes(pkt9))])


# -- builders (RC -> LRU) - each returns exactly 10 bytes -------------------

def build_link_request(status_type: int = 0, sub_status_type: int = 0) -> bytes:
    """command_type 0x30: byte 4 = sub_status(hi nibble)/status(lo nibble), bytes 5-9 reserved."""
    pkt = bytearray(9)
    pkt[0] = BL_HEADER
    pkt[1] = PSI_FIXED
    pkt[2] = CT_LINK
    pkt[3] = ((sub_status_type & 0x0F) << 4) | (status_type & 0x0F)
    return _finish(pkt)


def build_lru_info_request(status_type: int = 0, sub_status_type: int = 0) -> bytes:
    """
    Request the RF LRU Status Response (LM_ID/MFG_ID/SERIAL/FW Version).

    ASSUMPTION (flagged with Yuvraj, open item 10): the spec only defines
    0x31 as the LRU->RC *response*; the request-side layout is not in the
    IDD. This mirrors link_request's minimal shape (header/psi/command_type/
    status byte/reserved/checksum) with command_type 0x31.
    """
    pkt = bytearray(9)
    pkt[0] = BL_HEADER
    pkt[1] = PSI_FIXED
    pkt[2] = CT_LRU_STATUS
    pkt[3] = ((sub_status_type & 0x0F) << 4) | (status_type & 0x0F)
    return _finish(pkt)


def build_mode_change_command(bsn_mode: int) -> bytes:
    """command_type 0x32: bsn_mode in byte 6 bits B06-B01 (top 2 bits reserved)."""
    pkt = bytearray(9)
    pkt[0] = BL_HEADER
    pkt[1] = PSI_FIXED
    pkt[2] = CT_MODE_CHANGE
    pkt[5] = bsn_mode & 0x3F
    return _finish(pkt)


def build_bitstream_receive_command(fw_packet_size: int, fw_packet_count: int,
                                    image_is_golden: bool = False) -> bytes:
    """command_type 0x33: tells the LRU to prepare to receive a bitstream."""
    pkt = bytearray(9)
    pkt[0] = BL_HEADER
    pkt[1] = PSI_FIXED
    pkt[2] = CT_BITSTREAM_RECEIVE
    pkt[3] = 1 if image_is_golden else 0
    pkt[5] = fw_packet_size & 0xFF
    pkt[6] = (fw_packet_size >> 8) & 0xFF
    pkt[7] = fw_packet_count & 0xFF
    pkt[8] = (fw_packet_count >> 8) & 0xFF
    return _finish(pkt)


def build_firmware_update_command(iap_mode: int, image_is_golden: bool = False) -> bytes:
    """command_type 0x35 (RC->LRU meaning): IAP AUTHENTICATE/PROGRAM/VERIFY."""
    pkt = bytearray(9)
    pkt[0] = BL_HEADER
    pkt[1] = PSI_FIXED
    pkt[2] = CT_FW_UPDATE_OR_BS_ACK
    pkt[3] = 1 if image_is_golden else 0
    pkt[5] = iap_mode & 0xFF
    return _finish(pkt)


def build_bitstream_data_header(fw_packet_length: int, fw_packet_count: int,
                                fw_packet_index: int) -> bytes:
    """
    Bytes 1-10 of the Bitstream Data Packet (command_type 0x34) - the only
    variable-length packet in the spec. GUI-side the DATA block travels in
    the outer Remote Programming frame's 4096-byte payload region, so this
    header is exactly the 10-byte inner-command slot with NO checksum: the
    spec's 0x34 table shows nothing after DATA (open item 3), and unlike
    every other packet, byte 10 here is fw_packet_index_msb, not a footer.
    """
    pkt = bytearray(10)
    pkt[0] = BL_HEADER
    pkt[1] = PSI_BITSTREAM
    pkt[2] = CT_BITSTREAM_DATA
    pkt[4] = fw_packet_length & 0xFF
    pkt[5] = (fw_packet_length >> 8) & 0xFF
    pkt[6] = fw_packet_count & 0xFF
    pkt[7] = (fw_packet_count >> 8) & 0xFF
    pkt[8] = fw_packet_index & 0xFF
    pkt[9] = (fw_packet_index >> 8) & 0xFF
    return bytes(pkt)


# -- parsed-response dataclasses (LRU -> RC) --------------------------------

@dataclass
class SlotBase:
    command_type: int
    status_type: int
    sub_status_type: int
    checksum_ok: bool
    raw: bytes


@dataclass
class MssLinkResponse(SlotBase):
    # Spec labels bytes 6-9 x"B1"..x"B4" with no confident meaning (open
    # item 5) - exposed raw, not interpreted.
    b1_b4: bytes = b""


@dataclass
class LruStatusResponse(SlotBase):
    lm_id: int = 0
    mfg_id: int = 0
    serial_num: int = 0
    fw_version: int = 0


@dataclass
class FwUpdateResponse(SlotBase):
    iap_mode: int = 0
    iap_status: int = 0

    @property
    def iap_mode_name(self) -> str:
        return IAP_MODE_NAMES.get(self.iap_mode, f"UNKNOWN({self.iap_mode})")

    @property
    def iap_status_name(self) -> str:
        return IAP_STATUS_NAMES.get(self.iap_status, f"UNKNOWN({self.iap_status})")


@dataclass
class BitstreamAck(SlotBase):
    ith_packet: int = 0
    pass_fail: int = 0

    @property
    def transfer_failed(self) -> bool:
        return bool(self.pass_fail & BS_ACK_TRANSFER_FAILED_BIT)

    @property
    def transfer_successful(self) -> bool:
        # Some implementations may report 0x00 with only the failed bit
        # meaningful; treat "not failed" as success unless proven otherwise.
        return not self.transfer_failed


@dataclass
class ErrorMsg(SlotBase):
    header_error: int = 0
    crc_error: int = 0
    timeout_error: int = 0


@dataclass
class UnknownSlot(SlotBase):
    pass


# Contexts for disambiguating the shared 0x35 Command Type on receive
# (open item 1): the GUI knows which operation is in flight, and only ever
# RECEIVES the LRU->RC meanings, so no byte-level guess is needed.
CONTEXT_FW_UPDATE = "fw_update"    # Authenticate/Verify/Program-start in flight
CONTEXT_BITSTREAM = "bitstream"    # chunk streaming in flight


def parse_slot(raw10: bytes, context: str = CONTEXT_FW_UPDATE):
    """
    Parse one QTRM's raw 10-byte bootloader response (the first 10 bytes of
    its 30-byte slot in the standard 2970-byte RX frame).

    Returns None for an all-zero slice (that QTRM hasn't responded yet), a
    typed dataclass for recognized packets, or UnknownSlot for anything
    with a plausible header but an unrecognized/unexpected command type.

    checksum_ok is computed under the PROVISIONAL XOR algorithm (see
    bootloader_checksum) - callers should display it but not reject decodes
    on it while the real algorithm is unconfirmed.
    """
    if len(raw10) != BL_PACKET_SIZE:
        raise ValueError(f"expected {BL_PACKET_SIZE} bytes, got {len(raw10)}")
    if raw10 == bytes(BL_PACKET_SIZE):
        return None

    ct = raw10[2]
    status_type = raw10[3] & 0x0F
    sub_status = (raw10[3] >> 4) & 0x0F
    csum_ok = (raw10[0] == BL_HEADER and
               bootloader_checksum(raw10[:9]) == raw10[9])
    base = dict(command_type=ct, status_type=status_type,
                sub_status_type=sub_status, checksum_ok=csum_ok, raw=bytes(raw10))

    if raw10[0] != BL_HEADER:
        return UnknownSlot(**base)

    if ct == CT_LINK:
        return MssLinkResponse(**base, b1_b4=bytes(raw10[5:9]))
    if ct == CT_LRU_STATUS:
        return LruStatusResponse(
            **base,
            lm_id=raw10[4],
            mfg_id=raw10[5],
            serial_num=raw10[6] | (raw10[7] << 8),
            fw_version=raw10[8],
        )
    if ct == CT_FW_UPDATE_OR_BS_ACK:
        if context == CONTEXT_BITSTREAM:
            return BitstreamAck(
                **base,
                ith_packet=raw10[5] | (raw10[6] << 8),
                pass_fail=raw10[7],
            )
        return FwUpdateResponse(**base, iap_mode=raw10[5], iap_status=raw10[6])
    if ct == CT_ERROR:
        return ErrorMsg(**base, header_error=raw10[5], crc_error=raw10[6],
                        timeout_error=raw10[7])
    return UnknownSlot(**base)
