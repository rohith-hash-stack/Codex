"""Telemetry Store interface and in-memory default implementation
(TAD component #16; directive D11).

Storage technology is deferred (no ADR names Telemetry Store
specifically -- `docs/architecture-conformance-audit.md` §X.4 flags
this as a genuine omission in TAD §77's own ADR list, not a
contradiction); this in-memory implementation follows the exact
`EvidenceStore`/`GraphStore` precedent -- a stable Protocol now, real
storage technology later, without reopening this interface.

**Append-only** (directive D11): `record_query_event`/
`record_failure_event` only ever add; nothing in this module offers
an update/delete/clear operation. `list_*` methods return a new list
each call -- mutating a returned list never affects the store.
"""

from __future__ import annotations

from typing import Protocol

from codex.telemetry.models import FailureCode, FailureTelemetryEvent, QueryTelemetryEvent


class TelemetryStore(Protocol):
    """Append-only interface for recording and reading back telemetry
    events. Read-only for every D1-D10 consumer -- nothing in this
    codebase's existing pipeline reads from a `TelemetryStore` (TAD
    §59: production does not perform unrestricted online learning;
    see `docs/architecture-conformance-audit.md` §X.7 for the
    dependency-direction proof this Protocol is designed to satisfy).
    """

    def record_query_event(self, event: QueryTelemetryEvent) -> None: ...

    def record_failure_event(self, event: FailureTelemetryEvent) -> None: ...

    def query_events(
        self,
        *,
        repository_id: str | None = None,
        query_id: str | None = None,
    ) -> list[QueryTelemetryEvent]: ...

    def failure_events(
        self,
        *,
        repository_id: str | None = None,
        code: FailureCode | None = None,
    ) -> list[FailureTelemetryEvent]: ...


class InMemoryTelemetryStore:
    """List-backed ``TelemetryStore`` for development and tests."""

    def __init__(self) -> None:
        self._query_events: list[QueryTelemetryEvent] = []
        self._failure_events: list[FailureTelemetryEvent] = []

    def record_query_event(self, event: QueryTelemetryEvent) -> None:
        self._query_events.append(event)

    def record_failure_event(self, event: FailureTelemetryEvent) -> None:
        self._failure_events.append(event)

    def query_events(
        self,
        *,
        repository_id: str | None = None,
        query_id: str | None = None,
    ) -> list[QueryTelemetryEvent]:
        results = []
        for event in self._query_events:
            if repository_id is not None and event.repository_id != repository_id:
                continue
            if query_id is not None and event.query_id != query_id:
                continue
            results.append(event)
        return results

    def failure_events(
        self,
        *,
        repository_id: str | None = None,
        code: FailureCode | None = None,
    ) -> list[FailureTelemetryEvent]:
        results = []
        for event in self._failure_events:
            if repository_id is not None and event.repository_id != repository_id:
                continue
            if code is not None and event.code != code:
                continue
            results.append(event)
        return results


__all__ = ["InMemoryTelemetryStore", "TelemetryStore"]
