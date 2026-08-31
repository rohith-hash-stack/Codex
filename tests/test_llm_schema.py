"""Behavioral tests for the Structured Answer / Claim schema (TAD
§44-45; directive D10.2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codex.llm.schema import Claim, ClaimType, StructuredAnswer
from codex.ontology.relationships import RelationshipType


def test_valid_claim_constructs() -> None:
    claim = Claim(
        subject="A", predicate=RelationshipType.CALLS, object="B", claim_type=ClaimType.FACT
    )
    assert claim.claim_type is ClaimType.FACT


def test_claim_predicate_must_be_a_canonical_relationship_type() -> None:
    with pytest.raises(ValidationError):
        Claim(subject="A", predicate="INVENTS", object="B", claim_type=ClaimType.FACT)


def test_claim_predicate_accepts_persisted_relationship_type() -> None:
    claim = Claim(subject="A", predicate="CALLS", object="B", claim_type=ClaimType.FACT)
    assert claim.predicate is RelationshipType.CALLS


def test_claim_predicate_accepts_derived_relationship_type() -> None:
    """TAD §45's own worked example: "DERIVED: A REACHES C" -- REACHES
    is in DERIVED_RELATIONSHIP_TYPES (TAD §14, computed at query time),
    not a RelationshipType enum member, and must still be representable."""
    claim = Claim(subject="A", predicate="REACHES", object="C", claim_type=ClaimType.DERIVED)
    assert claim.predicate == "REACHES"


def test_claim_type_must_be_one_of_the_four_canonical_values() -> None:
    with pytest.raises(ValidationError):
        Claim(subject="A", predicate=RelationshipType.CALLS, object="B", claim_type="MAYBE")


def test_structured_answer_defaults_to_empty_claims() -> None:
    answer = StructuredAnswer(explanation="No claims could be established.")
    assert answer.claims == []


def test_structured_answer_with_multiple_claims() -> None:
    answer = StructuredAnswer(
        explanation="X calls Y; Y reaches Z.",
        claims=[
            Claim(
                subject="X", predicate=RelationshipType.CALLS, object="Y", claim_type=ClaimType.FACT
            ),
            Claim(
                subject="Y",
                predicate=RelationshipType.CALLS,
                object="Z",
                claim_type=ClaimType.DERIVED,
            ),
        ],
    )
    assert len(answer.claims) == 2


def test_structured_answer_json_schema_is_strict_and_matches_tad_shape() -> None:
    schema = StructuredAnswer.model_json_schema()
    assert set(schema["required"]) == {"explanation"}  # claims has a default, explanation does not
    assert "claims" in schema["properties"]


def test_explanation_is_not_itself_verified_source_of_truth() -> None:
    """Structural proof of directive D10 Decision (Phase C.1): nothing
    on `StructuredAnswer` ties `explanation` to verification -- only
    `claims[]` carries the typed, verifiable subject-predicate-object
    structure the Verification Engine can check."""
    field_names = set(StructuredAnswer.model_fields)
    assert field_names == {"explanation", "claims"}
    assert "verified" not in field_names
    assert "confidence" not in field_names


def test_missing_claims_array_is_schema_invalid() -> None:
    with pytest.raises(ValidationError):
        StructuredAnswer.model_validate({"explanation": "x", "claims": "not-a-list"})


def test_malformed_per_claim_object_is_schema_invalid() -> None:
    with pytest.raises(ValidationError):
        StructuredAnswer.model_validate(
            {
                "explanation": "x",
                "claims": [{"subject": "A"}],
            }  # missing predicate/object/claim_type
        )
