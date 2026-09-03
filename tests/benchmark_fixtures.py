"""Shared real-repository ingestion fixture for `codex.benchmark` tests
(mirrors `tests/test_d7_providers_real_repository.py`'s own "self-
hosting" technique: Codex's own checked-out source is always present
wherever this test suite runs, unlike a sibling-checkout repository).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from codex.evidence.store import InMemoryEvidenceStore
from codex.ingestion.models import IngestionResult
from codex.ingestion.pipeline import IngestionPipeline
from codex.provider.ast_calls_adapter import AstCallsAdapter
from codex.provider.pyproject_dependency_adapter import PyprojectDependencyAdapter
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile
from codex.repository.models import RepositoryMetadata

REPO_ROOT = Path(__file__).resolve().parents[1]

FROZEN_REVISION = "b01755b1f8bb1f8243360414e1bc736301d399be"
"""The exact commit the checked-in `tests/fixtures/benchmark/
codex_self_dev_corpus.json` was frozen against. Documented, not
re-derived from `git` at test time (this project's own git history is
not guaranteed clean/available in every environment this suite runs
in) -- a live re-ingestion of the *current* working tree is still
compared against this frozen artifact by `test_benchmark_dev_corpus.py`,
since the two real, dependency-free providers used here (`AstCallsAdapter`,
`PyprojectDependencyAdapter`) do not themselves read git history, and
none of the four development-corpus cases' ground truth happens to be
perturbed by any file this benchmark milestone itself added (verified at
freeze time; see that test module's own docstring)."""

BENCHMARK_DEV_NOW = datetime(2026, 9, 3, tzinfo=UTC)

_CALLS_PROFILE = ProviderScoreProfile(evidence_quality=0.85, cost_factor=0.3)
_DEPS_PROFILE = ProviderScoreProfile(evidence_quality=0.95, cost_factor=0.1)


def make_codex_self_repository() -> RepositoryMetadata:
    return RepositoryMetadata(
        repository_id="codex", local_path=REPO_ROOT, head_revision=FROZEN_REVISION
    )


def ingest_codex_self() -> (
    tuple[IngestionResult, CapabilityRegistry, InMemoryEvidenceStore, RepositoryMetadata]
):
    """Real ingestion of Codex's own live source tree via the real,
    unmodified `IngestionPipeline` and exactly the two dependency-free,
    network-free D7 providers `codex.benchmark.dev_corpus` documents
    using -- never SCIP/CodeQL/Git, never a synthetic fixture."""
    repository = make_codex_self_repository()
    registry = CapabilityRegistry()
    registry.register(AstCallsAdapter(), _CALLS_PROFILE)
    registry.register(PyprojectDependencyAdapter(), _DEPS_PROFILE)
    evidence_store = InMemoryEvidenceStore()
    pipeline = IngestionPipeline(registry, evidence_store)
    result = pipeline.run(repository)
    return result, registry, evidence_store, repository


__all__ = [
    "BENCHMARK_DEV_NOW",
    "FROZEN_REVISION",
    "REPO_ROOT",
    "ingest_codex_self",
    "make_codex_self_repository",
]
