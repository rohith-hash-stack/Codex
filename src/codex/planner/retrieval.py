"""Deterministic Retrieval Engine (TAD §35; directive D9 Parts 10-11).

Executes a `RetrievalPlan` against the locked `GraphReader`/`EvidenceStore`
it was given. **Never invokes an LLM/SLM, never asks "what files should I
look at?"** -- retrieval scope comes entirely from the `RetrievalPlan`
(itself derived from `QueryContract` + graph structure), never from a
model call (directive Part 11, TAD §30).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from codex.evidence.model import CanonicalRelationship, Evidence
from codex.evidence.store import EvidenceStore
from codex.graph.store import GraphReader
from codex.ontology.entities import RepositorySymbol
from codex.ontology.relationships import RelationshipType


def resolve_targets(graph: GraphReader, targets: list[str]) -> list[RepositorySymbol]:
    """Turn `QueryContract.targets` free-text strings into graph entities
    (HLRD §33 "candidate generation" -- deterministic substring lookup via
    `GraphReader.find_entities()`, no embeddings/fuzzy matching, HLRD §34).
    """
    seen: dict[str, RepositorySymbol] = {}
    for target in targets:
        for entity in (
            *graph.find_entities(qualified_name=target),
            *graph.find_entities(name=target),
        ):
            seen[entity.canonical_id] = entity
    return sorted(seen.values(), key=lambda e: e.canonical_id)


@dataclass(frozen=True)
class TraversalResult:
    """Deterministic (sorted) bounded-traversal output. `truncated` is
    `True` whenever the `max_nodes`/`max_edges` ceiling stopped expansion
    before every reachable node/edge within `depth` was visited."""

    entities: list[RepositorySymbol]
    relationships: list[CanonicalRelationship]
    distances: dict[str, int] = field(default_factory=dict)
    truncated: bool = False


def bounded_traversal(
    graph: GraphReader,
    seeds: list[RepositorySymbol],
    relationship_types: list[RelationshipType],
    depth: int,
    max_nodes: int,
    max_edges: int,
) -> TraversalResult:
    """TAD §35's "bounded traversal", respecting `max_nodes`/`max_edges`
    (TAD §41) and `depth` (TAD §29's Planner output). Deterministic:
    frontier order follows `find_entities`'s sorted seed order and
    `neighbors()`'s own deterministic iteration; final output is sorted.
    """
    visited: dict[str, RepositorySymbol] = {s.canonical_id: s for s in seeds}
    distances: dict[str, int] = {s.canonical_id: 0 for s in seeds}
    relationships: dict[tuple[str, RelationshipType, str], CanonicalRelationship] = {}
    truncated = False
    frontier = list(seeds)
    predicates: list[RelationshipType | None] = list(relationship_types) or [None]

    for level in range(depth):
        next_frontier: list[RepositorySymbol] = []
        for entity in frontier:
            for predicate in predicates:
                for rel in graph.get_relationships(
                    subject=entity.canonical_id, predicate=predicate
                ):
                    if rel.key not in relationships and len(relationships) >= max_edges:
                        truncated = True
                        continue
                    relationships[rel.key] = rel
                for rel in graph.get_relationships(
                    object_id=entity.canonical_id, predicate=predicate
                ):
                    if rel.key not in relationships and len(relationships) >= max_edges:
                        truncated = True
                        continue
                    relationships[rel.key] = rel
            for direction in ("out", "in"):
                for predicate in predicates:
                    for neighbor in graph.neighbors(
                        entity.canonical_id, predicate=predicate, direction=direction
                    ):
                        if neighbor.canonical_id in visited:
                            continue
                        if len(visited) >= max_nodes:
                            truncated = True
                            continue
                        visited[neighbor.canonical_id] = neighbor
                        distances[neighbor.canonical_id] = level + 1
                        next_frontier.append(neighbor)
        frontier = next_frontier
        if not frontier:
            break

    entities = sorted(visited.values(), key=lambda e: e.canonical_id)
    edges = sorted(relationships.values(), key=lambda r: (r.subject, r.predicate.value, r.object))
    # `edges` needs no equivalent defensive slice: every relationship is
    # checked against `max_edges` before being added (above), so it can
    # never exceed the ceiling. `entities` differs because seeds are
    # admitted unconditionally at initialization, bypassing that check.
    if len(entities) > max_nodes:
        entities = entities[:max_nodes]
    return TraversalResult(
        entities=entities, relationships=edges, distances=distances, truncated=truncated
    )


def collect_evidence(
    evidence_store: EvidenceStore, relationships: list[CanonicalRelationship]
) -> list[Evidence]:
    """Resolve `CanonicalRelationship.supporting_evidence_ids` into real
    `Evidence` records via the caller-supplied `EvidenceStore` (same
    explicit-injection pattern as `GraphReader` -- `docs/architecture-
    conformance-audit.md` §R.3)."""
    seen: dict[str, Evidence] = {}
    for rel in relationships:
        for evidence_id in rel.supporting_evidence_ids:
            if evidence_id in seen:
                continue
            evidence = evidence_store.get_evidence(evidence_id)
            if evidence is not None:
                seen[evidence_id] = evidence
    return sorted(seen.values(), key=lambda e: e.evidence_id)


__all__ = [
    "TraversalResult",
    "bounded_traversal",
    "collect_evidence",
    "resolve_targets",
]
