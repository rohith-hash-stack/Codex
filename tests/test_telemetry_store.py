"""Behavioral tests for `codex.telemetry.store.InMemoryTelemetryStore`
(directive D11): append-only, filtered reads, no cross-store leakage.
"""

from __future__ import annotations

from datetime import UTC, datetime

from codex.telemetry.models import FailureCode, FailureTelemetryEvent, QueryTelemetryEvent
from codex.telemetry.store import InMemoryTelemetryStore
from telemetry_fixtures import make_contract, make_graph_version, make_plan

NOW = datetime(2026, 8, 31, tzinfo=UTC)


def make_query_event(*, query_id: str = "q1", repository_id: str = "repo1") -> QueryTelemetryEvent:
    gv = make_graph_version()
    gv = gv.model_copy(update={"repository_id": repository_id})
    plan = make_plan(gv)
    return QueryTelemetryEvent.build(
        query_id=query_id,
        graph_version=gv,
        query_contract=make_contract(),
        retrieval_plan=plan,
        candidate_count=1,
        mss_size=1,
        llm_calls=1,
        now=NOW,
    )


def make_failure_event(
    *, code: FailureCode = FailureCode.PROVIDER_TIMEOUT, repository_id: str = "repo1"
) -> FailureTelemetryEvent:
    return FailureTelemetryEvent.build(code=code, repository_id=repository_id, now=NOW)


# --- normal recording --------------------------------------------------


def test_recorded_query_event_is_retrievable() -> None:
    store = InMemoryTelemetryStore()
    event = make_query_event()
    store.record_query_event(event)
    assert store.query_events() == [event]


def test_recorded_failure_event_is_retrievable() -> None:
    store = InMemoryTelemetryStore()
    event = make_failure_event()
    store.record_failure_event(event)
    assert store.failure_events() == [event]


def test_empty_store_returns_empty_lists() -> None:
    store = InMemoryTelemetryStore()
    assert store.query_events() == []
    assert store.failure_events() == []


# --- filtering -----------------------------------------------------------


def test_query_events_filtered_by_repository_id() -> None:
    store = InMemoryTelemetryStore()
    store.record_query_event(make_query_event(repository_id="repo1"))
    store.record_query_event(make_query_event(repository_id="repo2"))
    results = store.query_events(repository_id="repo1")
    assert len(results) == 1
    assert results[0].repository_id == "repo1"


def test_query_events_filtered_by_query_id() -> None:
    store = InMemoryTelemetryStore()
    store.record_query_event(make_query_event(query_id="q1"))
    store.record_query_event(make_query_event(query_id="q2"))
    results = store.query_events(query_id="q2")
    assert len(results) == 1
    assert results[0].query_id == "q2"


def test_failure_events_filtered_by_code() -> None:
    store = InMemoryTelemetryStore()
    store.record_failure_event(make_failure_event(code=FailureCode.PROVIDER_TIMEOUT))
    store.record_failure_event(make_failure_event(code=FailureCode.CONCURRENT_UPDATE_DETECTED))
    results = store.failure_events(code=FailureCode.CONCURRENT_UPDATE_DETECTED)
    assert len(results) == 1
    assert results[0].code is FailureCode.CONCURRENT_UPDATE_DETECTED


def test_failure_events_filtered_by_repository_id() -> None:
    store = InMemoryTelemetryStore()
    store.record_failure_event(make_failure_event(repository_id="repo1"))
    store.record_failure_event(make_failure_event(repository_id="repo2"))
    results = store.failure_events(repository_id="repo2")
    assert len(results) == 1
    assert results[0].repository_id == "repo2"


def test_query_and_failure_events_are_stored_independently() -> None:
    """Recording a failure event never appears in query_events() and
    vice versa -- two genuinely separate append-only logs."""
    store = InMemoryTelemetryStore()
    store.record_query_event(make_query_event())
    store.record_failure_event(make_failure_event())
    assert len(store.query_events()) == 1
    assert len(store.failure_events()) == 1


# --- append-only behavior -------------------------------------------------


def test_multiple_events_all_retained_in_order() -> None:
    store = InMemoryTelemetryStore()
    first = make_query_event(query_id="q1")
    second = make_query_event(query_id="q2")
    store.record_query_event(first)
    store.record_query_event(second)
    assert store.query_events() == [first, second]


def test_mutating_returned_list_does_not_affect_the_store() -> None:
    store = InMemoryTelemetryStore()
    store.record_query_event(make_query_event())
    results = store.query_events()
    results.clear()
    assert len(store.query_events()) == 1


def test_no_update_or_delete_method_exists() -> None:
    """Append-only, by construction -- the store offers no method that
    could remove or replace an already-recorded event."""
    public_methods = {
        name
        for name in dir(InMemoryTelemetryStore)
        if not name.startswith("_") and callable(getattr(InMemoryTelemetryStore, name))
    }
    assert public_methods == {
        "record_query_event",
        "record_failure_event",
        "query_events",
        "failure_events",
    }
