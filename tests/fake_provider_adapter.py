"""A deterministic, provider-neutral ``ProviderAdapter`` fixture.

Used only to exercise the D1 contract's behavioral properties (TAD
§9; directive §13). This is **not** a Codex provider — Git and SCIP
adapters are out of scope for D1 and are not implemented here or
anywhere else yet.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime

from codex.evidence.model import CoverageStatus, Evidence, EvidenceCohort
from codex.ontology.relationships import RelationshipType
from codex.provider.capability import Capability
from codex.provider.contract import (
    EligibilityStatus,
    ExtractionResult,
    NormalizedEvidence,
    ProviderEligibility,
    ProviderExtractionError,
    ProviderFailureReason,
    ProviderHealthStatus,
    ValidationResult,
)
from codex.repository.models import RepositoryMetadata

DEFAULT_CAPABILITIES = frozenset({Capability.SYMBOL_DEFINITION, Capability.CALL_RELATIONSHIP})


class FakeProviderAdapter:
    """Configurable fake satisfying the ``ProviderAdapter`` protocol."""

    def __init__(
        self,
        *,
        name: str = "FAKE",
        version: str = "1.0.0",
        capabilities: frozenset[Capability] = DEFAULT_CAPABILITIES,
        health: ProviderHealthStatus = ProviderHealthStatus.HEALTHY,
        default_availability: float = 1.0,
        availability_overrides: dict[Capability, float] | None = None,
        eligibility: ProviderEligibility | None = None,
        validate_ok: bool | None = None,
        fail_capabilities: frozenset[Capability] = frozenset(),
        partial_capabilities: frozenset[Capability] = frozenset(),
        empty_capabilities: frozenset[Capability] = frozenset(),
        raise_on_extract: ProviderFailureReason | None = None,
    ) -> None:
        self._name = name
        self._version = version
        self._capabilities = capabilities
        self._health = health
        self._default_availability = default_availability
        self._availability_overrides = availability_overrides or {}
        self._eligibility = eligibility or ProviderEligibility(status=EligibilityStatus.ELIGIBLE)
        self._validate_ok = validate_ok
        self._fail = fail_capabilities
        self._partial = partial_capabilities
        self._empty = empty_capabilities
        self._raise_on_extract = raise_on_extract
        self._freshness: datetime | None = None
        self._extract_count = 0

    @property
    def provider_name(self) -> str:
        return self._name

    @property
    def provider_version(self) -> str:
        return self._version

    @property
    def supported_capabilities(self) -> frozenset[Capability]:
        return self._capabilities

    @property
    def health_status(self) -> ProviderHealthStatus:
        return self._health

    def availability(self, capability: Capability, repository: RepositoryMetadata) -> float:
        return self._availability_overrides.get(capability, self._default_availability)

    @property
    def freshness(self) -> datetime | None:
        return self._freshness

    def validate(self) -> ValidationResult:
        if self._validate_ok is not None:
            ok = self._validate_ok
        else:
            ok = self._health is not ProviderHealthStatus.UNHEALTHY
        return ValidationResult(ok=ok, problems=[] if ok else [f"{self._name} failed validation"])

    def check_eligibility(self, repository: RepositoryMetadata) -> ProviderEligibility:
        return self._eligibility

    def extract(
        self, repository: RepositoryMetadata, capabilities: Collection[Capability]
    ) -> ExtractionResult:
        if self._raise_on_extract is not None:
            raise ProviderExtractionError(self._name, self._raise_on_extract, "simulated failure")

        self._extract_count += 1
        snapshot_id = f"snap-{self._extract_count}"

        # Capabilities outside supported_capabilities are silently dropped:
        # "not supported" must stay distinct from "attempted and failed".
        requested = frozenset(capabilities) & self._capabilities
        failed = requested & self._fail
        partial = requested & self._partial
        successful = requested - failed - partial

        if not successful and not partial:
            coverage = CoverageStatus.NONE
        elif failed or partial:
            coverage = CoverageStatus.PARTIAL
        else:
            coverage = CoverageStatus.FULL

        cohort = EvidenceCohort(
            provider=self._name,
            provider_version=self._version,
            snapshot_id=snapshot_id,
            source_revision=repository.head_revision,
            successful_capabilities=sorted(c.value for c in successful),
            failed_capabilities=sorted(c.value for c in failed),
            partial_capabilities=sorted(c.value for c in partial),
            coverage_status=coverage,
        )
        self._freshness = cohort.observed_at

        produce_evidence_for = sorted(c.value for c in successful - self._empty)

        return ExtractionResult(
            cohort=cohort,
            raw_reference=f"artifact://fake/{snapshot_id}",
            raw_payload={
                "produce_evidence_for": produce_evidence_for,
                "repository_id": repository.repository_id,
            },
        )

    def normalize(self, result: ExtractionResult) -> NormalizedEvidence:
        payload = result.raw_payload
        evidence = [
            Evidence(
                evidence_id=f"{result.cohort.snapshot_id}-{i}",
                provider=self._name,
                provider_version=self._version,
                snapshot_id=result.cohort.snapshot_id,
                source_revision=result.cohort.source_revision,
                subject=f"codex:subject-{i}",
                predicate=RelationshipType.CALLS,
                object=f"codex:object-{i}",
                confidence=0.9,
                freshness=result.cohort.observed_at,
                raw_reference=result.raw_reference,
            )
            for i, _capability_name in enumerate(payload["produce_evidence_for"])
        ]
        return NormalizedEvidence(entities=[], evidence=evidence, cohort=result.cohort)
