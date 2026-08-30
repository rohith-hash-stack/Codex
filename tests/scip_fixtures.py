"""A minimal, test-only SCIP protobuf *encoder* -- the counterpart to
``codex.provider.scip.wire``'s decoder, used to build precise, valid
``.scip`` artifact bytes for tests without any external dependency or
language-specific indexer toolchain.

Field numbers match ``codex.provider.scip.index``'s documented table
(itself confirmed against the real `scip.proto`); a round-trip test in
``test_scip_index.py`` proves this encoder and the production decoder
agree byte-for-byte.
"""

from __future__ import annotations


def varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("varint encoding here only supports non-negative ints")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def tag(field_number: int, wire_type: int) -> bytes:
    return varint((field_number << 3) | wire_type)


def len_delimited(field_number: int, data: bytes) -> bytes:
    return tag(field_number, 2) + varint(len(data)) + data


def string_field(field_number: int, value: str) -> bytes:
    return len_delimited(field_number, value.encode("utf-8"))


def varint_field(field_number: int, value: int) -> bytes:
    return tag(field_number, 0) + varint(value)


def bool_field(field_number: int, value: bool) -> bytes:
    return varint_field(field_number, 1 if value else 0)


def packed_varints_field(field_number: int, values: list[int]) -> bytes:
    return len_delimited(field_number, b"".join(varint(v) for v in values))


def message_field(field_number: int, data: bytes) -> bytes:
    return len_delimited(field_number, data)


def relationship(
    symbol: str,
    *,
    is_reference: bool = False,
    is_implementation: bool = False,
    is_type_definition: bool = False,
    is_definition: bool = False,
) -> bytes:
    parts = [string_field(1, symbol)]
    if is_reference:
        parts.append(bool_field(2, True))
    if is_implementation:
        parts.append(bool_field(3, True))
    if is_type_definition:
        parts.append(bool_field(4, True))
    if is_definition:
        parts.append(bool_field(5, True))
    return b"".join(parts)


def symbol_information(
    symbol: str, *, kind: int = 0, relationships: tuple[bytes, ...] = ()
) -> bytes:
    parts = [string_field(1, symbol)]
    for rel in relationships:
        parts.append(message_field(4, rel))
    if kind:
        parts.append(varint_field(5, kind))
    return b"".join(parts)


def occurrence(
    symbol: str, *, roles: int = 0, range_: tuple[int, ...] | None = None
) -> bytes:
    parts = []
    if range_ is not None:
        parts.append(packed_varints_field(1, list(range_)))
    if symbol:
        parts.append(string_field(2, symbol))
    if roles:
        parts.append(varint_field(3, roles))
    return b"".join(parts)


def document(
    relative_path: str,
    *,
    language: str = "",
    occurrences: tuple[bytes, ...] = (),
    symbols: tuple[bytes, ...] = (),
) -> bytes:
    parts = [string_field(1, relative_path)]
    for occ in occurrences:
        parts.append(message_field(2, occ))
    for sym in symbols:
        parts.append(message_field(3, sym))
    if language:
        parts.append(string_field(4, language))
    return b"".join(parts)


def tool_info(name: str, version: str) -> bytes:
    return string_field(1, name) + string_field(2, version)


def metadata(*, tool_name: str = "", tool_version: str = "", project_root: str = "") -> bytes:
    parts = []
    if tool_name or tool_version:
        parts.append(message_field(2, tool_info(tool_name, tool_version)))
    if project_root:
        parts.append(string_field(3, project_root))
    return b"".join(parts)


def scip_index(
    *,
    tool_name: str = "test-indexer",
    tool_version: str = "1.0.0",
    documents: tuple[bytes, ...] = (),
    external_symbols: tuple[bytes, ...] = (),
) -> bytes:
    parts = [
        message_field(1, metadata(tool_name=tool_name, tool_version=tool_version))
    ]
    for doc in documents:
        parts.append(message_field(2, doc))
    for sym in external_symbols:
        parts.append(message_field(3, sym))
    return b"".join(parts)
