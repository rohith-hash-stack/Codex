"""Behavioral tests for `codex.telemetry.models` (TAD §55, §60, §64-65;
directive D11).
"""

from __future__ import annotations

from datetime import UTC, datetime

from codex.telemetry.models import (
    FailureCode,
    FailureTelemetryEvent,
    FeedbackKind,
    FeedbackRecord,
    QueryTelemetryEvent,
)
from codex.verification.state import VerificationStatus
from telemetry_fixtures import make_contract, make_graph_version, make_plan

NOW = datetime(2026, 8, 31, tzinfo=UTC)


# --- QueryTelemetryEvent.build() -------------------------------------------


def test_build_populates_provenance_fields_from_graph_version() -> None:
    gv = make_graph_version()
    plan = make_plan(gv)
    event = QueryTelemetryEvent.build(
        query_id="q1",
        graph_version=gv,
        query_contract=make_contract(),
        retrieval_plan=plan,
        candidate_count=3,
        mss_size=3,
        llm_calls=1,
        now=NOW,
    )
    assert event.query_id == "q1"
    assert event.repository_id == "repo1"
    assert event.graph_version_id == "repo1:rev1:scip=1.0.0"
    assert event.recorded_at == NOW


def test_build_carries_selected_providers_and_full_retrieval_plan() -> None:
    gv = make_graph_version()
    plan = make_plan(gv)
    event = QueryTelemetryEvent.build(
        query_id="q1",
        graph_version=gv,
        query_contract=make_contract(),
        retrieval_plan=plan,
        candidate_count=3,
        mss_size=3,
        llm_calls=1,
        now=NOW,
    )
    assert event.selected_providers == {"CALL_RELATIONSHIP": ["scip"]}
    assert event.retrieval_plan is plan
    # Provider *version* telemetry is reachable through the embedded plan,
    # per TAD §65's "selected_providers"/"graph_version" fields both being
    # satisfiable from the same already-existing GraphVersion.provider_versions.
    assert event.retrieval_plan.graph_version.provider_versions == {"scip": "1.0.0"}


def test_build_defaults_are_conservative_zero_or_none() -> None:
    gv = make_graph_version()
    plan = make_plan(gv)
    event = QueryTelemetryEvent.build(
        query_id="q1",
        graph_version=gv,
        query_contract=make_contract(),
        retrieval_plan=plan,
        candidate_count=0,
        mss_size=0,
        llm_calls=0,
        now=NOW,
    )
    assert event.llm_tokens is None
    assert event.latency_ms is None
    assert event.verification_result is None
    assert event.unsupported_claim_count == 0
    assert event.contradiction_count == 0
    assert event.cache_hit is False
    assert event.provider_failure_count == 0
    assert event.user_feedback is None


def test_build_carries_verification_result_and_counts_when_supplied() -> None:
    gv = make_graph_version()
    plan = make_plan(gv)
    event = QueryTelemetryEvent.build(
        query_id="q1",
        graph_version=gv,
        query_contract=make_contract(),
        retrieval_plan=plan,
        candidate_count=2,
        mss_size=2,
        llm_calls=1,
        llm_tokens=512,
        latency_ms=123.4,
        verification_result=VerificationStatus.VERIFIED,
        unsupported_claim_count=1,
        contradiction_count=2,
        cache_hit=True,
        provider_failure_count=1,
        user_feedback=FeedbackRecord(kind=FeedbackKind.THUMBS_UP),
        now=NOW,
    )
    assert event.llm_tokens == 512
    assert event.latency_ms == 123.4
    assert event.verification_result is VerificationStatus.VERIFIED
    assert event.unsupported_claim_count == 1
    assert event.contradiction_count == 2
    assert event.cache_hit is True
    assert event.provider_failure_count == 1
    assert event.user_feedback == FeedbackRecord(kind=FeedbackKind.THUMBS_UP)


def test_deterministic_event_id_for_identical_inputs() -> None:
    gv = make_graph_version()
    plan = make_plan(gv)
    event_a = QueryTelemetryEvent.build(
        query_id="q1",
        graph_version=gv,
        query_contract=make_contract(),
        retrieval_plan=plan,
        candidate_count=3,
        mss_size=3,
        llm_calls=1,
        now=NOW,
    )
    event_b = QueryTelemetryEvent.build(
        query_id="q1",
        graph_version=gv,
        query_contract=make_contract(),
        retrieval_plan=plan,
        candidate_count=3,
        mss_size=3,
        llm_calls=1,
        now=NOW,
    )
    assert event_a.event_id == event_b.event_id
    assert event_a == event_b


