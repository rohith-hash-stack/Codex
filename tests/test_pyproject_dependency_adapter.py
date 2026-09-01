"""Behavioral tests for the D7 pyproject.toml Dependency Adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from codex.evidence.model import CoverageStatus, EvidenceCohort
from codex.evidence.store import InMemoryEvidenceStore
from codex.ingestion.pipeline import IngestionPipeline
from codex.ontology.entities import BaseEntityType
from codex.ontology.relationships import RelationshipType
from codex.provider.capability import Capability
from codex.provider.contract import (
    EligibilityStatus,
    ExtractionResult,
    ProviderExtractionError,
    ProviderHealthStatus,
)
from codex.provider.pyproject_dependency_adapter import (
    DEFAULT_MANIFEST_FILENAME,
    PyprojectDependencyAdapter,
    _distribution_name,
)
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile
from codex.repository.models import RepositoryMetadata

PROFILE = ProviderScoreProfile(evidence_quality=0.95, cost_factor=0.1)


def make_repository(local_path: Path, revision: str = "rev1") -> RepositoryMetadata:
    return RepositoryMetadata(repository_id="repo1", local_path=local_path, head_revision=revision)


def write_manifest(tmp_path: Path, content: str) -> None:
    (tmp_path / DEFAULT_MANIFEST_FILENAME).write_text(content, encoding="utf-8")


def dependency_names(norm) -> set[str]:  # type: ignore[no-untyped-def]
    return {e.name for e in norm.entities if e.base_type is BaseEntityType.EXTERNAL_LIBRARY}


# --- identity / capabilities -------------------------------------------------


def test_identity_and_capabilities() -> None:
    adapter = PyprojectDependencyAdapter()
    assert adapter.provider_name == "pyproject_deps"
    assert adapter.provider_version == "stdlib-tomllib"
    assert adapter.supported_capabilities == frozenset({Capability.DEPENDENCY})
    assert adapter.health_status is ProviderHealthStatus.HEALTHY
    assert adapter.freshness is None


def test_validate_always_ok() -> None:
    assert PyprojectDependencyAdapter().validate().ok is True


def test_check_eligibility_missing_manifest(tmp_path: Path) -> None:
    result = PyprojectDependencyAdapter().check_eligibility(make_repository(tmp_path))
    assert result.status is EligibilityStatus.INELIGIBLE_REPOSITORY
    assert result.eligible is False


def test_check_eligibility_manifest_present(tmp_path: Path) -> None:
    write_manifest(tmp_path, "[project]\nname = 'x'\n")
    result = PyprojectDependencyAdapter().check_eligibility(make_repository(tmp_path))
    assert result.eligible is True


def test_check_eligibility_directory_at_manifest_path_is_ineligible(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_MANIFEST_FILENAME).mkdir()
    result = PyprojectDependencyAdapter().check_eligibility(make_repository(tmp_path))
    assert result.eligible is False


def test_availability_zero_for_unsupported_capability(tmp_path: Path) -> None:
    write_manifest(tmp_path, "[project]\nname = 'x'\n")
    adapter = PyprojectDependencyAdapter()
    assert adapter.availability(Capability.CALL_RELATIONSHIP, make_repository(tmp_path)) == 0.0


def test_availability_full_when_eligible(tmp_path: Path) -> None:
    write_manifest(tmp_path, "[project]\nname = 'x'\n")
    adapter = PyprojectDependencyAdapter()
    assert adapter.availability(Capability.DEPENDENCY, make_repository(tmp_path)) == 1.0


def test_availability_zero_when_ineligible(tmp_path: Path) -> None:
    adapter = PyprojectDependencyAdapter()
    assert adapter.availability(Capability.DEPENDENCY, make_repository(tmp_path)) == 0.0


# --- extraction of real PEP 621 sections --------------------------------------


def test_project_dependencies_extracted(tmp_path: Path) -> None:
    write_manifest(
        tmp_path,
        "[project]\nname = 'x'\ndependencies = ['networkx>=3.2', 'pydantic>=2.6']\n",
    )
    adapter = PyprojectDependencyAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert dependency_names(norm) == {"networkx", "pydantic"}
    for ev in norm.evidence:
        assert ev.predicate is RelationshipType.DEPENDS_ON
        assert ev.confidence == 1.0
        assert ev.provider == "pyproject_deps"


def test_optional_dependencies_flattened_across_extras(tmp_path: Path) -> None:
    write_manifest(
        tmp_path,
        "[project]\nname = 'x'\ndependencies = []\n"
        "[project.optional-dependencies]\n"
        "dev = ['pytest>=8.0', 'ruff>=0.4']\n"
        "docs = ['sphinx>=7']\n",
    )
    adapter = PyprojectDependencyAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert dependency_names(norm) == {"pytest", "ruff", "sphinx"}


def test_dependencies_and_optional_dependencies_combined_deduplicated(tmp_path: Path) -> None:
    write_manifest(
        tmp_path,
        "[project]\nname = 'x'\ndependencies = ['networkx>=3.2']\n"
        "[project.optional-dependencies]\n"
        "dev = ['networkx>=3.2', 'pytest>=8.0']\n",
    )
    adapter = PyprojectDependencyAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert dependency_names(norm) == {"networkx", "pytest"}
    # deduplicated -- one entity, one edge for `networkx`, not two.
    assert len(norm.evidence) == 2


def test_requirement_with_extras_and_marker_reduced_to_bare_name(tmp_path: Path) -> None:
    write_manifest(
        tmp_path,
        "[project]\nname = 'x'\n"
        "dependencies = [\"requests[socks]>=2.0; python_version>='3.8'\"]\n",
    )
    adapter = PyprojectDependencyAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert dependency_names(norm) == {"requests"}


def test_no_dependencies_declared_gives_zero_entities(tmp_path: Path) -> None:
    write_manifest(tmp_path, "[project]\nname = 'x'\ndependencies = []\n")
    adapter = PyprojectDependencyAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert dependency_names(norm) == set()
    # the REPOSITORY entity itself is still created even with zero deps.
    assert any(e.base_type is BaseEntityType.REPOSITORY for e in norm.entities)


def test_no_project_table_gives_zero_dependencies_not_an_error(tmp_path: Path) -> None:
    write_manifest(tmp_path, "[build-system]\nrequires = ['hatchling']\n")
    adapter = PyprojectDependencyAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    assert result.cohort.successful_capabilities == [Capability.DEPENDENCY.value]
    norm = adapter.normalize(result)
    assert dependency_names(norm) == set()


def test_repository_entity_subject_of_every_edge(tmp_path: Path) -> None:
    write_manifest(tmp_path, "[project]\nname = 'x'\ndependencies = ['networkx>=3.2']\n")
    adapter = PyprojectDependencyAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    repo_entity = next(e for e in norm.entities if e.base_type is BaseEntityType.REPOSITORY)
    assert repo_entity.name == "repo1"
    assert all(ev.subject == repo_entity.canonical_id for ev in norm.evidence)


def test_external_library_identity_independent_of_repository_revision(tmp_path: Path) -> None:
    write_manifest(tmp_path, "[project]\nname = 'x'\ndependencies = ['networkx>=3.2']\n")
    adapter = PyprojectDependencyAdapter()
    result = adapter.extract(make_repository(tmp_path, revision="rev1"), {Capability.DEPENDENCY})
    norm1 = adapter.normalize(result)
    result2 = adapter.extract(make_repository(tmp_path, revision="rev2"), {Capability.DEPENDENCY})
    norm2 = adapter.normalize(result2)
    lib1 = next(e for e in norm1.entities if e.base_type is BaseEntityType.EXTERNAL_LIBRARY)
    lib2 = next(e for e in norm2.entities if e.base_type is BaseEntityType.EXTERNAL_LIBRARY)
    assert lib1.canonical_id == lib2.canonical_id


# --- distribution-name extraction (PEP 508 grammar) ---------------------------


@pytest.mark.parametrize(
    ("requirement", "expected"),
    [
        ("networkx", "networkx"),
        ("networkx>=3.2", "networkx"),
        ("networkx>=3.2,<4.0", "networkx"),
        ("requests[socks]>=2.0", "requests"),
        ("some-package==1.0", "some-package"),
        ("some_package==1.0", "some_package"),
        ("Some.Package==1.0", "Some.Package"),
        ("pkg; python_version>='3.8'", "pkg"),
        ("pkg @ https://example.com/pkg.whl", "pkg"),
        ("", None),
        ("   ", None),
        ("[not-a-name]", None),
    ],
)
def test_distribution_name_extraction(requirement: str, expected: str | None) -> None:
    assert _distribution_name(requirement) == expected


def test_non_string_dependency_entries_ignored(tmp_path: Path) -> None:
    write_manifest(
        tmp_path,
        "[project]\nname = 'x'\ndependencies = ['networkx>=3.2']\n"
        "[project.optional-dependencies]\n"
        "dev = [1, true]\n",
    )
    adapter = PyprojectDependencyAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert dependency_names(norm) == {"networkx"}


def test_dependencies_field_wrong_type_ignored(tmp_path: Path) -> None:
    write_manifest(tmp_path, "[project]\nname = 'x'\ndependencies = 'not-a-list'\n")
    adapter = PyprojectDependencyAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert dependency_names(norm) == set()


def test_one_extra_group_wrong_type_ignored_others_kept(tmp_path: Path) -> None:
    write_manifest(
        tmp_path,
        "[project]\nname = 'x'\ndependencies = []\n"
        "[project.optional-dependencies]\n"
        "broken = 'not-a-list'\n"
        "dev = ['pytest>=8.0']\n",
    )
    adapter = PyprojectDependencyAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert dependency_names(norm) == {"pytest"}


def test_optional_dependencies_wrong_type_ignored(tmp_path: Path) -> None:
    write_manifest(
        tmp_path,
        "[project]\nname = 'x'\ndependencies = []\noptional-dependencies = 'nope'\n",
    )
    adapter = PyprojectDependencyAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert dependency_names(norm) == set()


# --- missing / malformed manifest handling (directive: "handle... safely") --


def test_extract_raises_when_manifest_missing(tmp_path: Path) -> None:
    adapter = PyprojectDependencyAdapter()
    with pytest.raises(ProviderExtractionError):
        adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)


def test_extract_raises_on_malformed_toml(tmp_path: Path) -> None:
    write_manifest(tmp_path, "[project\nname = 'unterminated table header'\n")
    adapter = PyprojectDependencyAdapter()
    with pytest.raises(ProviderExtractionError):
        adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)


def test_extract_raises_on_invalid_utf8(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_MANIFEST_FILENAME).write_bytes(b"[project]\nname = '\xff\xfe'\n")
    adapter = PyprojectDependencyAdapter()
    with pytest.raises(ProviderExtractionError):
        adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)


def test_unrequested_capability_not_extracted(tmp_path: Path) -> None:
    write_manifest(tmp_path, "[project]\nname = 'x'\ndependencies = ['networkx>=3.2']\n")
    adapter = PyprojectDependencyAdapter()
    result = adapter.extract(make_repository(tmp_path), frozenset())
    assert result.cohort.successful_capabilities == []
    assert result.cohort.coverage_status is CoverageStatus.NONE
    norm = adapter.normalize(result)
    assert norm.entities == []
    assert norm.evidence == []


def test_normalize_with_none_dependency_names_is_empty() -> None:
    """Boundary test on `ExtractionResult.raw_payload: Any` (TAD invariant
    #2): the not-requested-capability payload shape (`dependency_names:
    None`) must never fabricate the REPOSITORY entity or any evidence."""
    cohort = EvidenceCohort(
        provider="pyproject_deps",
        provider_version="stdlib-tomllib",
        snapshot_id="rev1",
        source_revision="rev1",
    )
    result = ExtractionResult(
        cohort=cohort,
        raw_payload={
            "repository_id": "repo1",
            "revision": "rev1",
            "dependency_names": None,
        },
    )
    norm = PyprojectDependencyAdapter().normalize(result)
    assert norm.entities == []
    assert norm.evidence == []


def test_extract_isolates_unexpected_internal_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import codex.provider.pyproject_dependency_adapter as module

    def boom(document):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated internal failure")

    monkeypatch.setattr(module, "_collect_dependency_names", boom)
    write_manifest(tmp_path, "[project]\nname = 'x'\ndependencies = ['networkx>=3.2']\n")
    adapter = PyprojectDependencyAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    assert result.cohort.failed_capabilities == [Capability.DEPENDENCY.value]
    assert result.cohort.successful_capabilities == []
    assert result.cohort.coverage_status is CoverageStatus.PARTIAL
    norm = adapter.normalize(result)
    assert norm.entities == []
    assert norm.evidence == []


def test_freshness_set_after_extraction(tmp_path: Path) -> None:
    write_manifest(tmp_path, "[project]\nname = 'x'\n")
    adapter = PyprojectDependencyAdapter()
    assert adapter.freshness is None
    adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    assert adapter.freshness is not None


def test_custom_manifest_filename() -> None:
    adapter = PyprojectDependencyAdapter(manifest_filename="custom.toml")
    assert adapter._manifest_filename == "custom.toml"  # noqa: SLF001


# --- pipeline integration: real ingestion path -------------------------------


def test_ingestion_pipeline_materializes_depends_on_edge(tmp_path: Path) -> None:
    write_manifest(tmp_path, "[project]\nname = 'x'\ndependencies = ['networkx>=3.2']\n")
    registry = CapabilityRegistry()
    registry.register(PyprojectDependencyAdapter(), PROFILE)
    pipeline = IngestionPipeline(registry, InMemoryEvidenceStore())
    result = pipeline.run(make_repository(tmp_path))

    relationships = result.graph_store.get_relationships()
    assert len(relationships) == 1
    assert relationships[0].predicate is RelationshipType.DEPENDS_ON
    entities = result.graph_store.find_entities()
    assert {e.name for e in entities} == {"repo1", "networkx"}


def test_tested_by_relationship_type_never_emitted(tmp_path: Path) -> None:
    """Directive: 'do not create a separate TESTED_BY provider'. This
    adapter only ever emits DEPENDS_ON."""
    write_manifest(tmp_path, "[project]\nname = 'x'\ndependencies = ['networkx>=3.2']\n")
    adapter = PyprojectDependencyAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    predicates = {ev.predicate for ev in norm.evidence}
    assert predicates <= {RelationshipType.DEPENDS_ON}
    assert RelationshipType.TESTED_BY not in predicates
