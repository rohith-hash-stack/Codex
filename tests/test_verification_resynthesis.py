"""Behavioral tests for the Re-synthesis Controller (TAD §44, §49;
directive D10.7, D10 Decision 2 -- one shared budget, maximum 1)."""

from __future__ import annotations

from datetime import UTC, datetime

from codex.evidence.model import CanonicalRelationship, Evidence
from codex.llm.schema import Claim, ClaimType, StructuredAnswer
from codex.ontology.relationships import RelationshipType
from codex.verification.resynthesis import (
    MAX_ATTEMPTS,
    ResynthesisOutcome,
    run_verification_loop,
)
from fake_llm_gateway import FakeLLMGateway, malformed_result, ok_result
from llm_fixtures import make_evidence_package

NOW = datetime(2026, 8, 31, tzinfo=UTC)


def _evidence(evidence_id: str = "e1") -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        provider="fake",
        provider_version="1.0",
        snapshot_id="s1",
        source_revision="rev1",
        subject="A",
        predicate=RelationshipType.CALLS,
        object="B",
        confidence=0.9,
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


def _request(package):
    from codex.llm.gateway import LLMRequest

    return LLMRequest(
        query_text="Who calls B?",
        evidence_package=package,
        response_schema=StructuredAnswer.model_json_schema(),
        token_budget=4000,
        latency_budget_ms=5000,
    )


def test_first_attempt_ok_no_contradiction_resolves_without_resynthesis() -> None:
    package = make_evidence_package(relationships=[_rel()], evidence=[_evidence()])
    answer = StructuredAnswer(explanation="A calls B.", claims=[_claim()])
    gateway = FakeLLMGateway([ok_result(answer)])
    result = run_verification_loop(gateway, _request(package), package, now=NOW)
    assert result.outcome is ResynthesisOutcome.RESOLVED
    assert result.attempts == 1
    assert result.resynthesis_used is False
    assert len(result.retained) == 1
    assert result.removed == []


def test_malformed_then_valid_uses_the_shared_budget_once() -> None:
    package = make_evidence_package(relationships=[_rel()], evidence=[_evidence()])
    answer = StructuredAnswer(explanation="A calls B.", claims=[_claim()])
    gateway = FakeLLMGateway([malformed_result(), ok_result(answer)])
    result = run_verification_loop(gateway, _request(package), package, now=NOW)
    assert result.outcome is ResynthesisOutcome.RESOLVED
    assert result.attempts == 2
    assert result.resynthesis_used is True
    assert gateway.requests[1].feedback is not None  # correction instruction sent


def test_directive_example_1_malformed_then_contradicted_no_second_resynthesis() -> None:
    """ "Initial generation -> invalid schema -> re-synthesis #1 -> valid
    but contradicted claim -> NO second re-synthesis -> final answer
    must follow verification/abstention policy." (D10 Decision 2)"""
    package = make_evidence_package(
        relationships=[_rel(contradiction_score=0.9)], evidence=[_evidence()]
    )
    contradicted_answer = StructuredAnswer(explanation="A calls B.", claims=[_claim()])
    gateway = FakeLLMGateway([malformed_result(), ok_result(contradicted_answer)])
    result = run_verification_loop(gateway, _request(package), package, now=NOW)
    assert result.attempts == 2  # never a third call
    assert result.outcome is ResynthesisOutcome.RESOLVED
    assert result.retained == []  # the contradicted claim was removed
    assert len(result.removed) == 1


def test_directive_example_2_two_contradictions_no_further_generation() -> None:
    """ "Initial generation -> valid answer -> contradiction ->
    re-synthesis #1 -> second contradiction -> no further LLM
    generation." (D10 Decision 2)"""
    package = make_evidence_package(
        relationships=[_rel(contradiction_score=0.9)], evidence=[_evidence()]
    )
    answer = StructuredAnswer(explanation="A calls B.", claims=[_claim()])
    gateway = FakeLLMGateway([ok_result(answer), ok_result(answer)])
    result = run_verification_loop(gateway, _request(package), package, now=NOW)
    assert result.attempts == 2  # exactly two calls, never three
    assert len(gateway.requests) == 2
    assert result.retained == []
    assert len(result.removed) == 2  # both attempts' contradicted claim recorded


def test_two_consecutive_malformed_outputs_exhausts_budget() -> None:
    gateway = FakeLLMGateway([malformed_result(), malformed_result()])
    package = make_evidence_package(relationships=[], evidence=[])
    result = run_verification_loop(gateway, _request(package), package, now=NOW)
    assert result.outcome is ResynthesisOutcome.GENERATION_FAILED
    assert result.attempts == 2
    assert result.final_answer is None
    assert result.failure_reason is not None


def test_attempts_never_exceeds_max_attempts_constant() -> None:
    assert MAX_ATTEMPTS == 2
    package = make_evidence_package(relationships=[], evidence=[])
    gateway = FakeLLMGateway([malformed_result(), malformed_result(), malformed_result()])
    result = run_verification_loop(gateway, _request(package), package, now=NOW)
    assert result.attempts <= MAX_ATTEMPTS
    assert len(gateway.requests) <= MAX_ATTEMPTS


def test_feedback_on_malformed_retry_describes_the_schema() -> None:
    package = make_evidence_package(relationships=[], evidence=[])
    answer = StructuredAnswer(explanation="ok", claims=[])
    gateway = FakeLLMGateway([malformed_result(), ok_result(answer)])
    run_verification_loop(gateway, _request(package), package, now=NOW)
    assert gateway.requests[0].feedback is None
    assert "schema" in (gateway.requests[1].feedback or "").lower()
