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


def _resolve_one_target(graph: GraphReader, target: str) -> list[RepositorySymbol]:
    """Candidate generation for one target string (HLRD §33), preferring
    an exact `qualified_name` match over a mere substring one (D9
    target-resolution refinement, `docs/architecture-conformance-audit.md`
    §II; real-repository benchmark findings #12/#13/#22/#24).

    `GraphReader.find_entities(name=..., qualified_name=...)` is itself
    still HLRD §34's plain, deterministic substring lookup, completely
    unchanged, on *both* axes. What changes is only the `qualified_name`
    axis's own contribution: if any of its results has a `qualified_name`
    *case-insensitively equal* to `target` (not merely containing it),
    only those exact matches are kept from that axis. The `name` axis's
    substring results are always kept in full, unnarrowed.

    This asymmetry is deliberate and measured, not a stylistic choice: a
    repository's own name (or any short string that happens to be a
    directory/file-path segment) makes `qualified_name` substring
    matching explode, because `qualified_name` routinely *is* a full
    repo-relative path and nearly every entity's path contains it (a
    `veyra`-repository query substring-matched 597 entities by
    `qualified_name` against only 2 by `name`, confirmed against the real
    repository). A short *symbol* name (a common method name, or a
    function name repeated across files) does not show this asymmetry --
    `name` and `qualified_name` substring matching return essentially the
    same set for those (confirmed: 98/98 for "extract", 92/92 for "run",
    13/13 for "classify" against the real repositories) -- and critically,
    real entities from different providers commonly do NOT share a bare
    exact name for the very same symbol (`SCIPAdapter`-derived entities
    carry a trailing ``().``/``ClassName#`` decoration `codex.provider.
    scip.mapping` documents; this AST provider's own entities do not).
    Narrowing the `name` axis to exact matches was tried first and
    reverted after it silently dropped SCIP-only entities (and the real
    evidence attached to them) for common callable names -- a real,
    measured regression, not a hypothetical one. Restricting the
    refinement to the `qualified_name` axis alone fixes the repository-
    name pathology with no such cost, because that axis's substring
    explosion was never where a short symbol name's real evidence lived.

    Deliberately does not try to normalize away provider-specific naming
    decoration itself -- doing so would require this provider-agnostic
    retrieval module to learn a specific provider's naming convention,
    exactly the kind of new architectural coupling this refinement must
    not introduce.
    """
    by_qualified_name = {e.canonical_id: e for e in graph.find_entities(qualified_name=target)}
    exact_qualified_name = {
        canonical_id: entity
        for canonical_id, entity in by_qualified_name.items()
        if entity.qualified_name.lower() == target.lower()
    }
    by_name = {e.canonical_id: e for e in graph.find_entities(name=target)}
    combined = {**(exact_qualified_name or by_qualified_name), **by_name}
    return list(combined.values())


def resolve_targets(graph: GraphReader, targets: list[str]) -> list[RepositorySymbol]:
    """Turn `QueryContract.targets` free-text strings into graph entities
    (HLRD §33 "candidate generation" -- deterministic substring lookup via
    `GraphReader.find_entities()`, no embeddings/fuzzy matching, HLRD §34;
    exact-match preference per target, see `_resolve_one_target`).
    """
    seen: dict[str, RepositorySymbol] = {}
    for target in targets:
        for entity in _resolve_one_target(graph, target):
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
    """Resolve both `CanonicalRelationship.supporting_evidence_ids` **and**
    `.contradicting_evidence_ids` into real `Evidence` records via the
    caller-supplied `EvidenceStore` (same explicit-injection pattern as
    `GraphReader` -- `docs/architecture-conformance-audit.md` §R.3).

    D10 Decision 4 (post-D9 closure audit §T.1 item 11): the package
    must be the verifier's **authoritative evidence boundary** -- a
    downstream Verification stage must never need to reach around
    `EvidencePackage` back into `EvidenceStore` merely to look up the
    evidence that contradicts a claim it is evaluating.
    """
    seen: dict[str, Evidence] = {}
    for rel in relationships:
        for evidence_id in (*rel.supporting_evidence_ids, *rel.contradicting_evidence_ids):
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
