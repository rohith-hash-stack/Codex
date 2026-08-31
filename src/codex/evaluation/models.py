"""Evaluation data model (TAD component #18's Dataset/Evaluation stages
only, TAD §59; directive D13-A -- the narrow, research-backed slice of
Offline Calibration Pipeline approved after `docs/architecture-
conformance-audit.md` §BB (STOP) and §CC (evidence-recovery pass)).

**Scope, per the approved D13-A decisions:**

1. Read-only evaluation infrastructure. Nothing here writes a
   calibration parameter, tunes a constant, collects feedback, or
   implements retention/Shadow/Canary/Production. See `codex.evaluation`
   package docstring for the full boundary.
2. Ground truth is never fabricated. `BenchmarkCorpus` is an input
   contract a caller supplies from a real, versioned benchmark corpus
   (HLRD §56: "Evaluation SHALL use a versioned benchmark corpus with
   validated ground truth"; HLRD §57: corpus/coverage requirements).
   No corpus is bundled, generated, or assumed by this module.
3. Only metrics explicitly named by TAD §66 / HLRD §56 are modeled
   (`EvaluationMetric`, 9 values, deduplicated across both documents).
   Every metric that cannot be computed without inventing a formula,
   a denominator, or a baseline TAD/HLRD never define produces a
   deterministic `NOT_EVALUABLE` disposition -- see `NotEvaluableReason`
   and `codex.evaluation.evaluate`'s module docstring for the exact,
   per-metric reasoning (`docs/architecture-conformance-audit.md` §DD).

**D13-B addition (`docs/architecture-conformance-audit.md` §EE):**
`EvaluationTrace`/`RankedCandidate` and `GroundTruthLabel.
relevant_entity_ids` make `PRECISION_AT_10`/`RECALL_AT_10`/`MRR`
conditionally computable -- given both a real, passively-observed
`EvaluationTrace` (`codex.evaluation.observer`, never fabricated) and a
ground-truth relevance set (never fabricated by this package). Neither
is bundled or generated here; both remain caller-supplied real data,
exactly the same non-fabrication discipline §CC/§DD already established
for `BenchmarkCorpus`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from codex.verification.state import VerificationStatus


class EvaluationMetric(StrEnum):
    """The complete, deduplicated set of metrics TAD §66 (Key Metrics)
    and HLRD §56 (Quantitative V1 Success Criteria) name by name. No
    metric outside this set is modeled -- per the directive, "implement
    only metrics explicitly defined by TAD §66 / HLRD §56."""

    PRECISION_AT_10 = "PRECISION_AT_10"
    """TAD §66, Retrieval."""
    RECALL_AT_10 = "RECALL_AT_10"
    """TAD §66, Retrieval."""
    MRR = "MRR"
    """TAD §66, Retrieval."""
    FACTUAL_ACCURACY = "FACTUAL_ACCURACY"
    """TAD §66, Answer; HLRD §56 target > 0.85."""
    CLAIM_VERIFICATION_ACCURACY = "CLAIM_VERIFICATION_ACCURACY"
    """TAD §66, Answer."""
    UNSUPPORTED_CLAIM_RATE = "UNSUPPORTED_CLAIM_RATE"
    """TAD §66, Answer."""
    ABSTENTION_PRECISION = "ABSTENTION_PRECISION"
    """TAD §66, Answer."""
    TOKEN_EFFICIENCY = "TOKEN_EFFICIENCY"
    """HLRD §56 target ">= 50% reduction vs naive retrieval". Not named
    in TAD §66's own Retrieval/Answer lists."""
    ASSERTION_TRACEABILITY = "ASSERTION_TRACEABILITY"
    """HLRD §56 target ">= 90%" (as "Assertion traceability"); HLRD
    §37/line 1396 area states Codex SHALL provide traceability, without
    a computable ratio definition. Not named in TAD §66."""


class NotEvaluableReason(StrEnum):
    """Why a metric could not be scored -- always one of these four,
    never a silently-invented value. Distinguishing them is itself
    evidence, not decoration: a caller can tell "supply ground truth"
    (`MISSING_GROUND_TRUTH`) apart from "this will never be computable
    without a schema/architecture change" (`MISSING_TELEMETRY_DATA`,
    `UNDEFINED_FORMULA`) apart from "there was nothing to measure this
    run" (`INSUFFICIENT_SAMPLE`)."""

    MISSING_GROUND_TRUTH = "MISSING_GROUND_TRUTH"
    """No `BenchmarkCorpus` was supplied, or none of its labels
    overlapped the evaluated dataset's query ids."""
    MISSING_TELEMETRY_DATA = "MISSING_TELEMETRY_DATA"
    """TAD §65's closed `QueryTelemetryEvent` schema (D11, unchanged)
    does not record the raw data this metric needs, regardless of
    ground truth -- e.g. no ranked candidate/entity-id list is
    recorded, only `candidate_count`/`mss_size` bare counts."""
    UNDEFINED_FORMULA = "UNDEFINED_FORMULA"
    """TAD/HLRD name the metric but never give a formula, denominator,
    or baseline definition precise enough to compute without
    inventing one -- permanently excluded, not merely blocked."""
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    """Ground truth and a computable formula both exist, but this
    particular dataset produced zero eligible data points (e.g. an
    empty dataset, or no query in it matched a supplied label)."""


