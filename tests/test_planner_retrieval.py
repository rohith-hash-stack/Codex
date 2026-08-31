"""Direct unit tests for `codex.planner.retrieval` (bounded_traversal
edge/node ceiling behavior not otherwise exercised through `plan_query`'s
own budget-fit scenarios)."""

from __future__ import annotations

from codex.evidence.model import CanonicalRelationship
from codex.graph.memory_store import InMemoryGraphStore
from codex.graph.version import GraphVersion
from codex.ontology.entities import BaseEntityType, RepositorySymbol
from codex.ontology.relationships import RelationshipType
from codex.planner.retrieval import bounded_traversal, collect_evidence


def _entity(canonical_id: str) -> RepositorySymbol:
    return RepositorySymbol(
        canonical_id=canonical_id,
        repository_id="repo1",
        repository_revision="rev1",
        name=canonical_id,
        qualified_name=canonical_id,
        base_type=BaseEntityType.FILE,
    )


def _store() -> InMemoryGraphStore:
    version = GraphVersion(version_id="v1", repository_id="repo1", repository_revision="rev1")
    return InMemoryGraphStore(version)


def test_traversal_truncates_on_edge_ceiling_with_generous_node_ceiling() -> None:
    store = _store()
    hub = _entity("hub")
    store.upsert_entity(hub)
    for i in range(10):
        leaf = _entity(f"leaf{i}")
        store.upsert_entity(leaf)
        store.upsert_relationship(
            CanonicalRelationship(
                subject="hub", predicate=RelationshipType.CALLS, object=leaf.canonical_id
            )
        )

    result = bounded_traversal(
        store, [hub], [RelationshipType.CALLS], depth=1, max_nodes=100, max_edges=3
    )
    assert result.truncated is True
    assert len(result.relationships) <= 3


def test_traversal_edge_ceiling_also_checked_on_incoming_relationships() -> None:
    store = _store()
    hub = _entity("hub")
    store.upsert_entity(hub)
    for i in range(10):
        leaf = _entity(f"leaf{i}")
        store.upsert_entity(leaf)
        store.upsert_relationship(
            CanonicalRelationship(
                subject=leaf.canonical_id, predicate=RelationshipType.CALLS, object="hub"
            )
        )

    result = bounded_traversal(
        store, [hub], [RelationshipType.CALLS], depth=1, max_nodes=100, max_edges=2
    )
    assert result.truncated is True
    assert len(result.relationships) <= 2


def test_traversal_seed_count_exceeding_max_nodes_is_sliced_defensively() -> None:
    store = _store()
    seeds = [_entity(f"seed{i}") for i in range(5)]
    for seed in seeds:
        store.upsert_entity(seed)

    result = bounded_traversal(store, seeds, [], depth=0, max_nodes=2, max_edges=10)
    assert len(result.entities) == 2


def test_collect_evidence_deduplicates_shared_evidence_ids() -> None:
    from datetime import UTC, datetime

    from codex.evidence.model import Evidence
    from codex.evidence.store import InMemoryEvidenceStore

    store = InMemoryEvidenceStore()
    ev = Evidence(
        evidence_id="e1",
        provider="fake",
        provider_version="1.0",
        snapshot_id="s1",
        source_revision="rev1",
        subject="a",
        predicate=RelationshipType.CALLS,
        object="b",
        confidence=0.9,
        freshness=datetime.now(UTC),
    )
    store.add_evidence(ev)

    rel_a = CanonicalRelationship(
        subject="a", predicate=RelationshipType.CALLS, object="b", supporting_evidence_ids=["e1"]
    )
    rel_b = CanonicalRelationship(
        subject="a", predicate=RelationshipType.CALLS, object="c", supporting_evidence_ids=["e1"]
    )
    result = collect_evidence(store, [rel_a, rel_b])
    assert [e.evidence_id for e in result] == ["e1"]


def test_collect_evidence_skips_missing_evidence_ids() -> None:
    from codex.evidence.store import InMemoryEvidenceStore

    store = InMemoryEvidenceStore()
    rel = CanonicalRelationship(
        subject="a",
        predicate=RelationshipType.CALLS,
        object="b",
        supporting_evidence_ids=["missing"],
    )
    assert collect_evidence(store, [rel]) == []


# --- D10 Decision 4: EvidencePackage must carry BOTH supporting and
# contradicting evidence -- the verifier's authoritative evidence
# boundary, never requiring it to reach around the package back into
# EvidenceStore. ------------------------------------------------------


def _make_evidence(evidence_id: str, *, independence_group: str | None = None):
    from datetime import UTC, datetime

    from codex.evidence.model import Evidence

    return Evidence(
        evidence_id=evidence_id,
        provider="fake",
        provider_version="1.0",
        snapshot_id="s1",
        source_revision="rev1",
        subject="a",
        predicate=RelationshipType.CALLS,
        object="b",
        confidence=0.9,
        freshness=datetime.now(UTC),
        independence_group=independence_group,
    )


