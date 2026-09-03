"""Focused regression tests for the R1/R2 ingestion-correctness fix
cycle (API + VS Code architecture audit, `52ab915`).

R1 -- concurrent ingestion of the same repository could race
`IngestionPipeline`'s shared per-repository accumulator dicts.
R2 -- `start_ingestion` could silently reuse a stale `head_revision`
captured at an earlier `register_repository`/`clone` call.

These tests use real `threading.Thread`s (via `CodexAPI.start_ingestion`
itself, unmodified), a real gate (`threading.Event`) to force a
genuine, deterministic concurrency window, and real `git` repositories
(via `GitPython`, the same library `RepositoryManager`/`GitAdapter`
already depend on) -- never a mocked boolean standing in for either
concurrency or git state.
"""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import pytest
from git import Actor, Repo

from codex.api.contracts import IngestionJobStatus, RepositoryPhase
from codex.api.service import CodexAPI, GitRevisionResolutionError
from codex.evidence.store import InMemoryEvidenceStore
from codex.ontology.entities import BaseEntityType
from codex.ontology.relationships import RelationshipType
from codex.provider.ast_calls_adapter import AstCallsAdapter
from codex.provider.capability import Capability
from codex.provider.git_adapter import GitAdapter
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile
from fake_ingestion_provider import DeterministicFakeAdapter

PROFILE = ProviderScoreProfile(evidence_quality=0.8, cost_factor=0.5)
DEFAULT_CAPS = frozenset({Capability.CALL_RELATIONSHIP, Capability.SYMBOL_REFERENCE})


def _make_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    repo = Repo.init(path)
    (path / "README.md").write_text("hello\n")
    repo.index.add(["README.md"])
    author = Actor("Test", "test@example.com")
    repo.index.commit("initial", author=author, committer=author)
    return path


def _commit_new_file(repo_dir: Path, filename: str, content: str) -> str:
    repo = Repo(repo_dir)
    (repo_dir / filename).write_text(content)
    repo.index.add([filename])
    author = Actor("Test", "test@example.com")
    repo.index.commit(f"add {filename}", author=author, committer=author)
    return repo.head.commit.hexsha


def _wait_for_terminal(api: CodexAPI, job_id: str, *, timeout: float = 5.0) -> IngestionJobStatus:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = api.get_job_status(job_id)
        if status.phase in (RepositoryPhase.READY, RepositoryPhase.FAILED):
            return status
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach a terminal phase within {timeout}s")


def _fake_adapter(**kwargs: object) -> DeterministicFakeAdapter:
    defaults: dict[str, object] = {
        "name": "fake",
        "capabilities": DEFAULT_CAPS,
        "base_type": BaseEntityType.FUNCTION,
        "predicate": RelationshipType.CALLS,
    }
    defaults.update(kwargs)
    return DeterministicFakeAdapter(**defaults)  # type: ignore[arg-type]


def _make_api(adapter: DeterministicFakeAdapter | None = None) -> CodexAPI:
    registry = CapabilityRegistry()
    evidence_store = InMemoryEvidenceStore()
    if adapter is not None:
        registry.register(adapter, PROFILE)
    return CodexAPI(registry, evidence_store)


