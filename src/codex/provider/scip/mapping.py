"""SCIP -> Codex ontology mapping helpers (Phase D directive D5 §4, §7-10).

Everything here is a pure function from decoded SCIP data
(``codex.provider.scip.index``) to Codex's own ontology types
(``codex.ontology``). No Codex component outside ``codex.provider.scip``
and ``codex.provider.scip_adapter`` imports this module — SCIP-specific
interpretation stays entirely inside the SCIP provider (directive D5 §5).

Two empirically-grounded design decisions, recorded here because they
directly shape what this module does (full reasoning in
``docs/architecture-conformance-audit.md``'s D5 entry and in
``docs/resources.md``):

1. ``SymbolInformation.kind`` is *not* reliably populated by real
   producers — a real `scip-typescript 0.4.0` index (`docs/resources.md`)
   reports ``UnspecifiedKind`` (0) for every symbol in a project with
   classes, interfaces, methods, and fields. ``infer_base_type()``
   therefore falls back to the symbol string's own descriptor-suffix
   punctuation (documented in `scip.proto`'s `Descriptor.Suffix` enum
   and confirmed against real output) whenever ``kind`` is unspecified,
   rather than defaulting every symbol to one generic bucket.
2. SCIP's ``Relationship`` message has no bit distinguishing "extends a
   class" from "implements an interface" — both surface as
   ``is_implementation=True`` in real output (confirmed against a real
   `implements`/`extends` fixture). Emitting ``EXTENDS`` would require
   guessing from ``kind`` (which is exactly the field just shown to be
   unreliable), so this module maps every ``is_implementation`` fact to
   ``RelationshipType.IMPLEMENTS`` only, never ``EXTENDS`` — directive
   D5 §8 requires a relationship only be produced when SCIP evidence
   "deterministically establishes" it, and this one does not.
"""

from __future__ import annotations

from dataclasses import dataclass

from codex.ontology.entities import BaseEntityType

# SCIP's SymbolInformation.Kind enum: every name confirmed 2026-08-30 by
# compiling the public `scip.proto` and enumerating its values (see
# docs/resources.md) -- kept as one authoritative int -> name table so
# both the base-type mapping below and `role_for_kind()` share it rather
# than each guessing at spelling independently.
KIND_NAMES: dict[int, str] = {
    0: "UnspecifiedKind",
    1: "Array",
    2: "Assertion",
    3: "AssociatedType",
    4: "Attribute",
    5: "Axiom",
    6: "Boolean",
    7: "Class",
    8: "Constant",
    9: "Constructor",
    10: "DataFamily",
    11: "Enum",
    12: "EnumMember",
    13: "Event",
    14: "Fact",
    15: "Field",
    16: "File",
    17: "Function",
    18: "Getter",
    19: "Grammar",
    20: "Instance",
    21: "Interface",
    22: "Key",
    23: "Lang",
    24: "Lemma",
    25: "Macro",
    26: "Method",
    27: "MethodReceiver",
    28: "Message",
    29: "Module",
    30: "Namespace",
    31: "Null",
    32: "Number",
    33: "Object",
    34: "Operator",
    35: "Package",
    36: "PackageObject",
    37: "Parameter",
    38: "ParameterLabel",
    39: "Pattern",
    40: "Predicate",
    41: "Property",
    42: "Protocol",
    43: "Quasiquoter",
    44: "SelfParameter",
    45: "Setter",
    46: "Signature",
    47: "Subscript",
    48: "String",
    49: "Struct",
    50: "Tactic",
    51: "Theorem",
    52: "ThisParameter",
    53: "Trait",
    54: "Type",
    55: "TypeAlias",
    56: "TypeClass",
    57: "TypeFamily",
    58: "TypeParameter",
    59: "Union",
    60: "Value",
    61: "Variable",
    62: "Contract",
    63: "Error",
    64: "Library",
    65: "Modifier",
    66: "AbstractMethod",
    67: "MethodSpecification",
    68: "ProtocolMethod",
    69: "PureVirtualMethod",
    70: "TraitMethod",
    71: "TypeClassMethod",
    72: "Accessor",
    73: "Delegate",
    74: "MethodAlias",
    75: "SingletonClass",
    76: "SingletonMethod",
    77: "StaticDataMember",
    78: "StaticEvent",
    79: "StaticField",
    80: "StaticMethod",
    81: "StaticProperty",
    82: "StaticVariable",
    84: "Extension",
    85: "Mixin",
    86: "Concept",
}

