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
from codex.ontology.entities import BaseEntityType, build_canonical_id
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
        indexed_relative_paths=frozenset(),
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


def test_external_symbol_with_dot_manager_normalizes_to_empty_string(tmp_path: Path) -> None:
    """Phase D gap-closure directive, Gap A: SCIP's "." placeholder for an
    unset Package field (confirmed against the reference `scip` Rust
    crate) must normalize to an empty string in the resulting
    EXTERNAL_LIBRARY identity, never leak through as a literal "."
    character."""
    local_symbol = "scip-test npm pkg 1.0.0 src/`a.ts`/Impl#"
    external_symbol = "scip-test . unmanaged-pkg . src/`x.ts`/External#"

    local_def = occurrence(local_symbol, roles=1, range_=(0, 0, 4))
    ref_to_external = occurrence(external_symbol, roles=0, range_=(1, 0, 8))
    local_sym_info = symbol_information(local_symbol, kind=7)
    doc = document(
        "src/a.ts", occurrences=(local_def, ref_to_external), symbols=(local_sym_info,)
    )

    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    external = [e for e in normalized.entities if e.base_type is BaseEntityType.EXTERNAL_LIBRARY]
    assert len(external) == 1
    assert external[0].qualified_name == ":unmanaged-pkg@"


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


def test_real_artifact_source_location_flows_through_to_the_graph(tmp_path: Path) -> None:
    """SourceLocation gap-closure directive Gap G: verify the *complete*
    provider -> normalization -> graph path with a real artifact, not
    just the ontology type in isolation. A real `Greeter` class
    definition's location, decoded from genuine `scip-typescript`
    output, must reach the graph as a valid `SourceLocation` (0-based,
    passing the model's own validator -- would raise ValidationError on
    a malformed range) with the exact repo-relative `file_path`
    convention `SourceLocation`'s docstring requires.

    Looks the entity up in the graph by `qualified_name` rather than by
    a `canonical_id` precomputed from the standalone `normalize()` call:
    since the D7/D9 convergence directive, `IngestionPipeline` runs
    every committed entity through `resolve_entities()`, which -- for a
    CLASS/METHOD/FUNCTION entity carrying a `source_location`, `Greeter`
    included -- now unconditionally recomputes `canonical_id` from the
    symbol-location identity key (`codex.resolution.entity_resolver`'s
    `SYMBOL_LOCATION_IDENTITY`), not merely when a second provider's
    matching entity happens to also be present. A standalone `normalize()`
    call never runs entity resolution, so its raw id is expected to
    differ from the graph's resolved id here -- that is the intended
    behavior this directive introduced, not a defect in either path."""
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(REAL_FIXTURE.read_bytes())

    registry = CapabilityRegistry()
    registry.register(SCIPAdapter(), ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.9))
    pipeline = IngestionPipeline(registry, InMemoryEvidenceStore())
    result = pipeline.run(make_repository(tmp_path))

    greeter = next(
        e for e in result.graph_store.find_entities() if e.qualified_name.endswith("Greeter#")
    )
    assert greeter.source_location is not None
    assert greeter.source_location.file_path == "src/greeter.ts"
    assert greeter.source_location.start_line >= 0
    assert greeter.source_location.end_line >= greeter.source_location.start_line


def test_real_artifact_through_ingestion_pipeline(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(REAL_FIXTURE.read_bytes())
    registry = CapabilityRegistry()
    registry.register(SCIPAdapter(), ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.9))
    pipeline = IngestionPipeline(registry, InMemoryEvidenceStore())

    result = pipeline.run(make_repository(tmp_path))

    assert result.committed_providers == ["scip"]
    assert len(result.graph_store.get_relationships()) > 0


# ---------------------------------------------------------------------------
# GAP-9 fix: `locally_defined` includes Definition-occurrence symbols too
# ---------------------------------------------------------------------------
#
# Confirmed root cause (investigation branch
# `investigate/gap9-scip-missing-large-classes`, `main`
# @ 7bca8e428f767bf0e3335e2da7c0278fb85cf7fb): a real producer
# (scip-python/pyright) can emit a genuine `Definition`-role Occurrence for
# a large, heavily-typed top-level class while omitting that same symbol's
# own SymbolInformation entry -- confirmed against real requests/flask/
# pytest/click/django SCIP indexes, with two independent decoders agreeing
# byte-for-byte that the raw `.scip` artifact itself lacks the entry (not a
# Codex decoding bug). `locally_defined` used to be built exclusively from
# `Document.symbols`, so such a symbol was routed through `_resolve_symbol`'s
# "not defined anywhere in this index" external-library branch, which (a)
# discarded its real identity/base_type and (b) collapsed it onto the same
# canonical_id as every other repository-owned symbol hitting this same gap
# (that branch's qualified_name is a pure function of repository+revision,
# never of the symbol's own descriptor path).
#
# Requirement 7 ("existing AstCalls/Git/other provider behavior is
# unchanged") has no dedicated test here: the fix touches only this one
# frozenset construction inside `SCIPAdapter.extract()`, and `grep` confirms
# no other module imports anything this diff touches -- proven instead by
# the full regression suite (`tests/test_ast_calls_adapter.py`,
# `tests/test_git_adapter.py`, etc.) passing unchanged, reported separately.


def _missing_symbol_information_index(*symbols: str) -> bytes:
    """A GAP-9 real-shape fixture: each `symbols` entry has a genuine
    Definition-role Occurrence but *no* SymbolInformation entry of its
    own -- only one of its members does, exactly matching the real shape
    confirmed against requests' `Response#`/click's `Command#` (a large
    top-level class's own SymbolInformation entry missing while its
    members' entries are present). Each symbol gets its own document."""
    docs = []
    for i, symbol in enumerate(symbols):
        definition = occurrence(symbol, roles=1, range_=(10, 0, 4))
        member_symbol = symbol.rstrip("#") + "#member()."
        member_info = symbol_information(member_symbol, kind=26)  # Method
        docs.append(document(f"src/file{i}.ts", occurrences=(definition,), symbols=(member_info,)))
    return scip_index(documents=tuple(docs))


def test_gap9_definition_without_symbol_information_gets_local_identity(tmp_path: Path) -> None:
    """Requirement 1: a Definition-role Occurrence with no matching
    SymbolInformation entry resolves to its own local identity, not the
    "not defined anywhere in this index" external-library fallback."""
    symbol = "scip-test npm pkg 1.0.0 src/`a.ts`/Missing#"
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_missing_symbol_information_index(symbol))
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    entities = [e for e in normalized.entities if e.qualified_name == "src/`a.ts`/Missing#"]
    assert len(entities) == 1
    entity = entities[0]
    assert entity.base_type is not BaseEntityType.EXTERNAL_LIBRARY
    assert entity.source_location is not None
    assert entity.source_location.file_path == "src/file0.ts"


def test_gap9_descriptor_suffix_inference_classifies_missing_class_as_class(
    tmp_path: Path,
) -> None:
    """Requirement 2: with no SymbolInformation entry (kind defaults to
    UnspecifiedKind/0), `infer_base_type`'s existing, untouched
    descriptor-suffix fallback correctly classifies the missing symbol
    as CLASS purely from its own trailing `"#"`."""
    symbol = "scip-test npm pkg 1.0.0 src/`a.ts`/Missing#"
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_missing_symbol_information_index(symbol))
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    entity = next(e for e in normalized.entities if e.qualified_name == "src/`a.ts`/Missing#")
    assert entity.base_type is BaseEntityType.CLASS


def test_gap9_missing_symbol_information_gets_own_canonical_id(tmp_path: Path) -> None:
    """Requirement 3: the missing-SymbolInformation class gets its own
    canonical_id, deterministically derived from its own descriptor
    path -- never the package-level id every "not defined anywhere"
    external-library symbol from the same repository/revision would
    share."""
    symbol = "scip-test npm pkg 1.0.0 src/`a.ts`/Missing#"
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_missing_symbol_information_index(symbol))
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    entity = next(e for e in normalized.entities if e.qualified_name == "src/`a.ts`/Missing#")
    bogus_external_id = build_canonical_id(
        repository_id="repo1",
        repository_revision="external",
        qualified_name="npm:pkg@1.0.0",
        base_type=BaseEntityType.EXTERNAL_LIBRARY,
    )
    assert entity.canonical_id != bogus_external_id
    assert entity.canonical_id == build_canonical_id(
        repository_id="repo1",
        repository_revision="rev1",
        qualified_name="src/`a.ts`/Missing#",
        base_type=BaseEntityType.CLASS,
    )


