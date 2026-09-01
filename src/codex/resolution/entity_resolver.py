"""Entity Resolution (TAD §12, §16; HLRD §18; post-D7 directive Phase B).

Deterministic convergence of provider-reported entities onto one
canonical `RepositorySymbol` per real repository entity — the
"Different providers describing the same real repository entity must
converge onto the same Codex canonical entity" requirement, sitting
between the Canonical Graph and Evidence Reconciliation in the pipeline
this directive names:

    IngestionPipeline -> Canonical Graph -> Entity Resolution
        -> Evidence Reconciliation -> ...

**Pre-audit findings (directive Phase B §7), verified by direct
inspection before writing this module — not assumed:**

- The *only* entity base type every current provider can produce is
  `FILE`: `GitAdapter` (HISTORY/CO_CHANGE), `SCIPAdapter` (file
  references), and `CodeQLAdapter` (finding/flow locations) all call
  `build_canonical_id(..., base_type=BaseEntityType.FILE, ...)` with a
  repository-relative `qualified_name`. Non-FILE entities (CLASS,
  METHOD, ...) were, at the time this section was first written,
  produced by `SCIPAdapter` alone — cross-provider symbol convergence
  had no live case then. **Now stale**: `AstCallsAdapter` (D7) also
  produces `FUNCTION`/`METHOD` entities, giving this a live case —
  closed by the symbol-location identity key below (D7/D9 convergence
  research + implementation, `docs/architecture-conformance-audit.md`
  §II/§JJ), which directly implements HLRD §19's "Codex SHALL resolve
  equivalent provider entities into canonical entities" for exactly
  this scenario.
- `build_canonical_id` already gives *exact* convergence when every
  input string matches byte-for-byte (same `repository_id`,
  `repository_revision`, `base_type`, `language`, `qualified_name`) --
  proven by existing tests (D5/D6). The one real, if so-far
  undemonstrated, risk is `qualified_name` *string* drift for FILE
  entities: each adapter derives its own path string from its own
  provider format (`GitAdapter` from `git`'s diff output, `SCIPAdapter`
  from SCIP's `Document.relative_path`, `CodeQLAdapter` from SARIF's
  `artifactLocation.uri`, which the OASIS schema types as an open
  `uri-reference` — nothing requires it to already be a bare,
  `/`-separated, repo-relative string). Real SARIF fixtures inspected
  for this phase all happen to already be plain relative paths (no
  defect observed in practice), but nothing enforced this — see
  `docs/architecture-conformance-audit.md` §M.
- `RepositorySymbol.provider_ids` exists (TAD's own field) but is
  populated only by `GitAdapter` today (`{"git": path}`); SCIP/CodeQL
  never set it. Not a defect -- optional per-provider metadata, present
  where a provider chose to record it -- but merging must not silently
  drop it when two entities converge.
- `IngestionPipeline._commit_entities` (D4) previously kept only the
  *last-committed* `RepositorySymbol` per `canonical_id` when two
  providers produced the same id -- a real, if never-yet-triggered,
  provenance-loss risk (`roles`/`provider_ids` from the earlier entity
  were silently discarded, contradicting D4's own documented "no
  evidence is ever discarded" invariant). This module replaces that
  behavior with an explicit, tested merge.

**What this module does NOT do (directive Phase B §8, explicit):**

- No LLM/SLM, no embeddings, no probabilistic/fuzzy name matching as a
  primary mechanism. Every merge decision here is a pure function of
  already-provider-reported, already-deterministic fields.
- No second identity system: the canonical `canonical_id` computed by
  `build_canonical_id` is not replaced -- this module only widens *which
  inputs* are compared before trusting a raw provider-supplied
  `qualified_name` verbatim (path normalization for FILE/DIRECTORY; a
  synthesized, source-location-derived `qualified_name` for symbol-level
  entities, both described below), and always recomputes the final id
  through the same, unmodified `build_canonical_id` function.

**Symbol-level (`FUNCTION`/`METHOD`/`CLASS`) convergence, added by the
D7/D9 convergence research + implementation directive:**

- Uses the approved key exactly: `(repository_id, repository_revision,
  base_type, file_path, start_line)` -- never `name`/`qualified_name`
  (both are provider-specific, incompatible schemes between SCIP and
  `AstCallsAdapter`; see the research report), never fuzzy/embedding/
  probabilistic matching, never a second provider's identifiers.
  `file_path`/`start_line` come from `RepositorySymbol.source_location`
  -- an entity with no location (e.g. an `EXTERNAL_LIBRARY`, which is
  symbol-shaped but never positioned in this repository) is never
  eligible, matching this module's existing "only use signals the
  architecture actually supports" principle.
- **Applied unconditionally, exactly like path normalization -- not
  gated on a merge partner being present in the same batch.** An
  earlier version of this key only recomputed/renamed an entity when a
  *second* raw entity in the same `resolve_entities()` call already
  shared its symbol-location key, leaving a lone (single-provider, so
  far) symbol entity's original provider-computed `canonical_id`
  untouched. That gating was found, by real-repository ingestion (not
  a hypothetical), to reintroduce exactly the provenance loss this
  module exists to prevent: `IngestionPipeline` commits each provider's
  entities *and* evidence together, one provider at a time, in
  alphabetical provider-name order (`ast_calls` before `scip`). A lone
  `AstCallsAdapter` entity committed first kept its original id (no
  merge partner yet); its own CALLS evidence was committed against
  that id in the same call. When `SCIPAdapter` committed its matching
  entity later, *that* commit correctly discovered the merge and
  renamed both entities' ids -- but nothing ever revisits
  already-committed evidence from an earlier provider's earlier commit
  (`EvidenceStore` has no update/re-key operation, by design -- see
  `codex.evidence.store`), so the earlier provider's evidence was left
  pointing at a now-nonexistent id and was silently dropped by
  `_materialize_store`'s referential-integrity check. A real, true
  `CALLS` relationship (`classify_and_audit -> classify` in `veyra`)
  was destroyed by the very feature meant to make Codex more correct,
  violating this module's own "no evidence is ever discarded"
  principle and HLRD §19's "shall preserve all provider references
  rather than deleting them."

  The fix is the same one path normalization already relies on:
  recompute the target id *unconditionally*, before any merge-partner
  check, so a provider's *own first-ever commit* of a symbol already
  receives its final, stable, location-derived id -- there is no
  window in which that provider's own evidence can go stale, because
  nothing about the id changes later when a second provider's matching
  entity shows up. This does not weaken the identity rule (the key is
  unchanged) or risk a false merge (unconditional application changes
  *when* the synthesized id is assigned, never *what* has to match for
  two entities to land on the same id) -- it only removes a
  same-batch-cardinality precondition that had no basis in the
  architecture and, in practice, actively broke provenance across
  ingestion batches. Unlike path normalization, the synthesized
  `qualified_name` (see `_SYMBOL_LOCATION_TAG`) never equals any real
  provider's own `qualified_name` string, so a symbol-level entity
  eligible for this key is *always* tagged `SYMBOL_LOCATION_IDENTITY`,
  even when (so far) no second provider has reported it -- an honest
  description of *how* the id was computed, not a claim that a merge
  occurred (`EntityMergeRecord.source_canonical_ids` still reports
  exactly one raw id for a still-uncorroborated symbol).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from codex.ontology.entities import (
    BaseEntityType,
    LifecycleStatus,
    RepositorySymbol,
    build_canonical_id,
)
from codex.resolution.paths import normalize_repo_relative_path

_PATH_SHAPED_BASE_TYPES = frozenset({BaseEntityType.FILE, BaseEntityType.DIRECTORY})
"""Base types whose `qualified_name` is a filesystem path and therefore
eligible for normalized-identity matching (directive Phase B §12's
"Path normalization" requirement). Symbol-level qualified_names (SCIP
descriptor paths, external-library `manager:name@version` strings) are
provider-internal identifiers, not filesystem paths, and are never
renormalized here -- trusting a provider's own exact, already-correct
identity computation for those is what "use signals explicitly
supported by the existing architecture" means in practice."""

