"""Behavioral tests for `codex.evaluation.evaluate.evaluate` (directives
D13-A, D13-B): every metric TAD/HLRD cannot ground-truthfully compute is
`NOT_EVALUABLE` with a fixed, correct reason; the computable metrics
(`CLAIM_VERIFICATION_ACCURACY`, `ABSTENTION_PRECISION`, and -- given a
D13-B `EvaluationTrace` -- `PRECISION_AT_10`/`RECALL_AT_10`/`MRR`) are
computed correctly given real ground truth and never otherwise;
determinism; no mutation of inputs; D13-A's pre-D13-B behavior is
unchanged when no trace is supplied.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from codex.evaluation.evaluate import evaluate
from codex.evaluation.models import (
    BenchmarkCorpus,
    EvaluationMetric,
    EvaluationTrace,
    GroundTruthLabel,
    NotEvaluableReason,
    RankedCandidate,
)
from codex.telemetry.models import QueryTelemetryEvent
from codex.verification.state import VerificationStatus
from telemetry_fixtures import make_contract, make_graph_version, make_plan

NOW = datetime(2026, 8, 31, tzinfo=UTC)

# Permanently, unconditionally NOT_EVALUABLE regardless of ground truth
# or traces -- unaffected by D13-B (directive: "existing NOT_EVALUABLE
# classifications must not silently change unless D13-B provides the
# exact information required" -- D13-B provides ranked-candidate data,
# which none of these four need).
STRUCTURALLY_EXCLUDED = {
    EvaluationMetric.FACTUAL_ACCURACY: NotEvaluableReason.MISSING_TELEMETRY_DATA,
    EvaluationMetric.UNSUPPORTED_CLAIM_RATE: NotEvaluableReason.UNDEFINED_FORMULA,
    EvaluationMetric.TOKEN_EFFICIENCY: NotEvaluableReason.UNDEFINED_FORMULA,
    EvaluationMetric.ASSERTION_TRACEABILITY: NotEvaluableReason.UNDEFINED_FORMULA,
}

# D13-B: conditionally excluded -- NOT_EVALUABLE only until a real
# EvaluationTrace + a relevance ground-truth label are both supplied.
RETRIEVAL_METRICS = (
    EvaluationMetric.PRECISION_AT_10,
    EvaluationMetric.RECALL_AT_10,
    EvaluationMetric.MRR,
)


def make_query_event(
    *,
    query_id: str = "q1",
    repository_id: str = "repo1",
    verification_result: VerificationStatus | None = None,
) -> QueryTelemetryEvent:
    gv = make_graph_version().model_copy(update={"repository_id": repository_id})
    return QueryTelemetryEvent.build(
        query_id=query_id,
        graph_version=gv,
        query_contract=make_contract(),
        retrieval_plan=make_plan(gv),
        candidate_count=1,
        mss_size=1,
        llm_calls=1,
        verification_result=verification_result,
        now=NOW,
    )


def result_for(report, metric: EvaluationMetric):  # type: ignore[no-untyped-def]
    (result,) = [r for r in report.results if r.metric is metric]
    return result


# --- structurally excluded metrics: always NOT_EVALUABLE --------------------


@pytest.mark.parametrize("metric,reason", list(STRUCTURALLY_EXCLUDED.items()))
def test_structurally_excluded_metric_is_never_evaluable_even_with_ground_truth(
    metric: EvaluationMetric, reason: NotEvaluableReason
) -> None:
    dataset = [make_query_event(verification_result=VerificationStatus.VERIFIED)]
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={"q1": GroundTruthLabel(query_id="q1", should_abstain=False)},
    )
    report = evaluate(dataset, corpus, now=NOW)
    result = result_for(report, metric)
    assert result.evaluable is False
    assert result.value is None
    assert result.reason is reason


def test_report_always_has_exactly_nine_results() -> None:
    report = evaluate([], None, now=NOW)
    assert len(report.results) == 9
    assert {r.metric for r in report.results} == set(EvaluationMetric)


# --- empty dataset / no ground truth ----------------------------------------


def test_empty_dataset_no_ground_truth_all_not_evaluable() -> None:
    report = evaluate([], None, now=NOW)
    assert report.dataset_size == 0
    assert report.corpus_version is None
    assert all(not r.evaluable for r in report.results)


def test_nonempty_dataset_no_ground_truth_computable_metrics_missing_ground_truth() -> None:
    dataset = [make_query_event(verification_result=VerificationStatus.VERIFIED)]
    report = evaluate(dataset, None, now=NOW)
    cva = result_for(report, EvaluationMetric.CLAIM_VERIFICATION_ACCURACY)
    ap = result_for(report, EvaluationMetric.ABSTENTION_PRECISION)
    assert cva.evaluable is False and cva.reason is NotEvaluableReason.MISSING_GROUND_TRUTH
    assert ap.evaluable is False and ap.reason is NotEvaluableReason.MISSING_GROUND_TRUTH


def test_ground_truth_supplied_but_no_overlapping_labels_is_insufficient_sample() -> None:
    dataset = [make_query_event(query_id="q1", verification_result=VerificationStatus.VERIFIED)]
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={"q-does-not-appear-in-dataset": GroundTruthLabel(query_id="other")},
    )
    report = evaluate(dataset, corpus, now=NOW)
    cva = result_for(report, EvaluationMetric.CLAIM_VERIFICATION_ACCURACY)
    assert cva.evaluable is False
    assert cva.reason is NotEvaluableReason.INSUFFICIENT_SAMPLE


# --- CLAIM_VERIFICATION_ACCURACY: computed correctly given ground truth -----


def test_claim_verification_accuracy_all_correct() -> None:
    dataset = [
        make_query_event(query_id="q1", verification_result=VerificationStatus.VERIFIED),
        make_query_event(query_id="q2", verification_result=VerificationStatus.REJECTED),
    ]
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={
            "q1": GroundTruthLabel(
                query_id="q1", expected_verification_status=VerificationStatus.VERIFIED
            ),
            "q2": GroundTruthLabel(
                query_id="q2", expected_verification_status=VerificationStatus.REJECTED
            ),
        },
    )
    report = evaluate(dataset, corpus, now=NOW)
    result = result_for(report, EvaluationMetric.CLAIM_VERIFICATION_ACCURACY)
    assert result.evaluable is True
    assert result.value == 1.0
    assert result.sample_size == 2


def test_claim_verification_accuracy_partial_mismatch() -> None:
    dataset = [
        make_query_event(query_id="q1", verification_result=VerificationStatus.VERIFIED),
        make_query_event(query_id="q2", verification_result=VerificationStatus.REJECTED),
    ]
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={
            "q1": GroundTruthLabel(
                query_id="q1", expected_verification_status=VerificationStatus.VERIFIED
            ),
            "q2": GroundTruthLabel(
                # Ground truth disagrees with the recorded REJECTED result.
                query_id="q2",
                expected_verification_status=VerificationStatus.VERIFIED,
            ),
        },
    )
    report = evaluate(dataset, corpus, now=NOW)
    result = result_for(report, EvaluationMetric.CLAIM_VERIFICATION_ACCURACY)
    assert result.evaluable is True
    assert result.value == 0.5
    assert result.sample_size == 2


def test_claim_verification_accuracy_skips_events_with_no_recorded_result() -> None:
    dataset = [make_query_event(query_id="q1", verification_result=None)]
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={
            "q1": GroundTruthLabel(
                query_id="q1", expected_verification_status=VerificationStatus.VERIFIED
            )
        },
    )
    report = evaluate(dataset, corpus, now=NOW)
    result = result_for(report, EvaluationMetric.CLAIM_VERIFICATION_ACCURACY)
    assert result.evaluable is False
    assert result.reason is NotEvaluableReason.INSUFFICIENT_SAMPLE


def test_claim_verification_accuracy_skips_labels_missing_expected_status() -> None:
    dataset = [make_query_event(query_id="q1", verification_result=VerificationStatus.VERIFIED)]
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={"q1": GroundTruthLabel(query_id="q1", should_abstain=True)},
    )
    report = evaluate(dataset, corpus, now=NOW)
    result = result_for(report, EvaluationMetric.CLAIM_VERIFICATION_ACCURACY)
    assert result.evaluable is False
    assert result.reason is NotEvaluableReason.INSUFFICIENT_SAMPLE


# --- ABSTENTION_PRECISION: computed correctly given ground truth -----------


def test_abstention_precision_all_correct() -> None:
    # REJECTED -> routing bucket ABSTAIN (D10's own closed mapping).
    dataset = [
        make_query_event(query_id="q1", verification_result=VerificationStatus.REJECTED),
        make_query_event(query_id="q2", verification_result=VerificationStatus.REJECTED),
    ]
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={
            "q1": GroundTruthLabel(query_id="q1", should_abstain=True),
            "q2": GroundTruthLabel(query_id="q2", should_abstain=True),
        },
    )
    report = evaluate(dataset, corpus, now=NOW)
    result = result_for(report, EvaluationMetric.ABSTENTION_PRECISION)
    assert result.evaluable is True
    assert result.value == 1.0
    assert result.sample_size == 2


def test_abstention_precision_partial() -> None:
    dataset = [
        make_query_event(query_id="q1", verification_result=VerificationStatus.REJECTED),
        make_query_event(query_id="q2", verification_result=VerificationStatus.REJECTED),
    ]
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={
            "q1": GroundTruthLabel(query_id="q1", should_abstain=True),
            # q2 abstained but ground truth says it shouldn't have (a false positive).
            "q2": GroundTruthLabel(query_id="q2", should_abstain=False),
        },
    )
    report = evaluate(dataset, corpus, now=NOW)
    result = result_for(report, EvaluationMetric.ABSTENTION_PRECISION)
    assert result.evaluable is True
    assert result.value == 0.5
    assert result.sample_size == 2


def test_abstention_precision_ignores_non_abstained_events() -> None:
    dataset = [
        make_query_event(query_id="q1", verification_result=VerificationStatus.VERIFIED),
    ]
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={"q1": GroundTruthLabel(query_id="q1", should_abstain=True)},
    )
    report = evaluate(dataset, corpus, now=NOW)
    result = result_for(report, EvaluationMetric.ABSTENTION_PRECISION)
    # q1 never abstained (VERIFIED, not REJECTED), so it contributes nothing.
    assert result.evaluable is False
    assert result.reason is NotEvaluableReason.INSUFFICIENT_SAMPLE


def test_abstention_precision_skips_labels_missing_should_abstain() -> None:
    dataset = [make_query_event(query_id="q1", verification_result=VerificationStatus.REJECTED)]
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={
            "q1": GroundTruthLabel(
                query_id="q1", expected_verification_status=VerificationStatus.REJECTED
            )
        },
    )
    report = evaluate(dataset, corpus, now=NOW)
    result = result_for(report, EvaluationMetric.ABSTENTION_PRECISION)
    assert result.evaluable is False
    assert result.reason is NotEvaluableReason.INSUFFICIENT_SAMPLE


# --- report-level properties -------------------------------------------------


def test_corpus_version_is_recorded_when_supplied() -> None:
    corpus = BenchmarkCorpus(corpus_version="bench-2026-08-31")
    report = evaluate([], corpus, now=NOW)
    assert report.corpus_version == "bench-2026-08-31"


def test_corpus_version_is_none_when_not_supplied() -> None:
    report = evaluate([], None, now=NOW)
    assert report.corpus_version is None


def test_dataset_size_reflects_input_length() -> None:
    dataset = [make_query_event(query_id="q1"), make_query_event(query_id="q2")]
    report = evaluate(dataset, None, now=NOW)
    assert report.dataset_size == 2


def test_evaluate_is_deterministic_for_identical_inputs() -> None:
    dataset = [make_query_event(query_id="q1", verification_result=VerificationStatus.VERIFIED)]
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={
            "q1": GroundTruthLabel(
                query_id="q1", expected_verification_status=VerificationStatus.VERIFIED
            )
        },
    )
    report1 = evaluate(dataset, corpus, now=NOW)
    report2 = evaluate(dataset, corpus, now=NOW)
    assert report1 == report2


def test_evaluate_never_mutates_dataset_or_ground_truth() -> None:
    dataset = [make_query_event(query_id="q1", verification_result=VerificationStatus.VERIFIED)]
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={
            "q1": GroundTruthLabel(
                query_id="q1", expected_verification_status=VerificationStatus.VERIFIED
            )
        },
    )
    dataset_before = list(dataset)
    corpus_before = corpus.model_copy(deep=True)
    evaluate(dataset, corpus, now=NOW)
    assert dataset == dataset_before
    assert corpus == corpus_before


# --- D13-B: PRECISION_AT_10 / RECALL_AT_10 / MRR -----------------------------


def make_trace(query_id: str, ranked_ids: list[str]) -> EvaluationTrace:
    """A trace with descending scores (1.0, 0.9, 0.8, ...) matching the
    given rank order -- scores only need to be internally consistent
    with rank for these tests, never inspected for their own value by
    the retrieval metrics (only rank/entity_id matter for Precision@10/
    Recall@10/MRR, per HLRD/TAD's own definitions)."""
    return EvaluationTrace(
        query_identity=query_id,
        repository_id="repo1",
        graph_version_id="repo1:rev1:scip=1.0.0",
        ordered_candidates=[
            RankedCandidate(entity_id=eid, score=max(0.0, 1.0 - 0.1 * i), rank=i + 1)
            for i, eid in enumerate(ranked_ids)
        ],
    )


@pytest.mark.parametrize("metric", RETRIEVAL_METRICS)
def test_retrieval_metric_stays_not_evaluable_with_ground_truth_but_no_traces(
    metric: EvaluationMetric,
) -> None:
    """D13-A's original behavior, unchanged: omitting `traces` (the
    default) leaves these three exactly as NOT_EVALUABLE as before
    D13-B existed."""
    dataset = [make_query_event(query_id="q1", verification_result=VerificationStatus.VERIFIED)]
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={"q1": GroundTruthLabel(query_id="q1", relevant_entity_ids=frozenset({"e1"}))},
    )
    report = evaluate(dataset, corpus, now=NOW)  # no traces=
    result = result_for(report, metric)
    assert result.evaluable is False
    assert result.value is None
    assert result.reason is NotEvaluableReason.MISSING_TELEMETRY_DATA


