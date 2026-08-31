"""Budget-aware planning tests (TAD §32, §41; directive D9 Part 18
"Budget"): plan within budget, latency/token over-budget pruning,
minimal plan blocked, EXHAUSTIVE cannot be over-pruned, PRUNED telemetry.
"""

from __future__ import annotations

from codex.coverage.engine import CompletenessLevel
from codex.ontology.relationships import RelationshipType
from codex.planner.cache import PlanCache
from codex.planner.models import PlanStatus
from codex.planner.planner import execute_query, plan_query
from codex.provider.capability import Capability
from codex.query_understanding.models import Intent, QueryContract
from planner_fixtures import build_graph


def make_contract(**overrides: object) -> QueryContract:
    kwargs: dict[str, object] = {
        "intent": Intent.FIND_CALLERS,
        "targets": ["service.py"],
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


def _fan_out_graph():
    return build_graph(
        entity_paths=("service.py", "auth.py", "billing.py", "cache.py"),
        relationship_pairs=(
            ("service.py", "auth.py"),
            ("service.py", "billing.py"),
            ("service.py", "cache.py"),
        ),
    )


def test_plan_within_budget_is_ok_with_no_pruning() -> None:
    result, registry, _, repository = _fan_out_graph()
    plan = plan_query(
        query_contract=make_contract(),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.status is PlanStatus.OK
    assert plan.telemetry.budget_trace.pruning_occurred is False


def test_latency_over_budget_prunes_traversal_depth() -> None:
    result, registry, _, repository = _fan_out_graph()
    plan = plan_query(
        query_contract=make_contract(intent=Intent.FIND_IMPACT, latency_budget_ms=1000),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.status is PlanStatus.PRUNED
    assert plan.traversal_depth == 0  # base depth 2, latency affords 1000//1500 = 0
    assert "reduce traversal depth" in plan.telemetry.budget_trace.pruning_steps


def test_token_over_budget_truncates_and_prunes() -> None:
    result, registry, _, repository = _fan_out_graph()
    plan = plan_query(
        query_contract=make_contract(token_budget=100),  # max_nodes=2, max_edges=5
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.status is PlanStatus.PRUNED
    assert plan.telemetry.budget_trace.pruning_occurred is True
    assert plan.telemetry.budget_trace.pruned_node_estimate <= plan.budget.max_nodes


def test_relationship_type_removal_step_when_multiple_types_requested() -> None:
    result, registry, _, repository = _fan_out_graph()
    plan = plan_query(
        query_contract=make_contract(
            token_budget=100,
            relationship_types=[RelationshipType.CALLS, RelationshipType.IMPORTS],
        ),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert "remove optional relationship types" in plan.telemetry.budget_trace.pruning_steps
    assert plan.relationship_types == [RelationshipType.CALLS]


def test_minimal_plan_blocked_when_budget_cannot_support_targets() -> None:
    result, registry, evidence_store, repository = _fan_out_graph()
    cache = PlanCache()
    plan = plan_query(
        query_contract=make_contract(token_budget=10),  # max_nodes = 10//50 = 0
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
        cache=cache,  # exercises the cache.put() path on the PLAN_UNSUPPORTED branch
    )
    assert plan.status is PlanStatus.PLAN_UNSUPPORTED
    assert plan.telemetry.budget_trace.reason is not None

    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    assert package.partial is True
    assert package.entities == []


def test_exhaustive_query_is_blocked_not_silently_pruned_on_latency() -> None:
    result, registry, evidence_store, repository = _fan_out_graph()
    cache = PlanCache()
    plan = plan_query(
        query_contract=make_contract(
            intent=Intent.FIND_IMPACT,
            latency_budget_ms=1000,
            completeness_requirement=CompletenessLevel.EXHAUSTIVE,
        ),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
        cache=cache,  # exercises the cache.put() path inside _blocked_plan()
    )
    assert plan.status is PlanStatus.PLAN_BLOCKED
    assert plan.telemetry.budget_trace.pruning_occurred is False  # blocked, never silently pruned

    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    assert package.partial is True


def test_exhaustive_query_is_blocked_not_silently_pruned_on_token_truncation() -> None:
    result, registry, _, repository = _fan_out_graph()
    plan = plan_query(
        query_contract=make_contract(
            token_budget=100, completeness_requirement=CompletenessLevel.EXHAUSTIVE
        ),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.status is PlanStatus.PLAN_BLOCKED


def test_pruned_telemetry_records_original_and_pruned_estimates() -> None:
    result, registry, _, repository = _fan_out_graph()
    plan = plan_query(
        query_contract=make_contract(token_budget=100),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    trace = plan.telemetry.budget_trace
    assert trace.original_node_estimate >= trace.pruned_node_estimate
    assert trace.reason is not None
    assert trace.pruning_steps != []
