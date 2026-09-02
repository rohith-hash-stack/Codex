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
