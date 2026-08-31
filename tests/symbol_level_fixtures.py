"""Deterministic FILE + symbol/function/class/method integration fixture.

Built for the D1-D10 integration hardening pass (per
``docs/architecture-truth-report.md`` §12 Finding 1): every pre-existing
D9/D10 test builds its graph exclusively through
``tests/planner_fixtures.py``'s ``build_graph()``, which is FILE-only,
so no D9/D10 test had ever exercised symbol/function/class/method-level
retrieval end to end -- even though HLRD/TAD's own worked examples
(HLRD §36's "Who calls `PaymentService`?") are symbol-level, not
file-level, and production retrieval code was independently proven
correct for symbol-level entities via a one-off, non-committed trace
script during the prior audit.

This module does **not** change `codex.ontology`'s entity/relationship
semantics and does **not** add any new retrieval behavior to
`codex.planner`/`codex.graph` -- it is a test-only fixture, built by
registering four `DeterministicFakeAdapter` instances (one per base
type, reusing that fixture's existing, now slightly-extended
constructor -- see its own module docstring) into one real, unmodified
`IngestionPipeline`/`CapabilityRegistry`, exactly the multi-provider
coexistence pattern D6 already established (Git+CodeQL, SCIP+CodeQL in
one pipeline run).

The resulting graph mirrors what a real SCIP-ingested repository would
look like for a small `AuthService` module:

- FILE entities: ``auth_service.py``, ``test_auth.py``, ``test_login.py``
- FUNCTION entities: ``authenticate``, ``test_valid_login``,
  ``test_invalid_login``, with real ``CALLS`` relationships from each
  test function to ``authenticate`` -- the same shape as this
  directive's own worked example, "Which tests call `authenticate`?"
- CLASS entity: ``AuthService``
- METHOD entity: ``AuthService.authenticate``, with a real ``CONTAINS``
  relationship from the CLASS to the METHOD
"""

from __future__ import annotations

from codex.evidence.store import InMemoryEvidenceStore
from codex.ingestion.models import IngestionResult
from codex.ingestion.pipeline import IngestionPipeline
from codex.ontology.entities import BaseEntityType
from codex.ontology.relationships import RelationshipType
from codex.provider.capability import Capability
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile
from codex.repository.models import RepositoryMetadata
from fake_ingestion_provider import DeterministicFakeAdapter
from planner_fixtures import make_repository

FILE_PROFILE = ProviderScoreProfile(evidence_quality=0.8, cost_factor=0.5)
FUNCTION_PROFILE = ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.4)
CLASS_PROFILE = ProviderScoreProfile(evidence_quality=0.85, cost_factor=0.4)
METHOD_PROFILE = ProviderScoreProfile(evidence_quality=0.85, cost_factor=0.4)

AUTHENTICATE_FN = "authenticate"
TEST_VALID_LOGIN_FN = "test_valid_login"
TEST_INVALID_LOGIN_FN = "test_invalid_login"
AUTH_SERVICE_CLASS = "AuthService"
AUTHENTICATE_METHOD = "AuthService.check_credentials"
"""`AUTHENTICATE_FN` ("authenticate") is a bare name, matching what
Tier-0 (`codex.query_understanding.tier0`) extracts as a query target
from text like "Which tests call authenticate?" -- `find_entities()`
matches on `RepositorySymbol.name`, and the fixture sets `name` equal
to whatever path string is supplied, so this must be the bare name,
not a qualified one, for a query built from real Tier-0 detection to
resolve it. `find_entities()` is a **substring** match (its own
docstring: "Deterministic, case-sensitive substring lookup" --
case-insensitive in the actual implementation), not an exact match, so
`AUTHENTICATE_METHOD` is deliberately named to share no substring with
`AUTHENTICATE_FN` ("AuthService.authenticate" would silently also
match a lookup for "authenticate", pulling in an unrelated CLASS/
METHOD relationship -- an artifact of this fixture's naming, not a
retrieval defect; production `find_entities()` behavior is unchanged
and correctly documented as substring matching)."""


def build_symbol_level_graph(
    *, repository: RepositoryMetadata | None = None
) -> tuple[IngestionResult, CapabilityRegistry, InMemoryEvidenceStore, RepositoryMetadata]:
    """Ingest a mixed FILE/FUNCTION/CLASS/METHOD graph through the real,
    unmodified `IngestionPipeline` and return everything a D8-D10 test
    needs to run a real symbol-level query end to end."""
    repository = repository or make_repository()
    registry = CapabilityRegistry()
    evidence_store = InMemoryEvidenceStore()
    pipeline = IngestionPipeline(registry, evidence_store)

    file_adapter = DeterministicFakeAdapter(
        name="fake-files",
        capabilities=frozenset({Capability.SYMBOL_DEFINITION}),
        entity_paths=("auth_service.py", "test_auth.py", "test_login.py"),
        base_type=BaseEntityType.FILE,
    )
    function_adapter = DeterministicFakeAdapter(
        name="fake-functions",
        capabilities=frozenset({Capability.CALL_RELATIONSHIP, Capability.SYMBOL_REFERENCE}),
        entity_paths=(AUTHENTICATE_FN, TEST_VALID_LOGIN_FN, TEST_INVALID_LOGIN_FN),
        relationship_pairs=(
            (TEST_VALID_LOGIN_FN, AUTHENTICATE_FN),
            (TEST_INVALID_LOGIN_FN, AUTHENTICATE_FN),
        ),
        predicate=RelationshipType.CALLS,
        base_type=BaseEntityType.FUNCTION,
    )
    class_adapter = DeterministicFakeAdapter(
        name="fake-classes",
        capabilities=frozenset({Capability.TYPE_RELATIONSHIP}),
        entity_paths=(AUTH_SERVICE_CLASS,),
        relationship_pairs=((AUTH_SERVICE_CLASS, AUTHENTICATE_METHOD),),
        predicate=RelationshipType.CONTAINS,
        base_type=BaseEntityType.CLASS,
        object_base_type=BaseEntityType.METHOD,
    )
    method_adapter = DeterministicFakeAdapter(
        name="fake-methods",
        capabilities=frozenset({Capability.IMPLEMENTATION}),
        entity_paths=(AUTHENTICATE_METHOD,),
        base_type=BaseEntityType.METHOD,
    )

    registry.register(file_adapter, FILE_PROFILE)
    registry.register(function_adapter, FUNCTION_PROFILE)
    registry.register(class_adapter, CLASS_PROFILE)
    registry.register(method_adapter, METHOD_PROFILE)

    result = pipeline.run(repository)
    return result, registry, evidence_store, repository
