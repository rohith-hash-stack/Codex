"""Direct tests for the TAD §31 ProviderScore formula and its ADR-018
input sourcing (codex.registry.scoring)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from codex.registry.scoring import (
    DEFAULT_FRESHNESS_HALF_LIFE,
    PROVIDER_SCORE_WEIGHTS,
    ProviderScoreInputs,
    ProviderScoreProfile,
    default_freshness_score,
    provider_score,
)

NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)


def test_weights_match_tad_section_31() -> None:
    assert PROVIDER_SCORE_WEIGHTS == {
        "capability_match": 0.40,
        "evidence_quality": 0.20,
        "availability": 0.15,
        "freshness": 0.15,
        "cost_factor": 0.10,
    }
    assert sum(PROVIDER_SCORE_WEIGHTS.values()) == pytest.approx(1.0)


def test_capability_match_zero_excludes_regardless_of_other_factors() -> None:
    inputs = ProviderScoreInputs(
        capability_match=0.0,
        evidence_quality=1.0,
        availability=1.0,
        freshness=1.0,
        cost_factor=1.0,
    )
    assert provider_score(inputs) == 0.0


def test_full_score_all_ones() -> None:
    inputs = ProviderScoreInputs(
        capability_match=1.0, evidence_quality=1.0, availability=1.0, freshness=1.0, cost_factor=1.0
    )
    assert provider_score(inputs) == pytest.approx(1.0)


def test_weighted_contribution_of_each_factor() -> None:
    baseline = ProviderScoreInputs(
        capability_match=1.0, evidence_quality=0.0, availability=0.0, freshness=0.0, cost_factor=0.0
    )
    assert provider_score(baseline) == pytest.approx(0.40)

    only_evidence_quality = baseline._replace(evidence_quality=1.0)
    assert provider_score(only_evidence_quality) == pytest.approx(0.40 + 0.20)

    only_availability = baseline._replace(availability=1.0)
    assert provider_score(only_availability) == pytest.approx(0.40 + 0.15)

    only_freshness = baseline._replace(freshness=1.0)
    assert provider_score(only_freshness) == pytest.approx(0.40 + 0.15)

    only_cost_factor = baseline._replace(cost_factor=1.0)
    assert provider_score(only_cost_factor) == pytest.approx(0.40 + 0.10)


# --- ProviderScoreProfile (ADR-018: evidence_quality/cost_factor are supplied metadata) ---


def test_profile_accepts_values_in_range() -> None:
    p = ProviderScoreProfile(evidence_quality=0.3, cost_factor=0.7)
    assert p.evidence_quality == 0.3
    assert p.cost_factor == 0.7


@pytest.mark.parametrize("field", ["evidence_quality", "cost_factor"])
def test_profile_rejects_out_of_range_values(field: str) -> None:
    with pytest.raises(ValidationError):
        ProviderScoreProfile(**{"evidence_quality": 0.5, "cost_factor": 0.5, field: 1.5})
    with pytest.raises(ValidationError):
        ProviderScoreProfile(**{"evidence_quality": 0.5, "cost_factor": 0.5, field: -0.1})


# --- default_freshness_score (ADR-018: generic, provider-neutral freshness derivation) ---


def test_freshness_score_none_is_zero() -> None:
    assert default_freshness_score(None, now=NOW) == 0.0


def test_freshness_score_just_extracted_is_one() -> None:
    assert default_freshness_score(NOW, now=NOW) == pytest.approx(1.0)


def test_freshness_score_future_timestamp_clamps_to_one() -> None:
    """Clock skew / a timestamp not yet in the past must not produce a
    nonsensical score above 1.0 or below 0.0."""
    assert default_freshness_score(NOW + timedelta(minutes=5), now=NOW) == pytest.approx(1.0)


def test_freshness_score_halves_after_one_half_life() -> None:
    aged = NOW - DEFAULT_FRESHNESS_HALF_LIFE
    assert default_freshness_score(aged, now=NOW) == pytest.approx(0.5)


def test_freshness_score_quarters_after_two_half_lives() -> None:
    aged = NOW - 2 * DEFAULT_FRESHNESS_HALF_LIFE
    assert default_freshness_score(aged, now=NOW) == pytest.approx(0.25)


def test_freshness_score_half_life_is_a_swappable_parameter() -> None:
    """The default half-life is a calibration point, not a hard-coded
    constant baked into the algorithm itself (ADR-018 point 4)."""
    aged = NOW - timedelta(hours=1)
    assert default_freshness_score(aged, now=NOW, half_life=timedelta(hours=1)) == pytest.approx(
        0.5
    )
