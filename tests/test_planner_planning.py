"""Behavioral tests for `plan_query()` (TAD §29; directive D9 Part 18
"Planning"): simple query, multi-target query, relationship traversal,
depth control, deterministic repeatability.
"""

from __future__ import annotations

from codex.coverage.engine import CompletenessLevel
from codex.ontology.relationships import RelationshipType
from codex.planner.models import PlanStatus
from codex.planner.planner import plan_query
from codex.provider.capability import Capability
from codex.query_understanding.models import Intent, QueryContract
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


def test_simple_query_resolves_target_and_returns_ok() -> None:
    result, registry, _, repository = build_graph(
        entity_paths=("service.py", "auth.py"),
        relationship_pairs=(("service.py", "auth.py"),),
    )
    plan = plan_query(
        query_contract=make_contract(targets=["auth.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.status is PlanStatus.OK
    assert len(plan.target_entity_ids) == 1


def test_multi_target_query_resolves_every_target() -> None:
    result, registry, _, repository = build_graph(
        entity_paths=("service.py", "auth.py", "billing.py"),
        relationship_pairs=(("service.py", "auth.py"), ("service.py", "billing.py")),
    )
    plan = plan_query(
        query_contract=make_contract(targets=["auth.py", "billing.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert len(plan.target_entity_ids) == 2


def test_relationship_traversal_follows_requested_predicate_only() -> None:
    result, registry, _, repository = build_graph(
        entity_paths=("service.py", "auth.py"),
        relationship_pairs=(("service.py", "auth.py"),),
        predicate=RelationshipType.CALLS,
    )
    plan = plan_query(
        query_contract=make_contract(
            targets=["service.py"], relationship_types=[RelationshipType.IMPORTS]
        ),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    # CALLS edges exist but IMPORTS was requested -- traversal finds nothing.
    assert plan.telemetry.budget_trace.pruned_edge_estimate == 0


def test_depth_control_lookup_intent_uses_depth_zero() -> None:
    result, registry, _, repository = build_graph(entity_paths=("auth.py",))
    plan = plan_query(
        query_contract=make_contract(intent=Intent.CODE_LOOKUP, targets=["auth.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.traversal_depth == 0


def test_depth_control_impact_intent_uses_depth_two() -> None:
    result, registry, _, repository = build_graph(entity_paths=("auth.py",))
    plan = plan_query(
        query_contract=make_contract(intent=Intent.FIND_IMPACT, targets=["auth.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.traversal_depth == 2


def test_depth_control_find_references_intent_uses_depth_one() -> None:
    """GAP-5 fix: `Intent.FIND_REFERENCES` gets the same single-hop
    depth as its relationship-seeking siblings (FIND_CALLERS,
    FIND_IMPLEMENTATIONS, FIND_TESTS, FIND_DEPENDENCIES)."""
    result, registry, _, repository = build_graph(entity_paths=("auth.py",))
    plan = plan_query(
        query_contract=make_contract(intent=Intent.FIND_REFERENCES, targets=["auth.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.traversal_depth == 1


def test_deterministic_repeatability_same_input_same_plan() -> None:
    result, registry, _, repository = build_graph(
        entity_paths=("service.py", "auth.py"),
        relationship_pairs=(("service.py", "auth.py"),),
    )
    contract = make_contract(targets=["auth.py"])
    first = plan_query(
        query_contract=contract,
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    second = plan_query(
        query_contract=contract,
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert first.model_dump(exclude={"telemetry"}) == second.model_dump(exclude={"telemetry"})
