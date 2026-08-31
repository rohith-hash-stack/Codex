"""Behavioral tests for `codex.evaluation.observer.observe_ranked_
candidates` (directive D13-B): captures D9's real ranked candidate
list faithfully, never alters it, never touches retrieval decisions,
GraphVersion, EvidencePackage, or Evidence.
"""

from __future__ import annotations

import inspect

from codex.evaluation.observer import observe_ranked_candidates
from codex.ontology.relationships import RelationshipType
from codex.planner.models import PlanStatus
from codex.planner.planner import execute_query, plan_query
from codex.planner.ranking import rank_entities
from codex.planner.retrieval import bounded_traversal
from codex.provider.capability import Capability
from codex.query_understanding.models import CompletenessLevel, Intent, QueryContract
from planner_fixtures import build_graph


def make_contract(**overrides: object) -> QueryContract:
    kwargs: dict[str, object] = {
        "intent": Intent.FIND_CALLERS,
        "targets": ["auth.py"],
        "relationship_types": [RelationshipType.CALLS],
        "complexity": 0.3,
        "ambiguity": 0.1,
        "confidence": 0.97,
        "completeness_requirement": CompletenessLevel.LOW,
        "required_evidence": [Capability.CALL_RELATIONSHIP],
        "token_budget": 4000,
        "latency_budget_ms": 5000,
    }
    kwargs.update(overrides)
    return QueryContract(**kwargs)


def _build_multi_caller_graph():  # type: ignore[no-untyped-def]
    """A graph with three callers of one target, giving `rank_entities`
    non-trivial (not all-tied) signals to order by."""
    return build_graph(
        entity_paths=("auth.py", "service_a.py", "service_b.py", "service_c.py"),
        relationship_pairs=(
            ("service_a.py", "auth.py"),
            ("service_b.py", "auth.py"),
            ("service_c.py", "auth.py"),
        ),
    )


# --- captures the real, unaltered D9 ranked output --------------------------