@pytest.mark.parametrize("metric", RETRIEVAL_METRICS)
def test_retrieval_metric_stays_not_evaluable_with_traces_but_no_ground_truth(
    metric: EvaluationMetric,
) -> None:
    dataset = [make_query_event(query_id="q1", verification_result=VerificationStatus.VERIFIED)]
    traces = {"q1": make_trace("q1", ["e1", "e2"])}
    report = evaluate(dataset, None, traces=traces, now=NOW)
    result = result_for(report, metric)
    assert result.evaluable is False
    assert result.reason is NotEvaluableReason.MISSING_GROUND_TRUTH


def test_precision_at_10_computed_correctly_given_trace_and_ground_truth() -> None:
    dataset = [make_query_event(query_id="q1")]
    # 4 candidates, 2 relevant -> 2/10 = 0.2 (fixed-denominator convention).
    traces = {"q1": make_trace("q1", ["e1", "e2", "e3", "e4"])}
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={"q1": GroundTruthLabel(query_id="q1", relevant_entity_ids=frozenset({"e1", "e3"}))},
    )
    report = evaluate(dataset, corpus, traces=traces, now=NOW)
    result = result_for(report, EvaluationMetric.PRECISION_AT_10)
    assert result.evaluable is True
    assert result.value == pytest.approx(0.2)
    assert result.sample_size == 1