def test_gap9_two_missing_definitions_do_not_collapse_onto_one_canonical_id(
    tmp_path: Path,
) -> None:
    """Requirement 4: the real click `Command#`/`Parameter#` shape --
    two entirely distinct real classes, both missing their own
    SymbolInformation entry, in the same repository+revision. Before
    this fix both collapsed onto the exact same canonical_id (the
    external-library branch's qualified_name never depends on the
    symbol's own descriptor path)."""
    sym_a = "scip-test npm pkg 1.0.0 src/`a.ts`/Foo#"
    sym_b = "scip-test npm pkg 1.0.0 src/`b.ts`/Bar#"
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_missing_symbol_information_index(sym_a, sym_b))
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    foo = next(e for e in normalized.entities if e.qualified_name == "src/`a.ts`/Foo#")
    bar = next(e for e in normalized.entities if e.qualified_name == "src/`b.ts`/Bar#")
    assert foo.canonical_id != bar.canonical_id
    assert foo.base_type is BaseEntityType.CLASS
    assert bar.base_type is BaseEntityType.CLASS
    assert not any(e.base_type is BaseEntityType.EXTERNAL_LIBRARY for e in normalized.entities)


def test_gap9_genuinely_external_symbol_without_definition_still_external_library(
    tmp_path: Path,
) -> None:
    """Requirement 5: a symbol referenced but never defined anywhere in
    the index (a genuine third-party import) is unaffected by this fix
    and still resolves as EXTERNAL_LIBRARY -- proving the fix doesn't
    overcorrect and misclassify real external references as local."""
    local_symbol = "scip-test npm pkg 1.0.0 src/`a.ts`/Local#"
    external_symbol = "scip-test npm other-pkg 2.0.0 src/`x.ts`/External#"
    definition = occurrence(local_symbol, roles=1, range_=(0, 0, 5))
    reference = occurrence(external_symbol, roles=0, range_=(1, 0, 8))
    sym_info = symbol_information(local_symbol, kind=7)  # this one DOES have SymbolInformation
    doc = document("src/a.ts", occurrences=(definition, reference), symbols=(sym_info,))
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    external = [e for e in normalized.entities if e.base_type is BaseEntityType.EXTERNAL_LIBRARY]
    assert len(external) == 1
    assert external[0].qualified_name == "npm:other-pkg@2.0.0"


def test_gap9_existing_symbol_information_entities_unchanged(tmp_path: Path) -> None:
    """Requirement 6: a symbol that already has both a Definition
    Occurrence and its own SymbolInformation entry (today's already-
    working case, `simple_class_index()`) is byte-for-byte unaffected by
    this fix -- same base_type, qualified_name, and canonical_id as
    before this change."""
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(simple_class_index())
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    entities = [e for e in normalized.entities if e.qualified_name == "src/`a.ts`/Foo#"]
    assert len(entities) == 1
    entity = entities[0]
    assert entity.base_type is BaseEntityType.CLASS
    assert entity.canonical_id == build_canonical_id(
        repository_id="repo1",
        repository_revision="rev1",
        qualified_name="src/`a.ts`/Foo#",
        base_type=BaseEntityType.CLASS,
    )


def test_gap9_locally_defined_ignores_capability_not_requested(tmp_path: Path) -> None:
    """When SYMBOL_DEFINITION is not among the requested capabilities,
    `definitions` stays `None` -- the fix's `(definitions or ())` guard
    must not raise, and `locally_defined` falls back to exactly its
    pre-fix, `Document.symbols`-only behavior."""
    symbol = "scip-test npm pkg 1.0.0 src/`a.ts`/Missing#"
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_missing_symbol_information_index(symbol))
    adapter = SCIPAdapter()
    requested = adapter.supported_capabilities - {Capability.SYMBOL_DEFINITION}
    result = adapter.extract(make_repository(tmp_path), requested)
    normalized = adapter.normalize(result)

    # No SYMBOL_DEFINITION requested -> no entities produced from
    # `definitions` at all (it's None and never processed downstream),
    # so "Missing#" never becomes any kind of entity -- this is
    # pre-existing, unrelated behavior, only confirmed unaffected here.
    assert not any(e.qualified_name == "src/`a.ts`/Missing#" for e in normalized.entities)


# ---------------------------------------------------------------------------
# GAP-10 fix: relationship object with no Definition Occurrence and no
# SymbolInformation, recovered when its own package_version matches this
# ingestion's revision AND its dotted-module prefix corresponds to a real
# file this same index indexed (a same-repository "phantom" symbol)
# ---------------------------------------------------------------------------
#
# Confirmed root cause: a real symbol can be named only as the *object* of
# another symbol's `is_implementation`/`is_type_definition` relationship,
# with zero Occurrences of any role and zero SymbolInformation entries
# anywhere in the index (real shape: flask's `tests.test_views`/Index#,
# named only inside `BetterIndex#`'s own relationships list). Before this
# fix, `_resolve_symbol` routed every such symbol through the "not defined
# anywhere -> external library" branch unconditionally, collapsing it onto
# the same shared package-level canonical_id every other same-repo phantom
# symbol in the run would also share -- confirmed against real
# django/flask/pytest/click data (0 on requests, its own narrower-scope
# index): 108/2/6/11 such symbols respectively.
#
# `package_version == revision` alone was proven UNSAFE by a second round
# of real-data investigation: scip-python attributes an *unresolved*
# import (stdlib, third-party, even a project's own external dependency)
# to the local project's package name and this exact revision whenever it
# can't resolve the import's true origin -- 18-95 such false positives
# observed per repository. The fixtures below use scip-python's own real
# descriptor convention (the whole dotted module path backtick-quoted as
# one unit, e.g. `` `tests.test_views`/Index# ``) and exercise both the
# safe recovery (the module's file IS one of this index's own indexed
# Documents) and the indexer-fallback false-positive this fix must reject
# (package_version matches but no such file was ever indexed).


def _relationship_object_without_definition_index(
    *, object_version: str, object_module_indexed: bool = True
) -> bytes:
    """`BetterIndex#` is a real, locally-defined class (Definition
    Occurrence + its own SymbolInformation) implementing `Index#` -- but
    `Index#` itself has zero Occurrences and zero SymbolInformation
    entries anywhere in this index, named only as the object of
    `BetterIndex#`'s own `is_implementation` relationship (the real
    GAP-10 shape, using scip-python's real descriptor convention: the
    whole dotted module path backtick-quoted as one unit). `object_version`
    controls whether `Index#`'s own symbol header claims the same
    `package_version` as this fixture's revision (`"rev1"`,
    `make_repository()`'s own default -- the same-repository-phantom
    recovery case) or a genuinely different one (a real external
    dependency, unaffected by this fix). `object_module_indexed` controls
    whether `pkg/b.py` (the file `` `pkg.b` `` maps to) is itself one of
    this index's own Documents -- False reproduces the confirmed
    indexer-fallback false-positive shape (matching version, no real file)."""
    subject_symbol = "scip-python python testrepo rev1 `pkg.a`/BetterIndex#"
    object_symbol = f"scip-python python testrepo {object_version} `pkg.b`/Index#"
    rel = relationship(object_symbol, is_implementation=True)
    subject_def = occurrence(subject_symbol, roles=1, range_=(0, 0, 4))
    subject_sym_info = symbol_information(subject_symbol, kind=7, relationships=(rel,))
    doc_a = document("pkg/a.py", occurrences=(subject_def,), symbols=(subject_sym_info,))
    docs = (doc_a, document("pkg/b.py")) if object_module_indexed else (doc_a,)
    return scip_index(documents=docs)


def test_gap10_same_repo_phantom_recovers_local_identity(tmp_path: Path) -> None:
    """Requirement: a relationship object with no Definition Occurrence
    and no SymbolInformation, whose own `package_version` matches this
    ingestion's revision, is recovered as its own real local entity --
    never routed through the "not defined anywhere" external-library
    branch."""
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(
        _relationship_object_without_definition_index(object_version="rev1")
    )
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name == "`pkg.b`/Index#"]
    assert len(matches) == 1
    entity = matches[0]
    assert entity.base_type is not BaseEntityType.EXTERNAL_LIBRARY
    assert entity.base_type is BaseEntityType.CLASS
    assert entity.source_location is None


