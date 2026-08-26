"""
tests/test_packet.py

Regression tests for core/packet.py's byte-exact wire format.

Run (stdlib only, nothing to pip install):
    python -m unittest discover -s tests -t .
pytest also runs these unchanged if you happen to have it.

WHY THIS FILE EXISTS - it is almost entirely about *offsets*.

A field that quietly moves still produces a frame of the right length that
passes its own checksum and is accepted by the QCC - it just means something
completely different once it gets there. That has happened repeatedly as the
IDD evolved: INPUT_PRT_PRI/OUTPUT_PRT_PRI missing entirely pushed
INPUT_PPS_WIDTH_US and PPS_COUNTER to the wrong offsets; DIP_SWITCH landing
at byte 83 shrank the reserved run after it; CHIP_ID leaving the header freed
four bytes back to reserved. None of those are caught by "does it build" or
by eyeballing a hexdump.

So the core of this file is `TestSpecOffsets*`, which pins every field
position against docs/idd/packet_spec.yaml - the declared source of truth -
rather than against a hand-copied constant. If a struct format string in
packet.py and the spec ever disagree, these fail and name the field.
"""

import os
import struct
import sys
import unittest

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apps.remote_prog_controller import (  # noqa: E402
    QCC_BODY_SWITCH_HIGH_SPEED, QCC_BODY_SWITCH_LOW_SPEED, RP_SUBCMD_BROADCAST,
)
from core import lru_config  # noqa: E402
from core.packet import (  # noqa: E402
    CHIP_ID_RESPONSE_SIZE, FIXED_HEADER_SIZE, FrameError, LINK_SENTINEL,
    QCC_HEADER_SIZE, RP_CMD_FRAME_SIZE, RP_FRAME_SIZE, RP_INNER_CMD_SIZE,
    RP_LRU_SELECT_BROADCAST, RP_PAYLOAD_SIZE, RP_QCC_LEVEL_FRAME_SIZE,
    ChipIdResponse, LRUChannel, LRUSlot, QCCHeaderRx, QCCHeaderTx,
    CMD_STATUS, DIAGNOSTIC_TYPE_FUTURE_BUFFER,
    STATUS_TYPE_DIAGNOSTIC, STATUS_TYPE_LINK,
    channels_per_lru, lru_block_size, lru_packet_size_id, lru_slot_size,
    message_length, num_lru, total_packet_size,
    build_broadcast_bootloader_frame, build_cal_frame, build_chip_id_response,
    build_dwell_frame, build_header_only_frame, build_individual_link_frame,
    build_isolation_frame, build_link_query_slot, build_link_test_frame,
    build_memory_write_frame, build_pps_body, build_prt_body,
    build_remote_programming_cmd_frame, build_remote_programming_frame,
    build_sob_body, build_soft_reset_frame, build_status_frame,
    crc8, extract_rp_slots,
    parse_diagnostic_response, parse_link_test_response, parse_rx_frame,
    parse_status_frame,
)

HEADER_SIZE = FIXED_HEADER_SIZE + QCC_HEADER_SIZE  # 90


# Every concrete byte offset in packet_spec.yaml describes the reference
# array (96 LRUs x 4 channels), so the offset tests run against exactly that
# shape - which is also the drift these tests exist to catch. The shape is a
# process-wide singleton, so it's pinned for the whole module rather than
# per-test; TestArrayShape below is the one place that varies it, and it
# restores the reference when it's done.
REFERENCE = lru_config.LRUConfig(num_lru=96, channels_per_lru=4)


def setUpModule():
    lru_config.set_config(REFERENCE)

_SPEC_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs", "idd", "packet_spec.yaml",
)

with open(_SPEC_PATH, encoding="utf-8") as _f:
    SPEC = yaml.safe_load(_f)

# Unsigned struct code by field width. Fields of any other width (the
# reserved byte-array runs) are checked for position/extent instead.
_CODE = {1: "B", 2: "H", 4: "I", 8: "Q"}


def _first_byte(field):
    """
    Spec fields write their position as either `byte: 7` or `byte: [7, 10]`
    (and `offset:` the same way inside a message body). Return the low end.
    """
    pos = field.get("byte", field.get("offset"))
    return pos[0] if isinstance(pos, list) else pos


def _spec_fields(section):
    """Fields of a spec section, in declared order."""
    return SPEC[section]["fields"]


def _link_reply_slot():
    """
    A valid Link *response* slot, as a LRU would send it - deliberately hand
    built rather than reusing build_link_query_slot(), because the query and
    the reply are NOT the same bytes: the query leaves offsets 4-8 zero and
    the LRU fills them with the 5 sentinel bytes when it answers.

    Packet Size Identifier 0x00 -> a 10-byte message, so the XOR checksum
    covers offsets 0-8 and lands at offset 9 (message_length() in packet.py).
    """
    slot = bytearray(lru_slot_size())
    slot[0] = LRUSlot.HEADER_BYTE
    slot[1] = 0x00
    slot[2] = CMD_STATUS
    slot[3] = STATUS_TYPE_LINK  # low nibble = status type
    slot[4:9] = LINK_SENTINEL
    chk = 0
    for b in slot[:9]:
        chk ^= b
    slot[9] = chk
    return bytes(slot)


def _link_response_frame():
    """A full 2970-byte response frame with all 96 LRUs replying OK."""
    return QCCHeaderTx().to_bytes() + _link_reply_slot() * num_lru()


def _distinct_value(index, size):
    """
    A value whose every byte is nonzero and unique to `index`, so this test
    catches a field landing at the wrong offset AND two same-width
    neighbours being swapped - which a value like 1 would not.
    """
    return int.from_bytes(bytes([(index % 255) + 1]) * size, "little")


class TestCrc8(unittest.TestCase):
    """The header checksum. Everything else is worthless if this is wrong."""

    def test_ccitt_check_value(self):
        # The standard CRC-8/CCITT check vector, also asserted in packet.py
        # itself and declared in the spec.
        self.assertEqual(crc8(b"123456789"), 0xF4)
        self.assertEqual(crc8(b"123456789"), SPEC["header"]["checksum"]["check_value"]["expect"])

    def test_empty_is_init_value(self):
        self.assertEqual(crc8(b""), SPEC["header"]["checksum"]["init"])


