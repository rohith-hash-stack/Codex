"""ProviderScore formula and its input sourcing (TAD §31; ADR-018).

TAD §31 fixes the aggregation weights but does not define how any of
the five factors are individually computed. ADR-018 (resolved
2026-08-30, see ``docs/architecture-conformance-audit.md`` §I and
``docs/architecture-reconciliation.md``) closes that gap:

- ``capability_match`` — derived deterministically by the Registry
  from declared capability support (1.0 for any provider returned by
  ``providers_for()``; providers that don't declare the capability
  are excluded before scoring, never scored at 0.0).
- ``availability`` — derived by the Registry from
  ``ProviderAdapter.availability(capability, repository)`` (D1).
- ``evidence_quality`` and ``cost_factor`` — **not** computed by this
  module. Both must already be normalized ``[0.0, 1.0]`` provider
  capability/configuration metadata, supplied once via
  ``ProviderScoreProfile`` at registration time (not per query) — see
  ``CapabilityRegistry.register()``. No universal quality formula or
  monetary-cost normalization is invented here.
- ``freshness`` — derived by the Registry from the adapter's own
  ``freshness`` timestamp (D1) via ``default_freshness_score()``, a
  single generic (not provider-specific) exponential-decay default.
  This is explicitly a calibration point (ADR-018 point 4), not a
  claimed-final algorithm — a future revision may recalibrate or
  override its half-life without an architecture change.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final, NamedTuple

from pydantic import BaseModel, Field

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

    A ``capability_match == 0`` input scores 0.0, matching TAD §31's
    exclusion rule — though ``CapabilityRegistry`` never calls this
    for such a provider in the first place; it filters those out
    before scoring.
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


class ProviderScoreProfile(BaseModel):
    """Canonical, per-provider scoring metadata (ADR-018).

    Supplied once at ``CapabilityRegistry.register()`` time, not per
    ``rank()`` call — this is the whole point of ADR-018's resolution:
    ranking for a given (capability, repository) must be identical no
    matter which caller invokes ``rank()``. Both fields must already
    be normalized by whoever supplies them (a future provider
    contract/configuration layer, e.g. D3+ adapter setup) — the
    Registry does not compute or invent either value.
    """

    evidence_quality: float = Field(ge=0.0, le=1.0)
    cost_factor: float = Field(ge=0.0, le=1.0)


DEFAULT_FRESHNESS_HALF_LIFE: Final[timedelta] = timedelta(hours=24)
"""ADR-018 calibration point: how long until a provider's evidence is
considered half as fresh. Not derived from HLRD/TAD (neither defines
this) — a documented default so freshness participates in scoring
today rather than blocking on offline calibration (TAD §59, §66)."""


def default_freshness_score(
    freshness: datetime | None,
    *,
    now: datetime,
    half_life: timedelta = DEFAULT_FRESHNESS_HALF_LIFE,
) -> float:
    """Generic (provider-neutral) exponential-decay freshness normalization.

    ``freshness=None`` (never extracted) scores 0.0. A non-positive
    age (clock skew, or extraction that just completed) scores 1.0.
    Otherwise the score halves every ``half_life``.
    """
    if freshness is None:
        return 0.0
    age = now - freshness
    if age <= timedelta(0):
        return 1.0
    return 0.5 ** (age / half_life)
