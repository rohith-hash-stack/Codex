"""Integration test for `codex.artifact` (directive D12): proves the
new `InMemoryArtifactStore` is compatible with `raw_reference` values
the real, **unmodified** D6 `CodeQLAdapter` already produces -- zero
changes to `codeql_adapter.py`, matching the directive's explicit "do
not modify D3/D5 behavior or reopen earlier contracts" (extended, by
the same reasoning, to D6, the one adapter that already sets a real
`raw_reference`).

Reuses `tests/fixtures/codeql/path-problem.sarif`, the same real fixture
`tests/test_codeql_adapter.py::test_path_problem_produces_source_to_
sink_references_evidence` already uses to prove `Evidence.raw_reference
== "artifact://sarif/0/0"`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codex.artifact.store import ArtifactConflictError, InMemoryArtifactStore
from codex.provider.capability import Capability
from codex.provider.codeql_adapter import CodeQLAdapter
from codex.repository.models import RepositoryMetadata

FIXTURES = Path(__file__).parent / "fixtures" / "codeql"


def make_repository(local_path: Path) -> RepositoryMetadata:
    return RepositoryMetadata(repository_id="repo1", local_path=local_path, head_revision="rev1")


def test_real_codeql_raw_reference_is_resolvable_through_the_artifact_store() -> None:
    """The real, unmodified D6 adapter's `raw_reference` string is
    fully compatible with the new store -- no adapter change needed
    because the reference already conforms to TAD §52's URI-scheme
    requirement (already validated since D1)."""
    adapter = CodeQLAdapter(sarif_filename="path-problem.sarif")
    result = adapter.extract(make_repository(FIXTURES), frozenset({Capability.DATA_FLOW}))
    normalized = adapter.normalize(result)

    assert len(normalized.evidence) == 1
    evidence = normalized.evidence[0]
    assert evidence.raw_reference == "artifact://sarif/0/0"

    # The orchestration-layer step this integration proves: whoever holds
    # the real SARIF bytes (e.g. the CI job that produced the file) stores
    # them under the adapter's own already-emitted reference -- codex.artifact
    # never needs codeql_adapter.py to change to make this work.
    real_sarif_bytes = (FIXTURES / "path-problem.sarif").read_bytes()

    store = InMemoryArtifactStore()
    assert store.resolve(evidence.raw_reference) is None  # nothing stored yet
    store.store(evidence.raw_reference, real_sarif_bytes)
    assert store.resolve(evidence.raw_reference) == real_sarif_bytes


def test_storing_the_same_real_artifact_twice_is_idempotent() -> None:
    """Re-running ingestion against the same unchanged SARIF file (the
    normal, repeated-ingestion case D4 already proves is idempotent at
    the graph level) must not raise a spurious conflict at the
    artifact layer either."""
    adapter = CodeQLAdapter(sarif_filename="path-problem.sarif")
    result = adapter.extract(make_repository(FIXTURES), frozenset({Capability.DATA_FLOW}))
    normalized = adapter.normalize(result)
    evidence = normalized.evidence[0]
    real_sarif_bytes = (FIXTURES / "path-problem.sarif").read_bytes()

    store = InMemoryArtifactStore()
    store.store(evidence.raw_reference, real_sarif_bytes)
    store.store(evidence.raw_reference, real_sarif_bytes)  # second run, same file -- no raise
    assert store.resolve(evidence.raw_reference) == real_sarif_bytes


def test_a_genuinely_changed_artifact_under_the_same_reference_conflicts() -> None:
    """If the SARIF file at a given (run_index, result_index) reference
    ever changed content without the reference itself changing, that
    is exactly the case `ArtifactConflictError` exists to catch --
    proven here against a real, structurally-valid SARIF fixture
    (`valid-sarif.sarif`) standing in for "different real bytes"."""
    adapter = CodeQLAdapter(sarif_filename="path-problem.sarif")
    result = adapter.extract(make_repository(FIXTURES), frozenset({Capability.DATA_FLOW}))
    normalized = adapter.normalize(result)
    evidence = normalized.evidence[0]
    original_bytes = (FIXTURES / "path-problem.sarif").read_bytes()
    different_real_bytes = (FIXTURES / "valid-sarif.sarif").read_bytes()

    store = InMemoryArtifactStore()
    store.store(evidence.raw_reference, original_bytes)
    with pytest.raises(ArtifactConflictError):
        store.store(evidence.raw_reference, different_real_bytes)
