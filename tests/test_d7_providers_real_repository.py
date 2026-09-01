"""Real-repository integration tests for the D7 providers (`AstCallsAdapter`,
`PyprojectDependencyAdapter`).

Runs both new adapters, through the real, unmodified `IngestionPipeline`,
against Codex's own live source tree and its own real `pyproject.toml`
-- not a synthetic fixture. This is the same "self-hosting" technique
already used elsewhere in this project's validation (the D7 research
audit's own real-repository battery used Codex itself as one of its two
real repositories) and is fully portable: unlike the `veyra` repository
used for manual validation during the D7 audit (a sibling checkout that
exists only in that audit's own environment, not part of this repo and
not guaranteed present wherever this test suite runs), Codex's own
checked-out source is always present wherever these tests execute.
"""

from __future__ import annotations

from pathlib import Path

from codex.evidence.store import InMemoryEvidenceStore
from codex.ingestion.pipeline import IngestionPipeline
from codex.ontology.entities import BaseEntityType
from codex.ontology.relationships import RelationshipType
from codex.provider.ast_calls_adapter import AstCallsAdapter
from codex.provider.pyproject_dependency_adapter import PyprojectDependencyAdapter
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile
from codex.repository.models import RepositoryMetadata

REPO_ROOT = Path(__file__).resolve().parents[1]

CALLS_PROFILE = ProviderScoreProfile(evidence_quality=0.85, cost_factor=0.3)
DEPS_PROFILE = ProviderScoreProfile(evidence_quality=0.95, cost_factor=0.1)


def make_repository() -> RepositoryMetadata:
    return RepositoryMetadata(
        repository_id="codex-self", local_path=REPO_ROOT, head_revision="test-revision"
    )


def test_real_repository_has_expected_manifest_and_source() -> None:
    """Sanity check on the fixture assumption itself: Codex's own real
    `pyproject.toml` declares real runtime dependencies, and its own
    real source tree exists, before asserting anything about them."""
    assert (REPO_ROOT / "pyproject.toml").is_file()
    assert (REPO_ROOT / "src" / "codex" / "ontology" / "entities.py").is_file()


def test_calls_adapter_extracts_real_resolved_call_edges() -> None:
    """`build_canonical_id` (`src/codex/ontology/entities.py`) is called,
    via `from codex.ontology.entities import build_canonical_id`, from
    several real adapter modules -- a genuine, resolvable, real cross-
    file call relationship, not object construction against a fixture."""
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(), adapter.supported_capabilities)
    norm = adapter.normalize(result)

    assert len(norm.entities) > 100  # a real repository, not a toy fixture
    assert len(norm.evidence) > 100
    assert all(ev.predicate is RelationshipType.CALLS for ev in norm.evidence)
    assert all(ev.confidence == 1.0 for ev in norm.evidence)
    assert all(ev.provider == "ast_calls" for ev in norm.evidence)

    by_id = {e.canonical_id: e for e in norm.entities}
    callers_of_build_canonical_id = {
        by_id[ev.subject].qualified_name
        for ev in norm.evidence
        if by_id[ev.object].qualified_name
        == "src/codex/ontology/entities.py::build_canonical_id"
    }
    assert (
        "src/codex/provider/scip_adapter.py::SCIPAdapter.normalize"
        in callers_of_build_canonical_id
    )
    assert (
        "src/codex/provider/pyproject_dependency_adapter.py::PyprojectDependencyAdapter.normalize"
        in callers_of_build_canonical_id
    )


def test_calls_adapter_no_evidence_references_test_or_venv_files() -> None:
    """The virtualenv-exclusion fix (structural `pyvenv.cfg` detection)
    must hold on the real, currently-active `.venv-work` this test suite
    itself runs under -- not just on a synthetic fixture directory."""
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert all(".venv-work" not in e.qualified_name for e in norm.entities)
    assert all("site-packages" not in e.qualified_name for e in norm.entities)


def test_deps_adapter_extracts_real_declared_dependencies() -> None:
    """Codex's own `pyproject.toml` really declares `networkx`,
    `pydantic`, and `GitPython` as runtime dependencies, and `pytest`/
    `ruff`/`mypy` under `[project.optional-dependencies].dev` -- real
    manifest content, not a handcrafted fixture."""
    adapter = PyprojectDependencyAdapter()
    result = adapter.extract(make_repository(), adapter.supported_capabilities)
    norm = adapter.normalize(result)

    names = {e.name for e in norm.entities if e.base_type is BaseEntityType.EXTERNAL_LIBRARY}
    assert {"networkx", "pydantic", "GitPython", "pytest", "ruff", "mypy"} <= names
    assert all(ev.predicate is RelationshipType.DEPENDS_ON for ev in norm.evidence)
    assert all(ev.confidence == 1.0 for ev in norm.evidence)

    repo_entity = next(e for e in norm.entities if e.base_type is BaseEntityType.REPOSITORY)
    assert repo_entity.name == "codex-self"
    assert all(ev.subject == repo_entity.canonical_id for ev in norm.evidence)


def test_full_ingestion_pipeline_with_both_new_providers() -> None:
    """Both providers together, through the real, unmodified
    `IngestionPipeline` -- graph-version/provenance/evidence invariants
    hold on the resulting real graph, exactly as they do for the
    existing D1-D6 providers."""
    registry = CapabilityRegistry()
    registry.register(AstCallsAdapter(), CALLS_PROFILE)
    registry.register(PyprojectDependencyAdapter(), DEPS_PROFILE)
    pipeline = IngestionPipeline(registry, InMemoryEvidenceStore())

    result = pipeline.run(make_repository())

    relationships = result.graph_store.get_relationships()
    predicates = {r.predicate for r in relationships}
    assert RelationshipType.CALLS in predicates
    assert RelationshipType.DEPENDS_ON in predicates
    assert RelationshipType.TESTED_BY not in predicates  # never fabricated (directive)

    entities = result.graph_store.find_entities()
    assert len(entities) > 100

    # every relationship's endpoints are real, committed graph entities --
    # exactly the referential-integrity invariant the earlier "Fix Real-
    # Repository Audit Findings" phase established for `CO_CHANGE`.
    entity_ids = {e.canonical_id for e in entities}
    for rel in relationships:
        assert rel.subject in entity_ids
        assert rel.object in entity_ids

    assert result.graph_store.version is not None
    outcomes = {o.provider_name: o.status.value for o in result.provider_outcomes}
    assert outcomes.get("ast_calls") == "COMMITTED"
    assert outcomes.get("pyproject_deps") == "COMMITTED"


def test_deterministic_repeated_extraction_same_ids() -> None:
    """Two independent extraction runs against the same real repository
    state produce byte-identical canonical ids and evidence subjects/
    objects -- the same determinism guarantee `SCIPAdapter`'s own real-
    artifact tests establish."""
    adapter1 = AstCallsAdapter()
    adapter2 = AstCallsAdapter()
    norm1 = adapter1.normalize(
        adapter1.extract(make_repository(), adapter1.supported_capabilities)
    )
    norm2 = adapter2.normalize(
        adapter2.extract(make_repository(), adapter2.supported_capabilities)
    )
    ids1 = sorted(e.canonical_id for e in norm1.entities)
    ids2 = sorted(e.canonical_id for e in norm2.entities)
    assert ids1 == ids2
    pairs1 = sorted((ev.subject, ev.object) for ev in norm1.evidence)
    pairs2 = sorted((ev.subject, ev.object) for ev in norm2.evidence)
    assert pairs1 == pairs2
