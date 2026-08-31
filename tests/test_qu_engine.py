"""Behavioral tests for the Query Understanding Engine end-to-end
(TAD §22-25; directive D8 Phases 6-7, 10-11)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from codex.query_understanding.engine import (
    DETERMINISTIC_THRESHOLD,
    UnderstandingStatus,
    understand_query,
)
from codex.query_understanding.models import Intent
from codex.query_understanding.session import SessionContext
from fake_slm_adapter import FakeSLMAdapter, make_interpretation

NOW = datetime(2026, 8, 31, tzinfo=UTC)


# --- Deterministic exact intent ---------------------------------------------


def test_deterministic_exact_intent_resolves_without_slm() -> None:
    result = understand_query("Who calls authenticate?", repository_id="repo1")
    assert result.status is UnderstandingStatus.RESOLVED
    assert result.contract is not None
    assert result.contract.intent is Intent.FIND_CALLERS
    assert result.contract.confidence > DETERMINISTIC_THRESHOLD


def test_deterministic_result_requires_no_slm_adapter() -> None:
    """A deterministic Tier-0 match must resolve even with slm_adapter=None."""
    result = understand_query("Find callers of authenticate", repository_id="repo1")
    assert result.status is UnderstandingStatus.RESOLVED


def test_deterministic_match_with_a_secondary_candidate_computes_real_ambiguity() -> None:
    """ "Which tests call authenticate?" resolves deterministically to
    FIND_TESTS but also surfaces a low-confidence FIND_CALLERS
    candidate -- the resulting contract's ambiguity must reflect that
    second candidate's presence, not collapse to 0.0 as it would for a
    query with only one candidate at all."""
    result = understand_query("Which tests call authenticate?", repository_id="repo1")
    assert result.status is UnderstandingStatus.RESOLVED
    assert result.contract is not None
    assert result.contract.intent is Intent.FIND_TESTS
    assert result.contract.ambiguity > 0.0


def test_required_evidence_derived_from_intent() -> None:
    from codex.provider.capability import Capability

    result = understand_query("Find callers of authenticate", repository_id="repo1")
    assert result.contract is not None
    assert Capability.CALL_RELATIONSHIP in result.contract.required_evidence
    assert Capability.SYMBOL_REFERENCE in result.contract.required_evidence


def test_required_evidence_derived_from_slm_intent_too() -> None:
    from codex.provider.capability import Capability

    adapter = FakeSLMAdapter(make_interpretation(intent=Intent.HISTORY_ANALYSIS, confidence=0.9))
    result = understand_query(
        "some vague history query", repository_id="repo1", slm_adapter=adapter
    )
    assert result.contract is not None
    assert result.contract.required_evidence == [Capability.HISTORY]


# --- Ambiguous intent / SLM escalation boundary -----------------------------


def test_ambiguous_query_without_slm_adapter_is_slm_unavailable() -> None:
    result = understand_query("Show everything related to authenticate", repository_id="repo1")
    assert result.status is UnderstandingStatus.SLM_UNAVAILABLE
    assert result.contract is None
    assert result.detail is not None


def test_false_positive_keyword_without_slm_is_slm_unavailable_not_high_confidence() -> None:
    result = understand_query("Call the API and verify the response", repository_id="repo1")
    assert result.status is UnderstandingStatus.SLM_UNAVAILABLE


def test_slm_escalation_produces_resolved_contract() -> None:
    adapter = FakeSLMAdapter(make_interpretation(intent=Intent.CODE_LOOKUP, confidence=0.9))
    result = understand_query(
        "Show everything related to authenticate", repository_id="repo1", slm_adapter=adapter
    )
    assert result.status is UnderstandingStatus.RESOLVED
    assert result.contract is not None
    assert result.contract.intent is Intent.CODE_LOOKUP
    assert adapter.calls == ["Show everything related to authenticate"]


def test_session_records_slm_resolved_query_too() -> None:
    session = SessionContext(repository_id="repo1")
    adapter = FakeSLMAdapter(make_interpretation(intent=Intent.CODE_LOOKUP, confidence=0.9))
    understand_query(
        "Show everything related to authenticate",
        repository_id="repo1",
        session=session,
        slm_adapter=adapter,
        now=NOW,
    )
    entries = session.active_entries(now=NOW)
    assert len(entries) == 1
    assert entries[0].intent is Intent.CODE_LOOKUP


def test_slm_not_invoked_for_deterministic_query() -> None:
    """TAD §22: "The SLM is not automatically invoked for every query.\""""
    adapter = FakeSLMAdapter(make_interpretation())
    understand_query("Who calls authenticate?", repository_id="repo1", slm_adapter=adapter)
    assert adapter.calls == []


