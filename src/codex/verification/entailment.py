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

**Grounding-Integrity fix** (OpenAI Claim Grounding Integrity directive):
`resolve_claim_endpoint` resolves a claim's raw `subject`/`object`
string to the one canonical entity id it unambiguously names within
`EvidencePackage` (its `entities` and the endpoints of its own
`relationships`), strictly (canonical id, then exact qualified_name,
then exact bare name -- never a substring/similarity match, never a
guess when two or more entities share a name) before
`direct_edge_match`/`find_path` compare it against
`CanonicalRelationship.subject`/`.object` (always canonical ids). An
edge is never treated as undirected: matching is always `(resolved
subject, predicate, resolved object) == (rel.subject, rel.predicate,
rel.object)`, so a claim whose subject/object are swapped relative to
the real edge fails to match -- it is not, and never was, mistaken for
the same fact.
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


def resolve_claim_endpoint(value: str, package: EvidencePackage) -> str | None:
    """Resolves a claim's raw `subject`/`object` string to the one
    canonical entity id it unambiguously identifies within `package`
    (`EvidencePackage.entities`/`.relationships` only -- never a fresh
    graph/identity-resolver query; TAD §47/directive D10.4's
    "no re-retrieval" constraint applies here exactly as it does to the
    rest of this module).

    Grounding-Integrity fix: entailment used to compare `claim.subject`/
    `claim.object` directly against `CanonicalRelationship.subject`/
    `.object` (always canonical ids) with no resolution step at all --
    which happened to fail closed (a name never equals a canonical id,
    so it always fell through to `UNRESOLVED`) but also meant a
    correctly-oriented claim expressed the way an LLM naturally writes
    one -- by name or qualified name, not an opaque id string -- could
    never entail-match either. This closes that gap the *safe* way:
    strict-equality resolution only, and **ambiguity is never
    guessed away** -- a name shared by two or more entities in this
    evidence package resolves to `None`, exactly like a name matching
    zero entities.

    Resolution order: (1) `value` is already a real canonical id known to
    this package -- checked against both `package.entities`' own ids and
    every relationship endpoint in `package.relationships`, since a
    relationship's own subject/object is not always duplicated into
    `entities` and a real canonical id is globally unique by construction
    (this axis alone is therefore never ambiguous, no matter which of the
    two it's found in); (2) exactly one entity's `qualified_name`; (3)
    exactly one entity's bare `name`. Never a substring or similarity
    match (directive: "Do not... accept semantic/name similarity as
    evidence")."""
    known_ids = {entity.canonical_id for entity in package.entities}
    known_ids.update(rel.subject for rel in package.relationships)
    known_ids.update(rel.object for rel in package.relationships)
    if value in known_ids:
        return value

    qualified_matches = {e.canonical_id for e in package.entities if e.qualified_name == value}
    if len(qualified_matches) == 1:
        return next(iter(qualified_matches))
    if len(qualified_matches) > 1:
        return None

    name_matches = {e.canonical_id for e in package.entities if e.name == value}
    if len(name_matches) == 1:
        return next(iter(name_matches))
    return None


def direct_edge_match(claim: Claim, package: EvidencePackage) -> CanonicalRelationship | None:
    """Exact `(subject, predicate, object)` match against canonical
    entity identity, exact predicate, and exact direction. Covers "set
    membership" (`CONTAINS`) and "type hierarchy" (`IMPLEMENTS`/
    `EXTENDS`) claims too -- TAD §47 names no separate mechanism for
    those, only more specific predicates within this same exact-match
    check.

    **Grounding-Integrity fix**: both endpoints are resolved to their
    canonical entity id (`resolve_claim_endpoint`) *before* matching --
    an edge is never treated as undirected, and a claim whose subject or
    object cannot be unambiguously resolved never matches anything, on
    either side, ever (no partial credit, no name-similarity fallback).
    A reversed claim (`B predicate A` when only `A predicate B` is real
    evidence) resolves both endpoints correctly but then matches no
    `CanonicalRelationship` in `package.relationships`, since its own
    `(subject, object)` pair is the exact reverse of the real edge's --
    this is `==` comparison, so `(B, A) != (A, B)` unless `A == B`.
    """
    if not isinstance(claim.predicate, RelationshipType):
        return None
    subject_id = resolve_claim_endpoint(claim.subject, package)
    object_id = resolve_claim_endpoint(claim.object, package)
    if subject_id is None or object_id is None:
        return None
    for rel in package.relationships:
        if (
            rel.subject == subject_id
            and rel.predicate is claim.predicate
            and rel.object == object_id
        ):
            return rel
    return None


def find_path(claim: Claim, package: EvidencePackage) -> list[CanonicalRelationship]:
    """Bounded BFS reachability from `claim.subject` to `claim.object`
    over `package.relationships` restricted to the derived predicate's
    base relationship type. Bounded by construction: the search space
    is exactly `package.relationships`' finite edge set, never a fresh
    graph query. Returns the hop-by-hop path if found, else `[]`.

    Both endpoints are resolved to canonical entity ids first (same
    `resolve_claim_endpoint` as `direct_edge_match`, same "unresolvable
    or ambiguous -> no match" discipline) -- the search itself already
    only ever follows an edge's real `subject -> object` direction
    (`adjacency` is keyed by `rel.subject`), so it was never vulnerable
    to the reversed-edge defect the way string-identity comparison was,
    but it still needs the same identity resolution to find a real path
    at all when a claim names its endpoints rather than quoting ids.
    """
    if not isinstance(claim.predicate, str) or claim.predicate not in DERIVED_RELATIONSHIP_TYPES:
        return []
    subject_id = resolve_claim_endpoint(claim.subject, package)
    object_id = resolve_claim_endpoint(claim.object, package)
    if subject_id is None or object_id is None:
        return []
    base_predicate = _DERIVED_BASE_PREDICATE[claim.predicate]

    adjacency: dict[str, list[CanonicalRelationship]] = {}
    for rel in package.relationships:
        if rel.predicate is base_predicate:
            adjacency.setdefault(rel.subject, []).append(rel)

    visited = {subject_id}
    # BFS carrying the path-so-far so the first path found is returned
    # deterministically (edges within one node's adjacency list are
    # visited in `package.relationships`'s own sorted order).
    frontier: list[tuple[str, list[CanonicalRelationship]]] = [(subject_id, [])]
    while frontier:
        next_frontier: list[tuple[str, list[CanonicalRelationship]]] = []
        for node, path_so_far in frontier:
            for rel in adjacency.get(node, []):
                if rel.object == object_id:
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
    "resolve_claim_endpoint",
]
