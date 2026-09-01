"""Ranking Engine (TAD §36-37; directive D9 Part 6, Part 18).

V1's four deterministic ranking proxies, implemented exactly as TAD §36
specifies -- **no signal here was invented**; `docs/architecture-
conformance-audit.md` §R.2 traces each one to its exact TAD text. The
aggregation weights (`RANKING_WEIGHTS`) are TAD §37's own named
calibration point ("weights are calibration parameters", no V1 default
given).

D13 Phase 1/2 (ranking-calibration design/experiment, this project's
first genuine independent-validation-informed calibration pass) moved
these off equal weighting -- see `RANKING_WEIGHTS`'s own docstring for
the evidence.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Final

from codex.evidence.model import CanonicalRelationship
from codex.ontology.entities import RepositorySymbol
from codex.ontology.relationships import RelationshipType

RANKING_WEIGHTS: Final[dict[str, float]] = {
    "semantic_relevance": 0.25,
    "structural_relevance": 0.40,
    "graph_proximity": 0.25,
    "query_constraint_match": 0.10,
}
"""Calibration point (TAD §37: "weights are calibration parameters", no
V1 default given). D13's first calibration pass (Codex commit `e5a8754`
frozen as the pre-calibration reference), evaluated against
`django/django` (this project's one `INDEPENDENT_VALIDATION` repository)
plus `psf/requests` as a second independent set, with
`sourcegraph/scip-python`'s full 56-query battery re-run as a
research-evidence-only regression check (never as calibration evidence,
per this project's repository-independence policy):

- Equal weighting (baseline) ranked django's real dependency entities
  (`django`, `asgiref`, `sqlparse`, ...) at position 5 among 30
  candidates for `"What does django depend on?"` -- `semantic_relevance`
  (BM25 over qualified-name path tokens) gets swamped by the literal
  token `"django"` recurring in dozens of unrelated same-repository
  test names (`test_is_django_module`, ...), the same
  repository-name-token pathology Finding 2/D9 already fought at
  candidate-generation time, now observed independently at ranking
  time. *Boosting* `semantic_relevance` (tried at 0.40 and 0.55) made
  this measurably **worse** (rank 5 -> 20 -> 23) and helped nothing
  else -- rejected.
- `structural_relevance` (does the candidate have an incident
  relationship of the query's actual primary predicate type) is
  unaffected by this pathology -- it looks at graph structure, not
  name tokens. Boosting it to 0.40 (query_constraint_match reduced to
  0.10 to compensate, since it is vacuously 1.0 for the near-totality
  of real queries this project has ever run and so contributes little
  real signal) fixed the dependency-ranking case outright (rank 5 -> 0)
  with **zero** change to any other django query, **zero** change on
  `psf/requests`, and **zero** invariant differences (`plan_status`,
  `entity_count`, `relationship_count`, `coverage`,
  `negative_query_candidate`) across the *entire* 56-query
  `scip-python` regression sweep -- confirming, empirically, what the
  architecture already guarantees (`execute_query`'s own data flow):
  ranking can only ever reorder `package.entities`, never change which
  relationships or entities are present.
"""

GRAPH_PROXIMITY_DECAY: Final[float] = 0.9
"""TAD §36's own literal constant: `0.9^d`."""

_STRUCTURAL_MATCH: Final[float] = 1.0
_STRUCTURAL_MISMATCH: Final[float] = 0.3
"""TAD §36's own literal constants."""

_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_PATTERN.findall(text.lower())


@dataclass(frozen=True)
class RankingSignals:
    semantic_relevance: float
    structural_relevance: float
    graph_proximity: float
    query_constraint_match: float


def bm25_scores(documents: list[list[str]], query_tokens: list[str]) -> list[float]:
    """TAD §36's `semantic_relevance`: BM25 over each candidate's
    qualified name/name/roles ("qualified names, symbols, paths") against
    `QueryContract`'s extracted targets/constraints, normalized to
    `[0,1]` (TAD §36's explicit requirement)."""
    n = len(documents)
    if n == 0 or not query_tokens:
        return [0.0] * n

    k1, b = 1.5, 0.75
    avg_len = sum(len(doc) for doc in documents) / n
    doc_freq: dict[str, int] = {}
    for doc in documents:
        for term in set(doc):
            doc_freq[term] = doc_freq.get(term, 0) + 1

    raw_scores: list[float] = []
    for doc in documents:
        term_freq: dict[str, int] = {}
        for term in doc:
            term_freq[term] = term_freq.get(term, 0) + 1
        doc_len = len(doc)
        score = 0.0
        for term in query_tokens:
            freq = term_freq.get(term)
            if not freq:
                continue
            idf = math.log(1 + (n - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
            score += idf * (freq * (k1 + 1)) / (freq + k1 * (1 - b + b * doc_len / avg_len))
        raw_scores.append(score)

    peak = max(raw_scores, default=0.0)
    if peak <= 0.0:
        return [0.0] * n
    return [score / peak for score in raw_scores]


def structural_relevance(
    relationship: CanonicalRelationship | None, primary: RelationshipType | None
) -> float:
    """TAD §36: `1.0` on primary-relationship match, `0.3` otherwise."""
    if relationship is None or primary is None:
        return _STRUCTURAL_MISMATCH
    return _STRUCTURAL_MATCH if relationship.predicate is primary else _STRUCTURAL_MISMATCH


def graph_proximity(distance: int | None) -> float:
    """TAD §36: `0.9^d`, `d` = shortest-path distance. An unreached
    candidate (`distance is None`) scores `0.0` -- not on any path found."""
    if distance is None:
        return 0.0
    return GRAPH_PROXIMITY_DECAY**distance


def query_constraint_match(candidate_tags: set[str], query_constraints: set[str]) -> float:
    """TAD §36: Jaccard similarity over applicable paths/tags/roles/
    constraints. No constraints requested means nothing to violate --
    trivially `1.0`, a documented interpretation (TAD gives no vacuous-
    case rule)."""
    if not query_constraints:
        return 1.0
    if not candidate_tags:
        return 0.0
    intersection = len(candidate_tags & query_constraints)
    union = len(candidate_tags | query_constraints)
    return intersection / union if union else 1.0


def candidate_tags(entity: RepositorySymbol) -> set[str]:
    """Paths/tags/roles per TAD §36's `query_constraint_match` corpus --
    the entity's own roles, base type, and path segments."""
    segments = {segment for segment in entity.qualified_name.split("/") if segment}
    return {*entity.roles, entity.base_type.value, *segments}


def score(signals: RankingSignals) -> float:
    """TAD §37's weighted sum: `Score = w1*semantic + w2*structural +
    w3*graph_proximity + w4*query_constraint_match`."""
    return (
        RANKING_WEIGHTS["semantic_relevance"] * signals.semantic_relevance
        + RANKING_WEIGHTS["structural_relevance"] * signals.structural_relevance
        + RANKING_WEIGHTS["graph_proximity"] * signals.graph_proximity
        + RANKING_WEIGHTS["query_constraint_match"] * signals.query_constraint_match
    )


def rank_entities(
    *,
    entities: list[RepositorySymbol],
    relationships: list[CanonicalRelationship],
    distances: dict[str, int],
    query_targets: list[str],
    query_constraints: list[str],
    primary_relationship_type: RelationshipType | None,
) -> list[tuple[RepositorySymbol, float]]:
    """Rank `entities` by TAD §37's formula. Deterministic: ties broken by
    `canonical_id` (stable sort on an already canonical-id-sorted input)."""
    documents = [
        _tokenize(f"{entity.name} {entity.qualified_name} {' '.join(entity.roles)}")
        for entity in entities
    ]
    query_tokens = _tokenize(" ".join(query_targets))
    semantic_scores = bm25_scores(documents, query_tokens)
    query_constraint_set = {c.lower() for c in query_constraints}

    incident: dict[str, CanonicalRelationship] = {}
    for rel in relationships:
        incident.setdefault(rel.subject, rel)
        incident.setdefault(rel.object, rel)

    scored: list[tuple[RepositorySymbol, float]] = []
    for entity, semantic in zip(entities, semantic_scores, strict=True):
        signals = RankingSignals(
            semantic_relevance=semantic,
            structural_relevance=structural_relevance(
                incident.get(entity.canonical_id), primary_relationship_type
            ),
            graph_proximity=graph_proximity(distances.get(entity.canonical_id)),
            query_constraint_match=query_constraint_match(
                {tag.lower() for tag in candidate_tags(entity)}, query_constraint_set
            ),
        )
        scored.append((entity, score(signals)))

    scored.sort(key=lambda pair: (-pair[1], pair[0].canonical_id))
    return scored


__all__ = [
    "GRAPH_PROXIMITY_DECAY",
    "RANKING_WEIGHTS",
    "RankingSignals",
    "bm25_scores",
    "candidate_tags",
    "graph_proximity",
    "query_constraint_match",
    "rank_entities",
    "score",
    "structural_relevance",
]