class TestFrameSizes(unittest.TestCase):
    """
    Frame sizes are duplicated in several places that must agree: packet.py's
    constants, the spec, and core/udp_worker.py's accept-lists.
    """

    def test_standard_frame_matches_spec(self):
        self.assertEqual(total_packet_size(), SPEC["frame"]["total_size_bytes"])
        self.assertEqual(total_packet_size(), 2970)
        self.assertEqual(HEADER_SIZE, SPEC["header"]["size_bytes"])
        self.assertEqual(HEADER_SIZE, 90)
        self.assertEqual(lru_block_size(), SPEC["lru_data_block"]["size_bytes"])
        self.assertEqual(lru_slot_size(), SPEC["lru_data_block"]["slot_size_bytes"])
        self.assertEqual(lru_slot_size(), SPEC["lru_slot"]["size_bytes"])
        self.assertEqual(num_lru(), SPEC["lru_data_block"]["num_slots"])
        self.assertEqual(HEADER_SIZE + lru_block_size(), total_packet_size())

    def test_reference_shape_matches_spec(self):
        ref = SPEC["array_shape"]["reference"]
        self.assertEqual(num_lru(), ref["num_lru"])
        self.assertEqual(channels_per_lru(), ref["channels_per_lru"])
        self.assertEqual(lru_slot_size(), ref["slot_size_bytes"])
        self.assertEqual(lru_block_size(), ref["block_size_bytes"])
        self.assertEqual(total_packet_size(), ref["total_size_bytes"])

    def test_remote_programming_shapes_match_spec(self):
        # Three on-wire shapes, one per entry in remote_programming_framing.
        declared = {entry["total_size_bytes"] for entry in SPEC["remote_programming_framing"]}
        self.assertEqual(declared, {RP_QCC_LEVEL_FRAME_SIZE, RP_CMD_FRAME_SIZE, RP_FRAME_SIZE})
        self.assertEqual(RP_QCC_LEVEL_FRAME_SIZE, 90)
        self.assertEqual(RP_CMD_FRAME_SIZE, 100)
        self.assertEqual(RP_FRAME_SIZE, 4196)
        self.assertEqual(RP_CMD_FRAME_SIZE, HEADER_SIZE + RP_INNER_CMD_SIZE)
        self.assertEqual(RP_FRAME_SIZE, HEADER_SIZE + RP_INNER_CMD_SIZE + RP_PAYLOAD_SIZE)

    def test_chip_id_response_size_matches_spec(self):
        self.assertEqual(CHIP_ID_RESPONSE_SIZE, SPEC["chip_id_read_response"]["total_size_bytes"])
        self.assertEqual(CHIP_ID_RESPONSE_SIZE, 10)

    def test_udp_worker_accepts_exactly_these_shapes(self):
        # udp_worker keeps its own size tuples; they must not drift from the
        # builders. Imported lazily - it pulls in PySide6.
        from core import udp_worker

        self.assertEqual(
            set(udp_worker.valid_tx_sizes()),
            {total_packet_size(), RP_QCC_LEVEL_FRAME_SIZE, RP_CMD_FRAME_SIZE, RP_FRAME_SIZE},
        )
        self.assertEqual(
            set(udp_worker.valid_rx_sizes()),
            {total_packet_size(), RP_QCC_LEVEL_FRAME_SIZE, CHIP_ID_RESPONSE_SIZE},
        )

    def test_udp_worker_sizes_track_the_configured_shape(self):
        """The accept-lists must follow a reconfiguration, not stay at 2970."""
        from core import udp_worker

        try:
            lru_config.set_config(lru_config.LRUConfig(8, 6))
            self.assertIn(410, udp_worker.valid_tx_sizes())
            self.assertIn(410, udp_worker.valid_rx_sizes())
            self.assertNotIn(2970, udp_worker.valid_tx_sizes())
        finally:
            lru_config.set_config(REFERENCE)


class TestSpecOffsetsResponseHeader(unittest.TestCase):
    """
    Every QCCHeaderTx (QCC -> RC response) field lands where the spec says.

    Built once with a distinct value per field, then each field is read back
    at the spec's declared offset.
    """

    # Spec field names with no matching QCCHeaderTx attribute: reserved runs
    # (always zero) and the two structural entries.
    _NO_ATTR = {"reserved_a", "reserved_b", "reserved_c", "message_body", "checksum"}

    @classmethod
    def setUpClass(cls):
        cls.header = QCCHeaderTx()
        cls.expected = {}

        # header.fields are 1-indexed; message_body_response offsets are
        # relative to message_body, which starts at 1-indexed byte 34.
        cls.body_base = _first_byte(
            next(f for f in _spec_fields("header") if f["name"] == "message_body")
        )
        assert cls.body_base == 34, cls.body_base

        for index, field in enumerate(_spec_fields("header") + _spec_fields("message_body_response")):
            name = field["name"]
            if name in cls._NO_ATTR or not hasattr(cls.header, name):
                continue
            value = _distinct_value(index, field["size"])
            if name == "fpga_temperature":
                value &= 0x1FF  # 10-bit 2's complement: keep it positive
            setattr(cls.header, name, value)
            cls.expected[name] = value

        cls.raw = cls.header.to_bytes()

    def _offset_of(self, field, in_body):
        """0-indexed byte offset of a spec field within the 90-byte header."""
        if in_body:
            return (self.body_base - 1) + _first_byte(field)
        return _first_byte(field) - 1

    def test_serialized_length(self):
        self.assertEqual(len(self.raw), HEADER_SIZE)

    def test_every_field_at_spec_offset(self):
        checked = 0
        for in_body, section in ((False, "header"), (True, "message_body_response")):
            for field in _spec_fields(section):
                name = field["name"]
                if name not in self.expected:
                    continue
                size = field["size"]
                self.assertIn(size, _CODE, f"{name}: unexpected width {size}")
                offset = self._offset_of(field, in_body)
                with self.subTest(field=name, offset=offset, size=size):
                    (actual,) = struct.unpack_from("<" + _CODE[size], self.raw, offset)
                    self.assertEqual(
                        actual, self.expected[name],
                        f"{name} is not at byte offset {offset} "
                        f"(spec says byte {_first_byte(field)} of "
                        f"{'message_body' if in_body else 'the header'})",
                    )
                checked += 1
        # Guard against the loop silently matching nothing.
        self.assertGreaterEqual(checked, 30, "far fewer fields checked than expected")

    def test_reserved_runs_are_zero_and_sized(self):
        """Reserved bytes must actually be zero-filled, at the spec's extent."""
        for in_body, section in ((False, "header"), (True, "message_body_response")):
            for field in _spec_fields(section):
                if not field["name"].startswith("reserved"):
                    continue
                offset = self._offset_of(field, in_body)
                size = field["size"]
                with self.subTest(field=field["name"], offset=offset, size=size):
                    self.assertEqual(
                        self.raw[offset:offset + size], bytes(size),
                        f"{field['name']} at offset {offset} is not zero-filled",
                    )

    def test_no_gaps_or_overlaps_in_spec_itself(self):
        """
        The spec must tile the header exactly once - a gap or an overlap here
        means the IDD is wrong before any code is even involved.
        """
        for section, total, base in (("header", HEADER_SIZE, 1),
                                     ("message_body_response", 56, 0)):
            covered = []
            for field in _spec_fields(section):
                start = _first_byte(field)
                covered.extend(range(start, start + field["size"]))
            with self.subTest(section=section):
                self.assertEqual(
                    sorted(covered), list(range(base, base + total)),
                    f"{section} does not tile [{base}, {base + total}) exactly once",
                )

    def test_known_absolute_offsets(self):
        """
        Belt-and-braces pins for the bytes that have actually moved before,
        stated as literals so a wrong edit to BOTH packet.py and the spec is
        still caught here. 1-indexed byte N == 0-indexed N-1.
        """
        for name, one_indexed_byte in (
            ("qcc_command", 33),
            ("generator_status", 82),
            ("dip_switch", 83),
        ):
            with self.subTest(field=name):
                field = next(
                    f for f in _spec_fields("header") + _spec_fields("message_body_response")
                    if f["name"] == name
                )
                in_body = name != "qcc_command"
                self.assertEqual(self._offset_of(field, in_body), one_indexed_byte - 1)
                self.assertEqual(self.raw[one_indexed_byte - 1], self.expected[name])


