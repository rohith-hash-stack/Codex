"""Direct tests for the TAD §31 ProviderScore formula (codex.registry.scoring)."""

from __future__ import annotations

import pytest

from codex.registry.scoring import PROVIDER_SCORE_WEIGHTS, ProviderScoreInputs, provider_score


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
