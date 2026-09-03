"""Query Planner + Retrieval orchestration (TAD §19-20, §29, §32, §34-35,
§39-42; directive D9 Parts 4, 7-12).

`plan_query()` is DTD-03 (TAD §29): `QueryContract` -> `RetrievalPlan`,
locking `graph_version` at the start (TAD §20) and applying TAD §32's
five-step budget-aware pruning procedure. `execute_query()` is DTD-04
(TAD §35): executes the locked plan deterministically and assembles the
`EvidencePackage` (TAD §42).

**Boundary (TAD §30, directive Part 15):** this module never imports or
calls an LLM/SLM, never performs answer generation or verification, and
never asks "what files should I look at?" -- scope comes entirely from
`QueryContract` + graph structure (enforced by `tests/test_planner_
boundaries.py`'s AST-based import check).
"""

from __future__ import annotations

from codex.coverage.engine import (
    CompletenessLevel,
    NegativeQueryCoverage,
    classify_capability_coverage,
    evaluate_negative_query_coverage,
)
from codex.evidence.model import CanonicalRelationship
from codex.evidence.store import EvidenceStore
from codex.graph.store import GraphReader
from codex.graph.version import GraphVersion
from codex.ingestion.models import IngestionResult
from codex.ontology.relationships import RelationshipType
from codex.planner.budget import compute_budget, latency_derived_depth_ceiling
from codex.planner.cache import CacheKey, PlanCache, cache_key_for, compute_query_identity
from codex.planner.models import (
    BudgetTrace,
    PlanStatus,
    PlanTelemetry,
    RetrievalBudget,
    RetrievalPlan,
)
from codex.planner.mss import EvidencePackage, expand_for_source_context
from codex.planner.provider_selection import select_providers
from codex.planner.ranking import rank_entities
from codex.planner.retrieval import bounded_traversal, collect_evidence, resolve_targets
from codex.provider.capability import Capability
from codex.query_understanding.models import Intent, QueryContract
from codex.registry.registry import CapabilityRegistry
from codex.repository.models import RepositoryMetadata

_BASE_DEPTH_BY_INTENT: dict[Intent, int] = {
    Intent.FIND_CALLERS: 1,
    Intent.FIND_IMPLEMENTATIONS: 1,
    Intent.FIND_TESTS: 1,
    Intent.FIND_DEPENDENCIES: 1,
    Intent.FIND_REFERENCES: 1,  # GAP-5 fix: same single-hop shape as its siblings above.
    Intent.TRACE_EXECUTION: 2,
    Intent.FIND_IMPACT: 2,
    Intent.ARCHITECTURE_ANALYSIS: 2,
    Intent.CODE_LOOKUP: 0,
    Intent.HISTORY_ANALYSIS: 0,
    Intent.UNKNOWN: 0,
}
"""TAD §29 lists "traversal depth" as a Planner output without a formula
-- a documented, bounded implementation detail (directive Phase 19
category 1), not an invented architectural decision."""

_NEGATIVE_QUERY_INTENTS: frozenset[Intent] = frozenset(
    {
        Intent.FIND_CALLERS,
        Intent.FIND_IMPLEMENTATIONS,
        Intent.FIND_TESTS,
        Intent.FIND_DEPENDENCIES,
        Intent.FIND_IMPACT,
        Intent.TRACE_EXECUTION,
        # GAP-5 fix: "What references X?" is exactly the same "does X
        # relate to Y" shape as its relationship-seeking siblings above
        # -- a genuinely empty result is a legitimate negative-query
        # candidate, not silently excluded.
        Intent.FIND_REFERENCES,
    }
)
"""Relationship-seeking intents where an empty result is a candidate for
TAD §34's negative-query safety check -- lookup-shaped intents
(CODE_LOOKUP, HISTORY_ANALYSIS, ARCHITECTURE_ANALYSIS) are not "does X
relate to Y" questions and are excluded."""

_STOP_CONDITIONS = (
    "coverage_requirement_satisfied",
    "no_further_expected_value",
    "providers_exhausted",
)
"""HLRD §39's three-way stopping rule, instantiated as a fixed,
descriptive record on every plan (not a separate mechanism)."""