class TestSpecOffsetsCommandHeader(unittest.TestCase):
    """
    QCCHeaderRx (RC -> QCC command). Bytes 1-33 and 90 are the same fields as
    the response direction; bytes 19-32 are response-only telemetry that the
    command direction models as one 14-byte reserved0 blob.
    """

    _SHARED = ("destination_id", "source_id", "packet_size", "echo_byte", "command_ack",
               "message_number", "date", "month", "year", "time_of_day", "qcc_command")

    def test_shared_fields_at_spec_offsets(self):
        header = QCCHeaderRx()
        expected = {}
        for index, field in enumerate(_spec_fields("header")):
            if field["name"] not in self._SHARED:
                continue
            value = _distinct_value(index, field["size"])
            setattr(header, field["name"], value)
            expected[field["name"]] = value

        raw = header.to_bytes()
        self.assertEqual(len(raw), HEADER_SIZE)

        for field in _spec_fields("header"):
            name = field["name"]
            if name not in expected:
                continue
            offset = _first_byte(field) - 1
            with self.subTest(field=name, offset=offset):
                (actual,) = struct.unpack_from("<" + _CODE[field["size"]], raw, offset)
                self.assertEqual(actual, expected[name])

    def test_reserved0_covers_the_response_only_run(self):
        """
        QCCHeaderRx.reserved0 stands in for the spec's qcc_query_count
        through reserved_b - so it has to start and end exactly where that
        run does, or every field after it shifts.
        """
        response_only = ("qcc_query_count", "qcc_response_count",
                         "application_firmware_version", "rtl_firmware_version",
                         "reserved_a", "reserved_b")
        fields = {f["name"]: f for f in _spec_fields("header")}
        start = _first_byte(fields["qcc_query_count"]) - 1
        end = _first_byte(fields["reserved_b"]) - 1 + fields["reserved_b"]["size"]
        self.assertEqual(end - start, 14, "the response-only run is no longer 14 bytes")

        header = QCCHeaderRx(reserved0=bytes(range(1, 15)))
        raw = header.to_bytes()
        self.assertEqual(raw[start:end], bytes(range(1, 15)))
        # And the run really is those six fields back to back.
        covered = []
        for name in response_only:
            first = _first_byte(fields[name])
            covered.extend(range(first, first + fields[name]["size"]))
        self.assertEqual(sorted(covered), list(range(start + 1, end + 1)))


class TestSpecCommandEnums(unittest.TestCase):
    """QCC_COMMAND (byte 33) and the RP SubCommand enum (byte 34)."""

    def test_qcc_command_values_match_spec(self):
        for entry in SPEC["qcc_commands"]:
            attr = f"QCC_COMMAND_{entry['name']}"
            with self.subTest(command=entry["name"]):
                self.assertTrue(hasattr(QCCHeaderRx, attr), f"QCCHeaderRx is missing {attr}")
                self.assertEqual(getattr(QCCHeaderRx, attr), entry["value"])
                # The two classes carry the enum separately (by design) - so
                # check they still agree with each other.
                self.assertEqual(getattr(QCCHeaderTx, attr), entry["value"])

    def test_no_extra_commands_in_code(self):
        spec_names = {f"QCC_COMMAND_{e['name']}" for e in SPEC["qcc_commands"]}
        code_names = {n for n in vars(QCCHeaderRx) if n.startswith("QCC_COMMAND_")}
        self.assertEqual(code_names, spec_names)

    def test_rp_subcommand_values_match_spec(self):
        by_name = {e["name"]: e["value"] for e in SPEC["remote_programming_subcommands"]}
        self.assertEqual(RP_SUBCMD_BROADCAST, by_name["BROADCAST"])
        self.assertEqual(QCC_BODY_SWITCH_LOW_SPEED, by_name["QCC_LOW_SPEED"])
        self.assertEqual(QCC_BODY_SWITCH_HIGH_SPEED, by_name["QCC_HIGH_SPEED"])
        # BROADCAST must own 0x00: rc_settings.build_header() leaves
        # message_body zero-filled, so every LRU-targeted RP command gets
        # the broadcast SubCommand for free. If this value moves, they all
        # silently become whatever 0x00 now means.
        self.assertEqual(RP_SUBCMD_BROADCAST, 0x00)

    def test_subcommand_and_lru_select_byte_positions(self):
        """SubCommand at header byte 34, LRU_SELECT at 35 (1-indexed)."""
        fields = {f["name"]: f for f in SPEC["message_body_tx"]["per_command"]
                  ["REMOTE_PROGRAMMING"]["fields"]}
        self.assertEqual(_first_byte(fields["sub_command"]), 0)
        self.assertEqual(_first_byte(fields["lru_select"]), 1)

        body = bytes([QCC_BODY_SWITCH_LOW_SPEED, 7]) + bytes(54)
        header = QCCHeaderRx(qcc_command=QCCHeaderRx.QCC_COMMAND_REMOTE_PROGRAMMING,
                             message_body=body).to_bytes()
        self.assertEqual(header[33], QCC_BODY_SWITCH_LOW_SPEED)  # byte 34
        self.assertEqual(header[34], 7)                          # byte 35

    def test_lru_command_types_match_spec(self):
        from core import packet

        for entry in SPEC["lru_command_types"]:
            with self.subTest(command=entry["name"]):
                self.assertEqual(getattr(packet, entry["name"]), entry["value"])


