"""D13-A/D13-B/D13-C: the narrow Dataset/Evaluation/Observer/Benchmark
slice of Offline Calibration Pipeline (TAD component #18, TAD §59) --
approved after `docs/architecture-conformance-audit.md` §BB (STOP),
§CC (evidence-recovery pass), §EE (D13-B: Passive Evaluation Observer),
and §FF (D13-C: Benchmark Corpus / Ground Truth).

**What this package is:** a read-only reporting layer. `select_dataset`
(TAD §59's Dataset stage) reads existing, unmodified D11
`TelemetryStore` records. `observe_ranked_candidates` (D13-B) passively
reconstructs D9's real ranked retrieval output for one `RetrievalPlan`
by replaying D9's own exported, pure `bounded_traversal`/`rank_entities`
functions -- never a fabricated or approximated ordering, and never a
change to `codex.planner` itself. `evaluate` (TAD §59's Evaluation
stage) scores a Dataset against an optional, caller-supplied
`BenchmarkCorpus` (HLRD §56-57) and an optional `traces` mapping of
`EvaluationTrace` records -- neither ever fabricated by this package --
producing a deterministic `EvaluationReport` where every metric
TAD/HLRD cannot ground-truthfully compute resolves to `NOT_EVALUABLE`,
never an invented score.

**What this package is explicitly NOT** (per the approved D13-A/D13-B
decisions -- see `docs/architecture-conformance-audit.md` §DD/§EE):

- Not Calibration. Nothing here writes, tunes, or recommends a new
  value for any existing calibration-point constant
  (`RANKING_WEIGHTS`, `DEFAULT_FRESHNESS_HALF_LIFE`, the SLM
  thresholds, `DECAY_HALF_LIFE_QUERIES`, or the budget constants).
- Not a retrieval mechanism. `observe_ranked_candidates` never runs
  before, during, or in place of `execute_query` -- it cannot
  influence which candidates D9 itself returns.
- Not feedback collection. No `FeedbackRecord` producer is added.
- Not a retention/lifecycle policy. This package stores nothing of
  its own; `EvaluationReport`/`EvaluationTrace` are plain return
  values.
- Not Shadow/Canary/Production. No deployment/promotion mechanism.
- Does not reopen D7 or modify D1-D12 behavior/contracts (including
  `codex.planner` itself, which this package only reads from through
  its already-exported pure functions), and does not modify HLRD/TAD.
- Not a persistence/storage layer. `BenchmarkCase`/`BenchmarkCorpus`
  (D13-C) are plain caller-held values, never stored, never given
  retention/TTL semantics -- no benchmark storage/retention decision
  was made or was needed.
"""

from codex.evaluation.benchmark import verify_case_execution
from codex.evaluation.dataset import select_dataset
from codex.evaluation.evaluate import evaluate
from codex.evaluation.models import (
    BenchmarkCase,
    BenchmarkCorpus,
    EvaluationMetric,
    EvaluationReport,
    EvaluationTrace,
    GroundTruthLabel,
    MetricResult,
    NotEvaluableReason,
    RankedCandidate,
)
from codex.evaluation.observer import observe_ranked_candidates

__all__ = [
    "BenchmarkCase",
    "BenchmarkCorpus",
    "EvaluationMetric",
    "EvaluationReport",
    "EvaluationTrace",
    "GroundTruthLabel",
    "MetricResult",
    "NotEvaluableReason",
    "RankedCandidate",
    "evaluate",
    "observe_ranked_candidates",
    "select_dataset",
    "verify_case_execution",
]
