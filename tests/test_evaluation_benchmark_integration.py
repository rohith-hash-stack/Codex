"""Integration test for D13-C (directive requirement: "real repository
fixture -> ingestion -> graph -> D8 QueryContract -> D9 RetrievalPlan ->
D9 retrieval/ranking -> D13-B EvaluationTrace -> benchmark ground truth
-> D13-A evaluation -> Precision@10/Recall@10/MRR"). Reuses the real,
unmodified D8/D9 code and `tests/symbol_level_fixtures.py` -- no fake
candidate list or fabricated ground truth is constructed anywhere in
this file; the `BenchmarkCase`'s `query_id` is derived the canonical
way (`compute_query_identity` applied to the real `QueryContract`), not
invented.
"""

from __future__ import annotations

from datetime import UTC, datetime

from codex.evaluation.benchmark import verify_case_execution
from codex.evaluation.evaluate import evaluate
from codex.evaluation.models import (
    BenchmarkCase,
    BenchmarkCorpus,
    EvaluationMetric,
    GroundTruthLabel,
)
from codex.evaluation.observer import observe_ranked_candidates
from codex.planner.cache import compute_query_identity
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
QUERY_TEXT = "Which tests call authenticate?"


def test_benchmark_case_to_d13a_evaluation_end_to_end() -> None:
    result, registry, evidence_store, repository = build_symbol_level_graph()

    # A benchmark author determines the deterministic query_id the
    # canonical way -- by actually running the real D8 engine once,
    # never inventing an id ahead of time.
    understanding = understand_query(
        QUERY_TEXT, repository_id=repository.repository_id, now=NOW
    )
    assert understanding.status.value == "RESOLVED"
    contract = understanding.contract
    assert contract is not None
    query_id = compute_query_identity(contract)

    case = BenchmarkCase(
        query_id=query_id,
        repository_id=repository.repository_id,
        repository_revision=repository.head_revision,
        query_text=QUERY_TEXT,
    )

    # "real Codex execution": real D9 planning + retrieval/ranking.
    plan = plan_query(
        query_contract=contract,
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.query_identity == case.query_id  # the canonical-id claim, proven

    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    trace = observe_ranked_candidates(plan, result.graph_store)

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

    # "repository/revision mismatch handling": the case really was run
    # against the repository/revision it declares.
    assert verify_case_execution(case, event) is True

    test1_id = next(e.canonical_id for e in package.entities if e.name == TEST_VALID_LOGIN_FN)
    test2_id = next(e.canonical_id for e in package.entities if e.name == TEST_INVALID_LOGIN_FN)
    authenticate_id = next(e.canonical_id for e in package.entities if e.name == AUTHENTICATE_FN)

    ground_truth = BenchmarkCorpus(
        corpus_version="d13c-integration-v1",
        cases={case.query_id: case},
        labels={
            case.query_id: GroundTruthLabel(
                query_id=case.query_id,
                relevant_entity_ids=frozenset({test1_id, test2_id}),
            )
        },
    )

    report = evaluate([event], ground_truth, traces={case.query_id: trace}, now=NOW)

    precision = next(r for r in report.results if r.metric is EvaluationMetric.PRECISION_AT_10)
    recall = next(r for r in report.results if r.metric is EvaluationMetric.RECALL_AT_10)
    mrr = next(r for r in report.results if r.metric is EvaluationMetric.MRR)

    assert precision.evaluable and recall.evaluable and mrr.evaluable
    assert recall.value == 1.0  # both relevant tests are in the small candidate set
    assert mrr.value > 0.0
    assert authenticate_id not in ground_truth.labels[case.query_id].relevant_entity_ids


def test_benchmark_case_repository_revision_mismatch_is_detected_end_to_end() -> None:
    """A case authored against the wrong revision is caught by
    `verify_case_execution`, using a real event -- not a hand-built
    mismatch."""
    result, registry, evidence_store, repository = build_symbol_level_graph()
    understanding = understand_query(
        QUERY_TEXT, repository_id=repository.repository_id, now=NOW
    )
    contract = understanding.contract
    assert contract is not None

    wrong_case = BenchmarkCase(
        query_id=compute_query_identity(contract),
        repository_id=repository.repository_id,
        repository_revision="some-other-revision-entirely",
        query_text=QUERY_TEXT,
    )

    plan = plan_query(
        query_contract=contract,
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
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

    assert verify_case_execution(wrong_case, event) is False
