"""In-memory NetworkX-backed canonical Graph Store (Phase 1 default).

Graph storage technology is deferred to ADR-001 (TAD §77); NetworkX is
one of the TAD's own reference candidates (TAD §48, §53) and gives a
working default behind the ``GraphStore`` interface so the rest of
Codex can be built without waiting on that decision.
"""

from __future__ import annotations

import networkx as nx

from codex.evidence.model import CanonicalRelationship
from codex.graph.version import GraphVersion
from codex.ontology.entities import BaseEntityType, RepositorySymbol
from codex.ontology.relationships import RelationshipType


class InMemoryGraphStore:
    """``GraphStore`` implementation backed by ``networkx.MultiDiGraph``."""

    def __init__(self, version: GraphVersion) -> None:
        self._version = version
        self._graph: nx.MultiDiGraph = nx.MultiDiGraph()

    @property
    def version(self) -> GraphVersion:
        return self._version

    def upsert_entity(self, entity: RepositorySymbol) -> None:
        self._graph.add_node(entity.canonical_id, entity=entity)

    def upsert_relationship(self, relationship: CanonicalRelationship) -> None:
        self._graph.add_edge(
            relationship.subject,
            relationship.object,
            key=relationship.predicate.value,
            relationship=relationship,
        )

    def get_entity(self, canonical_id: str) -> RepositorySymbol | None:
        data = self._graph.nodes.get(canonical_id)
        return data["entity"] if data else None

    def get_relationships(
        self,
        *,
        subject: str | None = None,
        predicate: RelationshipType | None = None,
        object_id: str | None = None,
    ) -> list[CanonicalRelationship]:
        results: list[CanonicalRelationship] = []
        for u, v, data in self._graph.edges(data=True):
            rel: CanonicalRelationship = data["relationship"]
            if subject is not None and u != subject:
                continue
            if object_id is not None and v != object_id:
                continue
            if predicate is not None and rel.predicate != predicate:
                continue
            results.append(rel)
        return results

    def neighbors(
        self,
        canonical_id: str,
        *,
        predicate: RelationshipType | None = None,
        direction: str = "out",
    ) -> list[RepositorySymbol]:
        if canonical_id not in self._graph:
            return []
        if direction == "out":
            edges = list(self._graph.out_edges(canonical_id, data=True))
        elif direction == "in":
            edges = list(self._graph.in_edges(canonical_id, data=True))
        else:
            raise ValueError(f"direction must be 'in' or 'out', got {direction!r}")

        seen: set[str] = set()
        results: list[RepositorySymbol] = []
        for u, v, data in edges:
            rel: CanonicalRelationship = data["relationship"]
            if predicate is not None and rel.predicate != predicate:
                continue
            other_id = v if direction == "out" else u
            if other_id in seen:
                continue
            seen.add(other_id)
            entity = self.get_entity(other_id)
            if entity is not None:
                results.append(entity)
        return results

    def find_entities(
        self,
        *,
        name: str | None = None,
        qualified_name: str | None = None,
        base_type: BaseEntityType | None = None,
    ) -> list[RepositorySymbol]:
        results: list[RepositorySymbol] = []
        for _node, data in self._graph.nodes(data=True):
            entity: RepositorySymbol = data["entity"]
            if name is not None and name.lower() not in entity.name.lower():
                continue
            if (
                qualified_name is not None
                and qualified_name.lower() not in entity.qualified_name.lower()
            ):
                continue
            if base_type is not None and entity.base_type is not base_type:
                continue
            results.append(entity)
        results.sort(key=lambda e: e.canonical_id)
        return results