def test_gap10_recovered_entity_gets_its_own_canonical_id() -> None:
    """The recovered entity's canonical_id is derived from its own real
    descriptor path -- never the shared package-level id every other
    same-repo phantom symbol in the same run would otherwise collapse
    onto."""
    from codex.provider.scip_adapter import _resolve_symbol

    object_symbol = "scip-python python testrepo rev1 `pkg.b`/Index#"
    resolved = _resolve_symbol(
        object_symbol,
        repository_id="repo1",
        revision="rev1",
        locally_defined=frozenset(),
        kind_by_symbol={},
        indexed_relative_paths=frozenset({"pkg/b.py"}),
    )
    assert resolved is not None
    assert resolved.base_type is BaseEntityType.CLASS
    assert resolved.inferred_from_relationship_only is True
    bogus_external_id = build_canonical_id(
        repository_id="repo1",
        repository_revision="external",
        qualified_name="python:testrepo@rev1",
        base_type=BaseEntityType.EXTERNAL_LIBRARY,
    )
    assert resolved.canonical_id != bogus_external_id
    assert resolved.canonical_id == build_canonical_id(
        repository_id="repo1",
        repository_revision="rev1",
        qualified_name="`pkg.b`/Index#",
        base_type=BaseEntityType.CLASS,
    )


def test_gap10_recovered_entity_tagged_with_provenance_role(tmp_path: Path) -> None:
    """The recovered entity carries an explicit provenance role marking
    it as inferred purely from a relationship fact -- never
    indistinguishable from a genuinely-observed local symbol (never
    backed by any Occurrence at all, unlike GAP-9's own recovery case)."""
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(
        _relationship_object_without_definition_index(object_version="rev1")
    )
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    entity = next(e for e in normalized.entities if e.qualified_name == "`pkg.b`/Index#")
    assert "scip:inferred-from-relationship-only" in entity.roles

    # The genuinely-observed subject entity must NOT carry this role.
    subject = next(e for e in normalized.entities if e.qualified_name == "`pkg.a`/BetterIndex#")
    assert "scip:inferred-from-relationship-only" not in subject.roles


def test_gap10_implements_evidence_uses_the_recovered_entity(tmp_path: Path) -> None:
    """The real fact this whole gap is about -- `BetterIndex implements
    Index` -- is preserved and correctly attributed to the recovered
    `Index#` entity, not lost or misattributed to a collapsed node."""
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(
        _relationship_object_without_definition_index(object_version="rev1")
    )
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    implements = [e for e in normalized.evidence if e.predicate is RelationshipType.IMPLEMENTS]
    assert len(implements) == 1
    subject_entity = next(e for e in normalized.entities if e.canonical_id == implements[0].subject)
    object_entity = next(e for e in normalized.entities if e.canonical_id == implements[0].object)
    assert subject_entity.qualified_name == "`pkg.a`/BetterIndex#"
    assert object_entity.qualified_name == "`pkg.b`/Index#"
    assert object_entity.base_type is BaseEntityType.CLASS


def test_gap10_genuinely_external_relationship_object_still_external_library(
    tmp_path: Path,
) -> None:
    """Regression guard: a relationship object whose own `package_version`
    genuinely differs from this ingestion's revision (a real external
    dependency, e.g. a base class from a third-party library) is
    unaffected by this fix and still resolves as EXTERNAL_LIBRARY --
    proving the fix doesn't overcorrect and misclassify real external
    references as local."""
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(
        _relationship_object_without_definition_index(object_version="2.0.0")
    )
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    external = [e for e in normalized.entities if e.base_type is BaseEntityType.EXTERNAL_LIBRARY]
    assert len(external) == 1
    assert external[0].qualified_name == "python:testrepo@2.0.0"
    assert "scip:inferred-from-relationship-only" not in external[0].roles


def test_gap10_indexer_fallback_false_positive_still_external_library(tmp_path: Path) -> None:
    """The confirmed real false-positive shape: `package_version` matches
    this ingestion's revision, but no file matching the object's own
    dotted-module prefix was ever indexed (scip-python's own "unresolved
    import" fallback attribution, verified against real django/flask/
    pytest/click data -- 18-95 such cases per repository). Must NOT be
    recovered as a local entity; must still resolve as EXTERNAL_LIBRARY."""
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(
        _relationship_object_without_definition_index(
            object_version="rev1", object_module_indexed=False
        )
    )
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name == "`pkg.b`/Index#"]
    assert matches == []
    external = [e for e in normalized.entities if e.base_type is BaseEntityType.EXTERNAL_LIBRARY]
    assert len(external) == 1
    assert external[0].qualified_name == "python:testrepo@rev1"
    assert "scip:inferred-from-relationship-only" not in external[0].roles


def test_gap10_unclassifiable_descriptor_falls_back_to_external_library() -> None:
    """A same-repo-version relationship object whose descriptor shape
    `infer_base_type` cannot confidently classify (a bare Parameter
    descriptor, trailing `")"`) is never fabricated into a fake CLASS --
    it falls through to the pre-existing external-library branch
    instead, exactly like any other symbol this adapter can't classify."""
    from codex.provider.scip_adapter import _resolve_symbol

    object_symbol = "scip-python python testrepo rev1 `pkg.b`/Index#foo().(name)"
    resolved = _resolve_symbol(
        object_symbol,
        repository_id="repo1",
        revision="rev1",
        locally_defined=frozenset(),
        kind_by_symbol={},
        indexed_relative_paths=frozenset({"pkg/b.py"}),
    )
    assert resolved is not None
    assert resolved.base_type is BaseEntityType.EXTERNAL_LIBRARY
    assert resolved.inferred_from_relationship_only is False


def test_gap10_deterministic_repeated_resolution() -> None:
    from codex.provider.scip_adapter import _resolve_symbol

    object_symbol = "scip-python python testrepo rev1 `pkg.b`/Index#"
    kwargs = dict(
        repository_id="repo1",
        revision="rev1",
        locally_defined=frozenset(),
        kind_by_symbol={},
        indexed_relative_paths=frozenset({"pkg/b.py"}),
    )
    first = _resolve_symbol(object_symbol, **kwargs)
    second = _resolve_symbol(object_symbol, **kwargs)
    assert first == second


# ---------------------------------------------------------------------------
# GAP-12 fix: module-identity `:` (Meta) descriptor symbols, previously
# silently dropped by `infer_base_type`'s descriptor-suffix fallback
# ---------------------------------------------------------------------------
#
# Confirmed root cause: `:` is `scip.proto`'s own `Descriptor.Suffix.Meta`
# punctuation -- a real, documented SCIP descriptor kind, not malformed
# data. scip-python emits exactly one such symbol per source file, always
# shaped `<dotted-module>/__init__:` -- the module's own self-identity,
# distinct from its FILE entity and from any class/function/variable
# defined inside it. `infer_base_type` never enumerated this suffix, so
# every such symbol resolved to `None` -> no entity -> every reference to
# it silently discarded (confirmed: 31,411 real occurrences lost across 5
# repositories, `docs/python-fidelity-gap-register.md`).


def _module_identity_reference_index() -> bytes:
    """`pkg/a.py` defines `Helper#` and, via a plain (non-Definition)
    Occurrence, references `pkg/b.py`'s own module identity -- the real
    GAP-12 shape: `pkg.b`'s `__init__:` symbol has a Definition Occurrence
    and SymbolInformation in `pkg/b.py` itself, and is *also* referenced
    from `pkg/a.py` (e.g. `import pkg.b`)."""
    module_b_symbol = "scip-python python testrepo rev1 `pkg.b`/__init__:"
    helper_symbol = "scip-python python testrepo rev1 `pkg.a`/Helper#"

    module_b_def = occurrence(module_b_symbol, roles=1, range_=(0, 0, 0))
    module_b_sym_info = symbol_information(module_b_symbol, kind=0)
    doc_b = document("pkg/b.py", occurrences=(module_b_def,), symbols=(module_b_sym_info,))

    helper_def = occurrence(helper_symbol, roles=1, range_=(0, 0, 6))
    helper_sym_info = symbol_information(helper_symbol, kind=7)
    module_b_reference = occurrence(module_b_symbol, roles=0, range_=(1, 0, 5))
    doc_a = document(
        "pkg/a.py",
        occurrences=(helper_def, module_b_reference),
        symbols=(helper_sym_info,),
    )
    return scip_index(documents=(doc_a, doc_b))


