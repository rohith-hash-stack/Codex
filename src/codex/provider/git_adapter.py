"""The Git Adapter (HLRD §13; TAD §7, §14, §72; Phase D directive D3).

Independently implemented against Git's documented behavior via
GitPython (BSD-3-Clause, already a pinned dependency — see
``docs/resources.md``). Git is used purely as an external tool/
library: no Git source code or tests are copied or embedded, and no
shell string interpolation is used anywhere (every invocation goes
through GitPython's argument-array API — see ``docs/policy-
external-references.md``).

Two capabilities, matching exactly what HLRD §13 assigns to Git
("commits, revisions, file changes, renames, deletions, introductions,
co-change relationships, historical repository state") and nothing
else — no branch/tag listing, no blame, no arbitrary revision ranges:

- ``Capability.HISTORY`` — the tip commit's diff against its first
  parent (the empty tree for an initial commit). Per TAD §72, Codex's
  historical strategy is changesets/diffs, not full history walks, so
  this adapter reports one changeset at a time rather than replaying
  the whole repository.
- ``Capability.CO_CHANGE`` — file pairs that changed together across
  a bounded window of recent commits (first-parent chain from
  ``head_revision``), evidenced as ``RelationshipType.CO_CHANGED_WITH``.

File lifecycle (added/modified/deleted/renamed) is a *unary* fact
about one entity, not a relationship — it doesn't fit ``Evidence``'s
subject/predicate/object shape. Rather than inventing a synthetic
self-relationship or a ``CONTAINS`` edge (which would take on
directory-tree modeling that isn't Git's stated HLRD responsibility),
``HISTORY`` emits ``RepositorySymbol`` entities only, with provenance
carried by the ``EvidenceCohort`` rather than a per-entity ``Evidence``
record. This mirrors an already-flagged gap for CodeQL's single-entity
findings (``docs/research/provider-formats.md``) — the same recurring
pattern, not a new open question.
"""

from __future__ import annotations

import shutil
import time
from collections import defaultdict
from collections.abc import Collection, Iterator
from datetime import datetime
from itertools import combinations
from typing import Any, Final

import git

from codex.evidence.model import CoverageStatus, Evidence, EvidenceCohort
from codex.ontology.entities import (
    BaseEntityType,
    LifecycleStatus,
    RepositorySymbol,
    build_canonical_id,
)
from codex.ontology.relationships import RelationshipType
from codex.provider.capability import Capability
from codex.provider.contract import (
    EligibilityStatus,
    ExtractionResult,
    NormalizedEvidence,
    ProviderEligibility,
    ProviderExtractionError,
    ProviderFailureReason,
    ProviderHealthStatus,
    ValidationResult,
)
from codex.repository.models import RepositoryMetadata

DEFAULT_CO_CHANGE_WINDOW: Final = 20
"""How many recent commits (first-parent chain, inclusive of HEAD) CO_CHANGE examines."""

DEFAULT_CO_CHANGE_SATURATION: Final = 3
"""Co-occurrence count at which CO_CHANGED_WITH confidence saturates to 1.0.

A calibration point (not derived from HLRD/TAD, which specify no
formula), analogous to ADR-018's freshness half-life — documented and
swappable via the constructor, not a claimed-final algorithm.
"""

DEFAULT_COMMAND_TIMEOUT_SECONDS: Final = 30.0


class _GitTimeoutError(Exception):
    """Internal only: a bounded operation exceeded its timeout."""


def _check_timeout(started_at: float, timeout: float, *, now: float | None = None) -> None:
    """Raise ``_GitTimeoutError`` if more than ``timeout`` seconds have elapsed."""
    elapsed = (now if now is not None else time.monotonic()) - started_at
    if elapsed > timeout:
        raise _GitTimeoutError(f"exceeded {timeout}s (elapsed {elapsed:.2f}s)")


def _tip_diff(commit: git.Commit) -> git.DiffIndex:
    """Diff ``commit`` against its first parent, or the empty tree if it has none."""
    if commit.parents:
        return commit.parents[0].diff(commit)
    return commit.diff(git.NULL_TREE)


