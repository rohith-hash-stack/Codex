"""Behavioral tests for the Verification Engine orchestration (TAD §46;
directive D10.4)."""

from __future__ import annotations

from datetime import UTC, datetime

from codex.evidence.model import CanonicalRelationship, Evidence
from codex.llm.schema import Claim, ClaimType
from codex.ontology.relationships import RelationshipType
from codex.verification.confidence import ContradictionLevel
from codex.verification.engine import is_significantly_contradicted, verify_claim, verify_claims
from codex.verification.entailment import EntailmentStatus
from llm_fixtures import make_evidence_package

NOW = datetime(2026, 8, 31, tzinfo=UTC)


def _evidence(evidence_id: str, *, confidence: float = 0.9) -> Evidence:
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


def _rel(contradiction_score: float = 0.0) -> CanonicalRelationship:
    return CanonicalRelationship(
        subject="A",
        predicate=RelationshipType.CALLS,
        object="B",
        contradiction_score=contradiction_score,
        supporting_evidence_ids=["e1"],
    )


def _claim() -> Claim:
    return Claim(subject="A", predicate="CALLS", object="B", claim_type=ClaimType.FACT)


def test_verify_claim_supported_by_direct_edge() -> None:
    package = make_evidence_package(relationships=[_rel()], evidence=[_evidence("e1")])
    result = verify_claim(_claim(), package, now=NOW)
    assert result.entailment.status is EntailmentStatus.SUPPORTED
    assert result.confidence > 0.0
    assert result.contradiction_level is ContradictionLevel.NONE


def test_verify_claim_unresolved_with_no_evidence_yields_zero_confidence() -> None:
    package = make_evidence_package(relationships=[], evidence=[])
    result = verify_claim(_claim(), package, now=NOW)
    assert result.entailment.status is EntailmentStatus.UNRESOLVED
    assert result.confidence == 0.0


def test_verify_claim_significant_contradiction_caps_confidence() -> None:
    package = make_evidence_package(
        relationships=[_rel(contradiction_score=0.9)], evidence=[_evidence("e1")]
    )
    result = verify_claim(_claim(), package, now=NOW)
    assert result.contradiction_level is ContradictionLevel.SIGNIFICANT
    assert result.confidence <= 0.50
    assert is_significantly_contradicted(result)


def test_verify_claim_no_contradiction_is_not_flagged_significant() -> None:
    package = make_evidence_package(relationships=[_rel()], evidence=[_evidence("e1")])
    result = verify_claim(_claim(), package, now=NOW)
    assert not is_significantly_contradicted(result)


def test_verify_claims_preserves_order_and_count() -> None:
    package = make_evidence_package(relationships=[_rel()], evidence=[_evidence("e1")])
    claims = [_claim(), _claim()]
    results = verify_claims(claims, package, now=NOW)
    assert len(results) == 2
    assert [r.claim for r in results] == claims


def test_verify_claims_empty_list_returns_empty() -> None:
    package = make_evidence_package(relationships=[], evidence=[])
    assert verify_claims([], package, now=NOW) == []


def test_verify_claim_supported_by_path_existence_reads_contradiction_from_the_path() -> None:
    """A REACHES claim entailed via a multi-hop path (no single
    `matched_relationship`) must still classify contradiction correctly
    -- from the most-contradicted hop on the path."""
    hop1 = CanonicalRelationship(
        subject="A", predicate=RelationshipType.CALLS, object="X", contradiction_score=0.1
    )
    hop2 = CanonicalRelationship(
        subject="X", predicate=RelationshipType.CALLS, object="B", contradiction_score=0.9
    )
    package = make_evidence_package(relationships=[hop1, hop2], evidence=[])
    claim = Claim(subject="A", predicate="REACHES", object="B", claim_type=ClaimType.DERIVED)
    result = verify_claim(claim, package, now=NOW)
    assert result.entailment.method.value == "PATH_EXISTENCE"
    assert result.contradiction_level is ContradictionLevel.SIGNIFICANT


def test_verify_claim_is_deterministic() -> None:
    package = make_evidence_package(relationships=[_rel()], evidence=[_evidence("e1")])
    claim = _claim()
    first = verify_claim(claim, package, now=NOW)
    second = verify_claim(claim, package, now=NOW)
    assert first == second