def test_gap12_module_identity_symbol_recovers_as_module_entity(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_module_identity_reference_index())
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name == "`pkg.b`/__init__:"]
    assert len(matches) == 1
    entity = matches[0]
    assert entity.base_type is BaseEntityType.MODULE
    assert entity.base_type is not BaseEntityType.FILE


def test_gap12_module_identity_reference_evidence_preserved(tmp_path: Path) -> None:
    """The real fact this gap is about -- `pkg.a` references `pkg.b`'s
    own module identity -- must be preserved, not silently discarded."""
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_module_identity_reference_index())
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    module_entity = next(e for e in normalized.entities if e.qualified_name == "`pkg.b`/__init__:")
    references = [
        e
        for e in normalized.evidence
        if e.predicate is RelationshipType.REFERENCES and e.object == module_entity.canonical_id
    ]
    assert len(references) == 1


def test_gap12_recovered_entity_gets_its_own_deterministic_canonical_id() -> None:
    from codex.provider.scip_adapter import _resolve_symbol

    symbol = "scip-python python testrepo rev1 `pkg.b`/__init__:"
    kwargs = dict(
        repository_id="repo1",
        revision="rev1",
        locally_defined=frozenset({symbol}),
        kind_by_symbol={},
        indexed_relative_paths=frozenset(),
    )
    first = _resolve_symbol(symbol, **kwargs)
    second = _resolve_symbol(symbol, **kwargs)
    assert first is not None
    assert first.base_type is BaseEntityType.MODULE
    assert first == second


def test_gap12_distinct_module_identities_do_not_collapse() -> None:
    """Two different real modules' own `__init__:` symbols must resolve
    to two different canonical IDs -- never collapse onto one shared
    identity the way pre-GAP-9/10 same-repo phantoms once did."""
    from codex.provider.scip_adapter import _resolve_symbol

    symbol_a = "scip-python python testrepo rev1 `pkg.a`/__init__:"
    symbol_b = "scip-python python testrepo rev1 `pkg.b`/__init__:"
    kwargs = dict(
        repository_id="repo1",
        revision="rev1",
        locally_defined=frozenset({symbol_a, symbol_b}),
        kind_by_symbol={},
        indexed_relative_paths=frozenset(),
    )
    resolved_a = _resolve_symbol(symbol_a, **kwargs)
    resolved_b = _resolve_symbol(symbol_b, **kwargs)
    assert resolved_a is not None
    assert resolved_b is not None
    assert resolved_a.canonical_id != resolved_b.canonical_id


def test_gap12_module_identity_symbol_becomes_a_candidate(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_module_identity_reference_index())
    registry = CapabilityRegistry()
    registry.register(SCIPAdapter(), ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.9))
    pipeline = IngestionPipeline(registry, InMemoryEvidenceStore())
    result = pipeline.run(make_repository(tmp_path))

    from codex.planner.retrieval import resolve_targets

    candidates = resolve_targets(result.graph_store, ["__init__"])
    module_candidates = [c for c in candidates if c.base_type is BaseEntityType.MODULE]
    assert len(module_candidates) == 1
    assert module_candidates[0].qualified_name == "`pkg.b`/__init__:"


def test_gap12_genuinely_external_module_identity_still_external_library(
    tmp_path: Path,
) -> None:
    """Regression guard: a module-identity symbol from a genuinely
    external package (own version, not this ingestion's revision) is
    unaffected by this fix -- still resolves as EXTERNAL_LIBRARY, never
    fabricated as a local MODULE entity."""
    external_module_symbol = "scip-python python otherpkg 2.0.0 `otherpkg.sub`/__init__:"
    referencing_symbol = "scip-python python testrepo rev1 `pkg.a`/Helper#"
    helper_def = occurrence(referencing_symbol, roles=1, range_=(0, 0, 6))
    helper_sym_info = symbol_information(referencing_symbol, kind=7)
    external_reference = occurrence(external_module_symbol, roles=0, range_=(1, 0, 5))
    doc = document(
        "pkg/a.py",
        occurrences=(helper_def, external_reference),
        symbols=(helper_sym_info,),
    )
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name == "`otherpkg.sub`/__init__:"]
    assert matches == []
    external = [e for e in normalized.entities if e.base_type is BaseEntityType.EXTERNAL_LIBRARY]
    assert len(external) == 1
    assert external[0].qualified_name == "python:otherpkg@2.0.0"


def test_gap12_existing_file_class_method_variable_descriptors_unchanged() -> None:
    """This fix adds exactly one new branch (`:` -> MODULE) to
    `infer_base_type`'s descriptor-suffix fallback -- every other
    suffix's classification must be byte-identical to before."""
    from codex.provider.scip.mapping import infer_base_type

    assert (
        infer_base_type(kind=0, symbol="scip-ts npm p 1.0.0 src/`a.ts`/Foo#")
        == BaseEntityType.CLASS
    )
    assert (
        infer_base_type(kind=0, symbol="scip-ts npm p 1.0.0 src/`a.ts`/Foo#bar().")
        == BaseEntityType.METHOD
    )
    assert (
        infer_base_type(kind=0, symbol="scip-ts npm p 1.0.0 src/`a.ts`/bar().")
        == BaseEntityType.FUNCTION
    )
    assert (
        infer_base_type(kind=0, symbol="scip-ts npm p 1.0.0 src/`a.ts`/Foo#field.")
        == BaseEntityType.VARIABLE
    )
    assert (
        infer_base_type(kind=0, symbol="scip-ts npm p 1.0.0 src/`a.ts`/") == BaseEntityType.FILE
    )
    assert (
        infer_base_type(
            kind=0, symbol="scip-ts npm p 1.0.0 src/`a.ts`/Foo#`<constructor>`().(message)"
        )
        is None
    )


# ---------------------------------------------------------------------------
# GAP-13 fix: AST/SCIP identity convergence for the `@typing.overload` idiom
# (and the structurally identical `@property`/`@x.setter` pair)
# ---------------------------------------------------------------------------
#
# Confirmed root cause (real flask/requests data, `docs/python-fidelity-
# gap-register.md`): scip-python emits exactly one Definition-role
# Occurrence for a redefined method-level symbol, pinned to the *first*
# textual `def` (e.g. the first `@overload` stub) -- while every later
# textual redefinition of the same name (further stubs, the real
# implementation) gets a ReadAccess-role Occurrence of the *same* symbol
# string instead, plus its own redundant SymbolInformation entry.
# `AstCallsAdapter`'s own `_DefinitionCollector` independently arrives at
# the *last* textual definition (Python's own "last binding wins" runtime
# semantics; unconditional, no decorator awareness). The exact-line
# identity key in `entity_resolver.py` (untouched by this fix) then never
# converges: SCIP reports the first stub's line, AST reports the real
# impl's line.
#
# Verified real-data discriminator: >1 `SymbolInformation` entries for
# the same symbol within one document does NOT fire for an ordinarily
# single-defined, merely-frequently-referenced symbol (confirmed: a
# flask symbol referenced 11 times still has exactly 1 SymbolInformation
# entry) -- it is a reliable signal of "this name has multiple textual
# definitions here."


def _overload_family_index(*, third_line: int = 10, symbol_info_count: int = 3) -> bytes:
    """`Helper#compute().` -- a `@typing.overload` family: the Definition-
    role Occurrence for the first stub (line 4), a ReadAccess-role
    Occurrence for the second stub (line 7), and a ReadAccess-role
    Occurrence for the real implementation (`third_line`) -- the exact
    real shape confirmed against flask's `App.template_test` and
    requests' `iter_slices`. `symbol_info_count` controls how many
    (redundant, matching-symbol) SymbolInformation entries are emitted --
    the GAP-13 recovery signal requires more than one."""
    symbol = "scip-python python testrepo rev1 `pkg.a`/Helper#compute()."
    occs = [
        occurrence(symbol, roles=1, range_=(4, 4, 11)),
        occurrence(symbol, roles=8, range_=(7, 4, 11)),
        occurrence(symbol, roles=8, range_=(third_line, 4, 11)),
    ]
    sym_infos = tuple(symbol_information(symbol, kind=0) for _ in range(symbol_info_count))
    doc = document("pkg/a.py", occurrences=tuple(occs), symbols=sym_infos)
    return scip_index(documents=(doc,))


