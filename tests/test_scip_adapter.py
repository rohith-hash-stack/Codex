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


# ---------------------------------------------------------------------------
# FND-1 fix: distinct nested/local Python symbols (two closures/local classes
# sharing one name because they're nested inside *different* enclosing
# functions/methods) no longer collapse into one canonical identity.
#
# Confirmed root cause (real django/click/flask data, `docs/python-fidelity-
# gap-register.md` and `docs/resources.md`'s fifth material finding):
# scip-python's descriptor grammar encodes an enclosing *class*, never an
# enclosing *function* -- so a closure nested inside two different methods
# of the same class (django's `AbstractBaseUser.check_password`'s and
# `.acheck_password`'s own, separate `setter` closures; django's
# `Library#dec()`, five distinct closures in five different methods; click's
# `Group#decorator()`, flask's `App#decorator()`/`Blueprint#decorator()`/
# `Scaffold#decorator()`) all serialize to one identical descriptor string,
# even though each is a genuinely different real Python entity. Confirmed via
# direct wire-level inspection that `SymbolInformation.enclosing_symbol`
# (`scip.proto` field 8, designed for exactly this) is never populated by
# real scip-python@0.6.6 output -- `_nested_symbol_disambiguation` instead
# uses each Definition-role occurrence's own source *position*: the nearest
# textually-preceding real (non-local, non-Parameter) symbol definition in
# the same document is a deterministic, structural proxy for "the real
# entity whose body textually contains this occurrence."
#
# The reliable discriminator from GAP-13/14's own signal: more than one
# *real Definition-role Occurrence* for the same descriptor, each with a
# genuinely *different* nearest-enclosing scope. A symbol with 2+ Definition-
# role occurrences sharing the *same* enclosing scope (or none at all -- a
# module-level platform-conditional redefinition like click's `getchar`, or
# a wire-format quirk emitting two Definition occurrences on the identical
# position for one real declaration) is correctly left to the existing
# GAP-13/14 path, unaffected.
# ---------------------------------------------------------------------------


def _two_sibling_closures_index(*, third_gap: int = 0) -> bytes:
    """Two real, distinct methods (`method_a`, `method_b`) of one class,
    each with its own nested closure named `helper` -- the exact real
    shape confirmed against django's `AbstractBaseUser.check_password`/
    `.acheck_password` and their own, separate `setter` closures.
    `third_gap`, when nonzero, adds a third sibling method+closure pair
    further down (for "multiple closures" tests)."""
    outer_a = "scip-python python testrepo rev1 `pkg.a`/Outer#method_a()."
    outer_b = "scip-python python testrepo rev1 `pkg.a`/Outer#method_b()."
    closure = "scip-python python testrepo rev1 `pkg.a`/Outer#helper()."
    occs = [
        occurrence(outer_a, roles=1, range_=(0, 4, 12)),
        occurrence(closure, roles=1, range_=(1, 8, 14)),
        occurrence(outer_b, roles=1, range_=(4, 4, 12)),
        occurrence(closure, roles=1, range_=(5, 8, 14)),
    ]
    sym_infos = [
        symbol_information(outer_a, kind=0),
        symbol_information(closure, kind=0),
        symbol_information(outer_b, kind=0),
        symbol_information(closure, kind=0),
    ]
    if third_gap:
        outer_c = "scip-python python testrepo rev1 `pkg.a`/Outer#method_c()."
        line = 5 + third_gap
        occs += [
            occurrence(outer_c, roles=1, range_=(line, 4, 12)),
            occurrence(closure, roles=1, range_=(line + 1, 8, 14)),
        ]
        sym_infos += [
            symbol_information(outer_c, kind=0),
            symbol_information(closure, kind=0),
        ]
    doc = document("pkg/a.py", occurrences=tuple(occs), symbols=tuple(sym_infos))
    return scip_index(documents=(doc,))


def test_fnd1_two_closures_same_name_different_methods_split(tmp_path: Path) -> None:
    """(1): two closures named `helper`, nested in two different sibling
    methods of the same class, must become two distinct canonical
    entities -- not collapse into one, matching django's real
    `AbstractBaseUser.check_password`/`.acheck_password` `setter` case."""
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_two_sibling_closures_index())
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name.endswith("helper().")]
    assert len(matches) == 2
    ids = {e.canonical_id for e in matches}
    assert len(ids) == 2
    lines = sorted(e.source_location.start_line for e in matches if e.source_location)
    assert lines == [1, 5]
    for e in matches:
        assert "scip:nested-scope-disambiguated" in e.roles
        assert "method_a" in e.qualified_name or "method_b" in e.qualified_name


def test_fnd1_multiple_closures_same_name_many_methods_split(tmp_path: Path) -> None:
    """(2): three or more closures with the identical name, nested in
    three or more different sibling methods, must each become their own
    entity -- matches django's real `Library#dec()` (five distinct
    closures across five methods)."""
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_two_sibling_closures_index(third_gap=10))
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name.endswith("helper().")]
    assert len(matches) == 3
    assert len({e.canonical_id for e in matches}) == 3


def test_fnd1_closure_split_coexists_with_unrelated_overload_family(tmp_path: Path) -> None:
    """(3): a genuine `@typing.overload` family (GAP-13/14's own target)
    elsewhere in the *same* document must still converge to one entity,
    completely unaffected by a separate FND-1 closure split happening in
    the same file -- the two mechanisms must not interfere."""
    overload_symbol = "scip-python python testrepo rev1 `pkg.a`/Helper#compute()."
    overload_occs = (
        occurrence(overload_symbol, roles=1, range_=(20, 4, 11)),
        occurrence(overload_symbol, roles=8, range_=(23, 4, 11)),
        occurrence(overload_symbol, roles=8, range_=(26, 4, 11)),
    )
    overload_infos = tuple(symbol_information(overload_symbol, kind=0) for _ in range(3))
    outer_a = "scip-python python testrepo rev1 `pkg.a`/Outer#method_a()."
    outer_b = "scip-python python testrepo rev1 `pkg.a`/Outer#method_b()."
    closure_symbol = "scip-python python testrepo rev1 `pkg.a`/Outer#helper()."
    occs = (
        occurrence(outer_a, roles=1, range_=(0, 4, 12)),
        occurrence(closure_symbol, roles=1, range_=(1, 8, 14)),
        occurrence(outer_b, roles=1, range_=(4, 4, 12)),
        occurrence(closure_symbol, roles=1, range_=(5, 8, 14)),
        *overload_occs,
    )
    sym_infos = (
        symbol_information(outer_a, kind=0),
        symbol_information(closure_symbol, kind=0),
        symbol_information(outer_b, kind=0),
        symbol_information(closure_symbol, kind=0),
        *overload_infos,
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    closure_matches = [e for e in normalized.entities if e.qualified_name.endswith("helper().")]
    assert len(closure_matches) == 2
    assert len({e.canonical_id for e in closure_matches}) == 2

    overload_matches = [
        e for e in normalized.entities if e.qualified_name == "`pkg.a`/Helper#compute()."
    ]
    assert len(overload_matches) == 1
    assert overload_matches[0].source_location is not None
    assert overload_matches[0].source_location.start_line == 26
    assert "scip:redefinition-family" in overload_matches[0].roles


def test_fnd1_same_closure_name_different_modules_remain_distinct(tmp_path: Path) -> None:
    """(4): the same nested-closure collision pattern in two different
    modules must never cross-collapse -- each module's own two closures
    stay within that module's own 2-entity split."""
    docs = []
    for module in ("a", "b"):
        outer_a = f"scip-python python testrepo rev1 `pkg.{module}`/Outer#method_a()."
        outer_b = f"scip-python python testrepo rev1 `pkg.{module}`/Outer#method_b()."
        closure = f"scip-python python testrepo rev1 `pkg.{module}`/Outer#helper()."
        occs = (
            occurrence(outer_a, roles=1, range_=(0, 4, 12)),
            occurrence(closure, roles=1, range_=(1, 8, 14)),
            occurrence(outer_b, roles=1, range_=(4, 4, 12)),
            occurrence(closure, roles=1, range_=(5, 8, 14)),
        )
        sym_infos = (
            symbol_information(outer_a, kind=0),
            symbol_information(closure, kind=0),
            symbol_information(outer_b, kind=0),
            symbol_information(closure, kind=0),
        )
        docs.append(document(f"pkg/{module}.py", occurrences=occs, symbols=sym_infos))
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=tuple(docs)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name.endswith("helper().")]
    assert len(matches) == 4
    assert len({e.canonical_id for e in matches}) == 4


def test_fnd1_local_class_same_name_different_functions_split(tmp_path: Path) -> None:
    """(5)+(6): the identical pattern for a *locally-defined class*
    (not just a closure function) nested in two different functions --
    the same descriptor-grammar gap applies to any nested symbol kind,
    not only functions."""
    outer_a = "scip-python python testrepo rev1 `pkg.a`/make_a()."
    outer_b = "scip-python python testrepo rev1 `pkg.a`/make_b()."
    local_class = "scip-python python testrepo rev1 `pkg.a`/Helper#"
    occs = (
        occurrence(outer_a, roles=1, range_=(0, 0, 8)),
        occurrence(local_class, roles=1, range_=(1, 4, 10)),
        occurrence(outer_b, roles=1, range_=(4, 0, 8)),
        occurrence(local_class, roles=1, range_=(5, 4, 10)),
    )
    sym_infos = (
        symbol_information(outer_a, kind=0),
        symbol_information(local_class, kind=7),
        symbol_information(outer_b, kind=0),
        symbol_information(local_class, kind=7),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name.endswith("Helper#")]
    assert len(matches) == 2
    assert len({e.canonical_id for e in matches}) == 2
    for e in matches:
        assert e.base_type is BaseEntityType.CLASS


def test_fnd1_mixed_with_normal_class_methods(tmp_path: Path) -> None:
    """(7): an ordinary class method with no nesting ambiguity, in the
    same document as an FND-1 collision, is completely unaffected."""
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_two_sibling_closures_index())
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    method_a = next(e for e in normalized.entities if e.qualified_name.endswith("method_a()."))
    method_b = next(e for e in normalized.entities if e.qualified_name.endswith("method_b()."))
    assert method_a.canonical_id != method_b.canonical_id
    assert "scip:nested-scope-disambiguated" not in method_a.roles
    assert "scip:nested-scope-disambiguated" not in method_b.roles


