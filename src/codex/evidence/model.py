"""Evidence model (TAD §15-18)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, Field, field_validator

from codex.ontology.relationships import RelationshipType

RAW_REFERENCE_SCHEMES: Final = ("artifact://", "s3://", "file://")
"""Resolvable URI schemes for ``raw_reference`` (TAD §16, §52)."""


def validate_raw_reference(value: str | None) -> str | None:
    """Reject arbitrary provider-invented raw-reference formats (TAD §52)."""
    if value is not None and not value.startswith(RAW_REFERENCE_SCHEMES):
        raise ValueError(
            f"raw_reference must start with one of {RAW_REFERENCE_SCHEMES}, got {value!r}"
        )
    return value


class EvidenceStatus(StrEnum):
    """Reconciled status of a canonical relationship (TAD §18)."""

    SUPPORTED = "SUPPORTED"
    WEAKLY_SUPPORTED = "WEAKLY_SUPPORTED"
    DISPUTED = "DISPUTED"
    UNRESOLVED = "UNRESOLVED"
    CONTRADICTED = "CONTRADICTED"
    UNSUPPORTED = "UNSUPPORTED"


class CoverageStatus(StrEnum):
    """Whether a provider capability ran, and how completely (TAD §17)."""

    FULL = "FULL"
    PARTIAL = "PARTIAL"
    NONE = "NONE"


class Evidence(BaseModel):
    """A single provider-sourced assertion (TAD §15)."""

    evidence_id: str
    provider: str
    provider_version: str
    snapshot_id: str
    source_revision: str
    subject: str
    predicate: RelationshipType
    object: str
    confidence: float = Field(ge=0.0, le=1.0)
    freshness: datetime
    independence_group: str | None = None
    raw_reference: str | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _validate_raw_reference = field_validator("raw_reference")(validate_raw_reference)

    @property
    def effective_independence_group(self) -> str:
        """Evidence without an explicit group is non-independent (TAD §16)."""
        return self.independence_group or f"provider_default:{self.provider}"


class EvidenceCohort(BaseModel):
    """One provider's extraction run against one repository revision (TAD §17)."""

    provider: str
    provider_version: str
    snapshot_id: str
    source_revision: str
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    successful_capabilities: list[str] = Field(default_factory=list)
    failed_capabilities: list[str] = Field(default_factory=list)
    partial_capabilities: list[str] = Field(default_factory=list)
    coverage_status: CoverageStatus = CoverageStatus.NONE

    def supports(self, capability: str) -> bool:
        return capability in self.successful_capabilities


class CanonicalRelationship(BaseModel):
    """A reconciled graph edge with its supporting/contradicting evidence (TAD §73)."""

    subject: str
    predicate: RelationshipType
    object: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    status: EvidenceStatus = EvidenceStatus.UNRESOLVED
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)

    @property
    def key(self) -> tuple[str, RelationshipType, str]:
        return (self.subject, self.predicate, self.object)