def test_gap13_overload_family_symbol_recovers_last_occurrence_location(
    tmp_path: Path,
) -> None:
    """(1)+(2)+(3): one `@overload` family, multiple stubs + a real
    implementation, decodes and normalizes to the *last* textual
    definition's location, not the first stub's."""
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_overload_family_index())
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name == "`pkg.a`/Helper#compute()."]
    assert len(matches) == 1
    entity = matches[0]
    assert entity.source_location is not None
    assert entity.source_location.start_line == 10
    assert "scip:redefinition-family" in entity.roles


def test_gap13_recovered_entity_gets_deterministic_canonical_id() -> None:
    """(5): the recovered location still produces a deterministic
    canonical ID -- repeated resolution is byte-identical."""
    from codex.provider.scip.index import decode_index
    from codex.provider.scip_adapter import _collect_definitions

    data = _overload_family_index()
    first = _collect_definitions(decode_index(data))
    second = _collect_definitions(decode_index(data))
    assert first == second
    assert len(first) == 1
    assert first[0].range is not None
    assert first[0].range.start_line == 10
    assert first[0].is_redefinition_family is True


def test_gap13_ast_and_scip_converge_on_overload_family(tmp_path: Path) -> None:
    """(4): the core GAP-13 claim -- SCIP's own recovered entity for an
    overload family and AstCallsAdapter's independently-derived entity
    for the same real method converge onto one canonical entity (via
    `entity_resolver.resolve_entities`'s existing, untouched exact-line
    identity key), because both now agree on the real implementation's
    line. Mirrors `test_symbol_location_identity_converges_scip_and_ast_
    method` (`test_entity_resolution.py`)'s own established pattern for
    constructing each provider's `RepositorySymbol` directly."""
    from codex.ontology.entities import RepositorySymbol, build_canonical_id
    from codex.ontology.entities import SourceLocation as _SourceLocation
    from codex.resolution.entity_resolver import resolve_entities

    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_overload_family_index(third_line=10))
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)
    scip_entity = next(
        e for e in normalized.entities if e.qualified_name == "`pkg.a`/Helper#compute()."
    )
    assert scip_entity.source_location is not None
    assert scip_entity.source_location.start_line == 10

    ast_entity = RepositorySymbol(
        canonical_id=build_canonical_id(
            repository_id="repo1",
            repository_revision="rev1",
            qualified_name="pkg/a.py::Helper.compute",
            base_type=BaseEntityType.METHOD,
        ),
        repository_id="repo1",
        repository_revision="rev1",
        name="compute",
        qualified_name="pkg/a.py::Helper.compute",
        base_type=BaseEntityType.METHOD,
        source_location=_SourceLocation(
            file_path="pkg/a.py", start_line=10, end_line=11, start_column=4, end_column=17
        ),
        provider_ids={"ast_calls": "compute"},
    )

    resolution = resolve_entities([scip_entity, ast_entity])
    converged_ids = {e.canonical_id for e in resolution.entities}
    assert len(converged_ids) == 1
    (merged,) = resolution.entities
    assert merged.provider_ids.get("scip") == "Helper#compute()."
    assert merged.provider_ids.get("ast_calls") == "compute"


def test_gap13_implements_evidence_uses_the_recovered_entity(tmp_path: Path) -> None:
    """(6): the recovered entity remains fully usable as evidence
    subject/object -- a real IMPLEMENTS fact naming an overload-family
    symbol is preserved, not lost or misattributed."""
    symbol = "scip-python python testrepo rev1 `pkg.a`/Helper#compute()."
    subject_symbol = "scip-python python testrepo rev1 `pkg.a`/Impl#"
    rel = relationship(symbol, is_implementation=True)
    subject_def = occurrence(subject_symbol, roles=1, range_=(0, 0, 4))
    subject_info = symbol_information(subject_symbol, kind=7, relationships=(rel,))
    occs = (
        occurrence(symbol, roles=1, range_=(4, 4, 11)),
        occurrence(symbol, roles=8, range_=(7, 4, 11)),
        occurrence(symbol, roles=8, range_=(10, 4, 11)),
    )
    sym_infos = tuple(symbol_information(symbol, kind=0) for _ in range(3))
    doc = document(
        "pkg/a.py", occurrences=(subject_def, *occs), symbols=(subject_info, *sym_infos)
    )
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    implements = [e for e in normalized.evidence if e.predicate is RelationshipType.IMPLEMENTS]
    assert len(implements) == 1
    object_entity = next(e for e in normalized.entities if e.canonical_id == implements[0].object)
    assert object_entity.qualified_name == "`pkg.a`/Helper#compute()."
    assert object_entity.source_location is not None
    assert object_entity.source_location.start_line == 10


def test_gap13_distinct_symbols_in_different_modules_remain_distinct(tmp_path: Path) -> None:
    """(7): two different modules each with their own overload family
    for a same-named method must never collapse into one entity."""
    symbol_a = "scip-python python testrepo rev1 `pkg.a`/Helper#compute()."
    symbol_b = "scip-python python testrepo rev1 `pkg.b`/Helper#compute()."
    docs = []
    for i, symbol in enumerate((symbol_a, symbol_b)):
        occs = (
            occurrence(symbol, roles=1, range_=(4, 4, 11)),
            occurrence(symbol, roles=8, range_=(7, 4, 11)),
            occurrence(symbol, roles=8, range_=(10, 4, 11)),
        )
        sym_infos = tuple(symbol_information(symbol, kind=0) for _ in range(3))
        path = f"pkg/{'a' if i == 0 else 'b'}.py"
        docs.append(document(path, occurrences=occs, symbols=sym_infos))
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=tuple(docs)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = {
        e.qualified_name: e for e in normalized.entities if "Helper#compute" in e.qualified_name
    }
    assert len(matches) == 2
    ids = {e.canonical_id for e in matches.values()}
    assert len(ids) == 2


def test_gap13_distinct_methods_and_classes_remain_distinct(tmp_path: Path) -> None:
    """(8): an overload family in one class must never collapse with an
    unrelated, differently-named method or a different class."""
    family_symbol = "scip-python python testrepo rev1 `pkg.a`/Helper#compute()."
    other_method_symbol = "scip-python python testrepo rev1 `pkg.a`/Helper#other()."
    other_class_symbol = "scip-python python testrepo rev1 `pkg.a`/Other#compute()."
    family_occs = (
        occurrence(family_symbol, roles=1, range_=(4, 4, 11)),
        occurrence(family_symbol, roles=8, range_=(7, 4, 11)),
        occurrence(family_symbol, roles=8, range_=(10, 4, 11)),
    )
    family_infos = tuple(symbol_information(family_symbol, kind=0) for _ in range(3))
    other_method_def = occurrence(other_method_symbol, roles=1, range_=(20, 4, 9))
    other_method_info = symbol_information(other_method_symbol, kind=0)
    other_class_def = occurrence(other_class_symbol, roles=1, range_=(30, 4, 11))
    other_class_info = symbol_information(other_class_symbol, kind=0)
    doc = document(
        "pkg/a.py",
        occurrences=(*family_occs, other_method_def, other_class_def),
        symbols=(*family_infos, other_method_info, other_class_info),
    )
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    ids_by_name = {e.qualified_name: e.canonical_id for e in normalized.entities}
    assert ids_by_name["`pkg.a`/Helper#compute()."] != ids_by_name["`pkg.a`/Helper#other()."]
    assert ids_by_name["`pkg.a`/Helper#compute()."] != ids_by_name["`pkg.a`/Other#compute()."]
    assert ids_by_name["`pkg.a`/Helper#other()."] != ids_by_name["`pkg.a`/Other#compute()."]


def test_gap13_non_overloaded_function_byte_equivalent(tmp_path: Path) -> None:
    """(9): a symbol with exactly one SymbolInformation entry (the
    overwhelming majority of real symbols) is completely untouched by
    this fix -- same location, no new role, `is_redefinition_family`
    False."""
    from codex.provider.scip.index import decode_index
    from codex.provider.scip_adapter import _collect_definitions

    symbol = "scip-python python testrepo rev1 `pkg.a`/plain_function()."
    occs = (occurrence(symbol, roles=1, range_=(4, 4, 17)),)
    doc = document("pkg/a.py", occurrences=occs, symbols=(symbol_information(symbol, kind=0),))
    data = scip_index(documents=(doc,))
    records = _collect_definitions(decode_index(data))
    assert len(records) == 1
    assert records[0].is_redefinition_family is False
    assert records[0].range is not None
    assert records[0].range.start_line == 4


