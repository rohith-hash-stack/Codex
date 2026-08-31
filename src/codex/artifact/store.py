"""Artifact Store Protocol and in-memory default implementation (TAD
component #17; TAD §52-54, §61, §77-78; directive D12).

**Scope, per the approved D12 decisions**
(`docs/architecture-conformance-audit.md` §Z, and the explicit D12
implementation directive):

- **Immutable.** `store()` for a reference already holding identical
  content is an idempotent success (no-op); `store()` for a reference
  already holding *different* content raises `ArtifactConflictError`
  deterministically. Never a silent overwrite.
- **Opaque reference.** `reference` is validated only at the URI
  *scheme* level, reusing D1's existing `validate_raw_reference`/
  `RAW_REFERENCE_SCHEMES` (`artifact://`, `s3://`, `file://`)
  unchanged. Nothing here parses a fragment, path segment, or any
  other structure inside the reference string -- TAD §52 gives no
  such grammar, and none is invented.
- **No retention/lifecycle policy.** No TTL, no eviction, no deletion
  method exists anywhere on this Protocol -- TAD/HLRD specify none,
  and the directive explicitly forbids inventing one.

Content is always treated as opaque bytes -- this module never parses,
executes, or otherwise interprets what it stores (TAD §52: "raw
provider artifacts"; the same "content is data, never instructions"
discipline already established for query text and evidence fields,
extended here per `docs/architecture-conformance-audit.md` §Z.6).
"""

from __future__ import annotations

from typing import Protocol

from codex.evidence.model import validate_raw_reference


class ArtifactConflictError(ValueError):
    """A second `store()` call for `reference` supplied content that
    differs from what is already stored -- TAD gives no overwrite
    semantics, so this is the deterministic, never-silent alternative
    to guessing (directive D12: "never silently overwrite")."""

    def __init__(self, reference: str) -> None:
        self.reference = reference
        super().__init__(
            f"artifact reference already stored with different content: {reference!r}"
        )


class ArtifactStore(Protocol):
    """Store-and-resolve interface for opaque, provider-produced
    artifact bytes, addressed by an already-scheme-validated
    `raw_reference` string (TAD §52). Read-only for every D1-D11
    consumer -- nothing in the existing pipeline calls `resolve()`
    (see `docs/architecture-conformance-audit.md` §Z.9); `codex.llm`
    specifically must never import this module at all (TAD §61: "The
    LLM must not have unrestricted access to... artifact storage").
    """

    def store(self, reference: str, content: bytes) -> None: ...

    def resolve(self, reference: str) -> bytes | None: ...


class InMemoryArtifactStore:
    """Dict-backed ``ArtifactStore`` for development and tests.

    Storage technology is deferred (ADR-003, TAD §77 -- still open);
    this in-memory implementation follows the exact `EvidenceStore`/
    `GraphStore`/`TelemetryStore` precedent: a stable Protocol now,
    real storage technology later, without reopening this interface.
    """

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    def store(self, reference: str, content: bytes) -> None:
        validate_raw_reference(reference)
        existing = self._blobs.get(reference)
        if existing is not None:
            if existing == content:
                return
            raise ArtifactConflictError(reference)
        self._blobs[reference] = content

    def resolve(self, reference: str) -> bytes | None:
        return self._blobs.get(reference)


__all__ = ["ArtifactConflictError", "ArtifactStore", "InMemoryArtifactStore"]
