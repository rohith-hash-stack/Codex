"""Behavioral tests for GitAdapter (HLRD §13; directive D3 §12).

Every test builds its own deterministic temporary git repository via
GitPython — none depend on the Codex repository's own git history.
"""

from __future__ import annotations

from pathlib import Path

import git
import pytest

from codex.evidence.model import CoverageStatus
from codex.ontology.entities import LifecycleStatus
from codex.ontology.relationships import RelationshipType
from codex.provider.capability import Capability
from codex.provider.contract import ProviderExtractionError, ProviderHealthStatus
from codex.provider.git_adapter import (
    DEFAULT_CO_CHANGE_SATURATION,
    GitAdapter,
    _check_timeout,
)
from codex.repository.models import RepositoryMetadata

# --- fixtures ---


@pytest.fixture
def repo_path(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    repo = git.Repo.init(path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Codex Test")
        config.set_value("user", "email", "codex-test@example.com")
    return path


def _write(repo_path: Path, relative: str, content: str) -> None:
    file_path = repo_path / relative
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)


def _commit_all(repo_path: Path, message: str) -> str:
    repo = git.Repo(repo_path)
    repo.git.add(A=True)
    return repo.index.commit(message).hexsha


def make_repo(repo_path: Path, revision: str, repository_id: str = "repo1") -> RepositoryMetadata:
    return RepositoryMetadata(
        repository_id=repository_id, local_path=repo_path, head_revision=revision
    )


# --- identity / health / eligibility ---


def test_provider_identity_and_capabilities() -> None:
    adapter = GitAdapter()
    assert adapter.provider_name == "git"
    assert adapter.supported_capabilities == frozenset(
        {Capability.HISTORY, Capability.CO_CHANGE}
    )


def test_provider_version_is_a_real_git_version_string() -> None:
    adapter = GitAdapter()
    assert adapter.provider_version != "unknown"
    assert adapter.provider_version != ""


def test_health_and_validate_when_git_available() -> None:
    adapter = GitAdapter()
    assert adapter.health_status is ProviderHealthStatus.HEALTHY
    assert adapter.validate().ok is True


def test_health_and_validate_when_git_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("codex.provider.git_adapter.shutil.which", lambda _name: None)
    adapter = GitAdapter()
    assert adapter.health_status is ProviderHealthStatus.UNHEALTHY
    result = adapter.validate()
    assert result.ok is False
    assert result.problems


def test_check_eligibility_for_valid_git_repo(repo_path: Path) -> None:
    _write(repo_path, "a.py", "1")
    revision = _commit_all(repo_path, "initial")
    adapter = GitAdapter()
    eligibility = adapter.check_eligibility(make_repo(repo_path, revision))
    assert eligibility.eligible


def test_check_eligibility_for_non_git_directory(tmp_path: Path) -> None:
    plain_dir = tmp_path / "not-a-repo"
    plain_dir.mkdir()
    adapter = GitAdapter()
    eligibility = adapter.check_eligibility(make_repo(plain_dir, "irrelevant"))
    assert not eligibility.eligible


def test_availability_is_one_when_healthy_and_eligible(repo_path: Path) -> None:
    _write(repo_path, "a.py", "1")
    revision = _commit_all(repo_path, "initial")
    adapter = GitAdapter()
    assert adapter.availability(Capability.HISTORY, make_repo(repo_path, revision)) == 1.0


def test_availability_is_zero_for_non_git_directory(tmp_path: Path) -> None:
    plain_dir = tmp_path / "not-a-repo"
    plain_dir.mkdir()
    adapter = GitAdapter()
    assert adapter.availability(Capability.HISTORY, make_repo(plain_dir, "x")) == 0.0


def test_availability_is_zero_when_git_is_unhealthy(
    repo_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(repo_path, "a.py", "1")
    revision = _commit_all(repo_path, "initial")
    monkeypatch.setattr("codex.provider.git_adapter.shutil.which", lambda _name: None)
    adapter = GitAdapter()
    assert adapter.availability(Capability.HISTORY, make_repo(repo_path, revision)) == 0.0


def test_provider_version_falls_back_to_unknown_when_git_version_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BrokenGit:
        def version(self) -> str:
            raise git.GitCommandNotFound("git", "not found")

    monkeypatch.setattr("codex.provider.git_adapter.git.Git", _BrokenGit)
    adapter = GitAdapter()
    assert adapter.provider_version == "unknown"


# --- extract() failure semantics: total failure, not "found nothing" ---


def test_extract_raises_for_non_git_directory(tmp_path: Path) -> None:
    plain_dir = tmp_path / "not-a-repo"
    plain_dir.mkdir()
    adapter = GitAdapter()
    with pytest.raises(ProviderExtractionError) as exc_info:
        adapter.extract(make_repo(plain_dir, "irrelevant"), [Capability.HISTORY])
    assert "git repository" in str(exc_info.value)


def test_extract_raises_for_invalid_revision(repo_path: Path) -> None:
    _write(repo_path, "a.py", "1")
    _commit_all(repo_path, "initial")
    adapter = GitAdapter()
    with pytest.raises(ProviderExtractionError, match="invalid revision"):
        adapter.extract(make_repo(repo_path, "not-a-real-revision"), [Capability.HISTORY])


def test_extract_rejects_shell_metacharacters_as_an_invalid_revision_not_a_shell_command(
    repo_path: Path,
) -> None:
    """GitPython resolves revisions through its own ref-parsing, never a
    shell -- a hostile-looking revision string is just an invalid ref."""
    _write(repo_path, "a.py", "1")
    _commit_all(repo_path, "initial")
    adapter = GitAdapter()
    with pytest.raises(ProviderExtractionError, match="invalid revision"):
        adapter.extract(make_repo(repo_path, "; rm -rf / #"), [Capability.HISTORY])


def test_extract_raises_when_git_executable_missing(
    repo_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(repo_path, "a.py", "1")
    revision = _commit_all(repo_path, "initial")
    monkeypatch.setattr("codex.provider.git_adapter.shutil.which", lambda _name: None)
    adapter = GitAdapter()
    with pytest.raises(ProviderExtractionError) as exc_info:
        adapter.extract(make_repo(repo_path, revision), [Capability.HISTORY])
    assert "git executable" in str(exc_info.value)


def test_extract_with_no_requested_capabilities_matched_is_a_clean_empty_run(
    repo_path: Path,
) -> None:
    _write(repo_path, "a.py", "1")
    revision = _commit_all(repo_path, "initial")
    adapter = GitAdapter()
    result = adapter.extract(make_repo(repo_path, revision), [])
    assert result.cohort.successful_capabilities == []
    assert result.cohort.failed_capabilities == []
    assert result.cohort.coverage_status == CoverageStatus.NONE


# --- HISTORY: added / modified / deleted / renamed ---


def test_history_reports_added_files_on_initial_commit(repo_path: Path) -> None:
    _write(repo_path, "a.py", "1")
    _write(repo_path, "b.py", "2")
    revision = _commit_all(repo_path, "initial")

    adapter = GitAdapter()
    result = adapter.extract(make_repo(repo_path, revision), [Capability.HISTORY])
    normalized = adapter.normalize(result)

    assert result.cohort.successful_capabilities == [Capability.HISTORY.value]
    active = {
        e.qualified_name
        for e in normalized.entities
        if e.lifecycle_status == LifecycleStatus.ACTIVE
    }
    assert active == {"a.py", "b.py"}


def test_history_reports_modified_file(repo_path: Path) -> None:
    _write(repo_path, "a.py", "1")
    _commit_all(repo_path, "initial")
    _write(repo_path, "a.py", "1-changed")
    revision = _commit_all(repo_path, "modify a")

    adapter = GitAdapter()
    result = adapter.extract(make_repo(repo_path, revision), [Capability.HISTORY])
    normalized = adapter.normalize(result)

    assert len(normalized.entities) == 1
    assert normalized.entities[0].qualified_name == "a.py"
    assert normalized.entities[0].lifecycle_status == LifecycleStatus.ACTIVE


def test_history_reports_deleted_file(repo_path: Path) -> None:
    _write(repo_path, "a.py", "1")
    _write(repo_path, "b.py", "2")
    _commit_all(repo_path, "initial")
    (repo_path / "b.py").unlink()
    revision = _commit_all(repo_path, "delete b")

    adapter = GitAdapter()
    result = adapter.extract(make_repo(repo_path, revision), [Capability.HISTORY])
    normalized = adapter.normalize(result)

    assert len(normalized.entities) == 1
    assert normalized.entities[0].qualified_name == "b.py"
    assert normalized.entities[0].lifecycle_status == LifecycleStatus.DELETED


def test_history_reports_renamed_file(repo_path: Path) -> None:
    content = "content long enough for git's rename similarity detection to trigger\n" * 3
    _write(repo_path, "a.py", content)
    _commit_all(repo_path, "initial")
    repo = git.Repo(repo_path)
    repo.git.mv("a.py", "b.py")
    revision = repo.index.commit("rename a to b").hexsha

    adapter = GitAdapter()
    result = adapter.extract(make_repo(repo_path, revision), [Capability.HISTORY])
    normalized = adapter.normalize(result)

    assert len(normalized.entities) == 1
    assert normalized.entities[0].qualified_name == "b.py"
    assert normalized.entities[0].lifecycle_status == LifecycleStatus.RENAMED


def test_history_empty_successful_result_on_empty_commit(repo_path: Path) -> None:
    """An empty commit is a successful HISTORY extraction with zero
    results -- distinct from a failure (directive Sec9)."""
    _write(repo_path, "a.py", "1")
    _commit_all(repo_path, "initial")
    repo = git.Repo(repo_path)
    repo.git.commit(m="empty", allow_empty=True)
    revision = repo.head.commit.hexsha

    adapter = GitAdapter()
    result = adapter.extract(make_repo(repo_path, revision), [Capability.HISTORY])
    normalized = adapter.normalize(result)

    assert result.cohort.successful_capabilities == [Capability.HISTORY.value]
    assert result.cohort.failed_capabilities == []
    assert normalized.entities == []


def test_history_uses_the_given_revision_not_always_the_checked_out_head(repo_path: Path) -> None:
    """Branch-awareness: extract() must honor repository.head_revision,
    not whatever happens to be checked out on disk."""
    _write(repo_path, "a.py", "1")
    first_revision = _commit_all(repo_path, "first")

    repo = git.Repo(repo_path)
    repo.create_head("feature")
    repo.heads.feature.checkout()
    _write(repo_path, "feature-file.py", "x")
    feature_revision = _commit_all(repo_path, "on feature branch")

    repo.heads.master.checkout() if "master" in repo.heads else repo.heads.main.checkout()

    adapter = GitAdapter()
    # Even though "feature" isn't checked out, passing its revision explicitly
    # must still extract that revision's changes, not the checked-out branch's.
    result = adapter.extract(make_repo(repo_path, feature_revision), [Capability.HISTORY])
    normalized = adapter.normalize(result)
    assert {e.qualified_name for e in normalized.entities} == {"feature-file.py"}
    assert result.cohort.source_revision == feature_revision
    assert first_revision != feature_revision


# --- HISTORY / CO_CHANGE partial failure isolation ---


def test_capability_level_failure_does_not_abort_other_capabilities(
    repo_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(repo_path, "a.py", "1")
    revision = _commit_all(repo_path, "initial")

    def _boom(_commit: object) -> None:
        raise git.GitError("simulated malformed output")

    monkeypatch.setattr("codex.provider.git_adapter._tip_diff", _boom)

    adapter = GitAdapter()
    result = adapter.extract(
        make_repo(repo_path, revision), [Capability.HISTORY, Capability.CO_CHANGE]
    )
    # HISTORY uses _tip_diff directly -> fails. CO_CHANGE also uses _tip_diff
    # internally (via _co_change_counts) -> also fails here, but neither
    # raises ProviderExtractionError; both land in failed_capabilities.
    assert set(result.cohort.failed_capabilities) == {
        Capability.HISTORY.value,
        Capability.CO_CHANGE.value,
    }
    assert result.cohort.coverage_status == CoverageStatus.PARTIAL


# --- CO_CHANGE ---


def test_co_change_detects_files_changed_together(repo_path: Path) -> None:
    _write(repo_path, "a.py", "1")
    _write(repo_path, "b.py", "2")
    _commit_all(repo_path, "initial")
    _write(repo_path, "a.py", "1-changed")
    _write(repo_path, "b.py", "2-changed")
    revision = _commit_all(repo_path, "change both together")

    adapter = GitAdapter()
    result = adapter.extract(make_repo(repo_path, revision), [Capability.CO_CHANGE])
    normalized = adapter.normalize(result)

    assert result.cohort.successful_capabilities == [Capability.CO_CHANGE.value]
    assert len(normalized.evidence) == 1
    ev = normalized.evidence[0]
    assert ev.predicate is RelationshipType.CO_CHANGED_WITH
    assert ev.source_revision == revision


def test_co_change_confidence_saturates_with_repeated_co_occurrence(repo_path: Path) -> None:
    _write(repo_path, "a.py", "0")
    _write(repo_path, "b.py", "0")
    _commit_all(repo_path, "initial")
    revision = ""
    for i in range(DEFAULT_CO_CHANGE_SATURATION + 2):
        _write(repo_path, "a.py", str(i))
        _write(repo_path, "b.py", str(i))
        revision = _commit_all(repo_path, f"change both #{i}")

    adapter = GitAdapter()
    result = adapter.extract(make_repo(repo_path, revision), [Capability.CO_CHANGE])
    normalized = adapter.normalize(result)

    assert len(normalized.evidence) == 1
    assert normalized.evidence[0].confidence == pytest.approx(1.0)


def test_co_change_empty_successful_result_when_no_pairs(repo_path: Path) -> None:
    """Each commit touches only one file -- zero co-change pairs is a
    success with empty evidence, not a failure."""
    _write(repo_path, "a.py", "1")
    _commit_all(repo_path, "commit 1")
    _write(repo_path, "b.py", "1")
    revision = _commit_all(repo_path, "commit 2")

    adapter = GitAdapter()
    result = adapter.extract(make_repo(repo_path, revision), [Capability.CO_CHANGE])
    normalized = adapter.normalize(result)

    assert result.cohort.successful_capabilities == [Capability.CO_CHANGE.value]
    assert result.cohort.failed_capabilities == []
    assert normalized.evidence == []


def test_co_change_respects_the_configured_window(repo_path: Path) -> None:
    _write(repo_path, "old_a.py", "1")
    _write(repo_path, "old_b.py", "1")
    _commit_all(repo_path, "old co-change pair, outside the window")

    for i in range(5):
        _write(repo_path, f"filler_{i}.py", "x")
        revision = _commit_all(repo_path, f"filler commit {i}")

    adapter = GitAdapter(co_change_window=2)
    result = adapter.extract(make_repo(repo_path, revision), [Capability.CO_CHANGE])
    normalized = adapter.normalize(result)

    pairs = {(ev.subject, ev.object) for ev in normalized.evidence}
    # old_a.py/old_b.py's co-change is outside a 2-commit window from HEAD.
    assert len(pairs) == 0


# --- timeout ---


def test_check_timeout_helper_raises_past_deadline() -> None:
    from codex.provider.git_adapter import _GitTimeoutError

    with pytest.raises(_GitTimeoutError):
        _check_timeout(started_at=0.0, timeout=5.0, now=10.0)


def test_check_timeout_helper_does_not_raise_within_deadline() -> None:
    _check_timeout(started_at=0.0, timeout=5.0, now=1.0)  # must not raise


def test_co_change_timeout_lands_in_failed_capabilities_not_a_raised_error(
    repo_path: Path,
) -> None:
    _write(repo_path, "a.py", "1")
    _write(repo_path, "b.py", "1")
    revision = _commit_all(repo_path, "initial")

    adapter = GitAdapter(command_timeout=-1.0)  # already "expired" before the first check
    result = adapter.extract(make_repo(repo_path, revision), [Capability.CO_CHANGE])

    assert result.cohort.failed_capabilities == [Capability.CO_CHANGE.value]
    assert result.cohort.successful_capabilities == []


# --- freshness ---


def test_freshness_updates_after_extraction(repo_path: Path) -> None:
    _write(repo_path, "a.py", "1")
    revision = _commit_all(repo_path, "initial")
    adapter = GitAdapter()
    assert adapter.freshness is None
    adapter.extract(make_repo(repo_path, revision), [Capability.HISTORY])
    assert adapter.freshness is not None
