"""Provider selection tests (TAD §31; directive D9 Part 18 "Provider
selection"): unsupported capability excluded, unavailable provider
excluded, ranked provider selected correctly, D2 scoring reused rather
than duplicated.
"""

from __future__ import annotations

from codex.evidence.store import InMemoryEvidenceStore
from codex.ingestion.pipeline import IngestionPipeline
from codex.planner.provider_selection import select_providers
from codex.provider.capability import Capability
from codex.provider.contract import EligibilityStatus, ProviderEligibility, ProviderHealthStatus
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile
from fake_ingestion_provider import DeterministicFakeAdapter
from planner_fixtures import PROFILE, build_graph, make_repository


def test_unsupported_capability_maps_to_empty_list() -> None:
    _, registry, _, repository = build_graph(entity_paths=("auth.py",))
    selected = select_providers(registry, [Capability.DATA_FLOW], repository)
    assert selected == {"DATA_FLOW": []}


def test_unavailable_provider_is_excluded() -> None:
    registry = CapabilityRegistry()
    repository = make_repository()
    unhealthy = DeterministicFakeAdapter(
        name="unhealthy",
        capabilities=frozenset({Capability.CALL_RELATIONSHIP}),
        health=ProviderHealthStatus.UNHEALTHY,
    )
    registry.register(unhealthy, PROFILE)

    selected = select_providers(registry, [Capability.CALL_RELATIONSHIP], repository)
    assert selected == {"CALL_RELATIONSHIP": []}


def test_ineligible_provider_is_excluded() -> None:
    registry = CapabilityRegistry()
    repository = make_repository()
    ineligible = DeterministicFakeAdapter(
        name="ineligible",
        capabilities=frozenset({Capability.CALL_RELATIONSHIP}),
        eligibility=ProviderEligibility(
            status=EligibilityStatus.INELIGIBLE_REPOSITORY, reason="no match"
        ),
    )
    registry.register(ineligible, PROFILE)

    selected = select_providers(registry, [Capability.CALL_RELATIONSHIP], repository)
    assert selected == {"CALL_RELATIONSHIP": []}


def test_ranked_provider_selected_correctly_best_first() -> None:
    registry = CapabilityRegistry()
    repository = make_repository()
    low = DeterministicFakeAdapter(
        name="low", capabilities=frozenset({Capability.CALL_RELATIONSHIP})
    )
    high = DeterministicFakeAdapter(
        name="high", capabilities=frozenset({Capability.CALL_RELATIONSHIP})
    )
    registry.register(low, ProviderScoreProfile(evidence_quality=0.1, cost_factor=0.9))
    registry.register(high, ProviderScoreProfile(evidence_quality=0.99, cost_factor=0.01))

    selected = select_providers(registry, [Capability.CALL_RELATIONSHIP], repository)
    assert selected["CALL_RELATIONSHIP"][0] == "high"
    assert set(selected["CALL_RELATIONSHIP"]) == {"low", "high"}


def test_select_providers_reuses_registry_rank_not_a_new_algorithm() -> None:
    """`select_providers` must call `CapabilityRegistry.rank()` -- proven
    by registering a provider with NO `ProviderScoreProfile`, which
    `rank()` itself raises `ValueError` for (an existing D2 invariant);
    if `select_providers` had its own scoring logic that ignored
    profiles, this would not raise."""
    import pytest

    registry = CapabilityRegistry()
    repository = make_repository()
    unregistered_profile = DeterministicFakeAdapter(
        name="no-profile", capabilities=frozenset({Capability.CALL_RELATIONSHIP})
    )
    registry.register(unregistered_profile)  # no profile passed

    with pytest.raises(ValueError, match="ProviderScoreProfile"):
        select_providers(registry, [Capability.CALL_RELATIONSHIP], repository)


def test_ingestion_time_runs_every_usable_provider_query_time_only_ranks() -> None:
    """Ingestion-time selection (`CapabilityRegistry.evaluate()`, driving
    `IngestionPipeline`) and query-time selection (`select_providers()`,
    `.rank()`) are distinct operations -- both providers' evidence
    reaches the graph even though query-time ranking prefers one."""
    registry = CapabilityRegistry()
    evidence_store = InMemoryEvidenceStore()
    pipeline = IngestionPipeline(registry, evidence_store)
    repository = make_repository()

    provider_a = DeterministicFakeAdapter(
        name="a",
        capabilities=frozenset({Capability.CALL_RELATIONSHIP}),
        entity_paths=("x.py", "y.py"),
        relationship_pairs=(("x.py", "y.py"),),
    )
    provider_b = DeterministicFakeAdapter(
        name="b",
        capabilities=frozenset({Capability.CALL_RELATIONSHIP}),
        entity_paths=("x.py", "y.py"),
        relationship_pairs=(("x.py", "y.py"),),
    )
    registry.register(provider_a, ProviderScoreProfile(evidence_quality=0.2, cost_factor=0.5))
    registry.register(provider_b, ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.5))

    result = pipeline.run(repository)
    assert set(result.committed_providers) == {"a", "b"}  # both ran at ingestion time

    selected = select_providers(registry, [Capability.CALL_RELATIONSHIP], repository)
    assert selected["CALL_RELATIONSHIP"][0] == "b"  # query-time ranking prefers b
