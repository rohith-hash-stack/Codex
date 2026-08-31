"""Behavioral tests for Verification Confidence (TAD §48; directive
D10.4, D10 Decision 1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from codex.coverage.engine import CapabilityCoverage
from codex.evidence.model import CanonicalRelationship, Evidence
from codex.llm.schema import Claim, ClaimType
from codex.ontology.relationships import RelationshipType
from codex.verification.confidence import (
    CONTRADICTION_CAP,
    CONTRADICTION_SIGNIFICANT_THRESHOLD,
    CONTRADICTION_WEAK_THRESHOLD,
    V_WEIGHTS,
    ContradictionLevel,
    VerificationFactors,
    classify_contradiction,
    compute_confidence,
    compute_factors,
)
from codex.verification.entailment import entail_claim
from llm_fixtures import make_evidence_package

NOW = datetime(2026, 8, 31, tzinfo=UTC)


def _evidence(evidence_id: str, *, confidence: float = 0.9, provider: str = "fake", freshness=None):
    return Evidence(
        evidence_id=evidence_id,
        provider=provider,
        provider_version="1.0",
        snapshot_id="s1",
        source_revision="rev1",
        subject="A",
        predicate=RelationshipType.CALLS,
        object="B",
        confidence=confidence,
        freshness=freshness or NOW,
    )


def _rel(contradiction_score: float = 0.0, supporting_evidence_ids=("e1",)):
    return CanonicalRelationship(
        subject="A",
        predicate=RelationshipType.CALLS,
        object="B",
        contradiction_score=contradiction_score,
        supporting_evidence_ids=list(supporting_evidence_ids),
    )


# --- contradiction classification (D10 Decision 1) ---------------------------


def test_classify_contradiction_none_at_zero() -> None:
    assert classify_contradiction(0.0) is ContradictionLevel.NONE


def test_classify_contradiction_weak_below_threshold() -> None:
    assert classify_contradiction(0.1) is ContradictionLevel.WEAK
    assert classify_contradiction(CONTRADICTION_WEAK_THRESHOLD - 0.01) is ContradictionLevel.WEAK


def test_classify_contradiction_significant_above_threshold() -> None:
    assert classify_contradiction(0.9) is ContradictionLevel.SIGNIFICANT
    assert (
        classify_contradiction(CONTRADICTION_SIGNIFICANT_THRESHOLD + 0.01)
        is ContradictionLevel.SIGNIFICANT
    )


def test_classify_contradiction_intermediate_band() -> None:
    """ "0.40-0.60: treat as intermediate/uncertain rather than silently
    classifying it as strong or weak" (D10 Decision 1, verbatim)."""
    assert classify_contradiction(0.40) is ContradictionLevel.INTERMEDIATE
    assert classify_contradiction(0.50) is ContradictionLevel.INTERMEDIATE
    assert classify_contradiction(0.60) is ContradictionLevel.INTERMEDIATE


def test_thresholds_match_d10_decision_1_exactly() -> None:
    assert CONTRADICTION_SIGNIFICANT_THRESHOLD == 0.60
    assert CONTRADICTION_WEAK_THRESHOLD == 0.40
    assert CONTRADICTION_CAP == 0.50


# --- V formula weights (TAD §48 exact) ---------------------------------------


def test_v_weights_match_tad_48_exactly() -> None:
    assert V_WEIGHTS == {
        "evidence_support": 0.35,
        "evidence_quality": 0.20,
        "evidence_independence": 0.15,
        "coverage": 0.10,
        "freshness": 0.10,
        "provider_authority": 0.10,
    }
    assert abs(sum(V_WEIGHTS.values()) - 1.0) < 1e-9


def test_compute_confidence_is_weighted_sum() -> None:
    factors = VerificationFactors(
        evidence_support=1.0,
        evidence_quality=0.0,
        evidence_independence=0.0,
        coverage=0.0,
        freshness=0.0,
        provider_authority=0.0,
    )
    assert compute_confidence(factors, ContradictionLevel.NONE) == 0.35


def test_significant_contradiction_caps_v_at_0_50() -> None:
    factors = VerificationFactors(
        evidence_support=1.0,
        evidence_quality=1.0,
        evidence_independence=1.0,
        coverage=1.0,
        freshness=1.0,
        provider_authority=1.0,
    )
    v = compute_confidence(factors, ContradictionLevel.SIGNIFICANT)
    assert v == CONTRADICTION_CAP  # would be 1.0 uncapped


def test_weak_contradiction_does_not_apply_the_hard_cap() -> None:
    factors = VerificationFactors(
        evidence_support=1.0,
        evidence_quality=1.0,
        evidence_independence=1.0,
        coverage=1.0,
        freshness=1.0,
        provider_authority=1.0,
    )
    v = compute_confidence(factors, ContradictionLevel.WEAK)
    assert v == 1.0  # no cap applied -- penalty already reflected upstream in evidence_support


def test_no_contradiction_no_cap() -> None:
    factors = VerificationFactors(
        evidence_support=0.9,
        evidence_quality=0.9,
        evidence_independence=0.9,
        coverage=0.9,
        freshness=0.9,
        provider_authority=0.9,
    )
    assert compute_confidence(factors, ContradictionLevel.NONE) == pytest.approx(0.9)


# --- compute_factors ----------------------------------------------------------


def test_unresolved_entailment_yields_all_zero_factors() -> None:
    package = make_evidence_package(relationships=[], evidence=[])
    claim = Claim(subject="A", predicate="CALLS", object="B", claim_type=ClaimType.FACT)
    entailment = entail_claim(claim, package)
    factors = compute_factors(entailment, package, now=NOW)
    assert factors.evidence_support == 0.0
    assert factors.evidence_quality == 0.0


