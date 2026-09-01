"""Deterministic Retrieval Engine (TAD §35; directive D9 Parts 10-11).

Executes a `RetrievalPlan` against the locked `GraphReader`/`EvidenceStore`
it was given. **Never invokes an LLM/SLM, never asks "what files should I
look at?"** -- retrieval scope comes entirely from the `RetrievalPlan`
(itself derived from `QueryContract` + graph structure), never from a
model call (directive Part 11, TAD §30).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from codex.evidence.model import CanonicalRelationship, Evidence
from codex.evidence.store import EvidenceStore
from codex.graph.store import GraphReader
from codex.ontology.entities import RepositorySymbol
from codex.ontology.relationships import RelationshipType

_DIRECTIONAL_PREDICATES: Final = frozenset({RelationshipType.CALLS, RelationshipType.IMPLEMENTS})
"""Predicates with a single caller-facing "correct" direction relative to a
queried target (post-fix external-repository readiness audit, "relationship-
set imprecision" finding).

Every intent in `codex.query_understanding.models.Intent` that can ever
produce `RelationshipType.CALLS` (`FIND_CALLERS`, `FIND_TESTS`,
`TRACE_EXECUTION` -- see `codex.query_understanding.engine`'s
`_relationship_types_for_intent`) means "X calls the target" -- there is no
"what does the target call" intent in the current vocabulary, so this can be
keyed on predicate alone without threading `Intent` through `RetrievalPlan`.
`RelationshipType.IMPLEMENTS` is even narrower: only `FIND_IMPLEMENTATIONS`
ever produces it. Neither ambiguity case this file's docstrings warn about
elsewhere (SCIP naming decoration, provider coupling) applies here: this is
graph-topology direction, not name normalization.
"""


def _has_boundary_aligned_occurrence(text: str, target: str) -> bool:
    """True if `target` occurs in `text` (case-insensitive) at a position
    not immediately preceded by another alphanumeric character -- i.e. at
    the very start of `text`, or right after a non-alphanumeric separator
    (`#`, `.`, `/`, `::`, `_`, ...). False only when *every* occurrence of
    `target` in `text` is buried mid-identifier (preceded by a letter or
    digit), like `"classab"` inside `"SubclassableObject"` (preceded by the
    `b` of `"Subclassable"`).

    Deliberately provider-agnostic (`_resolve_one_target`'s own established
    discipline): this treats *any* non-alphanumeric character as a boundary
    -- it does not know SCIP's `().`/`#` convention is SCIP's, it just
    tolerates trailing/leading punctuation generically. `"add"` in
    `"add()."` (boundary: start of string) and `"extract"` in
    `"AdapterA#extract()."` (boundary: preceded by `#`) both count; `"add"`
    in `"AddHelperVariant0"` (boundary: start of string, followed by more
    letters -- still a real word-initial match) also counts. Only a match
    with letters/digits on *both* sides that don't include the boundary is
    buried.
    """
    lowered = text.lower()
    lowered_target = target.lower()
    start = 0
    while True:
        idx = lowered.find(lowered_target, start)
        if idx == -1:
            return False
        if idx == 0 or not text[idx - 1].isalnum():
            return True
        start = idx + 1


def _symbol_path(qualified_name: str) -> str:
    """The portion of `qualified_name` identifying the symbol itself, as
    opposed to the file/directory path it lives under (GAP-1 fix,
    "qualified-name file-path substring causes unrelated-symbol seed
    explosion" -- D13 independent-validation finding). `qualified_name`'s
    established format across every provider this project has is
    `<file-path>::<symbol-path>` -- confirmed for `AstCallsAdapter`
    (`"src/_pytest/approx.py::approx"`) and for SCIP-sourced entities too
    (`_resolve_one_target`'s own regression test constructs
    `"pkg/a.py::AdapterA.extract"` as the `qualified_name` for a
    SCIP-decorated `"AdapterA#extract()."` entity's `name`) -- so the
    substring after the *last* `"::"` is always the symbol's own path,
    never a directory/file segment. A `qualified_name` with no `"::"` at
    all (a bare module/file-level identity with no separate symbol
    suffix, as several of this module's own pre-existing tests
    construct) is returned unchanged -- there is no file-path segment to
    strip from it in that shape, and narrowing it would silently drop
    real matches the pre-GAP-1 substring/boundary logic already relied
    on for those entities.
    """
    return qualified_name.rpartition("::")[2] if "::" in qualified_name else qualified_name


def _match_tier(entity: RepositorySymbol, targets: set[str]) -> int:
    """Deterministic 3-way classification of how `entity` relates to the
    query's target strings (D9 candidate-prioritization refinement,
    following the directional-retrieval fix's own "relationship-set
    imprecision" finding to its D9 root cause -- real-repository
    confirmation: `sourcegraph/scip-python`'s `"SubclassableObject"`
    resolving as a candidate for `"ClassAB"` only because `"Subclassable"`
    happens to literally contain the letters `"classab"` mid-word).

    0 = literal identity: `entity.name` or `entity.qualified_name`'s own
        symbol path (`_symbol_path`) is exactly (case-insensitively)
        equal to one of `targets`.
    1 = boundary-aligned match: not identity, but at least one target
        occurs in `entity.name` or `entity.qualified_name`'s symbol path
        at a boundary (`_has_boundary_aligned_occurrence`) -- includes
        SCIP-decorated symbols (`"add()."`, `"AdapterA#extract()."`)
        *and* real word-initial collisions (`"AddHelperVariant0"`,
        `"InterfaceAB"`, `"TestClass1"`) that HLRD §34 discovery must
        keep finding.
    2 = incidental/buried: every occurrence of every target in this
        entity's `name`/`qualified_name` symbol path is buried
        mid-identifier -- **or occurs only in the file/directory-path
        segment `qualified_name` carries ahead of its symbol path**
        (GAP-1: a target that merely names the file a symbol happens to
        live in, e.g. `"approx"` matching `"src/_pytest/approx.py"` in
        `"src/_pytest/approx.py::_is_bool"`, is not evidence that symbol
        relates to the target at all).

    Only the symbol-path portion of `qualified_name` (`_symbol_path`)
    ever participates in this classification -- the file/directory-path
    segment, and any `/` separator within it, is never treated as a
    match or a match boundary, by construction (it is sliced off before
    any comparison runs, not filtered after the fact).
    """
    name_lower = entity.name.lower()
    qn_symbol_lower = _symbol_path(entity.qualified_name).lower()
    best = 2
    for target in targets:
        if name_lower == target or qn_symbol_lower == target:
            return 0
        if best > 1 and (
            _has_boundary_aligned_occurrence(entity.name, target)
            or _has_boundary_aligned_occurrence(_symbol_path(entity.qualified_name), target)
        ):
            best = 1
    return best


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

    **GAP-1 fix** (D13 independent-validation finding, "qualified-name
    file-path substring causes unrelated-symbol seed explosion" --
    real-repository confirmation: `pytest-dev/pytest`'s `approx.py`,
    where `find_entities(qualified_name="approx")`'s raw substring search
    matched every symbol in that file, including `_is_bool`/
    `_recursive_sequence_map`, whose own names have nothing to do with
    `"approx"` -- and `psf/requests`'s `models.py`, where the same
    mechanism produced a confident-looking non-empty candidate set for
    `"models"` even though no symbol literally named `"models"` exists
    anywhere in the repository). `qualified_name`'s established format
    across every provider is `<file-path>::<symbol-path>`
    (`_symbol_path`) -- the `qualified_name` axis's raw substring result
    is narrowed, immediately after the store lookup, to only the entities
    where `target` genuinely occurs in that *symbol*-path portion, not
    merely somewhere in the file/directory-path prefix. This is a
    distinct axis-narrowing from the exact-match one two paragraphs up
    (that one prefers a full-string exact match when one exists; this one
    removes candidates that were never really about the target symbol at
    all) -- both apply to the same `qualified_name` axis, never to the
    `name` axis, and neither touches `GraphReader.find_entities` itself.
    A `qualified_name` with no `"::"` at all keeps today's behavior
    entirely (`_symbol_path`'s own documented fallback) -- this fix is
    scoped exactly to the `<file-path>::<symbol-path>` shape it was
    written for.
    """
    raw_by_qualified_name = graph.find_entities(qualified_name=target)
    target_lower = target.lower()
    by_qualified_name = {
        e.canonical_id: e
        for e in raw_by_qualified_name
        if target_lower in _symbol_path(e.qualified_name).lower()
    }
    exact_qualified_name = {
        canonical_id: entity
        for canonical_id, entity in by_qualified_name.items()
        if entity.qualified_name.lower() == target.lower()
    }
    by_name = {e.canonical_id: e for e in graph.find_entities(name=target)}
    combined = {**(exact_qualified_name or by_qualified_name), **by_name}

    # D9 candidate-prioritization refinement (post-Finding-3 external
    # audit's "candidate-generation ambiguity" finding): once *any*
    # non-buried match for `target` exists anywhere in `combined` (tier 0
    # exact, or tier 1 boundary-aligned -- deliberately not tier-0-only,
    # see below), drop candidates whose *only* connection to `target` is
    # an incidental, mid-identifier substring occurrence (tier 2) -- e.g.
    # `"SubclassableObject"` for target `"ClassAB"`. Every boundary-aligned
    # match is kept unconditionally, including every shape HLRD §34
    # discovery and the Finding-2 exact-bare-name refinement already rely
    # on (`"add()."`, `"AdapterA#extract()."`, `"AddHelperVariant0"`,
    # `"InterfaceAB"`, `"TestClass1"`) -- this narrowing is strictly about
    # buried, boundary-free occurrences, never about a candidate merely
    # being a *longer* identifier than `target`.
    #
    # The gate is "tier 0 or 1", not "tier 0 only": real-repository
    # verification (`sourcegraph/scip-python`) showed every class/interface
    # entity IMPLEMENTS queries resolve is SCIP-only (`AstCallsAdapter`
    # never emits class-level entities) and therefore *always* carries
    # SCIP's own `#` class-descriptor suffix (`"ClassAB#"`, never bare
    # `"ClassAB"`) -- a byte-exact tier-0 match essentially never exists
    # for these queries at all, only tier-1 ones. Gating on tier-0-only
    # would make this refinement inert for the exact real-world case it
    # was written for; gating on "not buried" fixes that without changing
    # what counts as buried.
    target_set = {target.lower()}
    if any(_match_tier(entity, target_set) <= 1 for entity in combined.values()):
        combined = {
            canonical_id: entity
            for canonical_id, entity in combined.items()
            if _match_tier(entity, target_set) <= 1
        }
    return list(combined.values())


def resolve_targets(graph: GraphReader, targets: list[str]) -> list[RepositorySymbol]:
    """Turn `QueryContract.targets` free-text strings into graph entities
    (HLRD §33 "candidate generation" -- deterministic substring lookup via
    `GraphReader.find_entities()`, no embeddings/fuzzy matching, HLRD §34;
    exact-match preference per target, see `_resolve_one_target`).

    **Exact-bare-name-match ordering** (D9 target-resolution refinement,
    Finding 2 of the external GitHub real-repository readiness audit):
    when `plan_query`'s own existing budget truncation (`target_entities
    [:max_nodes]`, `codex.planner.planner`, unchanged by this refinement)
    later has to cut this list down to `max_nodes`, entities whose bare
    `name` *exactly* equals one of `targets` (case-insensitively) are
    sorted first, ahead of every entity that only matched by substring on
    either axis -- `canonical_id` remains the ordering *within* each of
    those two groups, exactly as before this refinement (deterministic,
    unchanged tie-break). This never changes *which* entities
    `resolve_targets` returns, only their order, so a caller under budget
    (the common case) sees byte-identical results; it only matters once a
    combined candidate set from a heavily name-colliding target (real
    example: a query for "add" resolving 7 exact real entities plus
    ~1,930 further substring matches, confirmed against `sourcegraph/
    scip-python`, an independently selected real repository) exceeds
    `max_nodes` and the previous canonical-id-only order could put every
    one of the genuine exact matches past the cut, discarding the very
    entity the query named before any relationship retrieval or
    verification is ever attempted.

    Deliberately still a plain, undecorated string-equality test against
    `RepositorySymbol.name` -- exactly `_resolve_one_target`'s own
    established discipline (its docstring: "does not try to normalize
    away provider-specific naming decoration itself... exactly the kind
    of new architectural coupling this refinement must not introduce").
    A SCIP-decorated name (`add().`) is therefore not specially rewritten
    to match a bare query target ("add") here either -- doing so would
    require this provider-agnostic module to learn SCIP's own naming
    convention, out of scope for this refinement.

    **Candidate prioritization / buried-match narrowing** (D9 refinement,
    post-Finding-3 external-repository readiness audit's "candidate-
    generation ambiguity" finding, `_match_tier`): the two-way exact/
    substring split above is now the outer two tiers of a 3-way
    `_match_tier` classification (0 exact identity, 1 boundary-aligned
    substring, 2 incidental/buried substring), used two ways:

    1. Ordering (this function): the sort key is now `_match_tier`
       (0/1/2) then `canonical_id`, a strict generalization of the
       previous `bool` key -- every entity this refinement used to sort
       into the "exact" group still sorts first, identically.
    2. Membership (`_resolve_one_target`, one target at a time): once an
       exact (tier 0) match exists for a given *target string*, tier-2
       (buried) candidates for that same target are dropped entirely --
       e.g. `"SubclassableObject"` is no longer returned at all for a
       query naming `"ClassAB"`, once the real `ClassAB` entity exists.
       This is a genuine change from "only their order" for that one,
       narrow case; every tier-0 and tier-1 candidate -- which includes
       *every* shape the docstring above and `_resolve_one_target`'s own
       docstring guard (`add().`, `AdapterA#extract().`,
       `AddHelperVariant0`, `InterfaceAB`, `TestClass1`) -- is returned
       exactly as before, unaffected by this narrowing.
    """
    seen: dict[str, RepositorySymbol] = {}
    for target in targets:
        for entity in _resolve_one_target(graph, target):
            seen[entity.canonical_id] = entity
    target_set = {target.lower() for target in targets}
    return sorted(
        seen.values(),
        key=lambda e: (_match_tier(e, target_set), e.canonical_id),
    )


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

    **Directional-predicate anchoring** (post-fix external-repository
    readiness audit, "relationship-set imprecision" finding): for
    `_DIRECTIONAL_PREDICATES` (`CALLS`, `IMPLEMENTS`), a relationship is only
    collected when its *object* endpoint is one of the original `seeds` --
    i.e. one of `resolve_targets`'s own resolved entities for this query,
    unchanged (D9 candidate generation is not touched by this filter; it
    decides *which* entities are seeds, this decides *which edges touching
    them* answer the directional question actually asked). Confirmed against
    the real `sourcegraph/scip-python` repository: this is what turns
    `"What calls foo?"`'s 7 relationships (4 wrong-directioned/unrelated) into
    the subset that actually reads "caller -> foo", and what makes
    `"Implementations of ClassAB"` (a real leaf class with zero subclassers)
    stop surfacing `ClassAB`'s own upward `IMPLEMENTS` edges as if they
    answered "what implements ClassAB".

    A directional-predicate relationship's *subject* endpoint is therefore
    never used to admit it, regardless of whether the subject is itself a
    seed or a hop-expanded neighbor -- `"What calls __init__?"` no longer
    surfaces `__init__ -> test1` (a real edge, wrong direction for this
    question) as if it were evidence of a caller. Non-directional predicates
    (`REFERENCES`, `IMPORTS`, `DEPENDS_ON`, `CO_CHANGED_WITH`, ...) keep the
    prior both-direction, any-frontier-entity collection unchanged -- this
    finding and its fix are specific to `CALLS`/`IMPLEMENTS`, not a general
    retrieval redesign.

    Node visitation (which entities enter `visited`/get returned) is
    completely unaffected -- only which *edges* end up in `relationships`.
    """
    visited: dict[str, RepositorySymbol] = {s.canonical_id: s for s in seeds}
    distances: dict[str, int] = {s.canonical_id: 0 for s in seeds}
    relationships: dict[tuple[str, RelationshipType, str], CanonicalRelationship] = {}
    truncated = False
    frontier = list(seeds)
    predicates: list[RelationshipType | None] = list(relationship_types) or [None]
    seed_ids = frozenset(visited)

    for level in range(depth):
        next_frontier: list[RepositorySymbol] = []
        for entity in frontier:
            for predicate in predicates:
                directional = predicate in _DIRECTIONAL_PREDICATES
                if not directional:
                    for rel in graph.get_relationships(
                        subject=entity.canonical_id, predicate=predicate
                    ):
                        if rel.key not in relationships and len(relationships) >= max_edges:
                            truncated = True
                            continue
                        relationships[rel.key] = rel
                if directional and entity.canonical_id not in seed_ids:
                    continue
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
