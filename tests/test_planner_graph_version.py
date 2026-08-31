"""Graph-version lock tests (TAD §19-20, §55; directive D9 Part 18
"Graph version"): version captured, version propagated, downstream
operations use same version, concurrent-update behavior per spec.
"""

from __future__ import annotations

import pytest

from codex.coverage.engine import CompletenessLevel
from codex.ontology.relationships import RelationshipType
from codex.planner.planner import GraphVersionMismatchError, execute_query, plan_query
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


def test_graph_version_is_captured_into_the_plan() -> None:
    result, registry, _, repository = build_graph(entity_paths=("auth.py",))
    plan = plan_query(
        query_contract=make_contract(),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.graph_version.version_id == result.graph_version.version_id
    assert plan.graph_version.published is True


def test_graph_version_is_propagated_into_telemetry() -> None:
    result, registry, _, repository = build_graph(entity_paths=("auth.py",))
    plan = plan_query(
        query_contract=make_contract(),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.telemetry.graph_version_id == result.graph_version.version_id


def test_execute_query_uses_the_same_locked_version() -> None:
    result, registry, evidence_store, repository = build_graph(
        entity_paths=("service.py", "auth.py"), relationship_pairs=(("service.py", "auth.py"),)
    )
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
    assert package.graph_version.version_id == plan.graph_version.version_id


def test_concurrent_graph_update_does_not_change_an_in_flight_plan() -> None:
    """A new ingestion run against a changed revision publishes a *new*
    GraphStore instance with a distinct GraphVersion (D4's deterministic
    `version_id` composite key changes with revision); the already-locked
    plan's `graph_version` is unaffected (TAD §20: "Concurrent graph
    updates do not change an active query")."""
    result, registry, evidence_store, repository = build_graph(
        entity_paths=("service.py", "auth.py"), relationship_pairs=(("service.py", "auth.py"),)
    )
    plan = plan_query(
        query_contract=make_contract(targets=["auth.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    locked_version_id = plan.graph_version.version_id

    # Simulate a concurrent update: the repository moves to a new revision
    # and ingestion re-runs, publishing a new, distinct GraphVersion.
    from codex.ingestion.pipeline import IngestionPipeline

    pipeline = IngestionPipeline(registry, evidence_store)
    moved_repository = repository.model_copy(update={"head_revision": "rev2"})
    new_result = pipeline.run(moved_repository)

    assert new_result.graph_version.version_id != locked_version_id
    assert plan.graph_version.version_id == locked_version_id  # unchanged


def test_execute_query_against_a_different_version_raises_concurrent_update() -> None:
    result, registry, evidence_store, repository = build_graph(
        entity_paths=("service.py", "auth.py"), relationship_pairs=(("service.py", "auth.py"),)
    )
    plan = plan_query(
        query_contract=make_contract(targets=["auth.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )

    from codex.ingestion.pipeline import IngestionPipeline

    pipeline = IngestionPipeline(registry, evidence_store)
    moved_repository = repository.model_copy(update={"head_revision": "rev2"})
    new_result = pipeline.run(moved_repository)

    with pytest.raises(GraphVersionMismatchError, match="CONCURRENT_UPDATE_DETECTED"):
        execute_query(
            plan,
            graph=new_result.graph_store,
            evidence_store=evidence_store,
            ingestion_result=new_result,
        )


def test_cache_hit_telemetry_reflects_reuse_not_a_new_lock() -> None:
    from codex.planner.cache import PlanCache

    result, registry, _, repository = build_graph(entity_paths=("auth.py",))
    cache = PlanCache()
    contract = make_contract()
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
    assert second.graph_version.version_id == first.graph_version.version_id