class _GatedOnFirstCallAdapter(DeterministicFakeAdapter):
    """A `DeterministicFakeAdapter` whose *first* `extract()` call
    blocks on `gate` until the test releases it, and signals
    `first_call_started` the instant it begins blocking -- giving the
    test a deterministic way to know a real ingestion is genuinely
    in-flight before it acts. Every call after the first proceeds
    immediately, unaffected."""

    def __init__(
        self,
        *args: object,
        gate: threading.Event,
        first_call_started: threading.Event,
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._gate = gate
        self._first_call_started = first_call_started
        self._call_index = 0
        self._call_index_lock = threading.Lock()

    def extract(self, repository: object, capabilities: object) -> object:  # type: ignore[override]
        with self._call_index_lock:
            is_first_call = self._call_index == 0
            self._call_index += 1
        if is_first_call:
            self._first_call_started.set()
            assert self._gate.wait(timeout=5.0), "test never released the gate"
        return super().extract(repository, capabilities)  # type: ignore[arg-type]


# ---------------------------------------------------------------------
# R1 -- concurrent ingestion of the same repository
# ---------------------------------------------------------------------
class TestR1ConcurrentIngestionSafety:
    def test_overlapping_start_ingestion_same_repository_is_singleflighted(
        self, tmp_path: Path
    ) -> None:
        """(R1 #1, #2) Two `start_ingestion` calls for the SAME
        repository, with the second issued while the first is
        genuinely still running (blocked mid-`extract()`), must not
        both mutate ingestion state: the second call must receive the
        first job's own handle (the documented behavior for an
        already-running repository), and the provider's `extract()`
        must have been invoked exactly once -- proving only one real
        mutation ever happened, not merely that two calls returned
        without raising."""
        repo_dir = _make_git_repo(tmp_path / "repo")
        gate = threading.Event()
        first_call_started = threading.Event()
        adapter = _GatedOnFirstCallAdapter(
            name="gated",
            capabilities=DEFAULT_CAPS,
            entity_paths=("a", "b"),
            relationship_pairs=(("a", "b"),),
            base_type=BaseEntityType.FUNCTION,
            gate=gate,
            first_call_started=first_call_started,
        )
        api = _make_api(adapter)
        api.register_repository("repo1", str(repo_dir))

        handle1 = api.start_ingestion("repo1")
        assert first_call_started.wait(timeout=5.0), "first ingestion never started extracting"

        # repo1's ingestion is now genuinely in-flight (blocked inside
        # extract()). A second request for the SAME repository must not
        # start a second mutation.
        handle2 = api.start_ingestion("repo1")
        assert handle2.job_id == handle1.job_id
        assert handle2.repository_id == handle1.repository_id

        gate.set()  # release the single blocked extraction
        final = _wait_for_terminal(api, handle1.job_id)
        assert final.phase == RepositoryPhase.READY
        assert adapter.extract_calls == 1, (
            "a second start_ingestion call for an in-flight repository triggered "
            "a second, unsafe mutation of shared ingestion state"
        )
        assert final.result is not None
        assert final.result.provider_summary[0].entities_upserted == 2

    def test_different_repositories_ingest_independently_not_serialized(
        self, tmp_path: Path
    ) -> None:
        """(R1 #3) A repository-scoped guard must not become a global
        one: while repoA's ingestion is genuinely blocked in-flight, a
        request for a *different* repository (repoB) must proceed and
        complete without waiting for repoA."""
        repo_dir_a = _make_git_repo(tmp_path / "repoA")
        repo_dir_b = _make_git_repo(tmp_path / "repoB")
        gate = threading.Event()
        first_call_started = threading.Event()
        adapter = _GatedOnFirstCallAdapter(
            name="gated",
            capabilities=DEFAULT_CAPS,
            entity_paths=("x",),
            base_type=BaseEntityType.FUNCTION,
            gate=gate,
            first_call_started=first_call_started,
        )
        api = _make_api(adapter)
        api.register_repository("repoA", str(repo_dir_a))
        api.register_repository("repoB", str(repo_dir_b))

        handle_a = api.start_ingestion("repoA")
        assert first_call_started.wait(timeout=5.0), "repoA's ingestion never started"

        # repoA is blocked; repoB must not be forced to wait for it.
        handle_b = api.start_ingestion("repoB")
        final_b = _wait_for_terminal(api, handle_b.job_id, timeout=5.0)
        assert final_b.phase == RepositoryPhase.READY

        # repoA is still legitimately in flight -- proves repoB's
        # completion required no interaction with repoA's lock/state.
        status_a = api.get_job_status(handle_a.job_id)
        assert status_a.phase == RepositoryPhase.INDEXING

        gate.set()
        final_a = _wait_for_terminal(api, handle_a.job_id)
        assert final_a.phase == RepositoryPhase.READY

    def test_failed_job_releases_active_slot_and_next_ingestion_succeeds(
        self, tmp_path: Path
    ) -> None:
        """(R1 #4, #5) A job that fails at the *pipeline* level (an
        unexpected exception, not an isolated per-provider failure --
        see `test_api_service.py`'s
        `test_provider_extraction_failure_is_isolated_and_still_reaches_ready`
        for that already-covered case) must still release its
        repository's active-ingestion slot, and a subsequent ingestion
        for that same repository must be able to start and succeed."""
        repo_dir = _make_git_repo(tmp_path / "repo")
        api = _make_api(_fake_adapter(entity_paths=("a",)))
        api.register_repository("repo1", str(repo_dir))

        real_run = api._pipeline.run
        call_count = {"n": 0}

        def _fail_once_then_delegate(metadata: object) -> object:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated pipeline-level failure")
            return real_run(metadata)  # type: ignore[arg-type]

        api._pipeline.run = _fail_once_then_delegate  # type: ignore[method-assign]

        handle1 = api.start_ingestion("repo1")
        final1 = _wait_for_terminal(api, handle1.job_id)
        assert final1.phase == RepositoryPhase.FAILED
        assert final1.detail is not None
        assert "simulated pipeline-level failure" in final1.detail

        # The failed job must not leave repo1 permanently "busy": a
        # fresh start_ingestion must get a brand-new job, not be
        # silently rejected or hang.
        handle2 = api.start_ingestion("repo1")
        assert handle2.job_id != handle1.job_id
        final2 = _wait_for_terminal(api, handle2.job_id)
        assert final2.phase == RepositoryPhase.READY
        assert final2.result is not None

    def test_repeated_sequential_ingestion_remains_valid(self, tmp_path: Path) -> None:
        """(R1 #6) The fix must not disturb ordinary, non-overlapping,
        repeated ingestion of the same repository -- three sequential
        calls each complete cleanly."""
        repo_dir = _make_git_repo(tmp_path / "repo")
        api = _make_api(_fake_adapter(entity_paths=("a", "b"), relationship_pairs=(("a", "b"),)))
        api.register_repository("repo1", str(repo_dir))

        for _ in range(3):
            handle = api.start_ingestion("repo1")
            final = _wait_for_terminal(api, handle.job_id)
            assert final.phase == RepositoryPhase.READY
            assert final.result is not None
            assert final.result.provider_summary[0].entities_upserted == 2


# ---------------------------------------------------------------------
# R2 -- stale HEAD revision
# ---------------------------------------------------------------------
class TestR2FreshHeadRevision:
    def test_start_ingestion_resolves_advanced_head_not_stale_revision(
        self, tmp_path: Path
    ) -> None:
        """(R2 #1, #2, #3, #4) Repository indexed at revision A; a new
        real commit advances it to revision B; a fresh `start_ingestion`
        must resolve and use B, never silently continue using A."""
        repo_dir = _make_git_repo(tmp_path / "repo")
        api = _make_api(_fake_adapter(entity_paths=("a",)))
        api.register_repository("repo1", str(repo_dir))

        handle1 = api.start_ingestion("repo1")
        final1 = _wait_for_terminal(api, handle1.job_id)
        assert final1.phase == RepositoryPhase.READY
        assert final1.result is not None
        revision_a = final1.result.head_revision

        revision_b = _commit_new_file(repo_dir, "b.txt", "more\n")
        assert revision_b != revision_a

        handle2 = api.start_ingestion("repo1")
        final2 = _wait_for_terminal(api, handle2.job_id)
        assert final2.phase == RepositoryPhase.READY
        assert final2.result is not None
        assert final2.result.head_revision == revision_b
        assert final2.result.head_revision != revision_a, (
            "start_ingestion silently reused the stale revision instead of "
            "resolving the repository's current HEAD"
        )

    def test_start_ingestion_with_real_providers_picks_up_new_symbol_after_commit(
        self, tmp_path: Path
    ) -> None:
        """(R2 #1-#4, strongest form) Uses the REAL `GitAdapter` +
        `AstCallsAdapter` -- the same providers `codex.api.__main__`
        wires up -- rather than the fake, to prove a genuinely new
        function added in a new commit is picked up by a second
        `start_ingestion` call: not just that the revision *string*
        changed, but that the graph itself reflects the new content."""
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        repo = Repo.init(repo_dir)
        (repo_dir / "a.py").write_text("def helper():\n    return 1\n")
        repo.index.add(["a.py"])
        author = Actor("Test", "test@example.com")
        repo.index.commit("add a.py", author=author, committer=author)

        registry = CapabilityRegistry()
        evidence_store = InMemoryEvidenceStore()
        registry.register(GitAdapter(), PROFILE)
        registry.register(AstCallsAdapter(), PROFILE)
        api = CodexAPI(registry, evidence_store)
        api.register_repository("repo1", str(repo_dir))

        handle1 = api.start_ingestion("repo1")
        final1 = _wait_for_terminal(api, handle1.job_id)
        assert final1.phase == RepositoryPhase.READY
        assert final1.result is not None
        before = api.lookup_symbols("repo1", "new_function_xyz")
        assert before.nodes == []

        (repo_dir / "a.py").write_text(
            "def helper():\n    return 1\n\ndef new_function_xyz():\n    return helper()\n"
        )
        repo.index.add(["a.py"])
        repo.index.commit("add new_function_xyz", author=author, committer=author)

        handle2 = api.start_ingestion("repo1")
        final2 = _wait_for_terminal(api, handle2.job_id)
        assert final2.phase == RepositoryPhase.READY
        assert final2.result is not None
        assert final2.result.head_revision != final1.result.head_revision

        after = api.lookup_symbols("repo1", "new_function_xyz")
        assert len(after.nodes) == 1
        assert after.nodes[0].qualified_name == "a.py::new_function_xyz"

    def test_start_ingestion_unchanged_head_still_succeeds(self, tmp_path: Path) -> None:
        """(R2 #5) Two `start_ingestion` calls with no intervening
        commit must both succeed cleanly, resolving to the identical
        revision each time -- refreshing HEAD must not introduce an
        error or a spurious "changed" signal when nothing changed."""
        repo_dir = _make_git_repo(tmp_path / "repo")
        api = _make_api(_fake_adapter(entity_paths=("a",)))
        api.register_repository("repo1", str(repo_dir))

        handle1 = api.start_ingestion("repo1")
        final1 = _wait_for_terminal(api, handle1.job_id)
        assert final1.phase == RepositoryPhase.READY
        assert final1.result is not None

        handle2 = api.start_ingestion("repo1")
        final2 = _wait_for_terminal(api, handle2.job_id)
        assert final2.phase == RepositoryPhase.READY
        assert final2.result is not None
        assert final2.result.head_revision == final1.result.head_revision

    def test_start_ingestion_unregistered_repository_still_raises_keyerror(self) -> None:
        """(R2 #4, missing repository) Unchanged, explicitly reconfirmed
        after the fix: an unregistered repository still fails fast with
        `KeyError`, never a phantom stale-revision ingestion."""
        api = _make_api()
        with pytest.raises(KeyError):
            api.start_ingestion("ghost")

    def test_start_ingestion_surfaces_git_resolution_failure_not_stale_reuse(
        self, tmp_path: Path
    ) -> None:
        """(R2 #6) Real git failure condition -- the registered
        repository's working tree is deleted out from under Codex after
        registration. `start_ingestion` must surface a distinct,
        named `GitRevisionResolutionError` rather than silently
        reusing the last-known-good revision or raising an opaque
        error."""
        repo_dir = _make_git_repo(tmp_path / "repo")
        api = _make_api(_fake_adapter(entity_paths=("a",)))
        api.register_repository("repo1", str(repo_dir))

        shutil.rmtree(repo_dir)

        with pytest.raises(GitRevisionResolutionError) as exc_info:
            api.start_ingestion("repo1")
        assert "repo1" in str(exc_info.value)
