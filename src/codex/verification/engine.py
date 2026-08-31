"""Verification Engine (TAD §46; directive D10.4).

TAD §46's DTD-05 pipeline: claim extraction validation -> evidence
mapping -> entailment -> contradiction detection -> coverage
verification -> confidence -> answer decision. This module implements
the middle five steps for one claim at a time (`verify_claim`) and
their aggregate over a `StructuredAnswer` (`verify_claims`); claim
extraction validation is D10.2's schema (a malformed claim never
reaches this module at all); "answer decision" is D10.8.

Verification evaluates claims **against `EvidencePackage` only** --
never retrieves additional evidence, never re-queries the graph, never
asks the LLM to judge its own claim (directive D10.4/D10 Phase E).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from pydantic import BaseModel

from codex.llm.schema import Claim
from codex.planner.mss import EvidencePackage
from codex.verification.confidence import (
    ContradictionLevel,
    VerificationFactors,
    classify_contradiction,
    compute_confidence,
    compute_factors,
)
from codex.verification.entailment import EntailmentResult, entail_claim


class ClaimVerification(BaseModel):
    """One claim's full verification record -- entailment result,
    TAD §48 factors, computed confidence, and contradiction
    classification. Does **not** yet carry the canonical `VerificationStatus`
    (TAD §50) -- that mapping is D10.6, applied after D10.5's contradiction
    handling has had a chance to remove/replace this record."""

    claim: Claim
    entailment: EntailmentResult
    factors: VerificationFactors
    confidence: float
    contradiction_level: ContradictionLevel


def verify_claim(
    claim: Claim,
    package: EvidencePackage,
    *,
    provider_authority: Mapping[str, float] | None = None,
    now: datetime | None = None,
) -> ClaimVerification:
    """TAD §46's evidence mapping -> entailment -> contradiction
    detection -> coverage verification -> confidence, for one claim."""
    entailment = entail_claim(claim, package)
    factors = compute_factors(entailment, package, provider_authority=provider_authority, now=now)

    contradiction_score = 0.0
    if entailment.matched_relationship is not None:
        contradiction_score = entailment.matched_relationship.contradiction_score
    elif entailment.matched_path:
        contradiction_score = max(r.contradiction_score for r in entailment.matched_path)
    contradiction_level = classify_contradiction(contradiction_score)

    confidence = compute_confidence(factors, contradiction_level)

    return ClaimVerification(
        claim=claim,
        entailment=entailment,
        factors=factors,
        confidence=confidence,
        contradiction_level=contradiction_level,
    )


def verify_claims(
    claims: list[Claim],
    package: EvidencePackage,
    *,
    provider_authority: Mapping[str, float] | None = None,
    now: datetime | None = None,
) -> list[ClaimVerification]:
    """Deterministic, order-preserving verification of every claim in a
    `StructuredAnswer.claims` list."""
    return [
        verify_claim(claim, package, provider_authority=provider_authority, now=now)
        for claim in claims
    ]


def is_significantly_contradicted(verification: ClaimVerification) -> bool:
    """TAD §48's own rule, restated as a guard: a claim with
    significant contradiction can never be VERIFIED (directive D10.4:
    "Do not permit a claim with significant contradiction to become
    VERIFIED")."""
    return verification.contradiction_level is ContradictionLevel.SIGNIFICANT


__all__ = [
    "ClaimVerification",
    "is_significantly_contradicted",
    "verify_claim",
    "verify_claims",
]