def test_gap13_far_away_reference_never_joins_the_family(tmp_path: Path) -> None:
    """(10)-adjacent safety check: a plain, unrelated later reference to
    the same symbol name -- far outside `_OVERLOAD_FAMILY_LINE_WINDOW`
    -- must never be picked up as "the real implementation." Matches
    the real-data case that motivated this window in the first place
    (a call site 368 lines away from a genuine property pair)."""
    symbol = "scip-python python testrepo rev1 `pkg.a`/Helper#compute()."
    occs = (
        occurrence(symbol, roles=1, range_=(4, 4, 11)),
        occurrence(symbol, roles=8, range_=(7, 4, 11)),
        occurrence(symbol, roles=8, range_=(500, 4, 11)),  # far outside the window
    )
    sym_infos = tuple(symbol_information(symbol, kind=0) for _ in range(2))
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    entity = next(e for e in normalized.entities if e.qualified_name == "`pkg.a`/Helper#compute().")
    # Only the Definition (4) and its adjacent stub (7) form a family (gap
    # 7->500 exceeds the window); the recovered location must be 7, never
    # the far-away 500.
    assert entity.source_location is not None
    assert entity.source_location.start_line == 7
    assert "scip:redefinition-family" in entity.roles


def test_gap13_external_overload_like_symbol_not_fabricated_local(tmp_path: Path) -> None:
    """(11): a genuinely external symbol referenced multiple times (no
    Definition-role Occurrence at all in this index -- exactly how a
    real external symbol always appears, since scip-python only ever
    emits Definition role for symbols it actually indexed as locally
    defined) is unaffected by this fix, however many times it's
    referenced -- still resolves as EXTERNAL_LIBRARY, never fabricated
    as local. `_redefinition_family_locations`'s own gate (requiring a
    real Definition-role anchor) protects this independently of the
    package/revision check."""
    subject_symbol = "scip-python python testrepo rev1 `pkg.a`/Impl#"
    external_symbol = "scip-python python otherpkg 2.0.0 `otherpkg.sub`/Base#compute()."
    subject_def = occurrence(subject_symbol, roles=1, range_=(0, 0, 4))
    subject_info = symbol_information(subject_symbol, kind=7)
    # Referenced 3 times, never Defined -- the real external shape.
    external_occs = (
        occurrence(external_symbol, roles=8, range_=(4, 4, 11)),
        occurrence(external_symbol, roles=8, range_=(7, 4, 11)),
        occurrence(external_symbol, roles=8, range_=(10, 4, 11)),
    )
    doc = document(
        "pkg/a.py",
        occurrences=(subject_def, *external_occs),
        symbols=(subject_info,),
    )
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if "otherpkg.sub" in e.qualified_name]
    assert matches == []
    external = [e for e in normalized.entities if e.base_type is BaseEntityType.EXTERNAL_LIBRARY]
    assert len(external) == 1
    assert external[0].qualified_name == "python:otherpkg@2.0.0"


def test_gap13_gap9_locally_defined_behavior_unchanged() -> None:
    """(12): GAP-9's own regression-locked test suite passing (run
    separately, `-k gap9`) already proves this; this spot check
    confirms the specific mechanism (`locally_defined` membership,
    `_resolve_symbol`'s own branch) this fix never touches remains
    reachable and correct for a plain, single-definition symbol."""
    from codex.provider.scip_adapter import _resolve_symbol

    symbol = "scip-python python testrepo rev1 `pkg.a`/Foo#"
    resolved = _resolve_symbol(
        symbol,
        repository_id="repo1",
        revision="rev1",
        locally_defined=frozenset({symbol}),
        kind_by_symbol={},
        indexed_relative_paths=frozenset(),
    )
    assert resolved is not None
    assert resolved.base_type is BaseEntityType.CLASS


def test_gap13_gap12_module_identity_unchanged(tmp_path: Path) -> None:
    """(14): GAP-12's `:` (Meta) module-identity symbols -- a
    completely different descriptor shape from the redefinition-family
    logic above -- must remain unaffected. A colon-suffixed symbol only
    ever gets exactly one SymbolInformation entry per document in real
    data, so it never enters this fix's own code path at all."""
    symbol = "scip-python python testrepo rev1 `pkg.a`/__init__:"
    occs = (occurrence(symbol, roles=1, range_=(0, 0, 0)),)
    doc = document("pkg/a.py", occurrences=occs, symbols=(symbol_information(symbol, kind=0),))
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    entity = next(e for e in normalized.entities if e.qualified_name == "`pkg.a`/__init__:")
    assert entity.base_type is BaseEntityType.MODULE
    assert "scip:redefinition-family" not in entity.roles


# ---------------------------------------------------------------------------
# GAP-14 fix: `_redefinition_family_locations` now anchors on the earliest
# *Definition-role* occurrence, not unconditionally on the earliest
# occurrence of any role.
#
# Confirmed root cause (real django/click/pytest/requests data,
# `docs/python-fidelity-gap-register.md`): a same-file `ReadAccess`
# reference to a redefined symbol can legally appear *before* that
# symbol's own textual definition (e.g. an earlier sibling method calling
# `self.foo(...)` before `foo` is itself defined lower in the class).
# GAP-13's original fix anchored the window walk on `ordered[0]`
# unconditionally; when that occurrence is such an early reference rather
# than the real Definition-role occurrence, the very next gap (reference
# -> real first definition) almost always exceeds the window and the walk
# breaks immediately, so the true family is never reached -- SCIP stays
# anchored on the pre-GAP-13 lone-Definition-occurrence location (the
# first overload stub), and AST/SCIP never converge, exactly like an
# unfixed GAP-13 case.
# ---------------------------------------------------------------------------


def _overload_family_with_leading_reference_index(
    *, leading_lines: tuple[int, ...] = (1,), third_line: int = 10
) -> bytes:
    """Same real shape as `_overload_family_index` (Definition at line 4,
    ReadAccess stub at line 7, ReadAccess impl at `third_line`), plus one
    or more extra ReadAccess occurrences of the *same* symbol at
    `leading_lines` -- all strictly before the Definition-role occurrence
    -- reproducing requests' `Response.iter_content` (referenced at line
    859 from `iter_lines`, defined at 907) and django's `Field.choices`
    (referenced 5 times at lines 261-368, defined at 584)."""
    symbol = "scip-python python testrepo rev1 `pkg.a`/Helper#compute()."
    occs = [occurrence(symbol, roles=8, range_=(line, 4, 11)) for line in leading_lines]
    occs += [
        occurrence(symbol, roles=1, range_=(4, 4, 11)),
        occurrence(symbol, roles=8, range_=(7, 4, 11)),
        occurrence(symbol, roles=8, range_=(third_line, 4, 11)),
    ]
    sym_infos = tuple(symbol_information(symbol, kind=0) for _ in range(3))
    doc = document("pkg/a.py", occurrences=tuple(occs), symbols=sym_infos)
    return scip_index(documents=(doc,))


def test_gap14_reference_before_single_definition_is_unaffected(tmp_path: Path) -> None:
    """(1): a symbol with only one real textual definition (no
    redefinition family -- `symbol_info_count` <= 1) plus an earlier
    ReadAccess reference must be completely unaffected by this fix: no
    family signal exists, so `_collect_definitions` falls back to the
    lone Definition occurrence's own location, exactly as before GAP-13
    ever existed."""
    symbol = "scip-python python testrepo rev1 `pkg.a`/Helper#compute()."
    occs = (
        occurrence(symbol, roles=8, range_=(1, 4, 11)),  # earlier reference
        occurrence(symbol, roles=1, range_=(4, 4, 11)),  # the one real definition
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=(symbol_information(symbol, kind=0),))
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    entity = next(e for e in normalized.entities if e.qualified_name == "`pkg.a`/Helper#compute().")
    assert entity.source_location is not None
    assert entity.source_location.start_line == 4
    assert "scip:redefinition-family" not in entity.roles


def test_gap14_reference_before_overload_family_still_recovers_last_location(
    tmp_path: Path,
) -> None:
    """(2): the core GAP-14 claim. A single early ReadAccess reference,
    strictly before the real `@overload` family (Definition at line 4,
    stub at 7, implementation at 10), must not prevent recovery of the
    real implementation's location -- reproduces requests'
    `Response.iter_content` and django's `Field.choices` exactly."""
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(
        _overload_family_with_leading_reference_index(leading_lines=(1,))
    )
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    entity = next(e for e in normalized.entities if e.qualified_name == "`pkg.a`/Helper#compute().")
    assert entity.source_location is not None
    assert entity.source_location.start_line == 10
    assert "scip:redefinition-family" in entity.roles