_SYMBOL_LOCATION_BASE_TYPES = frozenset(
    {BaseEntityType.FUNCTION, BaseEntityType.METHOD, BaseEntityType.CLASS}
)
"""Base types eligible for the symbol-location identity key (D7/D9
convergence directive's approved strategy). Deliberately not every
symbol-shaped base type: `EXTERNAL_LIBRARY` has no in-repository
position (`source_location` is always `None` for it, `SCIPAdapter`'s
own documented design), so it is never eligible -- there is no location
signal to key on, and this module does not guess one."""

_SYMBOL_LOCATION_TAG: Final = "codex:symbol-location-identity"
"""Prefix for the synthesized `qualified_name` `_recompute_symbol_
canonical_id` builds `build_canonical_id`'s input from. Deliberately
distinct from any real provider `qualified_name` shape (a plain repo-
relative path, a SCIP descriptor path, or this adapter's own `<path>::
<name>` scheme) so a synthesized id can never coincide with a real
provider-computed one by accident."""

_SYMBOL_DESCRIPTOR_PUNCTUATION: Final = ("().", "#")
"""Structural, syntax-shape markers a *decorated* symbol name may carry
-- never a provider name or identifier. Deliberately generic ("symbol-
descriptor punctuation"), not "SCIP's syntax": none of `().`/`#` is a
character Python's own identifier grammar permits, so this is a safe,
provider-agnostic structural test ("does this string look like a bare
identifier"), not knowledge of any one provider's naming convention --
`_choose_symbol_name`'s own docstring explains why this stays a
structural check, never `if provider == "scip"`."""


