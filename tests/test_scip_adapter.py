"""Behavioral tests for the D5 SCIP Adapter (directive D5 §19).

Uses handcrafted fixtures (``scip_fixtures.py``) for precise control
over edge cases, plus the real `scip-typescript`-produced artifact at
``tests/fixtures/scip/typescript_sample.scip`` for realistic end-to-end
validation (directive D5 §19's "validate ... against independently
generated/real SCIP artifacts" — see `docs/resources.md`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codex.evidence.model import CoverageStatus
from codex.evidence.store import InMemoryEvidenceStore
from codex.ingestion.pipeline import IngestionPipeline
from codex.ontology.entities import BaseEntityType
from codex.ontology.relationships import RelationshipType
from codex.provider.capability import Capability
from codex.provider.contract import EligibilityStatus, ProviderExtractionError, ProviderHealthStatus
from codex.provider.scip_adapter import DEFAULT_INDEX_FILENAME, SCIPAdapter
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile
from codex.repository.models import RepositoryMetadata
from scip_fixtures import document, occurrence, relationship, scip_index, symbol_information

REAL_FIXTURE = Path(__file__).parent / "fixtures" / "scip" / "typescript_sample.scip"


def make_repository(local_path: Path, revision: str = "rev1") -> RepositoryMetadata:
    return RepositoryMetadata(repository_id="repo1", local_path=local_path, head_revision=revision)


def simple_class_index() -> bytes:
    definition = occurrence("scip-test npm pkg 1.0.0 src/`a.ts`/Foo#", roles=1, range_=(0, 6, 9))
    sym = symbol_information("scip-test npm pkg 1.0.0 src/`a.ts`/Foo#", kind=7)
    doc = document("src/a.ts", occurrences=(definition,), symbols=(sym,))
    return scip_index(tool_name="test-indexer", tool_version="1.2.3", documents=(doc,))


# ---------------------------------------------------------------------------
# Identity, capabilities, health
# ---------------------------------------------------------------------------


def test_identity_and_capabilities() -> None:
    adapter = SCIPAdapter()
    assert adapter.provider_name == "scip"
    assert adapter.supported_capabilities == frozenset(
        {
            Capability.SYMBOL_DEFINITION,
            Capability.SYMBOL_REFERENCE,
            Capability.IMPLEMENTATION,
            Capability.TYPE_RELATIONSHIP,
        }
    )
    assert adapter.health_status is ProviderHealthStatus.HEALTHY
    assert adapter.validate().ok is True


def test_provider_version_unknown_before_extraction() -> None:
    assert SCIPAdapter().provider_version == "unknown"


def test_provider_version_reflects_last_decoded_tool_info(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(simple_class_index())
    adapter = SCIPAdapter()
    adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    assert adapter.provider_version == "test-indexer@1.2.3"


# ---------------------------------------------------------------------------
# Eligibility / availability
# ---------------------------------------------------------------------------


def test_check_eligibility_missing_index_file(tmp_path: Path) -> None:
    result = SCIPAdapter().check_eligibility(make_repository(tmp_path))
    assert result.status is EligibilityStatus.INELIGIBLE_REPOSITORY


def test_check_eligibility_index_present(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(simple_class_index())
    assert SCIPAdapter().check_eligibility(make_repository(tmp_path)).eligible is True


def test_check_eligibility_directory_at_index_path_is_ineligible(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).mkdir()
    result = SCIPAdapter().check_eligibility(make_repository(tmp_path))
    assert result.status is EligibilityStatus.INELIGIBLE_REPOSITORY


def test_availability_zero_for_unsupported_capability(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(simple_class_index())
    adapter = SCIPAdapter()
    assert adapter.availability(Capability.CALL_RELATIONSHIP, make_repository(tmp_path)) == 0.0


def test_availability_full_when_eligible_and_supported(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(simple_class_index())
    adapter = SCIPAdapter()
    assert adapter.availability(Capability.SYMBOL_DEFINITION, make_repository(tmp_path)) == 1.0


def test_availability_zero_when_ineligible(tmp_path: Path) -> None:
    adapter = SCIPAdapter()
    assert adapter.availability(Capability.SYMBOL_DEFINITION, make_repository(tmp_path)) == 0.0


def test_custom_index_filename(tmp_path: Path) -> None:
    (tmp_path / "custom.scip").write_bytes(simple_class_index())
    adapter = SCIPAdapter(index_filename="custom.scip")
    assert adapter.check_eligibility(make_repository(tmp_path)).eligible is True
    assert not (tmp_path / DEFAULT_INDEX_FILENAME).exists()


# ---------------------------------------------------------------------------
# extract() failure modes (directive D5 §13, §16)
# ---------------------------------------------------------------------------


def test_extract_missing_index_raises_unavailable(tmp_path: Path) -> None:
    adapter = SCIPAdapter()
    with pytest.raises(ProviderExtractionError):
        adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)


def test_extract_malformed_artifact_raises_unavailable(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(b"\xff\xff\xff\xff\xff\xff\xff\xff")
    adapter = SCIPAdapter()
    with pytest.raises(ProviderExtractionError, match="malformed"):
        adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)


def test_extract_empty_file_raises_unavailable(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(b"")
    adapter = SCIPAdapter()
    with pytest.raises(ProviderExtractionError, match="malformed|empty"):
        adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)


def test_extract_no_requested_capabilities_is_a_clean_empty_run(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(simple_class_index())
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), [])
    assert result.cohort.successful_capabilities == []
    assert result.cohort.failed_capabilities == []
    assert result.cohort.coverage_status is CoverageStatus.NONE


def test_extract_unsupported_capability_silently_dropped(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(simple_class_index())
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), [Capability.CALL_RELATIONSHIP])
    assert result.cohort.successful_capabilities == []
    assert result.cohort.failed_capabilities == []


def test_extract_successful_empty_index_is_not_a_failure(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index())  # zero documents
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    expected = {c.value for c in adapter.supported_capabilities}
    assert set(result.cohort.successful_capabilities) == expected
    assert result.cohort.failed_capabilities == []
    assert result.cohort.coverage_status is CoverageStatus.FULL
    normalized = adapter.normalize(result)
    assert normalized.entities == []
    assert normalized.evidence == []


# ---------------------------------------------------------------------------
# Capability-level failure isolation (directive D5 §14)
# ---------------------------------------------------------------------------


def test_freshness_set_after_successful_extraction(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(simple_class_index())
    adapter = SCIPAdapter()
    assert adapter.freshness is None
    adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    assert adapter.freshness is not None


@pytest.mark.parametrize(
    ("target", "capability"),
    [
        ("_collect_definitions", Capability.SYMBOL_DEFINITION),
        ("_collect_references", Capability.SYMBOL_REFERENCE),
    ],
)
def test_each_capability_is_isolated_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str, capability: Capability
) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(simple_class_index())
    adapter = SCIPAdapter()

    import codex.provider.scip_adapter as scip_adapter_module

    def boom(*args: object, **kwargs: object) -> list[object]:
        raise RuntimeError("simulated capability bug")

    monkeypatch.setattr(scip_adapter_module, target, boom)

    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    assert capability.value in result.cohort.failed_capabilities
    # Every other requested capability still succeeded.
    other_capabilities = adapter.supported_capabilities - {capability}
    for other in other_capabilities:
        assert other.value in result.cohort.successful_capabilities


def test_duplicate_relationship_facts_are_deduplicated(tmp_path: Path) -> None:
    subject_symbol = "scip-test npm pkg 1.0.0 src/`a.ts`/Sub#"
    object_symbol = "scip-test npm pkg 1.0.0 src/`a.ts`/Obj#"
    rel = relationship(object_symbol, is_implementation=True)
    # The exact same relationship record appears twice for one symbol.
    sym = symbol_information(subject_symbol, kind=7, relationships=(rel, rel))
    doc = document(
        "src/a.ts",
        occurrences=(occurrence(subject_symbol, roles=1), occurrence(object_symbol, roles=1)),
        symbols=(sym, symbol_information(object_symbol, kind=7)),
    )
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    implements = [e for e in normalized.evidence if e.predicate is RelationshipType.IMPLEMENTS]
    assert len(implements) == 1


@pytest.mark.parametrize(
    ("flag_kwarg", "capability", "predicate"),
    [
        ("is_implementation", Capability.IMPLEMENTATION, RelationshipType.IMPLEMENTS),
        ("is_type_definition", Capability.TYPE_RELATIONSHIP, RelationshipType.REFERENCES),
    ],
)
def test_relationship_to_malformed_object_symbol_is_skipped_not_fabricated(
    tmp_path: Path, flag_kwarg: str, capability: Capability, predicate: RelationshipType
) -> None:
    subject_symbol = "scip-test npm pkg 1.0.0 src/`a.ts`/Sub#"
    malformed_object = "not-a-valid-scip-symbol-header"
    rel = relationship(malformed_object, **{flag_kwarg: True})
    sym = symbol_information(subject_symbol, kind=7, relationships=(rel,))
    doc = document("src/a.ts", occurrences=(occurrence(subject_symbol, roles=1),), symbols=(sym,))
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    # The capability itself still succeeded (it's not a decode-level failure);
    # the one fact pointing at an unparseable symbol is silently skipped
    # rather than turned into a fabricated entity/evidence record.
    assert capability.value in result.cohort.successful_capabilities
    assert all(e.predicate is not predicate for e in normalized.evidence)


def test_resolve_symbol_returns_none_for_local_symbol() -> None:
    from codex.provider.scip_adapter import _resolve_symbol

    resolved = _resolve_symbol(
        "local 2",
        repository_id="repo1",
        revision="rev1",
        locally_defined=frozenset(),
        kind_by_symbol={},
    )
    assert resolved is None


def test_capability_level_failure_does_not_discard_other_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(simple_class_index())
    adapter = SCIPAdapter()

    import codex.provider.scip_adapter as scip_adapter_module

    def boom(index: object, *, want_implementation: bool) -> list[object]:
        raise RuntimeError("simulated capability bug")

    monkeypatch.setattr(scip_adapter_module, "_collect_relationship_facts", boom)

    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    assert Capability.SYMBOL_DEFINITION.value in result.cohort.successful_capabilities
    assert Capability.IMPLEMENTATION.value in result.cohort.failed_capabilities
    assert Capability.TYPE_RELATIONSHIP.value in result.cohort.failed_capabilities
    assert result.cohort.coverage_status is CoverageStatus.PARTIAL

    normalized = adapter.normalize(result)
    assert any(e.base_type is BaseEntityType.CLASS for e in normalized.entities)


# ---------------------------------------------------------------------------
# Definitions, references, relationships, external symbols
# ---------------------------------------------------------------------------


def _linked_index() -> bytes:
    """Two files: `Base` (interface) in b.ts, `Impl` (implements Base, references
    an external symbol) in a.ts."""
    base_symbol = "scip-test npm pkg 1.0.0 src/`b.ts`/Base#"
    impl_symbol = "scip-test npm pkg 1.0.0 src/`a.ts`/Impl#"
    external_symbol = "scip-test npm other-pkg 2.0.0 src/`x.ts`/External#"

    base_def = occurrence(base_symbol, roles=1, range_=(0, 0, 4))
    base_sym_info = symbol_information(base_symbol, kind=21)  # Interface
    doc_b = document("src/b.ts", occurrences=(base_def,), symbols=(base_sym_info,))

    impl_def = occurrence(impl_symbol, roles=1, range_=(0, 0, 4))
    impl_ref_to_base = occurrence(base_symbol, roles=0, range_=(0, 20, 24))
    impl_ref_to_external = occurrence(external_symbol, roles=2, range_=(1, 0, 8))  # Import role
    rel = relationship(base_symbol, is_implementation=True)
    impl_sym_info = symbol_information(impl_symbol, kind=7, relationships=(rel,))
    doc_a = document(
        "src/a.ts",
        occurrences=(impl_def, impl_ref_to_base, impl_ref_to_external),
        symbols=(impl_sym_info,),
    )

    return scip_index(documents=(doc_a, doc_b))


def test_definitions_produce_entities_with_source_location(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_linked_index())
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    impl_entities = [e for e in normalized.entities if e.qualified_name.endswith("Impl#")]
    assert len(impl_entities) == 1
    entity = impl_entities[0]
    assert entity.base_type is BaseEntityType.CLASS
    assert entity.source_location is not None
    assert entity.source_location.file_path == "src/a.ts"
    assert entity.source_location.start_line == 0
    assert entity.source_location.start_column == 0
    assert entity.source_location.end_column == 4


def test_implementation_relationship_produces_implements_evidence(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_linked_index())
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    implements = [e for e in normalized.evidence if e.predicate is RelationshipType.IMPLEMENTS]
    assert len(implements) == 1
    subject_entity = next(e for e in normalized.entities if e.canonical_id == implements[0].subject)
    object_entity = next(e for e in normalized.entities if e.canonical_id == implements[0].object)
    assert subject_entity.qualified_name.endswith("Impl#")
    assert object_entity.qualified_name.endswith("Base#")
    assert object_entity.base_type is BaseEntityType.INTERFACE


def test_reference_without_import_role_is_references_predicate(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_linked_index())
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    to_base = [
        e
        for e in normalized.evidence
        if e.predicate is RelationshipType.REFERENCES
        and any(
            ent.canonical_id == e.object and ent.qualified_name.endswith("Base#")
            for ent in normalized.entities
        )
    ]
    assert len(to_base) == 1


def test_reference_with_import_role_is_imports_predicate(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_linked_index())
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    imports = [e for e in normalized.evidence if e.predicate is RelationshipType.IMPORTS]
    assert len(imports) == 1


def test_external_symbol_becomes_external_library_entity(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_linked_index())
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    external = [e for e in normalized.entities if e.base_type is BaseEntityType.EXTERNAL_LIBRARY]
    assert len(external) == 1
    assert external[0].qualified_name == "npm:other-pkg@2.0.0"
    assert external[0].source_location is None


def test_external_library_identity_independent_of_repository_revision(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_linked_index())
    adapter = SCIPAdapter()

    result1 = adapter.extract(make_repository(tmp_path, "rev1"), adapter.supported_capabilities)
    entities1 = adapter.normalize(result1).entities
    external1 = next(e for e in entities1 if e.base_type is BaseEntityType.EXTERNAL_LIBRARY)

    result2 = adapter.extract(make_repository(tmp_path, "rev2"), adapter.supported_capabilities)
    entities2 = adapter.normalize(result2).entities
    external2 = next(e for e in entities2 if e.base_type is BaseEntityType.EXTERNAL_LIBRARY)

    assert external1.canonical_id == external2.canonical_id


def test_file_entity_converges_with_git_adapter_style_identity(tmp_path: Path) -> None:
    from codex.ontology.entities import build_canonical_id

    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_linked_index())
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    file_entities = [e for e in normalized.entities if e.base_type is BaseEntityType.FILE]
    expected_id = build_canonical_id(
        repository_id="repo1", repository_revision="rev1", qualified_name="src/a.ts",
        base_type=BaseEntityType.FILE,
    )
    assert any(e.canonical_id == expected_id for e in file_entities)


def test_evidence_provenance_fields(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_linked_index())
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    for ev in normalized.evidence:
        assert ev.provider == "scip"
        assert ev.provider_version == "test-indexer@1.0.0"
        assert ev.source_revision == "rev1"
        assert ev.snapshot_id == "rev1"
        assert ev.confidence == 1.0


# ---------------------------------------------------------------------------
# Duplicates and conflicting/ambiguous data (directive D5 §19)
# ---------------------------------------------------------------------------


def test_duplicate_occurrences_do_not_produce_duplicate_evidence(tmp_path: Path) -> None:
    symbol_a = "scip-test npm pkg 1.0.0 src/`a.ts`/A#"
    symbol_b = "scip-test npm pkg 1.0.0 src/`a.ts`/B#"
    ref = occurrence(symbol_b, roles=0, range_=(1, 0, 1))
    doc = document(
        "src/a.ts",
        occurrences=(
            occurrence(symbol_a, roles=1, range_=(0, 0, 1)),
            occurrence(symbol_b, roles=1, range_=(2, 0, 1)),
            ref,
            ref,
            ref,  # same reference occurrence repeated 3 times
        ),
        symbols=(symbol_information(symbol_a, kind=7), symbol_information(symbol_b, kind=7)),
    )
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    references = [e for e in normalized.evidence if e.predicate is RelationshipType.REFERENCES]
    file_to_b = [
        e
        for e in references
        if any(
            ent.canonical_id == e.object and ent.qualified_name.endswith("B#")
            for ent in normalized.entities
        )
    ]
    assert len(file_to_b) == 1


def test_conflicting_relationship_flags_both_preserved_independently(tmp_path: Path) -> None:
    # The same (subject, object) pair asserted as both an implementation fact
    # and a type-definition fact -- SCIP semantics permit both flags to be
    # true on one Relationship record simultaneously. Both must reach the
    # graph as separate evidence; the adapter must not pick one.
    subject_symbol = "scip-test npm pkg 1.0.0 src/`a.ts`/Sub#"
    object_symbol = "scip-test npm pkg 1.0.0 src/`a.ts`/Obj#"
    rel = relationship(object_symbol, is_implementation=True, is_type_definition=True)
    sym = symbol_information(subject_symbol, kind=7, relationships=(rel,))
    doc = document(
        "src/a.ts",
        occurrences=(occurrence(subject_symbol, roles=1), occurrence(object_symbol, roles=1)),
        symbols=(sym, symbol_information(object_symbol, kind=7)),
    )
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    implements = [e for e in normalized.evidence if e.predicate is RelationshipType.IMPLEMENTS]
    type_refs = [e for e in normalized.evidence if e.predicate is RelationshipType.REFERENCES]
    assert len(implements) == 1
    assert len(type_refs) == 1
    assert implements[0].subject == type_refs[0].subject
    assert implements[0].object == type_refs[0].object


# ---------------------------------------------------------------------------
# Determinism / idempotency (directive D5 §17)
# ---------------------------------------------------------------------------


def test_deterministic_repeated_extraction_same_ids(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_linked_index())
    adapter = SCIPAdapter()
    repo = make_repository(tmp_path)

    result1 = adapter.normalize(adapter.extract(repo, adapter.supported_capabilities))
    result2 = adapter.normalize(adapter.extract(repo, adapter.supported_capabilities))

    ids1 = sorted(e.canonical_id for e in result1.entities)
    ids2 = sorted(e.canonical_id for e in result2.entities)
    assert ids1 == ids2

    evidence_ids1 = sorted(e.evidence_id for e in result1.evidence)
    evidence_ids2 = sorted(e.evidence_id for e in result2.evidence)
    assert evidence_ids1 == evidence_ids2


def test_ingestion_pipeline_idempotent_across_two_runs(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_linked_index())
    registry = CapabilityRegistry()
    registry.register(SCIPAdapter(), ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.9))
    pipeline = IngestionPipeline(registry, InMemoryEvidenceStore())
    repo = make_repository(tmp_path)

    result1 = pipeline.run(repo)
    result2 = pipeline.run(repo)

    assert result1.committed_providers == ["scip"]
    assert result1.graph_version.version_id == result2.graph_version.version_id
    rels1 = len(result1.graph_store.get_relationships())
    rels2 = len(result2.graph_store.get_relationships())
    assert rels1 == rels2


# ---------------------------------------------------------------------------
# Real, independently generated SCIP artifact (directive D5 §19)
# ---------------------------------------------------------------------------


def test_real_artifact_full_extraction(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(REAL_FIXTURE.read_bytes())
    adapter = SCIPAdapter()
    repo = make_repository(tmp_path)
    result = adapter.extract(repo, adapter.supported_capabilities)

    assert result.cohort.coverage_status is CoverageStatus.FULL
    assert adapter.provider_version == "scip-typescript@0.4.0"

    normalized = adapter.normalize(result)
    assert len(normalized.entities) > 0
    assert any(e.base_type is BaseEntityType.EXTERNAL_LIBRARY for e in normalized.entities)
    assert any(ev.predicate is RelationshipType.IMPLEMENTS for ev in normalized.evidence)
    assert any(ev.predicate is RelationshipType.REFERENCES for ev in normalized.evidence)
    # No CALLS -- directive D5 §8: SCIP gives no deterministic call signal.
    assert all(ev.predicate is not RelationshipType.CALLS for ev in normalized.evidence)


def test_real_artifact_square_implements_both_circle_and_shape(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(REAL_FIXTURE.read_bytes())
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    square = next(e for e in normalized.entities if e.qualified_name.endswith("Square#"))
    implements = [
        e
        for e in normalized.evidence
        if e.predicate is RelationshipType.IMPLEMENTS and e.subject == square.canonical_id
    ]
    entities_by_id = {ent.canonical_id: ent for ent in normalized.entities}
    targets = {entities_by_id[e.object].qualified_name.split("/")[-1] for e in implements}
    assert targets == {"Circle#", "Shape#"}


def test_real_artifact_through_ingestion_pipeline(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(REAL_FIXTURE.read_bytes())
    registry = CapabilityRegistry()
    registry.register(SCIPAdapter(), ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.9))
    pipeline = IngestionPipeline(registry, InMemoryEvidenceStore())

    result = pipeline.run(make_repository(tmp_path))

    assert result.committed_providers == ["scip"]
    assert len(result.graph_store.get_relationships()) > 0
