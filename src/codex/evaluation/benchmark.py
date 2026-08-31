"""Benchmark-case execution consistency checks (directive D13-C).

`verify_case_execution` is a small, pure, read-only helper -- it never
runs anything itself, never constructs a `QueryContract`/`RetrievalPlan`,
and never touches `codex.planner`. It only compares fields already
present on two already-real values (a `BenchmarkCase` and the real
`QueryTelemetryEvent` a caller separately produced by actually running
Codex against it) -- proving a benchmark case was executed against the
repository/revision it declares, per the directive's required
"repository/revision mismatch handling" test category.
"""

from __future__ import annotations

from codex.evaluation.models import BenchmarkCase
from codex.telemetry.models import QueryTelemetryEvent


def verify_case_execution(case: BenchmarkCase, event: QueryTelemetryEvent) -> bool:
    """`True` iff `event` is a real execution of exactly `case`: the
    same `query_id`, the same `repository_id`, and the same
    `repository_revision` (reached via `event.retrieval_plan.
    graph_version.repository_revision` -- `QueryTelemetryEvent` embeds
    the full, real `GraphVersion`, TAD §65, so no new field is needed
    to check this). Never raises -- a caller decides what a mismatch
    means for their own evaluation run."""
    return (
        event.query_id == case.query_id
        and event.repository_id == case.repository_id
        and event.retrieval_plan.graph_version.repository_revision == case.repository_revision
    )


__all__ = ["verify_case_execution"]
