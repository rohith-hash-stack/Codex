"""Model-level tests for `codex.evaluation.models` (directive D13-A):
`EvaluationMetric` covers exactly TAD §66/HLRD §56's named metrics,
`BenchmarkCorpus` requires a non-empty version, `MetricResult` never
carries a value when not evaluable.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codex.evaluation.models import (
    BenchmarkCorpus,
    EvaluationMetric,
    GroundTruthLabel,
    MetricResult,
    NotEvaluableReason,
)
from codex.verification.state import VerificationStatus


def test_evaluation_metric_has_exactly_nine_values() -> None:
    """TAD §66 (7 metrics) + HLRD §56's two HLRD-only targets (Token
    efficiency, Assertion traceability) = 9 unique names, no more."""
    assert len(list(EvaluationMetric)) == 9


def test_benchmark_corpus_requires_nonempty_version() -> None:
    with pytest.raises(ValidationError):
        BenchmarkCorpus(corpus_version="")


def test_benchmark_corpus_defaults_to_no_labels() -> None:
    corpus = BenchmarkCorpus(corpus_version="bench-v1")
    assert corpus.labels == {}


def test_ground_truth_label_both_fields_optional() -> None:
    label = GroundTruthLabel(query_id="q1")
    assert label.expected_verification_status is None
    assert label.should_abstain is None


def test_ground_truth_label_can_carry_both_fields() -> None:
    label = GroundTruthLabel(
        query_id="q1",
        expected_verification_status=VerificationStatus.VERIFIED,
        should_abstain=False,
    )
    assert label.expected_verification_status is VerificationStatus.VERIFIED
    assert label.should_abstain is False


def test_metric_result_not_evaluable_has_no_value() -> None:
    result = MetricResult(
        metric=EvaluationMetric.MRR,
        evaluable=False,
        reason=NotEvaluableReason.MISSING_TELEMETRY_DATA,
    )
    assert result.value is None
    assert result.sample_size == 0


def test_metric_result_value_bounded_zero_to_one() -> None:
    with pytest.raises(ValidationError):
        MetricResult(metric=EvaluationMetric.MRR, evaluable=True, value=1.5, sample_size=1)
    with pytest.raises(ValidationError):
        MetricResult(metric=EvaluationMetric.MRR, evaluable=True, value=-0.1, sample_size=1)
