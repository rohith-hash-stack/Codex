"""The CodeQL Adapter (HLRD Resource Map §62; TAD §8-9, §10; Phase D directive D6).

A clean-room `ProviderAdapter` (D1) for CodeQL's SARIF 2.1.0 output.
Consumes an already-generated SARIF file — this adapter never invokes
the `codeql` CLI, never generates a CodeQL database, and never reads
one. See "Integration mechanism and licensing" below for exactly why,
and `docs/resources.md`'s CodeQL row for the full research record.

Capability / evidence mapping (directive D6's required table),
determined *before* writing this file and validated against the
authoritative OASIS SARIF 2.1.0 schema plus real SARIF artifacts
produced by "CodeQL command-line toolchain" (`docs/resources.md`):

CodeQL concept -> artifact -> Codex Capability -> Entity -> RelationshipType -> Evidence -> Class:

- A `result` whose `codeFlows` contains a thread flow with >= 2 distinct
  locations (a genuine path-problem/data-flow result) -> capability
  `DATA_FLOW` -> two FILE entities (source location, sink location) ->
  `REFERENCES` -> `Evidence(subject=source FILE, object=sink FILE,
  confidence=<policy mapping of result.level>, raw_reference=SARIF
  file + run + result index)` -> **Class B (deterministically derived)**:
  the codeFlow's own first/last locations are an explicit, ordered fact
  CodeQL states; taking its two endpoints is a mechanical operation on
  that fact, not an interpretation.
- A `result` with no `codeFlows` (an ordinary "problem"-kind finding,
  the majority of real CodeQL output, e.g. `js/unused-local-variable`)
  -> capability `DATA_FLOW` -> one FILE entity at the result's primary
  location, with a role `codeql:finding:<ruleId>` appended -> no
  relationship, no `Evidence` record (Codex's `Evidence` model requires
  two distinct endpoints; a single-location finding has only one, and
  no `BaseEntityType` represents a bare "finding"/"annotation" concept
  — see "Why plain findings are role-only, not a new entity/ontology
  concept" below) -> **Class A (direct)**: the finding itself is
  CodeQL's own explicit assertion, represented without embellishment.
- Per-step data-flow semantics (e.g. "this step reads X", "this step is
  the taint source") -> **NOT IMPLEMENTED**. `threadFlowLocation.kinds`
  is the only SARIF field that could carry this, and it is optional,
  freeform, and was not populated in any real fixture inspected for
  this adapter (`docs/resources.md`). Assigning `READS`/`WRITES`/
  `DEPENDS_ON` to individual steps would require interpreting a field
  with no reliable content — **Class C (unsupported)**, not attempted.
- `CALLS`, `EXTENDS`, `IMPLEMENTS`, `DEPENDS_ON` inferred from any
  CodeQL result for any reason -> **NOT IMPLEMENTED**. No SARIF field
  asserts any of these; producing them would be exactly the "a query
  could conceptually relate to X" fabrication directive D6 prohibits
  — **Class C (unsupported)**.
- Raw CodeQL databases -> **NOT CONSUMED AT ALL**. This adapter reads
  only the SARIF results format; see "Integration mechanism" below.

Integration mechanism and licensing (directive D6 §1-8, "STOP if
licensing creates a real architectural decision")
----------------------------------------------------------------------
This *did* surface a real, load-bearing licensing fact, resolved here
rather than escalated, because the resolution is the same "consume a
pre-generated artifact, never invoke the underlying tool" pattern
already established and approved in D5 (`SCIPAdapter`) — applying a
precedented, low-risk pattern to a second provider is not a new
architectural decision requiring a STOP.

The GitHub CodeQL Terms and Conditions (confirmed by fetching
`codeql-cli-binaries`'s `LICENSE.md` directly — this is *not* an
OSI-approved open source license) restrict free use of the CodeQL
CLI/engine to: academic research, demonstrating the software, testing
OSI-licensed queries, and analysis of an "Open Source Codebase" — with
automated database generation (CI/CD) further restricted to codebases
"hosted and maintained on GitHub.com" specifically, unless the user
holds a paid GitHub Advanced Security (GHAS) license. Using the
Software against a non-open-source (e.g. private) codebase without
GHAS, or redistributing/hosting the CLI for others, is explicitly
prohibited.

Given that, this adapter **only consumes pre-generated SARIF results**
— exactly like `GitAdapter` reads an existing git repository and
`SCIPAdapter` reads an existing `.scip` index, neither of which
generates anything. `CodeQLAdapter` never invokes `codeql`, never
generates a database, and is therefore never itself "using the
Software" under those Terms. Whoever produced the SARIF file (the
repository owner's own CI, a GHAS subscription, a manual run
permitted by the Terms) is the one bound by them — not Codex.

SARIF 2.1.0 itself is a separate, OASIS open standard (RF-on-RAND
terms) — independently implementing a parser against the published
schema is unrestricted, the same posture as SCIP's Apache-2.0 schema
in D5. This adapter's SARIF reader is dependency-free (stdlib `json`
only) and was never generated from or copied out of any CodeQL/SARIF
reference implementation.

**Consequence for eligibility/availability**: because Codex never
invokes CodeQL, there is no GHAS/private-repo licensing check for
Codex's own code to perform — that restriction governs artifact
*production*, which happens entirely outside Codex. `check_eligibility()`
reduces to "does a SARIF artifact exist at the configured path",
identical in shape to `SCIPAdapter`'s eligibility check. A commercial
licensing restriction is therefore never represented as a Codex
provider *failure* — it simply never becomes Codex's concern.

Why plain findings are role-only, not a new entity/ontology concept
----------------------------------------------------------------------
HLRD's `BaseEntityType` is a deliberately closed set (TAD §13: roles
carry finer distinctions "without exploding the ontology into one base
type per concept"); introducing a new base type for "a static-analysis
finding" would be a genuine ontology change outside D6's scope, and
Codex's `Evidence` model requires two distinct endpoints (`subject`,
`object`), which a single-location finding does not have. Rather than
fabricating a self-referential or invented relationship to force one
through, this adapter follows the same precedent as D3's `GitAdapter.
HISTORY` and D5's `SCIPAdapter.SYMBOL_DEFINITION`: represent the unary
fact as an entity annotation (here, a role on the file entity) and
nothing more. This is recorded as a real, documented scope limitation,
not a silent gap: exact per-finding detail (message, severity, exact
line) is not queryable from the canonical graph in V1 — only "this
file has a CodeQL finding for rule X" survives as a role. Full detail
remains reachable via `raw_reference` were the adapter to set one on a
richer evidence type, but no such evidence record exists for a
role-only finding; this is deliberately not worked around.

Provenance
----------
Every path-problem `Evidence` record's `raw_reference` points at
`artifact://sarif/<run index>/<result index>` (resolvable later via
the Artifact Store, TAD §52); `provider_version` reports
`<tool.driver.name>@<version or semanticVersion>` read from the SARIF
file's own metadata (mirroring `SCIPAdapter`'s "unknown" fallback
before any extraction). `ruleId` is preserved as the closest available
proxy for query identity — SARIF's `reportingDescriptor` (the `rule`
type) has no per-rule version field of its own; only the whole tool
driver is versioned, so "query version" in practice means the
CodeQL toolchain/query-pack version as a whole, not a finer-grained
concept SARIF itself does not offer.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from codex.evidence.model import CoverageStatus, Evidence, EvidenceCohort
from codex.ontology.entities import BaseEntityType, RepositorySymbol, build_canonical_id
from codex.ontology.relationships import RelationshipType
from codex.provider.capability import Capability
from codex.provider.contract import (
    EligibilityStatus,
    ExtractionResult,
    NormalizedEvidence,
    ProviderEligibility,
    ProviderExtractionError,
    ProviderFailureReason,
    ProviderHealthStatus,
    ValidationResult,
)
from codex.repository.models import RepositoryMetadata

DEFAULT_SARIF_FILENAME: Final = "results.sarif"
"""A reasonable, documented convention (not a verified upstream default the way
D5's ``index.scip`` was for `scip-typescript` -- SARIF output filenames are
caller-chosen in most CodeQL/CI setups). Configurable via the constructor."""

_LEVEL_CONFIDENCE: Final[dict[str, float]] = {
    "error": 1.0,
    "warning": 0.7,
    "note": 0.4,
    "none": 0.1,
}
"""Explicit policy mapping from SARIF's `result.level` (or, absent that, the
rule's `defaultConfiguration.level`) to `Evidence.confidence` -- a calibration
point (like D3's co-change saturation constant), not a claimed-universal
formula. SARIF severity is not a probability (docs/research/provider-formats.md)."""

_DEFAULT_LEVEL: Final = "warning"
"""SARIF's own schema default when neither `result.level` nor
`rule.defaultConfiguration.level` is present."""


class SarifFormatError(ValueError):
    """Raised for structurally invalid SARIF content this adapter cannot process."""


@dataclass(frozen=True)
class _ResolvedLocation:
    uri: str
    start_line: int | None


@dataclass(frozen=True)
class _DataFlowFact:
    run_index: int
    result_index: int
    rule_id: str
    level: str
    source: _ResolvedLocation
    sink: _ResolvedLocation


@dataclass(frozen=True)
class _FindingFact:
    run_index: int
    result_index: int
    rule_id: str
    location: _ResolvedLocation


def _looks_like_uri_reference(value: str) -> bool:
    """A lightweight, deliberately non-exhaustive check that ``value`` could
    be a valid URI-reference (SARIF's schema declares `artifactLocation.uri`
    as ``format: "uri-reference"`` -- confirmed against the authoritative
    schema). This is not a full RFC 3986 parser; it only rejects the clearest
    sign of non-conformance (literal whitespace, which a real URI-reference
    must percent-encode) so untrusted SARIF content can't inject an
    obviously-garbage string into the canonical graph as a file path
    (directive D6 security requirement — confirmed against a real malformed
    fixture, `with-invalid-uri.sarif`, see docs/resources.md)."""
    return bool(value) and not any(ch.isspace() for ch in value)


def _resolve_artifact_uri(
    artifact_location: dict[str, Any] | None, artifacts: list[dict[str, Any]]
) -> str | None:
    """Resolve a SARIF `artifactLocation` to a URI string.

    A location may give `uri` directly, or only an `index` into the
    run's own `artifacts[]` list (confirmed in a real fixture,
    `fingerprinting.input.sarif` -- see docs/resources.md). Returns
    ``None`` rather than fabricating a path if neither resolves, or if
    the given `uri` doesn't even look like a URI-reference.
    """
    if not artifact_location:
        return None
    uri = artifact_location.get("uri")
    if isinstance(uri, str) and _looks_like_uri_reference(uri):
        return uri
    index = artifact_location.get("index")
    if isinstance(index, int) and 0 <= index < len(artifacts):
        nested = artifacts[index].get("location")
        if isinstance(nested, dict):
            nested_uri = nested.get("uri")
            if isinstance(nested_uri, str) and _looks_like_uri_reference(nested_uri):
                return nested_uri
    return None


def _resolve_location(
    location: dict[str, Any], artifacts: list[dict[str, Any]]
) -> _ResolvedLocation | None:
    physical = location.get("physicalLocation")
    if not isinstance(physical, dict):
        return None
    uri = _resolve_artifact_uri(physical.get("artifactLocation"), artifacts)
    if uri is None:
        return None
    region = physical.get("region")
    start_line = None
    if isinstance(region, dict):
        line = region.get("startLine")
        if isinstance(line, int):
            start_line = line
    return _ResolvedLocation(uri=uri, start_line=start_line)


def _result_level(result: dict[str, Any], rules_by_id: dict[str, dict[str, Any]]) -> str:
    level = result.get("level")
    if isinstance(level, str) and level in _LEVEL_CONFIDENCE:
        return level
    rule_id = result.get("ruleId")
    rule = rules_by_id.get(rule_id) if isinstance(rule_id, str) else None
    if rule is not None:
        default_config = rule.get("defaultConfiguration")
        if isinstance(default_config, dict):
            rule_level = default_config.get("level")
            if isinstance(rule_level, str) and rule_level in _LEVEL_CONFIDENCE:
                return rule_level
    return _DEFAULT_LEVEL


def _flow_endpoints(
    result: dict[str, Any], artifacts: list[dict[str, Any]]
) -> tuple[_ResolvedLocation, _ResolvedLocation] | None:
    """The first and last resolvable locations of a result's first thread
    flow, if it represents a genuine (>= 2 distinct locations) path -- the
    explicit signal this adapter uses to distinguish a path-problem result
    from a plain one, since SARIF carries no separate "kind: path-problem"
    marker on the result itself (directive D6 semantic-fidelity requirement)."""
    code_flows = result.get("codeFlows")
    if not isinstance(code_flows, list) or not code_flows:
        return None
    thread_flows = code_flows[0].get("threadFlows")
    if not isinstance(thread_flows, list) or not thread_flows:
        return None
    locations = thread_flows[0].get("locations")
    if not isinstance(locations, list) or len(locations) < 2:
        return None

    resolved: list[_ResolvedLocation] = []
    for entry in locations:
        loc = entry.get("location") if isinstance(entry, dict) else None
        if isinstance(loc, dict):
            r = _resolve_location(loc, artifacts)
            if r is not None:
                resolved.append(r)
    if len(resolved) < 2:
        return None
    source, sink = resolved[0], resolved[-1]
    if source.uri == sink.uri and source.start_line == sink.start_line:
        return None
    return source, sink


def _process_run(
    run_index: int, run: dict[str, Any]
) -> tuple[list[_DataFlowFact], list[_FindingFact]]:
    tool = run.get("tool")
    driver = tool.get("driver") if isinstance(tool, dict) else None
    rules = driver.get("rules") if isinstance(driver, dict) else None
    rules_by_id: dict[str, dict[str, Any]] = {}
    for rule in rules if isinstance(rules, list) else []:
        if isinstance(rule, dict) and isinstance(rule.get("id"), str):
            rules_by_id[rule["id"]] = rule
    artifacts_raw = run.get("artifacts")
    artifacts: list[dict[str, Any]] = []
    if isinstance(artifacts_raw, list):
        artifacts = [a for a in artifacts_raw if isinstance(a, dict)]

    if "results" not in run:
        # Per the authoritative SARIF schema: "The results array can be
        # omitted when a run is solely exporting rules metadata" -- a
        # legitimate, non-malformed state, not an error (docs/resources.md).
        results: list[Any] = []
    else:
        results = run["results"]
        if not isinstance(results, list):
            raise SarifFormatError(f"run {run_index}: `results` must be an array")

    data_flow_facts: list[_DataFlowFact] = []
    finding_facts: list[_FindingFact] = []

    for result_index, result in enumerate(results):
        if not isinstance(result, dict):
            continue
        rule_id = result.get("ruleId")
        if not isinstance(rule_id, str):
            continue
        level = _result_level(result, rules_by_id)

        endpoints = _flow_endpoints(result, artifacts)
        if endpoints is not None:
            source, sink = endpoints
            data_flow_facts.append(
                _DataFlowFact(run_index, result_index, rule_id, level, source, sink)
            )
            continue

        locations = result.get("locations")
        if isinstance(locations, list) and locations and isinstance(locations[0], dict):
            resolved = _resolve_location(locations[0], artifacts)
            if resolved is not None:
                finding_facts.append(_FindingFact(run_index, result_index, rule_id, resolved))

    return data_flow_facts, finding_facts


class CodeQLAdapter:
    """``ProviderAdapter`` for CodeQL SARIF results (HLRD Resource Map; directive D6)."""

    def __init__(self, *, sarif_filename: str = DEFAULT_SARIF_FILENAME) -> None:
        self._sarif_filename = sarif_filename
        self._freshness: datetime | None = None
        self._last_tool_version: str | None = None

    @property
    def provider_name(self) -> str:
        return "codeql"

    @property
    def provider_version(self) -> str:
        """The producing tool's own name+version, read from the *last
        successfully decoded* SARIF file's first run. Before any extraction,
        or if unavailable, reports ``"unknown"`` (mirrors `SCIPAdapter`, D5)."""
        return self._last_tool_version or "unknown"

    @property
    def supported_capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.DATA_FLOW})

    @property
    def health_status(self) -> ProviderHealthStatus:
        # No external executable/service dependency -- this adapter reads a
        # file the caller points it at (same posture as SCIPAdapter, D5).
        return ProviderHealthStatus.HEALTHY

    def availability(self, capability: Capability, repository: RepositoryMetadata) -> float:
        if capability not in self.supported_capabilities:
            return 0.0
        return 1.0 if self.check_eligibility(repository).eligible else 0.0

    @property
    def freshness(self) -> datetime | None:
        return self._freshness

    def validate(self) -> ValidationResult:
        return ValidationResult(ok=True)

    def _sarif_path(self, repository: RepositoryMetadata) -> Path:
        return Path(repository.local_path) / self._sarif_filename

    def check_eligibility(self, repository: RepositoryMetadata) -> ProviderEligibility:
        path = self._sarif_path(repository)
        if not path.is_file():
            return ProviderEligibility(
                status=EligibilityStatus.INELIGIBLE_REPOSITORY,
                reason=f"no SARIF results found at {path}",
            )
        return ProviderEligibility(status=EligibilityStatus.ELIGIBLE)

    def extract(
        self, repository: RepositoryMetadata, capabilities: Collection[Capability]
    ) -> ExtractionResult:
        requested = frozenset(capabilities) & self.supported_capabilities
        path = self._sarif_path(repository)

        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ProviderExtractionError(
                self.provider_name, ProviderFailureReason.UNAVAILABLE, f"cannot read {path}: {exc}"
            ) from exc

        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderExtractionError(
                self.provider_name,
                ProviderFailureReason.UNAVAILABLE,
                f"malformed SARIF JSON at {path}: {exc}",
            ) from exc

        if not isinstance(document, dict) or not isinstance(document.get("runs"), list):
            raise ProviderExtractionError(
                self.provider_name,
                ProviderFailureReason.UNAVAILABLE,
                f"malformed SARIF document at {path}: missing/invalid top-level `runs`",
            )

        runs: list[dict[str, Any]] = [r for r in document["runs"] if isinstance(r, dict)]
        first_driver = None
        if runs:
            tool = runs[0].get("tool")
            if isinstance(tool, dict):
                first_driver = tool.get("driver")
        if isinstance(first_driver, dict) and isinstance(first_driver.get("name"), str):
            name = first_driver["name"]
            version = first_driver.get("version") or first_driver.get("semanticVersion")
            self._last_tool_version = f"{name}@{version}" if version else name or None

        data_flow_facts: list[_DataFlowFact] = []
        finding_facts: list[_FindingFact] = []
        runs_succeeded = 0
        runs_failed = 0

        if Capability.DATA_FLOW in requested:
            for run_index, run in enumerate(runs):
                try:
                    flows, findings = _process_run(run_index, run)
                except Exception:  # noqa: BLE001 - isolate one run's bug, directive D6
                    runs_failed += 1
                    continue
                data_flow_facts.extend(flows)
                finding_facts.extend(findings)
                runs_succeeded += 1

        successful: list[str] = []
        failed: list[str] = []
        partial: list[str] = []
        if Capability.DATA_FLOW in requested:
            if runs_succeeded > 0 and runs_failed > 0:
                # Some runs processed, some didn't -- a genuine partial
                # result, not a clean success (directive D6: distinguish
                # "analysis completed with complete scope" from "partial").
                partial.append(Capability.DATA_FLOW.value)
            elif runs_succeeded > 0:
                successful.append(Capability.DATA_FLOW.value)
            elif runs_failed > 0:
                failed.append(Capability.DATA_FLOW.value)
            else:
                # No runs at all in the document -- a valid, empty SARIF file
                # (confirmed against a real fixture, docs/resources.md) is a
                # successful run that legitimately found nothing.
                successful.append(Capability.DATA_FLOW.value)

        if not successful and not failed and not partial:
            coverage = CoverageStatus.NONE
        elif failed or partial:
            coverage = CoverageStatus.PARTIAL
        else:
            coverage = CoverageStatus.FULL

        cohort = EvidenceCohort(
            provider=self.provider_name,
            provider_version=self.provider_version,
            snapshot_id=repository.head_revision,
            source_revision=repository.head_revision,
            successful_capabilities=successful,
            failed_capabilities=failed,
            partial_capabilities=partial,
            coverage_status=coverage,
        )
        self._freshness = cohort.observed_at

        payload = {
            "repository_id": repository.repository_id,
            "revision": repository.head_revision,
            "data_flow_facts": data_flow_facts,
            "finding_facts": finding_facts,
        }
        return ExtractionResult(cohort=cohort, raw_reference=None, raw_payload=payload)

    def normalize(self, result: ExtractionResult) -> NormalizedEvidence:
        payload = result.raw_payload
        repository_id: str = payload["repository_id"]
        revision: str = payload["revision"]
        data_flow_facts: list[_DataFlowFact] = payload["data_flow_facts"]
        finding_facts: list[_FindingFact] = payload["finding_facts"]

        entities: dict[str, RepositorySymbol] = {}
        evidence: list[Evidence] = []

        def file_entity_id(uri: str) -> str:
            return build_canonical_id(
                repository_id=repository_id,
                repository_revision=revision,
                qualified_name=uri,
                base_type=BaseEntityType.FILE,
            )

        def ensure_file_entity(uri: str, *, role: str | None = None) -> str:
            canonical_id = file_entity_id(uri)
            existing = entities.get(canonical_id)
            if existing is None:
                entities[canonical_id] = RepositorySymbol(
                    canonical_id=canonical_id,
                    repository_id=repository_id,
                    repository_revision=revision,
                    name=uri.rsplit("/", maxsplit=1)[-1] or uri,
                    qualified_name=uri,
                    base_type=BaseEntityType.FILE,
                    roles=[role] if role else [],
                )
            elif role and role not in existing.roles:
                entities[canonical_id] = existing.model_copy(
                    update={"roles": [*existing.roles, role]}
                )
            return canonical_id

        for finding in finding_facts:
            ensure_file_entity(finding.location.uri, role=f"codeql:finding:{finding.rule_id}")

        sorted_flows = sorted(
            data_flow_facts,
            key=lambda f: (f.rule_id, f.source.uri, f.sink.uri, f.run_index, f.result_index),
        )
        for i, flow in enumerate(sorted_flows):
            source_id = ensure_file_entity(flow.source.uri)
            sink_id = ensure_file_entity(flow.sink.uri)
            evidence.append(
                Evidence(
                    evidence_id=f"codeql:{revision}:dataflow:{i}",
                    provider=result.cohort.provider,
                    provider_version=result.cohort.provider_version,
                    snapshot_id=result.cohort.snapshot_id,
                    source_revision=revision,
                    subject=source_id,
                    predicate=RelationshipType.REFERENCES,
                    object=sink_id,
                    confidence=_LEVEL_CONFIDENCE[flow.level],
                    freshness=result.cohort.observed_at,
                    raw_reference=f"artifact://sarif/{flow.run_index}/{flow.result_index}",
                )
            )

        return NormalizedEvidence(
            entities=list(entities.values()), evidence=evidence, cohort=result.cohort
        )
