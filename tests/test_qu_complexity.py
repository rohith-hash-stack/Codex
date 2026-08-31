"""Behavioral tests for TAD §26's complexity formula (directive D8 Phase 9)."""

from __future__ import annotations

import pytest

from codex.query_understanding.complexity import (
    COMPLEXITY_WEIGHTS,
    compute_complexity,
    normalize_intent_count,
)
from codex.query_understanding.models import ComplexityFactors


def test_weights_sum_to_one() -> None:
    assert sum(COMPLEXITY_WEIGHTS.values()) == pytest.approx(1.0)


def test_zero_factor_query_is_zero_complexity() -> None:
    factors = ComplexityFactors(
        intent_count=0.0,
        target_count=0.0,
        relationship_depth=0.0,
        ambiguity=0.0,
        temporal_dimension=0.0,
        reasoning_requirement=0.0,
    )
    assert compute_complexity(factors) == 0.0


def test_maximum_complexity_is_one() -> None:
    factors = ComplexityFactors(
        intent_count=1.0,
        target_count=1.0,
        relationship_depth=1.0,
        ambiguity=1.0,
        temporal_dimension=1.0,
        reasoning_requirement=1.0,
    )
    assert compute_complexity(factors) == pytest.approx(1.0)


def test_complexity_matches_exact_weighted_sum() -> None:
    factors = ComplexityFactors(
        intent_count=0.4,
        target_count=0.2,
        relationship_depth=0.6,
        ambiguity=0.1,
        temporal_dimension=0.5,
        reasoning_requirement=0.3,
    )
    expected = 0.25 * 0.4 + 0.15 * 0.2 + 0.25 * 0.6 + 0.15 * 0.1 + 0.10 * 0.5 + 0.10 * 0.3
    assert compute_complexity(factors) == pytest.approx(expected)


def test_complexity_is_deterministically_repeatable() -> None:
    factors = ComplexityFactors(
        intent_count=0.3,
        target_count=0.3,
        relationship_depth=0.3,
        ambiguity=0.3,
        temporal_dimension=0.3,
        reasoning_requirement=0.3,
    )
    assert compute_complexity(factors) == compute_complexity(factors)


def test_intent_count_normalization_five_is_one() -> None:
    assert normalize_intent_count(5) == 1.0


def test_intent_count_normalization_above_cap_stays_one() -> None:
    assert normalize_intent_count(9) == 1.0
    assert normalize_intent_count(1000) == 1.0


def test_intent_count_normalization_below_cap() -> None:
    assert normalize_intent_count(0) == 0.0
    assert normalize_intent_count(1) == pytest.approx(0.2)


def test_intent_count_normalization_rejects_negative() -> None:
    with pytest.raises(ValueError, match="intent_count"):
        normalize_intent_count(-1)


def test_complexity_factors_reject_out_of_range_values() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ComplexityFactors(
            intent_count=1.5,
            target_count=0.0,
            relationship_depth=0.0,
            ambiguity=0.0,
            temporal_dimension=0.0,
            reasoning_requirement=0.0,
        )
    with pytest.raises(ValidationError):
        ComplexityFactors(
            intent_count=0.0,
            target_count=-0.1,
            relationship_depth=0.0,
            ambiguity=0.0,
            temporal_dimension=0.0,
            reasoning_requirement=0.0,
        )
