"""Behavioral contract tests for ProviderAdapter (TAD §9; directive §13).

Exercises the contract through ``FakeProviderAdapter`` — a fixture,
not a real Codex provider. Git/SCIP adapters are out of scope for D1.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from codex.evidence.model import CoverageStatus, Evidence
from codex.ontology.relationships import RelationshipType
from codex.provider.capability import Capability
from codex.provider.contract import (
    EligibilityStatus,
    ExtractionResult,
    ProviderEligibility,
    ProviderExtractionError,
    ProviderFailureReason,
    ProviderHealthStatus,
)
from codex.repository.models import RepositoryMetadata
from fake_provider_adapter import FakeProviderAdapter


def make_repo(revision: str = "abc123", repository_id: str = "repo1") -> RepositoryMetadata:
    return RepositoryMetadata(
        repository_id=repository_id, local_path=Path("/tmp/repo1"), head_revision=revision
    )


def make_evidence(**overrides: object) -> Evidence:
    fields = {
        "evidence_id": "e1",
        "provider": "FAKE",
        "provider_version": "1.0",
        "snapshot_id": "s1",
        "source_revision": "abc",
        "subject": "codex:A",
        "predicate": RelationshipType.CALLS,
        "object": "codex:B",
        "confidence": 0.9,
        "freshness": datetime.now(UTC),
    }
    fields.update(overrides)
    return Evidence(**fields)  # type: ignore[arg-type]


# 1. Provider identity
def test_provider_identity() -> None:
    assert FakeProviderAdapter(name="FAKE").provider_name == "FAKE"


# 2. Provider version
def test_provider_version() -> None:
    assert FakeProviderAdapter(version="2.3.1").provider_version == "2.3.1"


# 3. Capability declaration
def test_capability_declaration() -> None:
    caps = frozenset({Capability.SYMBOL_DEFINITION, Capability.DATA_FLOW})
    assert FakeProviderAdapter(capabilities=caps).supported_capabilities == caps


# 4. Supported capability
def test_supported_capability_is_extracted() -> None:
    adapter = FakeProviderAdapter(capabilities=frozenset({Capability.CALL_RELATIONSHIP}))
    result = adapter.extract(make_repo(), [Capability.CALL_RELATIONSHIP])
    assert result.cohort.successful_capabilities == [Capability.CALL_RELATIONSHIP.value]


# 5. Unsupported capability: absent, not failed
def test_unsupported_capability_is_absent_not_failed() -> None:
    adapter = FakeProviderAdapter(capabilities=frozenset({Capability.CALL_RELATIONSHIP}))
    result = adapter.extract(make_repo(), [Capability.DATA_FLOW])
    assert Capability.DATA_FLOW.value not in result.cohort.successful_capabilities
    assert Capability.DATA_FLOW.value not in result.cohort.failed_capabilities
    assert Capability.DATA_FLOW.value not in result.cohort.partial_capabilities


# 6. Successful execution end to end (extract -> normalize)
def test_successful_execution_produces_evidence() -> None:
    adapter = FakeProviderAdapter(capabilities=frozenset({Capability.CALL_RELATIONSHIP}))
    result = adapter.extract(make_repo(), [Capability.CALL_RELATIONSHIP])
    normalized = adapter.normalize(result)
    assert len(normalized.evidence) == 1
    assert normalized.cohort.coverage_status == CoverageStatus.FULL


# 7. Empty successful result: succeeded, zero results -- not a failure
def test_empty_successful_result_is_distinct_from_failure() -> None:
    adapter = FakeProviderAdapter(
        capabilities=frozenset({Capability.CALL_RELATIONSHIP}),
        empty_capabilities=frozenset({Capability.CALL_RELATIONSHIP}),
    )
    result = adapter.extract(make_repo(), [Capability.CALL_RELATIONSHIP])
    normalized = adapter.normalize(result)
    assert result.cohort.successful_capabilities == [Capability.CALL_RELATIONSHIP.value]
    assert result.cohort.failed_capabilities == []
    assert normalized.evidence == []


# 8. Partial result
def test_partial_result() -> None:
    adapter = FakeProviderAdapter(
        capabilities=frozenset({Capability.CALL_RELATIONSHIP, Capability.DATA_FLOW}),
        partial_capabilities=frozenset({Capability.DATA_FLOW}),
    )
    result = adapter.extract(make_repo(), [Capability.CALL_RELATIONSHIP, Capability.DATA_FLOW])
    assert result.cohort.partial_capabilities == [Capability.DATA_FLOW.value]
    assert result.cohort.successful_capabilities == [Capability.CALL_RELATIONSHIP.value]
    assert result.cohort.coverage_status == CoverageStatus.PARTIAL


# 9. Failed capability: reported, does not raise
def test_failed_capability_does_not_raise() -> None:
    adapter = FakeProviderAdapter(
        capabilities=frozenset({Capability.CALL_RELATIONSHIP}),
        fail_capabilities=frozenset({Capability.CALL_RELATIONSHIP}),
    )
    result = adapter.extract(make_repo(), [Capability.CALL_RELATIONSHIP])
    assert result.cohort.failed_capabilities == [Capability.CALL_RELATIONSHIP.value]
    assert result.cohort.successful_capabilities == []
    assert result.cohort.coverage_status == CoverageStatus.NONE


# 10. Provider failure: raises, distinct from a failed capability
def test_provider_failure_raises_distinct_from_capability_failure() -> None:
    adapter = FakeProviderAdapter(raise_on_extract=ProviderFailureReason.UNAVAILABLE)
    with pytest.raises(ProviderExtractionError) as exc_info:
        adapter.extract(make_repo(), [Capability.CALL_RELATIONSHIP])
    assert exc_info.value.reason is ProviderFailureReason.UNAVAILABLE
    assert exc_info.value.provider_name == adapter.provider_name


# 11. Evidence provenance
def test_evidence_provenance() -> None:
    adapter = FakeProviderAdapter(
        name="FAKE", version="9.9.9", capabilities=frozenset({Capability.CALL_RELATIONSHIP})
    )
    result = adapter.extract(make_repo(revision="deadbeef"), [Capability.CALL_RELATIONSHIP])
    normalized = adapter.normalize(result)
    ev = normalized.evidence[0]
    assert ev.provider == "FAKE"
    assert ev.provider_version == "9.9.9"
    assert ev.source_revision == "deadbeef"
    assert ev.snapshot_id == result.cohort.snapshot_id


# 12. independence_group: explicit value respected
def test_independence_group_explicit() -> None:
    ev = make_evidence(independence_group="static-analysis")
    assert ev.effective_independence_group == "static-analysis"


# 13. Default NON_INDEPENDENT when omitted
def test_independence_group_default_non_independent() -> None:
    ev = make_evidence(provider="FAKE")
    assert ev.effective_independence_group == "provider_default:FAKE"


# 14. Source revision
def test_source_revision_flows_through() -> None:
    adapter = FakeProviderAdapter(capabilities=frozenset({Capability.CALL_RELATIONSHIP}))
    result = adapter.extract(make_repo(revision="rev-42"), [Capability.CALL_RELATIONSHIP])
    assert result.cohort.source_revision == "rev-42"


# 15. Snapshot identity distinguishes extraction runs
def test_snapshot_identity_distinguishes_extraction_runs() -> None:
    adapter = FakeProviderAdapter(capabilities=frozenset({Capability.CALL_RELATIONSHIP}))
    result1 = adapter.extract(make_repo(), [Capability.CALL_RELATIONSHIP])
    result2 = adapter.extract(make_repo(), [Capability.CALL_RELATIONSHIP])
    assert result1.cohort.snapshot_id != result2.cohort.snapshot_id


# 16. Raw artifact reference must be a resolvable URI
def test_raw_reference_must_be_resolvable_uri() -> None:
    adapter = FakeProviderAdapter(capabilities=frozenset({Capability.CALL_RELATIONSHIP}))
    result = adapter.extract(make_repo(), [Capability.CALL_RELATIONSHIP])
    assert result.raw_reference is not None
    assert result.raw_reference.startswith("artifact://")

    with pytest.raises(ValueError, match="raw_reference"):
        ExtractionResult(cohort=result.cohort, raw_reference="not-a-valid-scheme")


# 17. License/eligibility metadata
def test_eligibility_reports_license_gating() -> None:
    adapter = FakeProviderAdapter(
        eligibility=ProviderEligibility(
            status=EligibilityStatus.INELIGIBLE_LICENSE, reason="no GHAS license"
        )
    )
    eligibility = adapter.check_eligibility(make_repo())
    assert not eligibility.eligible
    assert eligibility.status is EligibilityStatus.INELIGIBLE_LICENSE


def test_eligible_by_default() -> None:
    assert FakeProviderAdapter().check_eligibility(make_repo()).eligible


# 18. Graph-version association: evidence carries the requested revision, never a stale one
def test_evidence_carries_the_requested_revision_not_a_stale_one() -> None:
    adapter = FakeProviderAdapter(capabilities=frozenset({Capability.CALL_RELATIONSHIP}))
    result_a = adapter.extract(make_repo(revision="rev-a"), [Capability.CALL_RELATIONSHIP])
    result_b = adapter.extract(make_repo(revision="rev-b"), [Capability.CALL_RELATIONSHIP])
    normalized_a = adapter.normalize(result_a)
    normalized_b = adapter.normalize(result_b)
    assert normalized_a.evidence[0].source_revision == "rev-a"
    assert normalized_b.evidence[0].source_revision == "rev-b"


# Bonus: health/availability/freshness (TAD §9, not separately numbered above)
def test_unhealthy_provider_reported() -> None:
    adapter = FakeProviderAdapter(health=ProviderHealthStatus.UNHEALTHY)
    assert adapter.health_status is ProviderHealthStatus.UNHEALTHY
    assert adapter.validate().ok is False


def test_freshness_updates_after_extraction() -> None:
    adapter = FakeProviderAdapter(capabilities=frozenset({Capability.CALL_RELATIONSHIP}))
    assert adapter.freshness is None
    adapter.extract(make_repo(), [Capability.CALL_RELATIONSHIP])
    assert adapter.freshness is not None


# D1 clarification (2026-08-30): health_status and availability are independent signals.
def test_healthy_provider_can_report_zero_availability() -> None:
    """A HEALTHY provider may still be unavailable for a specific capability
    in a specific repository/environment (e.g. a missing license) — this
    must be expressible without touching health_status at all."""
    adapter = FakeProviderAdapter(
        health=ProviderHealthStatus.HEALTHY,
        default_availability=1.0,
        availability_overrides={Capability.DATA_FLOW: 0.0},
    )
    repo = make_repo()
    assert adapter.health_status is ProviderHealthStatus.HEALTHY
    assert adapter.availability(Capability.DATA_FLOW, repo) == 0.0
    assert adapter.availability(Capability.CALL_RELATIONSHIP, repo) == 1.0


def test_unhealthy_provider_can_report_nonzero_availability() -> None:
    """The contract does not derive availability from health_status (or vice
    versa) — an adapter is free to report either independently."""
    adapter = FakeProviderAdapter(
        health=ProviderHealthStatus.UNHEALTHY, default_availability=1.0
    )
    assert adapter.availability(Capability.CALL_RELATIONSHIP, make_repo()) == 1.0


def test_availability_varies_per_capability_and_is_normalized() -> None:
    adapter = FakeProviderAdapter(
        default_availability=0.5,
        availability_overrides={Capability.CALL_RELATIONSHIP: 1.0, Capability.DATA_FLOW: 0.0},
    )
    repo = make_repo()
    assert adapter.availability(Capability.CALL_RELATIONSHIP, repo) == 1.0
    assert adapter.availability(Capability.DATA_FLOW, repo) == 0.0
    assert adapter.availability(Capability.SYMBOL_DEFINITION, repo) == 0.5
    for capability in Capability:
        assert 0.0 <= adapter.availability(capability, repo) <= 1.0