def test_precision_at_10_only_counts_ranks_within_top_10() -> None:
    dataset = [make_query_event(query_id="q1")]
    ranked_ids = [f"e{i}" for i in range(12)]  # e0..e11
    traces = {"q1": make_trace("q1", ranked_ids)}
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        # e11 is relevant but ranked 12th -- outside top 10, must not count.
        labels={
            "q1": GroundTruthLabel(query_id="q1", relevant_entity_ids=frozenset({"e0", "e11"}))
        },
    )
    report = evaluate(dataset, corpus, traces=traces, now=NOW)
    result = result_for(report, EvaluationMetric.PRECISION_AT_10)
    assert result.value == pytest.approx(0.1)  # only e0 counts


def test_recall_at_10_computed_correctly_given_trace_and_ground_truth() -> None:
    dataset = [make_query_event(query_id="q1")]
    traces = {"q1": make_trace("q1", ["e1", "e2"])}
    # 3 relevant total, 1 retrieved in top 10 -> 1/3.
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={
            "q1": GroundTruthLabel(
                query_id="q1", relevant_entity_ids=frozenset({"e1", "e-missing-1", "e-missing-2"})
            )
        },
    )
    report = evaluate(dataset, corpus, traces=traces, now=NOW)
    result = result_for(report, EvaluationMetric.RECALL_AT_10)
    assert result.evaluable is True
    assert result.value == pytest.approx(1 / 3)