class TestSpecOffsetsTxBodies(unittest.TestCase):
    """The PRT/SOB/PPS command bodies (message_body_tx.per_command)."""

    def _fields(self, command):
        return {f["name"]: f for f in
                SPEC["message_body_tx"]["per_command"][command]["fields"]}

    def test_prt_body(self):
        body = build_prt_body(prt_count=0x11111111, pri_width_us=0x22222222, prt_width_us=0x3333)
        self.assertEqual(len(body), SPEC["message_body_tx"]["size_bytes"])
        for command in ("PRT_BYPASS", "PRT_INTERNAL_GEN"):
            fields = self._fields(command)
            with self.subTest(command=command):
                self.assertEqual(_first_byte(fields["prt_count"]), 0)
                self.assertEqual(_first_byte(fields["pri_width_us"]), 4)
                self.assertEqual(_first_byte(fields["prt_width_us"]), 8)
        self.assertEqual(struct.unpack_from("<I", body, 0)[0], 0x11111111)
        self.assertEqual(struct.unpack_from("<I", body, 4)[0], 0x22222222)
        self.assertEqual(struct.unpack_from("<H", body, 8)[0], 0x3333)
        self.assertEqual(body[10:], bytes(46))

    def test_sob_body(self):
        body = build_sob_body(0xABCD)
        self.assertEqual(len(body), 56)
        for command in ("SOB_BYPASS", "SOB_INTERNAL_GEN"):
            with self.subTest(command=command):
                self.assertEqual(_first_byte(self._fields(command)["sob_width"]), 0)
        self.assertEqual(struct.unpack_from("<H", body, 0)[0], 0xABCD)
        self.assertEqual(body[2:], bytes(54))

    def test_pps_body(self):
        body = build_pps_body(0x1234)
        self.assertEqual(len(body), 56)
        self.assertEqual(_first_byte(self._fields("PPS_INTERNAL_GEN")["pps_width"]), 0)
        self.assertEqual(struct.unpack_from("<H", body, 0)[0], 0x1234)
        self.assertEqual(body[2:], bytes(54))


class TestSpecOffsetsLruSlot(unittest.TestCase):
    """The reference array's 30-byte LRU slot. Unlike the header, its spec bytes are 0-indexed."""

    def test_field_offsets(self):
        channels = [
            LRUChannel(control=0x10 + i, tx_phase=0x20 + i, tx_atten=0x30 + i,
                        rx_phase=0x40 + i, rx_atten=0x50 + i)
            for i in range(4)
        ]
        raw = LRUSlot(lru_id=1, command_type=0x21, ack_type=0xA, ack_on_off=0x5,
                       dwell_id=0x7B, frequency_id=0x7C, channels=channels).to_bytes()
        self.assertEqual(len(raw), lru_slot_size())

        fields = {f["name"]: f for f in _spec_fields("lru_slot")}
        self.assertEqual(raw[_first_byte(fields["header"])], LRUSlot.HEADER_BYTE)
        self.assertEqual(raw[_first_byte(fields["packet_size_id"])], lru_packet_size_id())
        self.assertEqual(raw[_first_byte(fields["command_type"])], 0x21)
        self.assertEqual(raw[_first_byte(fields["status_byte"])], 0xA5)
        self.assertEqual(raw[_first_byte(fields["dwell_id"])], 0x7B)
        self.assertEqual(raw[_first_byte(fields["frequency_id"])], 0x7C)
        reserved = _first_byte(fields["reserved"])
        self.assertEqual(raw[reserved:reserved + fields["reserved"]["size"]], bytes(3))

        # Channels, per the lru_channel sub-layout.
        ch_fields = {f["name"]: f for f in SPEC["lru_channel"]["fields"]}
        for i in range(4):
            base = _first_byte(fields[f"channel_{i + 1}"])
            with self.subTest(channel=i + 1, base=base):
                self.assertEqual(raw[base + _first_byte(ch_fields["control"])], 0x10 + i)
                self.assertEqual(raw[base + _first_byte(ch_fields["tx_phase"])], 0x20 + i)
                self.assertEqual(raw[base + _first_byte(ch_fields["tx_atten"])], 0x30 + i)
                self.assertEqual(raw[base + _first_byte(ch_fields["rx_phase"])], 0x40 + i)
                self.assertEqual(raw[base + _first_byte(ch_fields["rx_atten"])], 0x50 + i)

    def test_slot_tiles_exactly_once(self):
        covered = []
        for field in _spec_fields("lru_slot"):
            start = _first_byte(field)
            covered.extend(range(start, start + field["size"]))
        self.assertEqual(sorted(covered), list(range(lru_slot_size())))

    def test_channel_layout_matches_spec(self):
        """
        The generic rule the builder actually follows - channels start at a
        fixed byte and repeat on a fixed stride - stated separately from the
        reference array's four enumerated channel_N fields, since that rule
        is what holds at any channel count.
        """
        spec_slot = SPEC["lru_slot"]
        fields = {f["name"]: f for f in _spec_fields("lru_slot")}
        self.assertEqual(spec_slot["channels_start_byte"],
                         _first_byte(fields["channel_1"]))
        self.assertEqual(spec_slot["channel_stride_bytes"],
                         SPEC["lru_channel"]["size_bytes"])
        self.assertEqual(spec_slot["channel_count"], channels_per_lru())
        for i in range(2, spec_slot["channel_count"] + 1):
            self.assertEqual(
                _first_byte(fields[f"channel_{i}"]),
                spec_slot["channels_start_byte"] + (i - 1) * spec_slot["channel_stride_bytes"])

    def test_xor_checksum_covers_spec_range(self):
        spec_chk = SPEC["lru_slot"]["checksum"]
        self.assertEqual(spec_chk["covers"]["start_byte"], 0)
        self.assertEqual(spec_chk["covers"]["end_byte"], lru_slot_size() - 2)
        self.assertEqual(spec_chk["stored_at_byte"], lru_slot_size() - 1)

        raw = LRUSlot(lru_id=1, command_type=0x01, dwell_id=9).to_bytes()
        expected = 0
        for b in raw[:-1]:
            expected ^= b
        self.assertEqual(raw[-1], expected)


