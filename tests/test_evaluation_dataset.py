"""Tests for `codex.evaluation.dataset.select_dataset` (directive
D13-A): a thin, read-only wrapper over the unmodified D11
`TelemetryStore` -- no new filtering, no mutation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from codex.evaluation.dataset import select_dataset
from codex.telemetry.models import QueryTelemetryEvent
from codex.telemetry.store import InMemoryTelemetryStore
from telemetry_fixtures import make_contract, make_graph_version, make_plan

NOW = datetime(2026, 8, 31, tzinfo=UTC)


def make_query_event(*, query_id: str = "q1", repository_id: str = "repo1") -> QueryTelemetryEvent:
    gv = make_graph_version().model_copy(update={"repository_id": repository_id})
    return QueryTelemetryEvent.build(
        query_id=query_id,
        graph_version=gv,
        query_contract=make_contract(),
        retrieval_plan=make_plan(gv),
        candidate_count=1,
        mss_size=1,
        llm_calls=1,
        now=NOW,
    )


def test_select_dataset_returns_all_events_with_no_filter() -> None:
    store = InMemoryTelemetryStore()
    e1 = make_query_event(query_id="q1")
    e2 = make_query_event(query_id="q2")
    store.record_query_event(e1)
    store.record_query_event(e2)
    assert select_dataset(store) == [e1, e2]


def test_select_dataset_filters_by_repository_id() -> None:
    store = InMemoryTelemetryStore()
    e1 = make_query_event(query_id="q1", repository_id="repo1")
    e2 = make_query_event(query_id="q2", repository_id="repo2")
    store.record_query_event(e1)
    store.record_query_event(e2)
    assert select_dataset(store, repository_id="repo1") == [e1]


def test_select_dataset_filters_by_query_id() -> None:
    store = InMemoryTelemetryStore()
    e1 = make_query_event(query_id="q1")
    e2 = make_query_event(query_id="q2")
    store.record_query_event(e1)
    store.record_query_event(e2)
    assert select_dataset(store, query_id="q2") == [e2]


def test_select_dataset_on_empty_store_is_empty() -> None:
    store = InMemoryTelemetryStore()
    assert select_dataset(store) == []


def test_select_dataset_never_mutates_the_store() -> None:
    store = InMemoryTelemetryStore()
    store.record_query_event(make_query_event())
    before = store.query_events()
    select_dataset(store)
    select_dataset(store, repository_id="repo1")
    assert store.query_events() == before