def test_fnd1_ast_scip_convergence_unaffected_when_ast_has_no_counterpart(
    tmp_path: Path,
) -> None:
    """(8): confirmed real-data shape -- `AstCallsAdapter` never
    independently tracks nested closures (its `_DefinitionCollector`
    only reaches module functions and one level of class methods), so
    there is no AST entity to converge with here. The fix's job is
    limited to what it can prove: the two SCIP entities must never
    themselves collapse into each other, which this confirms directly
    at the identity-resolution layer via `resolve_entities`."""
    from codex.resolution.entity_resolver import resolve_entities

    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_two_sibling_closures_index())
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    closure_entities = [e for e in normalized.entities if e.qualified_name.endswith("helper().")]
    assert len(closure_entities) == 2
    resolved = resolve_entities(list(normalized.entities)).entities
    resolved_closures = [e for e in resolved if e.qualified_name.endswith("helper().")]
    assert len({e.canonical_id for e in resolved_closures}) == 2


def test_fnd1_overload_family_still_collapses_correctly(tmp_path: Path) -> None:
    """(9): GAP-13/14's own case -- a real `@typing.overload` family (one
    Definition-role occurrence, N ReadAccess) is not affected by this
    fix at all; it never has more than one real Definition-role
    occurrence, so it never reaches FND-1's own detection path."""
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_overload_family_index())
    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name == "`pkg.a`/Helper#compute()."]
    assert len(matches) == 1
    assert matches[0].source_location is not None
    assert matches[0].source_location.start_line == 10
    assert "scip:redefinition-family" in matches[0].roles
    assert "scip:nested-scope-disambiguated" not in matches[0].roles


def test_fnd1_property_setter_getter_unaffected(tmp_path: Path) -> None:
    """(10): a real `@property`/`@x.setter` pair (one Definition-role
    occurrence -- confirmed real shape) continues to converge to one
    entity via the existing GAP-13/14 path, not this fix's new one."""
    symbol = "scip-python python testrepo rev1 `pkg.a`/Field#choices()."
    occs = (
        occurrence(symbol, roles=1, range_=(4, 4, 11)),
        occurrence(symbol, roles=8, range_=(7, 4, 11)),
    )
    sym_infos = tuple(symbol_information(symbol, kind=0) for _ in range(2))
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name == "`pkg.a`/Field#choices()."]
    assert len(matches) == 1
    assert "scip:redefinition-family" in matches[0].roles


def test_fnd1_canonical_ids_deterministic_across_runs(tmp_path: Path) -> None:
    """(12): repeated normalization of the same index produces
    byte-identical canonical IDs and locations for the split entities."""
    data = _two_sibling_closures_index()
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(data)
    adapter = SCIPAdapter()

    first = adapter.normalize(
        adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    )
    second = adapter.normalize(
        adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    )
    first_closures = sorted(
        (e.canonical_id, e.source_location) for e in first.entities if "helper" in e.qualified_name
    )
    second_closures = sorted(
        (e.canonical_id, e.source_location) for e in second.entities if "helper" in e.qualified_name
    )
    assert first_closures == second_closures
    assert len(first_closures) == 2