class TestSpecOffsetsChipIdResponse(unittest.TestCase):
    """The standalone 10-byte CHIP_ID_READ response."""

    def test_layout_matches_spec(self):
        raw = build_chip_id_response(QCCHeaderTx.QCC_COMMAND_CHIP_ID_READ, 0x0123456789ABCDEF)
        self.assertEqual(len(raw), CHIP_ID_RESPONSE_SIZE)

        fields = {f["name"]: f for f in SPEC["chip_id_read_response"]["fields"]}
        self.assertEqual(raw[_first_byte(fields["command_id"])],
                         QCCHeaderTx.QCC_COMMAND_CHIP_ID_READ)
        chip_at = _first_byte(fields["chip_id"])
        self.assertEqual(fields["chip_id"]["size"], 8, "chip ID must be the full 64 bits")
        self.assertEqual(struct.unpack_from("<Q", raw, chip_at)[0], 0x0123456789ABCDEF)
        self.assertEqual(_first_byte(fields["checksum"]), CHIP_ID_RESPONSE_SIZE - 1)
        self.assertEqual(raw[-1], crc8(raw[:-1]))

    def test_full_64_bits_survive(self):
        """The whole point of moving it out of the header - no truncation."""
        parsed = ChipIdResponse.from_bytes(
            build_chip_id_response(0x08, 0xFFFFFFFFFFFFFFFF)
        )
        self.assertEqual(parsed.chip_id, 0xFFFFFFFFFFFFFFFF)
        self.assertTrue(parsed.checksum_ok)


class TestRoundTrip(unittest.TestCase):
    """to_bytes() -> from_bytes() must preserve every field."""

    def test_response_header(self):
        header = QCCHeaderTx()
        values = {
            "destination_id": 0x11, "source_id": 0x22, "packet_size": total_packet_size(),
            "echo_byte": 0x33, "command_ack": 1, "message_number": 0xDEADBEEF,
            "date": 27, "month": 7, "year": 2026, "time_of_day": 0x12345678,
            "qcc_query_count": 1234, "qcc_response_count": 1233,
            "application_firmware_version": 3, "rtl_firmware_version": 9,
            "qcc_command": QCCHeaderTx.QCC_COMMAND_QCC_STATUS,
            "board_temperature": 0x0BB8, "board_humidity": 0x1770,
            "input_sob_count": 111, "input_prt_count": 222, "input_pps_count": 333,
            "output_prt_count": 444, "output_sob_count": 555,
            "input_sob_width_us": 10, "output_sob_width_us": 11,
            "input_prt_width_us": 12, "output_prt_width_us": 13,
            "input_prt_pri": 100000, "output_prt_pri": 100001,
            "input_pps_width_us": 14, "pps_timestamp": 999999,
            "generator_status": 0b101, "dip_switch": 0xA5,
        }
        for name, value in values.items():
            setattr(header, name, value)

        back = QCCHeaderTx.from_bytes(header.to_bytes())
        for name, value in values.items():
            with self.subTest(field=name):
                self.assertEqual(getattr(back, name), value)
        self.assertTrue(back.checksum_ok)

    def test_response_header_negative_fpga_temperature(self):
        """FPGA temp is 10-bit 2's complement - the sign must survive."""
        for temp in (-512, -100, -1, 0, 1, 100, 511):
            with self.subTest(temp=temp):
                header = QCCHeaderTx()
                header.fpga_temperature = temp
                self.assertEqual(QCCHeaderTx.from_bytes(header.to_bytes()).fpga_temperature, temp)

    def test_command_header(self):
        header = QCCHeaderRx(
            destination_id=0x0A, source_id=0x0B, echo_byte=0x0C,
            qcc_command=QCCHeaderRx.QCC_COMMAND_DATA_DISTRIBUTION,
            message_number=42, date=4, month=8, year=2026, time_of_day=7,
            message_body=bytes(range(56)), reserved0=bytes(range(14)),
        )
        back = QCCHeaderRx.from_bytes(header.to_bytes())
        for name in ("destination_id", "source_id", "packet_size", "echo_byte", "command_ack",
                     "message_number", "date", "month", "year", "time_of_day",
                     "qcc_command", "message_body", "reserved0"):
            with self.subTest(field=name):
                self.assertEqual(getattr(back, name), getattr(header, name))
        self.assertTrue(back.checksum_ok)

    def test_lru_slot(self):
        channels = [LRUChannel(control=i, tx_phase=63 - i, tx_atten=i * 2,
                                rx_phase=i * 3, rx_atten=i * 4) for i in range(4)]
        slot = LRUSlot(lru_id=5, command_type=0x01, ack_type=0x2, ack_on_off=0x1,
                        dwell_id=17, frequency_id=18, channels=channels)
        back = LRUSlot.from_bytes(5, slot.to_bytes())

        self.assertEqual(back.command_type, 0x01)
        self.assertEqual(back.ack_type, 0x2)
        self.assertEqual(back.ack_on_off, 0x1)
        self.assertEqual(back.dwell_id, 17)
        self.assertEqual(back.frequency_id, 18)
        self.assertTrue(back.checksum_ok)
        self.assertTrue(back.header_ok)
        for original, parsed in zip(channels, back.channels):
            self.assertEqual(original.to_bytes(), parsed.to_bytes())