def test_event_id_differs_for_different_query_id() -> None:
    gv = make_graph_version()
    plan = make_plan(gv)
    kwargs = dict(
        graph_version=gv,
        query_contract=make_contract(),
        retrieval_plan=plan,
        candidate_count=3,
        mss_size=3,
        llm_calls=1,
        now=NOW,
    )
    event_a = QueryTelemetryEvent.build(query_id="q1", **kwargs)
    event_b = QueryTelemetryEvent.build(query_id="q2", **kwargs)
    assert event_a.event_id != event_b.event_id


def test_event_id_differs_for_different_recorded_at() -> None:
    gv = make_graph_version()
    plan = make_plan(gv)
    kwargs = dict(
        query_id="q1",
        graph_version=gv,
        query_contract=make_contract(),
        retrieval_plan=plan,
        candidate_count=3,
        mss_size=3,
        llm_calls=1,
    )
    event_a = QueryTelemetryEvent.build(now=NOW, **kwargs)
    event_b = QueryTelemetryEvent.build(now=datetime(2026, 9, 1, tzinfo=UTC), **kwargs)
    assert event_a.event_id != event_b.event_id


def test_build_omitting_now_still_produces_a_valid_event() -> None:
    gv = make_graph_version()
    plan = make_plan(gv)
    event = QueryTelemetryEvent.build(
        query_id="q1",
        graph_version=gv,
        query_contract=make_contract(),
        retrieval_plan=plan,
        candidate_count=0,
        mss_size=0,
        llm_calls=0,
    )
    assert event.event_id.startswith("telemetry:")
    assert event.recorded_at.tzinfo is not None


# --- FailureTelemetryEvent.build() -----------------------------------------


def test_failure_event_build_carries_provided_fields() -> None:
    event = FailureTelemetryEvent.build(
        code=FailureCode.PROVIDER_UNAVAILABLE,
        repository_id="repo1",
        detail="scip binary not found",
        now=NOW,
    )
    assert event.code is FailureCode.PROVIDER_UNAVAILABLE
    assert event.repository_id == "repo1"
    assert event.query_id is None
    assert event.graph_version_id is None
    assert event.detail == "scip binary not found"
    assert event.recorded_at == NOW


def test_failure_event_build_is_deterministic() -> None:
    event_a = FailureTelemetryEvent.build(
        code=FailureCode.LLM_SCHEMA_FAILURE, repository_id="repo1", query_id="q1", now=NOW
    )
    event_b = FailureTelemetryEvent.build(
        code=FailureCode.LLM_SCHEMA_FAILURE, repository_id="repo1", query_id="q1", now=NOW
    )
    assert event_a.event_id == event_b.event_id


def test_failure_event_id_differs_by_code() -> None:
    event_a = FailureTelemetryEvent.build(
        code=FailureCode.PROVIDER_TIMEOUT, repository_id="repo1", now=NOW
    )
    event_b = FailureTelemetryEvent.build(
        code=FailureCode.PLAN_BLOCKED, repository_id="repo1", now=NOW
    )
    assert event_a.event_id != event_b.event_id


def test_all_eleven_tad_64_failure_codes_are_representable() -> None:
    """TAD §64's failure taxonomy, verbatim -- no more, no fewer."""
    expected = {
        "PROVIDER_UNAVAILABLE",
        "PROVIDER_TIMEOUT",
        "PARTIAL_PROVIDER_RESULT",
        "ENTITY_UNRESOLVED",
        "GRAPH_VERSION_CONFLICT",
        "INSUFFICIENT_EVIDENCE",
        "PLAN_BLOCKED",
        "PLAN_UNSUPPORTED",
        "LLM_SCHEMA_FAILURE",
        "VERIFICATION_FAILURE",
        "CONCURRENT_UPDATE_DETECTED",
    }
    assert {code.value for code in FailureCode} == expected


def test_feedback_record_uses_tad_60_vocabulary_only() -> None:
    """TAD §60's feedback examples, verbatim -- not HLRD §46's slightly
    different superset (per the approved decision 3 discipline)."""
    expected = {
        "THUMBS_UP",
        "THUMBS_DOWN",
        "CORRECTION",
        "CLICK_THROUGH",
        "FOLLOW_UP_QUERY",
        "EXPLICIT_DISAMBIGUATION",
    }
    assert {kind.value for kind in FeedbackKind} == expected