def test_gap14_multiple_earlier_references_before_family_still_recovers(
    tmp_path: Path,
) -> None:
    """(3): several early references (django's `Field.choices` has 5)
    must all be ignored equally -- the anchor is the earliest
    Definition-role occurrence regardless of how many references
    precede it."""
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(
        _overload_family_with_leading_reference_index(leading_lines=(1, 2, 3, 3, 3))
    )
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    entity = next(e for e in normalized.entities if e.qualified_name == "`pkg.a`/Helper#compute().")
    assert entity.source_location is not None
    assert entity.source_location.start_line == 10
    assert "scip:redefinition-family" in entity.roles


def test_gap14_reference_between_overload_members_still_recovers(tmp_path: Path) -> None:
    """(4): a reference that lands textually *between* two real family
    members (still within the window of its predecessor) must not break
    the chain -- it is simply folded in like any other occurrence on the
    way to the true last definition, exactly as the pre-GAP-14 walk
    already did once correctly anchored."""
    symbol = "scip-python python testrepo rev1 `pkg.a`/Helper#compute()."
    occs = (
        occurrence(symbol, roles=1, range_=(4, 4, 11)),  # Definition (first stub)
        occurrence(symbol, roles=8, range_=(6, 4, 11)),  # unrelated in-between reference
        occurrence(symbol, roles=8, range_=(7, 4, 11)),  # second stub
        occurrence(symbol, roles=8, range_=(10, 4, 11)),  # real implementation
    )
    sym_infos = tuple(symbol_information(symbol, kind=0) for _ in range(3))
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    entity = next(e for e in normalized.entities if e.qualified_name == "`pkg.a`/Helper#compute().")
    assert entity.source_location is not None
    assert entity.source_location.start_line == 10
    assert "scip:redefinition-family" in entity.roles


def test_gap14_reference_far_after_family_never_extends_past_it(tmp_path: Path) -> None:
    """(5): combined with an early reference (so the anchor fix is
    actually exercised), a later reference far outside the window must
    still never be picked up as part of the family -- the recovered
    location stays at the true last definition, not the far-away
    reference. Matches GAP-13's own already-established far-away
    safety check, now proven robust to an early reference too."""
    symbol = "scip-python python testrepo rev1 `pkg.a`/Helper#compute()."
    occs = (
        occurrence(symbol, roles=8, range_=(1, 4, 11)),  # early, unrelated reference
        occurrence(symbol, roles=1, range_=(4, 4, 11)),  # Definition (first stub)
        occurrence(symbol, roles=8, range_=(7, 4, 11)),  # real implementation
        occurrence(symbol, roles=8, range_=(500, 4, 11)),  # far outside the window
    )
    sym_infos = tuple(symbol_information(symbol, kind=0) for _ in range(2))
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    entity = next(e for e in normalized.entities if e.qualified_name == "`pkg.a`/Helper#compute().")
    assert entity.source_location is not None
    assert entity.source_location.start_line == 7
    assert "scip:redefinition-family" in entity.roles


def test_gap14_mixed_before_inside_after_references(tmp_path: Path) -> None:
    """(6): references before, between, and (far) after the real family
    all in one index -- the recovered location must still land exactly
    on the true last definition."""
    symbol = "scip-python python testrepo rev1 `pkg.a`/Helper#compute()."
    occs = (
        occurrence(symbol, roles=8, range_=(1, 4, 11)),  # before
        occurrence(symbol, roles=1, range_=(4, 4, 11)),  # Definition (first stub)
        occurrence(symbol, roles=8, range_=(6, 4, 11)),  # between
        occurrence(symbol, roles=8, range_=(7, 4, 11)),  # second stub
        occurrence(symbol, roles=8, range_=(10, 4, 11)),  # real implementation
        occurrence(symbol, roles=8, range_=(400, 4, 11)),  # far after
    )
    sym_infos = tuple(symbol_information(symbol, kind=0) for _ in range(3))
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    entity = next(e for e in normalized.entities if e.qualified_name == "`pkg.a`/Helper#compute().")
    assert entity.source_location is not None
    assert entity.source_location.start_line == 10
    assert "scip:redefinition-family" in entity.roles


def test_gap14_ast_and_scip_converge_despite_early_reference(tmp_path: Path) -> None:
    """(7): the core GAP-14 fix proven at the identity-resolution layer,
    not just at `_collect_definitions` -- SCIP's recovered entity (now
    immune to the early reference) and AstCallsAdapter's independently-
    derived entity for the same real method converge onto one canonical
    entity via `entity_resolver.resolve_entities`'s existing, untouched
    exact-line identity key. Mirrors GAP-13's own
    `test_gap13_ast_and_scip_converge_on_overload_family`."""
    from codex.ontology.entities import RepositorySymbol, build_canonical_id
    from codex.ontology.entities import SourceLocation as _SourceLocation
    from codex.resolution.entity_resolver import resolve_entities

    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(
        _overload_family_with_leading_reference_index(leading_lines=(1,), third_line=10)
    )
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)
    scip_entity = next(
        e for e in normalized.entities if e.qualified_name == "`pkg.a`/Helper#compute()."
    )
    assert scip_entity.source_location is not None
    assert scip_entity.source_location.start_line == 10

    ast_entity = RepositorySymbol(
        canonical_id=build_canonical_id(
            repository_id="repo1",
            repository_revision="rev1",
            qualified_name="pkg/a.py::Helper.compute",
            base_type=BaseEntityType.METHOD,
        ),
        repository_id="repo1",
        repository_revision="rev1",
        name="compute",
        qualified_name="pkg/a.py::Helper.compute",
        base_type=BaseEntityType.METHOD,
        provider_ids={"ast_calls": "compute"},
        source_location=_SourceLocation(file_path="pkg/a.py", start_line=10, end_line=10),
    )

    resolved = resolve_entities([scip_entity, ast_entity]).entities
    matching = [e for e in resolved if e.source_location and e.source_location.start_line == 10]
    canonical_ids = {e.canonical_id for e in matching}
    assert len(canonical_ids) == 1, "SCIP and AST identities must converge to one canonical_id"


def test_gap14_evidence_signatures_and_locations_preserved(tmp_path: Path) -> None:
    """(8): the recovered entity still carries a real, deterministic
    source location and its provenance role, and repeated normalization
    is byte-identical -- no evidence/signature/location is lost or
    fabricated by ignoring the early reference."""
    data = _overload_family_with_leading_reference_index(leading_lines=(1, 2))
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(data)
    adapter = SCIPAdapter()

    first_normalized = adapter.normalize(
        adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    )
    second_normalized = adapter.normalize(
        adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    )
    first_entity = next(
        e for e in first_normalized.entities if e.qualified_name == "`pkg.a`/Helper#compute()."
    )
    second_entity = next(
        e for e in second_normalized.entities if e.qualified_name == "`pkg.a`/Helper#compute()."
    )
    assert first_entity.canonical_id == second_entity.canonical_id
    assert first_entity.source_location == second_entity.source_location
    assert first_entity.source_location is not None
    assert first_entity.source_location.file_path == "pkg/a.py"
    assert first_entity.source_location.start_line == 10
    assert sorted(first_entity.roles) == sorted(second_entity.roles)


def test_gap14_implements_relationship_continuity_despite_early_reference(
    tmp_path: Path,
) -> None:
    """(9): an `IMPLEMENTS` relationship naming the redefined symbol as
    object still resolves to the recovered (early-reference-immune)
    entity, exactly as GAP-13's own
    `test_gap13_implements_evidence_uses_the_recovered_entity` requires --
    now proven robust to an early reference to that object symbol too."""
    symbol = "scip-python python testrepo rev1 `pkg.a`/Helper#compute()."
    subject_symbol = "scip-python python testrepo rev1 `pkg.a`/Impl#"
    rel = relationship(symbol, is_implementation=True)
    subject_def = occurrence(subject_symbol, roles=1, range_=(0, 0, 4))
    subject_info = symbol_information(subject_symbol, kind=7, relationships=(rel,))
    occs = (
        occurrence(symbol, roles=8, range_=(1, 4, 11)),  # early, unrelated reference
        occurrence(symbol, roles=1, range_=(4, 4, 11)),
        occurrence(symbol, roles=8, range_=(7, 4, 11)),
        occurrence(symbol, roles=8, range_=(10, 4, 11)),
    )
    sym_infos = tuple(symbol_information(symbol, kind=0) for _ in range(3))
    doc = document(
        "pkg/a.py", occurrences=(subject_def, *occs), symbols=(subject_info, *sym_infos)
    )
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    implements = [e for e in normalized.evidence if e.predicate is RelationshipType.IMPLEMENTS]
    assert len(implements) == 1
    object_entity = next(e for e in normalized.entities if e.canonical_id == implements[0].object)
    assert object_entity.qualified_name == "`pkg.a`/Helper#compute()."
    assert object_entity.source_location is not None
    assert object_entity.source_location.start_line == 10


