"""Behavioral tests for deterministic Tier-0 intent detection (TAD §23;
directive D8 Phases 6, 11). The adversarial examples are the directive's
own -- proving distinct intents do not collapse merely because of an
overlapping word, and that a bare ambiguous keyword never reaches the
deterministic-execution band alone.
"""

from __future__ import annotations

from codex.query_understanding.engine import DETERMINISTIC_THRESHOLD, SLM_DISAMBIGUATION_FLOOR
from codex.query_understanding.models import Intent
from codex.query_understanding.tier0 import detect


def test_exact_structural_pattern_who_calls() -> None:
    candidates = detect("Who calls authenticate?")
    assert candidates[0].intent is Intent.FIND_CALLERS
    assert candidates[0].score > DETERMINISTIC_THRESHOLD
    assert candidates[0].targets == ("authenticate",)


def test_exact_structural_pattern_find_callers_of() -> None:
    candidates = detect("Find callers of authenticate")
    assert candidates[0].intent is Intent.FIND_CALLERS
    assert candidates[0].score > DETERMINISTIC_THRESHOLD


def test_distinct_intent_which_tests_call_does_not_collapse_to_find_callers() -> None:
    """The directive's own adversarial example: "Which tests call
    authenticate?" must NOT become FIND_CALLERS."""
    candidates = detect("Which tests call authenticate?")
    assert candidates[0].intent is Intent.FIND_TESTS
    assert candidates[0].score > DETERMINISTIC_THRESHOLD
    # FIND_CALLERS may still appear as a low-confidence secondary candidate
    # (the ambiguous keyword "call" is genuinely present) but must never win.
    assert all(
        c.score <= candidates[0].score for c in candidates if c.intent is Intent.FIND_CALLERS
    )


def test_bare_ambiguous_keyword_never_reaches_deterministic_band() -> None:
    """"Call the API and verify the response" -- an imperative sentence,
    not a question about the repository. Must not produce a
    high-confidence FIND_CALLERS match merely because it contains "Call"."""
    candidates = detect("Call the API and verify the response")
    assert all(c.score < SLM_DISAMBIGUATION_FLOOR for c in candidates)


def test_causal_why_question_lands_in_slm_disambiguation_band() -> None:
    """"Why does authenticate call database?" has extractable structure
    (a subject, object, and CALLS-shaped relation) but genuine semantic
    ambiguity about what "why" wants -- must land in the SLM
    disambiguation band, not the deterministic one."""
    candidates = detect("Why does authenticate call database?")
    assert candidates[0].intent is Intent.TRACE_EXECUTION
    assert SLM_DISAMBIGUATION_FLOOR <= candidates[0].score <= DETERMINISTIC_THRESHOLD
    assert candidates[0].targets == ("authenticate", "database")


def test_vague_broad_query_scores_low() -> None:
    """"Show everything related to authenticate" is too broad to be a
    strong deterministic match."""
    candidates = detect("Show everything related to authenticate")
    assert candidates[0].score < SLM_DISAMBIGUATION_FLOOR


def test_no_meaningful_match_returns_empty() -> None:
    candidates = detect("The weather today is quite pleasant.")
    assert candidates == []


def test_find_implementations_structural_pattern() -> None:
    candidates = detect("Find implementations of Shape")
    assert candidates[0].intent is Intent.FIND_IMPLEMENTATIONS
    assert candidates[0].score > DETERMINISTIC_THRESHOLD


def test_find_dependencies_structural_pattern() -> None:
    candidates = detect("What does OrderService depend on")
    assert candidates[0].intent is Intent.FIND_DEPENDENCIES
    assert candidates[0].score > DETERMINISTIC_THRESHOLD


def test_history_analysis_structural_pattern() -> None:
    candidates = detect("history of PaymentProcessor")
    assert candidates[0].intent is Intent.HISTORY_ANALYSIS


def test_impact_analysis_structural_pattern() -> None:
    candidates = detect("impact of changing UserModel")
    assert candidates[0].intent is Intent.FIND_IMPACT
    assert candidates[0].targets == ("UserModel",)


def test_candidates_sorted_highest_score_first() -> None:
    candidates = detect("Which tests call authenticate?")
    scores = [c.score for c in candidates]
    assert scores == sorted(scores, reverse=True)


def test_determinism_same_query_same_result() -> None:
    a = detect("Who calls authenticate?")
    b = detect("Who calls authenticate?")
    assert a == b


def test_short_target_treated_as_noise() -> None:
    """A capture shorter than the minimum target length is dropped, not
    fabricated into a misleading single-character "target"."""
    candidates = detect("Who calls a?")
    assert candidates[0].intent is Intent.FIND_CALLERS
    assert candidates[0].targets == ()
