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
