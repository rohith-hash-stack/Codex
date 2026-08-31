"""Behavioral tests for the Final Answer / Abstention Policy (HLRD §43,
TAD §50; directive D10.8)."""

from __future__ import annotations

from datetime import UTC, datetime

from codex.coverage.engine import NegativeQueryCoverage
from codex.evidence.model import CanonicalRelationship, Evidence
from codex.llm.schema import Claim, ClaimType, StructuredAnswer
from codex.ontology.relationships import RelationshipType
from codex.verification.answer import AnswerDecision, build_final_answer
from codex.verification.engine import verify_claim
from codex.verification.resynthesis import ResynthesisOutcome, ResynthesisResult
from codex.verification.state import VerificationStatus
from llm_fixtures import make_evidence_package

NOW = datetime(2026, 8, 31, tzinfo=UTC)


def _evidence(evidence_id: str = "e1", *, confidence: float = 0.95) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        provider="fake",
        provider_version="1.0",
        snapshot_id="s1",
        source_revision="rev1",
        subject="A",
        predicate=RelationshipType.CALLS,
        object="B",
        confidence=confidence,
        freshness=NOW,
    )


def _rel(*, contradiction_score: float = 0.0) -> CanonicalRelationship:
    return CanonicalRelationship(
        subject="A",
        predicate=RelationshipType.CALLS,
        object="B",
        contradiction_score=contradiction_score,
        supporting_evidence_ids=["e1"],
    )


def _claim() -> Claim:
    return Claim(subject="A", predicate="CALLS", object="B", claim_type=ClaimType.FACT)


def _resolved_result(
    retained, removed, *, answer: StructuredAnswer | None = None
) -> ResynthesisResult:
    return ResynthesisResult(
        outcome=ResynthesisOutcome.RESOLVED,
        final_answer=answer or StructuredAnswer(explanation="A calls B.", claims=[]),
        retained=retained,
        removed=removed,
        attempts=1,
    )


def test_generation_failed_always_abstains() -> None:
    result = ResynthesisResult(
        outcome=ResynthesisOutcome.GENERATION_FAILED,
        final_answer=None,
        attempts=2,
        failure_reason="generation_status=MALFORMED_OUTPUT",
    )
    answer = build_final_answer(result)
    assert answer.decision is AnswerDecision.ABSTAIN
    assert answer.verification_status is VerificationStatus.REJECTED
    assert answer.supported_claims == []


def test_strong_evidence_verified_claim_is_strong_answer() -> None:
    package = make_evidence_package(relationships=[_rel()], evidence=[_evidence(confidence=0.95)])
    verification = verify_claim(_claim(), package, now=NOW)
    result = _resolved_result([verification], [])
    answer = build_final_answer(result)
    assert answer.decision is AnswerDecision.STRONG_ANSWER
    assert answer.verification_status is VerificationStatus.VERIFIED
    assert answer.supported_claims == [verification.claim]
    assert answer.limitations == []


def test_no_claims_no_removals_abstains_never_asserts_unsupported_fact() -> None:
    """Core rule: NO EVIDENCE -> NO REPOSITORY FACTUAL ASSERTION."""
    result = _resolved_result([], [])
    answer = build_final_answer(result)
    assert answer.decision is AnswerDecision.ABSTAIN
    assert answer.supported_claims == []


def test_verified_plus_a_removed_contradiction_is_qualified() -> None:
    package = make_evidence_package(relationships=[_rel()], evidence=[_evidence()])
    verified = verify_claim(_claim(), package, now=NOW)
    contradicted_package = make_evidence_package(
        relationships=[_rel(contradiction_score=0.9)], evidence=[_evidence()]
    )
    removed_verification = verify_claim(_claim(), contradicted_package, now=NOW)
    result = _resolved_result([verified], [removed_verification])
    answer = build_final_answer(result)
    assert answer.decision is AnswerDecision.QUALIFIED_ANSWER
    assert answer.verification_status is VerificationStatus.QUALIFIED
    assert any("removed" in limitation for limitation in answer.limitations)
    assert answer.supported_claims == [verified.claim]  # the removed claim is never asserted


def test_all_removed_nothing_retained_is_qualified_explains_conflict() -> None:
    contradicted_package = make_evidence_package(
        relationships=[_rel(contradiction_score=0.9)], evidence=[_evidence()]
    )
    removed_verification = verify_claim(_claim(), contradicted_package, now=NOW)
    result = _resolved_result([], [removed_verification])
    answer = build_final_answer(result)
    assert answer.decision is AnswerDecision.QUALIFIED_ANSWER
    assert answer.supported_claims == []  # nothing asserted, only explained


def test_mixed_verified_and_inconclusive_is_qualified() -> None:
    package = make_evidence_package(relationships=[_rel()], evidence=[_evidence()])
    verified = verify_claim(_claim(), package, now=NOW)
    unresolved_package = make_evidence_package(relationships=[], evidence=[])
    inconclusive = verify_claim(_claim(), unresolved_package, now=NOW)
    result = _resolved_result([verified, inconclusive], [])
    answer = build_final_answer(result)
    assert answer.decision is AnswerDecision.QUALIFIED_ANSWER
    assert answer.verification_status is VerificationStatus.PARTIALLY_VERIFIED
    assert answer.supported_claims == [verified.claim]


# --- negative-query safety (directive D10.8 item 3) --------------------------


def test_negative_query_no_evidence_found_asserts_absence() -> None:
    result = _resolved_result([], [])
    answer = build_final_answer(
        result, negative_query_result=NegativeQueryCoverage.NO_EVIDENCE_FOUND
    )
    assert answer.decision is AnswerDecision.STRONG_ANSWER
    assert "No matching relationship" in answer.text
    assert answer.supported_claims == []


def test_negative_query_inconclusive_never_asserts_absence() -> None:
    """ "Otherwise: INCONCLUSIVE / insufficient evidence" -- coverage
    that isn't proven complete must never become a false "nothing was
    found" claim."""
    result = _resolved_result([], [])
    answer = build_final_answer(result, negative_query_result=NegativeQueryCoverage.INCONCLUSIVE)
    assert answer.decision is AnswerDecision.ABSTAIN
    assert answer.verification_status is VerificationStatus.INCONCLUSIVE
    assert "insufficient" in answer.text.lower()


def test_negative_query_result_ignored_when_a_verified_claim_exists() -> None:
    """A negative_query_result only governs the no-evidence case -- once
    a real verified claim exists, the answer speaks for itself."""
    package = make_evidence_package(relationships=[_rel()], evidence=[_evidence()])
    verified = verify_claim(_claim(), package, now=NOW)
    result = _resolved_result([verified], [])
    answer = build_final_answer(
        result, negative_query_result=NegativeQueryCoverage.NO_EVIDENCE_FOUND
    )
    assert answer.decision is AnswerDecision.STRONG_ANSWER
    assert answer.supported_claims == [verified.claim]
