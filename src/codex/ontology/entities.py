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

from pydantic import BaseModel, Field, model_validator


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
    """A location within a specific repository revision (TAD §12).

    Canonical coordinate convention, closed 2026-08-31 (previously an
    undocumented ambiguity — flagged in the D7 research pass, resolved here
    as a specification clarification rather than a new ADR: no HLRD/TAD
    text ever mandated a convention, so nothing was contradicted, only
    left unstated. `SCIPAdapter` — the only current populator of this
    field — already emits values matching this convention by construction,
    so closing the gap requires no behavior change there.):

    - **0-based, half-open.** ``start_line``/``start_column`` are the
      first included line/character; ``end_line``/``end_column`` are the
      first line/character *not* included (matches SCIP's own documented
      ``[start, end)`` range semantics, and the LSP/tree-sitter convention
      most providers and editor tooling already use).
    - ``start_column``/``end_column`` are optional and independent of
      ``start_line``/``end_line``: a location may be line-only (both
      columns ``None``) when a provider gives no character-level
      precision. They must be supplied together, never one without the
      other — a range's start is meaningless without a matching end at
      the same precision.
    - There is no "whole file" or "unknown location" sentinel *inside*
      this type: a ``RepositorySymbol`` with no location information sets
      ``source_location=None`` on itself instead (already how
      `SCIPAdapter` represents `EXTERNAL_LIBRARY` entities, which have no
      in-repository position). A populated ``SourceLocation`` always
      names at least a line range.
    - ``file_path`` is repository-root-relative, ``/``-separated, with no
      leading ``./`` or ``/`` — the same convention `build_canonical_id`'s
      ``qualified_name`` already uses for `FILE` entities, so a location's
      ``file_path`` can be compared/joined against a FILE's identity
      directly without renormalizing.
    - ``repository_id``/revision are deliberately **not** fields here:
      this type is always embedded inside an entity (`RepositorySymbol`)
      that already carries `repository_id`/`repository_revision` — adding
      duplicate fields here would invent state the architecture doesn't
      require and could drift out of sync with the parent entity.

    **Provider normalization (not yet a behavior change, since only SCIP
    currently populates this field):** SARIF's `region` (OASIS SARIF 2.1.0
    schema, confirmed directly against the authoritative schema text) is
    **1-based**, with ``endLine`` *inclusive* of the last line containing
    a character and ``endColumn`` *exclusive* (documented as "the column
    of the character following the end of the region") — structurally the
    same half-open-at-the-end model as SCIP, offset by exactly one on all
    four numbers. A future capability that surfaces CodeQL/SARIF location
    data as a ``SourceLocation`` (today's `CodeQLAdapter` only uses
    `region.startLine` internally, for codeFlow endpoint matching — it
    never leaks into this type) must subtract 1 from every SARIF
    line/column value it converts, never pass them through as-is. Git
    currently attaches no `SourceLocation` at all (`HISTORY` is a
    whole-file lifecycle fact, TAD §12; no per-line data is produced).
    """

    file_path: str
    start_line: int = Field(ge=0)
    end_line: int = Field(ge=0)
    start_column: int | None = Field(default=None, ge=0)
    end_column: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_range(self) -> SourceLocation:
        if self.end_line < self.start_line:
            raise ValueError(
                f"end_line ({self.end_line}) must be >= start_line ({self.start_line})"
            )
        if (self.start_column is None) != (self.end_column is None):
            raise ValueError("start_column and end_column must both be set or both be None")
        if (
            self.start_column is not None
            and self.end_column is not None
            and self.end_line == self.start_line
            and self.end_column < self.start_column
        ):
            raise ValueError(
                f"end_column ({self.end_column}) must be >= start_column "
                f"({self.start_column}) when start_line == end_line"
            )
        return self


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
