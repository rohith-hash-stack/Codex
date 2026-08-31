"""Behavioral tests for TAD §28's session context (directive D8 Phase 8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from codex.query_understanding.models import Intent
from codex.query_understanding.session import (
    MAX_QUERIES,
    MAX_WINDOW,
    RepositoryMismatchError,
    SessionContext,
)

BASE_TIME = datetime(2026, 8, 31, 12, 0, 0, tzinfo=UTC)


def test_repository_isolation_rejects_mismatched_repository() -> None:
    session = SessionContext(repository_id="repo1")
    with pytest.raises(RepositoryMismatchError):
        session.record(
            repository_id="repo2",
            query_text="who calls x",
            intent=Intent.FIND_CALLERS,
            observed_at=BASE_TIME,
        )


def test_context_from_one_repository_never_influences_another() -> None:
    """Two independent sessions for two repositories never share state."""
    session_a = SessionContext(repository_id="repo_a")
    session_b = SessionContext(repository_id="repo_b")
    session_a.record(
        repository_id="repo_a",
        query_text="who calls authenticate",
        intent=Intent.FIND_CALLERS,
        observed_at=BASE_TIME,
    )
    assert session_b.active_entries(now=BASE_TIME) == []
    assert len(session_a.active_entries(now=BASE_TIME)) == 1


def test_sliding_window_caps_at_ten_queries() -> None:
    session = SessionContext(repository_id="repo1")
    for i in range(15):
        session.record(
            repository_id="repo1",
            query_text=f"query {i}",
            intent=Intent.FIND_CALLERS,
            observed_at=BASE_TIME + timedelta(seconds=i),
        )
    active = session.active_entries(now=BASE_TIME + timedelta(seconds=15))
    assert len(active) == MAX_QUERIES
    # The most recent 10 survive -- queries 5..14.
    assert active[0].query_text == "query 5"
    assert active[-1].query_text == "query 14"


def test_thirty_minute_window_expires_old_queries() -> None:
    session = SessionContext(repository_id="repo1")
    session.record(
        repository_id="repo1",
        query_text="old query",
        intent=Intent.FIND_CALLERS,
        observed_at=BASE_TIME,
    )
    later = BASE_TIME + MAX_WINDOW + timedelta(seconds=1)
    assert session.active_entries(now=later) == []


def test_thirty_minute_window_boundary_is_inclusive() -> None:
    session = SessionContext(repository_id="repo1")
    session.record(
        repository_id="repo1",
        query_text="query",
        intent=Intent.FIND_CALLERS,
        observed_at=BASE_TIME,
    )
    exactly_at_boundary = BASE_TIME + MAX_WINDOW
    assert len(session.active_entries(now=exactly_at_boundary)) == 1


def test_context_decays_with_age() -> None:
    session = SessionContext(repository_id="repo1")
    for i in range(3):
        session.record(
            repository_id="repo1",
            query_text=f"query {i}",
            intent=Intent.FIND_CALLERS,
            observed_at=BASE_TIME + timedelta(seconds=i),
        )
    weighted = session.weighted_entries(now=BASE_TIME + timedelta(seconds=3))
    weights = [w for _entry, w in weighted]
    # Most recent first, strictly decreasing weight.
    assert weights == sorted(weights, reverse=True)
    assert weights[0] == 1.0
    assert weights[-1] < weights[0]


def test_explicit_clarification_persists_and_is_flagged() -> None:
    session = SessionContext(repository_id="repo1")
    session.record(
        repository_id="repo1",
        query_text="the second one",
        intent=Intent.FIND_CALLERS,
        observed_at=BASE_TIME,
        is_clarification=True,
    )
    entries = session.active_entries(now=BASE_TIME)
    assert entries[0].is_clarification is True


def test_stale_context_does_not_dominate_a_new_unrelated_query() -> None:
    """Ten authentication queries, then an eleventh, unrelated
    database-migration query -- the window cap evicts the oldest
    authentication entry, and the new query is both the most recent
    entry and the one carrying the highest decay weight (never
    "dominated" by the older, larger block of authentication history)."""
    session = SessionContext(repository_id="repo1")
    for i in range(10):
        session.record(
            repository_id="repo1",
            query_text="authentication",
            intent=Intent.FIND_CALLERS,
            observed_at=BASE_TIME + timedelta(seconds=i),
        )
    session.record(
        repository_id="repo1",
        query_text="database migration",
        intent=Intent.CODE_LOOKUP,
        observed_at=BASE_TIME + timedelta(seconds=10),
    )
    now = BASE_TIME + timedelta(seconds=10)
    active = session.active_entries(now=now)
    assert len(active) == MAX_QUERIES  # the very first "authentication" entry was evicted
    assert active[-1].query_text == "database migration"

    weighted = session.weighted_entries(now=now)
    top_entry, top_weight = weighted[0]
    assert top_entry.query_text == "database migration"
    assert top_weight == 1.0
    assert all(w <= top_weight for _entry, w in weighted)
