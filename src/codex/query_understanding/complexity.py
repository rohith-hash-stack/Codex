"""Query complexity (TAD §26; directive D8 Phase 9).

``C = Σ(weight_i × normalized_factor_i)``, TAD §26's V1 weights, exact.
"""

from __future__ import annotations

from typing import Final

from codex.query_understanding.models import ComplexityFactors

COMPLEXITY_WEIGHTS: Final[dict[str, float]] = {
    "intent_count": 0.25,
    "target_count": 0.15,
    "relationship_depth": 0.25,
    "ambiguity": 0.15,
    "temporal_dimension": 0.10,
    "reasoning_requirement": 0.10,
}
"""TAD §26's V1 weights, verbatim. Sum verified to equal 1.0 by a
dedicated test (`tests/test_qu_complexity.py`), not merely asserted."""

_INTENT_COUNT_CAP: Final = 5
"""TAD gives no explicit cap value for `intent_count` normalization; the
gap-closure precedent (documented calibration points, e.g. ADR-018's
freshness half-life) applies here too. Directive D8's own Phase 9 text
states the cap explicitly ("5 intents = 1.0, values above the cap are
capped at 1.0"), so this one *is* directive-specified, not invented."""


def normalize_intent_count(count: int) -> float:
    """ "5 intents = 1.0", values above the cap capped at 1.0 (directive
    D8 Phase 9, restating what the caller must already know as policy —
    TAD §26 names the factor but not this specific cap value)."""
    if count < 0:
        raise ValueError(f"intent_count must be >= 0, got {count}")
    return min(count / _INTENT_COUNT_CAP, 1.0)


def compute_complexity(factors: ComplexityFactors) -> float:
    """TAD §26's exact weighted sum. ``ComplexityFactors`` already
    validates each input to ``[0,1]`` (pydantic `Field(ge=0.0, le=1.0)`),
    so the weighted sum is structurally bounded to ``[0,1]`` too (a
    convex combination of values in ``[0,1]`` with weights summing to
    ``1.0`` stays in ``[0,1]`` by construction) — no separate clamp
    needed, verified by a dedicated boundary test rather than assumed.
    """
    return (
        COMPLEXITY_WEIGHTS["intent_count"] * factors.intent_count
        + COMPLEXITY_WEIGHTS["target_count"] * factors.target_count
        + COMPLEXITY_WEIGHTS["relationship_depth"] * factors.relationship_depth
        + COMPLEXITY_WEIGHTS["ambiguity"] * factors.ambiguity
        + COMPLEXITY_WEIGHTS["temporal_dimension"] * factors.temporal_dimension
        + COMPLEXITY_WEIGHTS["reasoning_requirement"] * factors.reasoning_requirement
    )


__all__ = ["COMPLEXITY_WEIGHTS", "compute_complexity", "normalize_intent_count"]
