"""Repository Manager data models (TAD §7)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field


class RepositoryMetadata(BaseModel):
    """A registered repository (TAD §7)."""

    repository_id: str
    local_path: Path
    remote_url: str | None = None
    default_branch: str | None = None
    head_revision: str
    registered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ChangeSet(BaseModel):
    """Files changed between two repository revisions (HLRD §23, TAD §72)."""

    repository_id: str
    from_revision: str
    to_revision: str
    added: list[str] = Field(default_factory=list)
    modified: list[str] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list)
    renamed: list[tuple[str, str]] = Field(default_factory=list)

    @property
    def affected_files(self) -> list[str]:
        """All files touched by this change, for driving incremental re-analysis."""
        renamed_files = [new for _old, new in self.renamed]
        return [*self.added, *self.modified, *self.deleted, *renamed_files]