def test_recall_at_10_skips_queries_with_empty_relevant_set() -> None:
    """Zero relevant entities makes the denominator undefined (0/0),
    not zero -- this query must not silently contribute 0.0."""
    dataset = [make_query_event(query_id="q1")]
    traces = {"q1": make_trace("q1", ["e1"])}
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={"q1": GroundTruthLabel(query_id="q1", relevant_entity_ids=frozenset())},
    )
    report = evaluate(dataset, corpus, traces=traces, now=NOW)
    result = result_for(report, EvaluationMetric.RECALL_AT_10)
    assert result.evaluable is False
    assert result.reason is NotEvaluableReason.INSUFFICIENT_SAMPLE


def test_mrr_computed_correctly_first_relevant_at_rank_3() -> None:
    dataset = [make_query_event(query_id="q1")]
    traces = {"q1": make_trace("q1", ["e1", "e2", "e3", "e4"])}
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={"q1": GroundTruthLabel(query_id="q1", relevant_entity_ids=frozenset({"e3"}))},
    )
    report = evaluate(dataset, corpus, traces=traces, now=NOW)
    result = result_for(report, EvaluationMetric.MRR)
    assert result.evaluable is True
    assert result.value == pytest.approx(1 / 3)


def test_mrr_is_zero_when_no_retrieved_candidate_is_relevant() -> None:
    """Standard MRR convention: a query with no relevant candidate
    retrieved contributes 0.0, it is not excluded from the average."""
    dataset = [make_query_event(query_id="q1")]
    traces = {"q1": make_trace("q1", ["e1", "e2"])}
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={
            "q1": GroundTruthLabel(
                query_id="q1", relevant_entity_ids=frozenset({"e-not-retrieved"})
            )
        },
    )
    report = evaluate(dataset, corpus, traces=traces, now=NOW)
    result = result_for(report, EvaluationMetric.MRR)
    assert result.evaluable is True
    assert result.value == 0.0
    assert result.sample_size == 1