def test_collect_evidence_includes_supporting_evidence() -> None:
    from codex.evidence.store import InMemoryEvidenceStore

    store = InMemoryEvidenceStore()
    store.add_evidence(_make_evidence("support-1"))
    rel = CanonicalRelationship(
        subject="a",
        predicate=RelationshipType.CALLS,
        object="b",
        supporting_evidence_ids=["support-1"],
    )
    result = collect_evidence(store, [rel])
    assert [e.evidence_id for e in result] == ["support-1"]


def test_collect_evidence_includes_contradicting_evidence() -> None:
    from codex.evidence.store import InMemoryEvidenceStore

    store = InMemoryEvidenceStore()
    store.add_evidence(_make_evidence("support-1"))
    store.add_evidence(_make_evidence("contradict-1"))
    rel = CanonicalRelationship(
        subject="a",
        predicate=RelationshipType.CALLS,
        object="b",
        supporting_evidence_ids=["support-1"],
        contradicting_evidence_ids=["contradict-1"],
    )
    result = collect_evidence(store, [rel])
    assert {e.evidence_id for e in result} == {"support-1", "contradict-1"}


def test_collect_evidence_preserves_independence_group_provenance() -> None:
    """Evidence objects are returned unmodified -- `.independence_group`
    (TAD §16) survives, proving no repackaging/stripping occurred."""
    from codex.evidence.store import InMemoryEvidenceStore

    store = InMemoryEvidenceStore()
    store.add_evidence(_make_evidence("contradict-1", independence_group="scip"))
    rel = CanonicalRelationship(
        subject="a",
        predicate=RelationshipType.CALLS,
        object="b",
        contradicting_evidence_ids=["contradict-1"],
    )
    result = collect_evidence(store, [rel])
    assert result[0].effective_independence_group == "scip"


def test_collect_evidence_dedupes_an_id_appearing_as_both_supporting_and_contradicting() -> None:
    """A pathological but structurally possible case (two different
    relationships disagreeing about the same evidence record's role) --
    the record is still returned exactly once."""
    from codex.evidence.store import InMemoryEvidenceStore

    store = InMemoryEvidenceStore()
    store.add_evidence(_make_evidence("e1"))
    rel_support = CanonicalRelationship(
        subject="a", predicate=RelationshipType.CALLS, object="b", supporting_evidence_ids=["e1"]
    )
    rel_contradict = CanonicalRelationship(
        subject="a",
        predicate=RelationshipType.CALLS,
        object="c",
        contradicting_evidence_ids=["e1"],
    )
    result = collect_evidence(store, [rel_support, rel_contradict])
    assert [e.evidence_id for e in result] == ["e1"]


def test_execute_query_evidence_package_is_self_sufficient_for_a_disputed_relationship() -> None:
    """End-to-end proof (D10 Decision 4): a caller holding only the
    returned `EvidencePackage` -- never touching `EvidenceStore` again
    -- can already see both the supporting AND contradicting evidence
    for a disputed relationship, with full provenance intact."""
    from codex.coverage.engine import CompletenessLevel
    from codex.evidence.model import EvidenceStatus
    from codex.planner.planner import execute_query, plan_query
    from codex.provider.capability import Capability
    from codex.query_understanding.models import Intent, QueryContract
    from planner_fixtures import build_graph

    result, registry, evidence_store, repository = build_graph(
        entity_paths=("service.py", "auth.py"), relationship_pairs=(("service.py", "auth.py"),)
    )
    # Hand-craft a second, contradicting evidence record and attach it to
    # the already-ingested relationship directly (simulating what a real
    # Reconciliation pass would have produced from a second provider).
    contradicting = _make_evidence("contradict-1", independence_group="codeql")
    evidence_store.add_evidence(contradicting)
    rel = result.graph_store.get_relationships()[0]
    disputed = rel.model_copy(
        update={
            "status": EvidenceStatus.DISPUTED,
            "contradicting_evidence_ids": [*rel.contradicting_evidence_ids, "contradict-1"],
        }
    )
    result.graph_store.upsert_relationship(disputed)

    contract = QueryContract(
        intent=Intent.FIND_CALLERS,
        targets=["auth.py"],
        relationship_types=[RelationshipType.CALLS],
        complexity=0.3,
        ambiguity=0.1,
        confidence=0.97,
        completeness_requirement=CompletenessLevel.LOW,
        required_evidence=[Capability.CALL_RELATIONSHIP],
        token_budget=4000,
        latency_budget_ms=5000,
    )
    plan = plan_query(
        query_contract=contract,
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )

    evidence_ids = {e.evidence_id for e in package.evidence}
    assert "contradict-1" in evidence_ids  # contradicting evidence present
    assert any(e.evidence_id != "contradict-1" for e in package.evidence)  # supporting too
    contradicting_in_package = next(e for e in package.evidence if e.evidence_id == "contradict-1")
    assert contradicting_in_package.effective_independence_group == "codeql"  # provenance intact
    packaged_rel = next(r for r in package.relationships if r.key == disputed.key)
    assert packaged_rel.status is EvidenceStatus.DISPUTED  # relationship-level state also present