def _changed_files(
    diff_index: git.DiffIndex,
) -> tuple[list[str], list[str], list[str], list[tuple[str, str]]]:
    """Classify a GitPython ``DiffIndex`` into added/modified/deleted/renamed paths.

    Same classification pattern already proven in
    ``codex.repository.manager.RepositoryManager.detect_changed_files``.
    """
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

    return added, modified, deleted, renamed


def _walk_commits(head_commit: git.Commit, window: int) -> Iterator[git.Commit]:
    """First-parent chain from ``head_commit``, at most ``window`` commits."""
    commit: git.Commit | None = head_commit
    seen = 0
    while commit is not None and seen < window:
        yield commit
        seen += 1
        commit = commit.parents[0] if commit.parents else None


def _co_change_counts(
    head_commit: git.Commit, *, window: int, timeout: float, started_at: float
) -> dict[tuple[str, str], int]:
    """Tally file-pair co-occurrence across the commit window. Raises ``_GitTimeoutError``."""
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for commit in _walk_commits(head_commit, window):
        _check_timeout(started_at, timeout)
        added, modified, deleted, renamed = _changed_files(_tip_diff(commit))
        changed = sorted({*added, *modified, *deleted, *(new for _old, new in renamed)})
        for a, b in combinations(changed, 2):
            counts[(a, b)] += 1
    return dict(counts)


def _file_symbol(
    repository_id: str, revision: str, path: str, status: LifecycleStatus
) -> RepositorySymbol:
    canonical_id = build_canonical_id(
        repository_id=repository_id,
        repository_revision=revision,
        qualified_name=path,
        base_type=BaseEntityType.FILE,
    )
    return RepositorySymbol(
        canonical_id=canonical_id,
        repository_id=repository_id,
        repository_revision=revision,
        name=path.rsplit("/", 1)[-1],
        qualified_name=path,
        base_type=BaseEntityType.FILE,
        lifecycle_status=status,
        provider_ids={"git": path},
    )