class GroundTruthLabel(BaseModel):
    """One query's ground-truth label, supplied by a real benchmark
    corpus (HLRD §56-57) -- never fabricated here. Deliberately minimal:
    only the two fields the two computable metrics (`evaluate.py`) need.
    HLRD §57's fuller corpus characterization (repository size/query
    category) is a corpus-*construction* concern, out of this slice's
    scope, and is not modeled as a field here to avoid inventing a
    schema HLRD only describes in prose."""

    query_id: str
    expected_verification_status: VerificationStatus | None = None
    """Ground truth for `CLAIM_VERIFICATION_ACCURACY`: what the
    Verification Engine's status *should* have been for this query."""
    should_abstain: bool | None = None
    """Ground truth for `ABSTENTION_PRECISION`: whether the correct
    behavior for this query was to abstain (no sufficient evidence)."""
    relevant_entity_ids: frozenset[str] | None = None
    """Ground truth for `PRECISION_AT_10`/`RECALL_AT_10`/`MRR` (D13-B):
    the canonical entity ids that are actually relevant to this query,
    per a real benchmark corpus (HLRD §57: "ground truth sufficient to
    measure...Retrieval"). Never populated by this package -- a corpus
    supplies it, the same as every other `GroundTruthLabel` field."""


class BenchmarkCorpus(BaseModel):
    """A versioned benchmark corpus (HLRD §56: "Evaluation SHALL use a
    versioned benchmark corpus with validated ground truth") -- an
    input contract, never generated or fabricated by this package.
    `corpus_version` is required and non-empty so every `EvaluationReport`
    can record exactly which corpus produced it (HLRD §56's own
    "versioned" requirement), the same provenance-via-containing-record
    pattern already established for Telemetry/Artifact Store (D11/D12)."""

    corpus_version: str = Field(min_length=1)
    labels: dict[str, GroundTruthLabel] = Field(default_factory=dict)


class RankedCandidate(BaseModel):
    """One entity as it appeared in D9's real ranked retrieval output
    (`codex.planner.ranking.rank_entities`) -- passively observed
    (`codex.evaluation.observer`), never recomputed with different
    logic or reordered. `score` is provably bounded `[0,1]`: TAD §37's
    weighted sum uses weights summing to exactly 1.0 over four signals
    each already bounded `[0,1]` (`docs/architecture-conformance-
    audit.md` §EE.1)."""

    entity_id: str
    """The candidate's real, unmodified canonical id -- never
    re-derived, never truncated, never obfuscated."""
    score: float = Field(ge=0.0, le=1.0)
    rank: int = Field(ge=1)
    """1-based position in D9's real ranked order (rank 1 = top),
    matching the standard IR convention Mean Reciprocal Rank (`1/rank`)
    itself depends on."""


class EvaluationTrace(BaseModel):
    """A passive, read-only observation of one query's real D9 ranked
    retrieval output (`codex.evaluation.observer.observe_ranked_
    candidates`) -- **observational, never authoritative**: it carries
    no independent claim about what D9 *should* have returned, only a
    faithful record of what it *did* return for this exact
    `RetrievalPlan`/`GraphReader` pair. No TAD/HLRD section prescribes
    this schema (confirmed by direct grep, `docs/architecture-
    conformance-audit.md` §EE.2) -- this is the minimum structure
    needed to make `PRECISION_AT_10`/`RECALL_AT_10`/`MRR` computable,
    with no field added beyond that need."""

    query_identity: str
    repository_id: str
    graph_version_id: str
    ordered_candidates: list[RankedCandidate] = Field(default_factory=list)


class MetricResult(BaseModel):
    """One metric's disposition for one `evaluate()` call. `value` is
    `None` whenever `evaluable` is `False` -- a `NOT_EVALUABLE` result
    never carries a number, invented or otherwise."""

    metric: EvaluationMetric
    evaluable: bool
    value: float | None = Field(default=None, ge=0.0, le=1.0)
    sample_size: int = Field(default=0, ge=0)
    reason: NotEvaluableReason | None = None


class EvaluationReport(BaseModel):
    """The full, deterministic output of one `evaluate()` call -- one
    `MetricResult` per `EvaluationMetric` value, always all nine,
    whatever their disposition."""

    generated_at: datetime
    dataset_size: int = Field(ge=0)
    corpus_version: str | None = None
    results: list[MetricResult]


__all__ = [
    "BenchmarkCorpus",
    "EvaluationMetric",
    "EvaluationReport",
    "EvaluationTrace",
    "GroundTruthLabel",
    "MetricResult",
    "NotEvaluableReason",
    "RankedCandidate",
]
