"""Behavioral tests for the Re-synthesis Controller (TAD §44, §49;
directive D10.7, D10 Decision 2 -- one shared budget, maximum 1)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from codex.evidence.model import CanonicalRelationship, Evidence
from codex.llm.schema import Claim, ClaimType, StructuredAnswer
from codex.ontology.relationships import RelationshipType
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile
from codex.verification.resynthesis import (
    MAX_ATTEMPTS,
    ResynthesisOutcome,
    run_verification_loop,
)
from fake_llm_gateway import FakeLLMGateway, malformed_result, ok_result
from fake_provider_adapter import FakeProviderAdapter
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


# --- registry-derived provider_authority (D2 gap-hardening pass, TAD §48) ---


def _run(package, *, provider_authority=None, registry=None):
    answer = StructuredAnswer(explanation="A calls B.", claims=[_claim()])
    gateway = FakeLLMGateway([ok_result(answer)])
    return run_verification_loop(
        gateway,
        _request(package),
        package,
        provider_authority=provider_authority,
        registry=registry,
        now=NOW,
    )


def test_no_provider_authority_and_no_registry_preserves_pre_hardening_default() -> None:
    """The pre-hardening default -- every provider trusted uniformly
    (1.0) -- must be unchanged for a caller that supplies neither
    argument, since every pre-existing test in this file (and every
    D9/D10 integration test) relies on exactly that behavior."""
    package = make_evidence_package(relationships=[_rel()], evidence=[_evidence()])
    result = _run(package)
    assert result.retained[0].factors.provider_authority == pytest.approx(1.0)


def test_registry_derived_provider_authority_changes_confidence() -> None:
    """`_evidence()`'s provider is `"fake"` -- a registry with a
    `ProviderScoreProfile.evidence_quality=0.2` for that exact provider
    name must lower the computed `provider_authority` factor (and
    therefore `V`) relative to the no-registry default, proving the
    registry's already-canonical D2 metadata actually reaches TAD
    §48's Verification Confidence formula."""
    package = make_evidence_package(relationships=[_rel()], evidence=[_evidence()])
    baseline = _run(package)

    registry = CapabilityRegistry()
    profile = ProviderScoreProfile(evidence_quality=0.2, cost_factor=0.5)
    registry.register(FakeProviderAdapter(name="fake"), profile)
    wired = _run(package, registry=registry)

    assert wired.retained[0].factors.provider_authority == pytest.approx(0.2)
    assert wired.retained[0].confidence < baseline.retained[0].confidence


def test_explicit_provider_authority_overrides_registry_derived_mapping() -> None:
    """An explicitly-supplied `provider_authority` mapping always wins
    over `registry` (preserves every pre-existing caller's exact
    behavior when both happen to be supplied)."""
    package = make_evidence_package(relationships=[_rel()], evidence=[_evidence()])
    baseline = _run(package)

    registry = CapabilityRegistry()
    profile = ProviderScoreProfile(evidence_quality=0.1, cost_factor=0.5)
    registry.register(FakeProviderAdapter(name="fake"), profile)
    overridden = _run(package, provider_authority={"fake": 1.0}, registry=registry)

    assert overridden.retained[0].confidence == pytest.approx(baseline.retained[0].confidence)


def test_registry_with_no_profile_for_the_evidence_provider_falls_back_to_full_trust() -> None:
    """A registry that simply never registered `"fake"` (or registered
    it with no profile) must not crash or silently zero out
    provider_authority -- it falls back to the historical default
    (1.0), identical to omitting `registry` entirely."""
    package = make_evidence_package(relationships=[_rel()], evidence=[_evidence()])
    baseline = _run(package)

    empty_registry = CapabilityRegistry()
    result = _run(package, registry=empty_registry)

    assert result.retained[0].confidence == pytest.approx(baseline.retained[0].confidence)