def test_fnd1_relationship_to_ambiguous_symbol_skipped_not_fabricated(tmp_path: Path) -> None:
    """(13): a relationship fact naming an ambiguous (nested-closure)
    symbol as its object -- structurally impossible to disambiguate,
    since `SymbolInformation.relationships` carries no location at all
    -- is safely skipped rather than guessed at. Confirmed this never
    occurs in real data (0 of 239 real FND-1 symbols carry any
    relationship), but the fallback must still never crash or
    fabricate."""
    outer_a = "scip-python python testrepo rev1 `pkg.a`/Outer#method_a()."
    outer_b = "scip-python python testrepo rev1 `pkg.a`/Outer#method_b()."
    closure = "scip-python python testrepo rev1 `pkg.a`/Outer#helper()."
    subject_symbol = "scip-python python testrepo rev1 `pkg.a`/Impl#"
    rel = relationship(closure, is_implementation=True)
    subject_def = occurrence(subject_symbol, roles=1, range_=(20, 0, 4))
    subject_info = symbol_information(subject_symbol, kind=7, relationships=(rel,))
    occs = (
        subject_def,
        occurrence(outer_a, roles=1, range_=(0, 4, 12)),
        occurrence(closure, roles=1, range_=(1, 8, 14)),
        occurrence(outer_b, roles=1, range_=(4, 4, 12)),
        occurrence(closure, roles=1, range_=(5, 8, 14)),
    )
    sym_infos = (
        subject_info,
        symbol_information(outer_a, kind=0),
        symbol_information(closure, kind=0),
        symbol_information(outer_b, kind=0),
        symbol_information(closure, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)  # must not raise

    implements = [e for e in normalized.evidence if e.predicate is RelationshipType.IMPLEMENTS]
    assert implements == []  # skipped, never fabricated onto one arbitrary entity
    closure_matches = [e for e in normalized.entities if e.qualified_name.endswith("helper().")]
    assert len(closure_matches) == 2  # the definitions themselves are still correct


def test_fnd1_reference_to_ambiguous_symbol_skipped_not_fabricated(tmp_path: Path) -> None:
    """(13)-adjacent: a plain `REFERENCES`-shaped occurrence of an
    ambiguous symbol from elsewhere in the same file (document-level
    aggregate, no per-occurrence position by the time it reaches
    `_collect_references`) is skipped the same way -- never
    arbitrarily attributed to one of the two real entities."""
    outer_a = "scip-python python testrepo rev1 `pkg.a`/Outer#method_a()."
    outer_b = "scip-python python testrepo rev1 `pkg.a`/Outer#method_b()."
    closure = "scip-python python testrepo rev1 `pkg.a`/Outer#helper()."
    occs = (
        occurrence(outer_a, roles=1, range_=(0, 4, 12)),
        occurrence(closure, roles=1, range_=(1, 8, 14)),
        occurrence(outer_b, roles=1, range_=(4, 4, 12)),
        occurrence(closure, roles=1, range_=(5, 8, 14)),
        occurrence(closure, roles=8, range_=(20, 0, 6)),  # plain later reference
    )
    sym_infos = (
        symbol_information(outer_a, kind=0),
        symbol_information(closure, kind=0),
        symbol_information(outer_b, kind=0),
        symbol_information(closure, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)  # must not raise

    refs = [
        e
        for e in normalized.evidence
        if e.predicate in (RelationshipType.REFERENCES, RelationshipType.IMPORTS)
    ]
    assert refs == []
    closure_matches = [e for e in normalized.entities if e.qualified_name.endswith("helper().")]
    assert len(closure_matches) == 2


def test_fnd1_external_symbol_unaffected(tmp_path: Path) -> None:
    """(14): a genuinely external symbol, referenced many times, is
    completely untouched by FND-1's detection -- it requires a real,
    locally-defined Definition-role occurrence to even be considered,
    which an external symbol never has."""
    local_symbol = "scip-python python testrepo rev1 `pkg.a`/Outer#method_a()."
    external_symbol = "scip-python python otherpkg 2.0.0 `otherpkg.sub`/helper()."
    occs = (
        occurrence(local_symbol, roles=1, range_=(0, 0, 8)),
        occurrence(external_symbol, roles=8, range_=(1, 4, 11)),
        occurrence(external_symbol, roles=8, range_=(2, 4, 11)),
    )
    doc = document(
        "pkg/a.py", occurrences=occs, symbols=(symbol_information(local_symbol, kind=0),)
    )
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    external = [e for e in normalized.entities if e.base_type is BaseEntityType.EXTERNAL_LIBRARY]
    assert len(external) == 1
    assert external[0].qualified_name == "python:otherpkg@2.0.0"


def test_fnd1_same_position_wire_quirk_never_split(tmp_path: Path) -> None:
    """(also FND-1's own safety check): two Definition-role occurrences
    for the same symbol that share the exact same nearest-enclosing
    scope -- confirmed real shape, django's PEP-695 generic-class wire
    quirk (`class GenericModelPEP695[T](...)`, two Definition-role
    occurrences on the identical source line, one real class) -- must
    never be split. Both occurrences here share no enclosing scope
    (module-level), which is the same "no genuine cross-scope
    collision" case."""
    symbol = "scip-python python testrepo rev1 `pkg.a`/GenericThing#"
    occs = (
        occurrence(symbol, roles=1, range_=(10, 6, 18)),
        occurrence(symbol, roles=1, range_=(10, 25, 30)),
    )
    sym_infos = tuple(symbol_information(symbol, kind=7) for _ in range(2))
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name == "`pkg.a`/GenericThing#"]
    assert len(matches) == 1
    assert "scip:nested-scope-disambiguated" not in matches[0].roles


def test_fnd1_gap9_locally_defined_behavior_unchanged() -> None:
    """(GAP-9/10 regression): the local-symbol/relationship-only-object
    machinery this fix does not touch remains byte-identical."""
    from codex.provider.scip.index import decode_index
    from codex.provider.scip_adapter import _collect_definitions

    symbol = "scip-python python testrepo rev1 `pkg.a`/Foo#"
    occ = occurrence(symbol, roles=1, range_=(0, 0, 3))
    doc = document("pkg/a.py", occurrences=(occ,), symbols=(symbol_information(symbol, kind=7),))
    data = scip_index(documents=(doc,))
    definitions = _collect_definitions(decode_index(data))
    assert len(definitions) == 1
    assert definitions[0].nested_qualifier is None
    assert definitions[0].is_redefinition_family is False


def test_fnd1_gap12_module_identity_unchanged(tmp_path: Path) -> None:
    """(GAP-12 regression): a colon-suffixed module-identity symbol
    (never more than one real SymbolInformation entry per document in
    real data) never enters this fix's own detection path."""
    symbol = "scip-python python testrepo rev1 `pkg.a`/__init__:"
    occs = (occurrence(symbol, roles=1, range_=(0, 0, 0)),)
    doc = document("pkg/a.py", occurrences=occs, symbols=(symbol_information(symbol, kind=0),))
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    entity = next(e for e in normalized.entities if e.qualified_name == "`pkg.a`/__init__:")
    assert entity.base_type is BaseEntityType.MODULE
    assert "scip:nested-scope-disambiguated" not in entity.roles


def test_fnd1_true_indentation_not_fooled_by_keyword_length(tmp_path: Path) -> None:
    """Regression lock for the confirmed real-data false-negative found
    during this fix's own exhaustive validation: django's `Signal.asend`
    (an ordinary top-level `async def` method) was first found merging
    with an unrelated `asend` closure nested in `Signal.send` (a plain
    `def` sibling), because a raw SCIP occurrence *column* is not a safe
    proxy for true indentation -- `async def ` is six characters longer
    than `def `, so a genuinely top-level sibling using `async def` gets
    an identifier column that looks "more indented" than a shorter-
    keyword sibling at the *same* real nesting depth, with nothing else
    at a shallower indentation in between.

    This fixture reproduces the exact shape with a real, matching source
    file on disk (`pkg/a.py`) so the fix's true-indentation read path is
    actually exercised, not the raw-column fallback used by every other
    handcrafted (no-real-source-file) test in this module::

        class Outer:
            def method_a(self):        # indent 4, identifier col 8
                def helper():          # indent 8, identifier col 12 (nested closure)
                    pass
                return helper

            async def helper(self):    # indent 4 (same as method_a!), identifier col 14
                pass

    A raw-column comparison sees `method_a` (col 8) < `helper` (col 14)
    and wrongly treats `method_a` as `helper`'s enclosing container. True
    source indentation shows both at indent 4 -- genuine siblings, not
    parent/child -- so the top-level `helper` must resolve to its real
    container (the `Outer` class) and keep a plain, non-`<locals>`
    identity, distinct from the closure genuinely nested in `method_a`.
    """
    source = (
        "class Outer:\n"
        "    def method_a(self):\n"
        "        def helper():\n"
        "            pass\n"
        "        return helper\n"
        "\n"
        "    async def helper(self):\n"
        "        pass\n"
    )
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text(source, encoding="utf-8")

    outer_class = "scip-python python testrepo rev1 `pkg.a`/Outer#"
    method_a = "scip-python python testrepo rev1 `pkg.a`/Outer#method_a()."
    helper = "scip-python python testrepo rev1 `pkg.a`/Outer#helper()."
    occs = (
        occurrence(outer_class, roles=1, range_=(0, 6, 11)),
        occurrence(method_a, roles=1, range_=(1, 8, 16)),
        occurrence(helper, roles=1, range_=(2, 12, 18)),  # nested closure
        occurrence(helper, roles=1, range_=(6, 14, 20)),  # real top-level sibling
    )
    sym_infos = (
        symbol_information(outer_class, kind=7),
        symbol_information(method_a, kind=0),
        symbol_information(helper, kind=0),
        symbol_information(helper, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name.endswith("helper().")]
    assert len(matches) == 2
    assert len({e.canonical_id for e in matches}) == 2

    top_level = next(e for e in matches if e.qualified_name == "`pkg.a`/Outer#helper().")
    nested = next(e for e in matches if e.qualified_name != "`pkg.a`/Outer#helper().")
    assert top_level.source_location is not None and top_level.source_location.start_line == 6
    assert nested.qualified_name == "`pkg.a`/Outer#method_a().<locals>.Outer#helper()."
    assert nested.source_location is not None and nested.source_location.start_line == 2
    for e in matches:
        assert "scip:nested-scope-disambiguated" in e.roles


def test_fnd1_no_real_source_file_falls_back_to_raw_column(tmp_path: Path) -> None:
    """When the source file referenced by the `.scip` index cannot be
    read (moved, deleted, or -- as in every other handcrafted fixture in
    this module -- simply never written to disk), FND-1 detection must
    not raise or silently disable itself: it falls back to the previous,
    less reliable raw-column comparison rather than fabricating an
    indentation value it has no real signal for. Reuses the ordinary
    two-sibling-closures shape, which the raw-column fallback still
    handles correctly (no keyword-length confusion present)."""
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(_two_sibling_closures_index())
    assert not (tmp_path / "pkg").exists()

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name.endswith("helper().")]
    assert len(matches) == 2
    assert len({e.canonical_id for e in matches}) == 2


# ---------------------------------------------------------------------------
# FND-2 fix: recursive scope-identity-based container resolution
# ---------------------------------------------------------------------------
#
# FND-2's root cause: `_nested_symbol_disambiguation` grouped nested-symbol
# occurrences by their immediate container's *descriptor string*, not its
# resolved semantic identity. Two confirmed real-data failure shapes:
#
#   (a) under-split -- the immediate container is itself cross-scope
#       ambiguous (flask `TestStreaming#generate()/gen()`, pytest
#       `TestPaste#mocked().DummyFile#read()`): occurrences nested in two
#       genuinely different real scopes serialize to the identical
#       descriptor string once scip-python collapses the enclosing-function
#       segment, so descriptor-string grouping wrongly merges them.
#
#   (b) inconsistent split -- a container is redefined in-place within one
#       shared real scope (django `Person#first_name` and 25+ similar
#       cases), and only the *first* textual instance carries a
#       Definition-role SCIP occurrence, so descriptor-string-only grouping
#       (which the earlier FND-1 fix did not fully generalize to
#       *containers*, only to the *target* symbols themselves) could see
#       the redefinitions as belonging to different groups depending on
#       which occurrence's raw descriptor happened to be sampled.
#
# The fix replaces string-keyed grouping with a position/identity-based
# "scope forest" (`_build_scope_forest`): every real function/class-typed
# occurrence in a document is resolved to a `_ScopeCandidate`, and
# candidates that represent the *same real scope* (however many times it
# is textually redefined) collapse to one recursively-computed "family"
# index, while candidates that are genuinely different real scopes never
# collapse merely because they share a descriptor string. This is the same
# identity-not-string-matching precedent as CodeQL's `getEnclosingScope()`
# (an edge to a specific parent scope *object*) and RepoGraph's node-
# identity `contains` edges -- see `docs/resources.md`'s "Seventh material
# finding" for the full research record.
#
# Every fixture below writes a real, matching Python source file to
# `tmp_path` (not just synthetic `.scip` occurrences) so the true-
# indentation/true-scope-opening read paths this fix depends on
# (`_read_line_indentations`, `_is_scope_opening_occurrence`) are actually
# exercised, following the established convention of
# `test_fnd1_true_indentation_not_fooled_by_keyword_length`.


def _write_source(tmp_path: Path, source: str) -> None:
    (tmp_path / "pkg").mkdir(exist_ok=True)
    (tmp_path / "pkg" / "a.py").write_text(source, encoding="utf-8")


def test_fnd2_flask_generate_ambiguous_container_splits_correctly(tmp_path: Path) -> None:
    """(1) FND-2 flask `TestStreaming#generate()/gen()` reproduction: two
    genuinely different real scopes (`test_a`'s `index()` closure and
    `test_b`'s `index()` closure) each define their own nested `generate()`
    closure. scip-python drops the enclosing-method segment for both
    `index()` instances and both `generate()` instances, so all four
    descriptors collide pairwise (`TestStreaming#index().` twice,
    `TestStreaming#generate().` twice) -- FND-1's fix (targeting only the
    *target* symbol's own descriptor collisions) still under-split these,
    since the collision here is at the *container* level, one level up.
    Must resolve to 4 distinct entities with 4 distinct canonical IDs and
    qualified names chained through the correct grandparent."""
    source = (
        "class TestStreaming:\n"
        "    def test_a(self):\n"
        "        def index():\n"
        "            def generate():\n"
        "                pass\n"
        "            return generate\n"
        "        return index\n"
        "\n"
        "    def test_b(self):\n"
        "        def index():\n"
        "            def generate():\n"
        "                pass\n"
        "            return generate\n"
        "        return index\n"
    )
    _write_source(tmp_path, source)

    cls = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#"
    test_a = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#test_a()."
    test_b = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#test_b()."
    index_sym = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#index()."
    generate_sym = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#generate()."

    occs = (
        occurrence(cls, roles=1, range_=(0, 6, 19)),
        occurrence(test_a, roles=1, range_=(1, 8, 14)),
        occurrence(index_sym, roles=1, range_=(2, 12, 17)),
        occurrence(generate_sym, roles=1, range_=(3, 16, 24)),
        occurrence(test_b, roles=1, range_=(8, 8, 14)),
        occurrence(index_sym, roles=1, range_=(9, 12, 17)),
        occurrence(generate_sym, roles=1, range_=(10, 16, 24)),
    )
    sym_infos = (
        symbol_information(cls, kind=7),
        symbol_information(test_a, kind=0),
        symbol_information(index_sym, kind=0),
        symbol_information(generate_sym, kind=0),
        symbol_information(test_b, kind=0),
        symbol_information(index_sym, kind=0),
        symbol_information(generate_sym, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    index_matches = [e for e in normalized.entities if e.qualified_name.endswith("index().")]
    generate_matches = [e for e in normalized.entities if e.qualified_name.endswith("generate().")]
    assert len(index_matches) == 2
    assert len(generate_matches) == 2
    assert len({e.canonical_id for e in index_matches}) == 2
    assert len({e.canonical_id for e in generate_matches}) == 2

    index_qualified = {e.qualified_name for e in index_matches}
    assert index_qualified == {
        "`pkg.a`/TestStreaming#test_a().<locals>.TestStreaming#index().",
        "`pkg.a`/TestStreaming#test_b().<locals>.TestStreaming#index().",
    }
    generate_qualified = {e.qualified_name for e in generate_matches}
    assert generate_qualified == {
        "`pkg.a`/TestStreaming#test_a().<locals>."
        "TestStreaming#index().<locals>.TestStreaming#generate().",
        "`pkg.a`/TestStreaming#test_b().<locals>."
        "TestStreaming#index().<locals>.TestStreaming#generate().",
    }
    for e in index_matches + generate_matches:
        assert "scip:nested-scope-disambiguated" in e.roles


def test_fnd2_pytest_mocked_ambiguous_container_splits_correctly(tmp_path: Path) -> None:
    """(2) FND-2 pytest `TestPaste#mocked().DummyFile#read()` reproduction:
    a local class `DummyFile` defined inside two different `mocked()`
    closures, each with its own `read()` method -- the local class itself
    is the ambiguous container this time (not a nested function), and its
    `read()` method must still split correctly through it."""
    source = (
        "class TestPaste:\n"
        "    def test_a(self):\n"
        "        def mocked():\n"
        "            class DummyFile:\n"
        "                def read(self):\n"
        "                    pass\n"
        "            return DummyFile\n"
        "        return mocked\n"
        "\n"
        "    def test_b(self):\n"
        "        def mocked():\n"
        "            class DummyFile:\n"
        "                def read(self):\n"
        "                    pass\n"
        "            return DummyFile\n"
        "        return mocked\n"
    )
    _write_source(tmp_path, source)

    cls = "scip-python python testrepo rev1 `pkg.a`/TestPaste#"
    test_a = "scip-python python testrepo rev1 `pkg.a`/TestPaste#test_a()."
    test_b = "scip-python python testrepo rev1 `pkg.a`/TestPaste#test_b()."
    mocked_sym = "scip-python python testrepo rev1 `pkg.a`/TestPaste#mocked()."
    dummy_sym = "scip-python python testrepo rev1 `pkg.a`/DummyFile#"
    read_sym = "scip-python python testrepo rev1 `pkg.a`/DummyFile#read()."

    occs = (
        occurrence(cls, roles=1, range_=(0, 6, 15)),
        occurrence(test_a, roles=1, range_=(1, 8, 14)),
        occurrence(mocked_sym, roles=1, range_=(2, 12, 18)),
        occurrence(dummy_sym, roles=1, range_=(3, 18, 27)),
        occurrence(read_sym, roles=1, range_=(4, 20, 24)),
        occurrence(test_b, roles=1, range_=(9, 8, 14)),
        occurrence(mocked_sym, roles=1, range_=(10, 12, 18)),
        occurrence(dummy_sym, roles=1, range_=(11, 18, 27)),
        occurrence(read_sym, roles=1, range_=(12, 20, 24)),
    )
    sym_infos = (
        symbol_information(cls, kind=7),
        symbol_information(test_a, kind=0),
        symbol_information(mocked_sym, kind=0),
        symbol_information(dummy_sym, kind=7),
        symbol_information(read_sym, kind=0),
        symbol_information(test_b, kind=0),
        symbol_information(mocked_sym, kind=0),
        symbol_information(dummy_sym, kind=7),
        symbol_information(read_sym, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    read_matches = [e for e in normalized.entities if e.qualified_name.endswith("read().")]
    assert len(read_matches) == 2
    assert len({e.canonical_id for e in read_matches}) == 2
    read_qualified = {e.qualified_name for e in read_matches}
    assert read_qualified == {
        "`pkg.a`/TestPaste#test_a().<locals>."
        "TestPaste#mocked().<locals>.DummyFile#read().",
        "`pkg.a`/TestPaste#test_b().<locals>."
        "TestPaste#mocked().<locals>.DummyFile#read().",
    }


def test_fnd2_django_person_first_name_redefined_container_converges(tmp_path: Path) -> None:
    """(3) FND-2 django `Person#first_name` reproduction: `Person` is
    redefined in-place inside one shared real scope (`outer()`), and --
    matching real observed scip-python behavior -- only the *first*
    textual instance carries a Definition-role occurrence for the class
    itself, while `first_name` gets a fresh Definition-role occurrence at
    *every* textual instance. Both `Person` instances must resolve to the
    SAME real scope (one family), so `first_name`'s two occurrences must
    converge to ONE entity, not split -- this is the "inconsistent split"
    FND-2 shape, not the "under-split" shape."""
    source = (
        "def outer():\n"
        "    class Person:\n"
        "        first_name = 1\n"
        "\n"
        "    class Person:\n"
        "        first_name = 2\n"
        "    return Person\n"
    )
    _write_source(tmp_path, source)

    outer_sym = "scip-python python testrepo rev1 `pkg.a`/outer()."
    person_sym = "scip-python python testrepo rev1 `pkg.a`/Person#"
    first_name_sym = "scip-python python testrepo rev1 `pkg.a`/Person#first_name."

    occs = (
        occurrence(outer_sym, roles=1, range_=(0, 4, 9)),
        occurrence(person_sym, roles=1, range_=(1, 10, 16)),
        occurrence(first_name_sym, roles=1, range_=(2, 8, 18)),
        occurrence(person_sym, roles=8, range_=(4, 10, 16)),
        occurrence(first_name_sym, roles=1, range_=(5, 8, 18)),
    )
    sym_infos = (
        symbol_information(outer_sym, kind=0),
        symbol_information(person_sym, kind=7),
        symbol_information(first_name_sym, kind=0),
        symbol_information(first_name_sym, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name.endswith("first_name.")]
    assert len(matches) == 1
    assert matches[0].qualified_name == "`pkg.a`/Person#first_name."


def test_fnd2_all_known_variants_coexist_in_one_document(tmp_path: Path) -> None:
    """(4) All currently known FND-2 variants -- ambiguous-container
    under-split (flask-style) and redefined-container inconsistent-split
    (django-style) -- must be handled correctly *simultaneously* within a
    single document, without one variant's handling interfering with the
    other's."""
    source = (
        "class TestStreaming:\n"
        "    def test_a(self):\n"
        "        def index():\n"
        "            pass\n"
        "        return index\n"
        "\n"
        "    def test_b(self):\n"
        "        def index():\n"
        "            pass\n"
        "        return index\n"
        "\n"
        "\n"
        "def outer():\n"
        "    class Person:\n"
        "        first_name = 1\n"
        "\n"
        "    class Person:\n"
        "        first_name = 2\n"
        "    return Person\n"
    )
    _write_source(tmp_path, source)

    cls = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#"
    test_a = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#test_a()."
    test_b = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#test_b()."
    index_sym = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#index()."
    outer_sym = "scip-python python testrepo rev1 `pkg.a`/outer()."
    person_sym = "scip-python python testrepo rev1 `pkg.a`/Person#"
    first_name_sym = "scip-python python testrepo rev1 `pkg.a`/Person#first_name."

    occs = (
        occurrence(cls, roles=1, range_=(0, 6, 19)),
        occurrence(test_a, roles=1, range_=(1, 8, 14)),
        occurrence(index_sym, roles=1, range_=(2, 12, 17)),
        occurrence(test_b, roles=1, range_=(6, 8, 14)),
        occurrence(index_sym, roles=1, range_=(7, 12, 17)),
        occurrence(outer_sym, roles=1, range_=(12, 4, 9)),
        occurrence(person_sym, roles=1, range_=(13, 10, 16)),
        occurrence(first_name_sym, roles=1, range_=(14, 8, 18)),
        occurrence(person_sym, roles=8, range_=(16, 10, 16)),
        occurrence(first_name_sym, roles=1, range_=(17, 8, 18)),
    )
    sym_infos = (
        symbol_information(cls, kind=7),
        symbol_information(test_a, kind=0),
        symbol_information(index_sym, kind=0),
        symbol_information(test_b, kind=0),
        symbol_information(index_sym, kind=0),
        symbol_information(outer_sym, kind=0),
        symbol_information(person_sym, kind=7),
        symbol_information(first_name_sym, kind=0),
        symbol_information(first_name_sym, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    index_matches = [e for e in normalized.entities if e.qualified_name.endswith("index().")]
    first_name_matches = [
        e for e in normalized.entities if e.qualified_name.endswith("first_name.")
    ]
    assert len(index_matches) == 2
    assert len({e.canonical_id for e in index_matches}) == 2
    assert len(first_name_matches) == 1


def test_fnd2_nested_functions_in_different_parent_methods(tmp_path: Path) -> None:
    """(5) Nested functions of the same name inside different parent
    methods (one level of nesting, no intermediate ambiguous container)
    must resolve as distinct entities -- baseline case FND-1 already
    covered, kept here as an FND-2-fix regression lock since the
    container-resolution machinery was fully rewritten."""
    source = (
        "class Widget:\n"
        "    def method_a(self):\n"
        "        def helper():\n"
        "            pass\n"
        "        return helper\n"
        "\n"
        "    def method_b(self):\n"
        "        def helper():\n"
        "            pass\n"
        "        return helper\n"
    )
    _write_source(tmp_path, source)

    cls = "scip-python python testrepo rev1 `pkg.a`/Widget#"
    method_a = "scip-python python testrepo rev1 `pkg.a`/Widget#method_a()."
    method_b = "scip-python python testrepo rev1 `pkg.a`/Widget#method_b()."
    helper = "scip-python python testrepo rev1 `pkg.a`/Widget#helper()."

    occs = (
        occurrence(cls, roles=1, range_=(0, 6, 12)),
        occurrence(method_a, roles=1, range_=(1, 8, 16)),
        occurrence(helper, roles=1, range_=(2, 12, 18)),
        occurrence(method_b, roles=1, range_=(6, 8, 16)),
        occurrence(helper, roles=1, range_=(7, 12, 18)),
    )
    sym_infos = (
        symbol_information(cls, kind=7),
        symbol_information(method_a, kind=0),
        symbol_information(helper, kind=0),
        symbol_information(method_b, kind=0),
        symbol_information(helper, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name.endswith("helper().")]
    assert len(matches) == 2
    assert len({e.canonical_id for e in matches}) == 2
    assert {e.qualified_name for e in matches} == {
        "`pkg.a`/Widget#method_a().<locals>.Widget#helper().",
        "`pkg.a`/Widget#method_b().<locals>.Widget#helper().",
    }


def test_fnd2_local_classes_in_different_functions(tmp_path: Path) -> None:
    """(6) Same-named local classes defined inside different top-level
    functions must resolve as distinct entities, with their own methods
    correctly chained through the resolved (not string-matched) local
    class."""
    source = (
        "def make_a():\n"
        "    class Box:\n"
        "        def value(self):\n"
        "            pass\n"
        "    return Box\n"
        "\n"
        "\n"
        "def make_b():\n"
        "    class Box:\n"
        "        def value(self):\n"
        "            pass\n"
        "    return Box\n"
    )
    _write_source(tmp_path, source)

    make_a = "scip-python python testrepo rev1 `pkg.a`/make_a()."
    make_b = "scip-python python testrepo rev1 `pkg.a`/make_b()."
    box = "scip-python python testrepo rev1 `pkg.a`/Box#"
    value = "scip-python python testrepo rev1 `pkg.a`/Box#value()."

    occs = (
        occurrence(make_a, roles=1, range_=(0, 4, 10)),
        occurrence(box, roles=1, range_=(1, 10, 13)),
        occurrence(value, roles=1, range_=(2, 12, 17)),
        occurrence(make_b, roles=1, range_=(7, 4, 10)),
        occurrence(box, roles=1, range_=(8, 10, 13)),
        occurrence(value, roles=1, range_=(9, 12, 17)),
    )
    sym_infos = (
        symbol_information(make_a, kind=0),
        symbol_information(box, kind=7),
        symbol_information(value, kind=0),
        symbol_information(make_b, kind=0),
        symbol_information(box, kind=7),
        symbol_information(value, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    box_matches = [e for e in normalized.entities if e.qualified_name.endswith("Box#")]
    value_matches = [e for e in normalized.entities if e.qualified_name.endswith("value().")]
    assert len(box_matches) == 2
    assert len({e.canonical_id for e in box_matches}) == 2
    assert len(value_matches) == 2
    assert len({e.canonical_id for e in value_matches}) == 2
    assert {e.qualified_name for e in value_matches} == {
        "`pkg.a`/make_a().<locals>.Box#value().",
        "`pkg.a`/make_b().<locals>.Box#value().",
    }


def test_fnd2_three_levels_of_nesting(tmp_path: Path) -> None:
    """(7) Multiple nested levels (3+ deep): a target symbol nested inside
    an ambiguous grandparent AND an ambiguous great-grandparent must still
    chain correctly through every resolved level, not just the immediate
    parent."""
    source = (
        "class Outer:\n"
        "    def make_a(self):\n"
        "        def level_b():\n"
        "            def level_c():\n"
        "                def target():\n"
        "                    pass\n"
        "                return target\n"
        "            return level_c\n"
        "        return level_b\n"
        "\n"
        "    def make_b(self):\n"
        "        def level_b():\n"
        "            def level_c():\n"
        "                def target():\n"
        "                    pass\n"
        "                return target\n"
        "            return level_c\n"
        "        return level_b\n"
    )
    _write_source(tmp_path, source)

    cls = "scip-python python testrepo rev1 `pkg.a`/Outer#"
    make_a = "scip-python python testrepo rev1 `pkg.a`/Outer#make_a()."
    make_b = "scip-python python testrepo rev1 `pkg.a`/Outer#make_b()."
    level_b = "scip-python python testrepo rev1 `pkg.a`/Outer#level_b()."
    level_c = "scip-python python testrepo rev1 `pkg.a`/Outer#level_c()."
    target = "scip-python python testrepo rev1 `pkg.a`/Outer#target()."

    occs = (
        occurrence(cls, roles=1, range_=(0, 6, 11)),
        occurrence(make_a, roles=1, range_=(1, 8, 14)),
        occurrence(level_b, roles=1, range_=(2, 12, 19)),
        occurrence(level_c, roles=1, range_=(3, 16, 23)),
        occurrence(target, roles=1, range_=(4, 20, 26)),
        occurrence(make_b, roles=1, range_=(10, 8, 14)),
        occurrence(level_b, roles=1, range_=(11, 12, 19)),
        occurrence(level_c, roles=1, range_=(12, 16, 23)),
        occurrence(target, roles=1, range_=(13, 20, 26)),
    )
    sym_infos = (
        symbol_information(cls, kind=7),
        symbol_information(make_a, kind=0),
        symbol_information(level_b, kind=0),
        symbol_information(level_c, kind=0),
        symbol_information(target, kind=0),
        symbol_information(make_b, kind=0),
        symbol_information(level_b, kind=0),
        symbol_information(level_c, kind=0),
        symbol_information(target, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    target_matches = [e for e in normalized.entities if e.qualified_name.endswith("target().")]
    assert len(target_matches) == 2
    assert len({e.canonical_id for e in target_matches}) == 2
    assert {e.qualified_name for e in target_matches} == {
        "`pkg.a`/Outer#make_a().<locals>.Outer#level_b().<locals>."
        "Outer#level_c().<locals>.Outer#target().",
        "`pkg.a`/Outer#make_b().<locals>.Outer#level_b().<locals>."
        "Outer#level_c().<locals>.Outer#target().",
    }


def test_fnd2_same_nested_name_three_distinct_scopes(tmp_path: Path) -> None:
    """(8) The same nested name appearing in three (not merely two)
    distinct scopes must resolve to three distinct entities -- rules out
    an implementation that only ever considers a binary "same or
    different" comparison instead of true per-family identity."""
    source = (
        "def make_a():\n"
        "    def helper():\n"
        "        pass\n"
        "    return helper\n"
        "\n"
        "\n"
        "def make_b():\n"
        "    def helper():\n"
        "        pass\n"
        "    return helper\n"
        "\n"
        "\n"
        "def make_c():\n"
        "    def helper():\n"
        "        pass\n"
        "    return helper\n"
    )
    _write_source(tmp_path, source)

    make_a = "scip-python python testrepo rev1 `pkg.a`/make_a()."
    make_b = "scip-python python testrepo rev1 `pkg.a`/make_b()."
    make_c = "scip-python python testrepo rev1 `pkg.a`/make_c()."
    helper = "scip-python python testrepo rev1 `pkg.a`/helper()."

    occs = (
        occurrence(make_a, roles=1, range_=(0, 4, 10)),
        occurrence(helper, roles=1, range_=(1, 8, 14)),
        occurrence(make_b, roles=1, range_=(6, 4, 10)),
        occurrence(helper, roles=1, range_=(7, 8, 14)),
        occurrence(make_c, roles=1, range_=(12, 4, 10)),
        occurrence(helper, roles=1, range_=(13, 8, 14)),
    )
    sym_infos = (
        symbol_information(make_a, kind=0),
        symbol_information(helper, kind=0),
        symbol_information(make_b, kind=0),
        symbol_information(helper, kind=0),
        symbol_information(make_c, kind=0),
        symbol_information(helper, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name.endswith("helper().")]
    assert len(matches) == 3
    assert len({e.canonical_id for e in matches}) == 3
    assert {e.qualified_name for e in matches} == {
        "`pkg.a`/make_a().<locals>.helper().",
        "`pkg.a`/make_b().<locals>.helper().",
        "`pkg.a`/make_c().<locals>.helper().",
    }


def test_fnd2_ambiguous_immediate_container_is_resolved_not_string_matched(tmp_path: Path) -> None:
    """(9) The defining FND-2 shape in isolation: the *immediate* container
    of the target symbol is itself ambiguous (two real scopes share its
    descriptor). This is exactly `test_fnd2_flask_generate_...` reduced to
    its minimal two-entity shape, kept as a separate targeted case per the
    directive's explicit category list."""
    source = (
        "class Outer:\n"
        "    def make_a(self):\n"
        "        def container():\n"
        "            def target():\n"
        "                pass\n"
        "            return target\n"
        "        return container\n"
        "\n"
        "    def make_b(self):\n"
        "        def container():\n"
        "            def target():\n"
        "                pass\n"
        "            return target\n"
        "        return container\n"
    )
    _write_source(tmp_path, source)

    cls = "scip-python python testrepo rev1 `pkg.a`/Outer#"
    make_a = "scip-python python testrepo rev1 `pkg.a`/Outer#make_a()."
    make_b = "scip-python python testrepo rev1 `pkg.a`/Outer#make_b()."
    container = "scip-python python testrepo rev1 `pkg.a`/Outer#container()."
    target = "scip-python python testrepo rev1 `pkg.a`/Outer#target()."

    occs = (
        occurrence(cls, roles=1, range_=(0, 6, 11)),
        occurrence(make_a, roles=1, range_=(1, 8, 14)),
        occurrence(container, roles=1, range_=(2, 12, 21)),
        occurrence(target, roles=1, range_=(3, 16, 22)),
        occurrence(make_b, roles=1, range_=(8, 8, 14)),
        occurrence(container, roles=1, range_=(9, 12, 21)),
        occurrence(target, roles=1, range_=(10, 16, 22)),
    )
    sym_infos = (
        symbol_information(cls, kind=7),
        symbol_information(make_a, kind=0),
        symbol_information(container, kind=0),
        symbol_information(target, kind=0),
        symbol_information(make_b, kind=0),
        symbol_information(container, kind=0),
        symbol_information(target, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    target_matches = [e for e in normalized.entities if e.qualified_name.endswith("target().")]
    assert len(target_matches) == 2
    assert len({e.canonical_id for e in target_matches}) == 2


def test_fnd2_redefined_container_missing_later_definition_occurrence(tmp_path: Path) -> None:
    """(10) A container class redefined in-place where the *second*
    instance carries only a ReadAccess-role (not Definition-role)
    occurrence -- matching real observed scip-python output -- must still
    be recognized as a genuine scope-opening candidate (via
    `_is_scope_opening_occurrence`'s verified-ReadAccess path) so that
    children nested under the second instance still resolve to the SAME
    family as children under the first, converging correctly. This is
    `test_fnd2_django_person_first_name_...` with the assertion focused
    specifically on the second (ReadAccess) instance's own children."""
    source = (
        "def outer():\n"
        "    class Config:\n"
        "        debug = 1\n"
        "\n"
        "    class Config:\n"
        "        debug = 2\n"
        "    return Config\n"
    )
    _write_source(tmp_path, source)

    outer_sym = "scip-python python testrepo rev1 `pkg.a`/outer()."
    config_sym = "scip-python python testrepo rev1 `pkg.a`/Config#"
    debug_sym = "scip-python python testrepo rev1 `pkg.a`/Config#debug."

    occs = (
        occurrence(outer_sym, roles=1, range_=(0, 4, 9)),
        occurrence(config_sym, roles=1, range_=(1, 10, 16)),
        occurrence(debug_sym, roles=1, range_=(2, 8, 13)),
        occurrence(config_sym, roles=8, range_=(4, 10, 16)),
        occurrence(debug_sym, roles=1, range_=(5, 8, 13)),
    )
    sym_infos = (
        symbol_information(outer_sym, kind=0),
        symbol_information(config_sym, kind=7),
        symbol_information(debug_sym, kind=0),
        symbol_information(debug_sym, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name.endswith("debug.")]
    assert len(matches) == 1
    # Matches the existing GAP-13/14 convention (`_redefinition_family_locations`):
    # the *last* member of a same-scope textual-redefinition cluster is the
    # representative location, not the first -- unchanged by this fix.
    assert matches[0].source_location is not None
    assert matches[0].source_location.start_line == 5


def test_fnd2_ast_scip_convergence_matches_independent_oracle(tmp_path: Path) -> None:
    """(11) AST<->SCIP convergence: for the flask-style ambiguous-container
    fixture, independently compute true lexical nesting straight from the
    real source file via Python's own `ast` module (never calling
    `_nested_symbol_disambiguation` or any of its helpers) and confirm the
    two `generate` functions' true parent-chain identities (by source
    position, not by name) disagree with each other -- i.e. the source
    genuinely contains two distinct scopes -- matching Codex's own
    resolution into two distinct canonical entities. This is the same
    independence discipline as the audit's own oracle: an AST-only ground
    truth, checked against Codex's output, not against Codex's own logic."""
    import ast

    source = (
        "class TestStreaming:\n"
        "    def test_a(self):\n"
        "        def index():\n"
        "            def generate():\n"
        "                pass\n"
        "            return generate\n"
        "        return index\n"
        "\n"
        "    def test_b(self):\n"
        "        def index():\n"
        "            def generate():\n"
        "                pass\n"
        "            return generate\n"
        "        return index\n"
    )
    _write_source(tmp_path, source)

    tree = ast.parse(source)
    generate_defs = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "generate"
    ]
    assert len(generate_defs) == 2

    def ancestry(target: ast.AST) -> tuple[str, ...]:
        chain: list[str] = []
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                if child is target and isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    chain.append(node.name)
                    chain.extend(ancestry(node))
        return tuple(chain)

    ancestries = {ancestry(node) for node in generate_defs}
    assert len(ancestries) == 2  # independent oracle: two genuinely distinct scopes

    cls = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#"
    test_a = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#test_a()."
    test_b = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#test_b()."
    index_sym = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#index()."
    generate_sym = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#generate()."

    occs = (
        occurrence(cls, roles=1, range_=(0, 6, 19)),
        occurrence(test_a, roles=1, range_=(1, 8, 14)),
        occurrence(index_sym, roles=1, range_=(2, 12, 17)),
        occurrence(generate_sym, roles=1, range_=(3, 16, 24)),
        occurrence(test_b, roles=1, range_=(8, 8, 14)),
        occurrence(index_sym, roles=1, range_=(9, 12, 17)),
        occurrence(generate_sym, roles=1, range_=(10, 16, 24)),
    )
    sym_infos = (
        symbol_information(cls, kind=7),
        symbol_information(test_a, kind=0),
        symbol_information(index_sym, kind=0),
        symbol_information(generate_sym, kind=0),
        symbol_information(test_b, kind=0),
        symbol_information(index_sym, kind=0),
        symbol_information(generate_sym, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    generate_matches = [e for e in normalized.entities if e.qualified_name.endswith("generate().")]
    assert len({e.canonical_id for e in generate_matches}) == len(ancestries)


def test_fnd2_gap13_gap14_legitimate_redefinitions_still_converge(tmp_path: Path) -> None:
    """(12) Regression: legitimate GAP-13/14 same-scope redefinitions
    (`@typing.overload` chains, `@property`/`.setter` pairs) at module or
    class level -- not inside any ambiguous container -- must still
    converge to one entity after the full container-resolution rewrite."""
    source = (
        "class Widget:\n"
        "    @property\n"
        "    def value(self):\n"
        "        return self._value\n"
        "\n"
        "    @value.setter\n"
        "    def value(self, val):\n"
        "        self._value = val\n"
    )
    _write_source(tmp_path, source)

    cls = "scip-python python testrepo rev1 `pkg.a`/Widget#"
    value = "scip-python python testrepo rev1 `pkg.a`/Widget#value()."

    occs = (
        occurrence(cls, roles=1, range_=(0, 6, 12)),
        occurrence(value, roles=1, range_=(2, 8, 13)),
        occurrence(value, roles=1, range_=(6, 8, 13)),
    )
    sym_infos = (
        symbol_information(cls, kind=7),
        symbol_information(value, kind=0),
        symbol_information(value, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name.endswith("value().")]
    assert len(matches) == 1
    assert "scip:redefinition-family" in matches[0].roles


def test_fnd2_fnd1_behavior_unchanged_true_indentation(tmp_path: Path) -> None:
    """(13) Regression: FND-1's true-indentation-not-fooled-by-keyword-
    length behavior (`async def` vs `def` column-length skew) must be
    unchanged by the FND-2 container-resolution rewrite. Reuses the exact
    shape of `test_fnd1_true_indentation_not_fooled_by_keyword_length`."""
    source = (
        "class Outer:\n"
        "    def method_a(self):\n"
        "        def helper():\n"
        "            pass\n"
        "        return helper\n"
        "\n"
        "    async def helper(self):\n"
        "        pass\n"
    )
    _write_source(tmp_path, source)

    outer_class = "scip-python python testrepo rev1 `pkg.a`/Outer#"
    method_a = "scip-python python testrepo rev1 `pkg.a`/Outer#method_a()."
    helper = "scip-python python testrepo rev1 `pkg.a`/Outer#helper()."
    occs = (
        occurrence(outer_class, roles=1, range_=(0, 6, 11)),
        occurrence(method_a, roles=1, range_=(1, 8, 16)),
        occurrence(helper, roles=1, range_=(2, 12, 18)),
        occurrence(helper, roles=1, range_=(6, 14, 20)),
    )
    sym_infos = (
        symbol_information(outer_class, kind=7),
        symbol_information(method_a, kind=0),
        symbol_information(helper, kind=0),
        symbol_information(helper, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name.endswith("helper().")]
    assert len(matches) == 2
    assert len({e.canonical_id for e in matches}) == 2
    top_level = next(e for e in matches if e.qualified_name == "`pkg.a`/Outer#helper().")
    nested = next(e for e in matches if e.qualified_name != "`pkg.a`/Outer#helper().")
    assert top_level.source_location is not None and top_level.source_location.start_line == 6
    assert nested.qualified_name == "`pkg.a`/Outer#method_a().<locals>.Outer#helper()."


def test_fnd2_self_referential_closure_same_name_as_parent(tmp_path: Path) -> None:
    """FND-2, third confirmed shape found mid-cycle during exhaustive
    re-sweeping (pytest's `TestExceptionInfoFormatter.importasmod`): a
    method defines a nested closure of its *own exact name* directly
    inside itself. Both share the identical SCIP descriptor
    (`Foo#bar().`), so a naive `.startswith()` "already embedded" check
    (without a strict `!=` guard) would collapse both to the same
    qualified name even after they are correctly split into separate
    families. Must remain two distinct entities with distinct,
    non-colliding qualified names."""
    source = (
        "class Foo:\n"
        "    def bar(self):\n"
        "        def bar():\n"
        "            pass\n"
        "        return bar\n"
    )
    _write_source(tmp_path, source)

    cls = "scip-python python testrepo rev1 `pkg.a`/Foo#"
    bar = "scip-python python testrepo rev1 `pkg.a`/Foo#bar()."

    occs = (
        occurrence(cls, roles=1, range_=(0, 6, 9)),
        occurrence(bar, roles=1, range_=(1, 8, 11)),
        occurrence(bar, roles=1, range_=(2, 12, 15)),
    )
    sym_infos = (
        symbol_information(cls, kind=7),
        symbol_information(bar, kind=0),
        symbol_information(bar, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name.endswith("bar().")]
    assert len(matches) == 2
    assert len({e.canonical_id for e in matches}) == 2
    assert len({e.qualified_name for e in matches}) == 2
    method = next(
        e for e in matches if e.source_location is not None and e.source_location.start_line == 1
    )
    closure = next(
        e for e in matches if e.source_location is not None and e.source_location.start_line == 2
    )
    assert method.qualified_name == "`pkg.a`/Foo#bar()."
    assert closure.qualified_name == "`pkg.a`/Foo#bar().<locals>.Foo#bar()."


def test_fnd2_deterministic_canonical_ids_across_repeated_ingestion(tmp_path: Path) -> None:
    """(14) Deterministic canonical IDs: re-ingesting the identical
    ambiguous-container fixture twice must produce identical canonical
    IDs for corresponding entities each time -- no dependence on
    iteration/dict/set ordering anywhere in the new scope-forest or
    family-resolution machinery."""
    source = (
        "class TestStreaming:\n"
        "    def test_a(self):\n"
        "        def index():\n"
        "            pass\n"
        "        return index\n"
        "\n"
        "    def test_b(self):\n"
        "        def index():\n"
        "            pass\n"
        "        return index\n"
    )
    _write_source(tmp_path, source)

    cls = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#"
    test_a = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#test_a()."
    test_b = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#test_b()."
    index_sym = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#index()."

    occs = (
        occurrence(cls, roles=1, range_=(0, 6, 19)),
        occurrence(test_a, roles=1, range_=(1, 8, 14)),
        occurrence(index_sym, roles=1, range_=(2, 12, 17)),
        occurrence(test_b, roles=1, range_=(6, 8, 14)),
        occurrence(index_sym, roles=1, range_=(7, 12, 17)),
    )
    sym_infos = (
        symbol_information(cls, kind=7),
        symbol_information(test_a, kind=0),
        symbol_information(index_sym, kind=0),
        symbol_information(test_b, kind=0),
        symbol_information(index_sym, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    data = scip_index(documents=(doc,))
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(data)

    adapter = SCIPAdapter()
    caps = adapter.supported_capabilities
    first = adapter.normalize(adapter.extract(make_repository(tmp_path), caps))
    second = adapter.normalize(adapter.extract(make_repository(tmp_path), caps))

    first_map = {
        e.qualified_name: e.canonical_id
        for e in first.entities
        if e.qualified_name.endswith("index().")
    }
    second_map = {
        e.qualified_name: e.canonical_id
        for e in second.entities
        if e.qualified_name.endswith("index().")
    }
    assert len(first_map) == 2
    assert first_map == second_map


def test_fnd2_evidence_and_relationships_preserved_for_split_entities(tmp_path: Path) -> None:
    """(15) Evidence and relationships must still be produced and correctly
    attributed to the right (split) entity after the container-resolution
    rewrite. `index()` itself is ambiguous (two split entities) so a
    reference *to* it is deliberately skipped by the existing shared
    `resolve()` closure (which never resolves references/relationships for
    any symbol in `ambiguous_symbols`, since a reference alone cannot say
    which split instance it means -- unchanged, pre-existing behavior).
    What must survive this fix is a reference *from inside* one of the
    split scopes to an unambiguous target (the enclosing class itself)."""
    source = (
        "class TestStreaming:\n"
        "    def test_a(self):\n"
        "        def index():\n"
        "            return TestStreaming\n"
        "        return index\n"
        "\n"
        "    def test_b(self):\n"
        "        def index():\n"
        "            return TestStreaming\n"
        "        return index\n"
    )
    _write_source(tmp_path, source)

    cls = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#"
    test_a = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#test_a()."
    test_b = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#test_b()."
    index_sym = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#index()."

    occs = (
        occurrence(cls, roles=1, range_=(0, 6, 19)),
        occurrence(test_a, roles=1, range_=(1, 8, 14)),
        occurrence(index_sym, roles=1, range_=(2, 12, 17)),
        occurrence(cls, roles=8, range_=(3, 19, 32)),
        occurrence(test_b, roles=1, range_=(6, 8, 14)),
        occurrence(index_sym, roles=1, range_=(7, 12, 17)),
        occurrence(cls, roles=8, range_=(8, 19, 32)),
    )
    sym_infos = (
        symbol_information(cls, kind=7),
        symbol_information(test_a, kind=0),
        symbol_information(index_sym, kind=0),
        symbol_information(test_b, kind=0),
        symbol_information(index_sym, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    index_matches = [e for e in normalized.entities if e.qualified_name.endswith("index().")]
    assert len(index_matches) == 2
    assert len(normalized.evidence) > 0
    ref_predicates = {ev.predicate for ev in normalized.evidence}
    assert RelationshipType.REFERENCES in ref_predicates


def test_fnd2_external_unresolved_symbols_unaffected(tmp_path: Path) -> None:
    """(16) External/unresolved symbols (no local Definition occurrence,
    outside the indexed project) must remain unaffected by the new scope-
    forest machinery -- they never enter `_build_scope_forest`'s candidate
    collection (which only considers occurrences from indexed documents),
    and referencing one from inside an otherwise-ambiguous nested scope
    must not perturb that scope's own disambiguation."""
    source = (
        "import external_pkg\n"
        "\n"
        "\n"
        "class TestStreaming:\n"
        "    def test_a(self):\n"
        "        def index():\n"
        "            external_pkg.call()\n"
        "        return index\n"
        "\n"
        "    def test_b(self):\n"
        "        def index():\n"
        "            external_pkg.call()\n"
        "        return index\n"
    )
    _write_source(tmp_path, source)

    cls = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#"
    test_a = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#test_a()."
    test_b = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#test_b()."
    index_sym = "scip-python python testrepo rev1 `pkg.a`/TestStreaming#index()."
    external_sym = "scip-python python external_pkg 1.0.0 `external_pkg`/call()."

    occs = (
        occurrence(cls, roles=1, range_=(3, 6, 19)),
        occurrence(test_a, roles=1, range_=(4, 8, 14)),
        occurrence(index_sym, roles=1, range_=(5, 12, 17)),
        occurrence(external_sym, roles=2, range_=(6, 12, 30)),
        occurrence(test_b, roles=1, range_=(9, 8, 14)),
        occurrence(index_sym, roles=1, range_=(10, 12, 17)),
        occurrence(external_sym, roles=2, range_=(11, 12, 30)),
    )
    sym_infos = (
        symbol_information(cls, kind=7),
        symbol_information(test_a, kind=0),
        symbol_information(index_sym, kind=0),
        symbol_information(test_b, kind=0),
        symbol_information(index_sym, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    index_matches = [e for e in normalized.entities if e.qualified_name.endswith("index().")]
    assert len(index_matches) == 2
    assert len({e.canonical_id for e in index_matches}) == 2
    assert not any(e.qualified_name.endswith("call().") for e in normalized.entities)


# ---------------------------------------------------------------------------
# FND-3 fix: Definition-occurrence multiplicity (not SymbolInformation
# multiplicity) gates cross-scope identity disambiguation
# ---------------------------------------------------------------------------
#
# FND-3's root cause, confirmed via direct inspection of scip-python's own
# indexer source (`packages/pyright-scip/src/treeVisitor.ts`, research
# only -- see `_nested_symbol_disambiguation`'s own docstring): scip-python
# does not emit `SymbolInformation` uniformly once-per-declaration. Function
# (`visitFunction`) and class (`emitDeclaration`'s `ParseNodeType.Class`
# branch) declarations push `SymbolInformation` unconditionally, once per
# visited node -- so their `SymbolInformation` count naturally tracks their
# Definition-occurrence count. But a plain name/attribute declaration
# (`emitDeclaration`'s generic branch -- which is what handles an instance
# attribute like `self.title = ...`, since the attribute name is visited as
# a `NameNode` under a `MemberAccessNode`) instead calls
# `emitSymbolInformationOnce`, an explicit, document-wide dedup-by-
# descriptor-string guard ("Only emit symbol info once"). A `.`-suffix
# instance-attribute descriptor genuinely redefined across 2+ distinct real
# local scopes can therefore carry exactly **one** `SymbolInformation` entry
# despite 2+ real, differently-scoped Definition-role Occurrences.
#
# `_nested_symbol_disambiguation`'s original gate (`symbol_info_counts >= 2
# and len(occs) >= 2`) silently skipped every such symbol -- it never even
# attempted cross-scope resolution, so genuinely distinct real entities
# collapsed to one canonical entity. The fix drops the `SymbolInformation`
# clause entirely: only Definition-occurrence multiplicity (`len(occs) >=
# 2`) triggers an attempt at disambiguation; whether that attempt actually
# finds 2+ *distinct* real scopes (worth splitting) or exactly 1 shared real
# scope (must stay merged, the ordinary GAP-13/14 case) is decided
# afterward, by the SAME unchanged `_build_scope_forest`/
# `_container_family_for` family-resolution machinery FND-1/FND-2 already
# validated -- this fix widens what's allowed to *reach* that machinery,
# it does not change what the machinery itself decides.
#
# Every fixture below writes a real, matching Python source file to
# `tmp_path` (not just synthetic `.scip` occurrences), following the
# established convention, so the true-indentation/scope-opening read paths
# are actually exercised.


def test_fnd3_symbolinfo_one_definition_occurrences_multiple_distinct_scopes(
    tmp_path: Path,
) -> None:
    """(1) FND-3's own flagship reproduction, mirrored from the real click
    `tests/test_context.py` `Foo#title.` case: a local class `Foo`,
    redefined separately inside three different top-level test functions,
    each assigning `self.title = "default"` in its own `__init__`. Exactly
    ONE `SymbolInformation` entry is emitted for `Foo#title.` (matching
    scip-python's own confirmed `emitSymbolInformationOnce` dedup-by-
    descriptor-string behavior for attribute declarations) despite THREE
    real, differently-scoped Definition-role Occurrences. Before this fix,
    `symbol_info_counts.get(symbol, 0) <= 1` was true (1 <= 1), so this
    symbol never reached scope disambiguation at all and all three real,
    distinct `title` attributes collapsed into one canonical entity. After
    the fix: three distinct entities, one per distinct real `Foo` class."""
    source = (
        "def test_a():\n"
        "    class Foo:\n"
        "        def __init__(self):\n"
        '            self.title = "default"\n'
        "\n"
        "\n"
        "def test_b():\n"
        "    class Foo:\n"
        "        def __init__(self):\n"
        '            self.title = "default"\n'
        "\n"
        "\n"
        "def test_c():\n"
        "    class Foo:\n"
        "        def __init__(self):\n"
        '            self.title = "default"\n'
    )
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text(source, encoding="utf-8")

    test_a = "scip-python python testrepo rev1 `pkg.a`/test_a()."
    test_b = "scip-python python testrepo rev1 `pkg.a`/test_b()."
    test_c = "scip-python python testrepo rev1 `pkg.a`/test_c()."
    foo = "scip-python python testrepo rev1 `pkg.a`/Foo#"
    init = "scip-python python testrepo rev1 `pkg.a`/Foo#__init__()."
    title = "scip-python python testrepo rev1 `pkg.a`/Foo#title."

    occs = (
        occurrence(test_a, roles=1, range_=(0, 4, 10)),
        occurrence(foo, roles=1, range_=(1, 10, 13)),
        occurrence(init, roles=1, range_=(2, 12, 20)),
        occurrence(title, roles=1, range_=(3, 17, 22)),
        occurrence(test_b, roles=1, range_=(6, 4, 10)),
        occurrence(foo, roles=1, range_=(7, 10, 13)),
        occurrence(init, roles=1, range_=(8, 12, 20)),
        occurrence(title, roles=1, range_=(9, 17, 22)),
        occurrence(test_c, roles=1, range_=(12, 4, 10)),
        occurrence(foo, roles=1, range_=(13, 10, 13)),
        occurrence(init, roles=1, range_=(14, 12, 20)),
        occurrence(title, roles=1, range_=(15, 17, 22)),
    )
    sym_infos = (
        symbol_information(test_a, kind=0),
        symbol_information(foo, kind=7),
        symbol_information(init, kind=0),
        # Exactly ONE SymbolInformation for `title`, despite 3 real
        # Definition occurrences -- this is FND-3's exact confirmed
        # real-data shape, not a simplification.
        symbol_information(title, kind=0),
        symbol_information(test_b, kind=0),
        symbol_information(foo, kind=7),
        symbol_information(init, kind=0),
        symbol_information(test_c, kind=0),
        symbol_information(foo, kind=7),
        symbol_information(init, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    title_matches = [e for e in normalized.entities if e.qualified_name.endswith("title.")]
    assert len(title_matches) == 3
    assert len({e.canonical_id for e in title_matches}) == 3
    assert len({e.qualified_name for e in title_matches}) == 3
    for e in title_matches:
        assert "scip:nested-scope-disambiguated" in e.roles
    start_lines = sorted(
        e.source_location.start_line for e in title_matches if e.source_location is not None
    )
    assert start_lines == [3, 9, 15]

    foo_matches = [e for e in normalized.entities if e.qualified_name.endswith("Foo#")]
    assert len(foo_matches) == 3
    assert len({e.canonical_id for e in foo_matches}) == 3


def test_fnd3_symbolinfo_one_definition_occurrences_same_scope_converges(
    tmp_path: Path,
) -> None:
    """(2) The critical guard against the fix over-correcting: a symbol
    with exactly ONE `SymbolInformation` entry and 2+ real Definition-role
    Occurrences, where every occurrence resolves to the SAME true real
    scope (two `self.title = ...` assignments inside one shared
    `__init__`), must still converge to exactly ONE canonical entity, not
    split into two. Definition-occurrence multiplicity alone must never be
    read as entity count -- only genuinely distinct resolved scopes may
    produce distinct entities, and this fixture's own `_build_scope_forest`
    resolution puts both occurrences in the identical family (the single
    `Foo#__init__()` scope), so `_nested_symbol_disambiguation`'s own
    internal `distinct_real_scopes` check declines to split it, exactly as
    for any other single-scope multi-occurrence symbol."""
    source = (
        "def test_a():\n"
        "    class Foo:\n"
        "        def __init__(self):\n"
        '            self.title = "default"\n'
        '            self.title = "overridden"\n'
    )
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text(source, encoding="utf-8")

    test_a = "scip-python python testrepo rev1 `pkg.a`/test_a()."
    foo = "scip-python python testrepo rev1 `pkg.a`/Foo#"
    init = "scip-python python testrepo rev1 `pkg.a`/Foo#__init__()."
    title = "scip-python python testrepo rev1 `pkg.a`/Foo#title."

    occs = (
        occurrence(test_a, roles=1, range_=(0, 4, 10)),
        occurrence(foo, roles=1, range_=(1, 10, 13)),
        occurrence(init, roles=1, range_=(2, 12, 20)),
        occurrence(title, roles=1, range_=(3, 17, 22)),
        occurrence(title, roles=1, range_=(4, 17, 22)),
    )
    sym_infos = (
        symbol_information(test_a, kind=0),
        symbol_information(foo, kind=7),
        symbol_information(init, kind=0),
        # Exactly ONE SymbolInformation, matching FND-3's real shape --
        # but here BOTH Definition occurrences genuinely share one scope.
        symbol_information(title, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    title_matches = [e for e in normalized.entities if e.qualified_name.endswith("title.")]
    assert len(title_matches) == 1
    assert title_matches[0].qualified_name == "`pkg.a`/Foo#title."


def test_fnd3_class_level_attribute_multiple_distinct_scopes(tmp_path: Path) -> None:
    """(10) Class-level (not instance) attributes must also disambiguate
    correctly when genuinely redefined across distinct real scopes and
    carrying only one `SymbolInformation` entry -- the fix's own docstring
    claims the mechanism is now suffix-shape-agnostic; this is the direct
    check for a class attribute rather than an instance attribute."""
    source = (
        "def make_a():\n"
        "    class Config:\n"
        "        debug = True\n"
        "\n"
        "\n"
        "def make_b():\n"
        "    class Config:\n"
        "        debug = True\n"
    )
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text(source, encoding="utf-8")

    make_a = "scip-python python testrepo rev1 `pkg.a`/make_a()."
    make_b = "scip-python python testrepo rev1 `pkg.a`/make_b()."
    config = "scip-python python testrepo rev1 `pkg.a`/Config#"
    debug = "scip-python python testrepo rev1 `pkg.a`/Config#debug."

    occs = (
        occurrence(make_a, roles=1, range_=(0, 4, 11)),
        occurrence(config, roles=1, range_=(1, 10, 16)),
        occurrence(debug, roles=1, range_=(2, 8, 13)),
        occurrence(make_b, roles=1, range_=(5, 4, 11)),
        occurrence(config, roles=1, range_=(6, 10, 16)),
        occurrence(debug, roles=1, range_=(7, 8, 13)),
    )
    sym_infos = (
        symbol_information(make_a, kind=0),
        symbol_information(config, kind=7),
        # Exactly one SymbolInformation for `debug`, despite 2 real
        # Definition occurrences in 2 distinct real `Config` classes.
        symbol_information(debug, kind=0),
        symbol_information(make_b, kind=0),
        symbol_information(config, kind=7),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name.endswith("debug.")]
    assert len(matches) == 2
    assert len({e.canonical_id for e in matches}) == 2


def test_fnd3_parameter_descriptor_unaffected(tmp_path: Path) -> None:
    """(11) Parameter descriptors (trailing `)`) are excluded from
    `_nested_symbol_disambiguation`'s own candidate collection by an
    unrelated, pre-existing, unchanged filter
    (`occ.symbol.endswith(")")`) -- verify the FND-3 gate change doesn't
    disturb that exclusion. A parameter name reused verbatim across two
    distinct functions (a real, extremely common shape -- e.g. two
    unrelated functions each with a `self` or `name` parameter) must never
    be treated as ambiguous or produce a `nested-scope-disambiguated`
    entity; parameters never become entities at all, regardless of how
    many raw Definition-role occurrences of that exact descriptor exist."""
    source = (
        "def make_a(name):\n"
        "    return name\n"
        "\n"
        "\n"
        "def make_b(name):\n"
        "    return name\n"
    )
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text(source, encoding="utf-8")

    make_a = "scip-python python testrepo rev1 `pkg.a`/make_a()."
    make_b = "scip-python python testrepo rev1 `pkg.a`/make_b()."
    # Parameter descriptors carry a `(param)` suffix in real SCIP output;
    # what matters here is the trailing `)` the existing filter checks.
    param_a = "scip-python python testrepo rev1 `pkg.a`/make_a().(name)"
    param_b = "scip-python python testrepo rev1 `pkg.a`/make_b().(name)"

    occs = (
        occurrence(make_a, roles=1, range_=(0, 4, 10)),
        occurrence(param_a, roles=1, range_=(0, 11, 15)),
        occurrence(make_b, roles=1, range_=(4, 4, 10)),
        occurrence(param_b, roles=1, range_=(4, 11, 15)),
    )
    sym_infos = (
        symbol_information(make_a, kind=0),
        symbol_information(make_b, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    assert not any(e.qualified_name.endswith("(name)") for e in normalized.entities)
    assert not any(
        "nested-scope-disambiguated" in e.roles and "name" in e.name for e in normalized.entities
    )


def test_fnd3_gap13_gap14_family_still_gated_by_own_symbolinfo_signal(tmp_path: Path) -> None:
    """(4) GAP-13/14 regression: `_redefinition_family_locations` is a
    completely separate function with its own, untouched
    `symbol_info_counts` gate (used for a different purpose -- recovering
    a representative location for a *non*-cross-scope-ambiguous
    redefinition family, e.g. `@property`/`@x.setter`). This fix touches
    only `_nested_symbol_disambiguation`'s entry condition; a legitimate
    property/setter pair must still converge to one entity via the
    untouched GAP-13/14 path."""
    source = (
        "class Widget:\n"
        "    @property\n"
        "    def value(self):\n"
        "        return self._value\n"
        "\n"
        "    @value.setter\n"
        "    def value(self, val):\n"
        "        self._value = val\n"
    )
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text(source, encoding="utf-8")

    cls = "scip-python python testrepo rev1 `pkg.a`/Widget#"
    value = "scip-python python testrepo rev1 `pkg.a`/Widget#value()."

    occs = (
        occurrence(cls, roles=1, range_=(0, 6, 12)),
        occurrence(value, roles=1, range_=(2, 8, 13)),
        occurrence(value, roles=1, range_=(6, 8, 13)),
    )
    sym_infos = (
        symbol_information(cls, kind=7),
        symbol_information(value, kind=0),
        symbol_information(value, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name.endswith("value().")]
    assert len(matches) == 1
    assert "scip:redefinition-family" in matches[0].roles


def test_fnd3_fnd1_fnd2_cases_unaffected(tmp_path: Path) -> None:
    """(2, 3, 5, 6, 7) FND-1/FND-2 regression, combined: ordinary
    function/class declarations (SymbolInformation count already matches
    Definition-occurrence count for these kinds, per this fix's own
    docstring) must behave identically before and after the fix --
    nested closures under different parent methods (FND-1), an ambiguous
    intermediate container (FND-2), and 3+ levels of nesting, all in one
    document."""
    source = (
        "class Outer:\n"
        "    def make_a(self):\n"
        "        def level_b():\n"
        "            def target():\n"
        "                pass\n"
        "            return target\n"
        "        return level_b\n"
        "\n"
        "    def make_b(self):\n"
        "        def level_b():\n"
        "            def target():\n"
        "                pass\n"
        "            return target\n"
        "        return level_b\n"
    )
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text(source, encoding="utf-8")

    cls = "scip-python python testrepo rev1 `pkg.a`/Outer#"
    make_a = "scip-python python testrepo rev1 `pkg.a`/Outer#make_a()."
    make_b = "scip-python python testrepo rev1 `pkg.a`/Outer#make_b()."
    level_b = "scip-python python testrepo rev1 `pkg.a`/Outer#level_b()."
    target = "scip-python python testrepo rev1 `pkg.a`/Outer#target()."

    occs = (
        occurrence(cls, roles=1, range_=(0, 6, 11)),
        occurrence(make_a, roles=1, range_=(1, 8, 14)),
        occurrence(level_b, roles=1, range_=(2, 12, 19)),
        occurrence(target, roles=1, range_=(3, 16, 22)),
        occurrence(make_b, roles=1, range_=(8, 8, 14)),
        occurrence(level_b, roles=1, range_=(9, 12, 19)),
        occurrence(target, roles=1, range_=(10, 16, 22)),
    )
    sym_infos = (
        symbol_information(cls, kind=7),
        symbol_information(make_a, kind=0),
        symbol_information(level_b, kind=0),
        symbol_information(target, kind=0),
        symbol_information(make_b, kind=0),
        symbol_information(level_b, kind=0),
        symbol_information(target, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    target_matches = [e for e in normalized.entities if e.qualified_name.endswith("target().")]
    assert len(target_matches) == 2
    assert len({e.canonical_id for e in target_matches}) == 2


def test_fnd3_readaccess_only_later_redefinition_unaffected(tmp_path: Path) -> None:
    """(8) A container class redefined in-place where the second instance
    carries only a ReadAccess-role occurrence (`_is_scope_opening_
    occurrence`'s verified-ReadAccess path, unchanged by this fix) --
    combined with an instance attribute nested inside it that ALSO shows
    FND-3's own 1-SymbolInformation shape. Both must still converge
    correctly: the container to one family (GAP-13/14-style), and the
    attribute (now reachable thanks to the FND-3 gate fix) to that same
    single family, not split."""
    source = (
        "def outer():\n"
        "    class Config:\n"
        "        def __init__(self):\n"
        '            self.debug = 1\n'
        "\n"
        "    class Config:\n"
        "        def __init__(self):\n"
        '            self.debug = 2\n'
        "    return Config\n"
    )
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text(source, encoding="utf-8")

    outer = "scip-python python testrepo rev1 `pkg.a`/outer()."
    config = "scip-python python testrepo rev1 `pkg.a`/Config#"
    init = "scip-python python testrepo rev1 `pkg.a`/Config#__init__()."
    debug = "scip-python python testrepo rev1 `pkg.a`/Config#debug."

    occs = (
        occurrence(outer, roles=1, range_=(0, 4, 9)),
        occurrence(config, roles=1, range_=(1, 10, 16)),
        occurrence(init, roles=1, range_=(2, 12, 20)),
        occurrence(debug, roles=1, range_=(3, 17, 22)),
        occurrence(config, roles=8, range_=(5, 10, 16)),
        occurrence(init, roles=1, range_=(6, 12, 20)),
        occurrence(debug, roles=1, range_=(7, 17, 22)),
    )
    sym_infos = (
        symbol_information(outer, kind=0),
        symbol_information(config, kind=7),
        symbol_information(init, kind=0),
        # Exactly one SymbolInformation for `debug` -- FND-3's shape --
        # even though BOTH occurrences genuinely belong to the SAME
        # shared real `Config` family (redefined in place, GAP-13/14
        # pattern for the container itself).
        symbol_information(debug, kind=0),
        symbol_information(init, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name.endswith("debug.")]
    assert len(matches) == 1
    assert matches[0].qualified_name == "`pkg.a`/Config#debug."


def test_fnd3_ast_scip_cross_provider_convergence_unaffected(tmp_path: Path) -> None:
    """(9) A plain, unambiguous, top-level method with only ONE real
    scope must still converge correctly with `AstCallsAdapter`'s own
    independently-derived entity via `_symbol_location_identity_key`
    (GAP-13's cross-provider mechanism, untouched by this fix) -- the
    FND-3 gate change must not perturb a symbol that was never ambiguous
    in the first place, regardless of its own SymbolInformation count."""
    source = "class Widget:\n    def compute(self):\n        return 1\n"
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text(source, encoding="utf-8")

    cls = "scip-python python testrepo rev1 `pkg.a`/Widget#"
    compute = "scip-python python testrepo rev1 `pkg.a`/Widget#compute()."

    occs = (
        occurrence(cls, roles=1, range_=(0, 6, 12)),
        occurrence(compute, roles=1, range_=(1, 8, 15)),
    )
    sym_infos = (
        symbol_information(cls, kind=7),
        symbol_information(compute, kind=0),
    )
    doc = document("pkg/a.py", occurrences=occs, symbols=sym_infos)
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc,)))

    adapter = SCIPAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    matches = [e for e in normalized.entities if e.qualified_name.endswith("compute().")]
    assert len(matches) == 1
    assert "scip:nested-scope-disambiguated" not in matches[0].roles
