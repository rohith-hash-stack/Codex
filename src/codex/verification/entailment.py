"""Deterministic Entailment Engine (TAD §47; directive D10.3).

TAD §47: "V1 supports: direct edge matching, path existence, bounded
graph traversal, set membership, type hierarchy. Complex semantic
assertions default to UNRESOLVED unless deterministic rules exist."

Reduced to its two genuinely distinct mechanisms (no invention beyond
what TAD names): **direct edge matching** covers "set membership" and
"type hierarchy" too -- both are exact `(subject, predicate, object)`
matches against a specific `RelationshipType` already in the closed
ontology (`CONTAINS` for membership, `IMPLEMENTS`/`EXTENDS` for type
hierarchy) -- TAD names no separate mechanism for them. **Bounded path
existence** covers the three `DERIVED_RELATIONSHIP_TYPES` (TAD §14:
`REACHES`/`TRANSITIVE_CALLS`/`INDIRECTLY_DEPENDS_ON`), searched via BFS
over `EvidencePackage.relationships` only -- the traversal is bounded
by construction (the package's edge set is already finite, produced by
D9's own bounded MSS) rather than needing a separate depth parameter;
this deliberately never re-queries a live graph ("do not retrieve
additional evidence behind the verifier's back," directive D10.4).

Entailment does **not** decide contradiction (TAD §46's own pipeline
keeps "entailment" and "contradiction detection" as distinct stages);
that is D10.5's job, given this stage's `matched_relationship`.

No embeddings, no semantic similarity, no LLM/SLM entailment, no fuzzy
matching, no probabilistic inference -- every check here is an exact
structural match or a graph-reachability computation.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from pydantic import BaseModel

from codex.evidence.model import CanonicalRelationship
from codex.llm.schema import Claim
from codex.ontology.relationships import DERIVED_RELATIONSHIP_TYPES, RelationshipType
from codex.planner.mss import EvidencePackage


class EntailmentStatus(StrEnum):
    """TAD §47's own two-way framing: a deterministic rule either finds
    support, or the claim is UNRESOLVED. Nothing else is decided here."""

    SUPPORTED = "SUPPORTED"
    UNRESOLVED = "UNRESOLVED"


class EntailmentMethod(StrEnum):
    DIRECT_EDGE = "DIRECT_EDGE"
    PATH_EXISTENCE = "PATH_EXISTENCE"
    NONE = "NONE"


class EntailmentResult(BaseModel):
    claim: Claim
    status: EntailmentStatus
    method: EntailmentMethod = EntailmentMethod.NONE
    matched_relationship: CanonicalRelationship | None = None
    """Present only for `DIRECT_EDGE` matches."""

    matched_path: list[CanonicalRelationship] = []
    """Present only for `PATH_EXISTENCE` matches -- the exact hop-by-hop
    edges found, for traceability (TAD §51)."""

    detail: str | None = None


_DERIVED_BASE_PREDICATE: Final[dict[str, RelationshipType]] = {
    "REACHES": RelationshipType.CALLS,
    "TRANSITIVE_CALLS": RelationshipType.CALLS,
    "INDIRECTLY_DEPENDS_ON": RelationshipType.DEPENDS_ON,
}
"""Implementation detail (directive Phase 19 category 1): TAD §14 names
the three derived types and gives one worked example (REACHES built on
CALLS chains) but does not spell out INDIRECTLY_DEPENDS_ON's base
predicate explicitly -- DEPENDS_ON is the only structurally consistent
reading of its own name. No new relationship semantics invented."""


def direct_edge_match(claim: Claim, package: EvidencePackage) -> CanonicalRelationship | None:
    """Exact `(subject, predicate, object)` match. Covers "set
    membership" (`CONTAINS`) and "type hierarchy" (`IMPLEMENTS`/
    `EXTENDS`) claims too -- TAD §47 names no separate mechanism for
    those, only more specific predicates within this same exact-match
    check."""
    if not isinstance(claim.predicate, RelationshipType):
        return None
    for rel in package.relationships:
        if (
            rel.subject == claim.subject
            and rel.predicate is claim.predicate
            and rel.object == claim.object
        ):
            return rel
    return None


def find_path(claim: Claim, package: EvidencePackage) -> list[CanonicalRelationship]:
    """Bounded BFS reachability from `claim.subject` to `claim.object`
    over `package.relationships` restricted to the derived predicate's
    base relationship type. Bounded by construction: the search space
    is exactly `package.relationships`' finite edge set, never a fresh
    graph query. Returns the hop-by-hop path if found, else `[]`."""
    if not isinstance(claim.predicate, str) or claim.predicate not in DERIVED_RELATIONSHIP_TYPES:
        return []
    base_predicate = _DERIVED_BASE_PREDICATE[claim.predicate]

    adjacency: dict[str, list[CanonicalRelationship]] = {}
    for rel in package.relationships:
        if rel.predicate is base_predicate:
            adjacency.setdefault(rel.subject, []).append(rel)

    visited = {claim.subject}
    # BFS carrying the path-so-far so the first path found is returned
    # deterministically (edges within one node's adjacency list are
    # visited in `package.relationships`'s own sorted order).
    frontier: list[tuple[str, list[CanonicalRelationship]]] = [(claim.subject, [])]
    while frontier:
        next_frontier: list[tuple[str, list[CanonicalRelationship]]] = []
        for node, path_so_far in frontier:
            for rel in adjacency.get(node, []):
                if rel.object == claim.object:
                    return [*path_so_far, rel]
                if rel.object not in visited:
                    visited.add(rel.object)
                    next_frontier.append((rel.object, [*path_so_far, rel]))
        frontier = next_frontier
    return []


def entail_claim(claim: Claim, package: EvidencePackage) -> EntailmentResult:
    """The single entry point D10.4 (Verification Engine) calls per
    claim. Never mutates `claim`/`package`; never queries anything
    beyond `package.relationships`."""
    direct = direct_edge_match(claim, package)
    if direct is not None:
        return EntailmentResult(
            claim=claim,
            status=EntailmentStatus.SUPPORTED,
            method=EntailmentMethod.DIRECT_EDGE,
            matched_relationship=direct,
        )

    path = find_path(claim, package)
    if path:
        return EntailmentResult(
            claim=claim,
            status=EntailmentStatus.SUPPORTED,
            method=EntailmentMethod.PATH_EXISTENCE,
            matched_path=path,
        )

    return EntailmentResult(
        claim=claim,
        status=EntailmentStatus.UNRESOLVED,
        method=EntailmentMethod.NONE,
        detail="no deterministic edge or bounded path found in EvidencePackage",
    )


__all__ = [
    "EntailmentMethod",
    "EntailmentResult",
    "EntailmentStatus",
    "direct_edge_match",
    "entail_claim",
    "find_path",
]
