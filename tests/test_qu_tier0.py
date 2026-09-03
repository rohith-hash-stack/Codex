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


# --- GAP-5 fix: "What references X?" / "Who references X?" -----------------


def test_find_references_what_references_x() -> None:
    candidates = detect("What references authenticate?")
    assert candidates[0].intent is Intent.FIND_REFERENCES
    assert candidates[0].score > DETERMINISTIC_THRESHOLD
    assert candidates[0].targets == ("authenticate",)


def test_find_references_who_references_x() -> None:
    candidates = detect("Who references PaymentProcessor?")
    assert candidates[0].intent is Intent.FIND_REFERENCES
    assert candidates[0].score > DETERMINISTIC_THRESHOLD
    assert candidates[0].targets == ("PaymentProcessor",)


def test_find_references_noun_phrase_form() -> None:
    """The pre-existing "references to X" noun-phrase shape, matching
    "implementations of X"'s own established convention."""
    candidates = detect("references to UserModel")
    assert candidates[0].intent is Intent.FIND_REFERENCES
    assert candidates[0].score > DETERMINISTIC_THRESHOLD
    assert candidates[0].targets == ("UserModel",)


def test_find_references_does_not_collapse_with_find_callers() -> None:
    """Adversarial check, matching this file's own "distinct intent"
    discipline: a query naming both "references" and "calls" structure
    must not have FIND_REFERENCES accidentally win over a genuine
    FIND_CALLERS match, or vice versa -- each pattern only fires on its
    own distinct phrase shape."""
    calls_candidates = detect("Who calls authenticate?")
    assert calls_candidates[0].intent is Intent.FIND_CALLERS
    assert all(c.intent is not Intent.FIND_REFERENCES for c in calls_candidates)

    references_candidates = detect("What references authenticate?")
    assert references_candidates[0].intent is Intent.FIND_REFERENCES
    assert all(c.intent is not Intent.FIND_CALLERS for c in references_candidates)


def test_find_references_determinism() -> None:
    a = detect("What references authenticate?")
    b = detect("What references authenticate?")
    assert a == b


# --- GAP-7 fix: "What implements X?" / "Who implements X?" -----------------


def test_find_implementations_what_implements_x() -> None:
    candidates = detect("What implements Storage?")
    assert candidates[0].intent is Intent.FIND_IMPLEMENTATIONS
    assert candidates[0].score > DETERMINISTIC_THRESHOLD
    assert candidates[0].targets == ("Storage",)


def test_find_implementations_who_implements_x() -> None:
    candidates = detect("Who implements Shape?")
    assert candidates[0].intent is Intent.FIND_IMPLEMENTATIONS
    assert candidates[0].score > DETERMINISTIC_THRESHOLD
    assert candidates[0].targets == ("Shape",)


def test_find_implementations_both_phrase_shapes_agree() -> None:
    """The pre-existing noun-phrase form ("implementations of X") and
    the new natural-question form ("what implements X?") must resolve
    to the identical intent/score for the same target -- two Tier-0
    entry points into one, already-validated retrieval path, not two
    different behaviors."""
    noun_phrase = detect("implementations of ClassAB")
    question = detect("What implements ClassAB?")
    assert noun_phrase[0].intent is question[0].intent is Intent.FIND_IMPLEMENTATIONS
    assert noun_phrase[0].score == question[0].score
    assert noun_phrase[0].targets == question[0].targets == ("ClassAB",)


def test_find_implementations_determinism() -> None:
    a = detect("What implements Storage?")
    b = detect("What implements Storage?")
    assert a == b


