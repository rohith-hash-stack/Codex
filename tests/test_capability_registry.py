"""Behavioral tests for CapabilityRegistry (TAD §10, §31; directive D2 §8; ADR-018).

Uses only ``FakeProviderAdapter`` (test-only, not a real provider) and
generic capability names — no Git/SCIP/CodeQL/Sourcegraph-specific
logic is exercised or implied anywhere in this module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from codex.provider.capability import Capability
from codex.provider.contract import EligibilityStatus, ProviderEligibility, ProviderHealthStatus
from codex.registry import CapabilityRegistry, ProviderEvaluationStatus, ProviderScoreProfile
from codex.repository.models import RepositoryMetadata
from fake_provider_adapter import FakeProviderAdapter

NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)


def make_repo(revision: str = "abc123", repository_id: str = "repo1") -> RepositoryMetadata:
    return RepositoryMetadata(
        repository_id=repository_id, local_path=Path("/tmp/repo1"), head_revision=revision
    )


def profile(evidence_quality: float = 0.5, cost_factor: float = 0.5) -> ProviderScoreProfile:
    return ProviderScoreProfile(evidence_quality=evidence_quality, cost_factor=cost_factor)


# --- registration / removal ---


def test_register_and_list_providers() -> None:
    registry = CapabilityRegistry()
    adapter = FakeProviderAdapter(name="A")
    registry.register(adapter)
    assert registry.registered_providers() == [adapter]


def test_duplicate_registration_replaces_adapter_not_duplicates() -> None:
    registry = CapabilityRegistry()
    original = FakeProviderAdapter(
        name="A", capabilities=frozenset({Capability.SYMBOL_DEFINITION})
    )
    registry.register(original)
    replacement = FakeProviderAdapter(name="A", capabilities=frozenset({Capability.DATA_FLOW}))
    registry.register(replacement)

    assert registry.registered_providers() == [replacement]
    assert registry.providers_for(Capability.SYMBOL_DEFINITION) == []
    assert registry.providers_for(Capability.DATA_FLOW) == [replacement]


def test_duplicate_registration_without_a_profile_preserves_the_existing_one() -> None:
    """Profiles are provider-level canonical metadata, not tied to a
    particular adapter object -- re-registering (e.g. a reconnect)
    without repeating the profile must not silently wipe it out."""
    registry = CapabilityRegistry()
    capabilities = frozenset({Capability.CALL_RELATIONSHIP})
    registry.register(FakeProviderAdapter(name="A", capabilities=capabilities), profile(0.9, 0.9))
    registry.register(FakeProviderAdapter(name="A", capabilities=capabilities))  # no profile

    ranked = registry.rank(Capability.CALL_RELATIONSHIP, make_repo(), now=NOW)
    assert ranked[0].score is not None  # did not raise "no profile registered"


def test_unregister_removes_provider_and_its_profile() -> None:
    registry = CapabilityRegistry()
    capabilities = frozenset({Capability.CALL_RELATIONSHIP})
    registry.register(FakeProviderAdapter(name="A", capabilities=capabilities), profile())
    registry.unregister("A")
    assert registry.registered_providers() == []

    registry.register(FakeProviderAdapter(name="A", capabilities=capabilities))  # no profile now
    with pytest.raises(ValueError, match="A"):
        registry.rank(Capability.CALL_RELATIONSHIP, make_repo(), now=NOW)


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
    assert registry.rank(Capability.CALL_RELATIONSHIP, make_repo()) == []


def test_capability_match_excludes_unsupported_provider_before_scoring() -> None:
    registry = CapabilityRegistry()
    registry.register(
        FakeProviderAdapter(name="A", capabilities=frozenset({Capability.SYMBOL_DEFINITION})),
        profile(1.0, 1.0),  # favorable profile -- must not matter, capability isn't declared
    )
    assert registry.providers_for(Capability.CALL_RELATIONSHIP) == []
    assert registry.evaluate(Capability.CALL_RELATIONSHIP, make_repo()) == []
    assert registry.rank(Capability.CALL_RELATIONSHIP, make_repo()) == []


# --- live evaluation status: Registry derives capability_match/availability itself ---


def test_registry_derives_availability_from_the_adapter_itself() -> None:
    """No caller supplies availability -- it comes only from
    adapter.availability(capability, repository) (D1)."""
    registry = CapabilityRegistry()
    registry.register(
        FakeProviderAdapter(
            name="A",
            capabilities=frozenset({Capability.CALL_RELATIONSHIP}),
            default_availability=0.37,
        )
    )
    [evaluation] = registry.evaluate(Capability.CALL_RELATIONSHIP, make_repo())
    assert evaluation.availability == 0.37


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


# --- ranking / scoring (ADR-018: canonical provider profile, no caller-supplied values) ---


def test_deterministic_ranking_by_score_descending() -> None:
    registry = CapabilityRegistry()
    capabilities = frozenset({Capability.CALL_RELATIONSHIP})
    registry.register(FakeProviderAdapter(name="LOW", capabilities=capabilities), profile(0.1, 0.1))
    registry.register(
        FakeProviderAdapter(name="HIGH", capabilities=capabilities), profile(0.9, 0.9)
    )

    ranked_1 = registry.rank(Capability.CALL_RELATIONSHIP, make_repo(), now=NOW)
    ranked_2 = registry.rank(Capability.CALL_RELATIONSHIP, make_repo(), now=NOW)

    assert [e.provider_name for e in ranked_1] == ["HIGH", "LOW"]
    assert ranked_1 == ranked_2  # identical inputs -> identical ordering, every time
    assert ranked_1[0].score is not None
    assert ranked_1[0].score > ranked_1[1].score  # type: ignore[operator]


def test_ranking_ties_break_by_provider_name() -> None:
    registry = CapabilityRegistry()
    capabilities = frozenset({Capability.CALL_RELATIONSHIP})
    registry.register(FakeProviderAdapter(name="B", capabilities=capabilities), profile())
    registry.register(FakeProviderAdapter(name="A", capabilities=capabilities), profile())
    ranked = registry.rank(Capability.CALL_RELATIONSHIP, make_repo(), now=NOW)
    assert [e.provider_name for e in ranked] == ["A", "B"]


def test_ranking_does_not_depend_on_caller_supplied_scoring_values() -> None:
    """ADR-018: the old per-call evidence_quality/freshness_score/cost_factor
    keyword arguments are gone -- rank() cannot be swayed by an arbitrary
    caller into producing a different ranking for the same repository."""
    registry = CapabilityRegistry()
    registry.register(
        FakeProviderAdapter(
            name="A", capabilities=frozenset({Capability.CALL_RELATIONSHIP})
        ),
        profile(),
    )
    with pytest.raises(TypeError):
        registry.rank(  # type: ignore[call-arg]
            Capability.CALL_RELATIONSHIP,
            make_repo(),
            evidence_quality={"A": 1.0},  # no longer an accepted parameter
        )


def test_rank_excludes_ineligible_and_failed_providers() -> None:
    registry = CapabilityRegistry()
    capabilities = frozenset({Capability.CALL_RELATIONSHIP})
    registry.register(FakeProviderAdapter(name="OK", capabilities=capabilities), profile())
    registry.register(
        FakeProviderAdapter(
            name="INELIGIBLE",
            capabilities=capabilities,
            eligibility=ProviderEligibility(status=EligibilityStatus.INELIGIBLE_LICENSE),
        ),
        profile(),
    )
    registry.register(
        FakeProviderAdapter(name="FAILED", capabilities=capabilities, validate_ok=False),
        profile(),
    )
    registry.register(
        FakeProviderAdapter(
            name="UNAVAILABLE", capabilities=capabilities, default_availability=0.0
        ),
        profile(),
    )
    ranked = registry.rank(Capability.CALL_RELATIONSHIP, make_repo(), now=NOW)
    assert [e.provider_name for e in ranked] == ["OK"]


def test_rank_raises_on_missing_profile_for_a_usable_candidate() -> None:
    registry = CapabilityRegistry()
    registry.register(
        FakeProviderAdapter(name="A", capabilities=frozenset({Capability.CALL_RELATIONSHIP}))
    )  # no profile
    with pytest.raises(ValueError, match="A"):
        registry.rank(Capability.CALL_RELATIONSHIP, make_repo(), now=NOW)


def test_rank_uses_the_providers_actual_availability_in_scoring() -> None:
    registry = CapabilityRegistry()
    registry.register(
        FakeProviderAdapter(
            name="A",
            capabilities=frozenset({Capability.CALL_RELATIONSHIP}),
            default_availability=0.5,
        ),
        profile(1.0, 1.0),
    )
    [evaluation] = registry.rank(Capability.CALL_RELATIONSHIP, make_repo(), now=NOW)
    # 0.40*capability_match(1.0) + 0.20*evidence_quality(1.0) + 0.15*availability(0.5)
    # + 0.15*freshness(0.0, never extracted) + 0.10*cost_factor(1.0)
    assert evaluation.score == pytest.approx(0.40 + 0.20 + 0.15 * 0.5 + 0.15 * 0.0 + 0.10)


def test_rank_uses_the_providers_profile_metadata_for_evidence_quality_and_cost() -> None:
    registry = CapabilityRegistry()
    capabilities = frozenset({Capability.CALL_RELATIONSHIP})
    registry.register(
        FakeProviderAdapter(name="A", capabilities=capabilities), profile(0.2, 0.8)
    )
    [evaluation] = registry.rank(Capability.CALL_RELATIONSHIP, make_repo(), now=NOW)
    expected = 0.40 + 0.20 * 0.2 + 0.15 * 1.0 + 0.15 * 0.0 + 0.10 * 0.8
    assert evaluation.score == pytest.approx(expected)


def test_rank_derives_freshness_from_the_adapters_extraction_history() -> None:
    """freshness is neither caller-supplied nor provider-declared metadata --
    it comes from the adapter's own freshness timestamp, set only once
    extract() actually runs. Isolate its contribution by zeroing out
    every other non-fixed factor (evidence_quality, cost_factor via the
    profile; availability via the adapter)."""
    registry = CapabilityRegistry()
    capabilities = frozenset({Capability.CALL_RELATIONSHIP})
    # UNAVAILABLE (availability == 0.0) is excluded from rank() entirely -- use a
    # tiny nonzero availability (PARTIAL) so the provider still reaches scoring,
    # while still contributing effectively 0.0 to the score.
    adapter = FakeProviderAdapter(name="A", capabilities=capabilities, default_availability=1e-9)
    registry.register(adapter, profile(0.0, 0.0))

    never_extracted = registry.rank(Capability.CALL_RELATIONSHIP, make_repo(), now=NOW)[0]
    assert never_extracted.score == pytest.approx(0.40, abs=1e-6)  # freshness contributes 0.0

    adapter.extract(make_repo(), [Capability.CALL_RELATIONSHIP])
    just_extracted = registry.rank(
        Capability.CALL_RELATIONSHIP, make_repo(), now=adapter.freshness
    )[0]
    # freshness now contributes its full 0.15 (age == 0)
    assert just_extracted.score == pytest.approx(0.40 + 0.15, abs=1e-6)


# --- provider_authority_map (D2 gap-hardening pass, TAD §48) ---


def test_provider_authority_map_empty_when_nothing_registered() -> None:
    registry = CapabilityRegistry()
    assert registry.provider_authority_map() == {}


def test_provider_authority_map_reflects_each_providers_evidence_quality() -> None:
    registry = CapabilityRegistry()
    registry.register(FakeProviderAdapter(name="A"), profile(evidence_quality=0.9))
    registry.register(FakeProviderAdapter(name="B"), profile(evidence_quality=0.3))
    assert registry.provider_authority_map() == {"A": 0.9, "B": 0.3}


def test_provider_authority_map_omits_providers_with_no_profile() -> None:
    """Unlike rank(), which raises for a usable candidate missing a
    profile, this is a best-effort lookup -- a provider used only for
    evidence (never for provider selection) may legitimately have no
    ProviderScoreProfile, and callers fall back to the historical
    default (1.0) for an absent entry rather than erroring."""
    registry = CapabilityRegistry()
    registry.register(FakeProviderAdapter(name="A"), profile(evidence_quality=0.7))
    registry.register(FakeProviderAdapter(name="B"))  # no profile
    assert registry.provider_authority_map() == {"A": 0.7}


def test_provider_authority_map_drops_a_providers_entry_on_unregister() -> None:
    registry = CapabilityRegistry()
    registry.register(FakeProviderAdapter(name="A"), profile(evidence_quality=0.6))
    registry.unregister("A")
    assert registry.provider_authority_map() == {}


def test_provider_authority_map_updates_when_a_new_profile_replaces_the_old_one() -> None:
    registry = CapabilityRegistry()
    registry.register(FakeProviderAdapter(name="A"), profile(evidence_quality=0.2))
    registry.register(FakeProviderAdapter(name="A"), profile(evidence_quality=0.8))
    assert registry.provider_authority_map() == {"A": 0.8}
