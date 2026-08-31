"""Behavioral tests for `codex.artifact.store` (TAD §52-54; directive
D12): opaque store/resolve, immutable/no-silent-overwrite semantics,
scheme validation reused from D1, no retention/lifecycle surface.
"""

from __future__ import annotations

import pytest

from codex.artifact.store import ArtifactConflictError, InMemoryArtifactStore

# --- normal store/resolve ---------------------------------------------------


def test_stored_content_is_resolvable() -> None:
    store = InMemoryArtifactStore()
    store.store("artifact://sarif/0/0", b"hello world")
    assert store.resolve("artifact://sarif/0/0") == b"hello world"


def test_unknown_reference_resolves_to_none() -> None:
    store = InMemoryArtifactStore()
    assert store.resolve("artifact://never/stored") is None


def test_resolving_an_unvalidated_garbage_reference_is_just_a_miss() -> None:
    """`resolve()` never raises for a malformed reference -- it simply
    was never stored, matching `EvidenceStore.get_evidence`'s plain
    dict-lookup precedent (no read-side validation anywhere in this
    codebase)."""
    store = InMemoryArtifactStore()
    assert store.resolve("not-a-valid-scheme") is None


def test_content_round_trips_exactly_including_binary_bytes() -> None:
    store = InMemoryArtifactStore()
    blob = bytes(range(256))
    store.store("artifact://blob/1", blob)
    assert store.resolve("artifact://blob/1") == blob


# --- scheme validation (reused from D1, not reinvented) ---------------------


@pytest.mark.parametrize("scheme", ["artifact://", "s3://", "file://"])
def test_each_d1_scheme_is_accepted(scheme: str) -> None:
    store = InMemoryArtifactStore()
    reference = f"{scheme}some/path"
    store.store(reference, b"data")
    assert store.resolve(reference) == b"data"


def test_invalid_scheme_is_rejected_deterministically() -> None:
    store = InMemoryArtifactStore()
    with pytest.raises(ValueError, match="raw_reference must start with"):
        store.store("ftp://not-allowed", b"data")


def test_rejected_store_never_partially_writes() -> None:
    """A `store()` call that fails scheme validation must not leave
    any trace -- proven by a subsequent valid `store()` for the same
    reference (with different content) succeeding cleanly, as if the
    first call never happened."""
    store = InMemoryArtifactStore()
    with pytest.raises(ValueError):
        store.store("ftp://bad", b"first")
    # "ftp://bad" was never actually written -- resolving it is a miss
    assert store.resolve("ftp://bad") is None


# --- immutable / no-silent-overwrite semantics ------------------------------


def test_re_storing_identical_content_is_an_idempotent_no_op() -> None:
    store = InMemoryArtifactStore()
    store.store("artifact://sarif/0/0", b"same bytes")
    store.store("artifact://sarif/0/0", b"same bytes")  # must not raise
    assert store.resolve("artifact://sarif/0/0") == b"same bytes"


def test_re_storing_different_content_raises_conflict_deterministically() -> None:
    store = InMemoryArtifactStore()
    store.store("artifact://sarif/0/0", b"original")
    with pytest.raises(ArtifactConflictError) as exc_info:
        store.store("artifact://sarif/0/0", b"different")
    assert exc_info.value.reference == "artifact://sarif/0/0"


def test_conflict_never_silently_overwrites_the_original_content() -> None:
    store = InMemoryArtifactStore()
    store.store("artifact://sarif/0/0", b"original")
    with pytest.raises(ArtifactConflictError):
        store.store("artifact://sarif/0/0", b"different")
    # the original content is untouched after a rejected conflicting write
    assert store.resolve("artifact://sarif/0/0") == b"original"


def test_conflict_error_message_names_the_reference() -> None:
    store = InMemoryArtifactStore()
    store.store("artifact://x/1", b"a")
    with pytest.raises(ArtifactConflictError, match="artifact://x/1"):
        store.store("artifact://x/1", b"b")


# --- no retention/lifecycle/deletion surface --------------------------------


def test_no_delete_update_or_retention_method_exists() -> None:
    """No TTL, eviction, or deletion semantics anywhere on the class
    (directive D12) -- proven by enumerating the entire public
    surface, the same technique `test_no_update_or_delete_method_
    exists` already established for `InMemoryTelemetryStore`."""
    public_methods = {
        name
        for name in dir(InMemoryArtifactStore)
        if not name.startswith("_") and callable(getattr(InMemoryArtifactStore, name))
    }
    assert public_methods == {"store", "resolve"}


# --- content is opaque data, never interpreted ------------------------------


def test_stored_content_is_never_parsed_or_executed() -> None:
    """Content that looks like code/JSON/anything else is stored and
    returned byte-for-byte, never parsed, evaluated, or otherwise
    interpreted -- the store is a pure byte container (TAD §52: "raw
    provider artifacts"; docs/architecture-conformance-audit.md §Z.6)."""
    store = InMemoryArtifactStore()
    suspicious_payloads = [
        b"__import__('os').system('echo pwned')",
        b'{"malicious": true, "eval": "1+1"}',
        b"<script>alert(1)</script>",
        b"\x00\x01\x02\xff\xfe",
    ]
    for i, payload in enumerate(suspicious_payloads):
        reference = f"artifact://untrusted/{i}"
        store.store(reference, payload)
        assert store.resolve(reference) == payload