# ---------------------------------------------------------------------
# Query-Shaped Evidence Retrieval milestone: new TRACE_EXECUTION/
# FIND_IMPACT phrasings (LLM Grounding / Graph Sufficiency Validation's
# multihop/behavioral/impact query shapes, which previously matched no
# Tier-0 pattern at all and so never reached the intent's existing
# required_evidence/relationship_types/depth wiring). Every phrasing
# below is one of the 15 real queries the validation used.
# ---------------------------------------------------------------------
def test_what_happens_when_x_is_invoked_routes_to_trace_execution() -> None:
    candidates = detect("What happens when Signal.send() is invoked?")
    assert candidates[0].intent is Intent.TRACE_EXECUTION
    assert candidates[0].score > DETERMINISTIC_THRESHOLD
    # Bare trailing identifier only -- the "Signal." qualifier is dropped
    # (task #127 measurement: resolve_targets deliberately never
    # normalizes across provider-specific qualified-name decoration, so
    # a dotted "ClassName.method" composite target only resolves by
    # accident for AST-style qualified names and fails entirely for
    # SCIP-style ones; the bare method name resolves against both).
    assert candidates[0].targets == ("send",)


def test_what_happens_when_x_runs_routes_to_trace_execution() -> None:
    candidates = detect("What happens when Flask.full_dispatch_request() runs?")
    assert candidates[0].intent is Intent.TRACE_EXECUTION
    assert candidates[0].score > DETERMINISTIC_THRESHOLD
    assert candidates[0].targets == ("full_dispatch_request",)


def test_what_happens_when_x_is_called_with_trailing_chain_clause() -> None:
    """The trailing "-- trace the call chain" clause must not prevent
    the earlier "what happens when X is called" clause from matching."""
    candidates = detect("What happens when Signal.send() is called -- trace the call chain.")
    assert candidates[0].intent is Intent.TRACE_EXECUTION
    assert candidates[0].score > DETERMINISTIC_THRESHOLD
    assert candidates[0].targets == ("send",)


def test_trace_what_happens_from_x_routes_to_trace_execution() -> None:
    candidates = detect("Trace what happens from FixtureRequest.getfixturevalue() onward.")
    assert candidates[0].intent is Intent.TRACE_EXECUTION
    assert candidates[0].score > DETERMINISTIC_THRESHOLD
    assert candidates[0].targets == ("getfixturevalue",)


def test_call_path_from_x_routes_to_trace_execution() -> None:
    candidates = detect("What is the call path from Flask.wsgi_app() down to dispatch_request()?")
    assert candidates[0].intent is Intent.TRACE_EXECUTION
    assert candidates[0].score > DETERMINISTIC_THRESHOLD
    assert candidates[0].targets == ("wsgi_app",)


def test_if_x_changes_what_could_be_affected_routes_to_find_impact() -> None:
    candidates = detect("If Signal.send changes, what components could be affected?")
    assert candidates[0].intent is Intent.FIND_IMPACT
    assert candidates[0].score > DETERMINISTIC_THRESHOLD
    assert candidates[0].targets == ("send",)


def test_if_x_changes_pattern_does_not_collide_with_find_dependencies() -> None:
    """"If X changes, what could be affected" is a distinct structural
    shape from "what does X depend on" -- must not collapse into
    FIND_DEPENDENCIES or any other pre-existing intent."""
    candidates = detect(
        "If FixtureRequest._get_active_fixturedef changes, what components could be affected?"
    )
    assert candidates[0].intent is Intent.FIND_IMPACT
    assert all(
        c.intent is not Intent.FIND_DEPENDENCIES
        for c in candidates
        if c.score >= DETERMINISTIC_THRESHOLD
    )


def test_new_trace_execution_phrasings_do_not_collide_with_find_callers() -> None:
    """"What happens when X is called" must never be claimed by the
    bare-keyword FIND_CALLERS rule or any "what calls X" structural
    rule -- "called" is a different word from "calls", and the phrase
    shape is entirely different."""
    candidates = detect("What happens when Session.send() is invoked?")
    assert candidates[0].intent is Intent.TRACE_EXECUTION
    assert all(
        c.intent is not Intent.FIND_CALLERS or c.score < DETERMINISTIC_THRESHOLD for c in candidates
    )


def test_new_trace_execution_and_find_impact_patterns_are_deterministic() -> None:
    a = detect("What happens when Signal.send() is invoked?")
    b = detect("What happens when Signal.send() is invoked?")
    assert a == b
    c = detect("If Session.send changes, what components could be affected?")
    d = detect("If Session.send changes, what components could be affected?")
    assert c == d
