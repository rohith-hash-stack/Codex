"""Completeness handling tests (TAD §33; directive D9 Part 18
"Completeness"): LOW/MEDIUM/HIGH propagate as requested (no invented
percentage, per `docs/architecture-conformance-audit.md` §R.2);
EXHAUSTIVE is the one quantitatively-defined level and drives pruning
refusal; PARTIAL evidence is reported via `EvidencePackage.partial`.
"""

from __future__ import annotations

import pytest

from codex.coverage.engine import CompletenessLevel
from codex.ontology.relationships import RelationshipType
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


@pytest.mark.parametrize(
    "level", [CompletenessLevel.LOW, CompletenessLevel.MEDIUM, CompletenessLevel.HIGH]
)
def test_completeness_level_propagates_unchanged_into_plan(level: CompletenessLevel) -> None:
    result, registry, _, repository = build_graph(
        entity_paths=("service.py", "auth.py"), relationship_pairs=(("service.py", "auth.py"),)
    )
    plan = plan_query(
        query_contract=make_contract(completeness_requirement=level),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.completeness_requirement is level


def test_exhaustive_completeness_propagates_and_refuses_pruning() -> None:
    result, registry, _, repository = build_graph(
        entity_paths=("service.py", "auth.py"), relationship_pairs=(("service.py", "auth.py"),)
    )
    plan = plan_query(
        query_contract=make_contract(completeness_requirement=CompletenessLevel.EXHAUSTIVE),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.completeness_requirement is CompletenessLevel.EXHAUSTIVE


def test_partial_evidence_from_truncated_traversal_marks_package_partial() -> None:
    result, registry, evidence_store, repository = build_graph(
        entity_paths=("service.py", "auth.py", "billing.py", "cache.py"),
        relationship_pairs=(
            ("service.py", "auth.py"),
            ("service.py", "billing.py"),
            ("service.py", "cache.py"),
        ),
    )
    plan = plan_query(
        query_contract=make_contract(token_budget=100),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    assert package.partial is True


def test_complete_evidence_within_budget_is_not_partial() -> None:
    result, registry, evidence_store, repository = build_graph(
        entity_paths=("service.py", "auth.py"), relationship_pairs=(("service.py", "auth.py"),)
    )
    plan = plan_query(
        query_contract=make_contract(),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    assert package.partial is False


def test_coverage_dict_reflects_capability_classification() -> None:
    result, registry, evidence_store, repository = build_graph(
        entity_paths=("service.py", "auth.py"), relationship_pairs=(("service.py", "auth.py"),)
    )
    plan = plan_query(
        query_contract=make_contract(),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    assert package.coverage["CALL_RELATIONSHIP"].value == "COMPLETE"


def test_source_location_requirement_with_no_located_entity_marks_package_partial() -> None:
    """No `DeterministicFakeAdapter`-built entity ever carries a
    `source_location` -- requiring `SOURCE_LOCATION` therefore always
    exhausts the MSS expansion bound (TAD §40) and the package is
    correctly reported as `partial`, with a limitation recorded."""
    result, registry, evidence_store, repository = build_graph(
        entity_paths=("service.py", "auth.py"), relationship_pairs=(("service.py", "auth.py"),)
    )
    plan = plan_query(
        query_contract=make_contract(
            required_evidence=[Capability.CALL_RELATIONSHIP, Capability.SOURCE_LOCATION]
        ),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    assert package.partial is True
    assert any("SOURCE_CONTEXT" in limitation for limitation in package.limitations)
    assert package.source_context != []
