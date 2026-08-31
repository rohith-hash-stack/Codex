"""Integration test for D13-B (directive requirement: "real Query -> D8
QueryContract -> D9 RetrievalPlan -> real D9 retrieval/ranking -> D13-B
EvaluationTrace -> D13-A evaluation"). Reuses the real, unmodified D8
(`codex.query_understanding`) and D9 (`codex.planner`) code and the
existing `tests/symbol_level_fixtures.py` graph -- no fake candidate
list is constructed anywhere in this file.
"""

from __future__ import annotations

from datetime import UTC, datetime

from codex.evaluation.evaluate import evaluate
from codex.evaluation.models import BenchmarkCorpus, EvaluationMetric, GroundTruthLabel
from codex.evaluation.observer import observe_ranked_candidates
from codex.planner.planner import execute_query, plan_query
from codex.query_understanding.engine import understand_query
from codex.telemetry.models import QueryTelemetryEvent
from symbol_level_fixtures import (
    AUTHENTICATE_FN,
    TEST_INVALID_LOGIN_FN,
    TEST_VALID_LOGIN_FN,
    build_symbol_level_graph,
)

NOW = datetime(2026, 8, 31, tzinfo=UTC)


def test_real_query_to_d8_to_d9_to_observer_to_d13a_evaluation() -> None:
    """The full required chain, driven by real Tier-0 detection on real
    query text against a real, unmodified D9 retrieval/ranking run --
    "Which tests call authenticate?", the same query this project's own
    D1-D10 integration hardening pass already established as its
    canonical symbol-level worked example."""
    result, registry, evidence_store, repository = build_symbol_level_graph()
    query_text = "Which tests call authenticate?"

    # Real D8.
    understanding = understand_query(
        query_text, repository_id=repository.repository_id, now=NOW
    )
    assert understanding.status.value == "RESOLVED"
    contract = understanding.contract
    assert contract is not None
    assert contract.targets == [AUTHENTICATE_FN]

    # Real D9 planning + real D9 retrieval/ranking (via execute_query,
    # exactly as production would run it).
    plan = plan_query(
        query_contract=contract,
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.status.value == "OK"
    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )

    # D13-B: passive observation of the same plan -- never influences
    # the `package` computed just above, which already exists.
    trace = observe_ranked_candidates(plan, result.graph_store)

    # The trace's candidates are exactly the package's real entities,
    # in the same real order (no SOURCE_LOCATION requested, so TAD §40
    # expansion never reorders anything) -- proving this is a faithful
    # observation, not an independently-computed approximation.
    assert [c.entity_id for c in trace.ordered_candidates] == [
        e.canonical_id for e in package.entities
    ]

    authenticate_id = next(e.canonical_id for e in package.entities if e.name == AUTHENTICATE_FN)
    test1_id = next(e.canonical_id for e in package.entities if e.name == TEST_VALID_LOGIN_FN)
    test2_id = next(e.canonical_id for e in package.entities if e.name == TEST_INVALID_LOGIN_FN)

    # D11 telemetry (real, unmodified schema) -- one real query event.
    event = QueryTelemetryEvent.build(
        query_id=plan.query_identity,
        graph_version=plan.graph_version,
        query_contract=contract,
        retrieval_plan=plan,
        candidate_count=len(package.entities),
        mss_size=len(package.entities),
        llm_calls=0,
        now=NOW,
    )

    # D13-A: evaluation, consuming the real D13-B trace + a real (test-
    # supplied, never fabricated by D13-A/D13-B themselves) ground
    # truth relevance set -- both TEST_VALID_LOGIN_FN and
    # TEST_INVALID_LOGIN_FN are the queried-for "tests that call
    # authenticate", i.e. the genuinely relevant answer to this query.
    ground_truth = BenchmarkCorpus(
        corpus_version="d13b-integration-v1",
        labels={
            plan.query_identity: GroundTruthLabel(
                query_id=plan.query_identity,
                relevant_entity_ids=frozenset({test1_id, test2_id}),
            )
        },
    )

    report = evaluate(
        [event], ground_truth, traces={plan.query_identity: trace}, now=NOW
    )

    precision = next(r for r in report.results if r.metric is EvaluationMetric.PRECISION_AT_10)
    recall = next(r for r in report.results if r.metric is EvaluationMetric.RECALL_AT_10)
    mrr = next(r for r in report.results if r.metric is EvaluationMetric.MRR)

    assert precision.evaluable is True
    assert recall.evaluable is True
    assert mrr.evaluable is True

    # Both relevant tests are among the (small, 3-entity) candidate
    # set, so recall is complete and MRR is 1.0 if either test is
    # ranked first; precision is bounded by the fixed /10 denominator
    # with only 2 relevant candidates total.
    assert recall.value == 1.0
    assert 0.0 < precision.value <= 0.2
    assert mrr.value > 0.0

    # authenticate_id itself is not "relevant" ground truth here (the
    # query asks *which tests call* authenticate, not for authenticate
    # itself) -- confirms ground truth was never conflated with every
    # observed candidate.
    assert authenticate_id not in ground_truth.labels[plan.query_identity].relevant_entity_ids
