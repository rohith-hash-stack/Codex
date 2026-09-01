"""Symbol-level cross-provider entity convergence (D7/D9 convergence
directive; HLRD §19; ``docs/architecture-conformance-audit.md`` §JJ).

Covers what ``tests/test_entity_resolution.py``'s existing, resolver-only
tests cannot: whether the symbol-location identity key (now applied
*unconditionally*, see ``codex.resolution.entity_resolver``'s module
docstring) actually converges independently-produced provider entities
through the real ``IngestionPipeline`` without losing evidence -- the
exact cross-batch evidence-staleness regression found and fixed during
this directive's implementation -- and whether it does so against real,
not synthetic, repository source (``AstCallsAdapter``'s real ``ast``
extraction + a real ``scip-python``-produced SCIP index, both run
independently against the same frozen, real Codex source snapshot).
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from codex.evidence.model import CoverageStatus, Evidence, EvidenceCohort
from codex.evidence.store import InMemoryEvidenceStore
from codex.ingestion.pipeline import IngestionPipeline
from codex.ontology.entities import (
    BaseEntityType,
    RepositorySymbol,
    SourceLocation,
    build_canonical_id,
)
from codex.ontology.relationships import RelationshipType
from codex.provider.ast_calls_adapter import AstCallsAdapter
from codex.provider.capability import Capability
from codex.provider.contract import (
    EligibilityStatus,
    ExtractionResult,
    NormalizedEvidence,
    ProviderEligibility,
    ProviderHealthStatus,
    ValidationResult,
)
from codex.provider.scip_adapter import DEFAULT_INDEX_FILENAME, SCIPAdapter
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile
from codex.repository.models import RepositoryMetadata

FIXTURES = Path(__file__).parent / "fixtures"
CONVERGENCE_SOURCE = FIXTURES / "codex_convergence_sample"
CONVERGENCE_SCIP_INDEX = FIXTURES / "scip" / "codex_resolution_sample.scip"
"""``entity_resolver.py``/``paths.py`` frozen, real, unmodified copies of
this project's own ``src/codex/resolution/`` source (checked in at the
time this test was written), plus ``codex_resolution_sample.scip`` -- a
*real* SCIP index produced by actually running ``scip-python`` (not a
handcrafted protobuf fixture, matching the existing precedent set by
``tests/fixtures/scip/typescript_sample.scip``) against exactly that
same source, via ``scip-python index --target-only src/codex/resolution``
from this repository's own root. Frozen together deliberately (not
regenerated at test time, which would need network/npx and be slow and
non-hermetic) -- if ``src/codex/resolution/`` changes later, this
fixture pair simply keeps testing the frozen snapshot, exactly as
``typescript_sample.scip`` already does for a language this repository
has no live source for at all."""


# ---------------------------------------------------------------------------
# A minimal, self-contained fake provider pair for the cross-batch
# evidence-staleness regression -- deliberately NOT reusing
# `DeterministicFakeAdapter` (many other tests depend on its exact
# behavior/signature; this fixture needs a `source_location`, which that
# one does not support, and touching it risks unrelated regressions).
# ---------------------------------------------------------------------------


class _OneSymbolAdapter:
    """Produces exactly one symbol-level entity (with a `source_location`)
    and, optionally, one `CALLS` evidence item from that entity to a
    second, never-converging entity it also produces -- just enough to
    reproduce a provider's own "commit entity + commit its own evidence
    in the same call" sequence `IngestionPipeline._run_one_provider`
    performs for every real provider."""

    def __init__(
        self,
        *,
        name: str,
        qualified_name: str,
        base_type: BaseEntityType,
        file_path: str,
        start_line: int,
        emit_calls_evidence: bool,
    ) -> None:
        self._name = name
        self._qualified_name = qualified_name
        self._base_type = base_type
        self._file_path = file_path
        self._start_line = start_line
        self._emit_calls_evidence = emit_calls_evidence

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def provider_version(self) -> str:
        return "1.0.0"

    @property
    def supported_capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.SYMBOL_DEFINITION, Capability.CALL_RELATIONSHIP})

    @property
    def health_status(self) -> ProviderHealthStatus:
        return ProviderHealthStatus.HEALTHY

    def availability(self, capability: Capability, repository: RepositoryMetadata) -> float:
        return 1.0

    @property
    def freshness(self) -> None:
        return None

    def validate(self) -> ValidationResult:
        return ValidationResult(ok=True, problems=[])

    def check_eligibility(self, repository: RepositoryMetadata) -> ProviderEligibility:
        return ProviderEligibility(status=EligibilityStatus.ELIGIBLE)

    def extract(
        self, repository: RepositoryMetadata, capabilities: Collection[Capability]
    ) -> ExtractionResult:
        cohort = EvidenceCohort(
            provider=self._name,
            provider_version=self.provider_version,
            snapshot_id=repository.head_revision,
            source_revision=repository.head_revision,
            successful_capabilities=sorted(c.value for c in self.supported_capabilities),
            failed_capabilities=[],
            coverage_status=CoverageStatus.FULL,
        )
        return ExtractionResult(
            cohort=cohort,
            raw_payload={
                "repository_id": repository.repository_id,
                "revision": repository.head_revision,
            },
        )

    def normalize(self, result: ExtractionResult) -> NormalizedEvidence:
        payload = result.raw_payload
        repository_id: str = payload["repository_id"]
        revision: str = payload["revision"]

        location = SourceLocation(
            file_path=self._file_path,
            start_line=self._start_line,
            end_line=self._start_line,
            start_column=0,
            end_column=10,
        )
        target_qualified_name = f"{self._qualified_name}::callee"
        subject_id = build_canonical_id(
            repository_id=repository_id,
            repository_revision=revision,
            qualified_name=self._qualified_name,
            base_type=self._base_type,
        )
        target_id = build_canonical_id(
            repository_id=repository_id,
            repository_revision=revision,
            qualified_name=target_qualified_name,
            base_type=BaseEntityType.FUNCTION,
        )
        entities = [
            RepositorySymbol(
                canonical_id=subject_id,
                repository_id=repository_id,
                repository_revision=revision,
                name=self._qualified_name,
                qualified_name=self._qualified_name,
                base_type=self._base_type,
                source_location=location,
            ),
        ]
        evidence: list[Evidence] = []
        if self._emit_calls_evidence:
            entities.append(
                RepositorySymbol(
                    canonical_id=target_id,
                    repository_id=repository_id,
                    repository_revision=revision,
                    name=target_qualified_name,
                    qualified_name=target_qualified_name,
                    base_type=BaseEntityType.FUNCTION,
                    source_location=SourceLocation(
                        file_path=self._file_path, start_line=999, end_line=999
                    ),
                )
            )
            evidence.append(
                Evidence(
                    evidence_id=f"{self._name}:{revision}:0",
                    provider=self._name,
                    provider_version=self.provider_version,
                    snapshot_id=result.cohort.snapshot_id,
                    source_revision=revision,
                    subject=subject_id,
                    predicate=RelationshipType.CALLS,
                    object=target_id,
                    confidence=1.0,
                    freshness=result.cohort.observed_at,
                )
            )
        return NormalizedEvidence(entities=entities, evidence=evidence, cohort=result.cohort)


def _run_two_providers(
    *, first: _OneSymbolAdapter, second: _OneSymbolAdapter, revision: str = "rev1"
):
    registry = CapabilityRegistry()
    registry.register(first, ProviderScoreProfile(evidence_quality=0.8, cost_factor=0.3))
    registry.register(second, ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.3))
    pipeline = IngestionPipeline(registry, InMemoryEvidenceStore())
    repository = RepositoryMetadata(
        repository_id="repo1", local_path=Path("/nonexistent"), head_revision=revision
    )
    return pipeline.run(repository)


# --- Cross-batch evidence-staleness regression (the bug this directive fixes) ---


def test_first_providers_own_evidence_survives_a_later_convergence() -> None:
    """The exact bug found during implementation, reproduced minimally:
    provider "aaa_first" (sorts before "bbb_second", matching real
    `ast_calls` < `scip` ordering) commits a symbol entity *and* its own
    `CALLS` evidence in one call, as a singleton (no merge partner yet).
    Provider "bbb_second" commits a second entity at the *same*
    `(base_type, file_path, start_line)` afterwards, which converges
    with the first. `aaa_first`'s own evidence, committed before the
    convergence was ever discovered, must still resolve to a real,
    committed entity -- not be silently dropped by `_materialize_store`'s
    referential-integrity check, which is exactly what happened before
    this directive's fix (symbol-location keying was gated on a merge
    partner already being present in the same `resolve_entities()` call,
    so a provider's own first commit kept its original id, and nothing
    ever revisited that provider's already-committed evidence when a
    later provider's commit renamed the entity)."""
    first = _OneSymbolAdapter(
        name="aaa_first",
        qualified_name="mod.py::foo",
        base_type=BaseEntityType.FUNCTION,
        file_path="mod.py",
        start_line=10,
        emit_calls_evidence=True,
    )
    second = _OneSymbolAdapter(
        name="bbb_second",
        qualified_name="`mod`/foo().",
        base_type=BaseEntityType.FUNCTION,
        file_path="mod.py",
        start_line=10,
        emit_calls_evidence=False,
    )
    result = _run_two_providers(first=first, second=second)

    entities = result.graph_store.find_entities()
    foo_entities = [e for e in entities if e.source_location and e.source_location.start_line == 10]
    assert len(foo_entities) == 1, "the two raw `foo` entities must have converged into one"
    merged_id = foo_entities[0].canonical_id

    relationships = result.graph_store.get_relationships()
    calls = [r for r in relationships if r.predicate is RelationshipType.CALLS]
    assert len(calls) == 1, "aaa_first's own CALLS evidence must not have been dropped"
    assert calls[0].subject == merged_id, "the surviving relationship must point at the merged id"

    entity_ids = {e.canonical_id for e in entities}
    assert calls[0].subject in entity_ids
    assert calls[0].object in entity_ids


