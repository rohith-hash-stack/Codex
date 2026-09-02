"""Tests for the SCIP-specific decoder (directive D5 §16, §19).

Uses both handcrafted fixtures (``scip_fixtures.py``, for precise
edge-case control) and a real `.scip` artifact produced by
`scip-typescript` (``tests/fixtures/scip/typescript_sample.scip`` --
see `docs/resources.md` for how it was generated), per directive D5
§19's "validate important assumptions against independently generated/
real SCIP artifacts."
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codex.provider.scip.index import decode_index
from codex.provider.scip.wire import WireFormatError
from scip_fixtures import (
    bool_field,
    document,
    message_field,
    occurrence,
    relationship,
    scip_index,
    string_field,
    symbol_information,
    varint_field,
)

REAL_FIXTURE = Path(__file__).parent / "fixtures" / "scip" / "typescript_sample.scip"


def test_decode_empty_bytes_raises() -> None:
    with pytest.raises(WireFormatError, match="empty"):
        decode_index(b"")


def test_decode_garbage_bytes_raises() -> None:
    with pytest.raises(WireFormatError):
        decode_index(b"\xff\xff\xff\xff\xff\xff\xff\xff\xff")


def test_decode_minimal_index_with_metadata_only() -> None:
    data = scip_index(tool_name="acme-indexer", tool_version="2.1.0")
    idx = decode_index(data)
    assert idx.metadata.tool_info.name == "acme-indexer"
    assert idx.metadata.tool_info.version == "2.1.0"
    assert idx.documents == ()
    assert idx.external_symbols == ()


def test_decode_index_with_a_document_but_no_metadata_field_uses_defaults() -> None:
    # scip_index() always writes a metadata field; build the top-level Index by
    # hand to omit it entirely and confirm decode_index() doesn't assume its presence.
    doc = document("f.py", symbols=(symbol_information("A"),))
    idx = decode_index(message_field(2, doc))
    assert idx.metadata.tool_info.name == ""
    assert idx.metadata.project_root == ""
    assert idx.documents[0].relative_path == "f.py"


def test_decode_document_with_definition_occurrence() -> None:
    occ = occurrence("scip-test npm pkg 1.0.0 src/`a.py`/Foo#", roles=1, range_=(0, 6, 9))
    sym = symbol_information("scip-test npm pkg 1.0.0 src/`a.py`/Foo#", kind=7)
    doc = document("src/a.py", language="python", occurrences=(occ,), symbols=(sym,))
    idx = decode_index(scip_index(documents=(doc,)))

    assert len(idx.documents) == 1
    decoded_doc = idx.documents[0]
    assert decoded_doc.relative_path == "src/a.py"
    assert decoded_doc.language == "python"
    assert len(decoded_doc.occurrences) == 1
    assert decoded_doc.occurrences[0].symbol_roles == 1
    assert decoded_doc.occurrences[0].range is not None
    assert decoded_doc.occurrences[0].range.start_line == 0
    assert decoded_doc.occurrences[0].range.start_character == 6
    assert decoded_doc.occurrences[0].range.end_line == 0
    assert decoded_doc.occurrences[0].range.end_character == 9
    assert decoded_doc.symbols[0].kind == 7


def test_decode_four_element_range_is_multiline() -> None:
    occ = occurrence("scip-test npm pkg 1.0.0 x#", range_=(1, 2, 5, 8))
    idx = decode_index(scip_index(documents=(document("f", occurrences=(occ,)),)))
    r = idx.documents[0].occurrences[0].range
    assert r is not None
    assert (r.start_line, r.start_character, r.end_line, r.end_character) == (1, 2, 5, 8)


def test_decode_single_line_range_typed_field() -> None:
    single_line_range = varint_field(1, 3) + varint_field(2, 5) + varint_field(3, 9)
    occ = message_field(8, single_line_range) + string_field(2, "sym")
    idx = decode_index(scip_index(documents=(document("f", occurrences=(occ,)),)))
    r = idx.documents[0].occurrences[0].range
    assert r is not None
    assert (r.start_line, r.start_character, r.end_line, r.end_character) == (3, 5, 3, 9)


def test_decode_multi_line_range_typed_field() -> None:
    multi_line_range = (
        varint_field(1, 1) + varint_field(2, 2) + varint_field(3, 6) + varint_field(4, 0)
    )
    occ = message_field(9, multi_line_range) + string_field(2, "sym")
    idx = decode_index(scip_index(documents=(document("f", occurrences=(occ,)),)))
    r = idx.documents[0].occurrences[0].range
    assert r is not None
    assert (r.start_line, r.start_character, r.end_line, r.end_character) == (1, 2, 6, 0)


def test_decode_multi_line_range_takes_precedence_over_deprecated_range() -> None:
    # Per scip.proto: "When both typed_range and the deprecated range field
    # are set, typed_range takes precedence."
    multi_line_range = varint_field(1, 9) + varint_field(3, 9)
    occ = occurrence("sym", range_=(0, 0, 0)) + message_field(9, multi_line_range)
    idx = decode_index(scip_index(documents=(document("f", occurrences=(occ,)),)))
    r = idx.documents[0].occurrences[0].range
    assert r is not None
    assert r.start_line == 9


def test_decode_range_with_wrong_element_count_raises() -> None:
    occ = occurrence("x", range_=(1, 2))  # only 2 elements, invalid
    with pytest.raises(WireFormatError, match="3 or 4 elements"):
        decode_index(scip_index(documents=(document("f", occurrences=(occ,)),)))


def test_decode_relationship_flags() -> None:
    rel = relationship("Base#", is_implementation=True, is_reference=True)
    sym = symbol_information("Derived#", relationships=(rel,))
    idx = decode_index(scip_index(documents=(document("f", symbols=(sym,)),)))

    decoded_rel = idx.documents[0].symbols[0].relationships[0]
    assert decoded_rel.symbol == "Base#"
    assert decoded_rel.is_implementation is True
    assert decoded_rel.is_reference is True
    assert decoded_rel.is_type_definition is False
    assert decoded_rel.is_definition is False


def test_decode_multiple_documents_and_symbols() -> None:
    doc_a = document("a.py", symbols=(symbol_information("A"), symbol_information("B")))
    doc_b = document("b.py", symbols=(symbol_information("C"),))
    idx = decode_index(scip_index(documents=(doc_a, doc_b)))
    assert len(idx.documents) == 2
    assert len(idx.documents[0].symbols) == 2
    assert len(idx.documents[1].symbols) == 1


def test_decode_duplicate_symbol_records_are_all_preserved() -> None:
    # SCIP semantics permit the same symbol to appear more than once (e.g. multiple
    # occurrences referencing it) -- the decoder must not silently deduplicate.
    doc = document(
        "a.py",
        occurrences=(occurrence("X", roles=0), occurrence("X", roles=0), occurrence("X", roles=8)),
    )
    idx = decode_index(scip_index(documents=(doc,)))
    assert len(idx.documents[0].occurrences) == 3


def test_decode_external_symbols() -> None:
    ext = symbol_information("scip-test npm dep 2.0.0 Thing#", kind=7)
    idx = decode_index(scip_index(external_symbols=(ext,)))
    assert len(idx.external_symbols) == 1
    assert idx.external_symbols[0].symbol == "scip-test npm dep 2.0.0 Thing#"


def test_decode_document_missing_relative_path_raises() -> None:
    # A Document with only an occurrence, no field 1 (relative_path) at all.
    malformed_doc = message_field(2, occurrence("x", roles=1))
    with pytest.raises(WireFormatError, match="relative_path"):
        decode_index(scip_index(documents=(malformed_doc,)))


def test_decode_symbol_information_missing_symbol_raises() -> None:
    malformed_sym = string_field(3, "just documentation, no symbol field")
    doc = document("f.py", symbols=(malformed_sym,))
    with pytest.raises(WireFormatError, match="SymbolInformation.*symbol"):
        decode_index(scip_index(documents=(doc,)))


# FND-1 research finding (docs/resources.md): `SymbolInformation.enclosing_symbol`
# (`scip.proto` field 8) exists specifically to disambiguate a symbol whose own
# descriptor doesn't encode full lexical scope -- confirmed via direct wire-level
# inspection that real scip-python@0.6.6 output never populates it, but the
# decoder itself must still read it correctly for forward-compatibility.


def test_decode_symbol_information_enclosing_symbol_present() -> None:
    sym = symbol_information("Outer#inner().", kind=0) + string_field(8, "Outer#")
    doc = document("f.py", symbols=(sym,))
    idx = decode_index(scip_index(documents=(doc,)))
    assert idx.documents[0].symbols[0].enclosing_symbol == "Outer#"


def test_decode_symbol_information_enclosing_symbol_absent_defaults_empty() -> None:
    sym = symbol_information("Outer#inner().", kind=0)
    doc = document("f.py", symbols=(sym,))
    idx = decode_index(scip_index(documents=(doc,)))
    assert idx.documents[0].symbols[0].enclosing_symbol == ""


def test_decode_relationship_missing_symbol_raises() -> None:
    # is_implementation=True but no symbol (field 1) at all.
    malformed_rel = bool_field(3, True)
    sym = symbol_information("A", relationships=(malformed_rel,))
    with pytest.raises(WireFormatError, match="Relationship.*symbol"):
        decode_index(scip_index(documents=(document("f", symbols=(sym,)),)))


def test_decode_truncated_document_bytes_raises() -> None:
    good_doc = document("a.py", symbols=(symbol_information("A"),))
    truncated = good_doc[:-2]
    data = scip_index(documents=(truncated,))
    with pytest.raises(WireFormatError):
        decode_index(data)


def test_decode_unknown_fields_are_skipped_forward_compatibly() -> None:
    # A field number this decoder doesn't know about (99) must not break decoding
    # of the fields it does know about.
    doc = document("a.py", symbols=(symbol_information("A"),)) + varint_field(99, 1234)
    idx = decode_index(scip_index(documents=(doc,)))
    assert idx.documents[0].relative_path == "a.py"
    assert idx.documents[0].symbols[0].symbol == "A"


# --- Real, independently generated SCIP artifact (directive D5 §19) ---------


def test_decode_real_scip_typescript_artifact() -> None:
    data = REAL_FIXTURE.read_bytes()
    idx = decode_index(data)

    assert idx.metadata.tool_info.name == "scip-typescript"
    assert idx.metadata.tool_info.version == "0.4.0"
    paths = sorted(doc.relative_path for doc in idx.documents)
    assert paths == ["src/greeter.ts", "src/main.ts", "src/shapes.ts"]


def test_real_artifact_implements_relationship_decodes_correctly() -> None:
    idx = decode_index(REAL_FIXTURE.read_bytes())
    shapes = next(doc for doc in idx.documents if doc.relative_path == "src/shapes.ts")
    circle = next(sym for sym in shapes.symbols if sym.symbol.endswith("Circle#"))
    assert any(rel.is_implementation for rel in circle.relationships)
    shape_rel = next(rel for rel in circle.relationships if rel.symbol.endswith("Shape#"))
    assert shape_rel.is_implementation is True


def test_real_artifact_square_extends_and_implements_both_surface_as_implementation() -> None:
    # Empirically confirmed finding (docs/resources.md): SCIP's Relationship message
    # has no bit distinguishing "extends" from "implements" -- both are is_implementation.
    idx = decode_index(REAL_FIXTURE.read_bytes())
    shapes = next(doc for doc in idx.documents if doc.relative_path == "src/shapes.ts")
    square = next(sym for sym in shapes.symbols if sym.symbol.endswith("Square#"))
    target_symbols = {
        rel.symbol.rsplit("/", 1)[-1] for rel in square.relationships if rel.is_implementation
    }
    assert target_symbols == {"Circle#", "Shape#"}


def test_real_artifact_reference_occurrence_has_no_definition_role() -> None:
    idx = decode_index(REAL_FIXTURE.read_bytes())
    shapes = next(doc for doc in idx.documents if doc.relative_path == "src/shapes.ts")
    # The `implements Shape` clause references Shape# without defining it here.
    ref_occurrences = [
        occ
        for occ in shapes.occurrences
        if occ.symbol.endswith("Shape#") and not (occ.symbol_roles & 0x1)
    ]
    assert len(ref_occurrences) >= 1
