"""Evidence Reconciliation (TAD §16, §18, §38; post-D7 directive Phase C).

TAD component #7's second half (Entity Resolution, `codex.resolution`,
is the first). Computes a deterministic `CanonicalRelationship.status`/
`.confidence`/`.contradiction_score` from the raw `Evidence` records
attached to a relationship key — the step `IngestionPipeline` (D4)
deliberately left at `UNRESOLVED`/`0.0` because, in its own words, "no
real Reconciliation Engine exists yet." This module is that engine.

**Pre-audit findings (directive Phase C §14), from direct inspection of
`codex.evidence.model` and `codex.ontology.relationships` before writing
any reconciliation logic:**

- `CanonicalRelationship` already has `supporting_evidence_ids` and
  `contradicting_evidence_ids` (TAD §73) — this module populates both,
  it does not invent the shape.
- `EvidenceStatus` (TAD §18) is `SUPPORTED / WEAKLY_SUPPORTED / DISPUTED
  / UNRESOLVED / CONTRADICTED / UNSUPPORTED`. **This is a different,
  separate enum from TAD §50's `VERIFIED / PARTIALLY_VERIFIED /
  QUALIFIED / DISPUTED / INCONCLUSIVE / REJECTED`**, which is the much
  later Verification Engine's answer/claim-level taxonomy (downstream of
  LLM synthesis, TAD §50, component #9/DTD-05) — confirmed by reading
  both enum definitions directly in `docs/TAD.md` (§18 at line ~539,
  §50 at line ~1338) rather than assumed. The two happen to share the
  literal string "DISPUTED" and both have six members, which invites
  conflation, but they are unrelated fields on unrelated objects
  (`CanonicalRelationship.status` vs. a future `VerificationResult`).
  This module uses **only** `EvidenceStatus` — TAD §50's taxonomy is out
  of scope here and untouched, exactly as the directive's own §23
  ("do not implement the Coverage/Verification Engine prematurely")
  requires.
- `Evidence.effective_independence_group` (TAD §16) already implements
  the "omitted group defaults to non-independent *within the same
  provider*" rule correctly — confirmed by reading TAD §16's own two-part
  text ("default: `independence_group = provider_default_family`"; "if
  omitted, evidence SHALL be treated as non-independent") together, not
  in isolation. Reused as-is, not reimplemented.
- **`provider_authority` (TAD §38's own contradiction-score formula
  input) has no defined source anywhere in HLRD/TAD** — the same kind of
  formula-references-an-unsourced-factor gap ADR-018 already resolved
  for `evidence_quality`/`cost_factor` (TAD §31). Resolved the same way:
  explicit, per-provider, `[0,1]`-normalized, supplied by the caller —
  but **not** added to `codex.registry.scoring.ProviderScoreProfile`
  (that type is scoped to TAD §31's formula specifically and is closed,
  D2/ADR-018; extending it would mean updating every existing call site
  across D2/D3/D5/D6 tests for a factor those tests have no reason to
  care about). Instead this module and `IngestionPipeline` accept a
  separate `provider_authority: Mapping[str, float]` parameter. Unlike
  ADR-018's "no default, must be explicit" choice — appropriate there
  because it fed a provider *selection* decision — a missing entry here
  defaults to full trust (`1.0`): this factor only *weights* confidence
  on an already-committed relationship, so silently under-trusting a
  provider nobody configured would be a worse default than silently
  fully trusting it, and defaulting to full trust keeps every existing
  D1-D6 test passing unchanged (none of them configure authority today).
- **No negation mechanism exists in the ontology.** `RelationshipType`
  (`codex/ontology/relationships.py`) has no "does-not-X" counterpart to
  any predicate, and `Evidence` has no boolean/flag field for asserting
  a negative fact. The directive's own DISPUTED example ("SCIP → CALLS,
  Provider-X → DOES_NOT_CALL") is therefore **not representable** by any
  current provider — confirmed by inspecting all three adapters'
  `RelationshipType` usage (D5/D6 closure audits, §J). This module still
  implements the full TAD §38 formula and all six `EvidenceStatus`
  values generically (proven with handcrafted evidence in tests), but
  with the real Git/SCIP/CodeQL provider set, `contradicting` evidence
  for a given relationship key is always empty in practice today — an
  intentional limitation, documented here and in the audit
  (`docs/architecture-conformance-audit.md` §N), not silently assumed to
  work end-to-end with real data it cannot currently receive.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import UTC, datetime
from typing import Final

from codex.evidence.model import CanonicalRelationship, Evidence, EvidenceStatus
from codex.ontology.relationships import RelationshipType
from codex.registry.scoring import default_freshness_score

SUPPORTED_CONFIDENCE_THRESHOLD: Final = 0.5
"""Calibration point (not derived from HLRD/TAD, which give no numeric
threshold): the aggregate support weight above which a relationship with
no contradicting evidence counts as `SUPPORTED` rather than
`WEAKLY_SUPPORTED`. Mirrors ADR-018's own framing of its freshness
half-life -- an explicit, documented default, not a claimed-final value."""

CONTRADICTED_SCORE_THRESHOLD: Final = 0.5
"""Calibration point: the TAD §38 `contradiction_score` above which a
relationship with evidence on both sides counts as `CONTRADICTED` rather
than merely `DISPUTED` (contradiction dominates support)."""


def _evidence_weight(
    evidence: Evidence, provider_authority: Mapping[str, float], *, now: datetime
) -> float:
    """``evidence_confidence x provider_authority`` (TAD §38), additionally
    discounted by freshness decay (directive Phase C §17/§22's "stale
    evidence... affects coverage/confidence") via the already-established
    `default_freshness_score` (D2/ADR-018) -- reused, not reinvented, for
    the same reason evidence-level staleness and provider-level staleness
    are the same underlying concept (age since observation)."""
    authority = provider_authority.get(evidence.provider, 1.0)
    staleness = default_freshness_score(evidence.freshness, now=now)
    return evidence.confidence * authority * staleness


def _combine_independent(
    evidence: Collection[Evidence], provider_authority: Mapping[str, float], *, now: datetime
) -> float:
    """Independence-aware confidence combination (directive Phase C §16,
    §22's "multiple independent supporting providers -> confidence may
    increase" / "multiple correlated providers -> must not be counted as
    fully independent").

    Evidence sharing an `effective_independence_group` collapses to its
    single highest weight first (the same source asserting the same fact
    twice must not compound). Independent groups then combine via
    ``1 - product(1 - weight_i)`` -- a standard, deterministic
    probability-combination rule (not a learned model, not the entity-
    identity mechanism the directive's "no probabilistic matching"
    prohibition targets): monotonic, bounded in [0,1], and every group's
    contribution can only raise confidence, matching "more independent
    corroboration never decreases it."
    """
    by_group: dict[str, float] = {}
    for record in evidence:
        weight = _evidence_weight(record, provider_authority, now=now)
        group = record.effective_independence_group
        by_group[group] = max(by_group.get(group, 0.0), weight)
    if not by_group:
        return 0.0
    product = 1.0
    for weight in by_group.values():
        product *= 1.0 - weight
    return 1.0 - product


def reconcile_relationship(
    subject: str,
    predicate: RelationshipType,
    obj: str,
    *,
    supporting: Collection[Evidence],
    contradicting: Collection[Evidence] = (),
    provider_authority: Mapping[str, float] | None = None,
    known_entity_ids: Collection[str],
    now: datetime | None = None,
) -> CanonicalRelationship:
    """Deterministically reconcile one relationship key's evidence.

    ``known_entity_ids`` distinguishes a genuine information/system gap
    from a real dispute (directive Phase C §19): a relationship whose
    subject or object doesn't correspond to any currently-resolved entity
    is `UNRESOLVED`, checked *before* any confidence/contradiction math --
    "source entity exists, target entity missing" is not evidence of
    anything, it's missing structure.
    """
    reference_time = now if now is not None else datetime.now(UTC)
    authority = provider_authority or {}
    supporting = list(supporting)
    contradicting = list(contradicting)

    supporting_ids = sorted({e.evidence_id for e in supporting})
    contradicting_ids = sorted({e.evidence_id for e in contradicting})

    if subject not in known_entity_ids or obj not in known_entity_ids:
        return CanonicalRelationship(
            subject=subject,
            predicate=predicate,
            object=obj,
            status=EvidenceStatus.UNRESOLVED,
            confidence=0.0,
            contradiction_score=0.0,
            supporting_evidence_ids=supporting_ids,
            contradicting_evidence_ids=contradicting_ids,
        )

    support_sum = sum(
        _evidence_weight(e, authority, now=reference_time) for e in supporting
    )
    contradict_sum = sum(
        _evidence_weight(e, authority, now=reference_time) for e in contradicting
    )
    denominator = support_sum + contradict_sum
    contradiction_score = contradict_sum / denominator if denominator > 0.0 else 0.0

    support_weight = _combine_independent(supporting, authority, now=reference_time)

    if supporting and contradicting:
        status = (
            EvidenceStatus.CONTRADICTED
            if contradiction_score >= CONTRADICTED_SCORE_THRESHOLD
            else EvidenceStatus.DISPUTED
        )
    elif contradicting and not supporting:
        status = EvidenceStatus.UNSUPPORTED
    elif supporting and not contradicting:
        status = (
            EvidenceStatus.SUPPORTED
            if support_weight >= SUPPORTED_CONFIDENCE_THRESHOLD
            else EvidenceStatus.WEAKLY_SUPPORTED
        )
    else:
        status = EvidenceStatus.UNRESOLVED

    confidence = (
        0.0
        if status is EvidenceStatus.UNRESOLVED
        else support_weight * (1.0 - contradiction_score)
    )

    return CanonicalRelationship(
        subject=subject,
        predicate=predicate,
        object=obj,
        status=status,
        confidence=confidence,
        contradiction_score=contradiction_score,
        supporting_evidence_ids=supporting_ids,
        contradicting_evidence_ids=contradicting_ids,
    )


__all__ = [
    "CONTRADICTED_SCORE_THRESHOLD",
    "SUPPORTED_CONFIDENCE_THRESHOLD",
    "reconcile_relationship",
]
