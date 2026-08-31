"""Behavioral tests for Verification State Mapping (TAD §50; directive
D10.6)."""

from __future__ import annotations

from datetime import UTC, datetime

from codex.coverage.engine import CapabilityCoverage
from codex.evidence.model import CanonicalRelationship, Evidence
from codex.llm.schema import Claim, ClaimType
from codex.ontology.relationships import RelationshipType
from codex.verification.engine import verify_claim
from codex.verification.state import (
    VERIFIED_CONFIDENCE_THRESHOLD,
    VerificationStatus,
    classify_answer,
    classify_claim,
    to_hlrd_label,
    to_routing_bucket,
)
from llm_fixtures import make_evidence_package

NOW = datetime(2026, 8, 31, tzinfo=UTC)


def _evidence(evidence_id: str = "e1", *, confidence: float = 0.95, freshness=None) -> Evidence:
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
        freshness=freshness or NOW,
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


def _strong_verification():
    package = make_evidence_package(
        relationships=[_rel()],
        evidence=[_evidence(confidence=0.95)],
        coverage={"CALL_RELATIONSHIP": CapabilityCoverage.COMPLETE},
    )
    return verify_claim(_claim(), package, now=NOW)


# --- per-claim classification -------------------------------------------------


def test_unresolved_entailment_is_inconclusive() -> None:
    package = make_evidence_package(relationships=[], evidence=[])
    verification = verify_claim(_claim(), package, now=NOW)
    assert classify_claim(verification) is VerificationStatus.INCONCLUSIVE


def test_significant_contradiction_is_disputed() -> None:
    package = make_evidence_package(
        relationships=[_rel(contradiction_score=0.9)], evidence=[_evidence()]
    )
    verification = verify_claim(_claim(), package, now=NOW)
    assert classify_claim(verification) is VerificationStatus.DISPUTED


def test_strong_support_above_threshold_is_verified() -> None:
    verification = _strong_verification()
    assert verification.confidence >= VERIFIED_CONFIDENCE_THRESHOLD
    assert classify_claim(verification) is VerificationStatus.VERIFIED


def test_supported_but_weak_confidence_is_inconclusive_not_verified() -> None:
    """Structural support alone isn't "sufficient trusted evidence"
    (TAD §50) when quality/coverage/freshness are all weak too -- here:
    weak (not significant) contradiction, near-zero quality, stale
    evidence, and no coverage recorded."""
    from datetime import timedelta

    stale = _evidence(confidence=0.01, freshness=NOW - timedelta(days=3650))
    package = make_evidence_package(relationships=[_rel(contradiction_score=0.3)], evidence=[stale])
    verification = verify_claim(_claim(), package, now=NOW)
    assert verification.confidence < VERIFIED_CONFIDENCE_THRESHOLD
    assert classify_claim(verification) is VerificationStatus.INCONCLUSIVE


# --- answer-level aggregate ----------------------------------------------------


def test_all_verified_no_removals_is_verified() -> None:
    status = classify_answer(
        [VerificationStatus.VERIFIED, VerificationStatus.VERIFIED],
        any_removed_for_contradiction=False,
    )
    assert status is VerificationStatus.VERIFIED


def test_all_verified_with_a_removal_is_qualified() -> None:
    status = classify_answer([VerificationStatus.VERIFIED], any_removed_for_contradiction=True)
    assert status is VerificationStatus.QUALIFIED


def test_mixed_verified_and_inconclusive_is_partially_verified() -> None:
    status = classify_answer(
        [VerificationStatus.VERIFIED, VerificationStatus.INCONCLUSIVE],
        any_removed_for_contradiction=False,
    )
    assert status is VerificationStatus.PARTIALLY_VERIFIED


def test_all_inconclusive_is_inconclusive() -> None:
    status = classify_answer([VerificationStatus.INCONCLUSIVE], any_removed_for_contradiction=False)
    assert status is VerificationStatus.INCONCLUSIVE


def test_no_retained_claims_but_something_removed_is_disputed() -> None:
    status = classify_answer([], any_removed_for_contradiction=True)
    assert status is VerificationStatus.DISPUTED


def test_no_retained_claims_and_nothing_removed_is_inconclusive() -> None:
    status = classify_answer([], any_removed_for_contradiction=False)
    assert status is VerificationStatus.INCONCLUSIVE


# --- mapping functions (TAD §50's own tables, verbatim) ------------------------


def test_hlrd_label_mapping_matches_tad_50_table() -> None:
    assert to_hlrd_label(VerificationStatus.VERIFIED) == "FULLY_VERIFIED"
    assert to_hlrd_label(VerificationStatus.PARTIALLY_VERIFIED) == "PARTIALLY_VERIFIED"
    assert to_hlrd_label(VerificationStatus.QUALIFIED) == "PARTIALLY_VERIFIED"
    assert to_hlrd_label(VerificationStatus.DISPUTED) == "CONTRADICTED"
    assert to_hlrd_label(VerificationStatus.INCONCLUSIVE) == "UNVERIFIED"
    assert to_hlrd_label(VerificationStatus.REJECTED) == "CONTRADICTED"


def test_routing_bucket_mapping_matches_tad_50_table() -> None:
    assert to_routing_bucket(VerificationStatus.VERIFIED) == "VERIFIED"
    assert to_routing_bucket(VerificationStatus.PARTIALLY_VERIFIED) == "QUALIFIED"
    assert to_routing_bucket(VerificationStatus.QUALIFIED) == "QUALIFIED"
    assert to_routing_bucket(VerificationStatus.DISPUTED) == "QUALIFIED"
    assert to_routing_bucket(VerificationStatus.INCONCLUSIVE) == "QUALIFIED"
    assert to_routing_bucket(VerificationStatus.REJECTED) == "ABSTAIN"


def test_every_verification_status_has_both_mappings_defined() -> None:
    for status in VerificationStatus:
        assert to_hlrd_label(status) in {
            "FULLY_VERIFIED",
            "PARTIALLY_VERIFIED",
            "UNVERIFIED",
            "CONTRADICTED",
        }
        assert to_routing_bucket(status) in {"VERIFIED", "QUALIFIED", "ABSTAIN"}


def test_verification_status_is_not_evidence_status() -> None:
    """Structural proof of the naming-collision discipline
    (docs/architecture-conformance-audit.md §T.4 item 2): the two
    DISPUTED members are distinct enum *instances* from distinct enum
    *types* -- `is` correctly distinguishes them. (`==` does **not**:
    both are `StrEnum` members with the same underlying value
    "DISPUTED", so `VerificationStatus.DISPUTED ==
    EvidenceStatus.DISPUTED` is actually `True` via plain string
    equality -- exactly the accidental-mixing risk this discipline
    guards against. Code must compare with `is`, never `==`, when it
    matters which taxonomy a DISPUTED value belongs to.)"""
    from codex.evidence.model import EvidenceStatus

    assert VerificationStatus is not EvidenceStatus
    assert VerificationStatus.DISPUTED is not EvidenceStatus.DISPUTED
    assert VerificationStatus.DISPUTED == EvidenceStatus.DISPUTED  # the gotcha, documented
