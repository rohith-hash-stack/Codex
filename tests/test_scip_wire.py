"""Tests for the generic protobuf wire-format reader (directive D5 §16, §19)."""

from __future__ import annotations

import pytest

from codex.provider.scip.wire import (
    WIRE_TYPE_LENGTH_DELIMITED,
    WIRE_TYPE_VARINT,
    WireFormatError,
    as_bytes,
    as_int,
    as_str,
    decode_zigzag,
    packed_varints,
    parse_fields,
    read_varint,
)
from scip_fixtures import string_field, tag, varint, varint_field


def test_read_varint_single_byte() -> None:
    value, pos = read_varint(bytes([5]), 0)
    assert value == 5
    assert pos == 1


def test_read_varint_multi_byte() -> None:
    # 300 = 0b100101100 -> varint bytes [0xAC, 0x02]
    value, pos = read_varint(bytes([0xAC, 0x02]), 0)
    assert value == 300
    assert pos == 2


def test_read_varint_truncated_raises() -> None:
    with pytest.raises(WireFormatError, match="truncated varint"):
        read_varint(bytes([0x80]), 0)


def test_read_varint_too_long_raises() -> None:
    with pytest.raises(WireFormatError, match="too long"):
        read_varint(bytes([0x80] * 11 + [0x01]), 0)


def test_parse_fields_string_and_varint() -> None:
    data = string_field(1, "hello") + varint_field(2, 42)
    fields = parse_fields(data)
    assert as_str(fields[1][0], context="t") == "hello"
    assert as_int(fields[2][0], context="t") == 42


def test_parse_fields_repeated_field_preserves_order() -> None:
    data = varint_field(1, 10) + varint_field(1, 20) + varint_field(1, 30)
    fields = parse_fields(data)
    assert [as_int(f, context="t") for f in fields[1]] == [10, 20, 30]


def test_parse_fields_zero_field_number_raises() -> None:
    data = tag(0, WIRE_TYPE_VARINT) + varint(1)
    with pytest.raises(WireFormatError, match="zero field number"):
        parse_fields(data)


def test_parse_fields_unsupported_wire_type_raises() -> None:
    data = tag(1, 3)  # wire type 3 = deprecated "start group", unsupported here
    with pytest.raises(WireFormatError, match="unsupported wire type"):
        parse_fields(data)


def test_parse_fields_length_delimited_runs_past_end_raises() -> None:
    data = tag(1, WIRE_TYPE_LENGTH_DELIMITED) + varint(100) + b"short"
    with pytest.raises(WireFormatError, match="runs past end"):
        parse_fields(data)


def test_parse_fields_decodes_fixed64() -> None:
    data = tag(1, 1) + (12345).to_bytes(8, "little")  # wire type 1 = fixed64
    fields = parse_fields(data)
    assert as_int(fields[1][0], context="t") == 12345


def test_parse_fields_decodes_fixed32() -> None:
    data = tag(1, 5) + (999).to_bytes(4, "little")  # wire type 5 = fixed32
    fields = parse_fields(data)
    assert as_int(fields[1][0], context="t") == 999


def test_parse_fields_truncated_fixed64_raises() -> None:
    data = tag(1, 1) + b"\x00\x00\x00"  # wire type 1 = fixed64, needs 8 bytes
    with pytest.raises(WireFormatError, match="truncated fixed64"):
        parse_fields(data)


def test_parse_fields_truncated_fixed32_raises() -> None:
    data = tag(1, 5) + b"\x00\x00"  # wire type 5 = fixed32, needs 4 bytes
    with pytest.raises(WireFormatError, match="truncated fixed32"):
        parse_fields(data)


def test_parse_fields_empty_data_returns_empty_map() -> None:
    assert parse_fields(b"") == {}


def test_as_bytes_rejects_varint_field() -> None:
    fields = parse_fields(varint_field(1, 5))
    with pytest.raises(WireFormatError, match="expected length-delimited"):
        as_bytes(fields[1][0], context="t")


def test_as_int_rejects_length_delimited_field() -> None:
    fields = parse_fields(string_field(1, "x"))
    with pytest.raises(WireFormatError, match="expected varint/fixed"):
        as_int(fields[1][0], context="t")


def test_as_str_rejects_invalid_utf8() -> None:
    data = tag(1, WIRE_TYPE_LENGTH_DELIMITED) + varint(2) + b"\xff\xfe"
    fields = parse_fields(data)
    with pytest.raises(WireFormatError, match="invalid UTF-8"):
        as_str(fields[1][0], context="t")


def test_packed_varints_decodes_multiple_values() -> None:
    payload = varint(1) + varint(2) + varint(300)
    assert packed_varints(payload) == [1, 2, 300]


def test_packed_varints_empty() -> None:
    assert packed_varints(b"") == []


def test_decode_zigzag_positive_and_negative() -> None:
    assert decode_zigzag(0) == 0
    assert decode_zigzag(1) == -1
    assert decode_zigzag(2) == 1
    assert decode_zigzag(3) == -2
