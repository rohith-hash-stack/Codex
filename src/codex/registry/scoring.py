"""ProviderScore formula (TAD §31; Phase D directive D2 objective 4).

TAD §31 fixes the aggregation weights but — like the rest of TAD §31 —
does not define how any of the five factors are individually computed;
it only states that each is normalized to ``[0.0, 1.0]`` and that a
provider with ``capability_match == 0`` is excluded before scoring.

``codex.registry.CapabilityRegistry`` can derive two of the five
factors itself directly from the (already-approved) ``ProviderAdapter``
contract: ``capability_match`` (1.0 for any provider returned by
``providers_for()``, since capability-mismatched providers are never
scored at all) and ``availability`` (already a ``[0.0, 1.0]`` float
per the D1 clarification). The other three — ``evidence_quality``,
a normalized ``freshness`` score, and ``cost_factor`` — have no
defined source anywhere in HLRD, TAD, the reconciliation documents, or
the D1 contract: there is no adapter property for the first or third,
and no staleness-to-score conversion policy for the second (the
adapter only exposes a raw timestamp). Rather than inventing a
formula for any of the three, ``CapabilityRegistry.rank()`` requires
them as explicit, per-provider caller-supplied inputs with no default.
See ``docs/architecture-conformance-audit.md`` for the full writeup.
"""

from __future__ import annotations

from typing import Final, NamedTuple

PROVIDER_SCORE_WEIGHTS: Final[dict[str, float]] = {
    "capability_match": 0.40,
    "evidence_quality": 0.20,
    "availability": 0.15,
    "freshness": 0.15,
    "cost_factor": 0.10,
}


class ProviderScoreInputs(NamedTuple):
    """The five TAD §31 factors, each normalized to ``[0.0, 1.0]``."""

    capability_match: float
    evidence_quality: float
    availability: float
    freshness: float
    cost_factor: float


def provider_score(inputs: ProviderScoreInputs) -> float:
    """TAD §31's weighted formula.

    A ``capability_match == 0`` input scores 0.0, matching the
    directive's exclusion rule — though ``CapabilityRegistry`` never
    calls this for such a provider in the first place; it filters
    those out before scoring, per directive point 4.
    """
    if inputs.capability_match == 0.0:
        return 0.0
    return (
        PROVIDER_SCORE_WEIGHTS["capability_match"] * inputs.capability_match
        + PROVIDER_SCORE_WEIGHTS["evidence_quality"] * inputs.evidence_quality
        + PROVIDER_SCORE_WEIGHTS["availability"] * inputs.availability
        + PROVIDER_SCORE_WEIGHTS["freshness"] * inputs.freshness
        + PROVIDER_SCORE_WEIGHTS["cost_factor"] * inputs.cost_factor
    )
