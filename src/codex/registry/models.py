"""Capability Registry result types (TAD §10, §31; directive D2)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from codex.provider.capability import Capability
from codex.provider.contract import ProviderEligibility, ProviderHealthStatus, ValidationResult


class ProviderEvaluationStatus(StrEnum):
    """Live, per-repository usability of one provider for one capability.

    Every value here presumes ``capability_match`` already passed —
    the Phase D directive's sixth named status, "SUPPORTED", is a
    *static* claim ("this provider declares the capability") and
    corresponds to ``CapabilityRegistry.providers_for()``'s result
    set, a separate, cheaper query. It is not a member of this enum
    because no live evaluation ever needs to report it: a provider
    reaches this classification only after already passing that check.
    """

    AVAILABLE = "AVAILABLE"
    """Eligible, validated, healthy, and fully available (availability == 1.0)."""

    PARTIAL = "PARTIAL"
    """Usable, but not fully: availability in (0.0, 1.0), or healthy-but-DEGRADED/UNKNOWN
    at full availability."""

    UNAVAILABLE = "UNAVAILABLE"
    """Eligible and validated, but unusable right now: UNHEALTHY, or availability == 0.0."""

    INELIGIBLE = "INELIGIBLE"
    """``check_eligibility()`` reports this provider may not be used for this repository."""

    FAILED = "FAILED"
    """``validate()`` reports the adapter's own environment/configuration is broken."""


class ProviderEvaluation(BaseModel):
    """One provider's live evaluation for one capability/repository (TAD §10, §31)."""

    provider_name: str
    provider_version: str
    capability: Capability
    status: ProviderEvaluationStatus
    availability: float
    health_status: ProviderHealthStatus
    eligibility: ProviderEligibility
    validation: ValidationResult
    score: float | None = None
    """Populated only by ``CapabilityRegistry.rank()`` — ``None`` from ``evaluate()`` alone."""