def test_gap14_distinct_symbols_across_modules_remain_distinct(tmp_path: Path) -> None:
    """(10): two different modules, each with their own early-reference-
    preceded redefinition family for a same-named method, must never
    collapse into one entity."""
    docs = []
    for module in ("a", "b"):
        symbol = f"scip-python python testrepo rev1 `pkg.{module}`/Helper#compute()."
        occs = (
            occurrence(symbol, roles=8, range_=(1, 4, 11)),
            occurrence(symbol, roles=1, range_=(4, 4, 11)),
            occurrence(symbol, roles=8, range_=(7, 4, 11)),
            occurrence(symbol, roles=8, range_=(10, 4, 11)),
        )
        sym_infos = tuple(symbol_information(symbol, kind=0) for _ in range(3))
        docs.append(document(f"pkg/{module}.py", occurrences=occs, symbols=sym_infos))
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=tuple(docs)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = {
        e.qualified_name: e for e in normalized.entities if "Helper#compute" in e.qualified_name
    }
    assert len(matches) == 2
    ids = {e.canonical_id for e in matches.values()}
    assert len(ids) == 2
    for e in matches.values():
        assert e.source_location is not None
        assert e.source_location.start_line == 10


def test_gap14_distinct_symbols_across_classes_remain_distinct(tmp_path: Path) -> None:
    """(11): two different classes in the same module, each with their
    own early-reference-preceded redefinition family for a same-named
    method, must never collapse into one entity."""
    occs = []
    sym_infos = []
    for cls in ("Helper", "Other"):
        symbol = f"scip-python python testrepo rev1 `pkg.a`/{cls}#compute()."
        occs += [
            occurrence(symbol, roles=8, range_=(1, 4, 11)),
            occurrence(symbol, roles=1, range_=(4, 4, 11)),
            occurrence(symbol, roles=8, range_=(7, 4, 11)),
            occurrence(symbol, roles=8, range_=(10, 4, 11)),
        ]
        sym_infos += [symbol_information(symbol, kind=0) for _ in range(3)]
    doc = document("pkg/a.py", occurrences=tuple(occs), symbols=tuple(sym_infos))
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    helper = next(e for e in normalized.entities if e.qualified_name == "`pkg.a`/Helper#compute().")
    other = next(e for e in normalized.entities if e.qualified_name == "`pkg.a`/Other#compute().")
    assert helper.canonical_id != other.canonical_id


def test_gap14_local_symbols_remain_distinct_and_unaffected(tmp_path: Path) -> None:
    """(12): a local (function-scoped) symbol, however many times it is
    referenced before its own definition, never becomes an entity at
    all -- `is_local_symbol` excludes it before this fix's own logic
    ever runs, exactly as before."""
    local_symbol = "local 3"
    real_symbol = "scip-python python testrepo rev1 `pkg.a`/Helper#compute()."
    occs = (
        occurrence(local_symbol, roles=8, range_=(1, 4, 11)),
        occurrence(local_symbol, roles=1, range_=(2, 4, 11)),
        occurrence(real_symbol, roles=8, range_=(3, 4, 11)),
        occurrence(real_symbol, roles=1, range_=(4, 4, 11)),
        occurrence(real_symbol, roles=8, range_=(7, 4, 11)),
        occurrence(real_symbol, roles=8, range_=(10, 4, 11)),
    )
    sym_infos = tuple(symbol_information(real_symbol, kind=0) for _ in range(3))
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    assert all("local" not in e.qualified_name for e in normalized.entities)
    entity = next(e for e in normalized.entities if e.qualified_name == "`pkg.a`/Helper#compute().")
    assert entity.source_location is not None
    assert entity.source_location.start_line == 10


def test_gap14_external_symbol_referenced_before_local_definition_not_fabricated(
    tmp_path: Path,
) -> None:
    """(13): a genuinely external symbol, referenced (ReadAccess only,
    never Defined -- the real shape for an external symbol) *before* an
    unrelated local redefinition family appears later in the same
    document, must still resolve as EXTERNAL_LIBRARY, never fabricated
    as local just because it happened to be seen early."""
    external_symbol = "scip-python python otherpkg 2.0.0 `otherpkg.sub`/Base#compute()."
    local_symbol = "scip-python python testrepo rev1 `pkg.a`/Helper#compute()."
    occs = (
        occurrence(external_symbol, roles=8, range_=(1, 4, 11)),
        occurrence(external_symbol, roles=8, range_=(2, 4, 11)),
        occurrence(local_symbol, roles=1, range_=(4, 4, 11)),
        occurrence(local_symbol, roles=8, range_=(7, 4, 11)),
        occurrence(local_symbol, roles=8, range_=(10, 4, 11)),
    )
    sym_infos = tuple(symbol_information(local_symbol, kind=0) for _ in range(3))
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if "otherpkg.sub" in e.qualified_name]
    assert matches == []
    external = [e for e in normalized.entities if e.base_type is BaseEntityType.EXTERNAL_LIBRARY]
    assert len(external) == 1
    assert external[0].qualified_name == "python:otherpkg@2.0.0"
    local_entity = next(
        e for e in normalized.entities if e.qualified_name == "`pkg.a`/Helper#compute()."
    )
    assert local_entity.source_location is not None
    assert local_entity.source_location.start_line == 10


def test_gap14_non_overloaded_function_with_early_reference_byte_equivalent(
    tmp_path: Path,
) -> None:
    """(14): a plain, non-redefined function referenced before its own
    definition (completely ordinary, legal Python -- e.g. a module-
    level helper called from an earlier-defined function) must produce
    byte-identical output whether or not this fix exists, because the
    family signal (`symbol_info_count` > 1) never fires for it."""
    symbol = "scip-python python testrepo rev1 `pkg.a`/helper()."
    occs = (
        occurrence(symbol, roles=8, range_=(1, 4, 10)),
        occurrence(symbol, roles=1, range_=(5, 0, 10)),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=(symbol_information(symbol, kind=0),))
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    entity = next(e for e in normalized.entities if e.qualified_name == "`pkg.a`/helper().")
    assert entity.source_location is not None
    assert entity.source_location.start_line == 5
    assert "scip:redefinition-family" not in entity.roles


def test_gap14_parameter_descriptor_with_repeated_references_unaffected(tmp_path: Path) -> None:
    """Parameter descriptors (trailing `)`) are excluded before this
    fix's own logic runs (matching GAP-13's own established behavior) --
    a parameter name referenced many times, including before any
    Definition-role occurrence, never becomes an entity and never
    affects the enclosing function's own recovery."""
    param_symbol = "scip-python python testrepo rev1 `pkg.a`/helper().(value)"
    fn_symbol = "scip-python python testrepo rev1 `pkg.a`/helper()."
    occs = (
        occurrence(param_symbol, roles=8, range_=(1, 4, 9)),
        occurrence(param_symbol, roles=8, range_=(2, 4, 9)),
        occurrence(param_symbol, roles=1, range_=(5, 15, 20)),
        occurrence(fn_symbol, roles=1, range_=(5, 0, 10)),
        occurrence(fn_symbol, roles=8, range_=(8, 0, 10)),
        occurrence(fn_symbol, roles=8, range_=(12, 0, 10)),
    )
    sym_infos = (
        symbol_information(param_symbol, kind=0),
        symbol_information(param_symbol, kind=0),
        symbol_information(fn_symbol, kind=0),
        symbol_information(fn_symbol, kind=0),
        symbol_information(fn_symbol, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    assert all(not e.qualified_name.endswith(")") for e in normalized.entities)
    fn_entity = next(e for e in normalized.entities if e.qualified_name == "`pkg.a`/helper().")
    assert fn_entity.source_location is not None
    assert fn_entity.source_location.start_line == 12
    assert "scip:redefinition-family" in fn_entity.roles