def test_provider_order_does_not_change_the_evidence_survival_outcome() -> None:
    """Same scenario, evidence-producing provider registered/named to
    run *second* alphabetically instead of first -- the fix must not
    depend on which side of the merge happens to own the evidence."""
    first = _OneSymbolAdapter(
        name="aaa_first",
        qualified_name="mod.py::foo",
        base_type=BaseEntityType.FUNCTION,
        file_path="mod.py",
        start_line=10,
        emit_calls_evidence=False,
    )
    second = _OneSymbolAdapter(
        name="bbb_second",
        qualified_name="`mod`/foo().",
        base_type=BaseEntityType.FUNCTION,
        file_path="mod.py",
        start_line=10,
        emit_calls_evidence=True,
    )
    result = _run_two_providers(first=first, second=second)

    entities = result.graph_store.find_entities()
    foo_entities = [e for e in entities if e.source_location and e.source_location.start_line == 10]
    assert len(foo_entities) == 1
    merged_id = foo_entities[0].canonical_id

    relationships = result.graph_store.get_relationships()
    calls = [r for r in relationships if r.predicate is RelationshipType.CALLS]
    assert len(calls) == 1
    assert calls[0].subject == merged_id


# --- Real veyra/codex-style dual-provider convergence, using real Codex source ---