class TestChecksumValidation(unittest.TestCase):
    """A corrupted frame must be reported as corrupted, not silently accepted."""

    def test_header_crc_covers_every_byte_before_it(self):
        spec_chk = SPEC["header"]["checksum"]
        self.assertEqual(spec_chk["covers"]["start_byte"], 1)
        self.assertEqual(spec_chk["covers"]["end_byte"], HEADER_SIZE - 1)
        self.assertEqual(spec_chk["stored_at_byte"], HEADER_SIZE)

        raw = QCCHeaderTx().to_bytes()
        self.assertTrue(QCCHeaderTx.from_bytes(raw).checksum_ok)

        # Flipping any single covered byte must be detected.
        for offset in range(HEADER_SIZE - 1):
            corrupted = bytearray(raw)
            corrupted[offset] ^= 0xFF
            with self.subTest(offset=offset):
                self.assertFalse(
                    QCCHeaderTx.from_bytes(bytes(corrupted)).checksum_ok,
                    f"corruption at byte {offset} slipped past the header CRC",
                )

    def test_slot_xor_detects_corruption(self):
        raw = LRUSlot(lru_id=1, command_type=0x01, dwell_id=3).to_bytes()
        self.assertTrue(LRUSlot.from_bytes(1, raw).checksum_ok)
        for offset in range(lru_slot_size() - 1):
            corrupted = bytearray(raw)
            corrupted[offset] ^= 0xFF
            with self.subTest(offset=offset):
                self.assertFalse(LRUSlot.from_bytes(1, bytes(corrupted)).checksum_ok)

    def test_chip_id_checksum_detects_corruption(self):
        raw = bytearray(build_chip_id_response(0x08, 0x1122334455667788))
        raw[4] ^= 0xFF
        self.assertFalse(ChipIdResponse.from_bytes(bytes(raw)).checksum_ok)


class TestFrameErrors(unittest.TestCase):
    """
    Parsers must reject a wrong-sized frame with FrameError - a real,
    catchable exception rather than an `assert`, which `python -O` strips.
    """

    def _short(self, size):
        return bytes(size - 1)

    def test_parsers_raise_frame_error(self):
        cases = (
            ("parse_rx_frame", lambda r: parse_rx_frame(r), total_packet_size()),
            ("parse_link_test_response", lambda r: parse_link_test_response(r), total_packet_size()),
            ("parse_status_frame", lambda r: parse_status_frame(r, 0x2), total_packet_size()),
            ("extract_rp_slots", lambda r: extract_rp_slots(r), total_packet_size()),
            ("QCCHeaderTx.from_bytes", QCCHeaderTx.from_bytes, HEADER_SIZE),
            ("QCCHeaderRx.from_bytes", QCCHeaderRx.from_bytes, HEADER_SIZE),
            ("ChipIdResponse.from_bytes", ChipIdResponse.from_bytes, CHIP_ID_RESPONSE_SIZE),
            ("LRUSlot.from_bytes", lambda r: LRUSlot.from_bytes(1, r), lru_slot_size()),
        )
        for name, call, size in cases:
            with self.subTest(parser=name):
                with self.assertRaises(FrameError):
                    call(self._short(size))
                with self.assertRaises(FrameError):
                    call(bytes(size + 1))

    def test_frame_error_is_a_value_error(self):
        """
        bootloader_packet.parse_slot already raises plain ValueError, so
        callers can catch one type for both.
        """
        self.assertTrue(issubclass(FrameError, ValueError))

    def test_unknown_status_type_raises_frame_error(self):
        with self.assertRaises(FrameError):
            parse_status_frame(bytes(total_packet_size()), 0xE)

    def test_validation_survives_optimized_mode(self):
        """
        The regression this guards: under `python -O` every `assert` is
        stripped, so if a parser goes back to asserting on wire data it will
        silently accept a malformed frame here instead of raising.
        """
        import subprocess

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = (
            "import sys; sys.path.insert(0, %r)\n"
            "from core.packet import parse_link_test_response, FrameError\n"
            "try:\n"
            "    parse_link_test_response(b'')\n"
            "except FrameError:\n"
            "    print('RAISED')\n" % repo_root
        )
        out = subprocess.run([sys.executable, "-O", "-c", script],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.strip(), "RAISED", out.stderr)


