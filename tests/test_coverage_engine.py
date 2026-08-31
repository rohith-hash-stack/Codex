"""Behavioral tests for the Coverage / Completeness Engine (gap-closure
directive Gap B). Uses `DeterministicFakeAdapter` to construct every
capability-coverage state precisely, plus one integration test through
the real `GitAdapter` and `IngestionPipeline` (directive Phase 18: do
not rely exclusively on fake-provider tests).
"""

from __future__ import annotations

from pathlib import Path

import git

from codex.coverage.engine import (
    CapabilityCoverage,
    NegativeQueryCoverage,
    classify_capability_coverage,
    evaluate_negative_query_coverage,
    is_exhaustive_coverage,
    is_provider_coverage_complete,
)
from codex.evidence.model import CoverageStatus, EvidenceCohort
from codex.evidence.store import InMemoryEvidenceStore
from codex.graph.memory_store import InMemoryGraphStore
from codex.graph.version import GraphVersion
from codex.ingestion.models import IngestionResult, ProviderRunOutcome, ProviderRunStatus
from codex.ingestion.pipeline import IngestionPipeline
from codex.provider.capability import Capability
from codex.provider.contract import EligibilityStatus, ProviderEligibility
from codex.provider.git_adapter import GitAdapter
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile
from codex.repository.models import RepositoryMetadata
from fake_ingestion_provider import DeterministicFakeAdapter

PROFILE = ProviderScoreProfile(evidence_quality=0.8, cost_factor=0.5)


def make_repository(repository_id: str = "repo1", revision: str = "rev1") -> RepositoryMetadata:
    return RepositoryMetadata(
        repository_id=repository_id, local_path=Path("/fake/repo"), head_revision=revision
    )


def _dummy_graph_version() -> GraphVersion:
    return GraphVersion(
        version_id="repo1:rev1", repository_id="repo1", repository_revision="rev1"
    ).publish()


def run_with(adapter: DeterministicFakeAdapter):
    registry = CapabilityRegistry()
    registry.register(adapter, PROFILE)
    pipeline = IngestionPipeline(registry, InMemoryEvidenceStore())
    return pipeline.run(make_repository())


# --- The six capability-coverage distinctions (directive Gap B) -------------


def test_capability_not_supported_by_any_registered_provider() -> None:
    result = run_with(
        DeterministicFakeAdapter(name="fake", capabilities=frozenset({Capability.HISTORY}))
    )
    coverage = classify_capability_coverage(result, Capability.DATA_FLOW)
    assert coverage is CapabilityCoverage.NOT_SUPPORTED


def test_capability_unavailable_when_provider_ineligible() -> None:
    adapter = DeterministicFakeAdapter(
        name="fake",
        capabilities=frozenset({Capability.HISTORY}),
        eligibility=ProviderEligibility(status=EligibilityStatus.INELIGIBLE_REPOSITORY),
    )
    result = run_with(adapter)
    coverage = classify_capability_coverage(result, Capability.HISTORY)
    assert coverage is CapabilityCoverage.UNAVAILABLE
    assert result.skipped_providers == ["fake"]


def test_capability_failed_when_provider_itself_raises() -> None:
    """Distinct from a cohort-level failed_capabilities entry: the provider
    never returns a cohort at all (ProviderExtractionError)."""
    from codex.provider.contract import ProviderFailureReason

    adapter = DeterministicFakeAdapter(
        name="fake",
        capabilities=frozenset({Capability.HISTORY}),
        raise_on_extract=ProviderFailureReason.UNAVAILABLE,
    )
    result = run_with(adapter)
    assert classify_capability_coverage(result, Capability.HISTORY) is CapabilityCoverage.FAILED
    assert result.failed_providers == ["fake"]


