"""Behavioral tests for Contradiction Handling (TAD §49; directive
D10.5)."""

from __future__ import annotations

from datetime import UTC, datetime

from codex.evidence.model import CanonicalRelationship, Evidence
from codex.llm.schema import Claim, ClaimType
from codex.ontology.relationships import RelationshipType
from codex.verification.contradiction import handle_contradictions
from codex.verification.engine import verify_claim
from llm_fixtures import make_evidence_package

NOW = datetime(2026, 8, 31, tzinfo=UTC)


def _evidence(evidence_id: str) -> Evidence:
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


def _rel(subject: str, obj: str, *, contradiction_score: float = 0.0) -> CanonicalRelationship:
    return CanonicalRelationship(
        subject=subject,
        predicate=RelationshipType.CALLS,
        object=obj,
        contradiction_score=contradiction_score,
        supporting_evidence_ids=["e1"],
    )


def _claim(subject: str, obj: str) -> Claim:
    return Claim(subject=subject, predicate="CALLS", object=obj, claim_type=ClaimType.FACT)


def test_no_contradictions_retains_everything_with_no_feedback() -> None:
    package = make_evidence_package(relationships=[_rel("A", "B")], evidence=[_evidence("e1")])
    verification = verify_claim(_claim("A", "B"), package, now=NOW)
    result = handle_contradictions([verification])
    assert result.retained == [verification]
    assert result.removed == []
    assert result.feedback is None
    assert result.has_contradictions is False


def test_significantly_contradicted_claim_is_removed() -> None:
    package = make_evidence_package(
        relationships=[_rel("A", "B", contradiction_score=0.9)], evidence=[_evidence("e1")]
    )
    verification = verify_claim(_claim("A", "B"), package, now=NOW)
    result = handle_contradictions([verification])
    assert result.retained == []
    assert result.removed == [verification]
    assert result.has_contradictions is True


def test_feedback_instructs_removal_not_justification() -> None:
    package = make_evidence_package(
        relationships=[_rel("A", "B", contradiction_score=0.9)], evidence=[_evidence("e1")]
    )
    verification = verify_claim(_claim("A", "B"), package, now=NOW)
    result = handle_contradictions([verification])
    assert result.feedback is not None
    assert "REMOVE" in result.feedback
    assert "must be removed" in result.feedback
    assert "Do not justify" in result.feedback


def test_mixed_batch_partitions_correctly() -> None:
    package = make_evidence_package(
        relationships=[
            _rel("A", "B", contradiction_score=0.9),
            _rel("X", "Y", contradiction_score=0.0),
        ],
        evidence=[_evidence("e1")],
    )
    contradicted = verify_claim(_claim("A", "B"), package, now=NOW)
    clean = verify_claim(_claim("X", "Y"), package, now=NOW)
    result = handle_contradictions([contradicted, clean])
    assert result.removed == [contradicted]
    assert result.retained == [clean]


def test_every_input_appears_in_exactly_one_output_list() -> None:
    package = make_evidence_package(
        relationships=[
            _rel("A", "B", contradiction_score=0.9),
            _rel("X", "Y", contradiction_score=0.0),
        ],
        evidence=[_evidence("e1")],
    )
    verifications = [
        verify_claim(_claim("A", "B"), package, now=NOW),
        verify_claim(_claim("X", "Y"), package, now=NOW),
    ]
    result = handle_contradictions(verifications)
    assert set(id(v) for v in result.retained) | set(id(v) for v in result.removed) == {
        id(v) for v in verifications
    }
    assert len(result.retained) + len(result.removed) == len(verifications)


def test_empty_input_produces_empty_output_no_feedback() -> None:
    result = handle_contradictions([])
    assert result.retained == []
    assert result.removed == []
    assert result.feedback is None