class GraphVersionMismatchError(ValueError):
    """Raised when `execute_query` is asked to run against a `GraphReader`
    whose `.version` differs from the one locked into `RetrievalPlan.
    graph_version` (TAD §55's `CONCURRENT_UPDATE_DETECTED`). Retrieval
    refuses rather than silently reading a different snapshot -- "no
    livelock" (TAD §55) means failing fast and deterministically, not
    retrying or blocking."""


def _prioritize_relationship_types_by_evidence(
    relationship_types: list[RelationshipType],
    observed_relationships: list[CanonicalRelationship],
) -> list[RelationshipType]:
    """GAP-11 fix: when the truncation step below has to narrow an
    over-budget query down to a single relationship type, choose the
    type with real evidence in the traversal already computed -- not
    blindly `relationship_types[0]` (`_relationship_types_for_intent`'s
    own alphabetical `sorted(types, key=lambda t: t.value)`, an
    incidental artifact of that function's determinism requirement, never
    intended as a relevance ordering).

    Root cause (Python fidelity audit, `docs/python-fidelity-gap-
    register.md`): real `scip-python` output *never* sets the SCIP
    `Import` occurrence-role bit (confirmed: 0 of 972,111 real
    occurrences across 5 repositories), so `RelationshipType.IMPORTS` is
    permanently empty for Python repositories -- and `"IMPORTS" <
    "REFERENCES"` alphabetically, so `FIND_REFERENCES` queries
    deterministically kept the one type guaranteed to have zero results
    (measured: django `QuerySet` 38->0, click `Command` 95->0). The same
    class of bug latently affects every other multi-relationship-type
    intent (`FIND_CALLERS`, `TRACE_EXECUTION`, `FIND_TESTS`,
    `FIND_IMPACT`, `ARCHITECTURE_ANALYSIS`) -- today's alphabetical order
    merely happens to put a populated type first for those, an
    incidental accident of spelling, not a designed guarantee.

    Stable-partitions by "has any real evidence in the already-computed
    traversal" (a type with 1 edge and a type with 100 edges are treated
    alike -- both simply "has evidence"): every type with at least one
    observed edge keeps its original relative order and sorts ahead of
    every type with zero observed edges, which also keeps its own
    original relative order. This is deliberately **not** a full sort by
    raw edge count -- an earlier draft of this fix did that and broke
    `FIND_CALLERS` on real data (pytest `What calls approx?`: `CALLS` has
    13 real edges in the traversal, `REFERENCES` has 82; ranking by
    magnitude alone would silently swap a "what *calls* X" answer for a
    generic-reference one, exactly the kind of relevance regression
    `bounded_traversal`'s own docstring already warns `REFERENCES` risks
    when treated as more than supplementary context). Preserving relative
    order within each evidence bucket keeps every intent's *existing*
    prioritization among types that both have real data (`_relationship_
    types_for_intent`'s alphabetical order remains the tie-break there,
    unchanged) -- this fix only demotes a type once it is *provably*
    contributing nothing, which is the one and only failure mode GAP-11
    actually is. Uses only the traversal result `bounded_traversal`
    already produced this call -- no extra graph query, no redesign of
    traversal or budget logic, no per-language/per-repository
    special-casing.
    """
    has_evidence: set[RelationshipType] = {rel.predicate for rel in observed_relationships}
    return sorted(relationship_types, key=lambda t: t not in has_evidence)


