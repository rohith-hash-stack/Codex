"""Dataset construction (TAD §59's second pipeline stage; directive
D13-A). Per `docs/architecture-conformance-audit.md` §CC.2: the record
schema a Dataset draws from is already closed (TAD §65, D11) and is
reused here completely unchanged -- this module adds no new field, no
new store, and no write path. "Building a Dataset" is, for this narrow
slice, exactly what it looks like below: a thin, named, read-only
selection over the existing `TelemetryStore`.
"""

from __future__ import annotations

from codex.telemetry.models import QueryTelemetryEvent
from codex.telemetry.store import TelemetryStore


def select_dataset(
    store: TelemetryStore,
    *,
    repository_id: str | None = None,
    query_id: str | None = None,
) -> list[QueryTelemetryEvent]:
    """Select a Dataset (TAD §59) from an existing, unmodified
    `TelemetryStore` -- never mutates `store`, never invents a field
    `QueryTelemetryEvent` doesn't already carry. Filtering is exactly
    `TelemetryStore.query_events`'s own existing filter surface (D11);
    no new selection/sampling rule is introduced."""
    return store.query_events(repository_id=repository_id, query_id=query_id)


__all__ = ["select_dataset"]
