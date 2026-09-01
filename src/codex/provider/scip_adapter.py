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
from codex.provider.scip.index import ScipIndex, ScipRange, decode_index
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


def _collect_definitions(index: ScipIndex) -> list[_DefinitionRecord]:
    kind_by_symbol = _build_kind_by_symbol(index)
    records = [
        _DefinitionRecord(
            occ.symbol, kind_by_symbol.get(occ.symbol, 0), occ.range, doc.relative_path
        )
        for doc in index.documents
        for occ in doc.occurrences
        if (occ.symbol_roles & _DEFINITION_ROLE) and occ.symbol and not is_local_symbol(occ.symbol)
    ]
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


@dataclass(frozen=True)
class _ResolvedSymbol:
    canonical_id: str
    base_type: BaseEntityType
    qualified_name: str
    revision: str


def _resolve_symbol(
    symbol: str,
    *,
    repository_id: str,
    revision: str,
    locally_defined: frozenset[str],
    kind_by_symbol: dict[str, int],
) -> _ResolvedSymbol | None:
    """Resolve a SCIP symbol string to a canonical Codex identity.

    Returns ``None`` for a local-scope symbol or one whose header this
    adapter can't parse — callers must skip rather than fabricate an
    identity (directive D5 §9, §11). Uses the *same* ``kind_by_symbol``
    lookup as ``_collect_definitions`` so a locally defined symbol always
    resolves to the same base type -- and therefore the same
    ``canonical_id`` -- whether it's reached via SYMBOL_DEFINITION,
    SYMBOL_REFERENCE, IMPLEMENTATION, or TYPE_RELATIONSHIP.
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
        canonical_id = build_canonical_id(
            repository_id=repository_id,
            repository_revision=revision,
            qualified_name=qualified_name,
            base_type=base_type,
        )
        return _ResolvedSymbol(canonical_id, base_type, qualified_name, revision)

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
                definitions = _collect_definitions(index)
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
            "locally_defined": frozenset(
                info.symbol for doc in index.documents for info in doc.symbols
            ),
            "kind_by_symbol": _build_kind_by_symbol(index),
            "definitions": definitions,
            "references": references,
            "implementations": implementations,
            "type_relationships": type_relationships,
        }
        return ExtractionResult(cohort=cohort, raw_reference=None, raw_payload=payload)

    def normalize(self, result: ExtractionResult) -> NormalizedEvidence:
        payload = result.raw_payload
        repository_id: str = payload["repository_id"]
        revision: str = payload["revision"]
        locally_defined: frozenset[str] = payload["locally_defined"]
        kind_by_symbol: dict[str, int] = payload["kind_by_symbol"]

        entities: dict[str, RepositorySymbol] = {}
        evidence: list[Evidence] = []

        def resolve(symbol: str) -> _ResolvedSymbol | None:
            return _resolve_symbol(
                symbol,
                repository_id=repository_id,
                revision=revision,
                locally_defined=locally_defined,
                kind_by_symbol=kind_by_symbol,
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
            entities[resolved.canonical_id] = RepositorySymbol(
                canonical_id=resolved.canonical_id,
                repository_id=repository_id,
                repository_revision=resolved.revision,
                name=name,
                qualified_name=resolved.qualified_name,
                base_type=resolved.base_type,
                roles=roles or [],
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
                resolved = resolve(definition.symbol)
                if resolved is None:
                    continue
                role = role_for_kind(definition.kind)
                location = _location_from_range(definition.range, definition.relative_path)
                ensure_entity(resolved, roles=[role] if role else [], source_location=location)

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
