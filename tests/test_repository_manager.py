from pathlib import Path

import git
import pytest

from codex.repository.manager import RepositoryManager


@pytest.fixture
def local_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    repo = git.Repo.init(repo_path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Codex Test")
        config.set_value("user", "email", "codex-test@example.com")

    file_a = repo_path / "a.py"
    file_a.write_text("print('a')\n")
    repo.index.add([str(file_a)])
    first_commit = repo.index.commit("initial commit")

    file_b = repo_path / "b.py"
    file_b.write_text("print('b')\n")
    file_a.write_text("print('a updated')\n")
    repo.index.add([str(file_a), str(file_b)])
    second_commit = repo.index.commit("add b, update a")

    return repo_path, first_commit.hexsha, second_commit.hexsha


def test_register_reports_head_revision(local_repo: tuple[Path, str, str]) -> None:
    repo_path, _first, second = local_repo
    manager = RepositoryManager()
    metadata = manager.register("codex-test", repo_path)

    assert metadata.repository_id == "codex-test"
    assert metadata.head_revision == second
    assert metadata.local_path == repo_path.resolve()


def test_get_head_revision_reflects_new_commits(local_repo: tuple[Path, str, str]) -> None:
    repo_path, _first, second = local_repo
    manager = RepositoryManager()
    manager.register("codex-test", repo_path)
    assert manager.get_head_revision("codex-test") == second

    repo = git.Repo(repo_path)
    new_file = repo_path / "c.py"
    new_file.write_text("print('c')\n")
    repo.index.add([str(new_file)])
    third_commit = repo.index.commit("add c")

    assert manager.get_head_revision("codex-test") == third_commit.hexsha


def test_detect_changed_files_between_revisions(local_repo: tuple[Path, str, str]) -> None:
    repo_path, first, second = local_repo
    manager = RepositoryManager()
    manager.register("codex-test", repo_path)

    changeset = manager.detect_changed_files("codex-test", first, second)

    assert changeset.added == ["b.py"]
    assert changeset.modified == ["a.py"]
    assert changeset.deleted == []
    assert changeset.renamed == []
    assert set(changeset.affected_files) == {"a.py", "b.py"}
