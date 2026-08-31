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
  METHOD, ...) are currently produced by `SCIPAdapter` alone — there is
  no second provider to converge symbol-level identity against yet, so
  "cross-provider symbol convergence" has no live case today (recorded
  honestly below, not silently assumed to work).
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
  `qualified_name` verbatim (path normalization, FILE/DIRECTORY only),
  and always recomputes the final id through the same, unmodified
  `build_canonical_id` function.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

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


class MatchReason(StrEnum):
    """Why two provider-reported entities were merged (directive Phase B §9, §11)."""

    EXACT_CANONICAL_ID = "EXACT_CANONICAL_ID"
    """Both providers already computed the identical `canonical_id`."""

    NORMALIZED_PATH_IDENTITY = "NORMALIZED_PATH_IDENTITY"
    """Different raw `canonical_id`s, but the same FILE/DIRECTORY once
    both `qualified_name`s are run through `normalize_repo_relative_path`
    -- a strong deterministic match, not a probabilistic one."""


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
    - ``language``/``name``/``qualified_name``: first non-empty/`base`'s
      value wins; these are expected to already agree when entities
      converge (they're part of, or derived from, the same identity key).
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
    entities (symbols, external libraries) are never renormalized --
    their own provider-computed id is trusted as-is.

    O(N): one dict keyed by the target id, no all-pairs comparison.
    """
    ordered = sorted(enumerate(entities), key=lambda pair: (pair[1].canonical_id, pair[0]))

    by_target_id: dict[str, RepositorySymbol] = {}
    sources: dict[str, set[str]] = {}
    reasons: dict[str, MatchReason] = {}

    for _, entity in ordered:
        normalized_key = _normalized_identity_key(entity)
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
        else:
            target_id = entity.canonical_id
            candidate = entity
            reason = MatchReason.EXACT_CANONICAL_ID

        existing = by_target_id.get(target_id)
        if existing is not None:
            by_target_id[target_id] = _merge_pair(existing, candidate)
            sources[target_id].add(entity.canonical_id)
            if reason is MatchReason.NORMALIZED_PATH_IDENTITY:
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
