"""D13-A: the narrow Dataset/Evaluation slice of Offline Calibration
Pipeline (TAD component #18, TAD §59) -- approved after
`docs/architecture-conformance-audit.md` §BB (STOP) and §CC
(evidence-recovery pass) found five of eight blocking gaps only
PARTIALLY RESOLVED and three genuinely NOT RESOLVED.

**What this package is:** a read-only reporting layer. `select_dataset`
(TAD §59's Dataset stage) reads existing, unmodified D11
`TelemetryStore` records. `evaluate` (TAD §59's Evaluation stage)
scores them against an optional, caller-supplied `BenchmarkCorpus`
(HLRD §56-57) -- never a fabricated one -- producing a deterministic
`EvaluationReport` where every metric TAD/HLRD cannot ground truthfully
compute resolves to `NOT_EVALUABLE`, never an invented score.

**What this package is explicitly NOT** (per the approved D13-A
decisions -- see `docs/architecture-conformance-audit.md` §DD):

- Not Calibration. Nothing here writes, tunes, or recommends a new
  value for any existing calibration-point constant
  (`RANKING_WEIGHTS`, `DEFAULT_FRESHNESS_HALF_LIFE`, the SLM
  thresholds, `DECAY_HALF_LIFE_QUERIES`, or the budget constants).
- Not feedback collection. No `FeedbackRecord` producer is added.
- Not a retention/lifecycle policy. This package stores nothing of
  its own; `EvaluationReport` is a plain return value.
- Not Shadow/Canary/Production. No deployment/promotion mechanism.
- Does not reopen D7 or modify D1-D12 behavior/contracts, and does
  not modify HLRD/TAD.
"""

from codex.evaluation.dataset import select_dataset
from codex.evaluation.evaluate import evaluate
from codex.evaluation.models import (
    BenchmarkCorpus,
    EvaluationMetric,
    EvaluationReport,
    GroundTruthLabel,
    MetricResult,
    NotEvaluableReason,
)

__all__ = [
    "BenchmarkCorpus",
    "EvaluationMetric",
    "EvaluationReport",
    "GroundTruthLabel",
    "MetricResult",
    "NotEvaluableReason",
    "evaluate",
    "select_dataset",
]
