"""Query-Level Cache tests (TAD §54-55; directive D9 Part 18 "Cache"):
graph-version isolation, schema-version isolation, policy-version
isolation, location-specific invalidation, semantic-contract behavior.
"""

from __future__ import annotations

from codex.coverage.engine import CompletenessLevel
from codex.graph.version import GraphVersion
from codex.ontology.relationships import RelationshipType
from codex.planner.cache import CacheKey, PlanCache, cache_key_for, compute_query_identity
from codex.planner.planner import plan_query
from codex.provider.capability import Capability
from codex.query_understanding.models import Intent, QueryContract
from planner_fixtures import build_graph


def _version(**overrides: object) -> GraphVersion:
    kwargs: dict[str, object] = {
        "version_id": "v1",
        "repository_id": "repo1",
        "repository_revision": "rev1",
    }
    kwargs.update(overrides)
    return GraphVersion(**kwargs)


def test_compute_query_identity_is_deterministic() -> None:
    contract = QueryContract(
        intent=Intent.FIND_CALLERS,
        targets=["auth.py"],
        complexity=0.3,
        ambiguity=0.1,
        confidence=0.97,
        token_budget=4000,
        latency_budget_ms=5000,
    )
    assert compute_query_identity(contract) == compute_query_identity(contract.model_copy())


def test_compute_query_identity_differs_on_any_field_change() -> None:
    base = QueryContract(
        intent=Intent.FIND_CALLERS,
        targets=["auth.py"],
        complexity=0.3,
        ambiguity=0.1,
        confidence=0.97,
        token_budget=4000,
        latency_budget_ms=5000,
    )
    changed = base.model_copy(update={"targets": ["billing.py"]})
    assert compute_query_identity(base) != compute_query_identity(changed)


def test_cache_key_isolates_on_graph_version() -> None:
    identity = "q1"
    key_a = cache_key_for(graph_version=_version(version_id="v1"), query_identity=identity)
    key_b = cache_key_for(graph_version=_version(version_id="v2"), query_identity=identity)
    assert key_a != key_b


def test_cache_key_isolates_on_schema_version() -> None:
    identity = "q1"
    key_a = cache_key_for(graph_version=_version(schema_version="1.0"), query_identity=identity)
    key_b = cache_key_for(graph_version=_version(schema_version="2.0"), query_identity=identity)
    assert key_a != key_b


def test_cache_key_isolates_on_policy_version() -> None:
    identity = "q1"
    key_a = cache_key_for(graph_version=_version(policy_version="1.0"), query_identity=identity)
    key_b = cache_key_for(graph_version=_version(policy_version="2.0"), query_identity=identity)
    assert key_a != key_b


def test_cache_key_isolates_on_repository() -> None:
    identity = "q1"
    key_a = cache_key_for(graph_version=_version(repository_id="repo1"), query_identity=identity)
    key_b = cache_key_for(graph_version=_version(repository_id="repo2"), query_identity=identity)
    assert key_a != key_b


def test_plan_cache_get_put_roundtrip() -> None:
    from codex.planner.models import (
        BudgetTrace,
        PlanStatus,
        PlanTelemetry,
        RetrievalBudget,
        RetrievalPlan,
    )

    cache = PlanCache()
    key = CacheKey("repo1", "v1", "1.0", "1.0", "q1")
    assert cache.get(key) is None

    plan = RetrievalPlan(
        query_identity="q1",
        graph_version=_version(),
        traversal_depth=0,
        completeness_requirement=CompletenessLevel.LOW,
        budget=RetrievalBudget(
            token_budget=4000, latency_budget_ms=5000, max_nodes=10, max_edges=10
        ),
        ranking_strategy="tad_v1_four_signal",
        status=PlanStatus.OK,
        telemetry=PlanTelemetry(
            graph_version_id="v1",
            budget_trace=BudgetTrace(
                original_node_estimate=0,
                original_edge_estimate=0,
                pruned_node_estimate=0,
                pruned_edge_estimate=0,
            ),
        ),
    )
    cache.put(key, plan)
    assert cache.get(key) is plan


def _contract(**overrides: object) -> QueryContract:
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


def test_plan_query_cache_hit_on_identical_repeated_call() -> None:
    from codex.planner.cache import PlanCache

    result, registry, _, repository = build_graph(entity_paths=("auth.py",))
    cache = PlanCache()
    contract = _contract()
    first = plan_query(
        query_contract=contract,
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
        cache=cache,
    )
    second = plan_query(
        query_contract=contract,
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
        cache=cache,
    )
    assert first.telemetry.cache_hit is False
    assert second.telemetry.cache_hit is True


def test_plan_query_cache_miss_on_different_query_same_version() -> None:
    """Location-specific / semantic-contract behavior (TAD §54): a
    different query against the *same* graph_version is correctly a
    cache miss -- the query identity is part of the key, so results
    are never confused across queries even when nothing about the graph
    changed."""
    from codex.planner.cache import PlanCache

    result, registry, _, repository = build_graph(
        entity_paths=("auth.py", "billing.py"),
    )
    cache = PlanCache()
    first = plan_query(
        query_contract=_contract(targets=["auth.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
        cache=cache,
    )
    second = plan_query(
        query_contract=_contract(targets=["billing.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
        cache=cache,
    )
    assert first.telemetry.cache_hit is False
    assert second.telemetry.cache_hit is False
    assert first.target_entity_ids != second.target_entity_ids