class TestFrameBuilders(unittest.TestCase):
    """End-to-end: what a builder produces is what the matching parser reads."""

    def _header(self):
        return QCCHeaderRx(qcc_command=QCCHeaderRx.QCC_COMMAND_DATA_DISTRIBUTION).to_bytes()

    def test_link_query_frame_replicates_the_query_slot(self):
        frame = build_link_test_frame(header=self._header())
        self.assertEqual(len(frame), total_packet_size())
        query_slot = build_link_query_slot()
        for q in range(num_lru()):
            base = HEADER_SIZE + q * lru_slot_size()
            with self.subTest(lru=q):
                self.assertEqual(frame[base:base + lru_slot_size()], query_slot)
        # The query asks for a Link-type status response (byte 3 low nibble).
        self.assertEqual(query_slot[3] & 0x0F, STATUS_TYPE_LINK)

    def test_individual_link_frame_targets_one_slot(self):
        query_slot = build_link_query_slot()
        for target in (0, 47, num_lru() - 1):
            with self.subTest(target=target):
                frame = build_individual_link_frame(target, header=self._header())
                self.assertEqual(len(frame), total_packet_size())
                for q in range(num_lru()):
                    base = HEADER_SIZE + q * lru_slot_size()
                    slot = frame[base:base + lru_slot_size()]
                    # Non-target slots are entirely zero - no header, no
                    # command at all - not merely a different command.
                    self.assertEqual(slot, query_slot if q == target else bytes(lru_slot_size()))

    def test_link_response_parsing_accepts_a_valid_reply(self):
        frame = _link_response_frame()
        results = parse_link_test_response(frame)
        self.assertEqual(len(results), num_lru())
        self.assertTrue(all(results), "every valid reply slot should validate")

    def test_link_response_sentinel_position(self):
        """The LRU echoes the 5 sentinel bytes at slot offset 4-8."""
        frame = _link_response_frame()
        self.assertEqual(frame[HEADER_SIZE + 4:HEADER_SIZE + 9], LINK_SENTINEL)

    def test_link_response_rejects_bad_slots(self):
        """Each validation rule in is_link_response_ok() must actually bite."""
        target = 30
        base = HEADER_SIZE + target * lru_slot_size()
        corruptions = {
            "wrong header byte": (0, 0x55),
            "wrong status type": (3, 0x03),
            "broken sentinel": (6, 0x00),
            "bad checksum": (9, 0x00),
        }
        for label, (offset, value) in corruptions.items():
            with self.subTest(corruption=label):
                frame = bytearray(_link_response_frame())
                if frame[base + offset] == value:  # make sure it's a real change
                    value ^= 0xFF
                frame[base + offset] = value
                results = parse_link_test_response(bytes(frame))
                self.assertFalse(results[target], f"{label} was accepted as a valid reply")
                self.assertEqual(sum(results), num_lru() - 1, "only the corrupted slot should fail")

    def test_empty_slot_is_not_a_reply(self):
        """A LRU that never answered leaves its slot zeroed - not 'OK'."""
        frame = QCCHeaderTx().to_bytes() + bytes(lru_block_size())
        self.assertFalse(any(parse_link_test_response(frame)))

    def test_dwell_frame_slots_are_addressable(self):
        channels = [[LRUChannel(control=(q + c) & 0xFF) for c in range(4)]
                    for q in range(num_lru())]
        frame = build_dwell_frame(channels, header=self._header())
        self.assertEqual(len(frame), total_packet_size())
        _, slots = parse_rx_frame(frame)
        self.assertEqual(len(slots), num_lru())
        for q in (0, 1, 95):
            with self.subTest(lru=q):
                self.assertEqual(slots[q].channels[0].control, q & 0xFF)
                self.assertTrue(slots[q].checksum_ok)

    def test_header_only_frame_zero_fills_the_lru_block(self):
        frame = build_header_only_frame(self._header())
        self.assertEqual(len(frame), total_packet_size())
        self.assertEqual(frame[HEADER_SIZE:], bytes(lru_block_size()))

    def test_rp_cmd_frame_is_header_plus_command(self):
        inner = bytes(range(RP_INNER_CMD_SIZE))
        frame = build_remote_programming_cmd_frame(self._header(), inner)
        self.assertEqual(len(frame), RP_CMD_FRAME_SIZE)
        self.assertEqual(frame[HEADER_SIZE:], inner)

    def test_rp_data_frame_puts_command_before_payload(self):
        """
        Order matters: QCC forwards bytes 90.. verbatim and the LRU
        bootloader reads its 10-byte command header first.
        """
        inner = bytes([0x34] + [0] * (RP_INNER_CMD_SIZE - 1))
        payload = bytes([0xA5]) * 100
        frame = build_remote_programming_frame(self._header(), inner, payload)
        self.assertEqual(len(frame), RP_FRAME_SIZE)
        self.assertEqual(frame[HEADER_SIZE:HEADER_SIZE + RP_INNER_CMD_SIZE], inner)
        start = HEADER_SIZE + RP_INNER_CMD_SIZE
        self.assertEqual(frame[start:start + len(payload)], payload)
        self.assertEqual(frame[start + len(payload):], bytes(RP_PAYLOAD_SIZE - len(payload)),
                         "short payload must be zero-filled to the right")

    def test_mode_step1_broadcast_replicates_into_every_slot(self):
        inner = bytes([0x31] + [0] * (RP_INNER_CMD_SIZE - 1))
        frame = build_broadcast_bootloader_frame(self._header(), inner,
                                                 RP_LRU_SELECT_BROADCAST)
        self.assertEqual(len(frame), total_packet_size())
        for slot in extract_rp_slots(frame):
            self.assertEqual(slot, inner)

    def test_mode_step1_single_target_zero_fills_the_rest(self):
        inner = bytes([0x31] + [0] * (RP_INNER_CMD_SIZE - 1))
        target = 12
        frame = build_broadcast_bootloader_frame(self._header(), inner, target)
        for index, slot in enumerate(extract_rp_slots(frame)):
            with self.subTest(lru=index):
                self.assertEqual(slot, inner if index == target else bytes(RP_INNER_CMD_SIZE))


class TestPhysicalUnits(unittest.TestCase):
    """
    Raw 6-bit code -> physical value. The phase full-scale convention is an
    open question (CLAUDE.md item 10); these lock in what is implemented
    today so a change to it is deliberate and visible, not incidental.
    """

    def test_attenuator_full_scale_is_31_5_db(self):
        from core.packet import atten_db

        self.assertAlmostEqual(atten_db(0), 0.0)
        self.assertAlmostEqual(atten_db(1), 0.5)
        self.assertAlmostEqual(atten_db(63), 31.5)

    def test_phase_step_is_360_over_64(self):
        from core.packet import phase_degrees

        self.assertAlmostEqual(phase_degrees(0), 0.0)
        self.assertAlmostEqual(phase_degrees(1), 5.625)
        self.assertAlmostEqual(phase_degrees(63), 354.375)

    def test_phase_display_is_lossless_at_3dp(self):
        """Every multiple of 5.625 lands exactly on 3 decimal places."""
        from core.packet import describe_phase, phase_degrees

        for code in range(64):
            with self.subTest(code=code):
                self.assertEqual(float(describe_phase(code).rstrip("°")),
                                 round(phase_degrees(code), 3))


