"""Behavioral tests for the D4 Ingestion Pipeline (directive D4 §16).

Uses ``DeterministicFakeAdapter`` (tests/fake_ingestion_provider.py) for
most cases — deliberately chosen over ``tests/fake_provider_adapter.py``
(D1/D2's fixture) since idempotency/determinism tests need repeated
``extract()`` calls with identical inputs to produce identical output.
The real ``GitAdapter`` (D3) is exercised in a real temporary git repo
at the bottom of this file, per directive D4 §16's "fake providers AND
the real Git adapter where appropriate."
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import git
import pytest

from codex.evidence.model import EvidenceStatus
from codex.evidence.store import InMemoryEvidenceStore
from codex.ingestion.models import ProviderRunStatus
from codex.ingestion.pipeline import IngestionPipeline
from codex.ontology.entities import BaseEntityType, build_canonical_id
from codex.ontology.relationships import RelationshipType
from codex.provider.capability import Capability
from codex.provider.contract import (
    EligibilityStatus,
    ProviderEligibility,
    ProviderFailureReason,
    ProviderHealthStatus,
)
from codex.provider.git_adapter import GitAdapter
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile
from codex.repository.models import RepositoryMetadata
from fake_ingestion_provider import DeterministicFakeAdapter


def make_repository(repository_id: str = "repo1", revision: str = "rev1") -> RepositoryMetadata:
    return RepositoryMetadata(
        repository_id=repository_id, local_path=Path("/fake/repo"), head_revision=revision
    )


def make_pipeline() -> tuple[IngestionPipeline, CapabilityRegistry, InMemoryEvidenceStore]:
    registry = CapabilityRegistry()
    evidence_store = InMemoryEvidenceStore()
    pipeline = IngestionPipeline(registry, evidence_store)
    return pipeline, registry, evidence_store


PROFILE = ProviderScoreProfile(evidence_quality=0.8, cost_factor=0.5)


def file_id(repository_id: str, revision: str, path: str) -> str:
    return build_canonical_id(
        repository_id=repository_id,
        repository_revision=revision,
        qualified_name=path,
        base_type=BaseEntityType.FILE,
    )


# ---------------------------------------------------------------------------
# Basic orchestration: discovery, selection, extraction, normalization, graph update
# ---------------------------------------------------------------------------


def test_single_committed_provider_upserts_entities_and_evidence() -> None:
    pipeline, registry, evidence_store = make_pipeline()
    adapter = DeterministicFakeAdapter(
        name="fake",
        capabilities=frozenset({Capability.HISTORY, Capability.CO_CHANGE}),
        entity_paths=("a.py", "b.py"),
        relationship_pairs=(("a.py", "b.py"),),
    )
    registry.register(adapter, PROFILE)

    result = pipeline.run(make_repository())

    assert result.committed_providers == ["fake"]
    assert result.failed_providers == []
    assert result.skipped_providers == []
    assert len(result.graph_store.get_relationships()) == 1
    rel = result.graph_store.get_relationships()[0]
    assert rel.predicate == RelationshipType.CO_CHANGED_WITH
    assert len(rel.supporting_evidence_ids) == 1
    assert evidence_store.get_evidence(rel.supporting_evidence_ids[0]) is not None
    assert evidence_store.get_cohorts(provider="fake") != []


def test_capabilities_param_restricts_extraction_to_subset() -> None:
    pipeline, registry, _ = make_pipeline()
    adapter = DeterministicFakeAdapter(
        name="fake",
        capabilities=frozenset({Capability.HISTORY, Capability.CO_CHANGE}),
        entity_paths=("a.py",),
    )
    registry.register(adapter, PROFILE)

    result = pipeline.run(make_repository(), capabilities={Capability.HISTORY})

    outcome = result.provider_outcomes[0]
    assert outcome.capabilities_requested == frozenset({"HISTORY"})


def test_registry_selection_is_never_bypassed_ineligible_provider_not_extracted() -> None:
    pipeline, registry, _ = make_pipeline()
    adapter = DeterministicFakeAdapter(
        name="fake",
        capabilities=frozenset({Capability.HISTORY}),
        eligibility=ProviderEligibility(status=EligibilityStatus.INELIGIBLE_REPOSITORY),
    )
    registry.register(adapter, PROFILE)

    result = pipeline.run(make_repository())

    assert adapter.extract_calls == 0
    assert result.skipped_providers == ["fake"]
    assert result.provider_outcomes[0].status is ProviderRunStatus.SKIPPED
    assert "INELIGIBLE" in (result.provider_outcomes[0].detail or "")


def test_unavailable_provider_is_skipped_not_failed() -> None:
    pipeline, registry, _ = make_pipeline()
    adapter = DeterministicFakeAdapter(
        name="fake",
        capabilities=frozenset({Capability.HISTORY}),
        health=ProviderHealthStatus.UNHEALTHY,
    )
    registry.register(adapter, PROFILE)

    result = pipeline.run(make_repository())

    assert adapter.extract_calls == 0
    assert result.skipped_providers == ["fake"]
    assert result.failed_providers == []


# ---------------------------------------------------------------------------
# Failure isolation (directive D4 §6, §14)
# ---------------------------------------------------------------------------


def test_provider_failure_does_not_discard_another_providers_evidence() -> None:
    pipeline, registry, _ = make_pipeline()
    broken = DeterministicFakeAdapter(
        name="broken",
        capabilities=frozenset({Capability.HISTORY}),
        raise_on_extract=ProviderFailureReason.UNAVAILABLE,
    )
    working = DeterministicFakeAdapter(
        name="working",
        capabilities=frozenset({Capability.HISTORY}),
        entity_paths=("a.py",),
    )
    registry.register(broken, PROFILE)
    registry.register(working, PROFILE)

    result = pipeline.run(make_repository())

    assert result.failed_providers == ["broken"]
    assert result.committed_providers == ["working"]
    assert result.graph_version.provider_versions == {"working": "1.0.0"}


def test_unexpected_exception_during_extract_is_isolated() -> None:
    pipeline, registry, _ = make_pipeline()

    class ExplodingAdapter(DeterministicFakeAdapter):
        def extract(self, repository, capabilities):  # type: ignore[override]
            raise RuntimeError("kaboom")

    exploding = ExplodingAdapter(name="exploding", capabilities=frozenset({Capability.HISTORY}))
    working = DeterministicFakeAdapter(
        name="working", capabilities=frozenset({Capability.HISTORY}), entity_paths=("a.py",)
    )
    registry.register(exploding, PROFILE)
    registry.register(working, PROFILE)

    result = pipeline.run(make_repository())

    assert result.failed_providers == ["exploding"]
    outcome = next(o for o in result.provider_outcomes if o.provider_name == "exploding")
    assert "kaboom" in (outcome.detail or "")
    assert result.committed_providers == ["working"]


def test_mismatched_cohort_provider_fails_validation_stage() -> None:
    pipeline, registry, _ = make_pipeline()

    class ImpersonatingAdapter(DeterministicFakeAdapter):
        def extract(self, repository, capabilities):  # type: ignore[override]
            result = super().extract(repository, capabilities)
            bad_cohort = result.cohort.model_copy(update={"provider": "someone-else"})
            return result.model_copy(update={"cohort": bad_cohort})

    adapter = ImpersonatingAdapter(
        name="impersonator", capabilities=frozenset({Capability.HISTORY})
    )
    registry.register(adapter, PROFILE)

    result = pipeline.run(make_repository())

    assert result.failed_providers == ["impersonator"]
    assert "cohort.provider" in (result.provider_outcomes[0].detail or "")


def test_unexpected_exception_during_normalize_is_isolated() -> None:
    pipeline, registry, _ = make_pipeline()

    class ExplodingAdapter(DeterministicFakeAdapter):
        def normalize(self, result):  # type: ignore[override]
            raise RuntimeError("boom")

    exploding = ExplodingAdapter(name="exploding", capabilities=frozenset({Capability.HISTORY}))
    working = DeterministicFakeAdapter(
        name="working", capabilities=frozenset({Capability.HISTORY}), entity_paths=("a.py",)
    )
    registry.register(exploding, PROFILE)
    registry.register(working, PROFILE)

    result = pipeline.run(make_repository())

    assert result.failed_providers == ["exploding"]
    outcome = next(o for o in result.provider_outcomes if o.provider_name == "exploding")
    assert "boom" in (outcome.detail or "")
    assert result.committed_providers == ["working"]


def test_capability_level_failure_still_commits_provider() -> None:
    pipeline, registry, _ = make_pipeline()
    adapter = DeterministicFakeAdapter(
        name="fake",
        capabilities=frozenset({Capability.HISTORY, Capability.CO_CHANGE}),
        fail_capabilities=frozenset({Capability.CO_CHANGE}),
        entity_paths=("a.py",),
    )
    registry.register(adapter, PROFILE)

    result = pipeline.run(make_repository())

    assert result.committed_providers == ["fake"]
    outcome = result.provider_outcomes[0]
    assert outcome.cohort is not None
    assert outcome.cohort.failed_capabilities == ["CO_CHANGE"]
    assert outcome.cohort.successful_capabilities == ["HISTORY"]


def test_partial_provider_result_is_committed() -> None:
    pipeline, registry, evidence_store = make_pipeline()
    adapter = DeterministicFakeAdapter(
        name="fake",
        capabilities=frozenset({Capability.HISTORY, Capability.CO_CHANGE}),
        fail_capabilities=frozenset({Capability.CO_CHANGE}),
        entity_paths=("a.py", "b.py"),
    )
    registry.register(adapter, PROFILE)

    result = pipeline.run(make_repository())

    assert result.provider_outcomes[0].status is ProviderRunStatus.COMMITTED
    assert result.provider_outcomes[0].entities_upserted == 2
    assert evidence_store.get_cohorts()[0].coverage_status.value == "PARTIAL"


def test_empty_successful_result_is_not_treated_as_failure() -> None:
    pipeline, registry, _ = make_pipeline()
    adapter = DeterministicFakeAdapter(
        name="fake", capabilities=frozenset({Capability.HISTORY}), produce_empty=True
    )
    registry.register(adapter, PROFILE)

    result = pipeline.run(make_repository())

    assert result.committed_providers == ["fake"]
    outcome = result.provider_outcomes[0]
    assert outcome.cohort is not None
    assert outcome.cohort.successful_capabilities == ["HISTORY"]
    assert outcome.entities_upserted == 0
    assert outcome.evidence_upserted == 0


def test_mismatched_cohort_revision_fails_validation_stage() -> None:
    pipeline, registry, _ = make_pipeline()

    class MismatchedAdapter(DeterministicFakeAdapter):
        def extract(self, repository, capabilities):  # type: ignore[override]
            result = super().extract(repository, capabilities)
            bad_cohort = result.cohort.model_copy(update={"source_revision": "other-revision"})
            return result.model_copy(update={"cohort": bad_cohort})

    adapter = MismatchedAdapter(name="mismatched", capabilities=frozenset({Capability.HISTORY}))
    registry.register(adapter, PROFILE)

    result = pipeline.run(make_repository(revision="rev1"))

    assert result.failed_providers == ["mismatched"]
    assert "source_revision" in (result.provider_outcomes[0].detail or "")


# ---------------------------------------------------------------------------
# Contradictory evidence preservation (directive D4 §11)
# ---------------------------------------------------------------------------


def test_multiple_providers_contradictory_evidence_all_preserved() -> None:
    pipeline, registry, _ = make_pipeline()
    provider_a = DeterministicFakeAdapter(
        name="provider_a",
        capabilities=frozenset({Capability.CO_CHANGE}),
        relationship_pairs=(("a.py", "b.py"),),
        confidence=0.9,
    )
    provider_b = DeterministicFakeAdapter(
        name="provider_b",
        capabilities=frozenset({Capability.CO_CHANGE}),
        relationship_pairs=(("a.py", "b.py"),),
        confidence=0.1,
    )
    registry.register(provider_a, PROFILE)
    registry.register(provider_b, PROFILE)

    result = pipeline.run(make_repository())

    assert set(result.committed_providers) == {"provider_a", "provider_b"}
    relationships = result.graph_store.get_relationships()
    assert len(relationships) == 1
    rel = relationships[0]
    assert len(rel.supporting_evidence_ids) == 2
    assert "provider_a:rev1:0" in rel.supporting_evidence_ids
    assert "provider_b:rev1:0" in rel.supporting_evidence_ids
    assert len(rel.contradicting_evidence_ids) == 0
    # No Reconciliation Engine exists yet -- D4 must not invent status/confidence.
    assert rel.status is EvidenceStatus.UNRESOLVED


# ---------------------------------------------------------------------------
# Idempotency and determinism (directive D4 §10, §16)
# ---------------------------------------------------------------------------


def test_duplicate_ingestion_does_not_create_uncontrolled_duplicates() -> None:
    pipeline, registry, evidence_store = make_pipeline()
    adapter = DeterministicFakeAdapter(
        name="fake",
        capabilities=frozenset({Capability.HISTORY, Capability.CO_CHANGE}),
        entity_paths=("a.py", "b.py"),
        relationship_pairs=(("a.py", "b.py"),),
    )
    registry.register(adapter, PROFILE)
    repository = make_repository()

    result1 = pipeline.run(repository)
    result2 = pipeline.run(repository)

    assert len(result1.graph_store.get_relationships()) == 1
    assert len(result2.graph_store.get_relationships()) == 1
    assert result2.graph_store.get_relationships()[0].supporting_evidence_ids == (
        result1.graph_store.get_relationships()[0].supporting_evidence_ids
    )
    assert result2.graph_store.get_entity(file_id("repo1", "rev1", "a.py")) is not None
    assert result2.graph_store.get_entity(file_id("repo1", "rev1", "b.py")) is not None
    # No duplicate evidence records were created (same evidence_id overwrites).
    assert len(evidence_store.get_evidence_for()) == 1


def test_deterministic_repeated_ingestion_same_version_id_and_content() -> None:
    pipeline, registry, _ = make_pipeline()
    adapter = DeterministicFakeAdapter(
        name="fake",
        capabilities=frozenset({Capability.HISTORY}),
        entity_paths=("a.py",),
    )
    registry.register(adapter, PROFILE)
    repository = make_repository()

    result1 = pipeline.run(repository, now=datetime(2026, 1, 1, tzinfo=UTC))
    result2 = pipeline.run(repository, now=datetime(2026, 1, 1, tzinfo=UTC))

    assert result1.graph_version.version_id == result2.graph_version.version_id
    assert result1.graph_version.provider_versions == result2.graph_version.provider_versions


def test_provider_version_change_produces_a_different_version_id() -> None:
    pipeline, registry, _ = make_pipeline()
    adapter = DeterministicFakeAdapter(
        name="fake", capabilities=frozenset({Capability.HISTORY}), version="1.0.0"
    )
    registry.register(adapter, PROFILE)
    repository = make_repository()

    result1 = pipeline.run(repository)

    upgraded = DeterministicFakeAdapter(
        name="fake", capabilities=frozenset({Capability.HISTORY}), version="2.0.0"
    )
    registry.register(upgraded, PROFILE)
    result2 = pipeline.run(repository)

    assert result1.graph_version.version_id != result2.graph_version.version_id
    assert result1.graph_version.provider_versions == {"fake": "1.0.0"}
    assert result2.graph_version.provider_versions == {"fake": "2.0.0"}


# ---------------------------------------------------------------------------
# Incremental accumulation across revisions (HLRD §23)
# ---------------------------------------------------------------------------


def test_multiple_revisions_accumulate_without_rescanning() -> None:
    pipeline, registry, _ = make_pipeline()
    repo_id = "repo1"

    adapter_rev1 = DeterministicFakeAdapter(
        name="fake", capabilities=frozenset({Capability.HISTORY}), entity_paths=("a.py",)
    )
    registry.register(adapter_rev1, PROFILE)
    result1 = pipeline.run(make_repository(repo_id, "rev1"))
    assert result1.graph_store.get_entity(file_id(repo_id, "rev1", "a.py")) is not None

    adapter_rev2 = DeterministicFakeAdapter(
        name="fake", capabilities=frozenset({Capability.HISTORY}), entity_paths=("b.py",)
    )
    registry.register(adapter_rev2, PROFILE)
    result2 = pipeline.run(make_repository(repo_id, "rev2"))

    # rev2's graph carries forward rev1's entity (only re-extracted, incrementally,
    # by the adapter for its own revision) plus rev2's own new one.
    assert result2.graph_store.get_entity(file_id(repo_id, "rev1", "a.py")) is not None
    assert result2.graph_store.get_entity(file_id(repo_id, "rev2", "b.py")) is not None
    assert result1.graph_version.version_id != result2.graph_version.version_id


# ---------------------------------------------------------------------------
# Graph version creation and non-corruption of prior state (directive D4 §8, §15-16)
# ---------------------------------------------------------------------------


def test_graph_version_is_published_and_immutable() -> None:
    pipeline, registry, _ = make_pipeline()
    adapter = DeterministicFakeAdapter(name="fake", capabilities=frozenset({Capability.HISTORY}))
    registry.register(adapter, PROFILE)

    result = pipeline.run(make_repository())

    assert result.graph_version.published is True
    assert result.graph_version.repository_revision == "rev1"
    assert result.graph_store.version is result.graph_version


def test_failed_ingestion_does_not_corrupt_the_previous_graph() -> None:
    pipeline, registry, _ = make_pipeline()
    adapter = DeterministicFakeAdapter(
        name="fake", capabilities=frozenset({Capability.HISTORY}), entity_paths=("a.py",)
    )
    registry.register(adapter, PROFILE)
    repository = make_repository()

    good_result = pipeline.run(repository)
    good_entity_count = len(good_result.graph_store.get_relationships())

    broken = DeterministicFakeAdapter(
        name="fake",
        capabilities=frozenset({Capability.HISTORY}),
        raise_on_extract=ProviderFailureReason.UNAVAILABLE,
    )
    registry.register(broken, PROFILE)
    bad_result = pipeline.run(repository)

    assert bad_result.failed_providers == ["fake"]
    # The previously returned result's store/version are untouched.
    assert len(good_result.graph_store.get_relationships()) == good_entity_count
    assert good_result.graph_version.published is True


# ---------------------------------------------------------------------------
# Real GitAdapter integration (directive D4 §16: "fake providers AND the real Git adapter")
# ---------------------------------------------------------------------------


def _write(repo_path: Path, name: str, content: str) -> None:
    (repo_path / name).write_text(content)


def _commit_all(repo: git.Repo, message: str) -> str:
    repo.git.add(A=True)
    repo.git.commit(m=message)
    return repo.head.commit.hexsha


@pytest.fixture
def git_repo(tmp_path: Path) -> git.Repo:
    repo = git.Repo.init(tmp_path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Test")
        config.set_value("user", "email", "test@example.com")
    return repo


def test_real_git_adapter_ingestion_produces_history_entities(
    git_repo: git.Repo, tmp_path: Path
) -> None:
    _write(tmp_path, "a.py", "print('a')\n")
    _write(tmp_path, "b.py", "print('b')\n")
    revision = _commit_all(git_repo, "initial")

    registry = CapabilityRegistry()
    evidence_store = InMemoryEvidenceStore()
    registry.register(GitAdapter(), ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.9))
    pipeline = IngestionPipeline(registry, evidence_store)

    repository = RepositoryMetadata(
        repository_id="realrepo", local_path=tmp_path, head_revision=revision
    )
    result = pipeline.run(repository)

    assert result.committed_providers == ["git"]
    assert result.graph_store.get_entity(file_id("realrepo", revision, "a.py")) is not None
    assert result.graph_store.get_entity(file_id("realrepo", revision, "b.py")) is not None


def test_real_git_adapter_ingestion_is_idempotent_across_two_runs(
    git_repo: git.Repo, tmp_path: Path
) -> None:
    _write(tmp_path, "a.py", "print('a')\n")
    revision = _commit_all(git_repo, "initial")

    registry = CapabilityRegistry()
    evidence_store = InMemoryEvidenceStore()
    registry.register(GitAdapter(), ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.9))
    pipeline = IngestionPipeline(registry, evidence_store)
    repository = RepositoryMetadata(
        repository_id="realrepo", local_path=tmp_path, head_revision=revision
    )

    result1 = pipeline.run(repository)
    result2 = pipeline.run(repository)

    entity = file_id("realrepo", revision, "a.py")
    # Same canonical_id both times -- one node, not two (created_at legitimately
    # differs per run since each is a fresh extraction, not a stored duplicate).
    entity1 = result1.graph_store.get_entity(entity)
    entity2 = result2.graph_store.get_entity(entity)
    assert entity1 is not None and entity2 is not None
    assert entity1.canonical_id == entity2.canonical_id
    assert result1.graph_version.version_id == result2.graph_version.version_id
    # Re-running against the same commit produced no new HISTORY evidence to duplicate.
    assert result2.provider_outcomes[0].entities_upserted == 1


def test_real_git_adapter_ingestion_across_two_revisions_accumulates(
    git_repo: git.Repo, tmp_path: Path
) -> None:
    _write(tmp_path, "a.py", "print('a')\n")
    revision1 = _commit_all(git_repo, "first")
    _write(tmp_path, "b.py", "print('b')\n")
    revision2 = _commit_all(git_repo, "second")

    registry = CapabilityRegistry()
    evidence_store = InMemoryEvidenceStore()
    registry.register(GitAdapter(), ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.9))
    pipeline = IngestionPipeline(registry, evidence_store)

    repo1 = RepositoryMetadata(
        repository_id="realrepo", local_path=tmp_path, head_revision=revision1
    )
    repo2 = RepositoryMetadata(
        repository_id="realrepo", local_path=tmp_path, head_revision=revision2
    )

    pipeline.run(repo1)
    result2 = pipeline.run(repo2)

    # rev2's HISTORY only reports b.py (tip-vs-parent diff), but rev1's a.py
    # is still present in the accumulated graph — no rescan, no data loss.
    assert result2.graph_store.get_entity(file_id("realrepo", revision1, "a.py")) is not None
    assert result2.graph_store.get_entity(file_id("realrepo", revision2, "b.py")) is not None