def _looks_decorated(name: str) -> bool:
    """Whether `name` carries recognizable symbol-descriptor punctuation
    (`_SYMBOL_DESCRIPTOR_PUNCTUATION`) rather than reading as a bare
    identifier. Used only by `_choose_symbol_name`'s undecorated-name
    preference -- never by matching/convergence, which stays keyed on
    `(repository_id, repository_revision, base_type, file_path,
    start_line)` exactly as before, completely unaffected by this."""
    return any(marker in name for marker in _SYMBOL_DESCRIPTOR_PUNCTUATION)


def _choose_symbol_name(base: RepositorySymbol, other: RepositorySymbol) -> str:
    """Merge-time `name` selection for a converging pair, symbol-level
    entities only (Symbol Identity & Name Normalization investigation,
    `docs/architecture-conformance-audit.md` -- the real `sourcegraph/
    scip-python` audit's `add`/`add().` case).

    `RepositorySymbol.name` is not part of canonical identity (HLRD §18
    never lists it among the fields "identity may incorporate" -- only
    `qualified_name`, `source_location`, `provider_ids`, `language`,
    `entity type` are) and this function never touches `canonical_id`/
    `qualified_name`/the convergence key itself: those keep the exact
    `_merge_pair` behavior they already had (`qualified_name` is still
    `base`'s, via `model_copy`'s untouched default). This only decides
    which raw provider's *display* string `name` becomes.

    Before this function existed, `name` silently inherited whichever
    raw entity happened to be `base` -- itself decided by comparing two
    unrelated SHA-256 `canonical_id` digests (`resolve_entities`'s own
    stable sort). That comparison is fully deterministic (same inputs,
    same winner, every run) but was never a *meaningful* rule: measured
    directly against the real `scip-python` audit's 407 genuine AST/SCIP
    symbol convergences, the split was 208/199 -- indistinguishable from
    chance, confirming there was no actual SCIP-favoring (or AST-
    favoring) design intent behind it, only which hash digest happened
    to sort first.

    The explicit rule this function applies instead, for symbol-level
    entities (`FUNCTION`/`METHOD`/`CLASS`) only:

    1. If `base.name == other.name`, nothing to decide -- return it.
    2. If exactly one of the two raw names is decorated with recognizable
       symbol-descriptor punctuation (`_looks_decorated`) and the other
       is not, the *undecorated* one wins -- a bare identifier is a
       strictly more usable search/display string than one carrying a
       provider's own internal encoding, and preferring it costs nothing
       (identity is untouched either way).
    3. If both are bare, both are decorated, or (a base type outside
       `FUNCTION`/`METHOD`/`CLASS`, e.g. FILE/DIRECTORY, or a source
       type mismatch this module's own invariants otherwise rule out)
       the check cannot confidently distinguish them, the existing
       deterministic canonical-ID tie-break is preserved exactly:
       `base`'s name, unchanged from before this function existed. This
       is not a fallback bug -- for FILE/DIRECTORY convergence (where
       both providers' names are already expected to agree, per this
       module's own longstanding assumption) and for genuinely
       ambiguous symbol cases alike, today's behavior is correct as-is
       and must not change.

    Never a provider-specific condition (`if provider == "scip"`):
    `_looks_decorated` is a purely structural test on the string itself,
    not a lookup of which adapter produced it -- a future provider whose
    own `name` field happens to carry unrelated punctuation gets the
    exact same treatment, and a future provider that emits clean bare
    names benefits automatically, with no code change here.
    """
    if base.base_type not in _SYMBOL_LOCATION_BASE_TYPES:
        return base.name
    if base.name == other.name:
        return base.name
    base_decorated = _looks_decorated(base.name)
    other_decorated = _looks_decorated(other.name)
    if base_decorated and not other_decorated:
        return other.name
    if other_decorated and not base_decorated:
        return base.name
    return base.name