class TestArrayShape(unittest.TestCase):
    """
    The frame layout is parameterised on (num_lru, channels_per_lru). These
    are the properties that have to hold at ANY shape, as opposed to the
    offset tests above, which pin the reference array against the spec.

    Every test restores the reference shape, since it's a process-wide
    singleton the rest of the module depends on.
    """

    # Reference, a wide LRU, a small array, the single-channel edge, and the
    # largest LRU count the lru_select byte leaves room for.
    SHAPES = [(96, 4), (96, 24), (8, 6), (12, 1), (255, 5)]

    def tearDown(self):
        lru_config.set_config(REFERENCE)

    def _use(self, n_lru, n_ch):
        lru_config.set_config(lru_config.LRUConfig(n_lru, n_ch))

    def test_sizes_follow_the_idd_formula(self):
        for n_lru, n_ch in self.SHAPES:
            with self.subTest(num_lru=n_lru, channels=n_ch):
                self._use(n_lru, n_ch)
                self.assertEqual(lru_slot_size(), 5 * n_ch + 10)
                self.assertEqual(lru_block_size(), n_lru * (5 * n_ch + 10))
                self.assertEqual(total_packet_size(), 90 + n_lru * (5 * n_ch + 10))

    def test_reference_shape_is_byte_identical_to_the_old_fixed_layout(self):
        """96 x 4 must still produce exactly the frame that shipped."""
        self._use(96, 4)
        self.assertEqual(lru_slot_size(), 30)
        self.assertEqual(lru_block_size(), 2880)
        self.assertEqual(total_packet_size(), 2970)
        self.assertEqual(len(build_link_test_frame()), 2970)

    def test_every_builder_produces_the_configured_frame_size(self):
        for n_lru, n_ch in self.SHAPES:
            self._use(n_lru, n_ch)
            channels = [[LRUChannel() for _ in range(n_ch)] for _ in range(n_lru)]
            frames = {
                "link_test": build_link_test_frame(),
                "individual_link": build_individual_link_frame(0),
                "status": build_status_frame(STATUS_TYPE_LINK),
                "status_one": build_status_frame(STATUS_TYPE_LINK, target_lru_index=0),
                "soft_reset": build_soft_reset_frame(),
                "isolation": build_isolation_frame(True),
                "cal": build_cal_frame(True, 0, 1, 0, 0),
                "memory_write": build_memory_write_frame(1, b"\x01"),
                "dwell": build_dwell_frame(channels),
                "header_only": build_header_only_frame(bytes(HEADER_SIZE)),
                "rp_broadcast": build_broadcast_bootloader_frame(
                    bytes(HEADER_SIZE), bytes(RP_INNER_CMD_SIZE)),
            }
            for name, frame in frames.items():
                with self.subTest(num_lru=n_lru, channels=n_ch, builder=name):
                    self.assertEqual(len(frame), total_packet_size())

    def test_packet_size_id_is_the_channel_count(self):
        """
        What makes the receiver's message_length(id) = id*5 + 10 agree with
        the slot size we actually sent.
        """
        for n_lru, n_ch in self.SHAPES:
            with self.subTest(num_lru=n_lru, channels=n_ch):
                self._use(n_lru, n_ch)
                slot = LRUSlot(lru_id=1, command_type=0x01).to_bytes()
                self.assertEqual(slot[1], n_ch)
                self.assertEqual(message_length(slot[1]), lru_slot_size())

    def test_slot_round_trips_every_channel(self):
        for n_lru, n_ch in self.SHAPES:
            with self.subTest(num_lru=n_lru, channels=n_ch):
                self._use(n_lru, n_ch)
                channels = [LRUChannel(control=i & 0xFF, tx_phase=(i * 3) & 0x3F,
                                       tx_atten=(i * 5) & 0x3F, rx_phase=(i * 7) & 0x3F,
                                       rx_atten=(i * 11) & 0x3F)
                            for i in range(n_ch)]
                raw = LRUSlot(lru_id=1, command_type=0x01, channels=channels).to_bytes()
                self.assertEqual(len(raw), lru_slot_size())
                back = LRUSlot.from_bytes(1, raw)
                self.assertTrue(back.checksum_ok)
                self.assertEqual(len(back.channels), n_ch)
                for original, decoded in zip(channels, back.channels):
                    self.assertEqual(original.to_bytes(), decoded.to_bytes())

    def test_link_response_validates_at_every_shape(self):
        """
        A Link reply is a 10-byte message zero-padded into whatever the slot
        width is, so the padding must not break the checksum check.
        """
        for n_lru, n_ch in self.SHAPES:
            with self.subTest(num_lru=n_lru, channels=n_ch):
                self._use(n_lru, n_ch)
                slot = bytearray(lru_slot_size())
                slot[0] = LRUSlot.HEADER_BYTE
                slot[2] = CMD_STATUS
                slot[3] = STATUS_TYPE_LINK
                slot[4:9] = LINK_SENTINEL
                chk = 0
                for b in slot[:9]:
                    chk ^= b
                slot[9] = chk
                frame = bytes(HEADER_SIZE) + bytes(slot) * n_lru
                flags = parse_link_test_response(frame)
                self.assertEqual(len(flags), n_lru)
                self.assertTrue(all(flags))

    def test_diagnostic_response_decodes_every_channel(self):
        for n_lru, n_ch in self.SHAPES:
            with self.subTest(num_lru=n_lru, channels=n_ch):
                self._use(n_lru, n_ch)
                slot = bytearray(lru_slot_size())
                slot[0] = LRUSlot.HEADER_BYTE
                slot[1] = n_ch                       # full-slot message
                slot[3] = STATUS_TYPE_DIAGNOSTIC
                chk = 0
                for b in slot[:lru_slot_size() - 1]:
                    chk ^= b
                slot[lru_slot_size() - 1] = chk
                decoded = parse_diagnostic_response(bytes(slot),
                                                    DIAGNOSTIC_TYPE_FUTURE_BUFFER)
                self.assertIsNotNone(decoded)
                self.assertEqual(len(decoded["channels"]), n_ch)

    def test_header_packet_size_follows_the_shape(self):
        for n_lru, n_ch in self.SHAPES:
            with self.subTest(num_lru=n_lru, channels=n_ch):
                self._use(n_lru, n_ch)
                # Both directions default PACKET_SIZE to the frame size, and
                # neither may snapshot it at import time.
                self.assertEqual(QCCHeaderRx().packet_size, total_packet_size())
                self.assertEqual(QCCHeaderTx().packet_size, total_packet_size())

    def test_wire_format_limits_are_enforced(self):
        # channels_per_lru rides in a single byte.
        with self.assertRaises(lru_config.ConfigError):
            lru_config.LRUConfig(96, 256)
        # 0xFF is reserved for lru_select's broadcast.
        with self.assertRaises(lru_config.ConfigError):
            lru_config.LRUConfig(256, 4)
        # The header's packet_size is a u16.
        with self.assertRaises(lru_config.ConfigError):
            lru_config.LRUConfig(255, 255)
        for bad in (0, -1):
            with self.subTest(value=bad):
                with self.assertRaises(lru_config.ConfigError):
                    lru_config.LRUConfig(bad, 4)
                with self.assertRaises(lru_config.ConfigError):
                    lru_config.LRUConfig(96, bad)

if __name__ == "__main__":
    unittest.main(verbosity=2)
