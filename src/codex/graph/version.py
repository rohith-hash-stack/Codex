"""Graph versioning model (TAD §19-20, invariants #4-5).

A graph version is immutable once published; an active query locks
onto one version so concurrent ingestion never changes its results
mid-flight (TAD §20, §55).
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class GraphVersion(BaseModel):
    """Identifies the exact evidence composing one graph snapshot."""

    version_id: str
    repository_id: str
    repository_revision: str
    provider_versions: dict[str, str] = Field(default_factory=dict)
    schema_version: str = "1.0"
    policy_version: str = "1.0"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    published: bool = False

    def publish(self) -> GraphVersion:
        """Return a published copy. Published versions are immutable (invariant #4)."""
        return self.model_copy(update={"published": True})