class MatchReason(StrEnum):
    """Why two provider-reported entities were merged (directive Phase B §9, §11)."""

    EXACT_CANONICAL_ID = "EXACT_CANONICAL_ID"
    """Both providers already computed the identical `canonical_id`."""

    NORMALIZED_PATH_IDENTITY = "NORMALIZED_PATH_IDENTITY"
    """Different raw `canonical_id`s, but the same FILE/DIRECTORY once
    both `qualified_name`s are run through `normalize_repo_relative_path`
    -- a strong deterministic match, not a probabilistic one."""

    SYMBOL_LOCATION_IDENTITY = "SYMBOL_LOCATION_IDENTITY"
    """Different raw `canonical_id`s, different (provider-specific,
    incompatible) `name`/`qualified_name` schemes, but the same
    `(repository_id, repository_revision, base_type, file_path,
    start_line)` -- the D7/D9 convergence directive's approved key
    (additive, non-breaking: existing `EXACT_CANONICAL_ID`/
    `NORMALIZED_PATH_IDENTITY` behavior for every other entity is
    unchanged)."""


@dataclass(frozen=True)
class EntityMergeRecord:
    """One resolved entity's provenance trail, for auditability/tests."""

    canonical_id: str
    reason: MatchReason
    source_canonical_ids: tuple[str, ...]
    """Every raw `canonical_id` (deduplicated, sorted) that converged onto
    this one -- length 1 for an entity no other provider also produced."""


@dataclass(frozen=True)
class EntityResolutionResult:
    entities: tuple[RepositorySymbol, ...]
    """Deterministically ordered (by `canonical_id`) resolved entities --
    one per real repository entity, never one row per raw provider entity."""

    merges: tuple[EntityMergeRecord, ...]
    """One record per resolved entity, in the same order as `entities`."""


def _normalized_identity_key(entity: RepositorySymbol) -> tuple[str, str, str, str, str] | None:
    """The comparison key used for `NORMALIZED_PATH_IDENTITY` matching, or
    ``None`` for a base type this module does not renormalize (directive
    Phase B §8: only use signals the architecture actually supports)."""
    if entity.base_type not in _PATH_SHAPED_BASE_TYPES:
        return None
    return (
        entity.repository_id,
        entity.repository_revision,
        entity.base_type.value,
        entity.language or "",
        normalize_repo_relative_path(entity.qualified_name),
    )


def _recompute_canonical_id(entity: RepositorySymbol, normalized_path: str) -> str:
    return build_canonical_id(
        repository_id=entity.repository_id,
        repository_revision=entity.repository_revision,
        qualified_name=normalized_path,
        base_type=entity.base_type,
        language=entity.language,
    )


def _symbol_location_identity_key(
    entity: RepositorySymbol,
) -> tuple[str, str, str, str, int] | None:
    """The approved D7/D9 convergence key, or ``None`` when this entity is
    not eligible: a base type outside `_SYMBOL_LOCATION_BASE_TYPES`, or
    (any eligible base type, but) no `source_location` to key on --
    trusting a provider's own id as-is when there is nothing safe to
    compare, exactly this module's existing principle for the path-shaped
    key. `start_line` is read directly from `SourceLocation` -- already
    0-based per its own established convention (`codex.ontology.entities.
    SourceLocation`), which every current populator (`SCIPAdapter`,
    `AstCallsAdapter` after its own line-numbering fix) now emits
    correctly.
    """
    if entity.base_type not in _SYMBOL_LOCATION_BASE_TYPES:
        return None
    location = entity.source_location
    if location is None:
        return None
    return (
        entity.repository_id,
        entity.repository_revision,
        entity.base_type.value,
        location.file_path,
        location.start_line,
    )