# Only kinds with a clear, non-speculative mapping onto Codex's 16 base
# types are listed; anything absent here (UnspecifiedKind, an unnamed
# int, or an exotic kind with no faithful Codex equivalent, e.g.
# theorem-prover kinds like Axiom/Lemma/Tactic) falls through to
# `infer_base_type()`'s descriptor-suffix inference instead of being
# forced into a guessed bucket.
_KIND_TO_BASE_TYPE: dict[int, BaseEntityType] = {
    # Interface-like
    21: BaseEntityType.INTERFACE,  # Interface
    42: BaseEntityType.INTERFACE,  # Protocol
    53: BaseEntityType.INTERFACE,  # Trait
    56: BaseEntityType.INTERFACE,  # TypeClass
    # Class-like
    7: BaseEntityType.CLASS,  # Class
    49: BaseEntityType.CLASS,  # Struct
    11: BaseEntityType.CLASS,  # Enum
    59: BaseEntityType.CLASS,  # Union
    33: BaseEntityType.CLASS,  # Object
    75: BaseEntityType.CLASS,  # SingletonClass
    85: BaseEntityType.CLASS,  # Mixin
    84: BaseEntityType.CLASS,  # Extension
    54: BaseEntityType.CLASS,  # Type
    55: BaseEntityType.CLASS,  # TypeAlias
    3: BaseEntityType.CLASS,  # AssociatedType
    # Namespace/module-like
    30: BaseEntityType.NAMESPACE,  # Namespace
    29: BaseEntityType.MODULE,  # Module
    35: BaseEntityType.MODULE,  # Package
    36: BaseEntityType.MODULE,  # PackageObject
    64: BaseEntityType.MODULE,  # Library
    # Function-like
    17: BaseEntityType.FUNCTION,  # Function
    25: BaseEntityType.FUNCTION,  # Macro
    # Method-like
    26: BaseEntityType.METHOD,  # Method
    66: BaseEntityType.METHOD,  # AbstractMethod
    9: BaseEntityType.METHOD,  # Constructor
    80: BaseEntityType.METHOD,  # StaticMethod
    76: BaseEntityType.METHOD,  # SingletonMethod
    69: BaseEntityType.METHOD,  # PureVirtualMethod
    74: BaseEntityType.METHOD,  # MethodAlias
    67: BaseEntityType.METHOD,  # MethodSpecification
    68: BaseEntityType.METHOD,  # ProtocolMethod
    70: BaseEntityType.METHOD,  # TraitMethod
    71: BaseEntityType.METHOD,  # TypeClassMethod
    18: BaseEntityType.METHOD,  # Getter
    45: BaseEntityType.METHOD,  # Setter
    72: BaseEntityType.METHOD,  # Accessor
    34: BaseEntityType.METHOD,  # Operator
    47: BaseEntityType.METHOD,  # Subscript
    # Variable/field-like
    61: BaseEntityType.VARIABLE,  # Variable
    15: BaseEntityType.VARIABLE,  # Field
    41: BaseEntityType.VARIABLE,  # Property
    79: BaseEntityType.VARIABLE,  # StaticField
    81: BaseEntityType.VARIABLE,  # StaticProperty
    82: BaseEntityType.VARIABLE,  # StaticVariable
    77: BaseEntityType.VARIABLE,  # StaticDataMember
    78: BaseEntityType.VARIABLE,  # StaticEvent
    13: BaseEntityType.VARIABLE,  # Event
    8: BaseEntityType.VARIABLE,  # Constant
    12: BaseEntityType.VARIABLE,  # EnumMember
    # File
    16: BaseEntityType.FILE,  # File
}

# `Descriptor.Suffix`, from `scip.proto`, and the punctuation each
# renders as in a symbol string's final descriptor segment (confirmed
# against real `scip-typescript` output in `docs/resources.md`). Used
# only when `kind` gives no answer.
_SKIP_SUFFIXES = frozenset({")"})  # a bare Parameter descriptor: `(name)` — no entity created


@dataclass(frozen=True)
class ParsedSymbol:
    """The header portion of a non-local SCIP symbol string.

    SCIP symbol strings are ``<scheme> <manager> <name> <version>
    <descriptor-path>``, or ``local <local-id>`` for file-local symbols
    (`scip.proto`'s `Symbol`/`Package` messages, confirmed against real
    output — see `docs/resources.md`). This module never depends on a
    generated `Symbol` protobuf message; it splits the string directly,
    which is sufficient for every field this adapter actually needs.
    """

    scheme: str
    manager: str
    package_name: str
    package_version: str
    descriptor_path: str


def _normalize_package_field(value: str) -> str:
    """Undo SCIP's own ``"."`` placeholder for an empty `Package` field.

    Confirmed against the reference `scip` Rust crate's
    `bindings/rust/src/symbol.rs` (`raw.githubusercontent.com/sourcegraph/
    scip/main/bindings/rust/src/symbol.rs`, fetched directly — read only
    for its documented format/parse *behavior*, per
    `docs/policy-external-references.md`; no code copied): `format_symbol`
    emits literal ``"."`` for any `Package.manager`/`.name`/`.version`
    that is empty or the `Package` itself is absent (space-separated
    symbol strings can't represent an empty token directly), and its own
    `parse_symbol`'s `dot()` helper converts that ``"."`` straight back to
    ``""`` — round-tripped and proven by the crate's own test suite
    (`formats_symbol_with_dots`). ``"."`` therefore means "this field is
    unset," not a literal period, and the empty string is the
    semantically correct value — never the raw ``"."`` token itself.
    """
    return "" if value == "." else value


