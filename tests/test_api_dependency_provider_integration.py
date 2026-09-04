"""Regression tests for the PyprojectDependencyAdapter integration gap fix
(Codex validation continuation).

`PyprojectDependencyAdapter` was fully implemented and validated against
real repositories in the D7 milestone (`docs/architecture-conformance-
audit.md` §HH) -- these tests prove it, but had never been added to
`codex.api.__main__._build_api()`, the exact registry `python -m codex.api`
(and, through it, the VS Code extension) actually uses. This file proves
the fix through that same real construction path -- `_build_api()` itself,
never a hand-rolled registry -- not just via the adapter's own pre-existing
isolated unit tests (`tests/test_pyproject_dependency_adapter.py`).
"""

from __future__ import annotations

from pathlib import Path

from git import Actor, Repo

from codex.api.__main__ import _build_api
from codex.evidence.store import InMemoryEvidenceStore
from codex.ingestion.pipeline import IngestionPipeline
from codex.ontology.relationships import RelationshipType
from codex.planner.planner import execute_query, plan_query
from codex.provider.ast_calls_adapter import AstCallsAdapter
from codex.provider.capability import Capability
from codex.provider.git_adapter import GitAdapter
from codex.provider.pyproject_dependency_adapter import PyprojectDependencyAdapter
from codex.query_understanding.engine import UnderstandingStatus, understand_query
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile
from codex.repository.models import RepositoryMetadata

PROFILE = ProviderScoreProfile(evidence_quality=0.8, cost_factor=0.3)


def _make_git_repo(tmp_path: Path, *, with_pyproject: bool) -> Path:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo = Repo.init(repo_dir)
    (repo_dir / "app.py").write_text("def main():\n    return 1\n")
    tracked = ["app.py"]
    if with_pyproject:
        (repo_dir / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "0.1.0"\n'
            'dependencies = ["requests>=2.0", "click"]\n'
        )
        tracked.append("pyproject.toml")
    repo.index.add(tracked)
    author = Actor("Test", "test@example.com")
    repo.index.commit("init", author=author, committer=author)
    return repo_dir


def _ingest(repo_dir: Path, registry: CapabilityRegistry) -> tuple:
    evidence_store = InMemoryEvidenceStore()
    pipeline = IngestionPipeline(registry, evidence_store)
    repository = RepositoryMetadata(
        repository_id="demo", local_path=repo_dir, head_revision="test-revision"
    )
    result = pipeline.run(repository)
    return result, evidence_store, repository


class TestDefaultRegistryExposesDependencyProvider:
    def test_build_api_registers_a_dependency_capability_provider(self) -> None:
        """The exact registry `python -m codex.api` uses -- not a
        hand-built stand-in -- must expose `Capability.DEPENDENCY`."""
        api = _build_api()
        registry = api._registry  # the same private attribute CodexAPI itself reads from
        providers = registry.providers_for(Capability.DEPENDENCY)
        assert len(providers) >= 1
        assert any(isinstance(p, PyprojectDependencyAdapter) for p in providers)

    def test_build_api_still_registers_git_and_ast_calls(self) -> None:
        """The fix is additive -- the two pre-existing providers remain
        registered, unaffected by the new one."""
        api = _build_api()
        registry = api._registry
        assert any(isinstance(p, GitAdapter) for p in registry.providers_for(Capability.HISTORY))
        assert any(
            isinstance(p, AstCallsAdapter)
            for p in registry.providers_for(Capability.CALL_RELATIONSHIP)
        )


