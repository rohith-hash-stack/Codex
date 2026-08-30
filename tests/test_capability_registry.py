"""Behavioral tests for CapabilityRegistry (TAD §10, §31; directive D2 §8).

Uses only ``FakeProviderAdapter`` (test-only, not a real provider) and
generic capability names — no Git/SCIP/CodeQL/Sourcegraph-specific
logic is exercised or implied anywhere in this module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codex.provider.capability import Capability
from codex.provider.contract import EligibilityStatus, ProviderEligibility, ProviderHealthStatus
from codex.registry import CapabilityRegistry, ProviderEvaluationStatus
from codex.repository.models import RepositoryMetadata
from fake_provider_adapter import FakeProviderAdapter


def make_repo(revision: str = "abc123", repository_id: str = "repo1") -> RepositoryMetadata:
    return RepositoryMetadata(
        repository_id=repository_id, local_path=Path("/tmp/repo1"), head_revision=revision
    )


# --- registration / removal ---


def test_register_and_list_providers() -> None:
    registry = CapabilityRegistry()
    adapter = FakeProviderAdapter(name="A")
    registry.register(adapter)
    assert registry.registered_providers() == [adapter]


def test_duplicate_registration_replaces_not_duplicates() -> None:
    registry = CapabilityRegistry()
    original = FakeProviderAdapter(name="A", capabilities=frozenset({Capability.SYMBOL_DEFINITION}))
    registry.register(original)
    replacement = FakeProviderAdapter(name="A", capabilities=frozenset({Capability.DATA_FLOW}))
    registry.register(replacement)

    assert registry.registered_providers() == [replacement]
    assert registry.providers_for(Capability.SYMBOL_DEFINITION) == []
    assert registry.providers_for(Capability.DATA_FLOW) == [replacement]


def test_unregister_removes_provider() -> None:
    registry = CapabilityRegistry()
    adapter = FakeProviderAdapter(name="A")
    registry.register(adapter)
    registry.unregister("A")
    assert registry.registered_providers() == []


def test_unregister_unknown_provider_is_a_noop() -> None:
    registry = CapabilityRegistry()
    registry.unregister("does-not-exist")  # must not raise
    assert registry.registered_providers() == []


# --- capability discovery ---


def test_unknown_capability_returns_empty_not_error() -> None:
    registry = CapabilityRegistry()
    registry.register(FakeProviderAdapter(capabilities=frozenset({Capability.SYMBOL_DEFINITION})))
    assert registry.providers_for(Capability.DATA_FLOW) == []
    assert registry.evaluate(Capability.DATA_FLOW, make_repo()) == []


def test_no_providers_registered_at_all() -> None:
    registry = CapabilityRegistry()
    assert registry.providers_for(Capability.CALL_RELATIONSHIP) == []
    assert registry.evaluate(Capability.CALL_RELATIONSHIP, make_repo()) == []
    assert (
        registry.rank(
            Capability.CALL_RELATIONSHIP,
            make_repo(),
            evidence_quality={},
            freshness_score={},
            cost_factor={},
        )
        == []
    )


def test_capability_match_excludes_unsupported_provider_before_scoring() -> None:
    registry = CapabilityRegistry()
    registry.register(
        FakeProviderAdapter(name="A", capabilities=frozenset({Capability.SYMBOL_DEFINITION}))
    )
    # Provider never declared CALL_RELATIONSHIP: must never appear in evaluate()/rank(),
    # regardless of how favorable evidence_quality/freshness/cost inputs would be.
    assert registry.providers_for(Capability.CALL_RELATIONSHIP) == []
    assert registry.evaluate(Capability.CALL_RELATIONSHIP, make_repo()) == []
    ranked = registry.rank(
        Capability.CALL_RELATIONSHIP,
        make_repo(),
        evidence_quality={"A": 1.0},
        freshness_score={"A": 1.0},
        cost_factor={"A": 1.0},
    )
    assert ranked == []


# --- live evaluation status ---


def test_healthy_and_fully_available_is_available() -> None:
    registry = CapabilityRegistry()
    registry.register(
        FakeProviderAdapter(
            name="A",
            capabilities=frozenset({Capability.CALL_RELATIONSHIP}),
            health=ProviderHealthStatus.HEALTHY,
            default_availability=1.0,
        )
    )
    [evaluation] = registry.evaluate(Capability.CALL_RELATIONSHIP, make_repo())
    assert evaluation.status is ProviderEvaluationStatus.AVAILABLE


def test_healthy_but_zero_availability_is_unavailable() -> None:
    """A HEALTHY provider can still be unusable for a specific capability/repository
    (e.g. a missing license) -- health_status and availability stay independent."""
    registry = CapabilityRegistry()
    registry.register(
        FakeProviderAdapter(
            name="A",
            capabilities=frozenset({Capability.DATA_FLOW}),
            health=ProviderHealthStatus.HEALTHY,
            default_availability=0.0,
        )
    )
    [evaluation] = registry.evaluate(Capability.DATA_FLOW, make_repo())
    assert evaluation.status is ProviderEvaluationStatus.UNAVAILABLE
    assert evaluation.health_status is ProviderHealthStatus.HEALTHY


def test_partial_availability_is_partial() -> None:
    registry = CapabilityRegistry()
    registry.register(
        FakeProviderAdapter(
            name="A",
            capabilities=frozenset({Capability.CALL_RELATIONSHIP}),
            default_availability=0.5,
        )
    )
    [evaluation] = registry.evaluate(Capability.CALL_RELATIONSHIP, make_repo())
    assert evaluation.status is ProviderEvaluationStatus.PARTIAL


def test_ineligible_provider_is_ineligible() -> None:
    registry = CapabilityRegistry()
    registry.register(
        FakeProviderAdapter(
            name="A",
            capabilities=frozenset({Capability.DATA_FLOW}),
            eligibility=ProviderEligibility(
                status=EligibilityStatus.INELIGIBLE_LICENSE, reason="no license"
            ),
        )
    )
    [evaluation] = registry.evaluate(Capability.DATA_FLOW, make_repo())
    assert evaluation.status is ProviderEvaluationStatus.INELIGIBLE
    assert not evaluation.eligibility.eligible


def test_failed_validation_is_failed() -> None:
    registry = CapabilityRegistry()
    registry.register(
        FakeProviderAdapter(
            name="A",
            capabilities=frozenset({Capability.CALL_RELATIONSHIP}),
            validate_ok=False,
        )
    )
    [evaluation] = registry.evaluate(Capability.CALL_RELATIONSHIP, make_repo())
    assert evaluation.status is ProviderEvaluationStatus.FAILED


def test_unhealthy_but_validated_is_unavailable_not_failed() -> None:
    """FAILED (validate()) and UNAVAILABLE (health_status) are distinct
    classifications -- decoupling them here proves the registry checks
    health_status on its own, not merely as a side effect of validate()."""
    registry = CapabilityRegistry()
    registry.register(
        FakeProviderAdapter(
            name="A",
            capabilities=frozenset({Capability.CALL_RELATIONSHIP}),
            health=ProviderHealthStatus.UNHEALTHY,
            validate_ok=True,
            default_availability=1.0,
        )
    )
    [evaluation] = registry.evaluate(Capability.CALL_RELATIONSHIP, make_repo())
    assert evaluation.status is ProviderEvaluationStatus.UNAVAILABLE


def test_degraded_health_at_full_availability_is_partial() -> None:
    registry = CapabilityRegistry()
    registry.register(
        FakeProviderAdapter(
            name="A",
            capabilities=frozenset({Capability.CALL_RELATIONSHIP}),
            health=ProviderHealthStatus.DEGRADED,
            default_availability=1.0,
        )
    )
    [evaluation] = registry.evaluate(Capability.CALL_RELATIONSHIP, make_repo())
    assert evaluation.status is ProviderEvaluationStatus.PARTIAL


# --- multiple providers for the same capability ---


def test_multiple_providers_for_same_capability_all_evaluated() -> None:
    registry = CapabilityRegistry()
    capabilities = frozenset({Capability.CALL_RELATIONSHIP})
    registry.register(FakeProviderAdapter(name="A", capabilities=capabilities))
    registry.register(FakeProviderAdapter(name="B", capabilities=capabilities))
    evaluations = registry.evaluate(Capability.CALL_RELATIONSHIP, make_repo())
    assert {e.provider_name for e in evaluations} == {"A", "B"}


# --- ranking / scoring ---


def test_deterministic_ranking_by_score_descending() -> None:
    registry = CapabilityRegistry()
    registry.register(
        FakeProviderAdapter(name="LOW", capabilities=frozenset({Capability.CALL_RELATIONSHIP}))
    )
    registry.register(
        FakeProviderAdapter(name="HIGH", capabilities=frozenset({Capability.CALL_RELATIONSHIP}))
    )
    kwargs = {
        "evidence_quality": {"LOW": 0.1, "HIGH": 0.9},
        "freshness_score": {"LOW": 0.1, "HIGH": 0.9},
        "cost_factor": {"LOW": 0.1, "HIGH": 0.9},
    }
    ranked_1 = registry.rank(Capability.CALL_RELATIONSHIP, make_repo(), **kwargs)
    ranked_2 = registry.rank(Capability.CALL_RELATIONSHIP, make_repo(), **kwargs)

    assert [e.provider_name for e in ranked_1] == ["HIGH", "LOW"]
    assert ranked_1 == ranked_2  # deterministic: repeated calls agree
    assert ranked_1[0].score is not None
    assert ranked_1[0].score > ranked_1[1].score  # type: ignore[operator]


def test_ranking_ties_break_by_provider_name() -> None:
    registry = CapabilityRegistry()
    capabilities = frozenset({Capability.CALL_RELATIONSHIP})
    registry.register(FakeProviderAdapter(name="B", capabilities=capabilities))
    registry.register(FakeProviderAdapter(name="A", capabilities=capabilities))
    kwargs = {
        "evidence_quality": {"A": 0.5, "B": 0.5},
        "freshness_score": {"A": 0.5, "B": 0.5},
        "cost_factor": {"A": 0.5, "B": 0.5},
    }
    ranked = registry.rank(Capability.CALL_RELATIONSHIP, make_repo(), **kwargs)
    assert [e.provider_name for e in ranked] == ["A", "B"]


def test_rank_excludes_ineligible_and_failed_providers() -> None:
    registry = CapabilityRegistry()
    registry.register(
        FakeProviderAdapter(name="OK", capabilities=frozenset({Capability.CALL_RELATIONSHIP}))
    )
    registry.register(
        FakeProviderAdapter(
            name="INELIGIBLE",
            capabilities=frozenset({Capability.CALL_RELATIONSHIP}),
            eligibility=ProviderEligibility(status=EligibilityStatus.INELIGIBLE_LICENSE),
        )
    )
    registry.register(
        FakeProviderAdapter(
            name="FAILED",
            capabilities=frozenset({Capability.CALL_RELATIONSHIP}),
            validate_ok=False,
        )
    )
    registry.register(
        FakeProviderAdapter(
            name="UNAVAILABLE",
            capabilities=frozenset({Capability.CALL_RELATIONSHIP}),
            default_availability=0.0,
        )
    )
    ranked = registry.rank(
        Capability.CALL_RELATIONSHIP,
        make_repo(),
        evidence_quality={"OK": 1.0},
        freshness_score={"OK": 1.0},
        cost_factor={"OK": 1.0},
    )
    assert [e.provider_name for e in ranked] == ["OK"]


def test_rank_raises_on_missing_scoring_input_for_a_usable_candidate() -> None:
    registry = CapabilityRegistry()
    registry.register(
        FakeProviderAdapter(name="A", capabilities=frozenset({Capability.CALL_RELATIONSHIP}))
    )
    with pytest.raises(ValueError, match="A"):
        registry.rank(
            Capability.CALL_RELATIONSHIP,
            make_repo(),
            evidence_quality={},  # missing "A"
            freshness_score={"A": 1.0},
            cost_factor={"A": 1.0},
        )


def test_rank_uses_the_providers_actual_availability_in_scoring() -> None:
    registry = CapabilityRegistry()
    registry.register(
        FakeProviderAdapter(
            name="A",
            capabilities=frozenset({Capability.CALL_RELATIONSHIP}),
            default_availability=0.5,
        )
    )
    kwargs = {
        "evidence_quality": {"A": 1.0},
        "freshness_score": {"A": 1.0},
        "cost_factor": {"A": 1.0},
    }
    [evaluation] = registry.rank(Capability.CALL_RELATIONSHIP, make_repo(), **kwargs)
    # 0.40*capability_match(1.0) + 0.20*evidence_quality + 0.15*availability(0.5)
    # + 0.15*freshness + 0.10*cost_factor
    assert evaluation.score == pytest.approx(0.40 + 0.20 + 0.15 * 0.5 + 0.15 + 0.10)
