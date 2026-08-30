"""Repository Manager (TAD §7).

Owns repository registration, cloning, revision detection, and
changed-file detection between revisions. It SHALL NOT interpret user
queries (TAD §7) — that belongs to Query Understanding (DTD-02).
"""

from __future__ import annotations

from pathlib import Path

import git

from codex.repository.models import ChangeSet, RepositoryMetadata


class RepositoryManager:
    """Registers local repository clones and reports their revision history."""

    def __init__(self) -> None:
        self._repositories: dict[str, RepositoryMetadata] = {}

    def register(self, repository_id: str, local_path: str | Path) -> RepositoryMetadata:
        """Register an already-cloned local repository."""
        path = Path(local_path).resolve()
        repo = git.Repo(path)
        metadata = RepositoryMetadata(
            repository_id=repository_id,
            local_path=path,
            remote_url=self._remote_url(repo),
            default_branch=self._default_branch(repo),
            head_revision=repo.head.commit.hexsha,
        )
        self._repositories[repository_id] = metadata
        return metadata

    def clone(
        self,
        repository_id: str,
        url: str,
        dest: str | Path,
        *,
        revision: str | None = None,
    ) -> RepositoryMetadata:
        """Clone a remote repository and register it."""
        dest_path = Path(dest).resolve()
        repo = git.Repo.clone_from(url, dest_path)
        if revision is not None:
            repo.git.checkout(revision)
        metadata = RepositoryMetadata(
            repository_id=repository_id,
            local_path=dest_path,
            remote_url=url,
            default_branch=self._default_branch(repo),
            head_revision=repo.head.commit.hexsha,
        )
        self._repositories[repository_id] = metadata
        return metadata

    def get_metadata(self, repository_id: str) -> RepositoryMetadata:
        return self._repositories[repository_id]

    def get_head_revision(self, repository_id: str) -> str:
        """Refresh and return the current HEAD revision for a registered repository."""
        metadata = self._repositories[repository_id]
        repo = git.Repo(metadata.local_path)
        head = repo.head.commit.hexsha
        self._repositories[repository_id] = metadata.model_copy(update={"head_revision": head})
        return head

    def detect_changed_files(
        self, repository_id: str, from_revision: str, to_revision: str
    ) -> ChangeSet:
        """Diff two revisions to drive incremental graph updates (TAD §72)."""
        metadata = self._repositories[repository_id]
        repo = git.Repo(metadata.local_path)
        diff_index = repo.commit(from_revision).diff(repo.commit(to_revision))

        added: list[str] = []
        modified: list[str] = []
        deleted: list[str] = []
        renamed: list[tuple[str, str]] = []

        for diff in diff_index:
            if diff.new_file:
                assert diff.b_path is not None
                added.append(diff.b_path)
            elif diff.deleted_file:
                assert diff.a_path is not None
                deleted.append(diff.a_path)
            elif diff.renamed_file:
                assert diff.rename_from is not None
                assert diff.rename_to is not None
                renamed.append((diff.rename_from, diff.rename_to))
            else:
                assert diff.b_path is not None
                modified.append(diff.b_path)

        return ChangeSet(
            repository_id=repository_id,
            from_revision=from_revision,
            to_revision=to_revision,
            added=added,
            modified=modified,
            deleted=deleted,
            renamed=renamed,
        )

    @staticmethod
    def _remote_url(repo: git.Repo) -> str | None:
        try:
            return next(iter(repo.remotes)).url
        except (StopIteration, AttributeError):
            return None

    @staticmethod
    def _default_branch(repo: git.Repo) -> str | None:
        try:
            return repo.active_branch.name
        except TypeError:
            return None
