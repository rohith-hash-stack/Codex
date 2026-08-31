"""Minimum Sufficient Subgraph construction (TAD §39-42; directive D9 Part 12).

MSS is the smallest evidence subgraph sufficient to answer the
`QueryContract` (TAD §39): the already-ranked, budget-bounded retrieval
result *is* the MSS -- no separate minimization pass is needed because
`bounded_traversal`'s `max_nodes`/`max_edges` ceiling and `rank_entities`
already produce a minimized, priority-ordered set (HLRD §36: "SHALL NOT
remove evidence required to satisfy completeness merely because it has a
lower relevance score" -- enforced by never letting ranking drop anything
budget-pruning didn't already exclude).

`expand_for_source_context` implements TAD §40's exact expansion rule and
bounds (max 2 cycles, 50 additional nodes/cycle) -- unrestricted
expansion is never performed.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from codex.coverage.engine import CapabilityCoverage
from codex.evidence.model import CanonicalRelationship, Evidence
from codex.graph.store import GraphReader
from codex.graph.version import GraphVersion
from codex.ontology.entities import LifecycleStatus, RepositorySymbol
from codex.provider.capability import Capability

MAX_EXPANSION_CYCLES = 2
MAX_NODES_PER_CYCLE = 50
"""TAD §40's exact literal bounds."""


def _has_source_context(entities: list[RepositorySymbol]) -> bool:
    return any(
        entity.lifecycle_status is LifecycleStatus.ACTIVE and entity.source_location is not None
        for entity in entities
    )


def expand_for_source_context(
    *,
    graph: GraphReader,
    entities: list[RepositorySymbol],
    required_capabilities: list[Capability],
) -> tuple[list[RepositorySymbol], bool]:
    """TAD §40: expansion occurs only when `SOURCE_LOCATION` is required
    and the current MSS does not already satisfy it. Returns
    `(expanded_entities, partial)` -- `partial=True` when the bound was
    exhausted without satisfying the requirement."""
    if Capability.SOURCE_LOCATION not in required_capabilities:
        return entities, False
    if _has_source_context(entities):
        return entities, False

    visited = {entity.canonical_id: entity for entity in entities}
    frontier = list(entities)
    for _cycle in range(MAX_EXPANSION_CYCLES):
        if _has_source_context(list(visited.values())):
            return sorted(visited.values(), key=lambda e: e.canonical_id), False

        added = 0
        next_frontier: list[RepositorySymbol] = []
        for entity in frontier:
            if added >= MAX_NODES_PER_CYCLE:
                break
            for direction in ("out", "in"):
                if added >= MAX_NODES_PER_CYCLE:
                    break
                for neighbor in graph.neighbors(entity.canonical_id, direction=direction):
                    if added >= MAX_NODES_PER_CYCLE:
                        break
                    if neighbor.canonical_id in visited:
                        continue
                    visited[neighbor.canonical_id] = neighbor
                    next_frontier.append(neighbor)
                    added += 1
        frontier = next_frontier
        if not frontier:
            break

    expanded = sorted(visited.values(), key=lambda e: e.canonical_id)
    return expanded, not _has_source_context(expanded)


class EvidencePackage(BaseModel):
    """TAD §42's `EvidencePackage` struct, field-for-field. This is the
    LLM's entire repository context boundary (TAD §42) -- D9 builds it,
    the (not-yet-implemented) LLM Gateway consumes it.

    `source_context` carries `SourceLocation`s, not fetched source text:
    reading actual file content at a specific revision is a capability no
    D1-D8 provider exposes (`GitAdapter` reads diffs/history, never
    arbitrary file content at revision X) -- honestly scoped out rather
    than fabricated, per `docs/architecture-conformance-audit.md` §R.
    """

    graph_version: GraphVersion
    query_identity: str
    entities: list[RepositorySymbol]
    relationships: list[CanonicalRelationship]
    evidence: list[Evidence]
    source_context: list[RepositorySymbol] = Field(default_factory=list)
    coverage: dict[str, CapabilityCoverage] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    partial: bool = False


__all__ = [
    "MAX_EXPANSION_CYCLES",
    "MAX_NODES_PER_CYCLE",
    "EvidencePackage",
    "expand_for_source_context",
]