def _recompute_symbol_canonical_id(
    entity: RepositorySymbol, key: tuple[str, str, str, str, int]
) -> str:
    """Builds `build_canonical_id`'s `qualified_name` input purely from
    the symbol-location key's own components -- never from `entity.
    qualified_name` (the two providers' own qualified-name schemes are
    incompatible strings, exactly what this key avoids depending on).
    The *result* entity keeps its own real `qualified_name` unchanged
    (only `canonical_id` is overwritten on the candidate before merge)
    -- `_merge_pair`'s existing "base's value wins" tie-break then picks
    one real, meaningful provider qualified_name for display, never this
    synthesized string."""
    _, _, _, file_path, start_line = key
    synthetic_qualified_name = f"{_SYMBOL_LOCATION_TAG}:{file_path}:{start_line}"
    return build_canonical_id(
        repository_id=entity.repository_id,
        repository_revision=entity.repository_revision,
        qualified_name=synthetic_qualified_name,
        base_type=entity.base_type,
        language=entity.language,
    )


def _merge_pair(base: RepositorySymbol, other: RepositorySymbol) -> RepositorySymbol:
    """Combine two provider-reported entities already established to be the
    same real-world entity (directive Phase B §10: never destroy provider
    provenance during resolution).

    - ``roles``: union, first-seen order preserved (both directions).
    - ``provider_ids``: dict union; a genuine key collision with
      *different* values is a real conflicting-identity signal (directive
      §9) -- kept as ``base``'s value (deterministic: `base` is always
      the earlier-processed entity in `resolve()`'s stable ordering) and
      surfaced via ``merges`` for anyone auditing the run, never silently
      overwritten in a way that depends on dict iteration order.
    - ``lifecycle_status``: only `GitAdapter` currently reports a
      non-default value (ACTIVE/DELETED/RENAMED) -- Git is the only
      provider that observes file lifecycle at all, so a non-ACTIVE
      status from either side wins over a plain (default) ACTIVE from
      the other, since ACTIVE-by-default carries no information.
    - ``source_location``: first non-``None`` wins (no current provider
      pair produces two *different* locations for the same converged
      entity -- see the D5 closure audit, §J -- so this tie-break is
      currently never exercised by real data, only documented for
      determinism).
    - ``language``/``qualified_name``: first non-empty/`base`'s value
      wins, exactly as always -- untouched by this function's `name`
      change below; these are expected to already agree when entities
      converge (they're part of, or derived from, the same identity key).
    - ``name``: `base`'s value wins **except** for symbol-level entities
      (`FUNCTION`/`METHOD`/`CLASS`) where exactly one raw name is bare
      and the other carries recognizable symbol-descriptor punctuation
      (`_choose_symbol_name`) -- the bare one wins instead, regardless of
      which side is `base`. `canonical_id`/`qualified_name`/the
      convergence key are never touched by this: `name` is not part of
      canonical identity (HLRD §18) and this is a pure display/search-
      string choice. FILE/DIRECTORY `name` selection is unaffected.
    """
    merged_roles = list(base.roles)
    for role in other.roles:
        if role not in merged_roles:
            merged_roles.append(role)

    merged_provider_ids = dict(other.provider_ids)
    merged_provider_ids.update(base.provider_ids)  # base wins on key collision, deterministically

    lifecycle = base.lifecycle_status
    if lifecycle is LifecycleStatus.ACTIVE and other.lifecycle_status is not LifecycleStatus.ACTIVE:
        lifecycle = other.lifecycle_status

    source_location = (
        base.source_location if base.source_location is not None else other.source_location
    )

    return base.model_copy(
        update={
            "roles": merged_roles,
            "provider_ids": merged_provider_ids,
            "lifecycle_status": lifecycle,
            "source_location": source_location,
            "language": base.language or other.language,
            "name": _choose_symbol_name(base, other),
        }
    )


