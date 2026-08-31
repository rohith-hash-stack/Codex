"""Evaluation (TAD §59's third pipeline stage; directive D13-A).

Computes every metric named by TAD §66 / HLRD §56 (`EvaluationMetric`,
9 values) against a Dataset (a list of D11's unmodified
`QueryTelemetryEvent`) and an optional `BenchmarkCorpus`. **Never
invents a score.** Per metric, exactly one of two things happens:

1. It is structurally excluded -- TAD/HLRD name it, but this codebase
   has no way to compute it without inventing a formula, a denominator,
   or a baseline neither document defines. These seven are excluded
   *unconditionally*, regardless of ground truth, and the reason is
   fixed per metric (see `_STRUCTURALLY_EXCLUDED` below, and
   `docs/architecture-conformance-audit.md` §DD for the full reasoning
   trace behind each one):

   - `PRECISION_AT_10` / `RECALL_AT_10` / `MRR` -- TAD §65's closed
     telemetry schema (D11) records only `candidate_count`/`mss_size`
     as bare counts; no ranked candidate/entity-id list is recorded
     anywhere a Dataset could read it. `RetrievalPlan.target_entity_ids`
     is the query's own *targets* (input side), not a ranked retrieval
     result list -- confirmed by reading `codex.planner.planner`
     before writing this module, not assumed.
   - `FACTUAL_ACCURACY` -- no answer content or correctness flag is
     recorded in telemetry, only the `verification_result` label (the
     same field `CLAIM_VERIFICATION_ACCURACY` already uses below);
     treating them as interchangeable would invent an equivalence
     TAD never states.
   - `UNSUPPORTED_CLAIM_RATE` -- `unsupported_claim_count` is recorded,
     but no "total claims" denominator exists anywhere in the schema,
     and TAD §66 gives no rate formula.
   - `TOKEN_EFFICIENCY` -- HLRD §56's target is "vs naive retrieval,"
     but no naive-retrieval baseline is defined or implemented
     anywhere in Codex.
   - `ASSERTION_TRACEABILITY` -- HLRD names a target percentage but
     never defines what ratio of what population it is computed over.

2. It is computed, given both a real dataset event and a matching
   `GroundTruthLabel` -- exactly two metrics qualify:

   - `CLAIM_VERIFICATION_ACCURACY` = (events whose recorded
     `verification_result` equals the label's
     `expected_verification_status`) / (events with both a recorded
     `verification_result` and such a label) -- directly supported by
     HLRD §57's "ground truth sufficient to measure...Verification."
   - `ABSTENTION_PRECISION` = (abstained events the label says should
     have abstained) / (abstained events with a `should_abstain`
     label), where "abstained" reuses D10's own closed
     `codex.verification.state.to_routing_bucket` mapping
     (`REJECTED -> "ABSTAIN"`) rather than re-deriving abstention
     ad hoc.

   Both fall back to `NOT_EVALUABLE` (`MISSING_GROUND_TRUTH` when no
   corpus/no overlapping label exists at all; `INSUFFICIENT_SAMPLE`
   when the dataset itself is empty or nothing eligible remains after
   filtering) whenever the real ground truth data isn't there --
   **never a fabricated score.**

Nothing here writes to `codex.telemetry`, `codex.artifact`, or any
D1-D12 store or constant -- read-only, proven by
`tests/test_evaluation_boundaries.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from codex.evaluation.models import (
    BenchmarkCorpus,
    EvaluationMetric,
    EvaluationReport,
    MetricResult,
    NotEvaluableReason,
)
from codex.telemetry.models import QueryTelemetryEvent
from codex.verification.state import to_routing_bucket

_STRUCTURALLY_EXCLUDED: dict[EvaluationMetric, NotEvaluableReason] = {
    EvaluationMetric.PRECISION_AT_10: NotEvaluableReason.MISSING_TELEMETRY_DATA,
    EvaluationMetric.RECALL_AT_10: NotEvaluableReason.MISSING_TELEMETRY_DATA,
    EvaluationMetric.MRR: NotEvaluableReason.MISSING_TELEMETRY_DATA,
    EvaluationMetric.FACTUAL_ACCURACY: NotEvaluableReason.MISSING_TELEMETRY_DATA,
    EvaluationMetric.UNSUPPORTED_CLAIM_RATE: NotEvaluableReason.UNDEFINED_FORMULA,
    EvaluationMetric.TOKEN_EFFICIENCY: NotEvaluableReason.UNDEFINED_FORMULA,
    EvaluationMetric.ASSERTION_TRACEABILITY: NotEvaluableReason.UNDEFINED_FORMULA,
}
"""Permanent, unconditional dispositions -- never re-evaluated against
ground truth or dataset content, because no ground truth or dataset
content could make any of them computable without inventing something
TAD/HLRD do not define. Exactly the seven `EvaluationMetric` values not
covered by `_evaluate_claim_verification_accuracy`/
`_evaluate_abstention_precision` below."""


def _not_evaluable(
    metric: EvaluationMetric, reason: NotEvaluableReason, *, sample_size: int = 0
) -> MetricResult:
    return MetricResult(
        metric=metric, evaluable=False, value=None, sample_size=sample_size, reason=reason
    )


def _evaluate_claim_verification_accuracy(
    dataset: Sequence[QueryTelemetryEvent], ground_truth: BenchmarkCorpus | None
) -> MetricResult:
    metric = EvaluationMetric.CLAIM_VERIFICATION_ACCURACY
    if ground_truth is None:
        return _not_evaluable(metric, NotEvaluableReason.MISSING_GROUND_TRUTH)

    matches = 0
    considered = 0
    for event in dataset:
        if event.verification_result is None:
            continue
        label = ground_truth.labels.get(event.query_id)
        if label is None or label.expected_verification_status is None:
            continue
        considered += 1
        if event.verification_result == label.expected_verification_status:
            matches += 1

    if considered == 0:
        return _not_evaluable(metric, NotEvaluableReason.INSUFFICIENT_SAMPLE)

    return MetricResult(
        metric=metric,
        evaluable=True,
        value=matches / considered,
        sample_size=considered,
        reason=None,
    )


def _evaluate_abstention_precision(
    dataset: Sequence[QueryTelemetryEvent], ground_truth: BenchmarkCorpus | None
) -> MetricResult:
    metric = EvaluationMetric.ABSTENTION_PRECISION
    if ground_truth is None:
        return _not_evaluable(metric, NotEvaluableReason.MISSING_GROUND_TRUTH)

    abstained_correctly = 0
    considered = 0
    for event in dataset:
        if event.verification_result is None:
            continue
        if to_routing_bucket(event.verification_result) != "ABSTAIN":
            continue
        label = ground_truth.labels.get(event.query_id)
        if label is None or label.should_abstain is None:
            continue
        considered += 1
        if label.should_abstain:
            abstained_correctly += 1

    if considered == 0:
        return _not_evaluable(metric, NotEvaluableReason.INSUFFICIENT_SAMPLE)

    return MetricResult(
        metric=metric,
        evaluable=True,
        value=abstained_correctly / considered,
        sample_size=considered,
        reason=None,
    )


def evaluate(
    dataset: Sequence[QueryTelemetryEvent],
    ground_truth: BenchmarkCorpus | None = None,
    *,
    now: datetime | None = None,
) -> EvaluationReport:
    """Compute every `EvaluationMetric` against `dataset`/`ground_truth`.
    Read-only: never mutates `dataset`, `ground_truth`, or any store.
    Deterministic for identical inputs (including an identical explicit
    `now`) -- no randomness anywhere in this module."""
    results: list[MetricResult] = []
    for metric, reason in _STRUCTURALLY_EXCLUDED.items():
        results.append(_not_evaluable(metric, reason))
    results.append(_evaluate_claim_verification_accuracy(dataset, ground_truth))
    results.append(_evaluate_abstention_precision(dataset, ground_truth))
    # Stable, deterministic ordering matching `EvaluationMetric`'s own
    # declaration order, not insertion order (dict + two appends above).
    results.sort(key=lambda r: list(EvaluationMetric).index(r.metric))

    return EvaluationReport(
        generated_at=now or datetime.now(UTC),
        dataset_size=len(dataset),
        corpus_version=ground_truth.corpus_version if ground_truth is not None else None,
        results=results,
    )


__all__ = ["evaluate"]