class GitAdapter:
    """``ProviderAdapter`` for Git repository history (HLRD §13; directive D3)."""

    def __init__(
        self,
        *,
        co_change_window: int = DEFAULT_CO_CHANGE_WINDOW,
        co_change_saturation: int = DEFAULT_CO_CHANGE_SATURATION,
        command_timeout: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        self._co_change_window = co_change_window
        self._co_change_saturation = co_change_saturation
        self._command_timeout = command_timeout
        self._freshness: datetime | None = None
        self._version_cache: str | None = None

    @property
    def provider_name(self) -> str:
        return "git"

    @property
    def provider_version(self) -> str:
        if self._version_cache is None:
            try:
                self._version_cache = git.Git().version()
            except (git.GitCommandNotFound, git.GitCommandError):
                self._version_cache = "unknown"
        return self._version_cache

    @property
    def supported_capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.HISTORY, Capability.CO_CHANGE})

    @property
    def health_status(self) -> ProviderHealthStatus:
        return (
            ProviderHealthStatus.HEALTHY
            if shutil.which("git") is not None
            else ProviderHealthStatus.UNHEALTHY
        )

    def availability(self, capability: Capability, repository: RepositoryMetadata) -> float:
        if self.health_status is not ProviderHealthStatus.HEALTHY:
            return 0.0
        return 1.0 if self.check_eligibility(repository).eligible else 0.0

    @property
    def freshness(self) -> datetime | None:
        return self._freshness

    def validate(self) -> ValidationResult:
        if shutil.which("git") is None:
            return ValidationResult(ok=False, problems=["git executable not found on PATH"])
        return ValidationResult(ok=True)

    def check_eligibility(self, repository: RepositoryMetadata) -> ProviderEligibility:
        try:
            git.Repo(repository.local_path)
        except (git.InvalidGitRepositoryError, git.NoSuchPathError):
            return ProviderEligibility(
                status=EligibilityStatus.INELIGIBLE_REPOSITORY,
                reason=f"{repository.local_path} is not a git repository",
            )
        return ProviderEligibility(status=EligibilityStatus.ELIGIBLE)

    def extract(
        self, repository: RepositoryMetadata, capabilities: Collection[Capability]
    ) -> ExtractionResult:
        requested = frozenset(capabilities) & self.supported_capabilities

        if shutil.which("git") is None:
            raise ProviderExtractionError(
                self.provider_name, ProviderFailureReason.UNAVAILABLE, "git executable not found"
            )

        try:
            repo = git.Repo(repository.local_path)
        except (git.InvalidGitRepositoryError, git.NoSuchPathError) as exc:
            raise ProviderExtractionError(
                self.provider_name,
                ProviderFailureReason.UNAVAILABLE,
                f"not a git repository: {exc}",
            ) from exc

        try:
            head_commit = repo.commit(repository.head_revision)
        except (git.BadName, ValueError, git.GitCommandError) as exc:
            raise ProviderExtractionError(
                self.provider_name,
                ProviderFailureReason.UNAVAILABLE,
                f"invalid revision {repository.head_revision!r}: {exc}",
            ) from exc

        successful: list[str] = []
        failed: list[str] = []
        payload: dict[str, Any] = {"repository_id": repository.repository_id}
        started_at = time.monotonic()

        if Capability.HISTORY in requested:
            try:
                added, modified, deleted, renamed = _changed_files(_tip_diff(head_commit))
                payload["history"] = {
                    "added": added,
                    "modified": modified,
                    "deleted": deleted,
                    "renamed": renamed,
                }
                successful.append(Capability.HISTORY.value)
            except git.GitError:
                failed.append(Capability.HISTORY.value)

        if Capability.CO_CHANGE in requested:
            try:
                payload["co_change"] = _co_change_counts(
                    head_commit,
                    window=self._co_change_window,
                    timeout=self._command_timeout,
                    started_at=started_at,
                )
                successful.append(Capability.CO_CHANGE.value)
            except (_GitTimeoutError, git.GitError):
                failed.append(Capability.CO_CHANGE.value)

        if not successful and not failed:
            coverage = CoverageStatus.NONE
        elif failed:
            coverage = CoverageStatus.PARTIAL
        else:
            coverage = CoverageStatus.FULL

        cohort = EvidenceCohort(
            provider=self.provider_name,
            provider_version=self.provider_version,
            snapshot_id=repository.head_revision,
            source_revision=repository.head_revision,
            successful_capabilities=successful,
            failed_capabilities=failed,
            partial_capabilities=[],
            coverage_status=coverage,
        )
        self._freshness = cohort.observed_at

        return ExtractionResult(cohort=cohort, raw_reference=None, raw_payload=payload)

    def normalize(self, result: ExtractionResult) -> NormalizedEvidence:
        payload = result.raw_payload
        repository_id: str = payload["repository_id"]
        revision = result.cohort.source_revision

        entities: list[RepositorySymbol] = []
        evidence: list[Evidence] = []

        history = payload.get("history")
        if history is not None:
            for path in [*history["added"], *history["modified"]]:
                entities.append(
                    _file_symbol(repository_id, revision, path, LifecycleStatus.ACTIVE)
                )
            for path in history["deleted"]:
                entities.append(
                    _file_symbol(repository_id, revision, path, LifecycleStatus.DELETED)
                )
            for _old, new in history["renamed"]:
                entities.append(
                    _file_symbol(repository_id, revision, new, LifecycleStatus.RENAMED)
                )

        co_change: dict[tuple[str, str], int] | None = payload.get("co_change")
        if co_change is not None:
            for i, ((path_a, path_b), count) in enumerate(sorted(co_change.items())):
                subject = build_canonical_id(
                    repository_id=repository_id,
                    repository_revision=revision,
                    qualified_name=path_a,
                    base_type=BaseEntityType.FILE,
                )
                obj = build_canonical_id(
                    repository_id=repository_id,
                    repository_revision=revision,
                    qualified_name=path_b,
                    base_type=BaseEntityType.FILE,
                )
                confidence = min(1.0, count / self._co_change_saturation)
                evidence.append(
                    Evidence(
                        evidence_id=f"git:co_change:{revision}:{i}",
                        provider=self.provider_name,
                        provider_version=result.cohort.provider_version,
                        snapshot_id=result.cohort.snapshot_id,
                        source_revision=revision,
                        subject=subject,
                        predicate=RelationshipType.CO_CHANGED_WITH,
                        object=obj,
                        confidence=confidence,
                        freshness=result.cohort.observed_at,
                    )
                )

        return NormalizedEvidence(entities=entities, evidence=evidence, cohort=result.cohort)
