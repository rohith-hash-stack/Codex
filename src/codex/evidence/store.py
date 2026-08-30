"""Evidence Store interface and in-memory default implementation.

Storage technology is deferred to ADR-002 (TAD §77); this in-memory
implementation is the Phase 1 default so the rest of Codex can be
built and tested against a stable interface.
"""

from __future__ import annotations

from typing import Protocol

from codex.evidence.model import Evidence, EvidenceCohort
from codex.ontology.relationships import RelationshipType


class EvidenceStore(Protocol):
    """Read/write interface for provider evidence and evidence cohorts."""

    def add_evidence(self, evidence: Evidence) -> None: ...

    def get_evidence(self, evidence_id: str) -> Evidence | None: ...

    def get_evidence_for(
        self,
        *,
        subject: str | None = None,
        predicate: RelationshipType | None = None,
        object_id: str | None = None,
    ) -> list[Evidence]: ...

    def add_cohort(self, cohort: EvidenceCohort) -> None: ...

    def get_cohorts(self, *, provider: str | None = None) -> list[EvidenceCohort]: ...


class InMemoryEvidenceStore:
    """Dict-backed ``EvidenceStore`` for development and tests."""

    def __init__(self) -> None:
        self._evidence: dict[str, Evidence] = {}
        self._cohorts: list[EvidenceCohort] = []

    def add_evidence(self, evidence: Evidence) -> None:
        self._evidence[evidence.evidence_id] = evidence

    def get_evidence(self, evidence_id: str) -> Evidence | None:
        return self._evidence.get(evidence_id)

    def get_evidence_for(
        self,
        *,
        subject: str | None = None,
        predicate: RelationshipType | None = None,
        object_id: str | None = None,
    ) -> list[Evidence]:
        results = []
        for ev in self._evidence.values():
            if subject is not None and ev.subject != subject:
                continue
            if predicate is not None and ev.predicate != predicate:
                continue
            if object_id is not None and ev.object != object_id:
                continue
            results.append(ev)
        return results

    def add_cohort(self, cohort: EvidenceCohort) -> None:
        self._cohorts.append(cohort)

    def get_cohorts(self, *, provider: str | None = None) -> list[EvidenceCohort]:
        if provider is None:
            return list(self._cohorts)
        return [c for c in self._cohorts if c.provider == provider]
