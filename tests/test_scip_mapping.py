"""Tests for SCIP -> Codex ontology mapping helpers (directive D5 §4, §7-10)."""

from __future__ import annotations

from codex.ontology.entities import BaseEntityType
from codex.provider.scip.mapping import (
    infer_base_type,
    is_local_symbol,
    parse_symbol,
    role_for_kind,
)


def test_parse_symbol_standard_header() -> None:
    parsed = parse_symbol("scip-typescript npm scip-test 1.0.0 src/`greeter.ts`/Greeter#greet().")
    assert parsed is not None
    assert parsed.scheme == "scip-typescript"
    assert parsed.manager == "npm"
    assert parsed.package_name == "scip-test"
    assert parsed.package_version == "1.0.0"
    assert parsed.descriptor_path == "src/`greeter.ts`/Greeter#greet()."


def test_parse_symbol_local_returns_none() -> None:
    assert parse_symbol("local 2") is None


def test_parse_symbol_malformed_header_returns_none() -> None:
    assert parse_symbol("not-enough-tokens") is None
    assert parse_symbol("") is None


def test_is_local_symbol() -> None:
    assert is_local_symbol("local 2") is True
    assert is_local_symbol("local") is True
    assert is_local_symbol("scip-typescript npm pkg 1.0.0 x#") is False


def test_infer_base_type_from_known_kind() -> None:
    # kind=7 is Class per scip.proto's SymbolInformation.Kind enum.
    assert infer_base_type(kind=7, symbol="whatever") == BaseEntityType.CLASS


def test_infer_base_type_interface_kind() -> None:
    assert infer_base_type(kind=21, symbol="whatever") == BaseEntityType.INTERFACE


def test_infer_base_type_unspecified_kind_falls_back_to_suffix_class() -> None:
    symbol = "scip-ts npm p 1.0.0 src/`a.ts`/Foo#"
    assert infer_base_type(kind=0, symbol=symbol) == BaseEntityType.CLASS


def test_infer_base_type_unspecified_kind_method_suffix_nested_in_type() -> None:
    symbol = "scip-ts npm p 1.0.0 src/`a.ts`/Foo#bar()."
    assert infer_base_type(kind=0, symbol=symbol) == BaseEntityType.METHOD


def test_infer_base_type_unspecified_kind_method_suffix_top_level_is_function() -> None:
    symbol = "scip-ts npm p 1.0.0 src/`a.ts`/bar()."
    assert infer_base_type(kind=0, symbol=symbol) == BaseEntityType.FUNCTION


def test_infer_base_type_unspecified_kind_term_suffix_is_variable() -> None:
    symbol = "scip-ts npm p 1.0.0 src/`a.ts`/Foo#field."
    assert infer_base_type(kind=0, symbol=symbol) == BaseEntityType.VARIABLE


def test_infer_base_type_unspecified_kind_file_suffix() -> None:
    symbol = "scip-ts npm p 1.0.0 src/`a.ts`/"
    assert infer_base_type(kind=0, symbol=symbol) == BaseEntityType.FILE


def test_infer_base_type_parameter_descriptor_returns_none() -> None:
    symbol = "scip-ts npm p 1.0.0 src/`a.ts`/Foo#`<constructor>`().(message)"
    assert infer_base_type(kind=0, symbol=symbol) is None


def test_infer_base_type_unclassifiable_returns_none() -> None:
    assert infer_base_type(kind=0, symbol="") is None


def test_infer_base_type_unrecognized_trailing_punctuation_returns_none() -> None:
    symbol = "scip-ts npm p 1.0.0 src/`a.ts`/weird]"
    assert infer_base_type(kind=0, symbol=symbol) is None


def test_role_for_kind_unspecified_is_none() -> None:
    assert role_for_kind(0) is None


def test_role_for_kind_known() -> None:
    assert role_for_kind(66) == "scip:AbstractMethod"  # AbstractMethod


def test_role_for_kind_unknown_int_falls_back_to_generic_label() -> None:
    assert role_for_kind(9999) == "scip:kind-9999"
