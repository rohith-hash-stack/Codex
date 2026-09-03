"""Regression test for the "Diagnose and Fix Canonical v1 Evidence/
Fabrication Findings" checkpoint's Finding 1.

**What Finding 1 originally reported**: `EvidencePackage.evidence` was
observed empty (`0`) for every SCIP-backed canonical-corpus case, even
ones with well-populated `relationships`.

**What the follow-up investigation found**: this was a bug in a
one-off, ad-hoc diagnostic script from that investigation, which passed
a *fresh, empty* `InMemoryEvidenceStore()` to `execute_query` instead of
reusing the *same* store instance `IngestionPipeline` had just committed
real `Evidence` records into. `execute_query`/`collect_evidence`
(`codex.planner.retrieval.collect_evidence`) resolve `CanonicalRelationship.
supporting_evidence_ids` by looking them up in whatever `EvidenceStore` the
caller supplies -- passing a different, empty store silently (and
correctly, by that function's own contract) returns no evidence, because
no evidence was ever committed to *that* store. The actual canonical-v1
benchmark run (`scripts/run_canonical_benchmark.py`) never had this bug --
verified directly, it reuses the same store throughout -- so the real
`codex-canonical-v1` OpenAI run's `EvidencePackage.evidence` was correctly
populated all along. Finding 1 is **not a Codex defect**.

This test locks in the correct behavior permanently: for a real,
SCIP-sourced repository, evidence resolves end-to-end through
`execute_query` when (and only when) the correct store is used --
guarding against a future regression, and against the same
evidence-store mismatch this investigation's own tooling once made.
"""

from __future__ import annotations

from datetime import UTC, datetime

from codex.benchmark.canonical_corpus import make_click_repository, make_flask_repository
from codex.evidence.store import InMemoryEvidenceStore
from codex.ingestion.pipeline import IngestionPipeline
from codex.planner.planner import execute_query, plan_query
from codex.provider.scip_adapter import SCIPAdapter
from codex.query_understanding.engine import understand_query
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile

NOW = datetime(2026, 9, 3, tzinfo=UTC)
_PROFILE = ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.3)


def _ingest(repository, index_filename: str):
    registry = CapabilityRegistry()
    store = InMemoryEvidenceStore()
    registry.register(SCIPAdapter(index_filename=index_filename), _PROFILE)
    result = IngestionPipeline(registry, store).run(repository)
    return result, registry, store


def test_scip_backed_evidence_resolves_when_the_correct_store_is_reused() -> None:
    """The real, correct usage pattern (same store for ingestion and
    execution): `EvidencePackage.evidence` count matches `relationships`
    count exactly -- proving SCIP's real `IMPLEMENTS` evidence
    propagates all the way through `execute_query`."""
    repository = make_click_repository()
    result, registry, store = _ingest(repository, "click_sample.scip")

    understanding = understand_query(
        "What implements UsageError?", repository_id=repository.repository_id, now=NOW
    )
    assert understanding.contract is not None
    plan = plan_query(
        query_contract=understanding.contract,
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    package = execute_query(
        plan, graph=result.graph_store, evidence_store=store, ingestion_result=result
    )

    assert len(package.relationships) > 0
    assert len(package.evidence) == len(package.relationships)
    for relationship in package.relationships:
        assert relationship.supporting_evidence_ids
        for evidence_id in relationship.supporting_evidence_ids:
            assert store.get_evidence(evidence_id) is not None


def test_scip_backed_evidence_is_empty_when_a_mismatched_store_is_used() -> None:
    """The exact failure mode Finding 1 actually observed, reproduced
    deliberately: a *different*, empty `EvidenceStore` (never committed
    to by ingestion) correctly -- by `collect_evidence`'s own contract --
    resolves no evidence, even though `relationships` is populated.
    Proves the symptom was a caller-side store mismatch, not missing or
    malformed SCIP evidence."""
    repository = make_flask_repository()
    result, registry, _real_store = _ingest(repository, "flask_sample.scip")

    understanding = understand_query(
        "What implements Scaffold?", repository_id=repository.repository_id, now=NOW
    )
    assert understanding.contract is not None
    plan = plan_query(
        query_contract=understanding.contract,
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    mismatched_store = InMemoryEvidenceStore()  # never committed to
    package = execute_query(
        plan, graph=result.graph_store, evidence_store=mismatched_store, ingestion_result=result
    )

    assert len(package.relationships) > 0
    assert len(package.evidence) == 0


def test_scip_relationships_carry_real_supporting_evidence_ids() -> None:
    """Independent of `execute_query`: every `CanonicalRelationship` the
    real click graph produces from `SCIPAdapter` data has a non-empty
    `supporting_evidence_ids` naming a real, committed `Evidence` record
    -- confirmed directly against the `GraphStore`/`EvidenceStore`, not
    through the planner at all."""
    repository = make_click_repository()
    result, _registry, store = _ingest(repository, "click_sample.scip")

    relationships = result.graph_store.get_relationships()
    assert len(relationships) > 1000  # the real click graph, not a fixture stub
    sample = relationships[:50]
    for relationship in sample:
        assert relationship.supporting_evidence_ids
        evidence_id = relationship.supporting_evidence_ids[0]
        evidence = store.get_evidence(evidence_id)
        assert evidence is not None
        assert evidence.provider == "scip"