def plan_query(
    *,
    query_contract: QueryContract,
    graph: GraphReader,
    ingestion_result: IngestionResult,
    registry: CapabilityRegistry,
    repository: RepositoryMetadata,
    cache: PlanCache | None = None,
) -> RetrievalPlan:
    """DTD-03: `QueryContract` -> `RetrievalPlan`.

    `graph` and `ingestion_result` are the caller's already-selected
    graph snapshot -- this function only *locks* (captures `.version`
    from) whichever reader it is handed, per TAD §20; it never searches
    for "the latest" version itself (`docs/architecture-conformance-
    audit.md` §R.3 -- no such registry exists anywhere in the
    architecture, obtaining one is an orchestration-layer concern above
    D9's scope).
    """
    identity = compute_query_identity(query_contract)
    graph_version = graph.version
    key = cache_key_for(graph_version=graph_version, query_identity=identity)
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return cached.model_copy(
                update={"telemetry": cached.telemetry.model_copy(update={"cache_hit": True})}
            )

    target_entities = resolve_targets(graph, query_contract.targets)
    base_depth = _BASE_DEPTH_BY_INTENT.get(query_contract.intent, 0)
    relationship_types = list(query_contract.relationship_types)
    is_exhaustive = query_contract.completeness_requirement is CompletenessLevel.EXHAUSTIVE
    # Query-Shaped Evidence Retrieval milestone (task #127): see
    # `RetrievalPlan.trace_forward`'s own docstring for why TRACE_EXECUTION
    # -- and only TRACE_EXECUTION -- needs outbound-from-seed traversal.
    trace_forward = query_contract.intent is Intent.TRACE_EXECUTION

    selected_providers = select_providers(registry, query_contract.required_evidence, repository)
    budget = compute_budget(query_contract.token_budget, query_contract.latency_budget_ms)
    max_nodes, max_edges = budget.max_nodes, budget.max_edges

    pruning_steps: list[str] = []
    supplementary_types: tuple[RelationshipType, ...] = ()

    # TAD §41: budget cannot support even a minimally viable evidence
    # package (the target entities themselves) -> PLAN_UNSUPPORTED.
    #
    # D9 target-resolution refinement (real-repository benchmark findings
    # #12/#13/#22/#24): a short/common target name's deterministic
    # substring lookup (HLRD §34) can legitimately return far more
    # entities than the budget can seed a traversal from -- most of them
    # unrelated to the query (every path under a self-hosted repository's
    # own directory contains that repository's name as a substring, for
    # example). `_resolve_one_target` already narrows to an exact match
    # when one exists; when the set is *still* over budget, this is TAD
    # §32's own kind of budget-aware pruning applied to the target seed
    # set itself (the same `pruning_steps`/`BudgetTrace` record every
    # other pruning step in this function already uses), not a new
    # mechanism -- deterministically keep the first `max_nodes` entities
    # in `resolve_targets`'s own established canonical-id sort order and
    # continue planning, rather than declaring no plan possible.
    #
    # EXHAUSTIVE queries are the one case this must never apply to --
    # "Exhaustive queries cannot be pruned below required coverage" (TAD
    # §32) -- so an EXHAUSTIVE query over budget still gets exactly
    # today's unconditional PLAN_UNSUPPORTED, unchanged. Likewise when
    # `max_nodes == 0`: truncating to zero entities is not "a smaller but
    # still usable seed set", it is the exact "cannot support even a
    # minimally viable evidence package" case TAD §41 describes, so that
    # also keeps today's unconditional PLAN_UNSUPPORTED.
    if max_nodes < len(target_entities):
        if is_exhaustive or max_nodes == 0:
            plan = _build_plan(
                query_identity=identity,
                graph_version=graph_version,
                target_entity_ids=[e.canonical_id for e in target_entities],
                relationship_types=relationship_types,
                traversal_depth=0,
                query_contract=query_contract,
                selected_providers=selected_providers,
                budget=budget,
                status=PlanStatus.PLAN_UNSUPPORTED,
                negative_query_candidate=False,
                negative_query_result=None,
                trace_forward=trace_forward,
                budget_trace=BudgetTrace(
                    original_node_estimate=len(target_entities),
                    original_edge_estimate=0,
                    pruned_node_estimate=len(target_entities),
                    pruned_edge_estimate=0,
                    pruning_occurred=False,
                    reason="token_budget cannot support even the target entities themselves",
                ),
            )
            if cache is not None:
                cache.put(key, plan)
            return plan
        original_target_count = len(target_entities)
        target_entities = target_entities[:max_nodes]
        pruning_steps.append(
            f"reduce target-entity set to budget ({original_target_count} -> {max_nodes})"
        )

    depth_ceiling = latency_derived_depth_ceiling(query_contract.latency_budget_ms, base_depth)
    effective_depth = base_depth
    effective_relationship_types = relationship_types

    if depth_ceiling < base_depth:
        if is_exhaustive:
            return _blocked_plan(
                query_identity=identity,
                graph_version=graph_version,
                query_contract=query_contract,
                selected_providers=selected_providers,
                budget=budget,
                reason="latency_budget cannot afford the required EXHAUSTIVE traversal depth",
                cache=cache,
                key=key,
                trace_forward=trace_forward,
            )
        effective_depth = depth_ceiling
        pruning_steps.append("reduce traversal depth")

    traversal = bounded_traversal(
        graph,
        target_entities,
        effective_relationship_types,
        effective_depth,
        max_nodes,
        max_edges,
        reverse_directional=trace_forward,
    )
    original_nodes, original_edges = len(traversal.entities), len(traversal.relationships)

    if traversal.truncated:
        if is_exhaustive:
            return _blocked_plan(
                query_identity=identity,
                graph_version=graph_version,
                query_contract=query_contract,
                selected_providers=selected_providers,
                budget=budget,
                reason="budget ceiling cannot support EXHAUSTIVE completeness",
                cache=cache,
                key=key,
                trace_forward=trace_forward,
            )
        if len(effective_relationship_types) > 1:
            prioritized_relationship_types = _prioritize_relationship_types_by_evidence(
                effective_relationship_types, traversal.relationships
            )
            # File-Level REFERENCES Traversal Completeness milestone:
            # narrowing to a single relationship type below is correct
            # for volume control (real measurement: re-including a
            # dropped type's *full* expansion re-triggers the same node-
            # budget truncation), but for FIND_IMPACT specifically its
            # three relationship types are not redundant with each other
            # (`_REQUIRED_EVIDENCE[FIND_IMPACT]` draws them from three
            # distinct capabilities -- CALL_RELATIONSHIP, DEPENDENCY,
            # DATA_FLOW -- required together, unlike e.g. FIND_CALLERS'
            # REFERENCES/IMPORTS, which are supplementary signal for one
            # question), so dropping a type here can silently discard a
            # whole category of real impact evidence (confirmed: `src/
            # flask/app.py --REFERENCES--> Flask.dispatch_request`, a
            # real, supported edge, gone entirely once `CALLS` won this
            # cut). `supplementary_seed_predicates` recovers exactly the
            # dropped types' direct-on-seed edges (cheap, non-cascading,
            # see `bounded_traversal`'s own docstring) without reopening
            # that budget problem -- scoped to `FIND_IMPACT` only, so
            # every other pruning-affected intent (`FIND_CALLERS`,
            # `FIND_REFERENCES`, `ARCHITECTURE_ANALYSIS`) is unaffected.
            if query_contract.intent is Intent.FIND_IMPACT:
                observed_predicates = {rel.predicate for rel in traversal.relationships}
                supplementary_types = tuple(
                    t for t in prioritized_relationship_types[1:] if t in observed_predicates
                )
            effective_relationship_types = prioritized_relationship_types[:1]
            pruning_steps.append("remove optional relationship types")
            traversal = bounded_traversal(
                graph,
                target_entities,
                effective_relationship_types,
                effective_depth,
                max_nodes,
                max_edges,
                reverse_directional=trace_forward,
                supplementary_seed_predicates=supplementary_types,
            )

    if traversal.truncated:
        pruning_steps.append("increase stop-sufficiency threshold")

    pruning_occurred = bool(pruning_steps)
    negative_candidate = (
        query_contract.intent in _NEGATIVE_QUERY_INTENTS and len(traversal.relationships) == 0
    )
    negative_result = None
    if negative_candidate and query_contract.required_evidence:
        negative_result = evaluate_negative_query_coverage(
            ingestion_result, query_contract.required_evidence[0]
        )

    plan = _build_plan(
        query_identity=identity,
        graph_version=graph_version,
        target_entity_ids=[e.canonical_id for e in target_entities],
        relationship_types=effective_relationship_types,
        traversal_depth=effective_depth,
        query_contract=query_contract,
        selected_providers=selected_providers,
        budget=budget,
        status=PlanStatus.PRUNED if pruning_occurred else PlanStatus.OK,
        negative_query_candidate=negative_candidate,
        negative_query_result=negative_result,
        trace_forward=trace_forward,
        supplementary_relationship_types=supplementary_types,
        budget_trace=BudgetTrace(
            original_node_estimate=original_nodes,
            original_edge_estimate=original_edges,
            pruned_node_estimate=len(traversal.entities),
            pruned_edge_estimate=len(traversal.relationships),
            pruning_occurred=pruning_occurred,
            pruning_steps=pruning_steps,
            reason="; ".join(pruning_steps) if pruning_steps else None,
        ),
    )
    if cache is not None:
        cache.put(key, plan)
    return plan


