"""Query Planner / Retrieval data model (TAD §19-20, §29-42; directive D9).

`RetrievalPlan` is DTD-03's output (TAD §29): the Planner's decisions about
providers, capabilities, graph traversal, evidence types, depth, budget,
completeness, and stopping criteria, transformed from a `QueryContract`.
Every field traces to `docs/architecture-conformance-audit.md` §R.7 — no
speculative field was added.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from codex.coverage.engine import CompletenessLevel, NegativeQueryCoverage
from codex.graph.version import GraphVersion
from codex.ontology.relationships import RelationshipType
from codex.provider.capability import Capability


class PlanStatus(StrEnum):
    """TAD §32's `PLAN_BLOCKED` and §41's `PLAN_UNSUPPORTED` are distinct
    outcomes, not synonyms (§R.6): `PLAN_UNSUPPORTED` means the budget
    itself cannot support even a minimally viable evidence package (TAD
    §41); `PLAN_BLOCKED` means budget-aware pruning (TAD §32) was applied
    and no compliant plan still exists. `OK`/`PRUNED` are the two
    successful outcomes -- `PRUNED` only when pruning actually occurred."""

    OK = "OK"
    PRUNED = "PRUNED"
    PLAN_BLOCKED = "PLAN_BLOCKED"
    PLAN_UNSUPPORTED = "PLAN_UNSUPPORTED"


class RetrievalBudget(BaseModel):
    """TAD §41's evidence-volume ceiling derived from `token_budget`, plus
    the raw budget inputs the Planner was given (TAD §27/§32)."""

    token_budget: int = Field(gt=0)
    latency_budget_ms: int = Field(gt=0)
    max_nodes: int = Field(ge=0)
    max_edges: int = Field(ge=0)


class BudgetTrace(BaseModel):
    """TAD §32's exact required record: "original estimate, pruned
    estimate, whether pruning occurred, reason for pruning"."""

    original_node_estimate: int = Field(ge=0)
    original_edge_estimate: int = Field(ge=0)
    pruned_node_estimate: int = Field(ge=0)
    pruned_edge_estimate: int = Field(ge=0)
    pruning_occurred: bool = False
    pruning_steps: list[str] = Field(default_factory=list)
    reason: str | None = None


class PlanTelemetry(BaseModel):
    """Directive Part 4 item 5 (expose the selected version in telemetry)
    and Part 7 (budget trace); TAD §55 (`CONCURRENT_UPDATE_DETECTED`)."""

    graph_version_id: str
    budget_trace: BudgetTrace
    concurrent_update_detected: bool = False
    cache_hit: bool = False


class RetrievalPlan(BaseModel):
    """DTD-03's output (TAD §29). See `docs/architecture-conformance-
    audit.md` §R.7 for each field's exact traceability."""

    query_identity: str
    graph_version: GraphVersion
    target_entity_ids: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    relationship_types: list[RelationshipType] = Field(default_factory=list)
    traversal_depth: int = Field(ge=0)
    required_capabilities: list[Capability] = Field(default_factory=list)
    selected_providers: dict[str, list[str]] = Field(default_factory=dict)
    completeness_requirement: CompletenessLevel
    budget: RetrievalBudget
    ranking_strategy: str
    stop_conditions: list[str] = Field(default_factory=list)
    negative_query_candidate: bool = False
    negative_query_result: NegativeQueryCoverage | None = None
    trace_forward: bool = False
    """Query-Shaped Evidence Retrieval milestone (task #127): True only
    for `Intent.TRACE_EXECUTION` plans. Tells `bounded_traversal`
    (`codex.planner.retrieval`) to anchor `_DIRECTIONAL_PREDICATES`
    (`CALLS`, `IMPLEMENTS`) collection on each seed's *outbound* edges
    ("what does X call next") instead of the default inbound ("who
    calls X") -- real measurement against 5 independently-selected
    repositories confirmed the default direction returns none of the
    validated required edges for "what happens when X runs"/"trace what
    happens from X" queries, whose real edges run from the seed outward.
    Derived once, deterministically, from `QueryContract.intent` in
    `plan_query`; every other intent keeps the default `False` (byte-
    identical prior behavior)."""
    status: PlanStatus
    telemetry: PlanTelemetry


__all__ = [
    "BudgetTrace",
    "PlanStatus",
    "PlanTelemetry",
    "RetrievalBudget",
    "RetrievalPlan",
]
