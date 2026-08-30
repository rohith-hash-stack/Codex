"""A minimal, generic Protocol Buffers wire-format reader.

Independently written from the public Protocol Buffers wire-format
specification (varint/length-delimited/fixed32/fixed64 encoding is a
generic, openly documented binary format — not SCIP-specific and not
sourced from any SCIP implementation). This exists so ``codex.provider
.scip`` can decode a ``.scip`` index without depending on the
``protobuf`` package or generating/vendoring code from SCIP's own
``scip.proto`` schema — avoiding any question of whether doing so
would count as "introducing SCIP source code as a production
dependency" (Phase D directive D5 §3). Nothing here knows what a SCIP
message looks like; ``codex.provider.scip.index`` supplies that.

Malformed input handling: every function here raises ``WireFormatError``
(never lets a raw ``IndexError``/``struct.error`` propagate) so a
corrupted or truncated artifact can be turned into a clean adapter-level
failure rather than crashing ingestion (directive D5 §16).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

WIRE_TYPE_VARINT: Final = 0
WIRE_TYPE_FIXED64: Final = 1
WIRE_TYPE_LENGTH_DELIMITED: Final = 2
WIRE_TYPE_FIXED32: Final = 5
_KNOWN_WIRE_TYPES: Final = frozenset(
    {WIRE_TYPE_VARINT, WIRE_TYPE_FIXED64, WIRE_TYPE_LENGTH_DELIMITED, WIRE_TYPE_FIXED32}
)


class WireFormatError(ValueError):
    """Raised for any malformed/truncated/unsupported protobuf wire data."""


@dataclass(frozen=True)
class Field:
    """One decoded top-level field: its wire type and raw payload.

    ``value`` is an ``int`` for VARINT/FIXED32/FIXED64, or ``bytes`` for
    LENGTH_DELIMITED (a string, an embedded message, or a packed
    repeated scalar field — the caller decides which, by field number).
    """

    wire_type: int
    value: int | bytes


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Decode one base-128 varint starting at ``pos``. Returns (value, next_pos)."""
    result = 0
    shift = 0
    start = pos
    while True:
        if pos >= len(data):
            raise WireFormatError(f"truncated varint starting at offset {start}")
        byte = data[pos]
        result |= (byte & 0x7F) << shift
        pos += 1
        if not (byte & 0x80):
            return result, pos
        shift += 7
        if shift > 63:
            raise WireFormatError(f"varint too long starting at offset {start}")


def parse_fields(data: bytes) -> dict[int, list[Field]]:
    """Decode ``data`` into a map of field number -> every occurrence of that field.

    Preserves repetition and order (both matter for ``repeated`` fields).
    Unknown wire types are rejected outright rather than guessed at.
    """
    fields: dict[int, list[Field]] = {}
    pos = 0
    length = len(data)
    while pos < length:
        tag, pos = read_varint(data, pos)
        field_number = tag >> 3
        wire_type = tag & 0x7
        if field_number == 0:
            raise WireFormatError("decoded a zero field number")
        if wire_type not in _KNOWN_WIRE_TYPES:
            raise WireFormatError(f"unsupported wire type {wire_type} (field {field_number})")

        value: int | bytes
        if wire_type == WIRE_TYPE_VARINT:
            value, pos = read_varint(data, pos)
        elif wire_type == WIRE_TYPE_LENGTH_DELIMITED:
            size, pos = read_varint(data, pos)
            end = pos + size
            if size < 0 or end > length:
                raise WireFormatError(f"length-delimited field {field_number} runs past end")
            value = data[pos:end]
            pos = end
        elif wire_type == WIRE_TYPE_FIXED64:
            if pos + 8 > length:
                raise WireFormatError(f"truncated fixed64 field {field_number}")
            value = int.from_bytes(data[pos : pos + 8], "little")
            pos += 8
        else:  # WIRE_TYPE_FIXED32
            if pos + 4 > length:
                raise WireFormatError(f"truncated fixed32 field {field_number}")
            value = int.from_bytes(data[pos : pos + 4], "little")
            pos += 4

        fields.setdefault(field_number, []).append(Field(wire_type, value))
    return fields


def decode_zigzag(value: int) -> int:
    """Decode a zigzag-encoded sint32/sint64 varint. Unused by any SCIP field
    read today (SCIP's int32 fields are plain varints, not zigzag), kept only
    because it's a standard part of the wire format this module documents."""
    return (value >> 1) ^ -(value & 1)


def packed_varints(data: bytes) -> list[int]:
    """Decode a packed-repeated varint field's raw bytes into a list of ints
    (used for ``Occurrence.range``, proto3's default packing for ``repeated
    int32``)."""
    values: list[int] = []
    pos = 0
    length = len(data)
    while pos < length:
        value, pos = read_varint(data, pos)
        values.append(value)
    return values


def as_bytes(field: Field, *, context: str) -> bytes:
    if not isinstance(field.value, (bytes, bytearray)):
        raise WireFormatError(f"{context}: expected length-delimited field, got varint/fixed")
    return bytes(field.value)


def as_int(field: Field, *, context: str) -> int:
    if not isinstance(field.value, int):
        raise WireFormatError(f"{context}: expected varint/fixed field, got length-delimited")
    return field.value


def as_str(field: Field, *, context: str) -> str:
    raw = as_bytes(field, context=context)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WireFormatError(f"{context}: invalid UTF-8") from exc
