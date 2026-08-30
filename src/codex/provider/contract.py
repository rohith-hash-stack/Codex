"""The ProviderAdapter contract (TAD §8-9, §11, §64; directive §11-13).

Every provider is an evidence *producer*, never a source of canonical
truth (TAD §8, §74): ``extract()`` returns adapter-specific raw
results, and only ``normalize()`` may translate them into canonical
``RepositorySymbol``/``Evidence`` objects. Provider-specific types
never cross that boundary (TAD invariant #2) — nothing outside a
single adapter's own ``extract()``/``normalize()`` pair ever inspects
``ExtractionResult.raw_payload``.

This module defines the contract only. No concrete adapter (Git,
SCIP, ...) is implemented here — see ``docs/architecture-conformance-
audit.md`` for the phased plan.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from codex.evidence.model import Evidence, EvidenceCohort, validate_raw_reference
from codex.ontology.entities import RepositorySymbol
from codex.provider.capability import Capability
from codex.repository.models import RepositoryMetadata


class ProviderHealthStatus(StrEnum):
    """Operational condition of the provider itself (TAD §9).

    Independent of ``availability``: a HEALTHY provider can still be
    unavailable for a given capability/repository (e.g. a missing
    license), and ``availability`` must never be derived from this
    value or vice versa (D1 clarification, 2026-08-30).
    """

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"


class ValidationResult(BaseModel):
    """Result of checking an adapter's own environment/configuration (TAD §9 ``validate()``)."""

    ok: bool
    problems: list[str] = Field(default_factory=list)


class EligibilityStatus(StrEnum):
    """Repo/license/environment gating, kept distinct from capability support (directive §11)."""

    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE_LICENSE = "INELIGIBLE_LICENSE"
    INELIGIBLE_REPOSITORY = "INELIGIBLE_REPOSITORY"
    INELIGIBLE_ENVIRONMENT = "INELIGIBLE_ENVIRONMENT"


class ProviderEligibility(BaseModel):
    """Whether this adapter may run against a specific repository right now.

    This is adapter-reported information only. Aggregating it into an
    overall SUPPORTED/AVAILABLE/UNAVAILABLE/INELIGIBLE registry state
    is the Capability Registry's job (D2), not this contract's.
    """

    status: EligibilityStatus
    reason: str | None = None

    @property
    def eligible(self) -> bool:
        return self.status is EligibilityStatus.ELIGIBLE


class ProviderFailureReason(StrEnum):
    """Distinguishes total provider failure from a per-capability failure (TAD §64)."""

    UNAVAILABLE = "UNAVAILABLE"
    """TAD §64 PROVIDER_UNAVAILABLE."""

    TIMEOUT = "TIMEOUT"
    """TAD §64 PROVIDER_TIMEOUT."""


class ProviderExtractionError(Exception):
    """Raised when a provider cannot run at all (TAD §64).

    Not used for a single failed/partial capability within an
    otherwise-successful run — that is expressed through
    ``EvidenceCohort.failed_capabilities``/``partial_capabilities``
    on a normally-returned ``ExtractionResult`` instead (directive §8).
    """

    def __init__(
        self, provider_name: str, reason: ProviderFailureReason, detail: str | None = None
    ) -> None:
        self.provider_name = provider_name
        self.reason = reason
        self.detail = detail
        super().__init__(f"{provider_name}: {reason.value}" + (f" ({detail})" if detail else ""))


class ExtractionResult(BaseModel):
    """Raw output of one ``extract()`` call (TAD §9, §17, §52)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    cohort: EvidenceCohort
    raw_reference: str | None = None
    raw_payload: Any = None
    """Adapter-specific; consumed only by this same adapter's ``normalize()``."""

    _validate_raw_reference = field_validator("raw_reference")(validate_raw_reference)


class NormalizedEvidence(BaseModel):
    """Canonical output of one ``normalize()`` call (TAD §11)."""

    entities: list[RepositorySymbol] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    cohort: EvidenceCohort


class ProviderAdapter(Protocol):
    """What every provider adapter implements (TAD §9).

    Configuration representation (how a concrete adapter is
    constructed/configured) is intentionally left to each adapter —
    TAD does not specify a shared config schema, and inventing one
    here would be scope creep for D1.
    """

    @property
    def provider_name(self) -> str: ...

    @property
    def provider_version(self) -> str: ...

    @property
    def supported_capabilities(self) -> frozenset[Capability]: ...

    @property
    def health_status(self) -> ProviderHealthStatus:
        """Operational condition of the provider itself, independent of ``availability``."""
        ...

    def availability(self, capability: Capability, repository: RepositoryMetadata) -> float:
        """Normalized [0.0, 1.0] readiness for this capability in this repository/environment.

        Adapter-reported fact, not an aggregated selection score — the
        Capability Registry (D2) is responsible for aggregating this
        (and ``check_eligibility``/``health_status``) across providers
        into a SUPPORTED/AVAILABLE/UNAVAILABLE/INELIGIBLE decision
        (TAD §10, §31). A HEALTHY provider may still report 0.0 here
        (unsupported capability, missing entitlement for this
        repository, ...); this value must never be derived from
        ``health_status`` or vice versa (D1 clarification, 2026-08-30).
        """
        ...

    @property
    def freshness(self) -> datetime | None:
        """Timestamp of the most recent successful extraction, if any."""
        ...

    def validate(self) -> ValidationResult:
        """Check this adapter's own environment/configuration, independent of any repository."""
        ...

    def check_eligibility(self, repository: RepositoryMetadata) -> ProviderEligibility:
        """Report license/repository/environment eligibility for this specific repository."""
        ...

    def extract(
        self, repository: RepositoryMetadata, capabilities: Collection[Capability]
    ) -> ExtractionResult:
        """Run extraction for the requested capabilities.

        Raises ``ProviderExtractionError`` if the provider cannot run
        at all. A partial/failed *capability* within an otherwise
        successful run is reported via the returned result's
        ``EvidenceCohort``, not by raising.
        """
        ...

    def normalize(self, result: ExtractionResult) -> NormalizedEvidence:
        """Translate adapter-specific raw output into canonical entities/evidence.

        This is the only place ``ExtractionResult.raw_payload`` may be
        read (TAD §8 invariant #2).
        """
        ...
