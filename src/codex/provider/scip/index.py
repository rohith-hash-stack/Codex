"""A minimal, independently-written SCIP index decoder.

Decodes the subset of the SCIP wire format (`scip.proto`,
`github.com/sourcegraph/scip`, Apache-2.0 — see `docs/resources.md`)
that ``SCIPAdapter`` actually consumes, built on the generic protobuf
reader in ``codex.provider.scip.wire``.

This is a clean-room implementation per ``docs/policy-external-
references.md``: field numbers and semantics below were derived by
fetching and reading the public `scip.proto` schema text and by
inspecting real `.scip` artifacts produced by `scip-typescript`
(`docs/resources.md` records both). No SCIP source code (the Go/Rust/
TypeScript reference parsers) was read or copied — only the schema
(a public interface specification) and its own observable output.
Deliberately does not use the `protobuf` package or generate/vendor
code from `scip.proto`, to avoid any question of "introducing SCIP
[-adjacent] source as a production dependency" (Phase D directive D5
§3) — this module has zero non-stdlib dependencies.

Field numbers referenced here (confirmed 2026-08-30 against
`scip.proto` and cross-checked against real `scip-typescript` output):

    Index            { metadata=1, documents=2 (repeated), external_symbols=3 (repeated) }
    Metadata         { tool_info=2, project_root=3 }
    ToolInfo         { name=1, version=2 }
    Document         { relative_path=1, occurrences=2 (repeated), symbols=3 (repeated), language=4 }
    Occurrence       { range=1 (packed int32, deprecated), symbol=2, symbol_roles=3,
                        single_line_range=8, multi_line_range=9 }
    SingleLineRange  { line=1, start_character=2, end_character=3 }
    MultiLineRange   { start_line=1, start_character=2, end_line=3, end_character=4 }
    SymbolInformation{ symbol=1, relationships=4 (repeated), kind=5 }
    Relationship     { symbol=1, is_reference=2, is_implementation=3,
                        is_type_definition=4, is_definition=5 }

Unknown fields (any field number/message this module doesn't name
above) are silently skipped, matching protobuf's own designed-in
forward compatibility — a newer SCIP index with fields this module
doesn't know about still decodes the fields it does know about.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from codex.provider.scip.wire import (
    WireFormatError,
    as_bytes,
    as_int,
    as_str,
    packed_varints,
    parse_fields,
)


@dataclass(frozen=True)
class ScipRange:
    """A half-open [start, end) source range, 0-based lines/columns."""

    start_line: int
    start_character: int
    end_line: int
    end_character: int


@dataclass(frozen=True)
class ScipRelationship:
    symbol: str
    is_reference: bool = False
    is_implementation: bool = False
    is_type_definition: bool = False
    is_definition: bool = False


@dataclass(frozen=True)
class ScipSymbolInformation:
    symbol: str
    kind: int = 0
    relationships: tuple[ScipRelationship, ...] = ()
    enclosing_symbol: str = ""
    """``scip.proto`` field 8 -- an indexer's own, authoritative
    declaration of "the parent/owner of this symbol", intended by the
    spec itself for exactly this purpose (disambiguating a symbol whose
    own descriptor doesn't encode full lexical scope). Decoded for
    completeness and forward-compatibility; confirmed via direct
    wire-level inspection of real `scip-python@0.6.6` output that this
    producer never populates it (`docs/resources.md`'s FND-1 finding) --
    `codex.provider.scip_adapter` therefore cannot rely on it today and
    falls back to a position-based heuristic, but a future indexer
    version populating this field would already decode correctly."""


@dataclass(frozen=True)
class ScipOccurrence:
    symbol: str
    symbol_roles: int
    range: ScipRange | None


@dataclass(frozen=True)
class ScipDocument:
    relative_path: str
    language: str
    occurrences: tuple[ScipOccurrence, ...] = ()
    symbols: tuple[ScipSymbolInformation, ...] = ()


@dataclass(frozen=True)
class ScipToolInfo:
    name: str = ""
    version: str = ""


@dataclass(frozen=True)
class ScipMetadata:
    tool_info: ScipToolInfo = field(default_factory=ScipToolInfo)
    project_root: str = ""


@dataclass(frozen=True)
class ScipIndex:
    metadata: ScipMetadata
    documents: tuple[ScipDocument, ...] = ()
    external_symbols: tuple[ScipSymbolInformation, ...] = ()


def _decode_relationship(raw: bytes) -> ScipRelationship:
    fields = parse_fields(raw)
    symbol_fields = fields.get(1)
    if not symbol_fields:
        raise WireFormatError("Relationship missing required field `symbol`")
    return ScipRelationship(
        symbol=as_str(symbol_fields[0], context="Relationship.symbol"),
        is_reference=bool(as_int(fields[2][0], context="Relationship.is_reference"))
        if 2 in fields
        else False,
        is_implementation=bool(as_int(fields[3][0], context="Relationship.is_implementation"))
        if 3 in fields
        else False,
        is_type_definition=bool(as_int(fields[4][0], context="Relationship.is_type_definition"))
        if 4 in fields
        else False,
        is_definition=bool(as_int(fields[5][0], context="Relationship.is_definition"))
        if 5 in fields
        else False,
    )


def _decode_symbol_information(raw: bytes) -> ScipSymbolInformation:
    fields = parse_fields(raw)
    symbol_fields = fields.get(1)
    if not symbol_fields:
        raise WireFormatError("SymbolInformation missing required field `symbol`")
    relationships = tuple(
        _decode_relationship(as_bytes(f, context="SymbolInformation.relationships"))
        for f in fields.get(4, [])
    )
    kind = as_int(fields[5][0], context="SymbolInformation.kind") if 5 in fields else 0
    enclosing_symbol = (
        as_str(fields[8][0], context="SymbolInformation.enclosing_symbol") if 8 in fields else ""
    )
    return ScipSymbolInformation(
        symbol=as_str(symbol_fields[0], context="SymbolInformation.symbol"),
        kind=kind,
        relationships=relationships,
        enclosing_symbol=enclosing_symbol,
    )


def _decode_range_from_packed(raw: bytes) -> ScipRange | None:
    values = packed_varints(raw)
    if len(values) == 3:
        start_line, start_char, end_char = values
        return ScipRange(start_line, start_char, start_line, end_char)
    if len(values) == 4:
        start_line, start_char, end_line, end_char = values
        return ScipRange(start_line, start_char, end_line, end_char)
    raise WireFormatError(f"Occurrence.range must have 3 or 4 elements, got {len(values)}")


def _decode_single_line_range(raw: bytes) -> ScipRange:
    fields = parse_fields(raw)
    line = as_int(fields[1][0], context="SingleLineRange.line") if 1 in fields else 0
    start_char = (
        as_int(fields[2][0], context="SingleLineRange.start_character") if 2 in fields else 0
    )
    end_char = as_int(fields[3][0], context="SingleLineRange.end_character") if 3 in fields else 0
    return ScipRange(line, start_char, line, end_char)


def _decode_multi_line_range(raw: bytes) -> ScipRange:
    fields = parse_fields(raw)
    start_line = as_int(fields[1][0], context="MultiLineRange.start_line") if 1 in fields else 0
    start_char = (
        as_int(fields[2][0], context="MultiLineRange.start_character") if 2 in fields else 0
    )
    end_line = as_int(fields[3][0], context="MultiLineRange.end_line") if 3 in fields else 0
    end_char = as_int(fields[4][0], context="MultiLineRange.end_character") if 4 in fields else 0
    return ScipRange(start_line, start_char, end_line, end_char)


def _decode_occurrence(raw: bytes) -> ScipOccurrence:
    fields = parse_fields(raw)
    symbol = as_str(fields[2][0], context="Occurrence.symbol") if 2 in fields else ""
    symbol_roles = as_int(fields[3][0], context="Occurrence.symbol_roles") if 3 in fields else 0

    range_: ScipRange | None = None
    if 9 in fields:
        range_ = _decode_multi_line_range(as_bytes(fields[9][0], context="Occurrence"))
    elif 8 in fields:
        range_ = _decode_single_line_range(as_bytes(fields[8][0], context="Occurrence"))
    elif 1 in fields:
        range_ = _decode_range_from_packed(as_bytes(fields[1][0], context="Occurrence.range"))

    return ScipOccurrence(symbol=symbol, symbol_roles=symbol_roles, range=range_)


def _decode_document(raw: bytes) -> ScipDocument:
    fields = parse_fields(raw)
    path_fields = fields.get(1)
    if not path_fields:
        raise WireFormatError("Document missing required field `relative_path`")
    language = as_str(fields[4][0], context="Document.language") if 4 in fields else ""
    occurrences = tuple(
        _decode_occurrence(as_bytes(f, context="Document.occurrences")) for f in fields.get(2, [])
    )
    symbols = tuple(
        _decode_symbol_information(as_bytes(f, context="Document.symbols"))
        for f in fields.get(3, [])
    )
    return ScipDocument(
        relative_path=as_str(path_fields[0], context="Document.relative_path"),
        language=language,
        occurrences=occurrences,
        symbols=symbols,
    )


def _decode_tool_info(raw: bytes) -> ScipToolInfo:
    fields = parse_fields(raw)
    name = as_str(fields[1][0], context="ToolInfo.name") if 1 in fields else ""
    version = as_str(fields[2][0], context="ToolInfo.version") if 2 in fields else ""
    return ScipToolInfo(name=name, version=version)


def _decode_metadata(raw: bytes) -> ScipMetadata:
    fields = parse_fields(raw)
    tool_info = (
        _decode_tool_info(as_bytes(fields[2][0], context="Metadata.tool_info"))
        if 2 in fields
        else ScipToolInfo()
    )
    project_root = (
        as_str(fields[3][0], context="Metadata.project_root") if 3 in fields else ""
    )
    return ScipMetadata(tool_info=tool_info, project_root=project_root)


def decode_index(data: bytes) -> ScipIndex:
    """Decode a complete ``.scip`` artifact's bytes into a ``ScipIndex``.

    Raises ``codex.provider.scip.wire.WireFormatError`` for any
    malformed, truncated, or structurally invalid input — this is the
    one place ``SCIPAdapter`` needs to guard against untrusted artifact
    content (directive D5 §16).
    """
    if not data:
        raise WireFormatError("empty SCIP index artifact")
    fields = parse_fields(data)
    metadata = (
        _decode_metadata(as_bytes(fields[1][0], context="Index.metadata"))
        if 1 in fields
        else ScipMetadata()
    )
    documents = tuple(
        _decode_document(as_bytes(f, context="Index.documents")) for f in fields.get(2, [])
    )
    external_symbols = tuple(
        _decode_symbol_information(as_bytes(f, context="Index.external_symbols"))
        for f in fields.get(3, [])
    )
    return ScipIndex(metadata=metadata, documents=documents, external_symbols=external_symbols)


__all__ = [
    "ScipDocument",
    "ScipIndex",
    "ScipMetadata",
    "ScipOccurrence",
    "ScipRange",
    "ScipRelationship",
    "ScipSymbolInformation",
    "ScipToolInfo",
    "decode_index",
]
