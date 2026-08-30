"""A deterministic fake ``ProviderAdapter`` for ingestion pipeline tests.

Distinct from ``tests/fake_provider_adapter.py`` (D1/D2's fixture, left
untouched per "do not reopen D1/D2/D3 decisions"): that fixture's
``extract()`` derives ``snapshot_id`` from an internal call counter, so
repeated calls with identical inputs produce different ``evidence_id``s
— useful for D1/D2's own tests, but incompatible with proving D4's
idempotency and deterministic-repeated-ingestion behavior (directive D4
§10, §16), which require ``extract()``/``normalize()`` to be a pure
function of ``(repository.head_revision, requested capabilities)``.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime

from codex.evidence.model import CoverageStatus, Evidence, EvidenceCohort
from codex.ontology.entities import BaseEntityType, RepositorySymbol, build_canonical_id
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


class DeterministicFakeAdapter:
    """Configurable, deterministic fake satisfying the ``ProviderAdapter`` protocol."""

    def __init__(
        self,
        *,
        name: str,
        version: str = "1.0.0",
        capabilities: frozenset[Capability],
        health: ProviderHealthStatus = ProviderHealthStatus.HEALTHY,
        default_availability: float = 1.0,
        eligibility: ProviderEligibility | None = None,
        validate_ok: bool = True,
        fail_capabilities: frozenset[Capability] = frozenset(),
        raise_on_extract: ProviderFailureReason | None = None,
        entity_paths: tuple[str, ...] = (),
        relationship_pairs: tuple[tuple[str, str], ...] = (),
        predicate: RelationshipType = RelationshipType.CO_CHANGED_WITH,
        confidence: float = 0.8,
        produce_empty: bool = False,
    ) -> None:
        self._name = name
        self._version = version
        self._capabilities = capabilities
        self._health = health
        self._default_availability = default_availability
        self._eligibility = eligibility or ProviderEligibility(status=EligibilityStatus.ELIGIBLE)
        self._validate_ok = validate_ok
        self._fail = fail_capabilities
        self._raise_on_extract = raise_on_extract
        self._entity_paths = entity_paths
        self._relationship_pairs = relationship_pairs
        self._predicate = predicate
        self._confidence = confidence
        self._produce_empty = produce_empty
        self._freshness: datetime | None = None
        self.extract_calls = 0

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
        return self._default_availability

    @property
    def freshness(self) -> datetime | None:
        return self._freshness

    def validate(self) -> ValidationResult:
        problems = [] if self._validate_ok else ["broken"]
        return ValidationResult(ok=self._validate_ok, problems=problems)

    def check_eligibility(self, repository: RepositoryMetadata) -> ProviderEligibility:
        return self._eligibility

    def extract(
        self, repository: RepositoryMetadata, capabilities: Collection[Capability]
    ) -> ExtractionResult:
        self.extract_calls += 1
        if self._raise_on_extract is not None:
            raise ProviderExtractionError(self._name, self._raise_on_extract, "simulated failure")

        requested = frozenset(capabilities) & self._capabilities
        failed = requested & self._fail
        successful = requested - failed

        if not successful and not failed:
            coverage = CoverageStatus.NONE
        elif failed:
            coverage = CoverageStatus.PARTIAL
        else:
            coverage = CoverageStatus.FULL

        cohort = EvidenceCohort(
            provider=self._name,
            provider_version=self._version,
            snapshot_id=repository.head_revision,
            source_revision=repository.head_revision,
            successful_capabilities=sorted(c.value for c in successful),
            failed_capabilities=sorted(c.value for c in failed),
            coverage_status=coverage,
        )
        self._freshness = cohort.observed_at

        return ExtractionResult(
            cohort=cohort,
            raw_payload={
                "repository_id": repository.repository_id,
                "revision": repository.head_revision,
                "produce": bool(successful) and not self._produce_empty,
            },
        )

    def normalize(self, result: ExtractionResult) -> NormalizedEvidence:
        payload = result.raw_payload
        repository_id: str = payload["repository_id"]
        revision: str = payload["revision"]

        entities: list[RepositorySymbol] = []
        if payload["produce"]:
            for path in self._entity_paths:
                canonical_id = build_canonical_id(
                    repository_id=repository_id,
                    repository_revision=revision,
                    qualified_name=path,
                    base_type=BaseEntityType.FILE,
                )
                entities.append(
                    RepositorySymbol(
                        canonical_id=canonical_id,
                        repository_id=repository_id,
                        repository_revision=revision,
                        name=path,
                        qualified_name=path,
                        base_type=BaseEntityType.FILE,
                    )
                )

        evidence: list[Evidence] = []
        if payload["produce"]:
            for i, (path_a, path_b) in enumerate(self._relationship_pairs):
                subject = build_canonical_id(
                    repository_id=repository_id,
                    repository_revision=revision,
                    qualified_name=path_a,
                    base_type=BaseEntityType.FILE,
                )
                obj = build_canonical_id(
                    repository_id=repository_id,
                    repository_revision=revision,
                    qualified_name=path_b,
                    base_type=BaseEntityType.FILE,
                )
                evidence.append(
                    Evidence(
                        evidence_id=f"{self._name}:{revision}:{i}",
                        provider=self._name,
                        provider_version=self._version,
                        snapshot_id=result.cohort.snapshot_id,
                        source_revision=revision,
                        subject=subject,
                        predicate=self._predicate,
                        object=obj,
                        confidence=self._confidence,
                        freshness=result.cohort.observed_at,
                    )
                )

        return NormalizedEvidence(entities=entities, evidence=evidence, cohort=result.cohort)