def test_mrr_averages_across_multiple_queries() -> None:
    dataset = [make_query_event(query_id="q1"), make_query_event(query_id="q2")]
    traces = {
        "q1": make_trace("q1", ["e1", "e2"]),  # relevant at rank 1 -> RR=1.0
        "q2": make_trace("q2", ["e1", "e2"]),  # relevant at rank 2 -> RR=0.5
    }
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={
            "q1": GroundTruthLabel(query_id="q1", relevant_entity_ids=frozenset({"e1"})),
            "q2": GroundTruthLabel(query_id="q2", relevant_entity_ids=frozenset({"e2"})),
        },
    )
    report = evaluate(dataset, corpus, traces=traces, now=NOW)
    result = result_for(report, EvaluationMetric.MRR)
    assert result.value == pytest.approx((1.0 + 0.5) / 2)
    assert result.sample_size == 2


# --- malformed / incomplete traces: rejected, never fabricated --------------


def test_query_missing_from_traces_mapping_is_excluded_not_fabricated() -> None:
    dataset = [make_query_event(query_id="q1"), make_query_event(query_id="q2")]
    traces = {"q1": make_trace("q1", ["e1"])}  # q2 has no trace at all
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={
            "q1": GroundTruthLabel(query_id="q1", relevant_entity_ids=frozenset({"e1"})),
            "q2": GroundTruthLabel(query_id="q2", relevant_entity_ids=frozenset({"e1"})),
        },
    )
    report = evaluate(dataset, corpus, traces=traces, now=NOW)
    result = result_for(report, EvaluationMetric.MRR)
    # Only q1 contributes -- q2 is silently excluded, not treated as
    # a zero-candidate trace, since none was ever observed for it.
    assert result.sample_size == 1


