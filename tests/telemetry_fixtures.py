"""Shared fixtures for `codex.telemetry` tests."""

from __future__ import annotations

from codex.coverage.engine import CompletenessLevel
from codex.graph.version import GraphVersion
from codex.ontology.relationships import RelationshipType
from codex.planner.models import (
    BudgetTrace,
    PlanStatus,
    PlanTelemetry,
    RetrievalBudget,
    RetrievalPlan,
)
from codex.provider.capability import Capability
from codex.query_understanding.models import Intent, QueryContract


def make_graph_version(version_id: str = "repo1:rev1:scip=1.0.0") -> GraphVersion:
    return GraphVersion(
        version_id=version_id,
        repository_id="repo1",
        repository_revision="rev1",
        provider_versions={"scip": "1.0.0"},
        published=True,
    )


def make_contract() -> QueryContract:
    return QueryContract(
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


def make_plan(graph_version: GraphVersion) -> RetrievalPlan:
    return RetrievalPlan(
        query_identity="q1",
        graph_version=graph_version,
        target_entity_ids=["codex:abc"],
        completeness_requirement=CompletenessLevel.LOW,
        traversal_depth=1,
        selected_providers={"CALL_RELATIONSHIP": ["scip"]},
        budget=RetrievalBudget(
            token_budget=4000, latency_budget_ms=5000, max_nodes=100, max_edges=250
        ),
        ranking_strategy="TAD_36_37_v1",
        status=PlanStatus.OK,
        telemetry=PlanTelemetry(
            graph_version_id=graph_version.version_id,
            budget_trace=BudgetTrace(
                original_node_estimate=3,
                original_edge_estimate=2,
                pruned_node_estimate=3,
                pruned_edge_estimate=2,
            ),
        ),
    )
