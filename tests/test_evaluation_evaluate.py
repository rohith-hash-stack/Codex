"""Behavioral tests for `codex.evaluation.evaluate.evaluate` (directive
D13-A): every metric TAD/HLRD cannot ground-truthfully compute is
`NOT_EVALUABLE` with a fixed, correct reason; the two computable
metrics (`CLAIM_VERIFICATION_ACCURACY`, `ABSTENTION_PRECISION`) are
computed correctly given real ground truth and never otherwise;
determinism; no mutation of inputs.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from codex.evaluation.evaluate import evaluate
from codex.evaluation.models import (
    BenchmarkCorpus,
    EvaluationMetric,
    GroundTruthLabel,
    NotEvaluableReason,
)
from codex.telemetry.models import QueryTelemetryEvent
from codex.verification.state import VerificationStatus
from telemetry_fixtures import make_contract, make_graph_version, make_plan

NOW = datetime(2026, 8, 31, tzinfo=UTC)

STRUCTURALLY_EXCLUDED = {
    EvaluationMetric.PRECISION_AT_10: NotEvaluableReason.MISSING_TELEMETRY_DATA,
    EvaluationMetric.RECALL_AT_10: NotEvaluableReason.MISSING_TELEMETRY_DATA,
    EvaluationMetric.MRR: NotEvaluableReason.MISSING_TELEMETRY_DATA,
    EvaluationMetric.FACTUAL_ACCURACY: NotEvaluableReason.MISSING_TELEMETRY_DATA,
    EvaluationMetric.UNSUPPORTED_CLAIM_RATE: NotEvaluableReason.UNDEFINED_FORMULA,
    EvaluationMetric.TOKEN_EFFICIENCY: NotEvaluableReason.UNDEFINED_FORMULA,
    EvaluationMetric.ASSERTION_TRACEABILITY: NotEvaluableReason.UNDEFINED_FORMULA,
}


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
