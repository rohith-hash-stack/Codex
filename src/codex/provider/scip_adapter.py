"""The SCIP Adapter (HLRD Resource Map §62; TAD §8-9; Phase D directive D5).

A clean-room `ProviderAdapter` (D1) for SCIP indexes (`scip.proto`,
`github.com/sourcegraph/scip`, Apache-2.0 — see `docs/resources.md`),
built entirely on `codex.provider.scip` (an independently-written,
dependency-free decoder — see that package's own docstrings for the
clean-room provenance of every field number it reads).

Capability / evidence mapping (directive D5 §4), determined *before*
writing this file and validated against real `scip-typescript`-produced
`.scip` artifacts (`docs/resources.md`):

- `Occurrence` with the `Definition` role -> capability SYMBOL_DEFINITION
  -> entity `RepositorySymbol` (base_type via `Kind`/descriptor-suffix
  inference, `source_location` from the occurrence range) -> no
  relationship (mirrors D3's `HISTORY`: a unary fact, nothing synthesized).
- `Occurrence` without the `Definition` role -> capability
  SYMBOL_REFERENCE -> no new entity of its own -> `REFERENCES` (default)
  or `IMPORTS` (`Import` role bit set), subject = the document's own FILE
  entity (deterministic `canonical_id`, converges with `GitAdapter`'s
  FILE entities for the same repo+revision+path -- directive D5 §9).
- `SymbolInformation.relationships[].is_implementation` -> capability
  IMPLEMENTATION -> `IMPLEMENTS`, subject = the symbol carrying the
  relationship, object = `relationship.symbol`.
- `SymbolInformation.relationships[].is_type_definition` -> capability
  TYPE_RELATIONSHIP -> `REFERENCES`, subject/object as above.
- A referenced symbol not defined anywhere in this index -> folded into
  SYMBOL_REFERENCE -> entity `RepositorySymbol(base_type=EXTERNAL_LIBRARY,
  ...)` -> `REFERENCES` from the referencing file. Package-qualified
  `qualified_name` (`<manager>:<name>@<version>`); `canonical_id` uses a
  fixed `"external"` revision sentinel so a library's identity doesn't
  change every time the *consuming* repo's revision does (directive D5
  §10).

**Deliberately not implemented, with reasons (directive D5 §7-8):**

- `CALL_RELATIONSHIP` — SCIP's `SymbolRole` bitset (confirmed against
  the real `scip.proto`) has *no* "this is a call" bit, and a real
  `scip-typescript` fixture confirms a constructor call surfaces as a
  bare reference occurrence indistinguishable from a type mention.
  Reliably detecting a call would need source-text/syntax analysis
  this adapter doesn't have. Producing `CALLS` anyway would be
  "treating every SCIP reference as CALLS" — directive D5 §8
  explicitly prohibits exactly this.
- `EXTENDS` (as distinct from `IMPLEMENTS`) — SCIP's `Relationship`
  message has no bit distinguishing class-extends from interface-
  implements; both surface as `is_implementation=True` in real output
  (confirmed against a real `implements`/`extends` fixture — see
  `codex.provider.scip.mapping`'s module docstring). Every
  `is_implementation` fact is mapped to `IMPLEMENTS` only.
- `DATA_FLOW`, `DEPENDENCY` — no deterministic SCIP signal establishes
  either (`DATA_FLOW` is CodeQL's distinguishing capability per
  `docs/research/provider-formats.md`; SCIP's `Import` role is a
  symbol-level fact, not the package-level claim `DEPENDS_ON` implies).
- `SOURCE_LOCATION` as a *separate* declared capability — SCIP never
  offers "just a location" independent of a definition; `source_location`
  is a field on the entities `SYMBOL_DEFINITION` already produces.

**Input contract (directive D5 §15):** this adapter reads an
*already-generated* `.scip` index file from a configured path relative
to the repository root (`index_filename`, default `"index.scip"` —
matching `scip-typescript`'s own default `--output` name, confirmed
empirically). It does **not** invoke `scip-python`/`scip-typescript`/
any language-specific indexer itself; generating the index is an
operator/environment responsibility, documented here rather than
folded into D5's scope, exactly as the directive's §15 last sentence
requires.

**Identity consistency (important internal invariant):** a symbol's
canonical id must be computed the *same* way whether it's being
processed as a definition or as the object of some other symbol's
reference/relationship — otherwise the same real-world symbol would
get two different `canonical_id`s depending on which capability
touched it first, silently splitting one entity into two in the graph.
Both paths go through ``_resolve_symbol`` using the *same*
``kind_by_symbol`` lookup built once in ``extract()``, so a locally
defined symbol always resolves to the same base type (and therefore
the same canonical id) everywhere it's mentioned in a given run.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from codex.evidence.model import CoverageStatus, Evidence, EvidenceCohort
from codex.ontology.entities import (
    BaseEntityType,
    RepositorySymbol,
    SourceLocation,
    build_canonical_id,
)
from codex.ontology.relationships import RelationshipType
from codex.provider.capability import Capability
from codex.provider.contract import (
    EligibilityStatus,
    ExtractionResult,
    NormalizedEvidence,
    ProviderEligibility,
    ProviderExtractionError,
    ProviderFailureReason,
    ProviderHealthStatus,
    ValidationResult,
)
from codex.provider.scip.index import (
    ScipDocument,
    ScipIndex,
    ScipOccurrence,
    ScipRange,
    decode_index,
)
from codex.provider.scip.mapping import (
    infer_base_type,
    is_local_symbol,
    parse_symbol,
    role_for_kind,
)
from codex.provider.scip.wire import WireFormatError
from codex.repository.models import RepositoryMetadata

DEFAULT_INDEX_FILENAME: Final = "index.scip"

_DEFINITION_ROLE: Final = 0x1
_IMPORT_ROLE: Final = 0x2
_READ_ACCESS_ROLE: Final = 0x8

_OVERLOAD_FAMILY_LINE_WINDOW: Final = 25
"""GAP-13 fix: the maximum line gap between consecutive same-symbol
occurrences still treated as one textual "redefinition family" (the
`@typing.overload` idiom, and the structurally identical `@property`/
`@x.setter` pair) rather than an unrelated later reference. Real-data
justification (`docs/python-fidelity-gap-register.md`, 5 repositories):
every genuine redefinition family measured spans at most ~13 lines
between its own occurrences (property getter/setter pairs, `@overload`
stub blocks); unrelated call/reference sites to the same name measured
in the same real data land far outside this range (368 lines, in one
checked case) -- 25 is a deliberately generous but still discriminating
buffer, not a tight fit to any single observed case. A family whose real
span exceeds this window is not recovered by this fix (falls back to
today's Definition-role-only behavior) rather than risking an incorrect
guess -- this codebase's existing "never fabricate, prefer under- to
over-recovery" discipline (GAP-9/GAP-10's own precedent)."""

_EXTERNAL_REVISION_SENTINEL: Final = "external"
"""Fixed revision component for EXTERNAL_LIBRARY canonical IDs (see module
docstring) so a library's identity is independent of the consuming repo's
own revision -- a documented interpretation, not a change to
`build_canonical_id` itself (still closed, Phase 1)."""


@dataclass(frozen=True)
class _DefinitionRecord:
    symbol: str
    kind: int
    range: ScipRange | None
    relative_path: str
    is_redefinition_family: bool = False
    """GAP-13 fix: True when `range` was recovered from the *last*
    occurrence in a same-symbol textual-redefinition cluster (see
    `_OVERLOAD_FAMILY_LINE_WINDOW`), not directly from the symbol's own
    Definition-role Occurrence. Lets `normalize()` tag the resulting
    entity's `roles` so this provenance stays auditable."""
    nested_qualifier: str | None = None
    """FND-1 fix: the enclosing scope's own descriptor path, used by
    `normalize()` to enrich this record's identity so each real entity
    gets its own canonical_id instead of collapsing. Only meaningful
    together with `is_nested_disambiguation_representative`; may itself
    be `None` even for such a representative (a genuinely top-level
    class member disambiguated from its nested, same-named siblings
    uses its own plain descriptor, not a `<locals>` qualifier -- see
    `_NestedIdentity`'s own docstring)."""
    is_nested_disambiguation_representative: bool = False
    """FND-1 fix: True for a symbol confirmed to have more than one
    real Definition-role Occurrence with genuinely different nearest-
    enclosing scopes (see `_nested_symbol_disambiguation`) -- i.e. the
    SCIP descriptor is itself ambiguous between two or more distinct
    real Python entities (e.g. two same-named nested closures in
    sibling methods, or one nested closure colliding with an unrelated
    top-level sibling of the same name). Tells `normalize()` to resolve
    this record directly (with `nested_qualifier` applied, even when it
    is `None`) rather than through the shared closure that otherwise
    deliberately skips every symbol in this ambiguous state for
    references/relationships, which carry no position to disambiguate
    with."""


@dataclass(frozen=True)
class _ReferenceRecord:
    symbol: str
    relative_path: str
    is_import: bool


@dataclass(frozen=True)
class _RelationshipRecord:
    subject_symbol: str
    object_symbol: str


def _range_sort_key(range_: ScipRange | None) -> tuple[int, int, int, int]:
    if range_ is None:
        return (-1, -1, -1, -1)
    return (range_.start_line, range_.start_character, range_.end_line, range_.end_character)


def _build_kind_by_symbol(index: ScipIndex) -> dict[str, int]:
    kinds: dict[str, int] = {}
    for doc in index.documents:
        for info in doc.symbols:
            kinds.setdefault(info.symbol, info.kind)
    return kinds


def _redefinition_family_locations(
    index: ScipIndex, *, ambiguous_symbols: frozenset[tuple[str, str]] = frozenset()
) -> dict[tuple[str, str], ScipRange]:
    """GAP-13 fix (anchor corrected by GAP-14): for each ``(document,
    symbol)`` with more than one ``SymbolInformation`` entry recorded
    within that same document -- the real, verified signal for "this
    name has multiple textual definitions in this file" (a
    ``@typing.overload`` family, or the structurally identical
    ``@property``/``@x.setter`` pair; confirmed against real data this
    signal does *not* fire for an ordinarily single-defined, merely-
    frequently-referenced symbol -- see
    ``docs/python-fidelity-gap-register.md``) -- find the last
    occurrence in a textually-adjacent cluster (within
    ``_OVERLOAD_FAMILY_LINE_WINDOW`` lines of its predecessor).

    GAP-14 fix: the chain now starts at the *earliest Definition-role*
    occurrence, not unconditionally at the earliest occurrence of any
    role. scip-python emits exactly one Definition-role Occurrence per
    redefined symbol name (on the first textual definition), but a
    perfectly legal, earlier same-file `ReadAccess` reference to the
    same name (e.g. a sibling method calling ``self.foo(...)`` before
    ``foo`` is itself textually defined lower in the same class -- real
    data: requests' ``Response.iter_content``, referenced at line 859
    from inside ``iter_lines``, defined at line 907; django's
    ``Field.choices``, referenced 5 times from lines 261-368 before its
    own ``@property``/``@choices.setter`` pair at 584/588) can sort
    *before* that Definition-role occurrence. Anchoring on
    ``ordered[0]`` unconditionally (the original GAP-13 fix) then
    starts the window walk at that unrelated reference; the very next
    gap (reference -> real first definition) is almost always far
    larger than the window, so the walk breaks immediately and the true
    family -- however tightly clustered -- is never reached, leaving
    SCIP anchored on the old, pre-GAP-13 lone-Definition-occurrence
    location (the first overload stub) instead of the recovered last
    member. Confirmed via real-data audit: 53 such splits across
    django/click/pytest/requests, all sharing this exact mechanism (see
    GAP-14 in the gap register).

    Starting from the earliest Definition-role occurrence instead is a
    strict refinement, not a behavior change, for every case that
    already worked: whenever ``ordered[0]`` already *was* the
    Definition-role occurrence (no earlier reference exists -- true for
    every one of GAP-13's own validated cases), the new anchor is
    identical to the old one and the forward walk proceeds exactly as
    before. Earlier references are simply never visited by the walk,
    since it starts strictly after them in the sorted list -- "ignore
    unrelated earlier references" falls out of the anchor choice alone,
    with no separate exclusion logic needed.

    This mirrors ``AstCallsAdapter``'s own, already-existing,
    unconditional "last textual definition wins" convention (its
    ``_DefinitionCollector`` overwrites same-named dict entries in
    source order, with no decorator awareness at all) -- the two
    providers then naturally converge on the same ``source_location``
    through ``entity_resolver.py``'s existing, untouched exact-line
    identity key, rather than this fix loosening that key itself or
    touching AST-side logic.

    Never applied to a Parameter descriptor (trailing ``)``) -- those
    never become entities regardless (``infer_base_type``'s own
    ``_SKIP_SUFFIXES``), so their own, much noisier, per-reference
    ``SymbolInformation`` repetition (a parameter name used many times
    in its own function body) is irrelevant here and excluded before
    it can affect anything.
    """
    result: dict[tuple[str, str], ScipRange] = {}
    for doc in index.documents:
        symbol_info_counts: dict[str, int] = {}
        for info in doc.symbols:
            symbol_info_counts[info.symbol] = symbol_info_counts.get(info.symbol, 0) + 1

        occurrences_by_symbol: dict[str, list[ScipOccurrence]] = {}
        for occ in doc.occurrences:
            if not occ.symbol or is_local_symbol(occ.symbol) or occ.symbol.endswith(")"):
                continue
            if occ.symbol_roles & (_DEFINITION_ROLE | _READ_ACCESS_ROLE):
                occurrences_by_symbol.setdefault(occ.symbol, []).append(occ)

        for symbol, occs in occurrences_by_symbol.items():
            if symbol_info_counts.get(symbol, 0) <= 1:
                continue
            if (doc.relative_path, symbol) in ambiguous_symbols:
                # FND-1 fix: this symbol has multiple *real* Definition-role
                # occurrences with genuinely different enclosing scopes --
                # not one redefined symbol, but several distinct real
                # entities sharing one descriptor. Recovering a single
                # "family" location for it would silently pick one and
                # discard the others; leave it entirely to
                # `_nested_symbol_disambiguation` instead.
                continue
            if not any(occ.symbol_roles & _DEFINITION_ROLE for occ in occs):
                continue  # never cluster a group with no real Definition anchor
            ordered = sorted(
                (occ for occ in occs if occ.range is not None),
                key=lambda occ: _range_sort_key(occ.range),
            )
            if len(ordered) < 2:
                continue
            # GAP-14 fix: anchor on the earliest Definition-role occurrence,
            # not unconditionally on ordered[0] -- an earlier same-file
            # ReadAccess reference must never become the family anchor.
            anchor_index = next(
                (i for i, occ in enumerate(ordered) if occ.symbol_roles & _DEFINITION_ROLE),
                0,  # defensive fallback: preserves prior behavior if the
                # Definition-role occurrence itself lacked a range and was
                # filtered out of `ordered` above -- never observed in real
                # data (the guard just above already confirms one exists
                # in the unfiltered `occs`), kept only so this can never
                # raise on data this function hasn't seen before.
            )
            if anchor_index >= len(ordered) - 1:
                continue  # anchor is the last occurrence -- nothing to extend
            anchor = ordered[anchor_index]
            last = anchor.range
            assert last is not None
            for occ in ordered[anchor_index + 1 :]:
                assert occ.range is not None
                if occ.range.start_line - last.start_line <= _OVERLOAD_FAMILY_LINE_WINDOW:
                    last = occ.range
                else:
                    break
            if last is not anchor.range:
                result[(doc.relative_path, symbol)] = last
    return result


@dataclass(frozen=True)
class _NestedIdentity:
    qualifier_descriptor: str | None
    """The nearest enclosing function/method's own descriptor path --
    the additional identity dimension distinguishing this real entity
    from its same-descriptor siblings. ``None`` when this occurrence's
    own nearest enclosing real symbol is a *class* (a Namespace/Type
    descriptor, trailing ``#``) rather than a function/method -- i.e.
    this occurrence is itself a genuinely top-level class member, not
    actually nested inside anything, and merely happens to share its
    bare name with one or more real nested siblings elsewhere in the
    class (confirmed real: django's `Signal.asend` -- a genuine,
    ordinary top-level method -- shares its name with two unrelated
    closures nested inside `Signal.send` and `Signal.
    asend_and_wrap_exception` respectively). Using its own plain,
    unqualified descriptor already gives it an identity distinct from
    its `<locals>`-qualified siblings, without mislabeling a real
    top-level method as a nested one."""
    range: ScipRange
    """This entity's own representative source location (the last
    occurrence within its own enclosing scope, mirroring the same
    "last textual definition wins" convention `_redefinition_family_
    locations` already uses within a single scope)."""


def _read_line_indentations(repo_root: Path, relative_path: str) -> tuple[int, ...] | None:
    """The real leading-whitespace count for each line of ``relative_path``
    within ``repo_root``, indexed by 0-based line number -- the true,
    structural signal for Python nesting, read directly from the same
    source file `scip-python` indexed. A narrow, deliberate exception to
    this adapter's general "SCIP-index-only, never touch source" design,
    justified by a confirmed real-data false-negative: a bare SCIP
    occurrence *column* is not a reliable proxy for true indentation,
    because different Python keyword prefixes shift a definition's own
    identifier column by different amounts even at identical real nesting
    depth. Confirmed via django's `Signal` class: `send` is declared
    `def send(self, sender, **named):` (identifier column 8) while its
    ordinary top-level sibling `asend` is declared
    `async def asend(self, sender, **named):` (identifier column 14,
    six columns further right purely because of the extra `async `
    keyword) -- both are real top-level `Signal` methods at the *same*
    true nesting depth, but a raw-column comparison alone (8 < 14) reads
    `send` as `asend`'s "enclosing container." True source indentation
    has no such artifact: both lines have zero leading whitespace.

    Returns ``None`` when the source file cannot be read (missing,
    moved, permission error, not valid UTF-8 text) -- callers fall back
    to the less reliable raw-column comparison rather than fabricating
    an indentation value with no real signal behind it.
    """
    try:
        text = (repo_root / relative_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    indentations = []
    for line in text.splitlines():
        stripped = line.lstrip(" \t")
        indentations.append(len(line) - len(stripped))
    return tuple(indentations)


def _effective_indent(line: int, character: int, indentations: tuple[int, ...] | None) -> int:
    """The best available indentation signal for a source position: the
    real leading-whitespace count from ``indentations`` (see
    `_read_line_indentations`) when available for this line, else the
    SCIP occurrence's own identifier-token column as a fallback -- used
    only when the real source file isn't accessible (e.g. a handcrafted
    test fixture with no file on disk, or a `.scip` index whose source
    tree has since moved), matching this module's existing "never
    fabricate, degrade to the next-best deterministic signal" pattern.
    """
    if indentations is not None and 0 <= line < len(indentations):
        return indentations[line]
    return character


def _read_source_lines(repo_root: Path, relative_path: str) -> tuple[str, ...] | None:
    """The real source file's own lines (FND-2 fix), read once per
    document alongside `_read_line_indentations` -- used to verify that a
    candidate *container* occurrence genuinely opens a scope (`def `,
    `async def `, `class `) at its own position, including a redefined
    container's *later* textual instances, which real scip-python output
    tags `ReadAccess` rather than `Definition` (the same "only the first
    textual instance gets a Definition-role occurrence" pattern GAP-13
    established for functions, confirmed by this fix's own research to
    apply to classes too) and which are therefore invisible to a
    Definition-role-only container search. Returns ``None`` when the
    source file cannot be read, matching this module's existing
    degrade-rather-than-fabricate pattern -- callers fall back to
    Definition-role occurrences only, unable to verify a ReadAccess-role
    occurrence is really a scope-opening statement rather than an
    ordinary reference.
    """
    try:
        text = (repo_root / relative_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return tuple(text.splitlines())


_SCOPE_KEYWORD_RE = re.compile(r"(?:^|\s)(?:async\s+def|def|class)\s*$")


def _is_scope_opening_occurrence(lines: tuple[str, ...] | None, line: int, column: int) -> bool:
    """Whether the token starting at ``column`` on ``lines[line]`` is
    genuinely the declared name of a `def `/`async def `/`class `
    statement -- used to admit a ReadAccess-role occurrence of a
    function/class-typed symbol as a real container candidate (a later
    textual redefinition) while rejecting an ordinary reference to that
    same class/function name elsewhere on the *same* line (e.g. a type
    annotation like `def f(x: SomeClass):`, where `SomeClass`'s own
    occurrence sits on a line that itself starts with `def ` but is not
    itself the declared name -- checking only "does the *line* start
    with a scope keyword" would wrongly admit it; this checks that the
    text immediately *preceding this occurrence's own column* ends in
    the keyword, not merely that the line does somewhere). Returns
    ``False`` (never assume) when the source is unavailable, the line is
    out of range, or the column is out of bounds for that line."""
    if lines is None or not (0 <= line < len(lines)):
        return False
    text = lines[line]
    if not (0 <= column <= len(text)):
        return False
    return _SCOPE_KEYWORD_RE.search(text[:column]) is not None


@dataclass(frozen=True)
class _ScopeCandidate:
    """One real, position-identified candidate enclosing scope: any
    function/class-typed occurrence that genuinely opens a scope at its
    own position (FND-2 fix) -- a strict superset of the FND-1-era
    `containers` list, which only ever consumed Definition-role
    occurrences and therefore never saw a redefined container's own
    later textual instances (FND-2 failure mode (b))."""

    line: int
    column: int
    symbol: str
    indent: int


def _strip_module_prefix(descriptor_path: str) -> str:
    """Drop a SCIP descriptor path's leading backtick-quoted module
    segment (e.g. `` `pkg.a`/Outer#method_a(). `` -> ``Outer#method_a().``),
    matching the module boundary is always the *last* ``/`` in a local
    descriptor path (class/function chains use ``#``/``().``, never
    ``/``, confirmed against `scip.proto`). Used when chaining a second
    or later container into a `<locals>`-joined qualifier -- the module
    prefix must appear exactly once, at the very front of the whole
    chain (matching Python's own qualname convention), never repeated
    at each hop."""
    if "/" in descriptor_path:
        return descriptor_path.rsplit("/", maxsplit=1)[-1]
    return descriptor_path


def _build_scope_forest(
    doc: ScipDocument, indentations: tuple[int, ...] | None, lines: tuple[str, ...] | None
) -> tuple[
    list[_ScopeCandidate],
    list[tuple[int, int]],
    list[int],
    dict[int, str],
    dict[int, str | None],
]:
    """Build this document's full containment tree of real enclosing
    scopes, and resolve each one to a stable *identity* (FND-2 fix) --
    the direct architectural analogue of CodeQL's `getEnclosingScope()`
    (an edge to a specific parent scope *object*, never a re-matched
    name string) and RepoGraph's node-identity `contains` edges, per
    this fix's own research (`docs/resources.md`'s seventh finding).

    Two passes over every function/class-typed, non-local, non-Parameter
    occurrence in the document (Definition-role always; ReadAccess-role
    only when verified, via `_is_scope_opening_occurrence`, to be a genuine
    `def`/`class` statement -- FND-2's fix over FND-1's Definition-role-
    only `containers` list):

    1. **Containment**: sort all candidates by position; each candidate's
       *parent* is the nearest textually-preceding candidate with
       strictly smaller true indentation (unchanged from FND-1's own
       proxy for "whose body textually contains this position" -- still
       the deterministic, position-based signal this fix relies on).
       Since a scope's own `def`/`class` line always textually precedes
       everything nested inside it, a single forward pass over
       position-sorted candidates is sufficient: no candidate's parent
       can appear later in this same sorted list.

    2. **Identity resolution**: each candidate is resolved to a *family
       index* -- the index of the first candidate sharing both (a) the
       identical descriptor string and (b) the identical *resolved*
       parent family. Two real, differently-positioned redeclarations of
       one shared real scope (same descriptor, same real enclosing
       scope -- GAP-13/14's own "redefinition family" contract, now
       generalized recursively to any depth rather than only the
       top-level symbol being resolved) collapse to the SAME family
       index; two candidates that share a descriptor string but do NOT
       share a resolved parent (FND-2 failure mode (a) -- e.g. `index()`
       redefined once per differently-named test method) resolve to
       DIFFERENT family indices, even though their raw descriptor
       strings are identical. This is exactly what the old
       `_nearest_preceding_container` could not distinguish, since it
       returned the raw string itself.

    3. **Qualifier resolution**: for every family (at its own
       representative candidate), determine whether *it itself* needs a
       disambiguating qualifier -- i.e. whether its own descriptor
       string is shared by 2+ distinct families (an ambiguous container,
       FND-2 failure mode (a)/(b) recurring one level up: `index()` is
       itself exactly such a case, and so is `mocked()` in pytest's
       `TestPaste#mocked().DummyFile#read()`). When it is, that
       family's own upstream qualifier is computed *recursively* from
       its own resolved parent -- but only when the parent's own bare
       descriptor is not *already* textually embedded as a prefix of
       this family's own descriptor. Real scip-python output is
       inconsistent about this (confirmed empirically, not assumed: a
       nested closure's descriptor drops its own immediate non-method
       function's name in some cases -- `index()` never appears in
       `generate()`'s descriptor -- but keeps it in others -- `mocked()`
       *does* appear literally inside `DummyFile#read()`'s own raw
       descriptor, `TestPaste#mocked().DummyFile#read().`) -- rather
       than trying to predict which case applies (which would require
       reading scip-python's own source, against the clean-room policy),
       a direct string-prefix check on the real descriptor decides it:
       when the parent's name is already embedded, only the parent's
       *own* upstream qualifier is prepended (never re-adding the
       parent's own bare descriptor a second time); when it is not,
       the parent's full resolved identity (its own qualifier plus its
       own bare descriptor) is prepended. This single rule subsumes
       FND-1's original `is_class_qualifier` special case (a class
       container's own descriptor is *always* embedded as a literal
       prefix of a member's descriptor, by SCIP's own descriptor
       grammar) without needing to special-case it separately, and
       reduces to FND-1's exact original, already-tested qualifier text
       whenever no container in the chain is itself ambiguous.
    """
    candidates: list[_ScopeCandidate] = []
    for occ in doc.occurrences:
        if not occ.symbol or is_local_symbol(occ.symbol):
            continue
        if not (occ.symbol.endswith("().") or occ.symbol.endswith("#")):
            continue  # only function/class-typed symbols open a scope
        if occ.range is None:
            continue
        is_definition = bool(occ.symbol_roles & _DEFINITION_ROLE)
        if not is_definition and not _is_scope_opening_occurrence(
            lines, occ.range.start_line, occ.range.start_character
        ):
            continue
        indent = _effective_indent(occ.range.start_line, occ.range.start_character, indentations)
        candidates.append(
            _ScopeCandidate(occ.range.start_line, occ.range.start_character, occ.symbol, indent)
        )
    candidates.sort(key=lambda c: (c.line, c.column))
    positions = [(c.line, c.column) for c in candidates]

    parent_of: list[int | None] = [None] * len(candidates)
    for i, cand in enumerate(candidates):
        idx = bisect.bisect_left(positions, (cand.line, cand.column))
        for j in range(idx - 1, -1, -1):
            if candidates[j].indent < cand.indent:
                parent_of[i] = j
                break

    family_of: list[int] = [0] * len(candidates)
    seen: dict[tuple[str, int | None], int] = {}
    for i, cand in enumerate(candidates):
        parent_idx = parent_of[i]
        parent_family = family_of[parent_idx] if parent_idx is not None else None
        key = (cand.symbol, parent_family)
        rep = seen.get(key)
        if rep is None:
            rep = i
            seen[key] = i
        family_of[i] = rep

    symbol_families: dict[str, set[int]] = {}
    for i, cand in enumerate(candidates):
        symbol_families.setdefault(cand.symbol, set()).add(family_of[i])

    bare_descriptor: dict[int, str] = {}
    upstream_qualifier: dict[int, str | None] = {}
    for i, cand in enumerate(candidates):
        if family_of[i] != i:
            continue  # computed once, at each family's own representative
        parsed = parse_symbol(cand.symbol)
        descriptor_path = parsed.descriptor_path if parsed is not None else cand.symbol
        bare_descriptor[i] = descriptor_path
        if len(symbol_families[cand.symbol]) <= 1:
            upstream_qualifier[i] = None
            continue
        parent_idx = parent_of[i]
        if parent_idx is None:
            upstream_qualifier[i] = None
            continue
        parent_family = family_of[parent_idx]
        parent_bare = bare_descriptor[parent_family]
        # A strict prefix (not equality): a self-referential nested
        # closure sharing its exact descriptor with its own direct
        # parent (confirmed real: pytest's `TestExceptionInfoFormatter.
        # importasmod` fixture method defines a same-named closure
        # directly inside itself) must NOT take the "already embedded"
        # shortcut merely because the two descriptors happen to be
        # identical -- that would silently discard the very
        # disambiguation this family needs.
        if descriptor_path != parent_bare and descriptor_path.startswith(parent_bare):
            # the parent's own identity is already textually embedded in
            # this family's own descriptor -- never re-add it, only
            # prepend whatever qualifies the parent itself (if anything)
            upstream_qualifier[i] = upstream_qualifier[parent_family]
        else:
            parent_upstream = upstream_qualifier[parent_family]
            upstream_qualifier[i] = (
                f"{parent_upstream.rstrip('.')}.<locals>.{_strip_module_prefix(parent_bare)}"
                if parent_upstream is not None
                else parent_bare
            )

    return candidates, positions, family_of, bare_descriptor, upstream_qualifier


def _container_family_for(
    candidates: list[_ScopeCandidate],
    positions: list[tuple[int, int]],
    family_of: list[int],
    line: int,
    column: int,
    indent: int,
) -> int | None:
    """The resolved *family identity* (see `_build_scope_forest`) of the
    nearest real enclosing scope for a position, or ``None`` when no
    locatable enclosing scope exists (module-level, never fabricated).
    Replaces FND-1's `_nearest_preceding_container` (which returned a
    container's own descriptor *string*): returning the resolved family
    index instead is what lets two same-descriptor containers in
    genuinely different real scopes group separately (FND-2 fix).

    Deliberately does *not* exclude candidates sharing the target
    symbol's own descriptor (FND-1's original design did, via an
    ``own_symbol`` parameter): the *nearest*-preceding-with-smaller-
    indent search itself already finds a genuinely different, more
    specific container first whenever one truly intervenes (confirmed:
    e.g. django's `Signal.asend`, nested once in `Signal.send()` and
    again in `Signal.send_robust()`, always finds the immediately
    enclosing `send()`/`send_robust()` first -- both different
    descriptors -- long before the search could ever reach back to an
    earlier `asend()` occurrence). Excluding same-descriptor candidates
    unconditionally instead broke a real, confirmed case this fix's own
    validation found: pytest's `TestExceptionInfoFormatter.importasmod`
    fixture method, which defines a *nested closure of the identical
    name* directly inside itself (`def importasmod(self, ...): def
    importasmod(source): ...`) -- there the closure's own true immediate
    parent (the outer method) legitimately shares its exact descriptor,
    and excluding it by name alone skipped straight past the real
    container onto an unrelated outer class."""
    idx = bisect.bisect_left(positions, (line, column))
    for i in range(idx - 1, -1, -1):
        if candidates[i].indent < indent:
            return family_of[i]
    return None


def _nested_symbol_disambiguation(
    index: ScipIndex,
    repo_root: Path | None = None,
) -> dict[tuple[str, str, int, int], _NestedIdentity]:
    """FND-1/FND-2 fix: detect symbols whose SCIP descriptor is genuinely
    ambiguous -- shared by two or more distinct real Python entities
    because the descriptor grammar has no room to encode enclosing-
    *function* scope for a symbol nested inside a function/method body
    (a nested closure, a locally-defined class, etc: SCIP's descriptor
    grammar encodes an enclosing *class*, never an enclosing function --
    confirmed against `scip.proto`; see `codex.provider.scip.mapping`).

    The reliable signal, confirmed against real django/flask/click data
    (`docs/python-fidelity-gap-register.md`, FND-1): more than one
    *real Definition-role Occurrence* for the exact same descriptor
    string -- not merely more than one `SymbolInformation` entry (that
    alone is `_redefinition_family_locations`'s own signal: a
    `@typing.overload`/`@property` family, or a wire-format quirk
    emitting two Definition occurrences on the identical position for
    one real declaration, both of which get a single, shared nearest-
    enclosing scope here and are therefore *not* split). Two or more
    Definition-role occurrences with *different* nearest-enclosing
    scopes is the confirmed, specific signature of real distinct
    entities: e.g. two same-named nested closures in sibling methods
    (django's ``AbstractBaseUser.check_password``'s and
    ``.acheck_password``'s own, separate ``setter`` closures; django's
    ``Library#dec()``, five distinct closures nested in five different
    methods; click's ``Group#decorator()``, flask's ``App#decorator()``
    /``Blueprint#decorator()``/``Scaffold#decorator()``, three or four
    distinct closures each).

    **FND-2 fix (this cycle):** "nearest enclosing scope" is no longer
    identified by a container occurrence's own descriptor *string*
    (FND-1's original design) -- that string is not always a reliable
    proxy for "which real scope instance this is." Two confirmed real
    failure shapes (`docs/python-fidelity-gap-register.md`'s FND-2 row):
    a container itself genuinely nested in 2+ different real scopes
    serializes to an *identical* descriptor in every instance (flask's
    `index()`, redefined once per differently-named test method,
    dropping its own enclosing test-method segment exactly as Finding 5
    describes -- every nested child's lookup then returns the same
    string regardless of which real `index()` it is actually inside);
    and a container redefined *in place within one shared real scope*
    (the ordinary GAP-13/14 pattern, which already converges correctly
    for the container itself) carries a Definition-role SCIP occurrence
    on only its *first* textual instance, so a Definition-role-only
    container search skips straight past its later, un-marked
    re-declarations onto an unrelated sibling.

    The fix (`_build_scope_forest`, `_container_family_for` -- see their
    own docstrings, and `docs/resources.md`'s seventh finding for the
    CodeQL/RepoGraph precedent this follows): resolve every real
    enclosing-scope candidate to its own recursively-collapsed *family
    identity* first (same real scope, however many times redefined in
    place, collapses to one identity; genuinely different real scopes
    never collapse, even sharing one descriptor string), THEN group a
    nested symbol's occurrences by their container's *resolved identity*
    -- never by re-matching descriptor text. Indentation is still read
    from the real source file on disk when available (`repo_root`,
    `_read_line_indentations`, unchanged from FND-1) -- a raw SCIP
    occurrence *column* remains an unsafe proxy for it, since different
    keyword prefixes (`def `, `async def `, `class `) shift a
    definition's own identifier column by different amounts even at
    identical true nesting depth (Finding 6). When the real source file
    isn't available, this falls back to the raw column (`_effective_
    indent`) for indentation, and to Definition-role-only container
    candidates (unable to verify a ReadAccess-role occurrence is really
    a scope-opening statement without reading the source) -- the same
    degrade-rather-than-fabricate pattern as before. This is a
    structural fact about symbol nesting in the source (which real
    declared entity's body textually contains this occurrence), not a
    bare line number used as an identity -- a platform-conditional
    module-level redefinition (e.g. click's ``getchar``, defined once
    under ``if WIN:`` and once under ``else:``, both directly at module
    scope, same column) has *no* enclosing-scope difference between its
    two definitions and is correctly left alone (confirmed: `getchar`
    has exactly one real Definition-role occurrence in practice, so it
    never even reaches this check).

    Returns, for each distinct real entity found, a mapping keyed by
    that entity's own *representative* occurrence position to a
    `_NestedIdentity` -- deliberately keyed by position (not merely by
    symbol) so `_collect_definitions` can distinguish "this exact
    occurrence is the one representing its scope" from "this occurrence
    is a redundant redefinition within the same scope, already
    represented." Occurrences whose nearest enclosing scope cannot be
    determined (no preceding real symbol in the document -- e.g. a
    module-level ambiguous definition with no enclosing function) are
    never guessed at from a bare position; they are simply excluded
    (never fabricate an identity this function has no real signal for).
    """
    result: dict[tuple[str, str, int, int], _NestedIdentity] = {}
    for doc in index.documents:
        indentations = (
            _read_line_indentations(repo_root, doc.relative_path) if repo_root is not None else None
        )
        lines = _read_source_lines(repo_root, doc.relative_path) if repo_root is not None else None
        candidates, positions, family_of, bare_descriptor, upstream_qualifier = _build_scope_forest(
            doc, indentations, lines
        )

        symbol_info_counts: dict[str, int] = {}
        for info in doc.symbols:
            symbol_info_counts[info.symbol] = symbol_info_counts.get(info.symbol, 0) + 1

        def_occs_by_symbol: dict[str, list[ScipOccurrence]] = {}
        for occ in doc.occurrences:
            if not (occ.symbol_roles & _DEFINITION_ROLE):
                continue
            if not occ.symbol or is_local_symbol(occ.symbol) or occ.symbol.endswith(")"):
                continue
            def_occs_by_symbol.setdefault(occ.symbol, []).append(occ)

        for symbol, occs in def_occs_by_symbol.items():
            if symbol_info_counts.get(symbol, 0) <= 1 or len(occs) <= 1:
                continue
            ordered = sorted(
                (o for o in occs if o.range is not None), key=lambda o: _range_sort_key(o.range)
            )
            if len(ordered) < 2:
                continue

            groups: dict[int | None, list[ScipOccurrence]] = {}
            for occ in ordered:
                assert occ.range is not None
                occ_indent = _effective_indent(
                    occ.range.start_line, occ.range.start_character, indentations
                )
                family = _container_family_for(
                    candidates,
                    positions,
                    family_of,
                    occ.range.start_line,
                    occ.range.start_character,
                    occ_indent,
                )
                groups.setdefault(family, []).append(occ)

            distinct_real_scopes = {f for f in groups if f is not None}
            if len(distinct_real_scopes) <= 1:
                continue  # not a genuine cross-scope collision -- leave to
                # `_redefinition_family_locations`'s own, unrelated signal

            parsed_target = parse_symbol(symbol)
            if parsed_target is None:
                continue
            target_descriptor = parsed_target.descriptor_path

            for family, group_occs in groups.items():
                if family is None:
                    continue  # no locatable enclosing scope -- never fabricate one
                container_bare = bare_descriptor[family]
                # When the container's own identity is already textually
                # embedded as a prefix of this symbol's own raw descriptor
                # (always true for a class container, by SCIP's own
                # descriptor grammar; also true for some, but not all,
                # function containers -- confirmed empirically, not
                # assumed, see `_build_scope_forest`'s own docstring) it
                # must never be re-added -- only whatever qualifies the
                # container *itself* (if it is itself ambiguous) is
                # prepended. Otherwise the container's own full resolved
                # identity (its own qualifier plus its own bare
                # descriptor) becomes this symbol's qualifier -- FND-1's
                # original, already-tested behavior when the container
                # itself needs no further disambiguation. A strict
                # prefix, not equality: a symbol sharing its *exact*
                # descriptor with its own resolved container (the
                # self-referential nested-closure case, see
                # `_build_scope_forest`'s own docstring) must still be
                # qualified against that container's own bare
                # descriptor, never treated as already-redundant.
                if target_descriptor != container_bare and target_descriptor.startswith(
                    container_bare
                ):
                    qualifier_descriptor = upstream_qualifier[family]
                else:
                    container_upstream = upstream_qualifier[family]
                    qualifier_descriptor = (
                        f"{container_upstream.rstrip('.')}.<locals>."
                        f"{_strip_module_prefix(container_bare)}"
                        if container_upstream is not None
                        else container_bare
                    )
                representative = max(group_occs, key=lambda o: _range_sort_key(o.range))
                assert representative.range is not None
                key = (
                    doc.relative_path,
                    symbol,
                    representative.range.start_line,
                    representative.range.start_character,
                )
                identity = _NestedIdentity(qualifier_descriptor, representative.range)
                result[key] = identity
    return result


def _collect_definitions(
    index: ScipIndex, repo_root: Path | None = None
) -> list[_DefinitionRecord]:
    kind_by_symbol = _build_kind_by_symbol(index)
    nested_identities = _nested_symbol_disambiguation(index, repo_root)
    ambiguous_symbols = frozenset((path, symbol) for path, symbol, _, _ in nested_identities)
    family_locations = _redefinition_family_locations(index, ambiguous_symbols=ambiguous_symbols)
    records = []
    for doc in index.documents:
        for occ in doc.occurrences:
            is_definition = occ.symbol_roles & _DEFINITION_ROLE
            if not (is_definition and occ.symbol and not is_local_symbol(occ.symbol)):
                continue
            if (doc.relative_path, occ.symbol) in ambiguous_symbols:
                # FND-1 fix: this descriptor represents 2+ distinct real
                # entities. Only the *representative* occurrence of each
                # distinct enclosing scope becomes a record -- every other
                # occurrence within that same scope is a redundant
                # redefinition of the same real entity, already covered by
                # its group's own representative (never emit duplicates).
                assert occ.range is not None
                identity = nested_identities.get(
                    (doc.relative_path, occ.symbol, occ.range.start_line, occ.range.start_character)
                )
                if identity is None:
                    continue
                records.append(
                    _DefinitionRecord(
                        occ.symbol,
                        kind_by_symbol.get(occ.symbol, 0),
                        identity.range,
                        doc.relative_path,
                        nested_qualifier=identity.qualifier_descriptor,
                        is_nested_disambiguation_representative=True,
                    )
                )
                continue
            family_range = family_locations.get((doc.relative_path, occ.symbol))
            records.append(
                _DefinitionRecord(
                    occ.symbol,
                    kind_by_symbol.get(occ.symbol, 0),
                    family_range if family_range is not None else occ.range,
                    doc.relative_path,
                    is_redefinition_family=family_range is not None,
                )
            )
    records.sort(key=lambda r: (r.relative_path, _range_sort_key(r.range), r.symbol))
    return records


def _collect_references(index: ScipIndex) -> list[_ReferenceRecord]:
    seen: set[tuple[str, str, bool]] = set()
    records: list[_ReferenceRecord] = []
    for doc in index.documents:
        for occ in doc.occurrences:
            if occ.symbol_roles & _DEFINITION_ROLE:
                continue
            if not occ.symbol or is_local_symbol(occ.symbol):
                continue
            is_import = bool(occ.symbol_roles & _IMPORT_ROLE)
            key = (doc.relative_path, occ.symbol, is_import)
            if key in seen:
                continue
            seen.add(key)
            records.append(_ReferenceRecord(occ.symbol, doc.relative_path, is_import))
    records.sort(key=lambda r: (r.relative_path, r.symbol, r.is_import))
    return records


def _collect_relationship_facts(
    index: ScipIndex, *, want_implementation: bool
) -> list[_RelationshipRecord]:
    seen: set[tuple[str, str]] = set()
    records: list[_RelationshipRecord] = []
    for doc in index.documents:
        for info in doc.symbols:
            if is_local_symbol(info.symbol):
                continue
            for rel in info.relationships:
                flag = rel.is_implementation if want_implementation else rel.is_type_definition
                if not flag or is_local_symbol(rel.symbol):
                    continue
                key = (info.symbol, rel.symbol)
                if key in seen:
                    continue
                seen.add(key)
                records.append(_RelationshipRecord(info.symbol, rel.symbol))
    records.sort(key=lambda r: (r.subject_symbol, r.object_symbol))
    return records


def _location_from_range(range_: ScipRange | None, relative_path: str) -> SourceLocation | None:
    if range_ is None:
        return None
    return SourceLocation(
        file_path=relative_path,
        start_line=range_.start_line,
        end_line=range_.end_line,
        start_column=range_.start_character,
        end_column=range_.end_character,
    )


def _file_path_from_descriptor(descriptor_path: str) -> str:
    """Recover a plain repo-relative path from a SCIP file-descriptor's
    descriptor path (e.g. ``src/`greeter.ts`/`` -> ``src/greeter.ts``).

    SCIP backtick-quotes any descriptor segment containing characters its
    grammar treats as special (confirmed against real output: a segment
    like ``greeter.ts`` -- which contains a literal ``.`` -- is emitted as
    `` `greeter.ts` ``, while a plain segment like ``src`` is not). This
    strips that escaping and the trailing ``/`` so a file's identity here
    matches the plain ``Document.relative_path`` used everywhere else
    (`GitAdapter`'s FILE entities included -- directive D5 §9's identity-
    convergence requirement). A literal backtick in a real file name would
    defeat this simple strip; that's an accepted, documented limitation
    rather than implementing SCIP's full descriptor grammar just for this.
    """
    return descriptor_path.removesuffix("/").replace("`", "")


def _dotted_prefix_file_candidates(dotted: str) -> tuple[str, str]:
    """The two real-file shapes a dotted module path could correspond to
    on disk: a flat module (``pkg/mod.py``) or a package (``pkg/mod/
    __init__.py``)."""
    slash_form = dotted.replace(".", "/")
    return f"{slash_form}.py", f"{slash_form}/__init__.py"


def _is_indexed_project_file(
    descriptor_path: str, indexed_relative_paths: frozenset[str]
) -> bool:
    """GAP-10 fix: True only when a backtick-quoted dotted-module
    descriptor prefix corresponds to a real file *this exact SCIP index
    itself indexed* as part of the target repository's own source tree.

    Investigation finding (real data, django/flask/click/pytest):
    ``parsed.package_version == revision`` alone is NOT a safe local/
    external discriminator, contrary to this fix's first draft. scip-python
    falls back to attributing an *unresolved* import -- stdlib
    (``unittest.case``, ``json.encoder``, ``_ast``), third-party
    (``zope.interface``, ``werkzeug.exceptions``, ``PIL.ImageEnhance``,
    ``psycopg.pq``), even a project's own external dependency
    (``click.core`` inside flask's own index) -- to the *local* project's
    package name and this exact ingestion ``revision``, indistinguishably
    from a genuine same-repository phantom. Confirmed: 18-95 such
    indexer-fallback false positives per repository if `package_version
    == revision` were trusted alone (pytest 18, flask 43, click 16,
    django 95 -- out of the "backtick-quoted, version-matches" candidate
    set in each). Backtick-quoting itself doesn't discriminate either --
    it's used for *any* dotted qualified name, local or not.

    The one signal that does hold with zero counterexamples across all
    four repositories: convert the descriptor's own dotted prefix to its
    two possible file-system shapes and check whether *this same parsed
    index* actually has a ``Document`` at that path. A real local phantom
    (e.g. flask's `` `tests.test_views`/Index# ``) always does -- its
    ``tests/test_views.py`` is one of the files scip-python parsed. An
    indexer-fallback artifact for an unresolved import never does, because
    no such file exists in the project scip-python was pointed at.
    """
    if not descriptor_path.startswith("`"):
        return False
    end = descriptor_path.find("`", 1)
    if end == -1:
        return False
    dotted = descriptor_path[1:end]
    if not dotted:
        return False
    flat, package = _dotted_prefix_file_candidates(dotted)
    return flat in indexed_relative_paths or package in indexed_relative_paths


@dataclass(frozen=True)
class _ResolvedSymbol:
    canonical_id: str
    base_type: BaseEntityType
    qualified_name: str
    revision: str
    inferred_from_relationship_only: bool = False
    """GAP-10 fix: True only for a symbol recovered by
    ``_resolve_symbol``'s same-repository-phantom branch -- never backed
    by any ``Occurrence`` (not even a non-Definition reference), only
    named as the *object* of another symbol's ``is_implementation``/
    ``is_type_definition`` relationship. Lets ``normalize()`` tag the
    resulting entity's ``roles`` so this weaker provenance (inferred from
    one relationship fact, never independently observed) stays auditable
    rather than silently indistinguishable from a genuinely-observed
    local symbol."""


def _resolve_symbol(
    symbol: str,
    *,
    repository_id: str,
    revision: str,
    locally_defined: frozenset[str],
    kind_by_symbol: dict[str, int],
    indexed_relative_paths: frozenset[str],
    nested_qualifier: str | None = None,
) -> _ResolvedSymbol | None:
    """Resolve a SCIP symbol string to a canonical Codex identity.

    Returns ``None`` for a local-scope symbol or one whose header this
    adapter can't parse — callers must skip rather than fabricate an
    identity (directive D5 §9, §11). Uses the *same* ``kind_by_symbol``
    lookup as ``_collect_definitions`` so a locally defined symbol always
    resolves to the same base type -- and therefore the same
    ``canonical_id`` -- whether it's reached via SYMBOL_DEFINITION,
    SYMBOL_REFERENCE, IMPLEMENTATION, or TYPE_RELATIONSHIP.

    ``nested_qualifier`` (FND-1 fix): when given, it is the enclosing
    function/method's own descriptor path, as found by
    ``_nested_symbol_disambiguation`` -- folded into ``qualified_name``
    (Python's own ``<locals>`` convention for nested-scope qualnames)
    *before* computing ``canonical_id``, so this call produces a
    genuinely distinct identity from the same call without a qualifier.
    Only ever passed for a symbol already confirmed to have multiple
    real, differently-scoped Definition-role Occurrences -- for every
    other symbol this parameter is never supplied and resolution is
    byte-identical to before this fix.
    """
    if is_local_symbol(symbol):
        return None
    parsed = parse_symbol(symbol)
    if parsed is None:
        return None

    if symbol in locally_defined:
        base_type = infer_base_type(kind=kind_by_symbol.get(symbol, 0), symbol=symbol)
        if base_type is None:
            return None
        qualified_name = (
            _file_path_from_descriptor(parsed.descriptor_path)
            if base_type is BaseEntityType.FILE
            else parsed.descriptor_path
        )
        if nested_qualifier is not None:
            bare_tail = qualified_name.rsplit("/", maxsplit=1)[-1]
            qualified_name = f"{nested_qualifier.rstrip('.')}.<locals>.{bare_tail}"
        canonical_id = build_canonical_id(
            repository_id=repository_id,
            repository_revision=revision,
            qualified_name=qualified_name,
            base_type=base_type,
        )
        return _ResolvedSymbol(canonical_id, base_type, qualified_name, revision)

    # GAP-10 fix ("relationship object with no Definition occurrence and
    # no SymbolInformation" investigation): a symbol can be named only as
    # the *object* of another symbol's `is_implementation`/
    # `is_type_definition` relationship, or reached only via a non-
    # Definition Reference, with zero Occurrences of Definition role and
    # zero SymbolInformation entries anywhere in the index -- confirmed
    # real and reproducible against django/flask/pytest/click (0 on
    # requests, its own narrower-scope index).
    #
    # First-draft signal (`parsed.package_version == revision` alone) was
    # proven UNSAFE by real-data investigation: scip-python attributes an
    # *unresolved* import -- stdlib, third-party, even a project's own
    # external dependency -- to the local project's package name and this
    # exact ingestion `revision` whenever it can't resolve the import's
    # true origin (18-95 such false positives observed per repository).
    # `_is_indexed_project_file` closes that gap: it additionally requires
    # the descriptor's own dotted-module prefix to correspond to a real
    # file *this same SCIP index actually indexed* (its `Document` list) --
    # a signal with zero counterexamples across django/flask/pytest/click.
    # Recovering the real identity (rather than collapsing it onto the
    # same shared package-level EXTERNAL_LIBRARY node every other
    # same-repo phantom symbol in this run would otherwise share) reuses
    # `infer_base_type`'s existing kind-unspecified descriptor-suffix
    # fallback verbatim -- the identical mechanism already trusted for a
    # locally-defined symbol with no `kind` info (GAP-9's own fix).
    if parsed.package_version == revision and _is_indexed_project_file(
        parsed.descriptor_path, indexed_relative_paths
    ):
        base_type = infer_base_type(kind=kind_by_symbol.get(symbol, 0), symbol=symbol)
        if base_type is not None:
            qualified_name = (
                _file_path_from_descriptor(parsed.descriptor_path)
                if base_type is BaseEntityType.FILE
                else parsed.descriptor_path
            )
            canonical_id = build_canonical_id(
                repository_id=repository_id,
                repository_revision=revision,
                qualified_name=qualified_name,
                base_type=base_type,
            )
            return _ResolvedSymbol(
                canonical_id,
                base_type,
                qualified_name,
                revision,
                inferred_from_relationship_only=True,
            )
        # Unclassifiable descriptor shape (e.g. a bare Parameter) -- fall
        # through to the external-library branch below, exactly like any
        # other symbol this adapter can't confidently classify; never
        # fabricate a base_type it has no real signal for.

    # Not defined anywhere in this index -> external library (directive D5 §10).
    qualified_name = f"{parsed.manager}:{parsed.package_name}@{parsed.package_version}"
    canonical_id = build_canonical_id(
        repository_id=repository_id,
        repository_revision=_EXTERNAL_REVISION_SENTINEL,
        qualified_name=qualified_name,
        base_type=BaseEntityType.EXTERNAL_LIBRARY,
    )
    return _ResolvedSymbol(
        canonical_id, BaseEntityType.EXTERNAL_LIBRARY, qualified_name, _EXTERNAL_REVISION_SENTINEL
    )


def _make_evidence(
    evidence_id: str, *, cohort: EvidenceCohort, subject: str, predicate: RelationshipType, obj: str
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        provider=cohort.provider,
        provider_version=cohort.provider_version,
        snapshot_id=cohort.snapshot_id,
        source_revision=cohort.source_revision,
        subject=subject,
        predicate=predicate,
        object=obj,
        confidence=1.0,
        freshness=cohort.observed_at,
    )


class SCIPAdapter:
    """``ProviderAdapter`` for SCIP indexes (HLRD Resource Map; directive D5)."""

    def __init__(self, *, index_filename: str = DEFAULT_INDEX_FILENAME) -> None:
        self._index_filename = index_filename
        self._freshness: datetime | None = None
        self._last_tool_version: str | None = None

    @property
    def provider_name(self) -> str:
        return "scip"

    @property
    def provider_version(self) -> str:
        """The producing indexer's own name+version (e.g. ``scip-typescript@0.4.0``),
        read from the *last successfully decoded* index's metadata. Before any
        extraction, or if that metadata is empty, reports ``"unknown"`` --
        this adapter has no version of its own independent of whatever
        indexer actually produced the artifact (mirrors `GitAdapter`'s own
        documented "unknown" fallback, D3)."""
        return self._last_tool_version or "unknown"

    @property
    def supported_capabilities(self) -> frozenset[Capability]:
        return frozenset(
            {
                Capability.SYMBOL_DEFINITION,
                Capability.SYMBOL_REFERENCE,
                Capability.IMPLEMENTATION,
                Capability.TYPE_RELATIONSHIP,
            }
        )

    @property
    def health_status(self) -> ProviderHealthStatus:
        # No external executable/service dependency -- this adapter is pure
        # Python reading a file the caller points it at (directive D5 §15).
        return ProviderHealthStatus.HEALTHY

    def availability(self, capability: Capability, repository: RepositoryMetadata) -> float:
        if capability not in self.supported_capabilities:
            return 0.0
        return 1.0 if self.check_eligibility(repository).eligible else 0.0

    @property
    def freshness(self) -> datetime | None:
        return self._freshness

    def validate(self) -> ValidationResult:
        return ValidationResult(ok=True)

    def _index_path(self, repository: RepositoryMetadata) -> Path:
        return Path(repository.local_path) / self._index_filename

    def check_eligibility(self, repository: RepositoryMetadata) -> ProviderEligibility:
        path = self._index_path(repository)
        if not path.is_file():
            return ProviderEligibility(
                status=EligibilityStatus.INELIGIBLE_REPOSITORY,
                reason=f"no SCIP index found at {path}",
            )
        return ProviderEligibility(status=EligibilityStatus.ELIGIBLE)

    def extract(
        self, repository: RepositoryMetadata, capabilities: Collection[Capability]
    ) -> ExtractionResult:
        requested = frozenset(capabilities) & self.supported_capabilities
        path = self._index_path(repository)

        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ProviderExtractionError(
                self.provider_name, ProviderFailureReason.UNAVAILABLE, f"cannot read {path}: {exc}"
            ) from exc

        try:
            index = decode_index(data)
        except WireFormatError as exc:
            raise ProviderExtractionError(
                self.provider_name,
                ProviderFailureReason.UNAVAILABLE,
                f"malformed SCIP index at {path}: {exc}",
            ) from exc

        tool_info = index.metadata.tool_info
        self._last_tool_version = (
            f"{tool_info.name}@{tool_info.version}" if tool_info.name else None
        )

        successful: list[str] = []
        failed: list[str] = []
        definitions: list[_DefinitionRecord] | None = None
        references: list[_ReferenceRecord] | None = None
        implementations: list[_RelationshipRecord] | None = None
        type_relationships: list[_RelationshipRecord] | None = None

        if Capability.SYMBOL_DEFINITION in requested:
            try:
                definitions = _collect_definitions(index, repository.local_path)
                successful.append(Capability.SYMBOL_DEFINITION.value)
            except Exception:  # noqa: BLE001 - isolate this capability, directive D5 §14
                failed.append(Capability.SYMBOL_DEFINITION.value)

        if Capability.SYMBOL_REFERENCE in requested:
            try:
                references = _collect_references(index)
                successful.append(Capability.SYMBOL_REFERENCE.value)
            except Exception:  # noqa: BLE001
                failed.append(Capability.SYMBOL_REFERENCE.value)

        if Capability.IMPLEMENTATION in requested:
            try:
                implementations = _collect_relationship_facts(index, want_implementation=True)
                successful.append(Capability.IMPLEMENTATION.value)
            except Exception:  # noqa: BLE001
                failed.append(Capability.IMPLEMENTATION.value)

        if Capability.TYPE_RELATIONSHIP in requested:
            try:
                type_relationships = _collect_relationship_facts(index, want_implementation=False)
                successful.append(Capability.TYPE_RELATIONSHIP.value)
            except Exception:  # noqa: BLE001
                failed.append(Capability.TYPE_RELATIONSHIP.value)

        if not successful and not failed:
            coverage = CoverageStatus.NONE
        elif failed:
            coverage = CoverageStatus.PARTIAL
        else:
            coverage = CoverageStatus.FULL

        cohort = EvidenceCohort(
            provider=self.provider_name,
            provider_version=self.provider_version,
            snapshot_id=repository.head_revision,
            source_revision=repository.head_revision,
            successful_capabilities=successful,
            failed_capabilities=failed,
            partial_capabilities=[],
            coverage_status=coverage,
        )
        self._freshness = cohort.observed_at

        payload = {
            "repository_id": repository.repository_id,
            "revision": repository.head_revision,
            # GAP-9 fix ("candidate-generation completeness" investigation):
            # `Document.symbols` (SymbolInformation) alone is not a reliable
            # signal of "this symbol is defined in this repository" -- a real
            # producer (scip-python/pyright) can emit a genuine
            # `Definition`-role Occurrence for a symbol (a large, heavily-
            # typed top-level class, confirmed against real requests/flask/
            # pytest/click/django indexes) while omitting that same symbol's
            # SymbolInformation entry entirely, even though every one of its
            # own members gets one. Relying on `doc.symbols` alone silently
            # routed such a symbol through `_resolve_symbol`'s "not defined
            # anywhere in this index -> external library" branch, which (a)
            # discarded its real identity and (b) collapsed it onto the same
            # canonical_id as every other repository-owned symbol hitting
            # this same gap (that branch's qualified_name is a pure function
            # of repository+revision, never of the symbol's own descriptor
            # path) -- up to 46 distinct real symbols conflated onto one
            # node in a real `requests` index. `_collect_definitions()`
            # (just above) already derives the correct, complete set of
            # locally-defined symbols directly from `Definition`-role
            # Occurrences -- the same data `_resolve_symbol` needs, just
            # never previously fed into `locally_defined`. Unioning it in
            # here fixes the root cause without touching `_resolve_symbol`,
            # `infer_base_type` (its existing kind-unspecified fallback
            # already classifies these symbols correctly once they reach
            # it -- confirmed against real data), `_collect_definitions`
            # itself, or canonical-id computation.
            "locally_defined": frozenset(
                info.symbol for doc in index.documents for info in doc.symbols
            )
            | frozenset(record.symbol for record in (definitions or ())),
            "kind_by_symbol": _build_kind_by_symbol(index),
            # GAP-10 fix: the set of files this exact SCIP index itself
            # indexed as part of the target repository's own source tree --
            # ground truth for `_is_indexed_project_file`'s cross-reference,
            # independent of (and safer than) the indexer's own package
            # attribution for a symbol it failed to resolve.
            "indexed_relative_paths": frozenset(doc.relative_path for doc in index.documents),
            "definitions": definitions,
            "references": references,
            "implementations": implementations,
            "type_relationships": type_relationships,
            # FND-1 fix: symbol strings confirmed to represent 2+ distinct
            # real entities (see `_nested_symbol_disambiguation`). The
            # shared `resolve()` below -- used for references and
            # relationship endpoints, neither of which carries enough
            # position information to say *which* of the real entities is
            # meant (references are already a document-level aggregate;
            # relationship facts carry no location at all in the wire
            # format) -- skips these symbols entirely rather than
            # guessing. Definitions are unaffected: `_collect_definitions`
            # already gives each real entity its own `nested_qualifier`,
            # resolved directly (not through this shared closure) so each
            # gets its own correct, distinct identity.
            "ambiguous_symbols": (
                frozenset(
                    d.symbol for d in definitions if d.is_nested_disambiguation_representative
                )
                if definitions is not None
                else frozenset()
            ),
        }
        return ExtractionResult(cohort=cohort, raw_reference=None, raw_payload=payload)

    def normalize(self, result: ExtractionResult) -> NormalizedEvidence:
        payload = result.raw_payload
        repository_id: str = payload["repository_id"]
        revision: str = payload["revision"]
        locally_defined: frozenset[str] = payload["locally_defined"]
        kind_by_symbol: dict[str, int] = payload["kind_by_symbol"]
        indexed_relative_paths: frozenset[str] = payload["indexed_relative_paths"]
        ambiguous_symbols: frozenset[str] = payload["ambiguous_symbols"]

        entities: dict[str, RepositorySymbol] = {}
        evidence: list[Evidence] = []

        def resolve(symbol: str) -> _ResolvedSymbol | None:
            if symbol in ambiguous_symbols:
                # FND-1 fix: never guess which of 2+ real entities a
                # position-less reference/relationship endpoint means --
                # skip rather than fabricate (directive D5 §9, §11's own
                # "never fabricate" discipline, applied to this new case).
                return None
            return _resolve_symbol(
                symbol,
                repository_id=repository_id,
                revision=revision,
                locally_defined=locally_defined,
                kind_by_symbol=kind_by_symbol,
                indexed_relative_paths=indexed_relative_paths,
            )

        def ensure_entity(
            resolved: _ResolvedSymbol,
            *,
            roles: list[str] | None = None,
            source_location: SourceLocation | None = None,
        ) -> None:
            existing = entities.get(resolved.canonical_id)
            if existing is not None and existing.source_location is not None:
                return
            name = resolved.qualified_name.rsplit("/", maxsplit=1)[-1] or resolved.qualified_name
            merged_roles = list(roles or [])
            inferred_role = "scip:inferred-from-relationship-only"
            if resolved.inferred_from_relationship_only and inferred_role not in merged_roles:
                merged_roles.append(inferred_role)
            entities[resolved.canonical_id] = RepositorySymbol(
                canonical_id=resolved.canonical_id,
                repository_id=repository_id,
                repository_revision=resolved.revision,
                name=name,
                qualified_name=resolved.qualified_name,
                base_type=resolved.base_type,
                roles=merged_roles,
                source_location=source_location,
                # This adapter's own raw name, recorded under its own
                # provider key (Symbol Identity & Name Normalization
                # investigation) -- preserved through cross-provider
                # entity-resolution merges (`codex.resolution.
                # entity_resolver._merge_pair`'s existing `provider_ids`
                # dict-union) even when this provider's own name loses
                # `_choose_symbol_name`'s undecorated-name preference, so
                # it is never silently discarded, only not the one shown
                # as `RepositorySymbol.name`. Never normalized/stripped
                # -- exactly this provider's own computed value, SCIP
                # descriptor punctuation included.
                provider_ids={self.provider_name: name},
            )

        definitions = payload["definitions"]
        if definitions is not None:
            for definition in definitions:
                if definition.is_nested_disambiguation_representative:
                    # FND-1 fix: resolved directly (bypassing the shared
                    # `resolve()` closure, which deliberately skips every
                    # symbol in `ambiguous_symbols` for references/
                    # relationships) so this specific, already-disambiguated
                    # occurrence gets its own distinct identity instead of
                    # being skipped too. `nested_qualifier` may itself be
                    # `None` here (a genuinely top-level class member
                    # disambiguated from nested siblings of the same name)
                    # -- `_resolve_symbol` already treats that as "use the
                    # plain descriptor," which is exactly what's wanted.
                    resolved = _resolve_symbol(
                        definition.symbol,
                        repository_id=repository_id,
                        revision=revision,
                        locally_defined=locally_defined,
                        kind_by_symbol=kind_by_symbol,
                        indexed_relative_paths=indexed_relative_paths,
                        nested_qualifier=definition.nested_qualifier,
                    )
                else:
                    resolved = resolve(definition.symbol)
                if resolved is None:
                    continue
                kind_role = role_for_kind(definition.kind)
                roles: list[str] = [kind_role] if kind_role else []
                if definition.is_redefinition_family:
                    # GAP-13 fix: this location came from the last member
                    # of a same-symbol textual-redefinition cluster
                    # (`@typing.overload`/`@property`+`.setter`), not
                    # directly from the Definition-role Occurrence --
                    # kept auditable rather than silently indistinguishable
                    # from an ordinary single-defined symbol.
                    roles.append("scip:redefinition-family")
                if definition.is_nested_disambiguation_representative:
                    # FND-1 fix: this entity's identity was disambiguated
                    # from same-descriptor siblings -- kept auditable, same
                    # spirit as the redefinition-family tag above.
                    roles.append("scip:nested-scope-disambiguated")
                location = _location_from_range(definition.range, definition.relative_path)
                ensure_entity(resolved, roles=roles, source_location=location)

        counter = 0

        def emit(subject: str, predicate: RelationshipType, obj: str, tag: str) -> None:
            nonlocal counter
            evidence.append(
                _make_evidence(
                    f"scip:{revision}:{tag}:{counter}",
                    cohort=result.cohort,
                    subject=subject,
                    predicate=predicate,
                    obj=obj,
                )
            )
            counter += 1

        references = payload["references"]
        if references is not None:
            for ref in references:
                file_id = build_canonical_id(
                    repository_id=repository_id,
                    repository_revision=revision,
                    qualified_name=ref.relative_path,
                    base_type=BaseEntityType.FILE,
                )
                file_resolved = _ResolvedSymbol(
                    file_id, BaseEntityType.FILE, ref.relative_path, revision
                )
                ensure_entity(file_resolved)
                resolved = resolve(ref.symbol)
                if resolved is None:
                    continue
                ensure_entity(resolved)
                predicate = (
                    RelationshipType.IMPORTS if ref.is_import else RelationshipType.REFERENCES
                )
                emit(file_id, predicate, resolved.canonical_id, "reference")

        implementations = payload["implementations"]
        if implementations is not None:
            for fact in implementations:
                subject_resolved = resolve(fact.subject_symbol)
                object_resolved = resolve(fact.object_symbol)
                if subject_resolved is None or object_resolved is None:
                    continue
                ensure_entity(subject_resolved)
                ensure_entity(object_resolved)
                emit(
                    subject_resolved.canonical_id,
                    RelationshipType.IMPLEMENTS,
                    object_resolved.canonical_id,
                    "implementation",
                )

        type_relationships = payload["type_relationships"]
        if type_relationships is not None:
            for fact in type_relationships:
                subject_resolved = resolve(fact.subject_symbol)
                object_resolved = resolve(fact.object_symbol)
                if subject_resolved is None or object_resolved is None:
                    continue
                ensure_entity(subject_resolved)
                ensure_entity(object_resolved)
                emit(
                    subject_resolved.canonical_id,
                    RelationshipType.REFERENCES,
                    object_resolved.canonical_id,
                    "type",
                )

        return NormalizedEvidence(
            entities=list(entities.values()), evidence=evidence, cohort=result.cohort
        )