def parse_symbol(symbol: str) -> ParsedSymbol | None:
    """Parse a non-local SCIP symbol string's header. Returns ``None`` for a
    local symbol (``scheme == "local"``) or a string that doesn't match the
    expected 5-token header shape (never guesses at a malformed symbol).

    ``manager``/``package_name``/``package_version`` are normalized via
    `_normalize_package_field` so a real producer's ``"."`` placeholder
    for an unset field never leaks into Codex's own identity strings as
    a literal period (Phase D gap-closure directive, Gap A).
    """
    if symbol.startswith("local "):
        return None
    parts = symbol.split(" ", maxsplit=4)
    if len(parts) != 5:
        return None
    scheme, manager, name, version, descriptor_path = parts
    return ParsedSymbol(
        scheme,
        _normalize_package_field(manager),
        _normalize_package_field(name),
        _normalize_package_field(version),
        descriptor_path,
    )


def is_local_symbol(symbol: str) -> bool:
    return symbol == "local" or symbol.startswith("local ")


def infer_base_type(*, kind: int, symbol: str) -> BaseEntityType | None:
    """Best-effort, deterministic base type for a SCIP symbol.

    Returns ``None`` when no entity should be created for this symbol at
    all (a bare `Parameter` descriptor, or a descriptor path this module
    can't confidently classify) — callers must treat that as "skip", not
    as an error.
    """
    if kind in _KIND_TO_BASE_TYPE:
        return _KIND_TO_BASE_TYPE[kind]

    # kind is UnspecifiedKind (or an exotic kind not in the table above):
    # fall back to the descriptor path's own trailing punctuation. A
    # symbol ending in "/" is itself a file/namespace descriptor (its
    # "last segment" after the final "/" is empty by construction) --
    # checked first so the empty-segment case below doesn't swallow it.
    if not symbol:
        return None
    if symbol.endswith("/"):
        return BaseEntityType.FILE

    # A symbol that doesn't end in "/" (checked above) always has a
    # non-empty last "/"-separated segment.
    last_segment = symbol.rsplit("/", maxsplit=1)[-1]
    trailing = last_segment[-1]
    if trailing in _SKIP_SUFFIXES:
        return None
    if last_segment.endswith("()."):
        # Method suffix. A `#` earlier in the *full* symbol (not just this
        # segment) means it's nested under a type -> METHOD; otherwise it's
        # a top-level function -> FUNCTION.
        return BaseEntityType.METHOD if "#" in symbol else BaseEntityType.FUNCTION
    if trailing == "#":
        return BaseEntityType.CLASS
    if trailing == ".":
        return BaseEntityType.VARIABLE
    if trailing == ":":
        # GAP-12 fix: `:` is `scip.proto`'s own `Descriptor.Suffix.Meta`
        # punctuation, alongside `/` (Namespace), `#` (Type), `().`
        # (Method) and `.` (Term) above -- a real, documented SCIP
        # descriptor kind this function simply never enumerated, not
        # malformed or ambiguous data. scip-python emits exactly one such
        # symbol per source file, always shaped `<dotted-module>/__init__:`
        # (confirmed: the segment immediately before `:` is always the
        # literal `__init__:`, never anything else, across every real
        # index checked) -- the module's own self-identity, distinct from
        # the file's own FILE entity (a separate identity path, built
        # from `Document.relative_path` by `GitAdapter`/this adapter's
        # own FILE-subject convention) and from any class/function/
        # variable defined inside it. Real-data measurement
        # (`docs/python-fidelity-gap-register.md`, GAP-12): this shape
        # was previously unclassified -> `None` -> silently no entity,
        # silently dropping every reference/import fact naming a module
        # by its own identity (31,411 real occurrences lost across 5
        # repositories). `BaseEntityType.MODULE` is the pre-existing,
        # already-`_KIND_TO_BASE_TYPE`-mapped (kind 29) type for exactly
        # this concept -- reused verbatim, no new entity type invented.
        return BaseEntityType.MODULE
    return None


def role_for_kind(kind: int) -> str | None:
    """A ``RepositorySymbol.roles[]`` entry preserving SCIP's own
    (possibly finer-grained) kind name, when it's known (TAD §13: base
    types stay coarse, roles carry the finer distinction)."""
    if kind == 0:
        return None
    name = KIND_NAMES.get(kind)
    return f"scip:{name}" if name is not None else f"scip:kind-{kind}"


__all__ = [
    "KIND_NAMES",
    "ParsedSymbol",
    "infer_base_type",
    "is_local_symbol",
    "parse_symbol",
    "role_for_kind",
]
