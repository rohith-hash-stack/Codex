"""Behavioral tests for `QueryContract` validation (TAD §27; directive
D8 Phase 5, 10)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codex.coverage.engine import CompletenessLevel
from codex.ontology.relationships import RelationshipType
from codex.query_understanding.models import Intent, QueryContract, TemporalDimension


def _base_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "intent": Intent.FIND_CALLERS,
        "targets": ["authenticate"],
        "complexity": 0.2,
        "ambiguity": 0.1,
        "confidence": 0.97,
        "token_budget": 4000,
        "latency_budget_ms": 5000,
    }
    kwargs.update(overrides)
    return kwargs


def test_valid_contract_constructs() -> None:
    contract = QueryContract(**_base_kwargs())
    assert contract.intent is Intent.FIND_CALLERS
    assert contract.completeness_requirement is CompletenessLevel.LOW
    assert contract.temporal_dimension is TemporalDimension.NONE


def test_unknown_intent_string_is_rejected() -> None:
    with pytest.raises(ValidationError):
        QueryContract(**_base_kwargs(intent="NOT_A_REAL_INTENT"))


def test_unknown_relationship_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        QueryContract(**_base_kwargs(relationship_types=["NOT_A_REAL_RELATIONSHIP"]))


def test_confidence_above_one_is_rejected() -> None:
    with pytest.raises(ValidationError):
        QueryContract(**_base_kwargs(confidence=1.5))


def test_confidence_below_zero_is_rejected() -> None:
    with pytest.raises(ValidationError):
        QueryContract(**_base_kwargs(confidence=-0.1))


def test_complexity_out_of_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        QueryContract(**_base_kwargs(complexity=1.1))
    with pytest.raises(ValidationError):
        QueryContract(**_base_kwargs(complexity=-0.01))


def test_ambiguity_out_of_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        QueryContract(**_base_kwargs(ambiguity=2.0))


def test_zero_or_negative_token_budget_is_rejected() -> None:
    with pytest.raises(ValidationError):
        QueryContract(**_base_kwargs(token_budget=0))
    with pytest.raises(ValidationError):
        QueryContract(**_base_kwargs(token_budget=-100))


def test_zero_or_negative_latency_budget_is_rejected() -> None:
    with pytest.raises(ValidationError):
        QueryContract(**_base_kwargs(latency_budget_ms=0))


def test_contradictory_unknown_intent_with_targets_is_rejected() -> None:
    with pytest.raises(ValidationError, match="UNKNOWN"):
        QueryContract(**_base_kwargs(intent=Intent.UNKNOWN, targets=["authenticate"]))


def test_contradictory_unknown_intent_with_relationship_types_is_rejected() -> None:
    with pytest.raises(ValidationError, match="UNKNOWN"):
        QueryContract(
            **_base_kwargs(
                intent=Intent.UNKNOWN, targets=[], relationship_types=[RelationshipType.CALLS]
            )
        )


def test_unknown_intent_with_no_targets_is_valid() -> None:
    contract = QueryContract(**_base_kwargs(intent=Intent.UNKNOWN, targets=[]))
    assert contract.intent is Intent.UNKNOWN


def test_malformed_temporal_dimension_is_rejected() -> None:
    with pytest.raises(ValidationError):
        QueryContract(**_base_kwargs(temporal_dimension="SOMETIME_MAYBE"))


def test_malformed_completeness_requirement_is_rejected() -> None:
    with pytest.raises(ValidationError):
        QueryContract(**_base_kwargs(completeness_requirement="KINDA_COMPLETE"))


def test_relationship_types_accepts_real_ontology_values() -> None:
    contract = QueryContract(
        **_base_kwargs(relationship_types=[RelationshipType.CALLS, RelationshipType.IMPORTS])
    )
    assert contract.relationship_types == [RelationshipType.CALLS, RelationshipType.IMPORTS]
