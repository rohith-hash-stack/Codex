"""Ingestion Pipeline result types (TAD §17, §19-20, §64, §72-73; Phase D directive D4).

Plain dataclasses, not pydantic models: like ``CapabilityRegistry`` (D2),
these hold live ``ProviderAdapter``/``GraphStore`` objects (Protocols
without ``@runtime_checkable``), which pydantic's ``arbitrary_types_
allowed`` cannot validate via ``isinstance()``. Fields that *are*
already pydantic models (``EvidenceCohort``, ``GraphVersion``) are
reused as-is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from codex.evidence.model import EvidenceCohort
from codex.graph.store import GraphStore
from codex.graph.version import GraphVersion
from codex.provider.contract import ProviderFailureReason


class ProviderRunStatus(StrEnum):
    """What happened to one provider within a single ``IngestionPipeline.run()`` call."""

    COMMITTED = "COMMITTED"
    """extract()/normalize() succeeded; its entities/evidence were merged into the
    graph and its cohort recorded. A COMMITTED provider's cohort may still list
    failed_capabilities/partial_capabilities (TAD §17) — that per-capability detail
    lives on the cohort, not on this status."""

    FAILED = "FAILED"
    """The provider was selected to run (Registry classified it usable) but its
    extract() raised ProviderExtractionError (TAD §64 PROVIDER_UNAVAILABLE/
    PROVIDER_TIMEOUT), failed a pipeline-level integrity check, or raised
    unexpectedly. Isolated to this provider only (directive D4 §6, §14)."""

    SKIPPED = "SKIPPED"
    """Never attempted: the Capability Registry classified it UNAVAILABLE/
    INELIGIBLE/FAILED for every requested capability it declares (directive D4
    §2, §4) — provider selection stays the Registry's job, not this module's."""


@dataclass
class ProviderRunOutcome:
    """One provider's outcome within one ``IngestionPipeline.run()`` call."""

    provider_name: str
    status: ProviderRunStatus
    capabilities_requested: frozenset[str] = field(default_factory=frozenset)
    cohort: EvidenceCohort | None = None
    """Present only when ``status`` is COMMITTED — a provider that never ran, or
    that raised before returning a result, has none."""
    failure_reason: ProviderFailureReason | None = None
    detail: str | None = None
    entities_upserted: int = 0
    evidence_upserted: int = 0


@dataclass
class IngestionResult:
    """Outcome of one ``IngestionPipeline.run()`` call (TAD §72's full lifecycle).

    ``graph_version`` is always published (``.published is True``): an
    ingestion run that could not safely publish never returns an
    ``IngestionResult`` at all (directive D4 §15) — see
    ``IngestionPipeline.run()``.
    """

    repository_id: str
    repository_revision: str
    graph_version: GraphVersion
    graph_store: GraphStore
    provider_outcomes: list[ProviderRunOutcome] = field(default_factory=list)

    def _providers_with_status(self, status: ProviderRunStatus) -> list[str]:
        return [o.provider_name for o in self.provider_outcomes if o.status is status]

    @property
    def committed_providers(self) -> list[str]:
        return self._providers_with_status(ProviderRunStatus.COMMITTED)

    @property
    def failed_providers(self) -> list[str]:
        return self._providers_with_status(ProviderRunStatus.FAILED)

    @property
    def skipped_providers(self) -> list[str]:
        return self._providers_with_status(ProviderRunStatus.SKIPPED)
