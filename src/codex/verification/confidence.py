"""Verification Confidence (TAD §48; directive D10.4, D10 Decision 1).

TAD §48's exact six-factor formula and exact `V = min(V, 0.50)` cap.
The two numeric contradiction thresholds (`>0.60` significant, `<0.40`
weak) are **D10 Decision 1** -- explicit V1 calibration constants the
user has approved, not present in TAD's own qualitative text
("significant"/"weak"), swappable from benchmark telemetry later, never
silently invented.

**Do not retrieve additional evidence behind the verifier's back**
(directive D10.4): every factor here is computed from what
`EvidencePackage`/`EntailmentResult` already carry -- no fresh graph or
evidence-store query.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, Field

from codex.coverage.engine import CapabilityCoverage
from codex.evidence.model import CanonicalRelationship, Evidence
from codex.planner.mss import EvidencePackage
from codex.registry.scoring import default_freshness_score
from codex.verification.entailment import EntailmentResult, EntailmentStatus

CONTRADICTION_SIGNIFICANT_THRESHOLD: Final[float] = 0.60
"""D10 Decision 1: evidence_confidence strictly above this is
"significant" contradiction (TAD §48). Swappable calibration point."""

CONTRADICTION_WEAK_THRESHOLD: Final[float] = 0.40
"""D10 Decision 1: evidence_confidence strictly below this is "weak"
contradiction (TAD §48). Swappable calibration point."""

CONTRADICTION_CAP: Final[float] = 0.50
"""TAD §48's own exact literal value: `V = min(V, 0.50)`."""

V_WEIGHTS: Final[dict[str, float]] = {
    "evidence_support": 0.35,
    "evidence_quality": 0.20,
    "evidence_independence": 0.15,
    "coverage": 0.10,
    "freshness": 0.10,
    "provider_authority": 0.10,
}
"""TAD §48's own exact weights."""

_COVERAGE_FACTOR_SCORE: Final[dict[CapabilityCoverage, float]] = {
    CapabilityCoverage.COMPLETE: 1.0,
    CapabilityCoverage.EMPTY_SUCCESS: 1.0,
    CapabilityCoverage.PARTIAL: 0.5,
    CapabilityCoverage.FAILED: 0.0,
    CapabilityCoverage.UNAVAILABLE: 0.0,
    CapabilityCoverage.NOT_SUPPORTED: 0.0,
}
"""A documented calibration point **local to Verification's own
`coverage` factor** -- distinct from, and not a resolution of, TAD
§33's still-open LOW/MEDIUM/HIGH percentage-denominator gap (which
this module does not touch). `codex.coverage`'s own six-value
classification already exists (D9); this is the minimal, honest
[0,1] mapping needed to fold it into TAD §48's formula."""


class ContradictionLevel(StrEnum):
    """D10 Decision 1's three-way classification of a matched
    relationship's `contradiction_score` (TAD §38, already computed by
    `codex.reconciliation`)."""

    NONE = "NONE"
    WEAK = "WEAK"
    INTERMEDIATE = "INTERMEDIATE"
    """"0.40-0.60: treat as intermediate/uncertain rather than silently
    classifying it as strong or weak" (D10 Decision 1, verbatim)."""

    SIGNIFICANT = "SIGNIFICANT"


def classify_contradiction(contradiction_score: float) -> ContradictionLevel:
    if contradiction_score <= 0.0:
        return ContradictionLevel.NONE
    if contradiction_score < CONTRADICTION_WEAK_THRESHOLD:
        return ContradictionLevel.WEAK
    if contradiction_score > CONTRADICTION_SIGNIFICANT_THRESHOLD:
        return ContradictionLevel.SIGNIFICANT
    return ContradictionLevel.INTERMEDIATE


class VerificationFactors(BaseModel):
    """TAD §48's six named factors, each independently normalized to `[0,1]`."""

    evidence_support: float = Field(ge=0.0, le=1.0)
    evidence_quality: float = Field(ge=0.0, le=1.0)
    evidence_independence: float = Field(ge=0.0, le=1.0)
    coverage: float = Field(ge=0.0, le=1.0)
    freshness: float = Field(ge=0.0, le=1.0)
    provider_authority: float = Field(ge=0.0, le=1.0)