def test_capability_failed_defensive_committed_with_no_cohort() -> None:
    """Defensive branch: a COMMITTED outcome should always carry a cohort
    (D4 invariant) -- if it somehow doesn't, treat it as FAILED rather
    than crashing or silently treating it as COMPLETE."""
    outcome = ProviderRunOutcome(
        provider_name="fake",
        status=ProviderRunStatus.COMMITTED,
        capabilities_requested=frozenset({Capability.HISTORY.value}),
        cohort=None,
    )
    result = IngestionResult(
        repository_id="repo1",
        repository_revision="rev1",
        graph_version=_dummy_graph_version(),
        graph_store=InMemoryGraphStore(_dummy_graph_version()),
        provider_outcomes=[outcome],
    )
    assert classify_capability_coverage(result, Capability.HISTORY) is CapabilityCoverage.FAILED


def test_capability_unavailable_when_requested_but_absent_from_cohort_lists() -> None:
    """A capability present in capabilities_requested but absent from all
    three of successful/failed/partial_capabilities (e.g. a cohort
    genuinely never touched it) is UNAVAILABLE, not silently COMPLETE."""
    cohort = EvidenceCohort(
        provider="fake",
        provider_version="1.0.0",
        snapshot_id="rev1",
        source_revision="rev1",
        successful_capabilities=[],
        failed_capabilities=[],
        partial_capabilities=[],
        coverage_status=CoverageStatus.NONE,
    )
    outcome = ProviderRunOutcome(
        provider_name="fake",
        status=ProviderRunStatus.COMMITTED,
        capabilities_requested=frozenset({Capability.HISTORY.value}),
        cohort=cohort,
    )
    result = IngestionResult(
        repository_id="repo1",
        repository_revision="rev1",
        graph_version=_dummy_graph_version(),
        graph_store=InMemoryGraphStore(_dummy_graph_version()),
        provider_outcomes=[outcome],
    )
    assert (
        classify_capability_coverage(result, Capability.HISTORY) is CapabilityCoverage.UNAVAILABLE
    )


def test_capability_failed() -> None:
    adapter = DeterministicFakeAdapter(
        name="fake",
        capabilities=frozenset({Capability.HISTORY}),
        fail_capabilities=frozenset({Capability.HISTORY}),
    )
    result = run_with(adapter)
    coverage = classify_capability_coverage(result, Capability.HISTORY)
    assert coverage is CapabilityCoverage.FAILED


def test_one_capability_failing_does_not_contaminate_a_sibling_capabilitys_status() -> None:
    """A provider requesting two capabilities where only one fails: the
    failed one is FAILED, and the *other*, genuinely successful one is
    classified on its own `successful_capabilities`/entity-count
    membership, not contaminated by its sibling's failure -- per-
    capability classification, not cohort-wide."""
    adapter = DeterministicFakeAdapter(
        name="fake",
        capabilities=frozenset({Capability.HISTORY, Capability.CO_CHANGE}),
        fail_capabilities=frozenset({Capability.CO_CHANGE}),
        entity_paths=("a.py",),
    )
    result = run_with(adapter)
    assert classify_capability_coverage(result, Capability.CO_CHANGE) is CapabilityCoverage.FAILED
    assert classify_capability_coverage(result, Capability.HISTORY) is CapabilityCoverage.COMPLETE


def test_capability_partial_from_cohorts_partial_capabilities_field() -> None:
    """A genuine PARTIAL classification: `EvidenceCohort.partial_capabilities`
    membership (TAD §17's own distinct field, never populated by
    `DeterministicFakeAdapter`'s simple fail/succeed split -- constructed
    directly here, at the engine's own unit level, to exercise the
    branch a full pipeline run cannot currently reach)."""
    cohort = EvidenceCohort(
        provider="fake",
        provider_version="1.0.0",
        snapshot_id="rev1",
        source_revision="rev1",
        successful_capabilities=[],
        failed_capabilities=[],
        partial_capabilities=[Capability.CO_CHANGE.value],
        coverage_status=CoverageStatus.PARTIAL,
    )
    outcome = ProviderRunOutcome(
        provider_name="fake",
        status=ProviderRunStatus.COMMITTED,
        capabilities_requested=frozenset({Capability.CO_CHANGE.value}),
        cohort=cohort,
    )
    result = IngestionResult(
        repository_id="repo1",
        repository_revision="rev1",
        graph_version=_dummy_graph_version(),
        graph_store=InMemoryGraphStore(_dummy_graph_version()),
        provider_outcomes=[outcome],
    )
    assert classify_capability_coverage(result, Capability.CO_CHANGE) is CapabilityCoverage.PARTIAL