def _load_real_convergence_sources(tmp_path: Path) -> RepositoryMetadata:
    import shutil

    shutil.copy(CONVERGENCE_SOURCE / "entity_resolver.py", tmp_path / "entity_resolver.py")
    shutil.copy(CONVERGENCE_SOURCE / "paths.py", tmp_path / "paths.py")
    shutil.copy(CONVERGENCE_SCIP_INDEX, tmp_path / DEFAULT_INDEX_FILENAME)
    return RepositoryMetadata(
        repository_id="codex-sample", local_path=tmp_path, head_revision="rev1"
    )


def _by_name(entities: list[RepositorySymbol], *names: str) -> list[RepositorySymbol]:
    return [e for e in entities if e.name in names]


def test_real_codex_source_scip_and_ast_functions_converge(tmp_path: Path) -> None:
    """The 'real codex equivalent-symbol convergence' required test
    category: `AstCallsAdapter`'s real `ast`-based extraction and a
    real `scip-python`-produced SCIP index, run *independently* (no
    provider imports the other, no shared state) against the exact
    same real, frozen Codex source (`src/codex/resolution/
    entity_resolver.py`/`paths.py` as they existed when this fixture
    was captured) converge every real function both providers see, via
    the real `IngestionPipeline`, with no evidence loss."""
    repository = _load_real_convergence_sources(tmp_path)
    registry = CapabilityRegistry()
    scip_profile = ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.4)
    ast_profile = ProviderScoreProfile(evidence_quality=0.85, cost_factor=0.3)
    registry.register(SCIPAdapter(), scip_profile)
    registry.register(AstCallsAdapter(), ast_profile)
    pipeline = IngestionPipeline(registry, InMemoryEvidenceStore())

    result = pipeline.run(repository)

    entities = result.graph_store.find_entities()
    resolve_entities_fn = _by_name(entities, "resolve_entities", "resolve_entities().")
    assert len(resolve_entities_fn) == 1, (
        "SCIP's and AstCallsAdapter's independently produced `resolve_entities` "
        "function entities must converge to exactly one canonical entity"
    )

    merge_pair_fn = _by_name(entities, "_merge_pair", "_merge_pair().")
    assert len(merge_pair_fn) == 1, "cross-file real function must also converge"

    # Referential integrity: nothing dangling after real convergence + remap.
    entity_ids = {e.canonical_id for e in entities}
    relationships = result.graph_store.get_relationships()
    for rel in relationships:
        assert rel.subject in entity_ids
        assert rel.object in entity_ids

    # A real CALLS edge AstCallsAdapter alone can see (`resolve_entities`
    # calls `_merge_pair`) must still be present, now pointing at the
    # *converged* ids, not stale raw ones -- this is the exact regression
    # the cross-batch evidence-staleness fix targets, reproduced here
    # against real (not hand-built) provider output.
    resolve_id = resolve_entities_fn[0].canonical_id
    merge_pair_id = merge_pair_fn[0].canonical_id
    calls = [r for r in relationships if r.predicate is RelationshipType.CALLS]
    assert any(r.subject == resolve_id and r.object == merge_pair_id for r in calls)


def test_real_codex_source_convergence_is_provider_order_independent(tmp_path: Path) -> None:
    """Same real fixture, providers registered in the opposite name
    order -- `IngestionPipeline` always runs providers in
    `sorted(name)` order regardless of registration order, but this
    proves the *outcome* doesn't secretly depend on which provider the
    registry happens to iterate first either."""
    repository = _load_real_convergence_sources(tmp_path)
    registry = CapabilityRegistry()
    ast_profile = ProviderScoreProfile(evidence_quality=0.85, cost_factor=0.3)
    scip_profile = ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.4)
    registry.register(AstCallsAdapter(), ast_profile)
    registry.register(SCIPAdapter(), scip_profile)
    pipeline = IngestionPipeline(registry, InMemoryEvidenceStore())

    result = pipeline.run(repository)

    entities = result.graph_store.find_entities()
    resolve_entities_fn = _by_name(entities, "resolve_entities", "resolve_entities().")
    assert len(resolve_entities_fn) == 1
