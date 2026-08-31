"""Passive Evaluation Observer (directive D13-B).

**The problem this solves** (found during D13-A, `docs/architecture-
conformance-audit.md` §DD.1): D9's real ranked retrieval output --
`list[tuple[RepositorySymbol, float]]`, produced by `codex.planner.
ranking.rank_entities` inside `codex.planner.planner.execute_query` --
never survives past that function's local scope. `RetrievalPlan.
target_entity_ids` is the query's own *targets* (input side, confirmed
by reading `execute_query` directly), and `EvidencePackage.entities`
discards both the score and, whenever TAD §40 source-context expansion
actually triggers, the rank order too (`expand_for_source_context`
re-sorts by `canonical_id`). Nothing in D11's closed telemetry schema
records it either -- only `candidate_count`/`mss_size` bare counts.
So `PRECISION_AT_10`/`RECALL_AT_10`/`MRR` (TAD §66) were structurally
uncomputable, independent of ground truth.

**The observation point.** `bounded_traversal` (`codex.planner.
retrieval`) and `rank_entities` (`codex.planner.ranking`) are both
already-exported, pure, deterministic functions -- no I/O, no
randomness, no hidden state; their output depends only on their
explicit arguments. `observe_ranked_candidates` below calls these
*exact same, unmodified* functions a second time, from outside
`execute_query`, using only `RetrievalPlan` (already a public,
returned value) and the same `GraphReader` a caller already holds --
producing results bit-for-bit identical to what `execute_query` itself
computed internally, without executing any of `execute_query`'s other
(evidence-collection, source-context-expansion) side effects and
**without touching a single line of `codex.planner`**.

**Known synchronization risk, documented rather than hidden:** four
lines of this module (the `PlanStatus` short-circuit and the
`target_entities`/`query_terms`/`primary_relationship_type`
derivation) are copied verbatim from `execute_query`
(`src/codex/planner/planner.py`, the block spanning the early
`PLAN_BLOCKED`/`PLAN_UNSUPPORTED` return through the `rank_entities`
call) so this observer's reconstruction matches real D9 behavior
exactly, including the early-exit case. If `execute_query`'s own
derivation logic ever changes without a matching update here, this
module would silently drift out of sync -- the same class of risk
D5's real FILE-identity duplication bug once caused. Mitigated by
`tests/test_evaluation_integration.py`'s real D8->D9->observer chain
test, which asserts byte-for-byte equality between this module's
output and `EvidencePackage.entities`' real order for a query that
never triggers TAD §40 expansion (the one case where a direct
list-order comparison is unambiguous).

**Passive by construction:** this module is read-only with respect to
`codex.planner`/`codex.graph`/`codex.evidence` -- it only ever *calls*
`bounded_traversal`/`rank_entities` and *reads* `plan`/`graph`, never
constructs a `RetrievalPlan`, never calls `execute_query`, never
mutates `graph`, and returns a new `EvaluationTrace` value with no
side effect on any D1-D12 store. It cannot influence which candidates
`execute_query` itself returns, because it never runs before or during
`execute_query` and `execute_query`'s own code path is completely
unmodified and unaware this module exists.
"""

from __future__ import annotations

from codex.evaluation.models import EvaluationTrace, RankedCandidate
from codex.graph.store import GraphReader
from codex.planner.models import PlanStatus, RetrievalPlan
from codex.planner.ranking import rank_entities
from codex.planner.retrieval import bounded_traversal


def observe_ranked_candidates(plan: RetrievalPlan, graph: GraphReader) -> EvaluationTrace:
    """Reconstruct D9's real ranked candidate list for `plan` by
    replaying its own real, unmodified `bounded_traversal`/
    `rank_entities` calls -- never a fabricated or approximated
    ordering. Deterministic: identical `plan`/`graph` always produce an
    identical `EvaluationTrace` (both underlying functions are pure)."""
    if plan.status in (PlanStatus.PLAN_BLOCKED, PlanStatus.PLAN_UNSUPPORTED):
        # Mirrors `execute_query`'s own early return for these statuses
        # (planner.py:350-360): no traversal/ranking is ever attempted.
        return EvaluationTrace(
            query_identity=plan.query_identity,
            repository_id=plan.graph_version.repository_id,
            graph_version_id=plan.graph_version.version_id,
            ordered_candidates=[],
        )

    # Copied from `execute_query` (planner.py:362-382) verbatim -- see
    # this module's own docstring for the synchronization risk this
    # duplication carries and how it is mitigated.
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
    )
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

    ordered_candidates = [
        RankedCandidate(entity_id=entity.canonical_id, score=score, rank=index + 1)
        for index, (entity, score) in enumerate(ranked)
    ]
    return EvaluationTrace(
        query_identity=plan.query_identity,
        repository_id=plan.graph_version.repository_id,
        graph_version_id=plan.graph_version.version_id,
        ordered_candidates=ordered_candidates,
    )


__all__ = ["observe_ranked_candidates"]
