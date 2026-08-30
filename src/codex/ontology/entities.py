"""Canonical entity ontology (HLRD §16-18, TAD §12-13).

Codex separates a closed set of *base entity types* from an open,
extensible set of *roles*: a base type is the entity's fixed kind
(e.g. FUNCTION); roles layer semantic meaning on top (e.g. API,
ENTRY_POINT) without exploding the ontology into one base type per
concept (TAD §13).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class BaseEntityType(StrEnum):
    """Fixed base entity kinds (HLRD §16)."""

    REPOSITORY = "REPOSITORY"
    DIRECTORY = "DIRECTORY"
    FILE = "FILE"
    MODULE = "MODULE"
    NAMESPACE = "NAMESPACE"
    CLASS = "CLASS"
    INTERFACE = "INTERFACE"
    FUNCTION = "FUNCTION"
    METHOD = "METHOD"
    VARIABLE = "VARIABLE"
    TEST = "TEST"
    CONFIGURATION = "CONFIGURATION"
    EXTERNAL_LIBRARY = "EXTERNAL_LIBRARY"
    API = "API"
    DATABASE = "DATABASE"
    RUNTIME_COMPONENT = "RUNTIME_COMPONENT"


class CommonRole:
    """Non-exhaustive role constants (HLRD §16-17, TAD §13).

    Roles are plain strings rather than a closed enum: the ontology
    must remain extensible (HLRD §16), and providers may contribute
    roles beyond this common set.
    """

    CONTROLLER = "CONTROLLER"
    SERVICE = "SERVICE"
    API_HANDLER = "API_HANDLER"
    ENTRY_POINT = "ENTRY_POINT"
    HTTP_HANDLER = "HTTP_HANDLER"
    TEST_TARGET = "TEST_TARGET"


class LifecycleStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DELETED = "DELETED"
    RENAMED = "RENAMED"


class SourceLocation(BaseModel):
    """A location within a specific repository revision."""

    file_path: str
    start_line: int
    end_line: int
    start_column: int | None = None
    end_column: int | None = None


def build_canonical_id(
    *,
    repository_id: str,
    repository_revision: str,
    qualified_name: str,
    base_type: BaseEntityType,
    language: str | None = None,
) -> str:
    """Deterministically derive a canonical entity identity (HLRD §18).

    Multiple providers may reference the same canonical entity; this
    hash gives entity resolution a stable join key that does not
    depend on any single provider's symbol ID.
    """
    key = "|".join(
        [repository_id, repository_revision, base_type.value, language or "", qualified_name]
    )
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return f"codex:{digest}"


class RepositorySymbol(BaseModel):
    """A canonical graph node (TAD §12)."""

    canonical_id: str
    repository_id: str
    repository_revision: str
    name: str
    qualified_name: str
    base_type: BaseEntityType
    language: str | None = None
    roles: list[str] = Field(default_factory=list)
    source_location: SourceLocation | None = None
    lifecycle_status: LifecycleStatus = LifecycleStatus.ACTIVE
    provider_ids: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def has_role(self, role: str) -> bool:
        return role in self.roles
