"""Negative-query safety tests (TAD §34; directive D9 Part 18 "Negative
queries" / Part 9): complete empty result, incomplete empty result,
failed capability, partial provider, stale evidence -- `plan_query`
never bypasses `codex.coverage`, it only forwards the classification.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from codex.coverage.engine import CompletenessLevel, NegativeQueryCoverage
from codex.evidence.model import CoverageStatus, EvidenceCohort
from codex.ingestion.models import IngestionResult, ProviderRunOutcome, ProviderRunStatus
from codex.ontology.relationships import RelationshipType
from codex.planner.planner import execute_query, plan_query
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


def _with_outcomes(result: IngestionResult, outcomes: list[ProviderRunOutcome]) -> IngestionResult:
    return IngestionResult(
        repository_id=result.repository_id,
        repository_revision=result.repository_revision,
        graph_version=result.graph_version,
        graph_store=result.graph_store,
        provider_outcomes=outcomes,
    )


def test_complete_empty_result_is_no_evidence_found() -> None:
    """Target exists, zero relationships, capability ran to completion
    (COMMITTED, no failures) -> the absence is trustworthy."""
    result, registry, evidence_store, repository = build_graph(entity_paths=("auth.py",))
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
    assert any("negative_query_result" in limitation for limitation in package.limitations)
    assert plan.negative_query_candidate is True
    assert plan.negative_query_result is NegativeQueryCoverage.NO_EVIDENCE_FOUND


def test_incomplete_empty_result_when_capability_never_ran_is_inconclusive() -> None:
    result, registry, _, repository = build_graph(entity_paths=("auth.py",))
    incomplete = _with_outcomes(result, [])  # no provider outcomes at all -> NOT_SUPPORTED
    plan = plan_query(
        query_contract=make_contract(),
        graph=result.graph_store,
        ingestion_result=incomplete,
        registry=registry,
        repository=repository,
    )
    assert plan.negative_query_result is NegativeQueryCoverage.INCONCLUSIVE


def test_failed_capability_is_inconclusive() -> None:
    result, registry, _, repository = build_graph(
        entity_paths=("auth.py",),
        fail_capabilities=frozenset({Capability.CALL_RELATIONSHIP}),
    )
    plan = plan_query(
        query_contract=make_contract(),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.negative_query_result is NegativeQueryCoverage.INCONCLUSIVE


def test_partial_provider_cohort_is_inconclusive() -> None:
    result, registry, _, repository = build_graph(entity_paths=("auth.py",))
    partial_cohort = EvidenceCohort(
        provider="fake",
        provider_version="1.0.0",
        snapshot_id=repository.head_revision,
        source_revision=repository.head_revision,
        successful_capabilities=[],
        failed_capabilities=[],
        partial_capabilities=[Capability.CALL_RELATIONSHIP.value],
        coverage_status=CoverageStatus.PARTIAL,
    )
    partial_result = _with_outcomes(
        result,
        [
            ProviderRunOutcome(
                provider_name="fake",
                status=ProviderRunStatus.COMMITTED,
                capabilities_requested=frozenset({Capability.CALL_RELATIONSHIP.value}),
                cohort=partial_cohort,
            )
        ],
    )
    plan = plan_query(
        query_contract=make_contract(),
        graph=result.graph_store,
        ingestion_result=partial_result,
        registry=registry,
        repository=repository,
    )
    assert plan.negative_query_result is NegativeQueryCoverage.INCONCLUSIVE


def test_stale_but_complete_evidence_is_still_no_evidence_found() -> None:
    """Staleness (`EvidenceCohort.observed_at`) is a ranking/freshness
    concern, not a coverage-completeness one -- `codex.coverage` (and so
    `plan_query`) does not conflate the two, since neither HLRD nor TAD
    defines a staleness-invalidates-absence rule. A stale-but-COMPLETE
    cohort still yields NO_EVIDENCE_FOUND."""
    result, registry, _, repository = build_graph(entity_paths=("auth.py",))
    stale_cohort = EvidenceCohort(
        provider="fake",
        provider_version="1.0.0",
        snapshot_id=repository.head_revision,
        source_revision=repository.head_revision,
        observed_at=datetime.now(UTC) - timedelta(days=365),
        successful_capabilities=[Capability.CALL_RELATIONSHIP.value],
        failed_capabilities=[],
        coverage_status=CoverageStatus.FULL,
    )
    stale_result = _with_outcomes(
        result,
        [
            ProviderRunOutcome(
                provider_name="fake",
                status=ProviderRunStatus.COMMITTED,
                capabilities_requested=frozenset({Capability.CALL_RELATIONSHIP.value}),
                cohort=stale_cohort,
                entities_upserted=0,
                evidence_upserted=0,
            )
        ],
    )
    plan = plan_query(
        query_contract=make_contract(),
        graph=result.graph_store,
        ingestion_result=stale_result,
        registry=registry,
        repository=repository,
    )
    assert plan.negative_query_result is NegativeQueryCoverage.NO_EVIDENCE_FOUND


def test_lookup_intent_is_never_a_negative_query_candidate() -> None:
    result, registry, _, repository = build_graph(entity_paths=("auth.py",))
    plan = plan_query(
        query_contract=make_contract(intent=Intent.CODE_LOOKUP),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.negative_query_candidate is False
    assert plan.negative_query_result is None


def test_non_empty_result_is_not_a_negative_query_candidate() -> None:
    # `auth.py` is the caller (subject) and `service.py` the callee (object):
    # FIND_CALLERS/CALLS only anchors on the object endpoint (post-fix
    # external-repository readiness audit's "relationship-set imprecision"
    # fix, `codex.planner.retrieval.bounded_traversal`), so the query target
    # must be the *callee* for this fixture to represent real forward
    # evidence rather than accidentally exercising the reverse direction.
    result, registry, _, repository = build_graph(
        entity_paths=("service.py", "auth.py"), relationship_pairs=(("auth.py", "service.py"),)
    )
    plan = plan_query(
        query_contract=make_contract(targets=["service.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.negative_query_candidate is False


def test_reverse_direction_only_evidence_is_still_a_negative_query_candidate() -> None:
    """Directional-predicate anchoring (`codex.planner.retrieval.
    bounded_traversal`, post-fix external-repository readiness audit) is
    negative-query-detection *policy*-neutral: `plan_query`'s own
    `negative_candidate = intent in _NEGATIVE_QUERY_INTENTS and
    len(traversal.relationships) == 0` line is untouched. What changes is
    only the traversal's own relationship count feeding into it -- a
    relationship that exists but points the wrong way for `FIND_CALLERS`
    (the query target is the *caller*, never a real callee) correctly
    yields zero traversal relationships, and therefore still correctly
    triggers `negative_query_candidate=True`, exactly as it would if no
    relationship existed at all.
    """
    result, registry, _, repository = build_graph(
        entity_paths=("service.py", "auth.py"), relationship_pairs=(("service.py", "auth.py"),)
    )
    plan = plan_query(
        query_contract=make_contract(targets=["service.py"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.negative_query_candidate is True
