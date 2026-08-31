"""Behavioral tests for the D6 CodeQL Adapter (directive D6's test list).

Uses real SARIF artifacts fetched from `github/codeql-action`'s own test
fixtures (genuine output from "CodeQL command-line toolchain" and its
LGTM.com predecessor -- see `docs/resources.md`), plus two handcrafted,
schema-faithful fixtures for scenarios no real fixture covered:
``path-problem.sarif`` (a genuine data-flow/path-problem result, since
none of the real fixtures happened to contain one) and
``partial-multi-run.sarif`` (one good run + one malformed run, to
exercise per-run failure isolation within a single capability).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex.evidence.model import CoverageStatus, EvidenceStatus
from codex.evidence.store import InMemoryEvidenceStore
from codex.ingestion.pipeline import IngestionPipeline
from codex.ontology.entities import BaseEntityType
from codex.ontology.relationships import RelationshipType
from codex.provider.capability import Capability
from codex.provider.codeql_adapter import DEFAULT_SARIF_FILENAME, CodeQLAdapter
from codex.provider.contract import EligibilityStatus, ProviderExtractionError, ProviderHealthStatus
from codex.provider.git_adapter import GitAdapter
from codex.provider.scip_adapter import SCIPAdapter
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile
from codex.repository.models import RepositoryMetadata

FIXTURES = Path(__file__).parent / "fixtures" / "codeql"
PROFILE = ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.9)


def make_repository(local_path: Path, revision: str = "rev1") -> RepositoryMetadata:
    return RepositoryMetadata(repository_id="repo1", local_path=local_path, head_revision=revision)


def adapter_for(fixture_name: str) -> CodeQLAdapter:
    return CodeQLAdapter(sarif_filename=fixture_name)


# ---------------------------------------------------------------------------
# Identity, capabilities, health
# ---------------------------------------------------------------------------


def test_identity_and_capabilities() -> None:
    adapter = CodeQLAdapter()
    assert adapter.provider_name == "codeql"
    assert adapter.supported_capabilities == frozenset({Capability.DATA_FLOW})
    assert adapter.health_status is ProviderHealthStatus.HEALTHY
    assert adapter.validate().ok is True


def test_provider_version_unknown_before_extraction() -> None:
    assert CodeQLAdapter().provider_version == "unknown"


def test_provider_version_reflects_real_sarif_tool_info() -> None:
    adapter = adapter_for("valid-sarif.sarif")
    adapter.extract(make_repository(FIXTURES), adapter.supported_capabilities)
    assert adapter.provider_version == "LGTM.com@1.24.0-SNAPSHOT"


def test_provider_version_falls_back_to_bare_name_without_version() -> None:
    adapter = adapter_for("tool-names.sarif")
    adapter.extract(make_repository(FIXTURES), adapter.supported_capabilities)
    assert adapter.provider_version == "CodeQL command-line toolchain"


# ---------------------------------------------------------------------------
# Eligibility / availability (directive D6: SUPPORTED/AVAILABLE/INELIGIBLE/
# UNAVAILABLE/FAILED/PARTIAL; a licensing restriction is never a "failure")
# ---------------------------------------------------------------------------


def test_check_eligibility_missing_sarif_file(tmp_path: Path) -> None:
    result = CodeQLAdapter().check_eligibility(make_repository(tmp_path))
    assert result.status is EligibilityStatus.INELIGIBLE_REPOSITORY


def test_check_eligibility_sarif_present() -> None:
    adapter = adapter_for("valid-sarif.sarif")
    assert adapter.check_eligibility(make_repository(FIXTURES)).eligible is True


def test_check_eligibility_directory_at_sarif_path_is_ineligible(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_SARIF_FILENAME).mkdir()
    result = CodeQLAdapter().check_eligibility(make_repository(tmp_path))
    assert result.status is EligibilityStatus.INELIGIBLE_REPOSITORY


def test_availability_zero_for_unsupported_capability() -> None:
    adapter = adapter_for("valid-sarif.sarif")
    assert adapter.availability(Capability.SYMBOL_DEFINITION, make_repository(FIXTURES)) == 0.0


def test_availability_full_when_eligible_and_supported() -> None:
    adapter = adapter_for("valid-sarif.sarif")
    assert adapter.availability(Capability.DATA_FLOW, make_repository(FIXTURES)) == 1.0


def test_availability_zero_when_ineligible(tmp_path: Path) -> None:
    adapter = CodeQLAdapter()
    assert adapter.availability(Capability.DATA_FLOW, make_repository(tmp_path)) == 0.0


def test_custom_sarif_filename(tmp_path: Path) -> None:
    (tmp_path / "custom.sarif").write_text(json.dumps({"version": "2.1.0", "runs": []}))
    adapter = CodeQLAdapter(sarif_filename="custom.sarif")
    assert adapter.check_eligibility(make_repository(tmp_path)).eligible is True
    assert not (tmp_path / DEFAULT_SARIF_FILENAME).exists()


# ---------------------------------------------------------------------------
# extract() failure modes: UNAVAILABLE (missing/malformed artifact)
# ---------------------------------------------------------------------------


def test_extract_missing_sarif_raises_unavailable(tmp_path: Path) -> None:
    adapter = CodeQLAdapter()
    with pytest.raises(ProviderExtractionError):
        adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)


def test_extract_garbage_json_raises_unavailable(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_SARIF_FILENAME).write_text("{not valid json")
    adapter = CodeQLAdapter()
    with pytest.raises(ProviderExtractionError, match="malformed SARIF JSON"):
        adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)


def test_extract_empty_file_raises_unavailable(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_SARIF_FILENAME).write_text("")
    adapter = CodeQLAdapter()
    with pytest.raises(ProviderExtractionError):
        adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)


def test_extract_missing_runs_field_raises_unavailable(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_SARIF_FILENAME).write_text(json.dumps({"version": "2.1.0"}))
    adapter = CodeQLAdapter()
    with pytest.raises(ProviderExtractionError, match="missing/invalid"):
        adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)


def test_extract_zero_runs_is_a_clean_empty_run(tmp_path: Path) -> None:
    (tmp_path / DEFAULT_SARIF_FILENAME).write_text(json.dumps({"version": "2.1.0", "runs": []}))
    adapter = CodeQLAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    assert result.cohort.successful_capabilities == [Capability.DATA_FLOW.value]
    assert result.cohort.coverage_status is CoverageStatus.FULL


def test_freshness_set_after_successful_extraction() -> None:
    adapter = adapter_for("valid-sarif.sarif")
    assert adapter.freshness is None
    adapter.extract(make_repository(FIXTURES), adapter.supported_capabilities)
    assert adapter.freshness is not None


def test_extract_no_requested_capabilities_is_a_clean_empty_run() -> None:
    adapter = adapter_for("valid-sarif.sarif")
    result = adapter.extract(make_repository(FIXTURES), [])
    assert result.cohort.successful_capabilities == []
    assert result.cohort.coverage_status is CoverageStatus.NONE


def test_extract_unsupported_capability_silently_dropped() -> None:
    adapter = adapter_for("valid-sarif.sarif")
    result = adapter.extract(make_repository(FIXTURES), [Capability.SYMBOL_DEFINITION])
    assert result.cohort.successful_capabilities == []
    assert result.cohort.failed_capabilities == []


# ---------------------------------------------------------------------------
# Real SARIF artifacts: empty result, valid findings, malformed input
# ---------------------------------------------------------------------------


def test_real_empty_sarif_is_successful_not_a_failure() -> None:
    adapter = adapter_for("empty-sarif.sarif")
    result = adapter.extract(make_repository(FIXTURES), adapter.supported_capabilities)
    assert result.cohort.coverage_status is CoverageStatus.FULL
    assert Capability.DATA_FLOW.value in result.cohort.successful_capabilities
    normalized = adapter.normalize(result)
    assert normalized.entities == []
    assert normalized.evidence == []


def test_real_valid_sarif_two_runs_both_problem_kind_become_roles() -> None:
    adapter = adapter_for("valid-sarif.sarif")
    result = adapter.extract(make_repository(FIXTURES), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    assert len(normalized.entities) == 2
    by_name = {e.qualified_name: e for e in normalized.entities}
    assert by_name["main.js"].base_type is BaseEntityType.FILE
    assert "codeql:finding:js/unused-local-variable" in by_name["main.js"].roles
    assert "codeql:finding:js/inconsistent-use-of-new" in by_name["src/promiseUtils.js"].roles
    # Direct findings never produce a fabricated Evidence relationship.
    assert normalized.evidence == []


def test_real_fingerprinting_fixture_resolves_artifact_index() -> None:
    # fingerprinting.input.sarif's second result has only an `index` into
    # run.artifacts[], no inline `uri` -- a real-world case this adapter
    # must resolve rather than drop (docs/resources.md).
    adapter = adapter_for("fingerprinting.input.sarif")
    result = adapter.extract(make_repository(FIXTURES), adapter.supported_capabilities)
    normalized = adapter.normalize(result)
    names = {e.qualified_name for e in normalized.entities}
    assert names == {"testFile1.js", "testFile2.js"}


def test_real_invalid_sarif_results_not_array_fails_capability() -> None:
    adapter = adapter_for("invalid-sarif.sarif")
    result = adapter.extract(make_repository(FIXTURES), adapter.supported_capabilities)
    assert Capability.DATA_FLOW.value in result.cohort.failed_capabilities
    assert result.cohort.coverage_status is CoverageStatus.PARTIAL
    normalized = adapter.normalize(result)
    assert normalized.entities == []


def test_real_with_invalid_uri_rejects_non_conforming_uri_not_fabricated() -> None:
    # SARIF's schema declares artifactLocation.uri as format "uri-reference";
    # "not a valid URI" (the real fixture's literal value) violates that.
    # This must not become a graph node -- security requirement, directive D6.
    adapter = adapter_for("with-invalid-uri.sarif")
    result = adapter.extract(make_repository(FIXTURES), adapter.supported_capabilities)
    assert Capability.DATA_FLOW.value in result.cohort.successful_capabilities
    normalized = adapter.normalize(result)
    assert normalized.entities == []


def test_real_tool_names_fixture_all_runs_export_rules_metadata_only() -> None:
    # Per the authoritative SARIF schema: "results" may be omitted entirely
    # when a run solely exports rule metadata -- not malformed, not a failure.
    adapter = adapter_for("tool-names.sarif")
    result = adapter.extract(make_repository(FIXTURES), adapter.supported_capabilities)
    assert result.cohort.coverage_status is CoverageStatus.FULL
    assert result.cohort.failed_capabilities == []


# ---------------------------------------------------------------------------
# Partial results / per-run failure isolation (directive D6)
# ---------------------------------------------------------------------------


def test_partial_multi_run_isolates_bad_run_from_good_run() -> None:
    adapter = adapter_for("partial-multi-run.sarif")
    result = adapter.extract(make_repository(FIXTURES), adapter.supported_capabilities)
    assert result.cohort.partial_capabilities == [Capability.DATA_FLOW.value]
    assert result.cohort.coverage_status is CoverageStatus.PARTIAL

    normalized = adapter.normalize(result)
    assert len(normalized.entities) == 1
    assert normalized.entities[0].qualified_name == "src/good.js"


# ---------------------------------------------------------------------------
# Direct vs. derived evidence, relationship derivation rules (directive D6)
# ---------------------------------------------------------------------------


def test_path_problem_produces_source_to_sink_references_evidence() -> None:
    adapter = adapter_for("path-problem.sarif")
    result = adapter.extract(make_repository(FIXTURES), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    assert len(normalized.evidence) == 1
    ev = normalized.evidence[0]
    assert ev.predicate is RelationshipType.REFERENCES
    entities_by_id = {e.canonical_id: e for e in normalized.entities}
    assert entities_by_id[ev.subject].qualified_name == "src/handler.js"
    assert entities_by_id[ev.object].qualified_name == "src/db.js"
    assert ev.confidence == 1.0  # level="error"
    assert ev.raw_reference == "artifact://sarif/0/0"


def test_no_calls_extends_implements_or_depends_on_ever_produced() -> None:
    # directive D6 semantic-fidelity requirement: never infer these from CodeQL.
    for fixture in ["valid-sarif.sarif", "path-problem.sarif", "fingerprinting.input.sarif"]:
        adapter = adapter_for(fixture)
        result = adapter.extract(make_repository(FIXTURES), adapter.supported_capabilities)
        normalized = adapter.normalize(result)
        forbidden = {
            RelationshipType.CALLS,
            RelationshipType.EXTENDS,
            RelationshipType.IMPLEMENTS,
            RelationshipType.DEPENDS_ON,
        }
        assert all(ev.predicate not in forbidden for ev in normalized.evidence)


def test_result_with_non_dict_entries_in_results_array_is_skipped(tmp_path: Path) -> None:
    sarif = {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "CodeQL command-line toolchain"}},
                "results": [
                    "not-a-result-object",
                    {"message": {"text": "no ruleId here"}},
                    {
                        "ruleId": "js/example",
                        "message": {"text": "valid"},
                        "locations": [
                            {"physicalLocation": {"artifactLocation": {"uri": "a.js"}}}
                        ],
                    },
                ],
            }
        ],
    }
    (tmp_path / DEFAULT_SARIF_FILENAME).write_text(json.dumps(sarif))
    adapter = CodeQLAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)
    assert len(normalized.entities) == 1
    assert normalized.entities[0].qualified_name == "a.js"


def test_location_missing_physical_location_is_skipped(tmp_path: Path) -> None:
    sarif = {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "CodeQL command-line toolchain"}},
                "results": [
                    {
                        "ruleId": "js/example",
                        "message": {"text": "no physicalLocation at all"},
                        "locations": [{"logicalLocations": []}],
                    }
                ],
            }
        ],
    }
    (tmp_path / DEFAULT_SARIF_FILENAME).write_text(json.dumps(sarif))
    adapter = CodeQLAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)
    assert normalized.entities == []


def test_location_with_no_resolvable_artifact_uri_is_skipped(tmp_path: Path) -> None:
    sarif = {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "CodeQL command-line toolchain"}},
                "results": [
                    {
                        "ruleId": "js/example",
                        "message": {"text": "no artifact location"},
                        "locations": [{"physicalLocation": {"artifactLocation": {}}}],
                    }
                ],
            }
        ],
    }
    (tmp_path / DEFAULT_SARIF_FILENAME).write_text(json.dumps(sarif))
    adapter = CodeQLAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)
    assert normalized.entities == []


def test_code_flow_with_empty_thread_flows_is_not_a_path(tmp_path: Path) -> None:
    sarif = {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "CodeQL command-line toolchain"}},
                "results": [
                    {
                        "ruleId": "js/example",
                        "message": {"text": "empty thread flows"},
                        "codeFlows": [{"threadFlows": []}],
                        "locations": [
                            {"physicalLocation": {"artifactLocation": {"uri": "a.js"}}}
                        ],
                    }
                ],
            }
        ],
    }
    (tmp_path / DEFAULT_SARIF_FILENAME).write_text(json.dumps(sarif))
    adapter = CodeQLAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)
    # Falls back to a plain finding since the codeFlow gave no usable path.
    assert normalized.evidence == []
    assert len(normalized.entities) == 1


def test_thread_flow_with_single_location_is_not_a_path(tmp_path: Path) -> None:
    sarif = {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "CodeQL command-line toolchain"}},
                "results": [
                    {
                        "ruleId": "js/example",
                        "message": {"text": "one-step flow"},
                        "codeFlows": [
                            {
                                "threadFlows": [
                                    {
                                        "locations": [
                                            {
                                                "location": {
                                                    "physicalLocation": {
                                                        "artifactLocation": {"uri": "a.js"}
                                                    }
                                                }
                                            }
                                        ]
                                    }
                                ]
                            }
                        ],
                        "locations": [
                            {"physicalLocation": {"artifactLocation": {"uri": "a.js"}}}
                        ],
                    }
                ],
            }
        ],
    }
    (tmp_path / DEFAULT_SARIF_FILENAME).write_text(json.dumps(sarif))
    adapter = CodeQLAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)
    assert normalized.evidence == []


def test_thread_flow_with_unresolvable_step_falls_below_two_locations(tmp_path: Path) -> None:
    sarif = {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "CodeQL command-line toolchain"}},
                "results": [
                    {
                        "ruleId": "js/example",
                        "message": {"text": "one step unresolvable"},
                        "codeFlows": [
                            {
                                "threadFlows": [
                                    {
                                        "locations": [
                                            {
                                                "location": {
                                                    "physicalLocation": {
                                                        "artifactLocation": {"uri": "a.js"}
                                                    }
                                                }
                                            },
                                            {
                                                "location": {
                                                    "physicalLocation": {
                                                        "artifactLocation": {}
                                                    }
                                                }
                                            },
                                        ]
                                    }
                                ]
                            }
                        ],
                        "locations": [
                            {"physicalLocation": {"artifactLocation": {"uri": "a.js"}}}
                        ],
                    }
                ],
            }
        ],
    }
    (tmp_path / DEFAULT_SARIF_FILENAME).write_text(json.dumps(sarif))
    adapter = CodeQLAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)
    assert normalized.evidence == []


def test_trivial_same_location_flow_is_not_treated_as_a_path(tmp_path: Path) -> None:
    sarif = {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "CodeQL command-line toolchain"}},
                "results": [
                    {
                        "ruleId": "js/example",
                        "message": {"text": "trivial"},
                        "codeFlows": [
                            {
                                "threadFlows": [
                                    {
                                        "locations": [
                                            {
                                                "location": {
                                                    "physicalLocation": {
                                                        "artifactLocation": {"uri": "a.js"},
                                                        "region": {"startLine": 1},
                                                    }
                                                }
                                            },
                                            {
                                                "location": {
                                                    "physicalLocation": {
                                                        "artifactLocation": {"uri": "a.js"},
                                                        "region": {"startLine": 1},
                                                    }
                                                }
                                            },
                                        ]
                                    }
                                ]
                            }
                        ],
                    }
                ],
            }
        ],
    }
    (tmp_path / DEFAULT_SARIF_FILENAME).write_text(json.dumps(sarif))
    adapter = CodeQLAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)
    assert normalized.evidence == []


# ---------------------------------------------------------------------------
# Entity identity, provenance, query identity/version
# ---------------------------------------------------------------------------


def test_file_entity_converges_with_git_and_scip_identity() -> None:
    from codex.ontology.entities import build_canonical_id

    adapter = adapter_for("valid-sarif.sarif")
    result = adapter.extract(make_repository(FIXTURES), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    expected_id = build_canonical_id(
        repository_id="repo1", repository_revision="rev1", qualified_name="main.js",
        base_type=BaseEntityType.FILE,
    )
    assert any(e.canonical_id == expected_id for e in normalized.entities)


def test_evidence_provenance_and_query_identity_preserved() -> None:
    adapter = adapter_for("path-problem.sarif")
    result = adapter.extract(make_repository(FIXTURES), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    ev = normalized.evidence[0]
    assert ev.provider == "codeql"
    assert ev.provider_version == "CodeQL command-line toolchain@2.15.0"
    assert ev.source_revision == "rev1"
    assert ev.snapshot_id == "rev1"
    assert ev.raw_reference is not None and ev.raw_reference.startswith("artifact://sarif/")


def test_two_findings_on_same_file_merge_roles_on_one_entity(tmp_path: Path) -> None:
    sarif = {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "CodeQL command-line toolchain"}},
                "results": [
                    {
                        "ruleId": "js/unused-local-variable",
                        "message": {"text": "unused"},
                        "locations": [
                            {"physicalLocation": {"artifactLocation": {"uri": "a.js"}}}
                        ],
                    },
                    {
                        "ruleId": "js/sql-injection",
                        "message": {"text": "sql injection"},
                        "locations": [
                            {"physicalLocation": {"artifactLocation": {"uri": "a.js"}}}
                        ],
                    },
                ],
            }
        ],
    }
    (tmp_path / DEFAULT_SARIF_FILENAME).write_text(json.dumps(sarif))
    adapter = CodeQLAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    assert len(normalized.entities) == 1
    entity = normalized.entities[0]
    assert entity.qualified_name == "a.js"
    assert set(entity.roles) == {
        "codeql:finding:js/unused-local-variable",
        "codeql:finding:js/sql-injection",
    }


def test_finding_role_preserves_rule_id_as_query_identity() -> None:
    adapter = adapter_for("valid-sarif.sarif")
    result = adapter.extract(make_repository(FIXTURES), adapter.supported_capabilities)
    normalized = adapter.normalize(result)
    all_roles = {r for e in normalized.entities for r in e.roles}
    assert "codeql:finding:js/unused-local-variable" in all_roles
    assert "codeql:finding:js/inconsistent-use-of-new" in all_roles


# ---------------------------------------------------------------------------
# External libraries (directive D6: preserve the established strategy, don't
# index external source just because CodeQL references it)
# ---------------------------------------------------------------------------


def test_codeql_adapter_never_creates_external_library_entities() -> None:
    # CodeQL SARIF results reference files in the analyzed workspace only;
    # this adapter has no external-symbol concept to (mis)handle, unlike
    # SCIP's package-qualified external references.
    for fixture in ["valid-sarif.sarif", "path-problem.sarif"]:
        adapter = adapter_for(fixture)
        result = adapter.extract(make_repository(FIXTURES), adapter.supported_capabilities)
        normalized = adapter.normalize(result)
        assert all(e.base_type is not BaseEntityType.EXTERNAL_LIBRARY for e in normalized.entities)


# ---------------------------------------------------------------------------
# Determinism / idempotency (directive D6)
# ---------------------------------------------------------------------------


def test_deterministic_repeated_extraction_same_ids() -> None:
    adapter = adapter_for("path-problem.sarif")
    repo = make_repository(FIXTURES)

    result1 = adapter.normalize(adapter.extract(repo, adapter.supported_capabilities))
    result2 = adapter.normalize(adapter.extract(repo, adapter.supported_capabilities))

    assert sorted(e.canonical_id for e in result1.entities) == sorted(
        e.canonical_id for e in result2.entities
    )
    assert sorted(e.evidence_id for e in result1.evidence) == sorted(
        e.evidence_id for e in result2.evidence
    )


def test_ingestion_pipeline_idempotent_across_two_runs() -> None:
    registry = CapabilityRegistry()
    registry.register(adapter_for("path-problem.sarif"), PROFILE)
    pipeline = IngestionPipeline(registry, InMemoryEvidenceStore())
    repo = make_repository(FIXTURES)

    result1 = pipeline.run(repo)
    result2 = pipeline.run(repo)

    assert result1.committed_providers == ["codeql"]
    assert result1.graph_version.version_id == result2.graph_version.version_id
    rels1 = len(result1.graph_store.get_relationships())
    rels2 = len(result2.graph_store.get_relationships())
    assert rels1 == rels2


# ---------------------------------------------------------------------------
# D4 integration, coexistence with SCIP/Git, contradictory evidence
# ---------------------------------------------------------------------------


def test_integration_through_ingestion_pipeline() -> None:
    registry = CapabilityRegistry()
    registry.register(adapter_for("path-problem.sarif"), PROFILE)
    pipeline = IngestionPipeline(registry, InMemoryEvidenceStore())

    result = pipeline.run(make_repository(FIXTURES))

    assert result.committed_providers == ["codeql"]
    assert len(result.graph_store.get_relationships()) == 1


def test_coexistence_with_scip_evidence_in_one_ingestion_run(tmp_path: Path) -> None:
    # Build a minimal SCIP index and a CodeQL SARIF file referencing an
    # overlapping file, register both adapters, and confirm one run commits
    # both providers' evidence without either discarding the other's.
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from scip_fixtures import document as scip_document
    from scip_fixtures import occurrence as scip_occurrence
    from scip_fixtures import scip_index
    from scip_fixtures import symbol_information as scip_symbol_information

    scip_bytes = scip_index(
        documents=(
            scip_document(
                "src/db.js",
                occurrences=(
                    scip_occurrence(
                        "scip-test npm pkg 1.0.0 src/`db.js`/Query#", roles=1, range_=(0, 0, 5)
                    ),
                ),
                symbols=(scip_symbol_information("scip-test npm pkg 1.0.0 src/`db.js`/Query#"),),
            ),
        )
    )
    (tmp_path / "index.scip").write_bytes(scip_bytes)
    (tmp_path / DEFAULT_SARIF_FILENAME).write_text(
        (FIXTURES / "path-problem.sarif").read_text()
    )

    registry = CapabilityRegistry()
    registry.register(SCIPAdapter(), PROFILE)
    registry.register(CodeQLAdapter(), PROFILE)
    pipeline = IngestionPipeline(registry, InMemoryEvidenceStore())

    result = pipeline.run(make_repository(tmp_path))

    assert set(result.committed_providers) == {"scip", "codeql"}
    assert result.failed_providers == []


def test_contradictory_evidence_between_git_and_codeql_both_preserved(tmp_path: Path) -> None:
    import git

    repo = git.Repo.init(tmp_path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Test")
        config.set_value("user", "email", "test@example.com")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "handler.js").write_text("// handler\n")
    (tmp_path / "src" / "db.js").write_text("// db\n")
    repo.git.add(A=True)
    repo.git.commit(m="initial")
    revision = repo.head.commit.hexsha

    (tmp_path / DEFAULT_SARIF_FILENAME).write_text(
        (FIXTURES / "path-problem.sarif").read_text()
    )

    registry = CapabilityRegistry()
    registry.register(GitAdapter(), PROFILE)
    registry.register(CodeQLAdapter(), PROFILE)
    pipeline = IngestionPipeline(registry, InMemoryEvidenceStore())

    result = pipeline.run(make_repository(tmp_path, revision))

    assert set(result.committed_providers) == {"git", "codeql"}
    # Both providers' evidence reaches the graph -- neither is dropped or
    # adjudicated as "more correct" (directive D6: preserve contradictory
    # evidence, no reconciliation logic inside the adapter). Evidence
    # Reconciliation (post-D7 directive Phase C) now computes a real
    # status/confidence from that preserved evidence -- no current
    # provider pair can assert a genuine contradiction (no negation
    # mechanism exists, see codex.reconciliation.reconciler), so the
    # relationship resolves to SUPPORTED/WEAKLY_SUPPORTED, never
    # perpetually UNRESOLVED as it did before Reconciliation existed.
    relationships = result.graph_store.get_relationships()
    assert len(relationships) >= 1
    assert any(
        rel.status in (EvidenceStatus.SUPPORTED, EvidenceStatus.WEAKLY_SUPPORTED)
        for rel in relationships
    )
    assert all(rel.contradicting_evidence_ids == [] for rel in relationships)