def _build_plan(
    *,
    query_identity: str,
    graph_version: GraphVersion,
    target_entity_ids: list[str],
    relationship_types: list[RelationshipType],
    traversal_depth: int,
    query_contract: QueryContract,
    selected_providers: dict[str, list[str]],
    budget: RetrievalBudget,
    status: PlanStatus,
    negative_query_candidate: bool,
    negative_query_result: NegativeQueryCoverage | None,
    budget_trace: BudgetTrace,
    trace_forward: bool = False,
    supplementary_relationship_types: tuple[RelationshipType, ...] = (),
) -> RetrievalPlan:
    return RetrievalPlan(
        query_identity=query_identity,
        graph_version=graph_version,
        target_entity_ids=target_entity_ids,
        constraints=query_contract.constraints,
        relationship_types=relationship_types,
        traversal_depth=traversal_depth,
        required_capabilities=query_contract.required_evidence,
        selected_providers=selected_providers,
        completeness_requirement=query_contract.completeness_requirement,
        budget=budget,
        ranking_strategy="tad_v1_four_signal",
        stop_conditions=list(_STOP_CONDITIONS),
        negative_query_candidate=negative_query_candidate,
        negative_query_result=negative_query_result,
        trace_forward=trace_forward,
        supplementary_relationship_types=list(supplementary_relationship_types),
        status=status,
        telemetry=PlanTelemetry(
            graph_version_id=graph_version.version_id,
            budget_trace=budget_trace,
        ),
    )


