"""MSS construction tests (TAD §39-40; directive D9 Part 18 "MSS"): no
expansion when unnecessary, SOURCE_CONTEXT expansion, two-cycle max,
50-node max, PARTIAL result.
"""

from __future__ import annotations

from codex.evidence.model import CanonicalRelationship
from codex.graph.memory_store import InMemoryGraphStore
from codex.graph.version import GraphVersion
from codex.ontology.entities import (
    BaseEntityType,
    LifecycleStatus,
    RepositorySymbol,
    SourceLocation,
)
from codex.ontology.relationships import RelationshipType
from codex.planner.mss import MAX_EXPANSION_CYCLES, MAX_NODES_PER_CYCLE, expand_for_source_context
from codex.provider.capability import Capability


def _entity(
    canonical_id: str,
    *,
    with_location: bool = False,
    active: bool = True,
) -> RepositorySymbol:
    return RepositorySymbol(
        canonical_id=canonical_id,
        repository_id="repo1",
        repository_revision="rev1",
        name=canonical_id,
        qualified_name=canonical_id,
        base_type=BaseEntityType.FILE,
        lifecycle_status=LifecycleStatus.ACTIVE if active else LifecycleStatus.DELETED,
        source_location=(
            SourceLocation(file_path=f"{canonical_id}.py", start_line=0, end_line=1)
            if with_location
            else None
        ),
    )


def _store() -> InMemoryGraphStore:
    version = GraphVersion(version_id="v1", repository_id="repo1", repository_revision="rev1")
    return InMemoryGraphStore(version)


def test_no_expansion_when_source_location_not_required() -> None:
    store = _store()
    seed = _entity("a")
    store.upsert_entity(seed)
    entities, partial = expand_for_source_context(
        graph=store, entities=[seed], required_capabilities=[Capability.CALL_RELATIONSHIP]
    )
    assert entities == [seed]
    assert partial is False


def test_no_expansion_when_seed_already_has_source_context() -> None:
    store = _store()
    seed = _entity("a", with_location=True)
    store.upsert_entity(seed)
    entities, partial = expand_for_source_context(
        graph=store, entities=[seed], required_capabilities=[Capability.SOURCE_LOCATION]
    )
    assert entities == [seed]
    assert partial is False


def test_expansion_finds_a_neighbor_with_source_context() -> None:
    store = _store()
    seed = _entity("a")
    neighbor = _entity("b", with_location=True)
    store.upsert_entity(seed)
    store.upsert_entity(neighbor)
    store.upsert_relationship(
        CanonicalRelationship(subject="a", predicate=RelationshipType.CALLS, object="b")
    )

    entities, partial = expand_for_source_context(
        graph=store, entities=[seed], required_capabilities=[Capability.SOURCE_LOCATION]
    )
    assert {e.canonical_id for e in entities} == {"a", "b"}
    assert partial is False


def test_expansion_reports_partial_when_bound_exhausted_without_satisfying() -> None:
    store = _store()
    seed = _entity("a")
    neighbor = _entity("b")  # no source_location anywhere reachable
    store.upsert_entity(seed)
    store.upsert_entity(neighbor)
    store.upsert_relationship(
        CanonicalRelationship(subject="a", predicate=RelationshipType.CALLS, object="b")
    )

    entities, partial = expand_for_source_context(
        graph=store, entities=[seed], required_capabilities=[Capability.SOURCE_LOCATION]
    )
    assert partial is True


def test_expansion_never_exceeds_two_cycles() -> None:
    store = _store()
    # A straight chain of 5 hops, none with a source_location -- expansion
    # must stop after MAX_EXPANSION_CYCLES even though the chain is longer.
    chain = [_entity(f"n{i}") for i in range(6)]
    for entity in chain:
        store.upsert_entity(entity)
    for a, b in zip(chain, chain[1:], strict=False):
        store.upsert_relationship(
            CanonicalRelationship(
                subject=a.canonical_id, predicate=RelationshipType.CALLS, object=b.canonical_id
            )
        )

    entities, partial = expand_for_source_context(
        graph=store, entities=[chain[0]], required_capabilities=[Capability.SOURCE_LOCATION]
    )
    assert partial is True
    # cycle 1 reaches n1, cycle 2 reaches n1's neighbors (n0 already visited, n2 new)
    assert {e.canonical_id for e in entities} == {"n0", "n1", "n2"}


def test_expansion_never_exceeds_fifty_nodes_per_cycle() -> None:
    store = _store()
    seed = _entity("hub")
    store.upsert_entity(seed)
    for i in range(80):
        leaf = _entity(f"leaf{i}")
        store.upsert_entity(leaf)
        store.upsert_relationship(
            CanonicalRelationship(
                subject="hub", predicate=RelationshipType.CALLS, object=leaf.canonical_id
            )
        )

    entities, partial = expand_for_source_context(
        graph=store, entities=[seed], required_capabilities=[Capability.SOURCE_LOCATION]
    )
    # cycle 1 adds at most MAX_NODES_PER_CYCLE leaves; hub is always kept.
    assert len(entities) - 1 <= MAX_NODES_PER_CYCLE
    assert partial is True


def test_expansion_stops_visiting_later_frontier_entities_once_cycle_is_full() -> None:
    """When the per-cycle ceiling is reached partway through the second
    cycle's frontier, later frontier entities are skipped entirely
    rather than each contributing a few more nodes over the ceiling."""
    store = _store()
    seed = _entity("seed")
    n1 = _entity("n1")
    n2 = _entity("n2")
    store.upsert_entity(seed)
    store.upsert_entity(n1)
    store.upsert_entity(n2)
    store.upsert_relationship(
        CanonicalRelationship(subject="seed", predicate=RelationshipType.CALLS, object="n1")
    )
    store.upsert_relationship(
        CanonicalRelationship(subject="seed", predicate=RelationshipType.CALLS, object="n2")
    )
    for i in range(60):
        leaf = _entity(f"leaf{i}")
        store.upsert_entity(leaf)
        store.upsert_relationship(
            CanonicalRelationship(
                subject="n1", predicate=RelationshipType.CALLS, object=leaf.canonical_id
            )
        )
    m1 = _entity("m1")
    store.upsert_entity(m1)
    store.upsert_relationship(
        CanonicalRelationship(subject="n2", predicate=RelationshipType.CALLS, object="m1")
    )

    entities, partial = expand_for_source_context(
        graph=store, entities=[seed], required_capabilities=[Capability.SOURCE_LOCATION]
    )
    ids = {e.canonical_id for e in entities}
    assert "m1" not in ids  # n2's neighbor never reached: cycle 2 filled up on n1 first
    assert partial is True


def test_expansion_constants_match_tad_bounds() -> None:
    assert MAX_EXPANSION_CYCLES == 2
    assert MAX_NODES_PER_CYCLE == 50
