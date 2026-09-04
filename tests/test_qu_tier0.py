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
    # Full dotted composite, qualifier included (High-Fan-Out Identity-
    # Aware Seed Resolution milestone: reverted from task #127's
    # bare-trailing-identifier-only capture -- the "Signal." qualifier is
    # real disambiguating evidence `_resolve_one_target`'s new
    # `_qualifier_confirmed` narrowing now uses, provider-agnostically).
    assert candidates[0].targets == ("Signal.send",)


def test_what_happens_when_x_runs_routes_to_trace_execution() -> None:
    candidates = detect("What happens when Flask.full_dispatch_request() runs?")
    assert candidates[0].intent is Intent.TRACE_EXECUTION
    assert candidates[0].score > DETERMINISTIC_THRESHOLD
    assert candidates[0].targets == ("Flask.full_dispatch_request",)


def test_what_happens_when_x_is_called_with_trailing_chain_clause() -> None:
    """The trailing "-- trace the call chain" clause must not prevent
    the earlier "what happens when X is called" clause from matching."""
    candidates = detect("What happens when Signal.send() is called -- trace the call chain.")
    assert candidates[0].intent is Intent.TRACE_EXECUTION
    assert candidates[0].score > DETERMINISTIC_THRESHOLD
    assert candidates[0].targets == ("Signal.send",)


def test_trace_what_happens_from_x_routes_to_trace_execution() -> None:
    candidates = detect("Trace what happens from FixtureRequest.getfixturevalue() onward.")
    assert candidates[0].intent is Intent.TRACE_EXECUTION
    assert candidates[0].score > DETERMINISTIC_THRESHOLD
    assert candidates[0].targets == ("FixtureRequest.getfixturevalue",)


def test_call_path_from_x_routes_to_trace_execution() -> None:
    candidates = detect("What is the call path from Flask.wsgi_app() down to dispatch_request()?")
    assert candidates[0].intent is Intent.TRACE_EXECUTION
    assert candidates[0].score > DETERMINISTIC_THRESHOLD
    assert candidates[0].targets == ("Flask.wsgi_app",)


def test_if_x_changes_what_could_be_affected_routes_to_find_impact() -> None:
    candidates = detect("If Signal.send changes, what components could be affected?")
    assert candidates[0].intent is Intent.FIND_IMPACT
    assert candidates[0].score > DETERMINISTIC_THRESHOLD
    assert candidates[0].targets == ("Signal.send",)


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


# -- Codex validation continuation: Query Understanding / Intent Coverage ---
# audit (real, reproduced NL-phrasing gaps -- see the fix's own PROGRESS.md
# entry for the exact reproduction battery this was found with).


def test_what_are_the_callers_of_x_routes_to_find_callers() -> None:
    candidates = detect("What are the callers of process_payment?")
    assert candidates[0].intent is Intent.FIND_CALLERS
    assert candidates[0].score > DETERMINISTIC_THRESHOLD
    assert candidates[0].targets == ("process_payment",)


def test_bare_callers_of_x_form_does_not_double_match_find_or_list_callers_of() -> None:
    """The new `what/who ... callers of X` rule must not also fire (as a
    second, redundant same-intent candidate) on the pre-existing `find
    callers of X`/`list callers of X` phrasings -- a duplicate same-intent
    candidate would inflate `_ambiguity_from_candidates`
    (`codex.query_understanding.engine`) with no genuine ambiguity."""
    for text in ("Find callers of foo", "Find the callers of foo", "List callers of foo"):
        candidates = detect(text)
        find_callers = [c for c in candidates if c.intent is Intent.FIND_CALLERS]
        assert len(find_callers) == 1, f"{text!r} produced {len(find_callers)} FIND_CALLERS"


def test_what_tests_exist_for_x_routes_to_find_tests() -> None:
    candidates = detect("What tests exist for parse_query?")
    assert candidates[0].intent is Intent.FIND_TESTS
    assert candidates[0].score > DETERMINISTIC_THRESHOLD
    assert candidates[0].targets == ("parse_query",)


def test_if_i_change_x_what_breaks_routes_to_find_impact() -> None:
    candidates = detect("If I change validate(), what breaks?")
    assert candidates[0].intent is Intent.FIND_IMPACT
    assert candidates[0].score > DETERMINISTIC_THRESHOLD
    assert candidates[0].targets == ("validate",)


def test_if_i_change_x_what_will_break_variant() -> None:
    candidates = detect("If I change User.save, what will break?")
    assert candidates[0].intent is Intent.FIND_IMPACT
    assert candidates[0].targets == ("User.save",)


def test_what_depends_on_x_routes_to_find_dependencies() -> None:
    """The reverse-direction phrasing the forward-only `what does X
    depend on` rule never covered -- confirmed safe to add because
    `codex.planner.retrieval.bounded_traversal` already collects
    `DEPENDS_ON` edges touching a seed in both directions regardless of
    which one asked (real, reproduced before this rule was added)."""
    candidates = detect("What depends on requests?")
    assert candidates[0].intent is Intent.FIND_DEPENDENCIES
    assert candidates[0].score > DETERMINISTIC_THRESHOLD
    assert candidates[0].targets == ("requests",)


def test_who_depends_on_x_variant() -> None:
    candidates = detect("Who depends on click?")
    assert candidates[0].intent is Intent.FIND_DEPENDENCIES
    assert candidates[0].targets == ("click",)


def test_dependencies_of_x_noun_phrase_routes_to_find_dependencies() -> None:
    candidates = detect("What are the dependencies of this package?")
    assert candidates[0].intent is Intent.FIND_DEPENDENCIES
    # "this package" isn't a real identifier -- honestly captured as-is.
    assert candidates[0].targets == ("this",)


def test_dependents_of_x_noun_phrase_routes_to_find_dependencies() -> None:
    candidates = detect("What are the dependents of core_utils?")
    assert candidates[0].intent is Intent.FIND_DEPENDENCIES
    assert candidates[0].targets == ("core_utils",)


def test_reverse_dependency_phrasing_does_not_double_match_forward_rule() -> None:
    """`what depends on X`/`dependencies of X` must not also collide with
    the pre-existing `what does X depend on` rule for either phrasing --
    both directions map to the same intent, but each query text should
    produce exactly one FIND_DEPENDENCIES candidate, not two."""
    texts = (
        "What does requests depend on?",
        "What depends on requests?",
        "Dependencies of requests",
    )
    for text in texts:
        candidates = detect(text)
        find_deps = [c for c in candidates if c.intent is Intent.FIND_DEPENDENCIES]
        assert len(find_deps) == 1, f"{text!r} produced {len(find_deps)} FIND_DEPENDENCIES"


def test_new_query_understanding_continuation_patterns_are_deterministic() -> None:
    for text in (
        "What are the callers of foo?",
        "What tests exist for foo?",
        "If I change foo, what breaks?",
        "What depends on foo?",
        "Dependencies of foo",
    ):
        assert detect(text) == detect(text)