def test_label_missing_relevant_entity_ids_is_not_evaluable_for_that_query() -> None:
    """A `GroundTruthLabel` that exists but never populated
    `relevant_entity_ids` (e.g. it only carries `should_abstain`) must
    not be treated as "zero relevant entities" -- it is simply not
    usable for the retrieval metrics."""
    dataset = [make_query_event(query_id="q1")]
    traces = {"q1": make_trace("q1", ["e1"])}
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={"q1": GroundTruthLabel(query_id="q1", should_abstain=True)},
    )
    report = evaluate(dataset, corpus, traces=traces, now=NOW)
    for metric in RETRIEVAL_METRICS:
        result = result_for(report, metric)
        assert result.evaluable is False
        assert result.reason is NotEvaluableReason.INSUFFICIENT_SAMPLE


def test_empty_traces_mapping_behaves_identically_to_no_traces() -> None:
    dataset = [make_query_event(query_id="q1")]
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={"q1": GroundTruthLabel(query_id="q1", relevant_entity_ids=frozenset({"e1"}))},
    )
    report_empty = evaluate(dataset, corpus, traces={}, now=NOW)
    report_none = evaluate(dataset, corpus, traces=None, now=NOW)
    for metric in RETRIEVAL_METRICS:
        assert result_for(report_empty, metric) == result_for(report_none, metric)


def test_retrieval_metric_values_never_exceed_the_0_to_1_bound() -> None:
    """Sanity check on the formulas themselves (bounds are also
    enforced by `MetricResult.value`'s own pydantic `Field(ge=0,le=1)`,
    but this proves the *arithmetic* never approaches violating it)."""
    dataset = [make_query_event(query_id="q1")]
    ranked_ids = [f"e{i}" for i in range(20)]
    traces = {"q1": make_trace("q1", ranked_ids)}
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={"q1": GroundTruthLabel(query_id="q1", relevant_entity_ids=frozenset(ranked_ids))},
    )
    report = evaluate(dataset, corpus, traces=traces, now=NOW)
    for metric in RETRIEVAL_METRICS:
        result = result_for(report, metric)
        assert result.value is not None
        assert 0.0 <= result.value <= 1.0


# --- D13-C: graph-version mismatch handling ----------------------------------


def test_retrieval_metric_excludes_a_trace_with_a_mismatched_graph_version() -> None:
    """A trace observed against a different graph snapshot than the
    one telemetry recorded for this query is never silently trusted
    (TAD invariant #5, the same discipline D9's own
    `GraphVersionMismatchError` enforces at execution time)."""
    dataset = [make_query_event(query_id="q1")]  # graph_version_id="repo1:rev1:scip=1.0.0"
    mismatched_trace = make_trace("q1", ["e1"]).model_copy(
        update={"graph_version_id": "repo1:rev2:scip=2.0.0"}
    )
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={"q1": GroundTruthLabel(query_id="q1", relevant_entity_ids=frozenset({"e1"}))},
    )
    report = evaluate(dataset, corpus, traces={"q1": mismatched_trace}, now=NOW)
    for metric in RETRIEVAL_METRICS:
        result = result_for(report, metric)
        assert result.evaluable is False
        assert result.reason is NotEvaluableReason.INSUFFICIENT_SAMPLE


def test_retrieval_metric_computes_normally_when_graph_versions_match() -> None:
    """Control case for the mismatch test above -- proves the check is
    a real filter, not a coincidental always-fail."""
    dataset = [make_query_event(query_id="q1")]
    matching_trace = make_trace("q1", ["e1"])  # same default graph_version_id as the event
    corpus = BenchmarkCorpus(
        corpus_version="bench-v1",
        labels={"q1": GroundTruthLabel(query_id="q1", relevant_entity_ids=frozenset({"e1"}))},
    )
    report = evaluate(dataset, corpus, traces={"q1": matching_trace}, now=NOW)
    result = result_for(report, EvaluationMetric.RECALL_AT_10)
    assert result.evaluable is True
