"""Evaluation (TAD §59's third pipeline stage; directives D13-A, D13-B).

Computes every metric named by TAD §66 / HLRD §56 (`EvaluationMetric`,
9 values) against a Dataset (a list of D11's unmodified
`QueryTelemetryEvent`), an optional `BenchmarkCorpus`, and (D13-B) an
optional `traces` mapping of passively-observed `EvaluationTrace`
records. **Never invents a score.** Per metric, exactly one of three
things happens:

1. It is structurally excluded -- TAD/HLRD name it, but this codebase
   has no way to compute it without inventing a formula, a denominator,
   or a baseline neither document defines. These four are excluded
   *unconditionally*, regardless of ground truth or traces, and the
   reason is fixed per metric (see `_STRUCTURALLY_EXCLUDED` below, and
   `docs/architecture-conformance-audit.md` §DD/§EE for the full
   reasoning trace behind each one):

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

2. It is computed given a real dataset event and a matching
   `GroundTruthLabel` -- `CLAIM_VERIFICATION_ACCURACY` and
   `ABSTENTION_PRECISION` (directive D13-A, unchanged):

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

3. It is computed given both a real, passively-observed
   `EvaluationTrace` (`traces[query_id]`, D13-B, never fabricated) and
   a `GroundTruthLabel.relevant_entity_ids` (never fabricated) --
   `PRECISION_AT_10`, `RECALL_AT_10`, `MRR` (directive D13-B). All
   three are the standard, externally-defined Information Retrieval
   formulas (not a Codex-specific invention), applied per query and
   averaged across every query with both a trace and a relevance
   label:

   - `PRECISION_AT_10` = mean over eligible queries of
     |{relevant ids among rank<=10 candidates}| / 10 (the standard
     fixed-denominator definition; a query with fewer than 10 total
     candidates is not treated specially -- unretrieved slots count
     as non-relevant, the conventional IR reading).
   - `RECALL_AT_10` = mean over eligible queries of |{relevant ids
     among rank<=10 candidates}| / |relevant_entity_ids|, skipping any
     query whose `relevant_entity_ids` is empty (the denominator would
     be undefined, 0/0, not zero).
   - `MRR` = mean over eligible queries of `1/rank` of the first
     `ordered_candidates` entry (by ascending rank) whose `entity_id`
     is in `relevant_entity_ids`, or `0.0` if no candidate for that
     query is relevant (the standard convention -- a query
     contributes 0, it is not excluded from the average, once it *is*
     eligible for scoring at all).

All three types 2/3 metrics fall back to `NOT_EVALUABLE`
(`MISSING_TELEMETRY_DATA` when no `traces` mapping is supplied at all
-- the D13-B observational data itself is absent; `MISSING_GROUND_TRUTH`
when no corpus, or no overlapping/populated label, exists;
`INSUFFICIENT_SAMPLE` when the dataset itself is empty or nothing
eligible remains after filtering) whenever the real data isn't there
-- **never a fabricated score.**

Nothing here writes to `codex.telemetry`, `codex.artifact`,
`codex.planner`, or any D1-D12 store or constant -- read-only, proven
by `tests/test_evaluation_boundaries.py`.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime

from codex.evaluation.models import (
    BenchmarkCorpus,
    EvaluationMetric,
    EvaluationReport,
    EvaluationTrace,
    MetricResult,
    NotEvaluableReason,
)
from codex.telemetry.models import QueryTelemetryEvent
from codex.verification.state import to_routing_bucket

_STRUCTURALLY_EXCLUDED: dict[EvaluationMetric, NotEvaluableReason] = {
    EvaluationMetric.FACTUAL_ACCURACY: NotEvaluableReason.MISSING_TELEMETRY_DATA,
    EvaluationMetric.UNSUPPORTED_CLAIM_RATE: NotEvaluableReason.UNDEFINED_FORMULA,
    EvaluationMetric.TOKEN_EFFICIENCY: NotEvaluableReason.UNDEFINED_FORMULA,
    EvaluationMetric.ASSERTION_TRACEABILITY: NotEvaluableReason.UNDEFINED_FORMULA,
}
"""Permanent, unconditional dispositions -- never re-evaluated against
ground truth, dataset content, or traces, because none of those could
make any of them computable without inventing something TAD/HLRD do
not define. `PRECISION_AT_10`/`RECALL_AT_10`/`MRR` are deliberately
*not* in this dict as of D13-B -- they are now conditionally
computable given a real `EvaluationTrace` (see `_evaluate_precision_
at_10`/`_evaluate_recall_at_10`/`_evaluate_mrr` below)."""

_TOP_K = 10


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


def _eligible_trace_label_pairs(
    dataset: Sequence[QueryTelemetryEvent],
    ground_truth: BenchmarkCorpus,
    traces: Mapping[str, EvaluationTrace],
) -> Iterator[tuple[EvaluationTrace, frozenset[str]]]:
    for event in dataset:
        trace = traces.get(event.query_id)
        if trace is None:
            continue
        label = ground_truth.labels.get(event.query_id)
        if label is None or label.relevant_entity_ids is None:
            continue
        yield trace, label.relevant_entity_ids


def _evaluate_precision_at_10(
    dataset: Sequence[QueryTelemetryEvent],
    ground_truth: BenchmarkCorpus | None,
    traces: Mapping[str, EvaluationTrace] | None,
) -> MetricResult:
    metric = EvaluationMetric.PRECISION_AT_10
    if not traces:
        return _not_evaluable(metric, NotEvaluableReason.MISSING_TELEMETRY_DATA)
    if ground_truth is None:
        return _not_evaluable(metric, NotEvaluableReason.MISSING_GROUND_TRUTH)

    per_query: list[float] = []
    for trace, relevant in _eligible_trace_label_pairs(dataset, ground_truth, traces):
        hits = sum(
            1
            for candidate in trace.ordered_candidates
            if candidate.rank <= _TOP_K and candidate.entity_id in relevant
        )
        per_query.append(hits / _TOP_K)

    if not per_query:
        return _not_evaluable(metric, NotEvaluableReason.INSUFFICIENT_SAMPLE)

    return MetricResult(
        metric=metric,
        evaluable=True,
        value=sum(per_query) / len(per_query),
        sample_size=len(per_query),
        reason=None,
    )


def _evaluate_recall_at_10(
    dataset: Sequence[QueryTelemetryEvent],
    ground_truth: BenchmarkCorpus | None,
    traces: Mapping[str, EvaluationTrace] | None,
) -> MetricResult:
    metric = EvaluationMetric.RECALL_AT_10
    if not traces:
        return _not_evaluable(metric, NotEvaluableReason.MISSING_TELEMETRY_DATA)
    if ground_truth is None:
        return _not_evaluable(metric, NotEvaluableReason.MISSING_GROUND_TRUTH)

    per_query: list[float] = []
    for trace, relevant in _eligible_trace_label_pairs(dataset, ground_truth, traces):
        if not relevant:
            # Zero relevant entities: the denominator is undefined
            # (0/0), not zero -- this query cannot contribute.
            continue
        hits = sum(
            1
            for candidate in trace.ordered_candidates
            if candidate.rank <= _TOP_K and candidate.entity_id in relevant
        )
        per_query.append(hits / len(relevant))

    if not per_query:
        return _not_evaluable(metric, NotEvaluableReason.INSUFFICIENT_SAMPLE)

    return MetricResult(
        metric=metric,
        evaluable=True,
        value=sum(per_query) / len(per_query),
        sample_size=len(per_query),
        reason=None,
    )


def _evaluate_mrr(
    dataset: Sequence[QueryTelemetryEvent],
    ground_truth: BenchmarkCorpus | None,
    traces: Mapping[str, EvaluationTrace] | None,
) -> MetricResult:
    metric = EvaluationMetric.MRR
    if not traces:
        return _not_evaluable(metric, NotEvaluableReason.MISSING_TELEMETRY_DATA)
    if ground_truth is None:
        return _not_evaluable(metric, NotEvaluableReason.MISSING_GROUND_TRUTH)

    reciprocal_ranks: list[float] = []
    for trace, relevant in _eligible_trace_label_pairs(dataset, ground_truth, traces):
        rr = 0.0
        for candidate in sorted(trace.ordered_candidates, key=lambda c: c.rank):
            if candidate.entity_id in relevant:
                rr = 1.0 / candidate.rank
                break
        reciprocal_ranks.append(rr)

    if not reciprocal_ranks:
        return _not_evaluable(metric, NotEvaluableReason.INSUFFICIENT_SAMPLE)

    return MetricResult(
        metric=metric,
        evaluable=True,
        value=sum(reciprocal_ranks) / len(reciprocal_ranks),
        sample_size=len(reciprocal_ranks),
        reason=None,
    )


def evaluate(
    dataset: Sequence[QueryTelemetryEvent],
    ground_truth: BenchmarkCorpus | None = None,
    *,
    traces: Mapping[str, EvaluationTrace] | None = None,
    now: datetime | None = None,
) -> EvaluationReport:
    """Compute every `EvaluationMetric` against `dataset`/`ground_truth`/
    `traces`. Read-only: never mutates `dataset`, `ground_truth`,
    `traces`, or any store. Deterministic for identical inputs
    (including an identical explicit `now`) -- no randomness anywhere
    in this module.

    `traces` (D13-B) maps `query_id -> EvaluationTrace` -- a passively
    observed record of D9's real ranked retrieval output for that
    query (`codex.evaluation.observer.observe_ranked_candidates`).
    Omitting it (the default) leaves `PRECISION_AT_10`/`RECALL_AT_10`/
    `MRR` at `NOT_EVALUABLE`/`MISSING_TELEMETRY_DATA`, identical to
    D13-A's original behavior before this parameter existed."""
    results: list[MetricResult] = []
    for metric, reason in _STRUCTURALLY_EXCLUDED.items():
        results.append(_not_evaluable(metric, reason))
    results.append(_evaluate_claim_verification_accuracy(dataset, ground_truth))
    results.append(_evaluate_abstention_precision(dataset, ground_truth))
    results.append(_evaluate_precision_at_10(dataset, ground_truth, traces))
    results.append(_evaluate_recall_at_10(dataset, ground_truth, traces))
    results.append(_evaluate_mrr(dataset, ground_truth, traces))
    # Stable, deterministic ordering matching `EvaluationMetric`'s own
    # declaration order, not insertion/append order above.
    results.sort(key=lambda r: list(EvaluationMetric).index(r.metric))

    return EvaluationReport(
        generated_at=now or datetime.now(UTC),
        dataset_size=len(dataset),
        corpus_version=ground_truth.corpus_version if ground_truth is not None else None,
        results=results,
    )


__all__ = ["evaluate"]