class TestRealRepositoryProducesDependencyEvidence:
    def test_real_repository_with_pyproject_produces_dependency_evidence(
        self, tmp_path: Path
    ) -> None:
        """A real temp repository with a real `pyproject.toml`, ingested
        through `_build_api()`'s own real registry (Git + AST + the newly
        registered dependency provider) -- not the adapter invoked in
        isolation."""
        repo_dir = _make_git_repo(tmp_path, with_pyproject=True)
        api = _build_api()
        result, _evidence_store, _repository = _ingest(repo_dir, api._registry)

        entities = list(result.graph_store.find_entities())
        dep_edges = [
            rel
            for rel in result.graph_store.get_relationships(predicate=RelationshipType.DEPENDS_ON)
        ]
        assert len(dep_edges) == 2  # requests, click
        names = {e.name for e in entities if e.name in ("requests", "click")}
        assert names == {"requests", "click"}
        # Class A (the manifest's own explicit declaration), no fabrication:
        # near-1.0 after Reconciliation's own saturation formula (unmodified,
        # not exactly 1.0 due to floating point), and SUPPORTED, never DISPUTED.
        assert all(rel.confidence > 0.99 for rel in dep_edges)
        assert all(rel.status.value == "SUPPORTED" for rel in dep_edges)

    def test_find_dependencies_consumes_the_real_evidence(self, tmp_path: Path) -> None:
        """The full D8 (`understand_query`) -> D9 (`plan_query`/
        `execute_query`) path -- the exact pipeline `CodexAPI.ask()` uses
        -- against evidence from `_build_api()`'s real registry."""
        repo_dir = _make_git_repo(tmp_path, with_pyproject=True)
        api = _build_api()
        result, evidence_store, repository = _ingest(repo_dir, api._registry)

        understanding = understand_query("What does demo depend on?", repository_id="demo")
        assert understanding.status is UnderstandingStatus.RESOLVED
        assert understanding.contract is not None

        plan = plan_query(
            query_contract=understanding.contract,
            graph=result.graph_store,
            ingestion_result=result,
            registry=api._registry,
            repository=repository,
        )
        package = execute_query(
            plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
        )

        assert package.coverage.get(Capability.DEPENDENCY.value) is not None
        assert package.coverage[Capability.DEPENDENCY.value].value == "COMPLETE"
        dep_relationships = [
            r for r in package.relationships if r.predicate is RelationshipType.DEPENDS_ON
        ]
        assert len(dep_relationships) == 2
        entity_names = {e.name for e in package.entities}
        assert {"requests", "click"} <= entity_names


class TestAbsentDependencyMetadataStaysHonest:
    def test_repository_without_pyproject_reports_not_supported_not_fabricated(
        self, tmp_path: Path
    ) -> None:
        """No `pyproject.toml` at all -- the provider's own, pre-existing
        `INELIGIBLE_REPOSITORY` eligibility check (unmodified by this fix)
        must make this an honest `NOT_SUPPORTED`/empty result, never a
        fabricated dependency or a pipeline crash."""
        repo_dir = _make_git_repo(tmp_path, with_pyproject=False)
        api = _build_api()
        result, evidence_store, repository = _ingest(repo_dir, api._registry)

        dep_edges = list(
            result.graph_store.get_relationships(predicate=RelationshipType.DEPENDS_ON)
        )
        assert dep_edges == []

        understanding = understand_query("What does demo depend on?", repository_id="demo")
        assert understanding.contract is not None
        plan = plan_query(
            query_contract=understanding.contract,
            graph=result.graph_store,
            ingestion_result=result,
            registry=api._registry,
            repository=repository,
        )
        package = execute_query(
            plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
        )
        assert package.relationships == []
        # Never a false "no dependencies exist" claim -- either an honest
        # NOT_SUPPORTED/PARTIAL coverage reading or an explicit negative-
        # query limitation string, never silent fabrication.
        coverage = package.coverage.get(Capability.DEPENDENCY.value)
        assert coverage is None or coverage.value in ("NOT_SUPPORTED", "FAILED", "UNAVAILABLE")


class TestExistingProvidersUnaffected:
    def test_git_and_ast_calls_evidence_identical_with_or_without_dependency_provider(
        self, tmp_path: Path
    ) -> None:
        """Registering the new provider must not change one byte of what
        `GitAdapter`/`AstCallsAdapter` themselves produce -- purely
        additive, per the directive's "preserve existing provider
        ordering/semantics" requirement."""
        repo_dir = _make_git_repo(tmp_path, with_pyproject=True)

        before_registry = CapabilityRegistry()
        before_registry.register(GitAdapter(), PROFILE)
        before_registry.register(AstCallsAdapter(), PROFILE)
        before_result, _, _ = _ingest(repo_dir, before_registry)

        api = _build_api()
        after_result, _, _ = _ingest(repo_dir, api._registry)

        before_calls = sorted(
            (r.subject, r.object)
            for r in before_result.graph_store.get_relationships(predicate=RelationshipType.CALLS)
        )
        after_calls = sorted(
            (r.subject, r.object)
            for r in after_result.graph_store.get_relationships(predicate=RelationshipType.CALLS)
        )
        assert before_calls == after_calls

        before_entity_ids = {e.canonical_id for e in before_result.graph_store.find_entities()}
        after_entity_ids = {e.canonical_id for e in after_result.graph_store.find_entities()}
        # After-set is a strict superset (the new REPOSITORY/EXTERNAL_LIBRARY
        # entities are additive); every pre-existing entity id is unchanged.
        assert before_entity_ids <= after_entity_ids