# --- Confidence validation / calibrated probability requirement -------------


def test_slm_confidence_below_escalate_floor_requires_llm() -> None:
    adapter = FakeSLMAdapter(make_interpretation(confidence=0.3))
    result = understand_query("some vague query", repository_id="repo1", slm_adapter=adapter)
    assert result.status is UnderstandingStatus.LLM_ESCALATION_REQUIRED
    assert result.contract is None


def test_slm_confidence_in_execute_with_clarification_band_still_resolves() -> None:
    adapter = FakeSLMAdapter(make_interpretation(confidence=0.6))
    result = understand_query("some vague query", repository_id="repo1", slm_adapter=adapter)
    assert result.status is UnderstandingStatus.RESOLVED
    assert result.contract is not None
    assert result.contract.confidence == pytest.approx(0.6)


def test_slm_confidence_is_bounded_to_calibrated_probability_range() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        make_interpretation(confidence=1.5)
    with pytest.raises(ValidationError):
        make_interpretation(confidence=-0.5)


# --- Session repository isolation / expiration / sliding window ------------


def test_session_records_only_resolved_queries() -> None:
    session = SessionContext(repository_id="repo1")
    understand_query("Who calls authenticate?", repository_id="repo1", session=session, now=NOW)
    assert len(session.active_entries(now=NOW)) == 1


def test_session_not_recorded_when_slm_unavailable() -> None:
    session = SessionContext(repository_id="repo1")
    result = understand_query(
        "Show everything related to authenticate",
        repository_id="repo1",
        session=session,
        now=NOW,
    )
    assert result.status is UnderstandingStatus.SLM_UNAVAILABLE
    assert session.active_entries(now=NOW) == []


def test_understand_query_requires_repository_id() -> None:
    with pytest.raises(ValueError, match="repository_id"):
        understand_query("Who calls authenticate?", repository_id="")


# --- No graph / provider / LLM access from Query Understanding -------------


def test_no_graph_access_result_never_carries_graph_data() -> None:
    """Structural proof, not just a naming convention: QueryContract has
    no graph_version/graph_store-shaped attribute at all."""
    result = understand_query("Who calls authenticate?", repository_id="repo1")
    assert result.contract is not None
    assert not hasattr(result.contract, "graph_version")
    assert not hasattr(result.contract, "graph_store")


def test_no_llm_answer_generation_llm_escalation_never_carries_an_answer() -> None:
    adapter = FakeSLMAdapter(make_interpretation(confidence=0.1))
    result = understand_query("vague", repository_id="repo1", slm_adapter=adapter)
    assert result.status is UnderstandingStatus.LLM_ESCALATION_REQUIRED
    assert result.contract is None
    assert not hasattr(result, "answer")


# --- Determinism / repeatability --------------------------------------------


def test_deterministic_repeatability_same_input_same_output() -> None:
    first = understand_query("Who calls authenticate?", repository_id="repo1", now=NOW)
    second = understand_query("Who calls authenticate?", repository_id="repo1", now=NOW)
    assert first.contract == second.contract
    assert first.status == second.status


def test_normalization_collapses_whitespace_variants_to_same_result() -> None:
    a = understand_query("Who   calls\tauthenticate?", repository_id="repo1")
    b = understand_query("Who calls authenticate?", repository_id="repo1")
    assert a.contract == b.contract