def _blocked_plan(
    *,
    query_identity: str,
    graph_version: GraphVersion,
    query_contract: QueryContract,
    selected_providers: dict[str, list[str]],
    budget: RetrievalBudget,
    reason: str,
    cache: PlanCache | None,
    key: CacheKey,
    trace_forward: bool = False,
) -> RetrievalPlan:
    plan = _build_plan(
        query_identity=query_identity,
        graph_version=graph_version,
        target_entity_ids=[],
        relationship_types=list(query_contract.relationship_types),
        traversal_depth=0,
        query_contract=query_contract,
        selected_providers=selected_providers,
        budget=budget,
        status=PlanStatus.PLAN_BLOCKED,
        negative_query_candidate=False,
        negative_query_result=None,
        trace_forward=trace_forward,
        budget_trace=BudgetTrace(
            original_node_estimate=0,
            original_edge_estimate=0,
            pruned_node_estimate=0,
            pruned_edge_estimate=0,
            pruning_occurred=False,
            reason=reason,
        ),
    )
    if cache is not None:
        cache.put(key, plan)
    return plan


def execute_query(
    plan: RetrievalPlan,
    *,
    graph: GraphReader,
    evidence_store: EvidenceStore,
    ingestion_result: IngestionResult,
) -> EvidencePackage:
    """DTD-04 (TAD §35): execute the locked `RetrievalPlan`.

    Refuses (`GraphVersionMismatchError`) rather than silently reading a
    different snapshot when `graph.version` no longer matches
    `plan.graph_version` (TAD §55's `CONCURRENT_UPDATE_DETECTED`) --
    holding the same `GraphReader` reference across planning and
    execution *is* the lock (`docs/architecture-conformance-audit.md`
    §R.3); this is the safety net that proves it was honored.
    """
    if graph.version.version_id != plan.graph_version.version_id:
        raise GraphVersionMismatchError(
            f"CONCURRENT_UPDATE_DETECTED: plan locked graph_version "
            f"{plan.graph_version.version_id!r}, but was asked to execute "
            f"against {graph.version.version_id!r}"
        )

    if plan.status in (PlanStatus.PLAN_BLOCKED, PlanStatus.PLAN_UNSUPPORTED):
        return EvidencePackage(
            graph_version=plan.graph_version,
            query_identity=plan.query_identity,
            entities=[],
            relationships=[],
            evidence=[],
            coverage={},
            limitations=[plan.telemetry.budget_trace.reason or plan.status.value],
            partial=True,
        )

    target_entities = [
        entity
        for canonical_id in plan.target_entity_ids
        if (entity := graph.get_entity(canonical_id)) is not None
    ]
    traversal = bounded_traversal(
        graph,
        target_entities,
        plan.relationship_types,
        plan.traversal_depth,
        plan.budget.max_nodes,
        plan.budget.max_edges,
        reverse_directional=plan.trace_forward,
        supplementary_seed_predicates=tuple(plan.supplementary_relationship_types),
    )

    # BM25 query terms come from the resolved target entities' own names
    # (TAD §36's "extracted query entities") -- not the opaque
    # canonical_id hash strings on `plan.target_entity_ids`.
    query_terms = [
        text for entity in target_entities for text in (entity.name, entity.qualified_name)
    ]
    primary_relationship_type = plan.relationship_types[0] if plan.relationship_types else None
    ranked = rank_entities(
        entities=traversal.entities,
        relationships=traversal.relationships,
        distances=traversal.distances,
        query_targets=query_terms,
        query_constraints=plan.constraints,
        primary_relationship_type=primary_relationship_type,
    )
    ranked_entities = [entity for entity, _score in ranked]

    expanded_entities, expansion_partial = expand_for_source_context(
        graph=graph,
        entities=ranked_entities,
        required_capabilities=plan.required_capabilities,
    )

    evidence = collect_evidence(evidence_store, traversal.relationships)
    coverage = {
        capability.value: classify_capability_coverage(ingestion_result, capability)
        for capability in plan.required_capabilities
    }

    limitations: list[str] = []
    if len(target_entities) > 1:
        # Query-Shaped Evidence Retrieval milestone (LLM Grounding / Graph
        # Sufficiency Validation, D21 finding): `resolve_targets` already
        # correctly preserves every distinct entity matching the query's
        # targets rather than collapsing them (HLRD §34's ambiguity-
        # abstention discipline, unchanged) -- but when that real
        # multiplicity is serialized into a large context alongside many
        # relationships, the ambiguity signal itself can be easy to miss.
        # Stating it explicitly, once, up front costs one string and
        # changes no retrieval/ranking/traversal behavior.
        limitations.append(
            f"ambiguous target: {len(target_entities)} distinct entities match this query"
        )
    if plan.status is PlanStatus.PRUNED:
        limitations.append(f"plan pruned: {plan.telemetry.budget_trace.reason}")
    if plan.negative_query_candidate and plan.negative_query_result is not None:
        limitations.append(f"negative_query_result={plan.negative_query_result.value}")
    if expansion_partial:
        limitations.append("SOURCE_CONTEXT expansion bound exhausted without satisfying it")

    source_context = (
        expanded_entities if Capability.SOURCE_LOCATION in plan.required_capabilities else []
    )

    return EvidencePackage(
        graph_version=plan.graph_version,
        query_identity=plan.query_identity,
        entities=expanded_entities,
        relationships=traversal.relationships,
        evidence=evidence,
        source_context=source_context,
        coverage=coverage,
        limitations=limitations,
        partial=expansion_partial or traversal.truncated,
    )


__all__ = [
    "GraphVersionMismatchError",
    "execute_query",
    "plan_query",
]
