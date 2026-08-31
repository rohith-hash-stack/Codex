"""Shared fixtures for `codex.planner` tests.

Reuses `DeterministicFakeAdapter` (tests/fake_ingestion_provider.py, D4's
own idempotency-safe fixture) rather than inventing a new fake provider.
"""

from __future__ import annotations

from pathlib import Path

from codex.evidence.store import InMemoryEvidenceStore
from codex.ingestion.models import IngestionResult
from codex.ingestion.pipeline import IngestionPipeline
from codex.ontology.relationships import RelationshipType
from codex.provider.capability import Capability
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile
from codex.repository.models import RepositoryMetadata
from fake_ingestion_provider import DeterministicFakeAdapter

PROFILE = ProviderScoreProfile(evidence_quality=0.8, cost_factor=0.5)


def make_repository(repository_id: str = "repo1", revision: str = "rev1") -> RepositoryMetadata:
    return RepositoryMetadata(
        repository_id=repository_id, local_path=Path("/fake/repo"), head_revision=revision
    )


def build_graph(
    *,
    entity_paths: tuple[str, ...],
    relationship_pairs: tuple[tuple[str, str], ...] = (),
    predicate: RelationshipType = RelationshipType.CALLS,
    capabilities: frozenset[Capability] = frozenset(
        {Capability.CALL_RELATIONSHIP, Capability.SYMBOL_REFERENCE}
    ),
    repository: RepositoryMetadata | None = None,
    fail_capabilities: frozenset[Capability] = frozenset(),
    produce_empty: bool = False,
) -> tuple[IngestionResult, CapabilityRegistry, InMemoryEvidenceStore, RepositoryMetadata]:
    """Ingest one deterministic fake provider's output and return the
    resulting `IngestionResult` plus the registry/evidence store/
    repository used to build it -- everything a planner test needs to
    construct a `RetrievalPlan`/`EvidencePackage` against a real (small)
    graph."""
    repository = repository or make_repository()
    registry = CapabilityRegistry()
    evidence_store = InMemoryEvidenceStore()
    pipeline = IngestionPipeline(registry, evidence_store)
    adapter = DeterministicFakeAdapter(
        name="fake",
        capabilities=capabilities,
        entity_paths=entity_paths,
        relationship_pairs=relationship_pairs,
        predicate=predicate,
        fail_capabilities=fail_capabilities,
        produce_empty=produce_empty,
    )
    registry.register(adapter, PROFILE)
    result = pipeline.run(repository)
    return result, registry, evidence_store, repository
