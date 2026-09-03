"""Tests for `codex.api.service.CodexAPI` (VS Code + Nervous-System scope
change, `docs/vscode-nervous-system-architecture.md` §11).

Built on the same `DeterministicFakeAdapter`/`CapabilityRegistry`
wiring `tests/planner_fixtures.py` already uses for every planner test
-- no new fake provider is invented. `register_repository`/
`start_ingestion` exercise the real, unmodified `RepositoryManager`/
`IngestionPipeline`, so each test registers a real (tiny) git
repository rather than bypassing that path.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from git import Actor, Repo

from codex.api.contracts import IngestionJobStatus, RepositoryPhase
from codex.api.service import CodexAPI, IngestionJobNotFoundError, RepositoryNotFoundError
from codex.evidence.store import InMemoryEvidenceStore
from codex.ontology.entities import BaseEntityType
from codex.ontology.relationships import RelationshipType
from codex.provider.capability import Capability
from codex.provider.contract import ProviderFailureReason
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile
from fake_ingestion_provider import DeterministicFakeAdapter

PROFILE = ProviderScoreProfile(evidence_quality=0.8, cost_factor=0.5)
DEFAULT_CAPS = frozenset({Capability.CALL_RELATIONSHIP, Capability.SYMBOL_REFERENCE})


def _make_git_repo(tmp_path: Path) -> Path:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo = Repo.init(repo_dir)
    (repo_dir / "README.md").write_text("hello\n")
    repo.index.add(["README.md"])
    author = Actor("Test", "test@example.com")
    repo.index.commit("initial", author=author, committer=author)
    return repo_dir


def _make_api(adapter: DeterministicFakeAdapter | None = None) -> CodexAPI:
    registry = CapabilityRegistry()
    evidence_store = InMemoryEvidenceStore()
    if adapter is not None:
        registry.register(adapter, PROFILE)
    return CodexAPI(registry, evidence_store)


def _fake_adapter(
    *,
    entity_paths: tuple[str, ...],
    relationship_pairs: tuple[tuple[str, str], ...] = (),
    predicate: RelationshipType = RelationshipType.CALLS,
    base_type: BaseEntityType = BaseEntityType.FUNCTION,
    capabilities: frozenset[Capability] = DEFAULT_CAPS,
    raise_on_extract: ProviderFailureReason | None = None,
) -> DeterministicFakeAdapter:
    return DeterministicFakeAdapter(
        name="fake",
        capabilities=capabilities,
        entity_paths=entity_paths,
        relationship_pairs=relationship_pairs,
        predicate=predicate,
        base_type=base_type,
        raise_on_extract=raise_on_extract,
    )


def _wait_for_terminal(api: CodexAPI, job_id: str, *, timeout: float = 5.0) -> IngestionJobStatus:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = api.get_job_status(job_id)
        if status.phase in (RepositoryPhase.READY, RepositoryPhase.FAILED):
            return status
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach a terminal phase within {timeout}s")


def _make_ready_api(
    tmp_path: Path,
    *,
    repository_id: str = "repo1",
    **adapter_kwargs: object,
) -> CodexAPI:
    repo_dir = _make_git_repo(tmp_path)
    api = _make_api(_fake_adapter(**adapter_kwargs))  # type: ignore[arg-type]
    api.register_repository(repository_id, str(repo_dir))
    handle = api.start_ingestion(repository_id)
    final = _wait_for_terminal(api, handle.job_id)
    assert final.phase == RepositoryPhase.READY, final.detail
    return api


class TestRepositoryLifecycle:
    def test_register_then_ingest_reaches_ready(self, tmp_path: Path) -> None:
        repo_dir = _make_git_repo(tmp_path)
        api = _make_api(
            _fake_adapter(entity_paths=("a", "b"), relationship_pairs=(("a", "b"),))
        )
        status = api.register_repository("repo1", str(repo_dir))
        assert status.phase == RepositoryPhase.REGISTERED
        assert status.head_revision is not None

        handle = api.start_ingestion("repo1")
        assert handle.repository_id == "repo1"

        final = _wait_for_terminal(api, handle.job_id)
        assert final.phase == RepositoryPhase.READY
        assert final.result is not None
        assert final.result.provider_summary[0].provider_name == "fake"
        assert final.result.graph_version_id is not None

        repo_status = api.get_repository_status("repo1")
        assert repo_status.phase == RepositoryPhase.READY
        assert repo_status.graph_version_id == final.result.graph_version_id

    def test_start_ingestion_does_not_block(self, tmp_path: Path) -> None:
        """Proves genuine non-blocking behavior (docs §6), not merely
        documented as a future concern: a deliberately slow provider's
        `extract()` must not have completed by the time `start_ingestion`
        returns control."""

        class _SlowAdapter(DeterministicFakeAdapter):
            def extract(self, repository: object, capabilities: object) -> object:  # type: ignore[override]
                time.sleep(0.2)
                return super().extract(repository, capabilities)  # type: ignore[arg-type]

        repo_dir = _make_git_repo(tmp_path)
        adapter = _SlowAdapter(
            name="slow",
            capabilities=DEFAULT_CAPS,
            entity_paths=("a",),
            base_type=BaseEntityType.FUNCTION,
        )
        api = _make_api(adapter)
        api.register_repository("repo1", str(repo_dir))

        started_at = time.monotonic()
        handle = api.start_ingestion("repo1")
        call_duration = time.monotonic() - started_at
        assert call_duration < 0.1, "start_ingestion must return before the slow provider finishes"

        final = _wait_for_terminal(api, handle.job_id, timeout=5.0)
        assert final.phase == RepositoryPhase.READY
        assert time.monotonic() - started_at >= 0.2

    def test_provider_extraction_failure_is_isolated_and_still_reaches_ready(
        self, tmp_path: Path
    ) -> None:
        """`IngestionPipeline` isolates a per-provider extraction
        failure -- it never raises out of `run()` (directive D4). The
        job tracker must report exactly what the pipeline reports,
        never invent a different outcome; this also proves the
        `FAILED` job phase is reserved for a genuine pipeline-level
        exception, not a normal per-provider failure."""
        repo_dir = _make_git_repo(tmp_path)
        adapter = _fake_adapter(
            entity_paths=("a",), raise_on_extract=ProviderFailureReason.TIMEOUT
        )
        api = _make_api(adapter)
        api.register_repository("repo1", str(repo_dir))
        handle = api.start_ingestion("repo1")
        final = _wait_for_terminal(api, handle.job_id)
        assert final.phase == RepositoryPhase.READY
        assert final.result is not None
        assert final.result.provider_summary[0].status == "FAILED"

    def test_get_repository_status_before_registration_is_not_registered(self) -> None:
        api = _make_api()
        status = api.get_repository_status("ghost")
        assert status.phase == RepositoryPhase.NOT_REGISTERED

    def test_get_repository_status_after_registration_before_ingestion(
        self, tmp_path: Path
    ) -> None:
        repo_dir = _make_git_repo(tmp_path)
        api = _make_api(_fake_adapter(entity_paths=()))
        api.register_repository("repo1", str(repo_dir))
        status = api.get_repository_status("repo1")
        assert status.phase == RepositoryPhase.REGISTERED
        assert status.graph_version_id is None

    def test_start_ingestion_unregistered_repository_raises_keyerror(self) -> None:
        api = _make_api()
        with pytest.raises(KeyError):
            api.start_ingestion("ghost")

    def test_get_job_status_unknown_job_raises(self) -> None:
        api = _make_api()
        with pytest.raises(IngestionJobNotFoundError):
            api.get_job_status("no-such-job")


class TestSymbolLookup:
    def test_lookup_returns_matching_entities_no_edges_depth_zero(self, tmp_path: Path) -> None:
        api = _make_ready_api(tmp_path, entity_paths=("pkg.mod.foo", "pkg.mod.bar"))
        result = api.lookup_symbols("repo1", "foo")
        assert result.requested_depth == 0
        assert result.edges == []
        assert {node.qualified_name for node in result.nodes} == {"pkg.mod.foo"}
        assert result.nodes[0].node_type is BaseEntityType.FUNCTION
        assert result.graph_version is not None

    def test_lookup_empty_query_returns_empty_graph(self, tmp_path: Path) -> None:
        api = _make_ready_api(tmp_path, entity_paths=("pkg.mod.foo",))
        result = api.lookup_symbols("repo1", "")
        assert result.nodes == []

    def test_lookup_no_match_returns_empty_graph_not_fabricated(self, tmp_path: Path) -> None:
        api = _make_ready_api(tmp_path, entity_paths=("pkg.mod.foo",))
        result = api.lookup_symbols("repo1", "totallyNonexistentSymbolXyzzy123")
        assert result.nodes == []

    def test_lookup_distinguishes_same_short_name_different_scopes(self, tmp_path: Path) -> None:
        """FND-1/2/3-shaped regression check through this new path
        (docs §11): two real, distinct entities sharing a trailing
        identifier must remain two distinct nodes here too, never
        collapsed into one by the API layer."""
        api = _make_ready_api(
            tmp_path,
            entity_paths=("pkg.Foo.title", "pkg.Bar.title"),
            base_type=BaseEntityType.METHOD,
        )
        result = api.lookup_symbols("repo1", "title")
        ids = {node.id for node in result.nodes}
        assert len(ids) == 2
        assert {node.qualified_name for node in result.nodes} == {
            "pkg.Foo.title",
            "pkg.Bar.title",
        }

    def test_lookup_unregistered_repository_raises(self) -> None:
        api = _make_api()
        with pytest.raises(RepositoryNotFoundError):
            api.lookup_symbols("ghost", "anything")


class TestNeighborhood:
    def test_neighborhood_matches_bounded_traversal_exactly(self, tmp_path: Path) -> None:
        api = _make_ready_api(
            tmp_path,
            entity_paths=("a", "b", "c"),
            relationship_pairs=(("a", "b"), ("b", "c")),
        )
        graph = api.get_neighborhood("repo1", "a", depth=2)
        assert {node.qualified_name for node in graph.nodes} == {"a", "b", "c"}
        assert len(graph.edges) == 2
        assert {edge.relationship_type for edge in graph.edges} == {RelationshipType.CALLS}
        assert graph.truncated is False
        assert graph.graph_version is not None
        assert graph.requested_depth == 2

        seed_node = next(node for node in graph.nodes if node.qualified_name == "a")
        assert seed_node.distance == 0
        far_node = next(node for node in graph.nodes if node.qualified_name == "c")
        assert far_node.distance == 2

    def test_neighborhood_depth_zero_returns_only_seed(self, tmp_path: Path) -> None:
        api = _make_ready_api(
            tmp_path, entity_paths=("a", "b"), relationship_pairs=(("a", "b"),)
        )
        graph = api.get_neighborhood("repo1", "a", depth=0)
        assert {node.qualified_name for node in graph.nodes} == {"a"}
        assert graph.edges == []

    def test_neighborhood_respects_max_nodes_and_reports_truncated(self, tmp_path: Path) -> None:
        api = _make_ready_api(
            tmp_path,
            entity_paths=("a", "b", "c", "d"),
            relationship_pairs=(("a", "b"), ("a", "c"), ("a", "d")),
        )
        graph = api.get_neighborhood("repo1", "a", depth=1, max_nodes=2)
        assert graph.truncated is True
        assert len(graph.nodes) <= 2

    def test_neighborhood_unknown_symbol_returns_empty_graph_not_fabricated(
        self, tmp_path: Path
    ) -> None:
        api = _make_ready_api(tmp_path, entity_paths=("a",))
        graph = api.get_neighborhood("repo1", "totallyNonexistentSymbolXyzzy123")
        assert graph.nodes == []
        assert graph.edges == []
        assert graph.graph_version is not None

    def test_neighborhood_rejects_negative_depth(self, tmp_path: Path) -> None:
        api = _make_ready_api(tmp_path, entity_paths=("a",))
        with pytest.raises(ValueError):
            api.get_neighborhood("repo1", "a", depth=-1)

    def test_neighborhood_unregistered_repository_raises(self) -> None:
        api = _make_api()
        with pytest.raises(RepositoryNotFoundError):
            api.get_neighborhood("ghost", "a")

    def test_neighborhood_relationship_type_filter_is_the_real_ontology_enum(
        self, tmp_path: Path
    ) -> None:
        api = _make_ready_api(
            tmp_path, entity_paths=("a", "b"), relationship_pairs=(("a", "b"),)
        )
        graph = api.get_neighborhood(
            "repo1", "a", depth=1, relationship_types=[RelationshipType.IMPLEMENTS]
        )
        # The stored edge is CALLS; filtering to IMPLEMENTS must not
        # surface it -- proves the filter is real, not a no-op.
        assert graph.edges == []