def test_capability_empty_success() -> None:
    adapter = DeterministicFakeAdapter(
        name="fake",
        capabilities=frozenset({Capability.HISTORY}),
        produce_empty=True,
    )
    result = run_with(adapter)
    coverage = classify_capability_coverage(result, Capability.HISTORY)
    assert coverage is CapabilityCoverage.EMPTY_SUCCESS


def test_capability_complete() -> None:
    adapter = DeterministicFakeAdapter(
        name="fake", capabilities=frozenset({Capability.HISTORY}), entity_paths=("a.py",)
    )
    result = run_with(adapter)
    coverage = classify_capability_coverage(result, Capability.HISTORY)
    assert coverage is CapabilityCoverage.COMPLETE


# --- Provider-level coverage completeness (items 5-6) ------------------------


def test_provider_coverage_complete_when_all_capabilities_succeed() -> None:
    adapter = DeterministicFakeAdapter(
        name="fake", capabilities=frozenset({Capability.HISTORY}), entity_paths=("a.py",)
    )
    result = run_with(adapter)
    outcome = next(o for o in result.provider_outcomes if o.provider_name == "fake")
    assert is_provider_coverage_complete(outcome) is True


def test_provider_coverage_incomplete_when_any_capability_fails() -> None:
    adapter = DeterministicFakeAdapter(
        name="fake",
        capabilities=frozenset({Capability.HISTORY, Capability.CO_CHANGE}),
        fail_capabilities=frozenset({Capability.CO_CHANGE}),
        entity_paths=("a.py",),
    )
    result = run_with(adapter)
    outcome = next(o for o in result.provider_outcomes if o.provider_name == "fake")
    assert is_provider_coverage_complete(outcome) is False


def test_provider_coverage_incomplete_when_provider_never_ran() -> None:
    adapter = DeterministicFakeAdapter(
        name="fake",
        capabilities=frozenset({Capability.HISTORY}),
        eligibility=ProviderEligibility(status=EligibilityStatus.INELIGIBLE_REPOSITORY),
    )
    result = run_with(adapter)
    outcome = next(o for o in result.provider_outcomes if o.provider_name == "fake")
    assert is_provider_coverage_complete(outcome) is False


# --- Best-evidence-wins across multiple providers of one capability --------


def test_best_status_wins_across_multiple_providers_of_same_capability() -> None:
    complete = DeterministicFakeAdapter(
        name="complete", capabilities=frozenset({Capability.CO_CHANGE}), entity_paths=("a.py",)
    )
    failed = DeterministicFakeAdapter(
        name="failed",
        capabilities=frozenset({Capability.CO_CHANGE}),
        fail_capabilities=frozenset({Capability.CO_CHANGE}),
    )
    registry = CapabilityRegistry()
    registry.register(complete, PROFILE)
    registry.register(failed, PROFILE)
    pipeline = IngestionPipeline(registry, InMemoryEvidenceStore())
    result = pipeline.run(make_repository())

    assert classify_capability_coverage(result, Capability.CO_CHANGE) is CapabilityCoverage.COMPLETE


# --- Negative-query safety (TAD §34 / directive Phase 11) -------------------


def test_negative_query_no_evidence_found_when_capability_complete() -> None:
    result = run_with(
        DeterministicFakeAdapter(
            name="fake", capabilities=frozenset({Capability.CO_CHANGE}), produce_empty=True
        )
    )
    outcome = evaluate_negative_query_coverage(result, Capability.CO_CHANGE)
    assert outcome is NegativeQueryCoverage.NO_EVIDENCE_FOUND


