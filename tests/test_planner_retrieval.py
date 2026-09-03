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
    # REFERENCES (not CALLS): this exercises outgoing-edge (subject=hub)
    # collection specifically, which the post-fix external-repository
    # readiness audit's "relationship-set imprecision" fix
    # (`codex.planner.retrieval.bounded_traversal`) no longer performs for
    # directional predicates (`CALLS`/`IMPLEMENTS`) -- see
    # `test_traversal_edge_ceiling_also_checked_on_incoming_relationships`
    # below for that predicate family's own (object-anchored) ceiling test.
    store = _store()
    hub = _entity("hub")
    store.upsert_entity(hub)
    for i in range(10):
        leaf = _entity(f"leaf{i}")
        store.upsert_entity(leaf)
        store.upsert_relationship(
            CanonicalRelationship(
                subject="hub", predicate=RelationshipType.REFERENCES, object=leaf.canonical_id
            )
        )

    result = bounded_traversal(
        store, [hub], [RelationshipType.REFERENCES], depth=1, max_nodes=100, max_edges=3
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


# --- Directional-predicate anchoring (post-fix external-repository
# readiness audit, "relationship-set imprecision" finding): CALLS/IMPLEMENTS
# relationships are only collected when the *resolved target/seed* entity is
# the object (callee/implemented) endpoint, never the subject endpoint, and
# never for a non-seed entity merely visited via hop expansion. -----------


def _edge(subject: str, predicate: RelationshipType, obj: str) -> CanonicalRelationship:
    return CanonicalRelationship(subject=subject, predicate=predicate, object=obj)


def test_multi_entity_candidate_set_keeps_only_relationships_anchored_on_each_seed() -> None:
    """Real "foo"/"FooImpl" collision shape (§3 of the post-fix audit):
    multiple resolved seeds, only some of their CALLS edges are the seed's
    own callers."""
    store = _store()
    foo = _entity("foo")
    fooimpl = _entity("fooimpl")
    caller1 = _entity("caller1")
    caller2 = _entity("caller2")
    somethingelse = _entity("somethingelse")
    for e in (foo, fooimpl, caller1, caller2, somethingelse):
        store.upsert_entity(e)
    store.upsert_relationship(_edge("caller1", RelationshipType.CALLS, "foo"))
    store.upsert_relationship(_edge("caller2", RelationshipType.CALLS, "fooimpl"))
    store.upsert_relationship(_edge("fooimpl", RelationshipType.CALLS, "somethingelse"))

    result = bounded_traversal(
        store, [foo, fooimpl], [RelationshipType.CALLS], depth=1, max_nodes=100, max_edges=100
    )

    kept = {(r.subject, r.object) for r in result.relationships}
    assert kept == {("caller1", "foo"), ("caller2", "fooimpl")}


def test_reverse_direction_calls_excluded_from_find_callers() -> None:
    store = _store()
    target = _entity("target")
    other = _entity("other")
    store.upsert_entity(target)
    store.upsert_entity(other)
    store.upsert_relationship(_edge("target", RelationshipType.CALLS, "other"))

    result = bounded_traversal(
        store, [target], [RelationshipType.CALLS], depth=1, max_nodes=100, max_edges=100
    )
    assert result.relationships == []


def test_unrelated_calls_edge_between_hop_expanded_neighbors_excluded() -> None:
    """Neither `neighbor_a` nor `neighbor_b` is the queried target ("hub");
    both only enter the traversal via hop expansion (same real shape as the
    real repository's `mro3.py` chain surfacing under an unrelated
    "Implementations of ClassAB" query)."""
    store = _store()
    hub = _entity("hub")
    neighbor_a = _entity("neighbor_a")
    neighbor_b = _entity("neighbor_b")
    for e in (hub, neighbor_a, neighbor_b):
        store.upsert_entity(e)
    store.upsert_relationship(_edge("hub", RelationshipType.CALLS, "neighbor_a"))
    store.upsert_relationship(_edge("neighbor_a", RelationshipType.CALLS, "neighbor_b"))

    result = bounded_traversal(
        store, [hub], [RelationshipType.CALLS], depth=2, max_nodes=100, max_edges=100
    )

    assert neighbor_b.canonical_id in {e.canonical_id for e in result.entities}
    assert ("neighbor_a", "neighbor_b") not in {(r.subject, r.object) for r in result.relationships}


def test_init_method_case_does_not_surface_reverse_direction_edges_as_callers() -> None:
    """Real repository shape: `Test.__init__` really does call `test1`/
    `test2` in its own body, but that is not evidence of a caller of
    `__init__` -- Python never invokes `__init__` via an explicit
    source-level call site."""
    store = _store()
    init_method = _entity("__init__")
    test1 = _entity("test1")
    test2 = _entity("test2")
    for e in (init_method, test1, test2):
        store.upsert_entity(e)
    store.upsert_relationship(_edge("__init__", RelationshipType.CALLS, "test1"))
    store.upsert_relationship(_edge("__init__", RelationshipType.CALLS, "test2"))

    result = bounded_traversal(
        store, [init_method], [RelationshipType.CALLS], depth=1, max_nodes=100, max_edges=100
    )
    assert result.relationships == []


def test_classab_leaf_class_produces_no_implementation_evidence() -> None:
    """Real repository shape: `ClassAB` has real upward `IMPLEMENTS` edges
    to its own bases but zero real subclassers -- "Implementations of
    ClassAB" must not surface `ClassAB`'s own base-class edges as if they
    answered the question."""
    store = _store()
    class_ab = _entity("ClassAB")
    mixin_a = _entity("MixinA")
    interface_ab = _entity("InterfaceAB")
    for e in (class_ab, mixin_a, interface_ab):
        store.upsert_entity(e)
    store.upsert_relationship(_edge("ClassAB", RelationshipType.IMPLEMENTS, "MixinA"))
    store.upsert_relationship(_edge("ClassAB", RelationshipType.IMPLEMENTS, "InterfaceAB"))

    result = bounded_traversal(
        store, [class_ab], [RelationshipType.IMPLEMENTS], depth=1, max_nodes=100, max_edges=100
    )
    assert result.relationships == []


def test_reverse_direction_implements_excluded() -> None:
    store = _store()
    base = _entity("base")
    other = _entity("other")
    store.upsert_entity(base)
    store.upsert_entity(other)
    store.upsert_relationship(_edge("base", RelationshipType.IMPLEMENTS, "other"))

    result = bounded_traversal(
        store, [base], [RelationshipType.IMPLEMENTS], depth=1, max_nodes=100, max_edges=100
    )
    assert result.relationships == []


def test_unrelated_implements_edge_between_hop_expanded_neighbors_excluded() -> None:
    store = _store()
    hub = _entity("hub")
    neighbor_a = _entity("neighbor_a")
    neighbor_b = _entity("neighbor_b")
    for e in (hub, neighbor_a, neighbor_b):
        store.upsert_entity(e)
    store.upsert_relationship(_edge("hub", RelationshipType.IMPLEMENTS, "neighbor_a"))
    store.upsert_relationship(_edge("neighbor_a", RelationshipType.IMPLEMENTS, "neighbor_b"))

    result = bounded_traversal(
        store, [hub], [RelationshipType.IMPLEMENTS], depth=2, max_nodes=100, max_edges=100
    )

    assert neighbor_b.canonical_id in {e.canonical_id for e in result.entities}
    assert ("neighbor_a", "neighbor_b") not in {(r.subject, r.object) for r in result.relationships}


def test_single_entity_calls_query_unchanged_by_directional_anchoring() -> None:
    store = _store()
    foo = _entity("foo")
    caller = _entity("caller")
    store.upsert_entity(foo)
    store.upsert_entity(caller)
    store.upsert_relationship(_edge("caller", RelationshipType.CALLS, "foo"))

    result = bounded_traversal(
        store, [foo], [RelationshipType.CALLS], depth=1, max_nodes=100, max_edges=100
    )
    assert [(r.subject, r.object) for r in result.relationships] == [("caller", "foo")]


def test_single_entity_implements_query_unchanged_by_directional_anchoring() -> None:
    store = _store()
    base = _entity("base")
    impl = _entity("impl")
    store.upsert_entity(base)
    store.upsert_entity(impl)
    store.upsert_relationship(_edge("impl", RelationshipType.IMPLEMENTS, "base"))

    result = bounded_traversal(
        store, [base], [RelationshipType.IMPLEMENTS], depth=1, max_nodes=100, max_edges=100
    )
    assert [(r.subject, r.object) for r in result.relationships] == [("impl", "base")]


def test_directional_anchoring_is_deterministic_across_repeated_execution() -> None:
    store = _store()
    foo = _entity("foo")
    fooimpl = _entity("fooimpl")
    caller1 = _entity("caller1")
    caller2 = _entity("caller2")
    somethingelse = _entity("somethingelse")
    for e in (foo, fooimpl, caller1, caller2, somethingelse):
        store.upsert_entity(e)
    store.upsert_relationship(_edge("caller1", RelationshipType.CALLS, "foo"))
    store.upsert_relationship(_edge("caller2", RelationshipType.CALLS, "fooimpl"))
    store.upsert_relationship(_edge("fooimpl", RelationshipType.CALLS, "somethingelse"))

    results = [
        bounded_traversal(
            store, [foo, fooimpl], [RelationshipType.CALLS], depth=1, max_nodes=100, max_edges=100
        )
        for _ in range(5)
    ]
    first = [(r.subject, r.object) for r in results[0].relationships]
    for other in results[1:]:
        assert [(r.subject, r.object) for r in other.relationships] == first


# --- `reverse_directional` (Query-Shaped Evidence Retrieval milestone,
# task #127 real-measurement finding): `Intent.TRACE_EXECUTION`'s "what
# does X call next" question needs the opposite anchoring from every
# other directional-predicate consumer above -- these tests mirror the
# default-mode tests immediately above them, with `reverse_directional=
# True`, to make the contrast explicit. ---------------------------------


def test_reverse_directional_collects_seed_outbound_edge() -> None:
    """Mirrors `test_reverse_direction_calls_excluded_from_find_callers`
    exactly, with `reverse_directional=True`: the same `target -> other`
    edge that mode correctly excludes (wrong direction for "who calls
    target") is exactly what "what does target call" needs, and must now
    be collected."""
    store = _store()
    target = _entity("target")
    other = _entity("other")
    store.upsert_entity(target)
    store.upsert_entity(other)
    store.upsert_relationship(_edge("target", RelationshipType.CALLS, "other"))

    result = bounded_traversal(
        store,
        [target],
        [RelationshipType.CALLS],
        depth=1,
        max_nodes=100,
        max_edges=100,
        reverse_directional=True,
    )
    assert [(r.subject, r.object) for r in result.relationships] == [("target", "other")]


def test_reverse_directional_inbound_edge_to_seed_still_excluded() -> None:
    """The flip is a swap, not an addition: in `reverse_directional`
    mode, an inbound ("someone calls target") edge must NOT also be kept
    -- only target's own outbound edge answers "what does target call"."""
    store = _store()
    target = _entity("target")
    caller = _entity("caller")
    store.upsert_entity(target)
    store.upsert_entity(caller)
    store.upsert_relationship(_edge("caller", RelationshipType.CALLS, "target"))

    result = bounded_traversal(
        store,
        [target],
        [RelationshipType.CALLS],
        depth=1,
        max_nodes=100,
        max_edges=100,
        reverse_directional=True,
    )
    assert result.relationships == []


def test_reverse_directional_enables_multihop_chain_via_hop_expanded_neighbor() -> None:
    """Mirrors `test_unrelated_calls_edge_between_hop_expanded_neighbors_
    excluded` exactly, with `reverse_directional=True`: real multi-hop
    execution tracing ("A calls B calls C") needs B's own outbound edge
    to reach C, even though B is only a hop-expanded neighbor, never one
    of the original `seeds` -- the exact edge the default (seed-only)
    mode correctly excludes for `"what calls X"`-shaped queries must now
    be included for `"what happens when X runs"`-shaped ones."""
    store = _store()
    hub = _entity("hub")
    neighbor_a = _entity("neighbor_a")
    neighbor_b = _entity("neighbor_b")
    for e in (hub, neighbor_a, neighbor_b):
        store.upsert_entity(e)
    store.upsert_relationship(_edge("hub", RelationshipType.CALLS, "neighbor_a"))
    store.upsert_relationship(_edge("neighbor_a", RelationshipType.CALLS, "neighbor_b"))

    result = bounded_traversal(
        store,
        [hub],
        [RelationshipType.CALLS],
        depth=2,
        max_nodes=100,
        max_edges=100,
        reverse_directional=True,
    )

    kept = {(r.subject, r.object) for r in result.relationships}
    assert kept == {("hub", "neighbor_a"), ("neighbor_a", "neighbor_b")}


def test_reverse_directional_does_not_affect_non_directional_predicates() -> None:
    """REFERENCES is not in `_DIRECTIONAL_PREDICATES` -- it already
    collects both directions from every frontier entity regardless, so
    `reverse_directional` must be a complete no-op for it."""
    store = _store()
    a = _entity("a")
    b = _entity("b")
    store.upsert_entity(a)
    store.upsert_entity(b)
    store.upsert_relationship(_edge("a", RelationshipType.REFERENCES, "b"))

    default_mode = bounded_traversal(
        store, [a], [RelationshipType.REFERENCES], depth=1, max_nodes=100, max_edges=100
    )
    reverse_mode = bounded_traversal(
        store,
        [a],
        [RelationshipType.REFERENCES],
        depth=1,
        max_nodes=100,
        max_edges=100,
        reverse_directional=True,
    )
    assert [(r.subject, r.object) for r in default_mode.relationships] == [
        (r.subject, r.object) for r in reverse_mode.relationships
    ]


def test_reverse_directional_defaults_to_false_and_is_backward_compatible() -> None:
    """Every existing call site (`codex.evaluation.observer`,
    `codex.api.service`, every other test in this file) omits
    `reverse_directional` entirely -- confirms the parameter's default
    reproduces the exact pre-milestone inbound-to-seed behavior."""
    store = _store()
    target = _entity("target")
    other = _entity("other")
    store.upsert_entity(target)
    store.upsert_entity(other)
    store.upsert_relationship(_edge("target", RelationshipType.CALLS, "other"))

    explicit_default = bounded_traversal(
        store,
        [target],
        [RelationshipType.CALLS],
        depth=1,
        max_nodes=100,
        max_edges=100,
        reverse_directional=False,
    )
    omitted = bounded_traversal(
        store, [target], [RelationshipType.CALLS], depth=1, max_nodes=100, max_edges=100
    )
    assert explicit_default.relationships == omitted.relationships == []


# --- `supplementary_seed_predicates` (File-Level REFERENCES Traversal
# Completeness milestone): edges collected directly on the original seeds
# for these predicates, never used to expand the traversal frontier --
# real shape: `plan_query`'s truncation-recovery narrows a FIND_IMPACT
# plan to one relationship type, and this recovers the dropped types'
# direct-on-seed edges without reopening the node-budget blowup that
# caused the narrowing. -------------------------------------------------


def test_supplementary_seed_predicates_collects_seed_anchored_edge() -> None:
    store = _store()
    target = _entity("target")
    caller = _entity("caller")
    file_entity = _entity("file")
    for e in (target, caller, file_entity):
        store.upsert_entity(e)
    store.upsert_relationship(_edge("caller", RelationshipType.CALLS, "target"))
    store.upsert_relationship(_edge("file", RelationshipType.REFERENCES, "target"))

    result = bounded_traversal(
        store,
        [target],
        [RelationshipType.CALLS],
        depth=1,
        max_nodes=100,
        max_edges=100,
        supplementary_seed_predicates=(RelationshipType.REFERENCES,),
    )
    kept = {(r.subject, r.object, r.predicate) for r in result.relationships}
    assert ("caller", "target", RelationshipType.CALLS) in kept
    assert ("file", "target", RelationshipType.REFERENCES) in kept
    assert "file" in {e.canonical_id for e in result.entities}


def test_supplementary_seed_predicates_does_not_expand_frontier() -> None:
    """The defining property: a supplementary predicate never cascades
    into a second hop, regardless of `depth` -- an entity reachable only
    *through* a supplementary-predicate edge from a non-seed neighbor
    must not appear, unlike ordinary `relationship_types` edges which do
    expand the frontier."""
    store = _store()
    target = _entity("target")
    file_entity = _entity("file")
    far_entity = _entity("far")
    for e in (target, file_entity, far_entity):
        store.upsert_entity(e)
    store.upsert_relationship(_edge("file", RelationshipType.REFERENCES, "target"))
    store.upsert_relationship(_edge("far", RelationshipType.REFERENCES, "file"))

    result = bounded_traversal(
        store,
        [target],
        [RelationshipType.CALLS],
        depth=2,
        max_nodes=100,
        max_edges=100,
        supplementary_seed_predicates=(RelationshipType.REFERENCES,),
    )
    kept_ids = {e.canonical_id for e in result.entities}
    assert "file" in kept_ids
    assert "far" not in kept_ids
    assert ("far", "file", RelationshipType.REFERENCES) not in {
        (r.subject, r.object, r.predicate) for r in result.relationships
    }


def test_supplementary_seed_predicates_collects_both_directions() -> None:
    store = _store()
    target = _entity("target")
    referrer = _entity("referrer")
    referenced = _entity("referenced")
    for e in (target, referrer, referenced):
        store.upsert_entity(e)
    store.upsert_relationship(_edge("referrer", RelationshipType.REFERENCES, "target"))
    store.upsert_relationship(_edge("target", RelationshipType.REFERENCES, "referenced"))

    result = bounded_traversal(
        store,
        [target],
        [RelationshipType.CALLS],
        depth=1,
        max_nodes=100,
        max_edges=100,
        supplementary_seed_predicates=(RelationshipType.REFERENCES,),
    )
    kept = {(r.subject, r.object) for r in result.relationships}
    assert ("referrer", "target") in kept
    assert ("target", "referenced") in kept


def test_supplementary_seed_predicates_defaults_to_empty_and_is_backward_compatible() -> None:
    store = _store()
    target = _entity("target")
    file_entity = _entity("file")
    store.upsert_entity(target)
    store.upsert_entity(file_entity)
    store.upsert_relationship(_edge("file", RelationshipType.REFERENCES, "target"))

    omitted = bounded_traversal(
        store, [target], [RelationshipType.CALLS], depth=1, max_nodes=100, max_edges=100
    )
    explicit_empty = bounded_traversal(
        store,
        [target],
        [RelationshipType.CALLS],
        depth=1,
        max_nodes=100,
        max_edges=100,
        supplementary_seed_predicates=(),
    )
    assert omitted.relationships == explicit_empty.relationships == []


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
