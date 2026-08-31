"""Maps an already-detected `GraphVersionMismatchError` (TAD §55's
`CONCURRENT_UPDATE_DETECTED`, D9) into a recordable `FailureTelemetryEvent`
(directive D11, approved decision 2).

**Detection stays in D9.** This module never compares graph versions,
never catches an exception it wasn't handed, and never decides whether
a mismatch occurred -- `codex.planner.planner.execute_query` already
did that (it raises `GraphVersionMismatchError` the moment
`graph.version.version_id != plan.graph_version.version_id`). This
function's only job is translating an already-raised exception plus
the `RetrievalPlan` that was locked at plan time into a typed,
storable record -- a pure mapping, not a second detector.
"""

from __future__ import annotations

from datetime import datetime

from codex.planner.models import RetrievalPlan
from codex.planner.planner import GraphVersionMismatchError
from codex.telemetry.models import FailureCode, FailureTelemetryEvent


def failure_event_from_graph_version_mismatch(
    exc: GraphVersionMismatchError,
    *,
    plan: RetrievalPlan,
    query_id: str,
    now: datetime | None = None,
) -> FailureTelemetryEvent:
    """Build the `CONCURRENT_UPDATE_DETECTED` telemetry record for an
    already-raised `GraphVersionMismatchError`.

    Intended call shape, at the orchestration boundary above D9 (no
    such boundary exists yet anywhere in this codebase -- see
    `docs/architecture-conformance-audit.md` §X.5; this function is
    the piece a future orchestrator would call)::

        try:
            package = execute_query(plan, graph=graph, ...)
        except GraphVersionMismatchError as exc:
            event = failure_event_from_graph_version_mismatch(
                exc, plan=plan, query_id=query_id
            )
            telemetry_store.record_failure_event(event)
            raise  # D9's own refusal-to-proceed behavior is unchanged
    """
    return FailureTelemetryEvent.build(
        code=FailureCode.CONCURRENT_UPDATE_DETECTED,
        repository_id=plan.graph_version.repository_id,
        query_id=query_id,
        graph_version_id=plan.graph_version.version_id,
        detail=str(exc),
        now=now,
    )


__all__ = ["failure_event_from_graph_version_mismatch"]
