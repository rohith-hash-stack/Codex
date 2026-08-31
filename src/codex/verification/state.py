"""Verification State Mapping (TAD §50; directive D10.6).

TAD §50's canonical six-value taxonomy -- the **only** verification
taxonomy in Codex (directive: "Do not introduce a third/fourth
verification taxonomy"). Two pure mapping functions reproduce TAD
§50's own tables verbatim (already read directly from the current
`docs/TAD.md` text, not assumed) -- no new labels invented.

**`VerificationStatus` is not `EvidenceStatus`** (`codex.evidence.model`,
TAD §18): `EvidenceStatus.DISPUTED` is a *relationship*-level state,
already computed by the implemented Reconciliation Engine, from
independent evidence agreeing/disagreeing about one canonical edge.
`VerificationStatus.DISPUTED` is a *claim*-level state, computed here,
from an LLM's claim against deterministic entailment. They share a
literal name and nothing else -- never conflate one for the other, and
never import `EvidenceStatus` into this module as a shortcut for a
`VerificationStatus` value (`docs/architecture-conformance-audit.md`
§T.4 item 2).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from codex.verification.confidence import ContradictionLevel
from codex.verification.engine import ClaimVerification
from codex.verification.entailment import EntailmentStatus


class VerificationStatus(StrEnum):
    """TAD §50's canonical, singular internal verification model."""

    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    QUALIFIED = "QUALIFIED"
    DISPUTED = "DISPUTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    REJECTED = "REJECTED"


VERIFIED_CONFIDENCE_THRESHOLD: Final[float] = 0.50
"""Documented calibration point (TAD §50 gives no numeric VERIFIED
threshold): a structurally SUPPORTED, non-contradicted claim whose own
confidence is nonetheless weak (low evidence_quality/independence/
freshness/provider_authority) is INCONCLUSIVE, not VERIFIED --
deterministic support alone isn't "sufficient trusted evidence" (TAD
§50's own VERIFIED definition) if every other factor is near zero.
Symmetric with `CONTRADICTION_CAP`, swappable from telemetry later."""


def classify_claim(verification: ClaimVerification) -> VerificationStatus:
    """Per-claim classification. Only `VERIFIED`/`DISPUTED`/
    `INCONCLUSIVE` are assigned here -- `PARTIALLY_VERIFIED`/`QUALIFIED`
    are answer-level aggregates (TAD §50's own definitions: "**some**
    claims supported, others lack..." / "answer is usable but
    carries..."), never meaningful for a single claim in isolation;
    `REJECTED` is reserved for an explicit enforcement-rule failure
    (e.g. exhausted re-synthesis, D10.7/D10.8), not assigned here.
    """
    if verification.entailment.status is EntailmentStatus.UNRESOLVED:
        return VerificationStatus.INCONCLUSIVE
    if verification.contradiction_level is ContradictionLevel.SIGNIFICANT:
        return VerificationStatus.DISPUTED
    if verification.confidence >= VERIFIED_CONFIDENCE_THRESHOLD:
        return VerificationStatus.VERIFIED
    return VerificationStatus.INCONCLUSIVE


def classify_answer(
    retained_states: list[VerificationStatus], *, any_removed_for_contradiction: bool
) -> VerificationStatus:
    """Answer-level aggregate over the claims that survived D10.5's
    contradiction handling (`retained_states` must only ever contain
    `VERIFIED`/`INCONCLUSIVE` -- `DISPUTED` claims are removed before
    this point, `REJECTED` is assigned only here or by D10.8)."""
    if not retained_states:
        return (
            VerificationStatus.DISPUTED
            if any_removed_for_contradiction
            else VerificationStatus.INCONCLUSIVE
        )

    verified_count = sum(1 for s in retained_states if s is VerificationStatus.VERIFIED)
    if verified_count == len(retained_states):
        return (
            VerificationStatus.QUALIFIED
            if any_removed_for_contradiction
            else VerificationStatus.VERIFIED
        )
    if verified_count == 0:
        return VerificationStatus.INCONCLUSIVE
    return VerificationStatus.PARTIALLY_VERIFIED


_HLRD_LABEL: Final[dict[VerificationStatus, str]] = {
    VerificationStatus.VERIFIED: "FULLY_VERIFIED",
    VerificationStatus.PARTIALLY_VERIFIED: "PARTIALLY_VERIFIED",
    VerificationStatus.QUALIFIED: "PARTIALLY_VERIFIED",
    VerificationStatus.DISPUTED: "CONTRADICTED",
    VerificationStatus.INCONCLUSIVE: "UNVERIFIED",
    VerificationStatus.REJECTED: "CONTRADICTED",
}
"""TAD §50's "HLRD presentation mapping" table, verbatim."""

_ROUTING_BUCKET: Final[dict[VerificationStatus, str]] = {
    VerificationStatus.VERIFIED: "VERIFIED",
    VerificationStatus.PARTIALLY_VERIFIED: "QUALIFIED",
    VerificationStatus.QUALIFIED: "QUALIFIED",
    VerificationStatus.DISPUTED: "QUALIFIED",
    VerificationStatus.INCONCLUSIVE: "QUALIFIED",
    VerificationStatus.REJECTED: "ABSTAIN",
}
"""TAD §50's "Pipeline routing bucket" table, verbatim."""


def to_hlrd_label(status: VerificationStatus) -> str:
    """TAD §50: "implemented as tested pure functions when the
    Verification Engine is built, never re-derived ad hoc" -- this is
    that function, for HLRD §42's four presentation labels."""
    return _HLRD_LABEL[status]


def to_routing_bucket(status: VerificationStatus) -> str:
    """TAD §50's three-bucket routing view (TAD §5's pipeline diagram)."""
    return _ROUTING_BUCKET[status]


__all__ = [
    "VERIFIED_CONFIDENCE_THRESHOLD",
    "VerificationStatus",
    "classify_answer",
    "classify_claim",
    "to_hlrd_label",
    "to_routing_bucket",
]