def test_negative_query_inconclusive_when_capability_failed() -> None:
    result = run_with(
        DeterministicFakeAdapter(
            name="fake",
            capabilities=frozenset({Capability.CO_CHANGE}),
            fail_capabilities=frozenset({Capability.CO_CHANGE}),
        )
    )
    outcome = evaluate_negative_query_coverage(result, Capability.CO_CHANGE)
    assert outcome is NegativeQueryCoverage.INCONCLUSIVE


def test_negative_query_inconclusive_when_capability_unavailable() -> None:
    result = run_with(
        DeterministicFakeAdapter(
            name="fake",
            capabilities=frozenset({Capability.CO_CHANGE}),
            eligibility=ProviderEligibility(status=EligibilityStatus.INELIGIBLE_REPOSITORY),
        )
    )
    outcome = evaluate_negative_query_coverage(result, Capability.CO_CHANGE)
    assert outcome is NegativeQueryCoverage.INCONCLUSIVE


def test_negative_query_inconclusive_when_capability_not_supported() -> None:
    result = run_with(
        DeterministicFakeAdapter(name="fake", capabilities=frozenset({Capability.HISTORY}))
    )
    outcome = evaluate_negative_query_coverage(result, Capability.DATA_FLOW)
    assert outcome is NegativeQueryCoverage.INCONCLUSIVE


def test_negative_query_never_returns_false() -> None:
    """FALSE is not a member of NegativeQueryCoverage at all -- structurally
    impossible to return it, matching TAD §34's explicit "not: FALSE"."""
    assert "FALSE" not in {member.value for member in NegativeQueryCoverage}


# --- Exhaustive coverage gate (TAD §33's one implementable level) -----------


def test_exhaustive_coverage_true_when_every_capability_complete() -> None:
    adapter = DeterministicFakeAdapter(
        name="fake",
        capabilities=frozenset({Capability.HISTORY, Capability.CO_CHANGE}),
        entity_paths=("a.py",),
        relationship_pairs=(("a.py", "b.py"),),
    )
    result = run_with(adapter)
    assert (
        is_exhaustive_coverage(result, frozenset({Capability.HISTORY, Capability.CO_CHANGE}))
        is True
    )


def test_exhaustive_coverage_false_when_any_capability_partial_or_failed() -> None:
    adapter = DeterministicFakeAdapter(
        name="fake",
        capabilities=frozenset({Capability.HISTORY, Capability.CO_CHANGE}),
        fail_capabilities=frozenset({Capability.CO_CHANGE}),
        entity_paths=("a.py",),
    )
    result = run_with(adapter)
    assert (
        is_exhaustive_coverage(result, frozenset({Capability.HISTORY, Capability.CO_CHANGE}))
        is False
    )


# --- Real-provider integration (directive Phase 18: not fakes-only) --------


def test_coverage_engine_against_real_git_adapter(tmp_path: Path) -> None:
    repo = git.Repo.init(tmp_path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Test")
        config.set_value("user", "email", "test@example.com")
    (tmp_path / "a.py").write_text("print('hi')\n")
    repo.git.add(A=True)
    repo.git.commit(m="initial")

    registry = CapabilityRegistry()
    registry.register(GitAdapter(), PROFILE)
    pipeline = IngestionPipeline(registry, InMemoryEvidenceStore())
    repository = RepositoryMetadata(
        repository_id="repo1", local_path=tmp_path, head_revision=repo.head.commit.hexsha
    )
    result = pipeline.run(repository)

    assert classify_capability_coverage(result, Capability.HISTORY) is CapabilityCoverage.COMPLETE
    assert (
        classify_capability_coverage(result, Capability.DATA_FLOW)
        is CapabilityCoverage.NOT_SUPPORTED
    )
    outcome = next(o for o in result.provider_outcomes if o.provider_name == "git")
    assert is_provider_coverage_complete(outcome) is True