def test_supported_claim_with_evidence_yields_nonzero_evidence_quality() -> None:
    rel = _rel()
    package = make_evidence_package(relationships=[rel], evidence=[_evidence("e1", confidence=0.8)])
    claim = Claim(subject="A", predicate="CALLS", object="B", claim_type=ClaimType.FACT)
    entailment = entail_claim(claim, package)
    factors = compute_factors(entailment, package, now=NOW)
    assert factors.evidence_quality == 0.8


def test_evidence_support_reduced_by_contradiction_score() -> None:
    rel = _rel(contradiction_score=0.3)
    package = make_evidence_package(relationships=[rel], evidence=[_evidence("e1")])
    claim = Claim(subject="A", predicate="CALLS", object="B", claim_type=ClaimType.FACT)
    entailment = entail_claim(claim, package)
    factors = compute_factors(entailment, package, now=NOW)
    assert abs(factors.evidence_support - 0.7) < 1e-9


def test_evidence_independence_reflects_distinct_groups() -> None:
    e1 = _evidence("e1", provider="scip")
    e2 = _evidence("e2", provider="codeql")
    rel = _rel(supporting_evidence_ids=("e1", "e2"))
    package = make_evidence_package(relationships=[rel], evidence=[e1, e2])
    claim = Claim(subject="A", predicate="CALLS", object="B", claim_type=ClaimType.FACT)
    entailment = entail_claim(claim, package)
    factors = compute_factors(entailment, package, now=NOW)
    assert factors.evidence_independence == 1.0  # two evidence records, two distinct providers


def test_coverage_factor_uses_capability_coverage_mapping() -> None:
    rel = _rel()
    package = make_evidence_package(
        relationships=[rel],
        evidence=[_evidence("e1")],
        coverage={"CALL_RELATIONSHIP": CapabilityCoverage.COMPLETE},
    )
    claim = Claim(subject="A", predicate="CALLS", object="B", claim_type=ClaimType.FACT)
    entailment = entail_claim(claim, package)
    factors = compute_factors(entailment, package, now=NOW)
    assert factors.coverage == 1.0


def test_provider_authority_defaults_to_full_trust() -> None:
    rel = _rel()
    package = make_evidence_package(
        relationships=[rel], evidence=[_evidence("e1", provider="unknown")]
    )
    claim = Claim(subject="A", predicate="CALLS", object="B", claim_type=ClaimType.FACT)
    entailment = entail_claim(claim, package)
    factors = compute_factors(entailment, package, now=NOW)
    assert factors.provider_authority == 1.0


def test_provider_authority_honors_explicit_mapping() -> None:
    rel = _rel()
    package = make_evidence_package(
        relationships=[rel], evidence=[_evidence("e1", provider="low-trust")]
    )
    claim = Claim(subject="A", predicate="CALLS", object="B", claim_type=ClaimType.FACT)
    entailment = entail_claim(claim, package)
    factors = compute_factors(entailment, package, provider_authority={"low-trust": 0.2}, now=NOW)
    assert factors.provider_authority == 0.2


def test_supported_claim_with_unresolvable_evidence_ids_yields_honest_zero_quality() -> None:
    """A matched relationship exists (structural support is real) but
    its `supporting_evidence_ids` don't resolve against `package.
    evidence` -- every evidence-derived factor is honestly 0.0 rather
    than fabricated, while `evidence_support` still reflects the
    relationship's own (here: zero) contradiction_score."""
    rel = _rel(contradiction_score=0.0, supporting_evidence_ids=("missing-id",))
    package = make_evidence_package(relationships=[rel], evidence=[])
    claim = Claim(subject="A", predicate="CALLS", object="B", claim_type=ClaimType.FACT)
    entailment = entail_claim(claim, package)
    factors = compute_factors(entailment, package, now=NOW)
    assert factors.evidence_support == 1.0
    assert factors.evidence_quality == 0.0
    assert factors.coverage == 0.0


def test_path_existence_factors_use_the_matched_paths_relationships() -> None:
    hop1 = CanonicalRelationship(
        subject="A", predicate=RelationshipType.CALLS, object="X", contradiction_score=0.0
    )
    hop2 = CanonicalRelationship(
        subject="X", predicate=RelationshipType.CALLS, object="B", contradiction_score=0.2
    )
    package = make_evidence_package(relationships=[hop1, hop2], evidence=[])
    claim = Claim(subject="A", predicate="REACHES", object="B", claim_type=ClaimType.DERIVED)
    entailment = entail_claim(claim, package)
    factors = compute_factors(entailment, package, now=NOW)
    assert abs(factors.evidence_support - 0.9) < 1e-9  # 1.0 - avg(0.0, 0.2)


def test_freshness_factor_decays_for_old_evidence() -> None:
    stale = _evidence("e1", freshness=NOW - timedelta(days=365))
    fresh = _evidence("e1", freshness=NOW)
    rel = _rel()
    stale_package = make_evidence_package(relationships=[rel], evidence=[stale])
    fresh_package = make_evidence_package(relationships=[rel], evidence=[fresh])
    claim = Claim(subject="A", predicate="CALLS", object="B", claim_type=ClaimType.FACT)
    entailment = entail_claim(claim, stale_package)
    stale_factors = compute_factors(entailment, stale_package, now=NOW)
    fresh_factors = compute_factors(entailment, fresh_package, now=NOW)
    assert stale_factors.freshness < fresh_factors.freshness