def resolve_entities(entities: list[RepositorySymbol]) -> EntityResolutionResult:
    """Deterministically resolve a batch of provider-reported entities.

    For a path-shaped entity (FILE/DIRECTORY), the *target* identity is
    always the canonical id recomputed from its **normalized** path --
    never whichever raw provider entity happens to sort first -- so the
    result is independent of provider/processing order even when two
    providers' raw `qualified_name` strings differ only by formatting
    (directive Phase B §13/§28: deterministic, order-independent
    resolution). A raw entity whose own `qualified_name` was already in
    canonical form recomputes to the *same* id (tagged
    `EXACT_CANONICAL_ID`); one that wasn't recomputes to a *different*
    id than it started with (tagged `NORMALIZED_PATH_IDENTITY`) and is
    filed under the normalized id, with its original raw id preserved in
    ``EntityMergeRecord.source_canonical_ids`` for audit. Non-path-shaped
    entities (symbols, external libraries) are never renormalized on this
    axis -- their own provider-computed id is trusted as-is here.

    For a symbol-level entity (`FUNCTION`/`METHOD`/`CLASS`) with a
    `source_location`, a *second*, independent identity key applies (the
    D7/D9 convergence directive's approved strategy): `(repository_id,
    repository_revision, base_type, file_path, start_line)`. Like path
    normalization, this key is applied *unconditionally* -- every
    eligible entity recomputes to its synthesized id (tagged
    `SYMBOL_LOCATION_IDENTITY`) whether or not a merge partner is present
    in this exact batch, so a provider's own first-ever commit of a
    symbol already receives its final id and that provider's own
    evidence (committed in the same call) is never at risk of going
    stale when a second provider's matching entity is committed later
    (see the module docstring's "Applied unconditionally" section for
    the real cross-batch evidence-loss bug this prevents). When two or
    more raw entities do share a key, they merge via the same
    `_merge_pair` provenance-preserving logic the path-shaped key
    already uses -- no second merge mechanism, only a second key; a
    still-uncorroborated symbol simply resolves to a single-source
    `EntityMergeRecord` under its new id.

    O(N): one dict-keyed resolution pass, no counting pre-pass, no
    all-pairs comparison.
    """
    ordered = sorted(enumerate(entities), key=lambda pair: (pair[1].canonical_id, pair[0]))

    by_target_id: dict[str, RepositorySymbol] = {}
    sources: dict[str, set[str]] = {}
    reasons: dict[str, MatchReason] = {}

    for _, entity in ordered:
        normalized_key = _normalized_identity_key(entity)
        symbol_key = _symbol_location_identity_key(entity)
        if normalized_key is not None:
            normalized_path = normalized_key[-1]
            target_id = _recompute_canonical_id(entity, normalized_path)
            if target_id == entity.canonical_id:
                candidate = entity
                reason = MatchReason.EXACT_CANONICAL_ID
            else:
                candidate = entity.model_copy(
                    update={"canonical_id": target_id, "qualified_name": normalized_path}
                )
                reason = MatchReason.NORMALIZED_PATH_IDENTITY
        elif symbol_key is not None:
            target_id = _recompute_symbol_canonical_id(entity, symbol_key)
            candidate = entity.model_copy(update={"canonical_id": target_id})
            reason = MatchReason.SYMBOL_LOCATION_IDENTITY
        else:
            target_id = entity.canonical_id
            candidate = entity
            reason = MatchReason.EXACT_CANONICAL_ID

        existing = by_target_id.get(target_id)
        if existing is not None:
            by_target_id[target_id] = _merge_pair(existing, candidate)
            sources[target_id].add(entity.canonical_id)
            renaming_reasons = (
                MatchReason.NORMALIZED_PATH_IDENTITY,
                MatchReason.SYMBOL_LOCATION_IDENTITY,
            )
            if reason in renaming_reasons:
                reasons[target_id] = reason
        else:
            by_target_id[target_id] = candidate
            sources[target_id] = {entity.canonical_id}
            reasons[target_id] = reason

    resolved_ids = sorted(by_target_id)
    resolved_entities = tuple(by_target_id[cid] for cid in resolved_ids)
    merges = tuple(
        EntityMergeRecord(
            canonical_id=cid,
            reason=reasons[cid],
            source_canonical_ids=tuple(sorted(sources[cid])),
        )
        for cid in resolved_ids
    )
    return EntityResolutionResult(entities=resolved_entities, merges=merges)


__all__ = [
    "EntityMergeRecord",
    "EntityResolutionResult",
    "MatchReason",
    "resolve_entities",
]
