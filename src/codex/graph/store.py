"""Canonical Graph Store interfaces (TAD §12, §53, §62, §75-76).

``GraphReader`` is the read-only surface query components depend on;
``GraphStore`` extends it with mutation, restricted to ingestion/
update pipelines — query processing, verification, and LLM reasoning
are all read-only (TAD §62, invariants #6-8).
"""

from __future__ import annotations

from typing import Protocol

from codex.evidence.model import CanonicalRelationship
from codex.graph.version import GraphVersion
from codex.ontology.entities import BaseEntityType, RepositorySymbol
from codex.ontology.relationships import RelationshipType


class GraphReader(Protocol):
    """Read-only access to one locked graph version."""

    @property
    def version(self) -> GraphVersion: ...

    def get_entity(self, canonical_id: str) -> RepositorySymbol | None: ...

    def get_relationships(
        self,
        *,
        subject: str | None = None,
        predicate: RelationshipType | None = None,
        object_id: str | None = None,
    ) -> list[CanonicalRelationship]: ...

    def neighbors(
        self,
        canonical_id: str,
        *,
        predicate: RelationshipType | None = None,
        direction: str = "out",
    ) -> list[RepositorySymbol]: ...

    def find_entities(
        self,
        *,
        name: str | None = None,
        qualified_name: str | None = None,
        base_type: BaseEntityType | None = None,
    ) -> list[RepositorySymbol]:
        """Deterministic, case-sensitive substring lookup by name/qualified_name
        (D9 directive Part 11/HLRD §33 "candidate generation"; no prior D1-D8
        component ever needed to turn a free-text string into a graph entity,
        so no such lookup existed before D9 — see architecture-conformance-
        audit.md §R.5). No embeddings, no fuzzy matching (HLRD §34)."""
        ...


class GraphStore(GraphReader, Protocol):
    """Mutating access, restricted to ingestion/update pipelines (TAD §62)."""

    def upsert_entity(self, entity: RepositorySymbol) -> None: ...

    def upsert_relationship(self, relationship: CanonicalRelationship) -> None: ...