_ZERO_FACTORS = VerificationFactors(
    evidence_support=0.0,
    evidence_quality=0.0,
    evidence_independence=0.0,
    coverage=0.0,
    freshness=0.0,
    provider_authority=0.0,
)


def _matched_relationships(entailment: EntailmentResult) -> list[CanonicalRelationship]:
    if entailment.matched_relationship is not None:
        return [entailment.matched_relationship]
    return entailment.matched_path


def _underlying_evidence(
    relationships: list[CanonicalRelationship], package: EvidencePackage
) -> list[Evidence]:
    evidence_by_id = {e.evidence_id: e for e in package.evidence}
    ids: set[str] = set()
    for rel in relationships:
        ids.update(rel.supporting_evidence_ids)
    return [evidence_by_id[i] for i in ids if i in evidence_by_id]


def compute_factors(
    entailment: EntailmentResult,
    package: EvidencePackage,
    *,
    provider_authority: Mapping[str, float] | None = None,
    now: datetime | None = None,
) -> VerificationFactors:
    """TAD §48's six factors, computed entirely from `entailment`/
    `package` -- no fresh evidence retrieval."""
    if entailment.status is EntailmentStatus.UNRESOLVED:
        return _ZERO_FACTORS

    relationships = _matched_relationships(entailment)
    evidence = _underlying_evidence(relationships, package)
    reference_time = now or datetime.now(UTC)
    authority = provider_authority or {}

    if not evidence:
        # A matched relationship/path exists (structural support) but no
        # resolvable underlying Evidence record -- support is real but
        # unquantifiable; every evidence-derived factor is honestly 0.
        avg_contradiction = sum(r.contradiction_score for r in relationships) / len(relationships)
        return VerificationFactors(
            evidence_support=max(0.0, 1.0 - avg_contradiction),
            evidence_quality=0.0,
            evidence_independence=0.0,
            coverage=0.0,
            freshness=0.0,
            provider_authority=0.0,
        )

    avg_contradiction = sum(r.contradiction_score for r in relationships) / len(relationships)
    evidence_support = max(0.0, 1.0 - avg_contradiction)

    evidence_quality = sum(e.confidence for e in evidence) / len(evidence)

    distinct_groups = {e.effective_independence_group for e in evidence}
    evidence_independence = len(distinct_groups) / len(evidence)

    coverage_scores = [
        _COVERAGE_FACTOR_SCORE.get(coverage_status, 0.0)
        for coverage_status in package.coverage.values()
    ]
    coverage = sum(coverage_scores) / len(coverage_scores) if coverage_scores else 0.0

    freshness = sum(
        default_freshness_score(e.freshness, now=reference_time) for e in evidence
    ) / len(evidence)

    provider_authority_score = sum(authority.get(e.provider, 1.0) for e in evidence) / len(evidence)

    return VerificationFactors(
        evidence_support=evidence_support,
        evidence_quality=evidence_quality,
        evidence_independence=evidence_independence,
        coverage=coverage,
        freshness=freshness,
        provider_authority=provider_authority_score,
    )


def compute_confidence(
    factors: VerificationFactors, contradiction_level: ContradictionLevel
) -> float:
    """TAD §48's weighted sum, with the `V = min(V, 0.50)` cap applied
    only for `SIGNIFICANT` contradiction (D10 Decision 1). `WEAK`
    contradiction's "small penalty" is already applied naturally: any
    nonzero `contradiction_score` already reduced `evidence_support`
    above (`1.0 - avg_contradiction`) -- "the canonical verification
    formula requires it" only in that sense; no second, separately
    invented penalty constant is layered on top."""
    v = sum(V_WEIGHTS[name] * getattr(factors, name) for name in V_WEIGHTS)
    if contradiction_level is ContradictionLevel.SIGNIFICANT:
        v = min(v, CONTRADICTION_CAP)
    return v


__all__ = [
    "CONTRADICTION_CAP",
    "CONTRADICTION_SIGNIFICANT_THRESHOLD",
    "CONTRADICTION_WEAK_THRESHOLD",
    "V_WEIGHTS",
    "ContradictionLevel",
    "VerificationFactors",
    "classify_contradiction",
    "compute_confidence",
    "compute_factors",
]
