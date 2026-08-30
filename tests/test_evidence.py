from codex.evidence import (
    CanonicalRelationship,
    CoverageStatus,
    Evidence,
    EvidenceCohort,
    EvidenceStatus,
    InMemoryEvidenceStore,
)
from codex.ontology import RelationshipType


def make_evidence(evidence_id: str, provider: str = "SCIP", confidence: float = 0.9) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        provider=provider,
        provider_version="1.0",
        snapshot_id="snap-1",
        source_revision="abc123",
        subject="codex:A",
        predicate=RelationshipType.CALLS,
        object="codex:B",
        confidence=confidence,
        freshness="2026-08-30T00:00:00Z",
    )


def test_evidence_independence_group_defaults_to_provider() -> None:
    evidence = make_evidence("e1")
    assert evidence.effective_independence_group == "provider_default:SCIP"


def test_evidence_independence_group_explicit_override() -> None:
    evidence = make_evidence("e1").model_copy(update={"independence_group": "static-analysis"})
    assert evidence.effective_independence_group == "static-analysis"


def test_evidence_store_round_trip() -> None:
    store = InMemoryEvidenceStore()
    e1 = make_evidence("e1", provider="SCIP")
    e2 = make_evidence("e2", provider="CodeQL")
    store.add_evidence(e1)
    store.add_evidence(e2)

    assert store.get_evidence("e1") == e1
    assert store.get_evidence("missing") is None

    results = store.get_evidence_for(subject="codex:A", predicate=RelationshipType.CALLS)
    assert {e.evidence_id for e in results} == {"e1", "e2"}


def test_evidence_cohort_supports() -> None:
    cohort = EvidenceCohort(
        provider="CodeQL",
        provider_version="2.0",
        snapshot_id="snap-1",
        source_revision="abc123",
        successful_capabilities=["CALL_RELATIONSHIP"],
        failed_capabilities=["DATA_FLOW"],
        coverage_status=CoverageStatus.PARTIAL,
    )
    assert cohort.supports("CALL_RELATIONSHIP")
    assert not cohort.supports("DATA_FLOW")


def test_evidence_store_cohorts_filter_by_provider() -> None:
    store = InMemoryEvidenceStore()
    store.add_cohort(
        EvidenceCohort(
            provider="SCIP", provider_version="1.0", snapshot_id="s1", source_revision="abc123"
        )
    )
    store.add_cohort(
        EvidenceCohort(
            provider="CodeQL", provider_version="2.0", snapshot_id="s1", source_revision="abc123"
        )
    )
    assert len(store.get_cohorts()) == 2
    assert [c.provider for c in store.get_cohorts(provider="SCIP")] == ["SCIP"]


def test_canonical_relationship_key() -> None:
    rel = CanonicalRelationship(
        subject="codex:A",
        predicate=RelationshipType.CALLS,
        object="codex:B",
        confidence=0.94,
        status=EvidenceStatus.SUPPORTED,
        supporting_evidence_ids=["e1", "e2"],
    )
    assert rel.key == ("codex:A", RelationshipType.CALLS, "codex:B")