def test_observer_captures_the_same_entities_execute_query_returns() -> None:
    """No SOURCE_LOCATION requested -> `EvidencePackage.entities` is
    `ranked_entities` unchanged (TAD §40 expansion never triggers), so
    a direct order comparison is unambiguous."""
    result, registry, evidence_store, repository = _build_multi_caller_graph()
    plan = plan_query(
        query_contract=make_contract(targets=["auth.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    trace = observe_ranked_candidates(plan, result.graph_store)

    assert [c.entity_id for c in trace.ordered_candidates] == [
        e.canonical_id for e in package.entities
    ]


def test_candidate_scores_match_rank_entities_called_directly() -> None:
    """Proves scores are D9's real, deterministic scores -- not
    invented -- by independently calling `rank_entities` with the same
    reconstructed inputs and comparing score-for-score."""
    result, registry, _evidence_store, repository = _build_multi_caller_graph()
    plan = plan_query(
        query_contract=make_contract(targets=["auth.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    graph = result.graph_store
    target_entities = [graph.get_entity(cid) for cid in plan.target_entity_ids]
    traversal = bounded_traversal(
        graph,
        [e for e in target_entities if e is not None],
        plan.relationship_types,
        plan.traversal_depth,
        plan.budget.max_nodes,
        plan.budget.max_edges,
    )
    query_terms = [
        text
        for e in target_entities
        if e is not None
        for text in (e.name, e.qualified_name)
    ]
    expected = rank_entities(
        entities=traversal.entities,
        relationships=traversal.relationships,
        distances=traversal.distances,
        query_targets=query_terms,
        query_constraints=plan.constraints,
        primary_relationship_type=plan.relationship_types[0] if plan.relationship_types else None,
    )

    trace = observe_ranked_candidates(plan, graph)

    assert [c.score for c in trace.ordered_candidates] == [score for _entity, score in expected]
    assert [c.entity_id for c in trace.ordered_candidates] == [
        entity.canonical_id for entity, _score in expected
    ]


def test_candidate_ids_are_canonical_entity_ids() -> None:
    result, registry, _evidence_store, repository = _build_multi_caller_graph()
    plan = plan_query(
        query_contract=make_contract(targets=["auth.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    trace = observe_ranked_candidates(plan, result.graph_store)
    real_ids = {e.canonical_id for e in result.graph_store.find_entities()}
    assert trace.ordered_candidates
    assert all(c.entity_id in real_ids for c in trace.ordered_candidates)


def test_rank_is_1_based_sequential_and_deterministic() -> None:
    result, registry, _evidence_store, repository = _build_multi_caller_graph()
    plan = plan_query(
        query_contract=make_contract(targets=["auth.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    trace = observe_ranked_candidates(plan, result.graph_store)
    ranks = [c.rank for c in trace.ordered_candidates]
    assert ranks == list(range(1, len(ranks) + 1))


def test_observer_is_deterministic_across_repeated_calls() -> None:
    result, registry, _evidence_store, repository = _build_multi_caller_graph()
    plan = plan_query(
        query_contract=make_contract(targets=["auth.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    trace1 = observe_ranked_candidates(plan, result.graph_store)
    trace2 = observe_ranked_candidates(plan, result.graph_store)
    assert trace1 == trace2


# --- empty / blocked / unsupported handling ----------------------------------


def test_empty_candidate_set_when_target_resolves_to_nothing() -> None:
    result, registry, _evidence_store, repository = build_graph(entity_paths=("auth.py",))
    plan = plan_query(
        query_contract=make_contract(targets=["does-not-exist.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    trace = observe_ranked_candidates(plan, result.graph_store)
    assert trace.ordered_candidates == []


def test_plan_blocked_status_produces_a_deterministic_empty_trace() -> None:
    result, registry, _evidence_store, repository = _build_multi_caller_graph()
    plan = plan_query(
        query_contract=make_contract(targets=["auth.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    blocked = plan.model_copy(update={"status": PlanStatus.PLAN_BLOCKED})
    trace = observe_ranked_candidates(blocked, result.graph_store)
    assert trace.ordered_candidates == []


def test_plan_unsupported_status_produces_a_deterministic_empty_trace() -> None:
    result, registry, _evidence_store, repository = _build_multi_caller_graph()
    plan = plan_query(
        query_contract=make_contract(targets=["auth.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    unsupported = plan.model_copy(update={"status": PlanStatus.PLAN_UNSUPPORTED})
    trace = observe_ranked_candidates(unsupported, result.graph_store)
    assert trace.ordered_candidates == []


def test_duplicate_candidate_ids_never_occur_matching_bounded_traversals_own_dedup() -> None:
    """`bounded_traversal`'s `visited` dict already deduplicates by
    canonical_id (existing D9 semantics, not reinterpreted here) --
    even with two independent paths to the same neighbor, it appears
    once."""
    result, registry, _evidence_store, repository = build_graph(
        entity_paths=("auth.py", "service_a.py", "service_b.py"),
        relationship_pairs=(("service_a.py", "auth.py"), ("service_b.py", "auth.py")),
    )
    plan = plan_query(
        query_contract=make_contract(targets=["auth.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    trace = observe_ranked_candidates(plan, result.graph_store)
    ids = [c.entity_id for c in trace.ordered_candidates]
    assert len(ids) == len(set(ids))


# --- graph-version identity preserved ----------------------------------------


def test_graph_version_id_matches_the_plans_own_locked_version() -> None:
    result, registry, _evidence_store, repository = _build_multi_caller_graph()
    plan = plan_query(
        query_contract=make_contract(targets=["auth.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    trace = observe_ranked_candidates(plan, result.graph_store)
    assert trace.graph_version_id == plan.graph_version.version_id
    assert trace.repository_id == plan.graph_version.repository_id


# --- cannot alter/mutate anything --------------------------------------------


def test_observer_never_mutates_the_plan_or_graph_version() -> None:
    result, registry, _evidence_store, repository = _build_multi_caller_graph()
    plan = plan_query(
        query_contract=make_contract(targets=["auth.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    plan_before = plan.model_copy(deep=True)
    graph_version_before = result.graph_store.version.model_copy(deep=True)

    observe_ranked_candidates(plan, result.graph_store)

    assert plan == plan_before
    assert result.graph_store.version == graph_version_before


def test_calling_observer_does_not_change_a_subsequent_execute_query_result() -> None:
    """Proves the observer cannot influence D9's own real retrieval
    decision -- calling it before `execute_query` produces the exact
    same `EvidencePackage` as never calling it at all."""
    result, registry, evidence_store, repository = _build_multi_caller_graph()
    plan = plan_query(
        query_contract=make_contract(targets=["auth.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    package_without_observer = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )

    observe_ranked_candidates(plan, result.graph_store)
    observe_ranked_candidates(plan, result.graph_store)  # called more than once, for good measure

    package_with_observer = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    assert package_without_observer == package_with_observer


def test_observer_signature_has_no_evidence_store_parameter() -> None:
    """Structural proof it cannot create or modify Evidence: the
    function does not even accept an `EvidenceStore`."""
    params = inspect.signature(observe_ranked_candidates).parameters
    assert "evidence_store" not in params


def test_observer_signature_has_no_registry_or_repository_parameter() -> None:
    """Structural proof it never performs provider selection: the
    function does not accept a `CapabilityRegistry` or
    `RepositoryMetadata`."""
    params = inspect.signature(observe_ranked_candidates).parameters
    assert "registry" not in params
    assert "repository" not in params


def test_observer_signature_has_exactly_plan_and_graph() -> None:
    params = list(inspect.signature(observe_ranked_candidates).parameters)
    assert params == ["plan", "graph"]
