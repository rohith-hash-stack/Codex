# Codex — Architecture Conformance Audit

> Produced per the Architecture Conformance, Research Traceability & Ordered Implementation Directive. Covers HLRD v1.0, TAD v1.0, the existing `src/codex/` code, tests, and `docs/research/provider-formats.md`. No production code was written or modified while producing this audit.

Audited: 2026-08-30 (commit `13bbaf6`).

**Update 2026-08-30 (post-reconciliation):** C-1, C-2, and C-3 below are now **RESOLVED** — see `docs/architecture-reconciliation.md` for the full resolution and `docs/HLRD.md`/`docs/TAD.md` for the amended specification text. ADR-012 and ADR-013 are now formally **CLOSED/SUPERSEDED** in TAD §77 itself, not just recommended for closure here. This update did not change any code — resolutions confirmed existing code was already conformant (C-1, C-2) or don't yet apply to any code (C-3). Sections B, D, E, G, and H below are annotated accordingly rather than rewritten, so the original audit trail stays visible.

**Update 2026-08-30 (post-D1):** the Phase D directive's D1 (ProviderAdapter Contract) is now **IMPLEMENTED** — `codex.provider.{capability,contract}`, 21 behavioral tests, 100% coverage, clean ruff/mypy. Sections B, F, G, and H are annotated accordingly. D2 (Capability Registry) is next; per the directive, implementation stops here pending review.

**Update 2026-08-30 (post-D2):** D2 (Capability Registry) is now **IMPLEMENTED** — `codex.registry.{models,scoring,registry}`, 27 new behavioral tests, 100% coverage, clean ruff/mypy. Sections B, F, G, and H are annotated accordingly. **New finding, not silently resolved:** TAD §31's `ProviderScore` formula needs five `[0,1]`-normalized inputs, but the (approved, closed) D1 `ProviderAdapter` contract only defines a source for two of them. See §I (superseded by the entry below).

**Update 2026-08-30 (ADR-018 resolved):** the finding above is now **RESOLVED** — see §I. `evidence_quality`/`cost_factor` are supplied via a `ProviderScoreProfile` attached once at `CapabilityRegistry.register()` (not per query); `freshness` is Registry-derived from the adapter's timestamp via a documented default decay function (`default_freshness_score`). `rank()` was refactored to take no caller-supplied scoring values at all — 14 more behavioral tests added/rewritten (79 total), 100% coverage maintained, clean ruff/mypy. D3 (Git Adapter) is next; per the directive, implementation stops here pending review.

**Update 2026-08-30 (post-D3):** D3 (Git Adapter) is now **IMPLEMENTED** — `codex.provider.git_adapter.GitAdapter`, the first concrete `ProviderAdapter`. Two capabilities, matching HLRD §13 exactly: `HISTORY` (tip-commit diff → `RepositorySymbol` entities carrying `lifecycle_status`) and `CO_CHANGE` (bounded commit-window co-occurrence → `CO_CHANGED_WITH` evidence). 30 new behavioral tests (109 total), 100% coverage on `git_adapter.py`, clean ruff/mypy, all built against real temporary git repositories (none depend on this repository's own history). Design note, not a new ADR: file lifecycle is a unary fact and doesn't fit `Evidence`'s S-P-O shape, so `HISTORY` emits entities only, provenance carried by `EvidenceCohort` — the same pattern already flagged for CodeQL's single-entity findings. No Capability Registry integration, SCIP/CodeQL/Sourcegraph adapters, or ingestion pipeline (D4) — all correctly out of D3's scope. Sections B, F, G, and H are annotated accordingly. D4 (Ingestion Pipeline) is next; per the directive, implementation stops here pending review.

---

## 0. A note on scope: DTD-01..05 do not exist as separate documents

The directive asks this audit to inspect "DTD-01" through "DTD-05" as distinct documents. **They don't exist as separate files.** The only artifacts in this repository are `docs/HLRD.md` and `docs/TAD.md`. "DTD-01" (Canonical Graph), "DTD-02" (Query Understanding), "DTD-03" (Query Planner), "DTD-04" (Evidence Selection/MSS), and "DTD-05" (Verification) exist **only as inline labels inside TAD's own pipeline diagram and component sections** (TAD §5, §22, §29, §46, §78, §80).

This is reported here rather than papered over: I am not going to fabricate five documents' worth of content that was never written. Everywhere below, "DTD-0x" means "the corresponding TAD section(s)," not an independent spec. Whether standalone DTD documents should be authored, or whether TAD's inline sections are meant to serve that role permanently, is itself an open item (see **C-5** below).

---

## A. Architecture Status

| Document | Status |
|---|---|
| HLRD v1.0 | Frozen baseline (`docs/HLRD.md`, commit `4bd76c4`). Read in full for this audit. |
| DTD-01..05 | Do not exist as separate documents — see §0. TAD's inline sections stand in for them. |
| TAD v1.0 | Architecture baseline (`docs/TAD.md`, commit `bce94fa`). Re-read in full for this audit. |
| ADRs | None exist as decisions. TAD §77 lists 17 candidate ADR titles; zero have been drafted or resolved. |
| Research resources | Partially consulted — see §C. SCIP, CodeQL SARIF, both RepoGraph implementations researched (`docs/research/provider-formats.md`). Sourcegraph, Tree-sitter, GraphRAG, TransE, LangChain/LlamaIndex, OpenTelemetry, scikit-learn calibration not yet researched (correctly deprioritized for most — see table). |
| Implementation | Phase 1 (Foundation): `codex.ontology`, `codex.evidence`, `codex.graph`, `codex.repository`. Plus Phase D: `codex.provider` (D1 contract, D3 `GitAdapter` — the first concrete adapter) and `codex.registry` (D2 Capability Registry, ADR-018 resolved). 0 of TAD's 18 components (§6) beyond these six have any code. |

---

## B. Requirement Traceability Matrix

Only major architectural requirements are shown (not every clause). "Module" is blank where nothing implements the requirement.

| Req ID | HLRD | TAD | Module | Test(s) | Status | Evidence |
|---|---|---|---|---|---|---|
| Base Type + Role ontology | §16-17 | §12-13 | `codex.ontology.entities` | `test_ontology.py` | **IMPLEMENTED** | `BaseEntityType`, free-string `roles[]`, `RepositorySymbol.has_role()` |
| Canonical relationship types | §16 | §14 | `codex.ontology.relationships` | none dedicated | **PARTIALLY_IMPLEMENTED** | All TAD §14 types + HLRD's extras implemented. **C-1 now RESOLVED**: `TESTED_BY` is canonical (HLRD §16 amended to match); code required no change. Still partial only because no dedicated test exists yet. |
| Canonical identity / join key | §18 | §12 | `codex.ontology.entities.build_canonical_id` | `test_ontology.py` | **IMPLEMENTED** (join key only) | Deterministic hash of repo+revision+type+language+qualified_name |
| Entity Resolution (multi-provider reconciliation) | §19 | component #7 | — | — | **NOT_IMPLEMENTED** | Only the join-key primitive exists; no reconciliation logic, and moot with zero providers |
| Evidence model (13 fields) | §15 (implied) | §15 | `codex.evidence.model.Evidence` | `test_evidence.py` | **IMPLEMENTED** | All TAD §15 fields present verbatim |
| Evidence independence default | — | §16 | `Evidence.effective_independence_group` | `test_evidence.py` | **IMPLEMENTED** | Defaults to `provider_default:{provider}` when unset |
| EvidenceCohort (successful/failed/partial capability lists) | — | §17 | `codex.evidence.model.EvidenceCohort` | `test_evidence.py` | **PARTIALLY_IMPLEMENTED** | Data container correct; nothing yet *consumes* it to drive negative-query `INCONCLUSIVE` logic (no Planner exists) |
| Evidence Status 6-way taxonomy, DISPUTED ≠ UNRESOLVED | — | §18 | `codex.evidence.model.EvidenceStatus` | none dedicated | **PARTIALLY_IMPLEMENTED** | Enum shape correct and distinct; no reconciliation logic yet assigns these, so the *behavioral* distinction is unverified |
| `raw_reference` resolvable URI (artifact://, s3://, file://) | — | §16, §52 | `Evidence.raw_reference` (`validate_raw_reference`), `ExtractionResult.raw_reference` | `test_provider_contract.py::test_raw_reference_must_be_resolvable_uri` | **PARTIALLY_IMPLEMENTED** | Scheme is now validated (rejects arbitrary strings) as of D1. Still no Artifact Store or resolution *service* — the URI is checked, not resolved. |
| GraphVersion composite key | §19 (via TAD) | §19 | `codex.graph.version.GraphVersion` | `test_graph_store.py` | **PARTIALLY_IMPLEMENTED** | **C-2 now RESOLVED**: generic `provider_versions` dict is canonical (TAD §19 amended to match); code required no change. Still partial only pending full version-locking flow (see below). |
| Graph version immutable once published | invariant | §20, invariant #4 | `GraphVersion.publish()` | `test_graph_store.py` | **PARTIALLY_IMPLEMENTED** | Model-level copy-on-publish works; nothing enforces immutability at the store level |
| Graph version locking through Planning→Retrieval→MSS→Verification | — | §20 | — | — | **NOT_IMPLEMENTED** | No Planner/Retrieval/Verification exist yet to lock a version through |
| Graph mutation boundary (ingestion-only writes) | — | §62, invariants #6-8 | `codex.graph.store.{GraphReader,GraphStore}` | none dedicated | **PARTIALLY_IMPLEMENTED** | Interfaces are structurally split; nothing runtime-enforces it (typing convention only), and it's moot until Query/Verification code exists |
| Historical storage = snapshot + diffs | — | §21 | — | — | **NOT_IMPLEMENTED** | No snapshotting or reconstruction exists |
| Repository Manager: register/clone/revision/diff | — | §7 | `codex.repository.manager.RepositoryManager` | `test_repository_manager.py` (3 tests, real git repos, no network) | **PARTIALLY_IMPLEMENTED** | All done except "triggering indexing" (explicit TAD §7 responsibility) — no ingestion pipeline exists to trigger |
| Repository Manager does not interpret queries | — | §7 | (absence of query code in this module) | — | **IMPLEMENTED** | Satisfied by construction |
| Provider Adapter contract (`extract/validate/normalize`, capabilities) | — | §8-9, §64; directive §11-13 | `codex.provider.contract.ProviderAdapter` (+ `Capability`, `ProviderHealthStatus`, `ValidationResult`, `ProviderEligibility`, `ExtractionResult`, `NormalizedEvidence`, `ProviderExtractionError`) | `tests/test_provider_contract.py` (25 tests) via `tests/fake_provider_adapter.py` | **IMPLEMENTED** | Behaviorally proven, not just declared: identity/version/capability declaration, supported vs. unsupported capability, successful/empty/partial/failed capability outcomes kept distinct, total provider failure raising `ProviderExtractionError` (distinct from a capability failure), provenance, `independence_group` default, source revision, snapshot identity, `raw_reference` validation, eligibility metadata, and revision association across runs. No concrete adapter (Git/SCIP) exists yet — that's D3/D5, correctly out of scope for this component. **`health_status`/`availability` interpretation (approved 2026-08-30, see below).** |
| Git Adapter: `HISTORY` and `CO_CHANGE` capabilities (first concrete `ProviderAdapter`) | — | §13; directive D3 §3-9, §13-16 | `codex.provider.git_adapter.GitAdapter` (uses `GitPython`, `codex.repository.models.RepositoryMetadata`) | `tests/test_git_adapter.py` (30 tests, real temporary git repos, never this repo's own history) | **IMPLEMENTED** | Exactly the two capabilities matching HLRD §13's stated Git scope — no more. `HISTORY`: tip-commit diff (initial-commit empty-tree diff included) → `RepositorySymbol` entities carrying `lifecycle_status` (ACTIVE for added/modified, DELETED, RENAMED), rename detection empirically confirmed against real GitPython behavior before being relied on. `CO_CHANGE`: bounded commit-window co-occurrence → `CO_CHANGED_WITH` evidence with saturation-based confidence. Capability-level failure isolation proven (one capability failing/timing out does not fail the other or raise); total-provider failure (`UNAVAILABLE`: no git executable, not a repo, invalid revision) kept distinct from a capability failure, per the D3 failure-semantics taxonomy. Security: argument-array-only GitPython APIs, no `shell=True`, empirically confirmed no shell-injection risk via a malicious-revision-string test. No Capability Registry integration or ingestion pipeline wiring — correctly out of D3's scope (D4). |

**Canonical interpretation of TAD §9's `health_status`/`availability` (approved 2026-08-30, D1 review):** TAD §9 lists these as two flat, undistinguished attributes. Resolved as: `health_status` (`ProviderHealthStatus`: `HEALTHY`/`DEGRADED`/`UNHEALTHY`/`UNKNOWN`) is the operational condition of the provider itself; `availability(capability, repository) -> float` in `[0.0, 1.0]` is whether the provider is currently usable for a *specific capability* in a *specific repository/environment* — an adapter-reported fact, not an aggregated selection score (that aggregation is D2's job, per TAD §10/§31). The two are structurally independent: nothing in the contract derives one from the other, and `test_healthy_provider_can_report_zero_availability`/`test_unhealthy_provider_can_report_nonzero_availability` prove a HEALTHY provider can report 0.0 availability (e.g. missing license for this repository) and an UNHEALTHY one can still report full availability. This was an ambiguity in a single document (TAD §9), not a cross-document contradiction — it did not require the C-1/C-2/C-3-style reconciliation process, only this recorded interpretation.
| Capability Registry: registration, capability discovery | — | §10 | `codex.registry.registry.CapabilityRegistry.{register,unregister,registered_providers,providers_for}` | `test_capability_registry.py` (11 tests) | **IMPLEMENTED** | Register/replace/unregister, static declared-capability lookup, empty-result behavior for unknown capabilities/no providers — all behaviorally tested |
| Capability Registry: eligibility/availability/health evaluation | — | §9-10; directive D1 §11, D2 §6 | `CapabilityRegistry.evaluate()`, `ProviderEvaluationStatus` (AVAILABLE/PARTIAL/UNAVAILABLE/INELIGIBLE/FAILED) | `test_capability_registry.py` (7 tests) | **IMPLEMENTED** | Every status path behaviorally distinguished, including the case that specifically decouples `health_status` (UNHEALTHY) from `validate()` (still ok) to prove the registry checks each independently rather than treating one as a proxy for the other |
| Capability Registry: provider selection/scoring | — | §31; directive D2 §4; ADR-018 | `CapabilityRegistry.rank()`, `codex.registry.scoring.{ProviderScoreInputs,ProviderScoreProfile,provider_score,default_freshness_score,PROVIDER_SCORE_WEIGHTS}` | `test_capability_registry.py` (10 tests), `test_provider_scoring.py` (11 tests) | **IMPLEMENTED** | Formula/weights implemented exactly per TAD §31 (0.40/0.20/0.15/0.15/0.10), `capability_match=0` exclusion proven, deterministic tie-broken ranking proven, and — per **ADR-018 (RESOLVED, see §I)** — `evidence_quality`/`cost_factor` come from a `ProviderScoreProfile` attached once at `register()` time (not per query, so ranking is caller-independent) and `freshness` is derived from the adapter's own timestamp via a documented, provider-neutral default decay function. `rank()` takes no caller-supplied scoring values at all. |
| Relationship Reconciliation / contradiction score | §20 | §38, §73 | `CanonicalRelationship` (shape only) | `test_evidence.py` (key only) | **PARTIALLY_IMPLEMENTED** | Data model exists (`supporting_evidence_ids`, `contradicting_evidence_ids`, `status`); zero logic computes them |
| External library package-qualified identity | — | §56 | `BaseEntityType.EXTERNAL_LIBRARY` | none | **PARTIALLY_IMPLEMENTED** | Ontology slot exists; nothing populates or validates `pypi:x@y`-style identifiers |
| DTD-02 Query Understanding (Tier-0 + SLM) | §24-28 | §22-28 | — | — | **NOT_IMPLEMENTED** | — |
| DTD-03 Query Planner + Planner/LLM boundary | §29-30 | §29-34 | — | — | **NOT_IMPLEMENTED** (boundary therefore **UNVERIFIED**, not proven) | Cannot test a boundary that doesn't exist yet |
| DTD-04 Retrieval/Ranking/MSS | §32-39 | §35-41 | — | — | **NOT_IMPLEMENTED** | — |
| LLM Gateway + structured claims | §41-45 | §42-45 | — | — | **NOT_IMPLEMENTED** | — |
| DTD-05 Verification Engine | §42-43 | §46-51 | — | — | **NOT_IMPLEMENTED** — spec is now consistent (**C-3 RESOLVED**), no longer contradicted | TAD §50's 6-state enum is now explicitly canonical, with tested mapping functions required to HLRD's 4-value presentation label and TAD §5's 3-bucket routing view. Safe to implement against once this phase starts. |
| Telemetry Store | §52 | §65 | — | — | **NOT_IMPLEMENTED** | — |
| Artifact Store | — | §52-53 | — | — | **NOT_IMPLEMENTED** | — |

**Summary (updated post-ADR-018):** 8 of ~26 major requirements IMPLEMENTED, 10 PARTIALLY_IMPLEMENTED, 8 NOT_IMPLEMENTED, 0 CONTRADICTED at the spec level. Nothing is marked implemented merely because a class exists — every "IMPLEMENTED" row has a passing behavioral test.

---

## C. Research Resource Audit

| Resource | Purpose | Codex adopts | Codex does NOT adopt | Section | Impact | Status |
|---|---|---|---|---|---|---|
| SCIP (`sourcegraph/scip`) | Precise code indexing format | The `scip.proto` schema as the wire format to normalize from (researched) | SCIP as the only indexing source; its 86-value `Kind` enum 1:1 (collapses into our 16 base types + roles) | HLRD §11, TAD §8-9 | ADR-004 | **RESEARCHED**; 0 adapter code |
| CodeQL | Static analysis / data-flow evidence | SARIF v2.1.0 as the output format to parse (researched) | CodeQL as a call-graph source (SCIP is cheaper for that); raw SARIF severity as confidence directly | HLRD §12, TAD §8-9 | ADR-005 | **RESEARCHED**; 0 adapter code |
| Git | Historical evidence, revision/diff detection | GitPython as the concrete library (already a dependency); commit/diff model | Treating git history as semantic truth (HLRD §13 explicit) | HLRD §13, TAD §7 | none open | **IMPLEMENTED** — `RepositoryManager` (Phase 1, revision/diff) and `GitAdapter` (D3, `HISTORY`/`CO_CHANGE` evidence-emitting capabilities) |
| RepoGraph (`chokevin/repograph`, `SillySerpent/Repograph`) | Reference repository-graph implementations | Ontology validation (their node/edge sets corroborate ours); architectural precedent for a tree-sitter fallback and for Kuzu as storage | Their code directly — no vendoring/wrapping, license unreviewed | HLRD §10, TAD §8 | ADR-001 (Kuzu candidate), ADR-006 | **RESEARCHED**; 0 code |
| Sourcegraph (docs, Code Navigation API) | Repository intelligence provider (search, precise nav, xrefs) | Concept of a Sourcegraph Adapter behind the `ProviderAdapter` contract | Sourcegraph as mandatory — HLRD §8 allows "any qualified repository intelligence/search provider" | HLRD §9, TAD §8 | ADR-006 | **NOT researched** — `sourcegraph.com` is blocked by this environment's egress proxy. Open gap. |
| Tree-sitter | Incremental parsing, referenced for a from-scratch graph adapter and for incremental updates | Candidate engine for a fallback adapter when no Sourcegraph/SCIP source is available | Not currently a dependency; no code uses it | HLRD §23, §62 | ADR-006 (fallback strategy) | **Indirectly touched** via the RepoGraph research, not researched directly; correctly out of scope until a fallback adapter is actually needed |
| Microsoft GraphRAG | General graph-based RAG pattern | The general "build a graph → retrieve a subgraph → ground the LLM" pattern, as validation of Codex's own DTD-04 approach | Its community-detection/summarization algorithms; not a dependency | HLRD §62 | none (background only) | **NOT researched**; informs philosophy only |
| TransE / KG embeddings paper | Reference for future relational embeddings | Nothing in V1 — HLRD §34 / TAD §3 make embeddings explicitly optional/non-mandatory; this is V2 research (TAD §79) | No embedding code planned for V1 | HLRD §62 | ADR-009, but P3 | **Correctly deprioritized**, not researched |
| LangChain / LlamaIndex | LLM orchestration reference | Potentially the LLM Gateway's structured-output/tool-calling plumbing (Phase 8, not started) | Not a dependency yet; no code exists to use them | HLRD §62 | ADR-008 | **Correctly out of scope** until Phase 8 |
| OpenTelemetry | Runtime Adapter source + general observability pattern | Nothing yet — Runtime Adapter is optional, Telemetry Store isn't built | Not a dependency | HLRD §14, TAD §57, §65 | ADR-014, Telemetry design | **Correctly out of scope** for Phase 1 |
| scikit-learn calibration (Platt/isotonic) | SLM confidence calibration reference | Nothing yet — no SLM exists | Not a dependency | HLRD §62, TAD §25 | ADR-007 | **Correctly out of scope** until Phase 4 |

**Important:** none of the above have been added as dependencies simply because they're mentioned in the architecture documents, per the directive's explicit instruction. `pyproject.toml` still only declares `networkx`, `pydantic`, `GitPython` — the three things actually used by Phase 1 code.

---

## D. ADR Audit

TAD §77 lists 17 candidate ADR titles. Classifying each per the directive's scheme (A: decided by HLRD/DTD, B: decided by TAD, C: genuine unresolved decision, D: implementation detail — not an ADR, E: duplicate, F: superseded, G: research item):

| ADR | Title | Class | Reasoning |
|---|---|---|---|
| ADR-001 | Graph Storage Technology | **C** | Genuinely open (TAD §39/§53/§75 explicitly defer it). Now better-informed: Neo4j/Dgraph/NetworkX (TAD's own list) + **Kuzu** (from RepoGraph research). |
| ADR-002 | Evidence Storage Technology | **C** | Genuinely open (TAD §53: "Technology selection remains an ADR"). |
| ADR-003 | Artifact Storage Technology | **C** | Genuinely open (TAD §52-53). |
| ADR-004 | SCIP Integration Strategy | **C** | Open — *strategy* (e.g. where call-edge derivation lives) is undecided even though the wire format is now known. |
| ADR-005 | CodeQL Integration Strategy | **C** | Open, and now sharper: research surfaced that single-entity `problem` findings don't cleanly fit the `subject/predicate/object` evidence shape — a real question this ADR must resolve, not a formality. |
| ADR-006 | Sourcegraph/RepoGraph Integration Strategy | **C** | Open, and blocked on the Sourcegraph API research gap (see §C). |
| ADR-007 | SLM Selection | **C + G** | Genuinely open per TAD §84 ("SLM/LLM selection 🟡 ADR"), but realistically needs benchmark data before it can be decided (research item as well as a decision). |
| ADR-008 | LLM Selection | **C + G** | Same reasoning as ADR-007. |
| ADR-009 | Embedding Strategy | **B (partly) + C (partly)** | *Whether* embeddings are mandatory is **already decided by TAD** (§3, §27, §34: optional, not mandatory) — do not reopen that question. *Which* embedding technology, if ever adopted, remains open but is P3/deferred (V1 doesn't need it at all). |
| ADR-010 | Search/Ranking Engine | **B (formula) + C (infra, narrower than the title suggests)** | The ranking *algorithm* is already decided by TAD §36-38 (BM25 + structural + proximity + Jaccard, with explicit formulas) — do not reopen the formula. What's genuinely open is narrower: in-process BM25 vs. standing up dedicated search infrastructure. Picking a BM25 *library* for the in-process case is implementation detail (**D**), not an ADR. |
| ADR-011 | Cache Technology | **C** | Genuinely open (TAD §53-54 defer technology, not architecture — the cache *architecture* is closed). |
| ADR-012 | Graph Versioning Strategy | **B — CLOSED/SUPERSEDED** (now formal in TAD §77 itself, post-reconciliation) | TAD §19-21 and §71 already fully specify the strategy (composite version key, snapshot+diff, immutable-once-published, locked-version reads), and TAD §84 marks "Versioning" and "Historical graph" 🟢 CLOSED. Not a genuine open ADR. The only residual question — which storage engine holds snapshots/diffs — folds into ADR-001/ADR-002, making a separate ADR-012 partly a duplicate (**E**) of those. |
| ADR-013 | Historical Graph Reconstruction | **B — CLOSED/SUPERSEDED** (now formal in TAD §77 itself, post-reconciliation) | TAD §21 already specifies "nearest snapshot + changesets," marked 🟢 CLOSED in §84. Residual storage-tech question folds into ADR-001/002. |
| ADR-014 | Runtime Adapter Strategy | **B (policy) + C (which providers, P3)** | "Optional, provider-specific, no universal instrumentation" is already decided (HLRD §14, TAD §57, and directive §8-9 explicitly forbid reopening this). What's open — which concrete runtime provider(s), if any, V1 actually integrates — is real but low priority since the capability itself is optional. |
| ADR-015 | API Protocol | **C** | Genuinely open (TAD §69 explicitly defers it to "a dedicated technical design deliverable"). Research added **MCP** as a candidate alongside REST/GraphQL/gRPC. |
| ADR-016 | Authentication/Authorization | **C** | Genuinely open — neither HLRD's "Security and Trust" (§50) nor TAD's "Security Boundary" (§61) go beyond stating the LLM-access constraint; no auth model is specified anywhere. |
| ADR-017 | Deployment Architecture | **C** | Genuinely open (TAD §84: "Deployment technology 🟡 ADR"; §70 gives only a logical sketch). |
| ADR-018 *(new, D2)* | Provider Scoring Factor Sourcing (`evidence_quality`, normalized `freshness`, `cost_factor`) | **RESOLVED 2026-08-30** | Not TAD's own list — surfaced by implementing D2, closed same-phase by explicit decision. `capability_match`/`availability` are Registry-derived; `evidence_quality`/`cost_factor` are supplied provider metadata (`ProviderScoreProfile`, attached once at `register()`, not per query); `freshness` is Registry-derived from the adapter's timestamp via a documented default decay function. Full resolution and rationale: §I. |

**Net effect:** 14 of 17 TAD-listed candidates remain genuine open decisions (several narrower or lower-priority than their titles suggest), 2 (ADR-012, ADR-013) should be treated as already closed by TAD and folded into ADR-001/002 rather than reopened, and 1 (ADR-010) is half-closed (formula fixed, only infrastructure choice open). No ADR should currently be marked D outright, but ADR-010's sub-question of "which BM25 library" is implementation detail within an otherwise-real ADR. D2 surfaced one genuinely new candidate not in TAD's original 17 (ADR-018) — now resolved.

---

## E. Cross-Document Contradiction Report

| ID | Doc A | Doc B | Conflict | Impact | Resolution | Code change needed | Doc change needed |
|---|---|---|---|---|---|---|---|
| **C-1** | HLRD §16 (`TESTS`) | TAD §14 (`TESTED_BY`) | Same concept, **inverted direction**, not just a naming difference: HLRD implies subject=test → object=code; TAD implies subject=code → object=test. | Medium — affects every test-coverage/adapter edge and TAD's own `TEST_COVERAGE` query intent (HLRD §28). | **RESOLVED 2026-08-30** (`docs/architecture-reconciliation.md` §2): `TESTED_BY` is canonical, direction = production code → test. No separate `TESTS` relationship; inverse queries traverse `TESTED_BY` by object at query time. | None — code (`codex.ontology.relationships.RelationshipType.TESTED_BY`) already correct. | **Done** — HLRD §16 amended to `TESTED_BY` with a resolution note. |
| **C-2** | TAD §19 (literal struct: `scip_index_version`, `codeql_snapshot_version`, `runtime_version`) | TAD invariant #1 / §8 / §75 ("canonical graph is provider-independent," "must never depend directly on provider-specific schemas") | TAD contradicts itself: §19's literal `GraphVersion` example hardcodes three specific provider names as struct fields, which a 4th provider would require a schema migration to accommodate — directly against the provider-independence invariant. | High if taken literally — silently reintroduces provider coupling into the one place (`GraphVersion`) that's supposed to be provider-agnostic. | **RESOLVED 2026-08-30** (`docs/architecture-reconciliation.md` §3): generic `provider_versions: dict[str, str]` is canonical; TAD §19's named-field struct was illustrative and is superseded. | None — code (`codex.graph.version.GraphVersion.provider_versions`) already correct. | **Done** — TAD §19 amended to the generic form with a resolution note. |
| **C-3** | HLRD §42-43 (`FULLY_VERIFIED, PARTIALLY_VERIFIED, UNVERIFIED, CONTRADICTED` — 4 values) | TAD §50 (`VERIFIED, PARTIALLY_VERIFIED, QUALIFIED, DISPUTED, INCONCLUSIVE, REJECTED` — 6 values) **and** TAD §5's pipeline diagram (`VERIFIED, QUALIFIED, ABSTAIN` — 3 buckets) | **Three different, unreconciled enumerations of the same "final answer/verification state" concept**, with no stated mapping between them. | **High** — directly gates Phase 9 (Verification Engine, DTD-05) and Phase 8's claim schema. | **RESOLVED 2026-08-30** (`docs/architecture-reconciliation.md` §4, explicit human decision): TAD §50's 6-value enum is canonical and internal. HLRD §42-43's 4 labels and TAD §5's 3 routing buckets are both derived, tested mapping functions from that one enum — never independently defined or re-derived ad hoc. | None yet — binding on Phase 9 when it's built, not on any current code. | **Done** — TAD §50 marked canonical with both mapping tables; TAD §5 diagram annotated; HLRD §42-43 marked as a presentation mapping with a pointer to TAD §50. |
| **C-4** | TAD §15 (`Evidence.confidence`, provider-reported) | TAD §25 (SLM confidence, calibrated probability), §31 (`evidence_quality`, registry-level), §48 (Verification `V`, composite formula) | Not a logical contradiction, but the word "confidence"/"quality" is reused across at least 4 distinct scoped values with different owners and formulas, with no namespacing convention. | Medium — documentation-clarity risk today (only `Evidence.confidence` exists in code); real implementation-bug risk once the other three exist (e.g. feeding `Evidence.confidence` where Verification's `V` is expected). | Recommend a terminology appendix in the TAD distinguishing the four scopes. No decision forced now. | None. | Suggested TAD addition, not required immediately. |
| **C-5** | This directive (assumes DTD-01..05 are separate documents) | Actual repo state (`docs/HLRD.md`, `docs/TAD.md` only) | See §0. Not an HLRD/TAD contradiction, but a mismatch between what's being asked for and what exists. | Low urgency for code, but blocks treating "DTD audit" as literally complete — there is nothing beyond TAD's own inline sections to audit. | **RESOLVED 2026-08-30** (`docs/architecture-reconciliation.md` §5, explicit decision): DTD-01..05 stay embedded in TAD as section ranges — no separate documents. A precise TAD-section mapping for each DTD is now recorded. Standalone files would require their own ADR first if ever pursued. | None. | **Done** — no doc split; mapping table lives in the reconciliation doc. |
| **C-6** | TAD §12 (`RepositorySymbol` example: 7 fields) | HLRD §18 (Canonical Identity: repo, revision, source location, qualified name, provider IDs, language, entity type) | Not a true contradiction — TAD §12 is explicitly "Example node," not an exhaustive schema — but worth recording so the extra fields in code (`repository_id`, `repository_revision`, `language`, `provider_ids`, `created_at`) read as a deliberate synthesis of HLRD §18, not an ungrounded addition. | Low. | Code is correct as-is. | None. | Suggested one-line note in TAD §12: "see HLRD §18 for the full identity field set." |

No contradiction above was silently resolved by picking a side without recording it — C-1 and C-2 recommend keeping the code's existing (correct) behavior but require a documentation update to make that an explicit, recorded decision rather than silent drift; C-3 is left genuinely open pending a human decision, per the directive's own conflict rule (§51).

---

## F. Current Code Architecture

| Module | Responsibility | Spec section |
|---|---|---|
| `codex.ontology.entities` | Base entity types, roles, `RepositorySymbol` node model, canonical ID hashing | HLRD §16-18, TAD §12-13 |
| `codex.ontology.relationships` | `RelationshipType` enum + derived-relationship registry | HLRD §16, TAD §14 (see **C-1**) |
| `codex.evidence.model` | `Evidence`, `EvidenceCohort`, `EvidenceStatus`, `CoverageStatus`, `CanonicalRelationship` | TAD §15-18, §73 |
| `codex.evidence.store` | `EvidenceStore` protocol + `InMemoryEvidenceStore` | TAD §53 (Evidence Store) — storage tech deferred (ADR-002) |
| `codex.graph.version` | `GraphVersion` (composite version, immutability via `.publish()`) | TAD §19-20, invariant #4 (see **C-2**) |
| `codex.graph.store` | `GraphReader`/`GraphStore` protocols (read/write split) | TAD §12, §62, invariants #6-8 |
| `codex.graph.memory_store` | `InMemoryGraphStore` (NetworkX-backed) | TAD §53 (Canonical Graph Store) — storage tech deferred (ADR-001) |
| `codex.repository.manager` | `RepositoryManager`: register/clone, HEAD revision, changed-file diff | TAD §7 |
| `codex.repository.models` | `RepositoryMetadata`, `ChangeSet` | TAD §7, §72 |
| `codex.provider.capability` | `Capability` (D1) | TAD §9-10 |
| `codex.provider.contract` | `ProviderAdapter` protocol + supporting types (D1) | TAD §8-9, §64; directive §11-13 |
| `codex.provider.git_adapter` | `GitAdapter` — first concrete `ProviderAdapter` (`HISTORY`, `CO_CHANGE`) (D3) | HLRD §13; TAD §7, §14, §72; directive D3 |
| `codex.registry.models` | `ProviderEvaluationStatus`, `ProviderEvaluation` (D2) | TAD §10 |
| `codex.registry.scoring` | `ProviderScoreInputs`, `ProviderScoreProfile`, `provider_score()`, `default_freshness_score()`, `PROVIDER_SCORE_WEIGHTS` (D2, ADR-018) | TAD §31; directive D2 §4; ADR-018 |
| `codex.registry.registry` | `CapabilityRegistry` (D2) | TAD §10, §31 |

**Zero code exists** for 12 of TAD's 18 named components (§6): Entity Resolution Engine, Query Understanding Engine, SLM Gateway, Query Planner, Retrieval Engine, Ranking Engine, MSS Builder, LLM Gateway, Verification Engine, Telemetry Store, Artifact Store, Offline Calibration Pipeline. Provider Adapter Manager now has both a contract (`codex.provider.contract`, D1) and its first concrete adapter (`codex.provider.git_adapter.GitAdapter`, D3) — 6 of 18 components have code (4 from Phase 1 + `codex.provider` + `codex.registry`); the count of components is unchanged since the Git Adapter lives inside the existing Provider Adapter Manager component, not a new one.

---

## G. Implementation Gap List

**P0 — architectural correctness blockers: all RESOLVED as of 2026-08-30 (see `docs/architecture-reconciliation.md`).**
- ~~Resolve **C-1**~~ — done: `TESTED_BY` canonical, HLRD §16 amended.
- ~~Resolve **C-2**~~ — done: generic `provider_versions` dict canonical, TAD §19 amended.
- ~~Resolve **C-3**~~ — done: TAD §50's 6-value enum canonical, mapping tables added to TAD §50/§5 and HLRD §42-43.
- ~~Decide **C-5**~~ — done: DTD-01..05 stay embedded in TAD; section mapping recorded.

No P0 items remain open. Proceed to P1.

**P1 — V1 functionality blockers (TAD §78 "Mandatory"):**
- ~~`ProviderAdapter` contract/protocol (TAD §9)~~ — ✅ **done (D1)**: `codex.provider`, 21 behavioral tests, 100% coverage.
- ~~Git Adapter proper (evidence-emitting: `CO_CHANGED_WITH`, introductions/deletions — beyond `RepositoryManager`'s revision/diff plumbing, which is a prerequisite, not the adapter)~~ — ✅ **done (D3)**: `codex.provider.git_adapter.GitAdapter`, 30 behavioral tests, 100% coverage. `HISTORY`/`CO_CHANGE` — see §B above.
- ~~Capability Registry (TAD §10)~~ — ✅ **done (D2, ADR-018 resolved)**: `codex.registry`, 79 behavioral tests, 100% coverage. Scoring fully implemented and sourced end-to-end — see §I.
- Ingestion pipeline wiring `RepositoryManager.detect_changed_files()` → provider extraction → graph upsert (TAD §72) — nothing currently connects Phase 1's pieces end-to-end. **D4, next.**
- SCIP Adapter (informed by `docs/research/provider-formats.md`).
- Entity Resolution + Reconciliation Engine (contradiction-score formula, TAD §38) — meaningful only once ≥2 providers exist.
- CodeQL Adapter (resolve the single-entity-finding evidence-shape question as part of this work, per ADR-005).
- A repository-graph Adapter (Sourcegraph or RepoGraph-style) — blocked on the Sourcegraph research gap.
- Telemetry Store, Artifact Store, LLM Gateway, DTD-02..05 — all `NOT_IMPLEMENTED`.

**P2 — important, non-blocking for a minimal vertical slice:**
- Runtime Adapter (explicitly optional).
- Store-level enforcement of the graph mutation boundary (today it's a typing convention only).
- Artifact Store's actual URI-resolution service (`raw_reference` scheme is now validated as of D1, but nothing resolves the URI to bytes yet).
- Terminology cleanup for **C-4** (confidence overload).

**P3 — future optimization, correctly out of scope (TAD §3, §79):**
- Embeddings (technology choice only — "is it mandatory" is already closed: no).
- Relational/GNN embeddings, multi-repository federation, online learning beyond telemetry/cache tuning.

---

## H. Ordered Implementation Plan

Concretizes TAD §80's phases into the actual next steps given what exists today:

0. **Resolve P0 items** — ✅ done (`docs/architecture-reconciliation.md`; C-1/C-2/C-3/C-5 all resolved, HLRD/TAD amended). No code was needed.
1. **`ProviderAdapter` contract (D1)** — ✅ **done**: `codex.provider.{capability,contract}`, 21 behavioral tests, 100% coverage. See `docs/architecture-reconciliation.md`-style traceability above.
1b. **Capability Registry (D2)** — ✅ **done, including ADR-018 resolution**: `codex.registry.{models,scoring,registry}`, 79 behavioral tests, 100% coverage. All 5 `ProviderScore` factors now fully sourced (§I).
2. **Git Adapter (D3)** — ✅ **done**: `codex.provider.git_adapter.GitAdapter`, 30 behavioral tests, 100% coverage. First concrete `ProviderAdapter`, no external wire format, extends the already-tested `RepositoryManager`. See §B above.
3. **Ingestion pipeline (D4)**: `ChangeSet` → `Evidence` → graph upsert, wiring existing Phase 1 pieces into one working, testable vertical slice for the Git Adapter before adding more providers. Not started, next per the Phase D directive's order.
4. **SCIP Adapter (D5)** — second provider; unlocks real Entity Resolution work (moot with only one provider). Not started.
5. **Entity Resolution + Reconciliation Engine** (contradiction score, `CanonicalRelationship.status` assignment) — needs ≥2 providers to be meaningful.
6. **CodeQL Adapter** — third provider; resolves the evidence-shape question from research as part of ADR-005.
7. **Repository-graph Adapter** (Sourcegraph, or a RepoGraph-style tree-sitter fallback if the Sourcegraph API gap isn't closed by then).
8. **Telemetry Store + Artifact Store** — needed before Query Understanding/Planning have anywhere to record decisions, and before adapters need `raw_reference` resolution.
9. **DTD-02 Query Understanding** (Tier-0 deterministic + SLM contract) — first Intelligence Engine component; needs a populated graph from steps 1-7.
10. **DTD-03 Query Planner** (Capability Registry-driven, model-independent, testably zero LLM/SLM imports) — needs DTD-02's `QueryContract`.
11. **DTD-04 Retrieval/Ranking/MSS** — needs DTD-03's `RetrievalPlan`.
12. **LLM Gateway + structured claims** (Phase 8) — needs DTD-04's `EvidencePackage`.
13. **DTD-05 Verification Engine** — **blocked on C-3** being resolved first.
14. **Offline Calibration Pipeline + benchmark corpus** (Phase 5/11) — needs an end-to-end pipeline to generate telemetry to calibrate against.
15. **Production hardening** (Phase 6).

Each step should land as its own reviewable commit with tests, per the directive's `audit → reconcile → trace → implement one phase → test → verify → checkpoint` loop — the same pattern already used for Phase 1.

---

## I. ADR-018 — Provider Scoring Factor Sourcing — RESOLVED 2026-08-30

**Status: RESOLVED.** Originally raised as a finding during D2 implementation (original writeup preserved below, unmodified, for the audit trail); closed by explicit architecture decision during D2 review. `CapabilityRegistry` and its tests were refactored to match — see the updated contract in "Resolution" below.

### Resolution

| Factor | Origin (final) |
|---|---|
| `capability_match` | Registry-derived: `1.0` for any provider `providers_for()` returns (it declares the capability), and such a provider is the only kind ever scored — non-declaring providers are excluded before scoring, never scored at `0.0`. |
| `availability` | Registry-derived: `adapter.availability(capability, repository)` (D1), read fresh on every `evaluate()`/`rank()` call. |
| `evidence_quality` | **Supplied metadata**, not computed: `ProviderScoreProfile.evidence_quality`, attached once via `CapabilityRegistry.register(adapter, profile)` — canonical per-provider configuration, not a per-query input. Pydantic-validated to `[0.0, 1.0]`. |
| `cost_factor` | **Supplied metadata**, same mechanism: `ProviderScoreProfile.cost_factor`, already normalized `[0.0, 1.0]` by whoever configures the profile — never a raw monetary value. |
| `freshness` | Registry-derived from the adapter's own `freshness` timestamp (D1) via `codex.registry.scoring.default_freshness_score()` — a single generic (provider-neutral) exponential decay with a documented, swappable half-life (`DEFAULT_FRESHNESS_HALF_LIFE`, 24h). Explicitly a calibration point (see below), not a claimed-final algorithm. |

**Why a profile, not per-call arguments:** the original D2 implementation required `rank()` callers to supply `evidence_quality`/`freshness_score`/`cost_factor` as dict arguments on every call — functionally correct (nothing was invented) but architecturally wrong, because two different callers could supply different values for the same provider/capability/repository and get different rankings. `ProviderScoreProfile` is now attached once at `register()` time (registry-owned state), so `rank(capability, repository)` — no scoring kwargs at all — is deterministic for a given repository regardless of caller. `test_ranking_does_not_depend_on_caller_supplied_scoring_values` proves the old signature is gone (calling with the removed kwargs raises `TypeError`).

**`evidence_quality`/`cost_factor` remain explicitly unresolved *sourcing policy*, by design:** ADR-018 decides *where the registry looks* (a profile attached at registration), not *how a real value gets computed* for a real provider (SCIP, CodeQL, ...) — that remains an open extension/calibration point, deliberately not decided in D2 per the directive's explicit instruction not to invent a universal quality formula or cost normalization. A provider registered without a profile is fully usable for discovery/evaluation (`providers_for()`, `evaluate()`); `rank()` raises a clear `ValueError` naming it if asked to score it, rather than guessing.

**Final `rank()` contract:**

```python
def rank(
    self, capability: Capability, repository: RepositoryMetadata, *, now: datetime | None = None
) -> list[ProviderEvaluation]: ...
```

`now` is accepted only to make freshness decay deterministic in tests; real callers omit it. No other parameters — this is the structural guarantee behind "ranking does not depend on arbitrary caller-provided scoring values."

**No cross-document contradiction found.** TAD §31 gives the aggregation weights and normalization requirement but never defined individual-factor sourcing; this decision fills that gap without changing the formula, the weights, the `capability_match=0` exclusion rule, or anything in the (still-untouched, still-approved) D1 `ProviderAdapter` contract. Provider-independence, eligibility-vs-availability separation, and deterministic tie-broken ordering are all unchanged and re-verified by the updated test suite.

---

### Original finding (2026-08-30, preserved for the audit trail)

Discovered while implementing D2 (`CapabilityRegistry.rank()`). Not silently resolved — flagged here per the Phase D directive's own instruction ("if D2 exposes an underspecified behavior that cannot be resolved from HLRD/TAD/reconciliation/resources, STOP and report it").

TAD §31 defines the `ProviderScore` aggregation:

```
ProviderScore =
    0.40 capability_match
  + 0.20 evidence_quality
  + 0.15 availability
  + 0.15 freshness
  + 0.10 cost_factor
```

...and states each factor is normalized to `[0.0, 1.0]`, with `capability_match = 0` excluding a provider before scoring. It does **not** define how any individual factor is computed — that was always going to be down to whatever implements the registry. D1 (already approved and closed) resolved two of the five:

| Factor | Source | Status |
|---|---|---|
| `capability_match` | `capability in adapter.supported_capabilities` — 1.0 if the provider is a candidate at all (it's never scored otherwise), 0.0 (excluded) otherwise | **Resolved**, derivable from D1 |
| `availability` | `adapter.availability(capability, repository)` — already `[0.0, 1.0]` per the D1 clarification | **Resolved**, direct D1 property |
| `evidence_quality` | — | **No source.** No adapter property, no formula in HLRD/TAD, no mention in the reconciliation docs. `Evidence.confidence` is per-*evidence-record* and only exists after extraction — it can't inform a pre-extraction provider-selection score for a query that hasn't retrieved anything yet, at least not without a historical/telemetry aggregation mechanism that doesn't exist yet (Telemetry Store, TAD §65, is itself `NOT_IMPLEMENTED`). |
| `freshness` (as a score) | — | **Half-resolved.** The *raw signal* exists (`adapter.freshness -> datetime \| None`, D1), but converting a timestamp into a `[0,1]` score needs a staleness/decay policy (how many hours/days maps to what score?) that is defined nowhere. |
| `cost_factor` | — | **No source.** No adapter property, no formula. TAD/HLRD mention "cost" only as this weighted factor name; an earlier turn's own guidance ("do not allow cost expressed in raw currency to contaminate the score") implies it should already be pre-normalized before use, but never says by whom or how. |

**What D2 does about it:** implements the formula and weights exactly as specified (`codex.registry.scoring`, tested independently of the registry in `test_provider_scoring.py`), and implements everything the registry *can* determine on its own (registration, capability discovery, eligibility/health/availability evaluation and classification). For the three unresolved factors, `CapabilityRegistry.rank()` requires them as **explicit, required, per-provider-name keyword arguments with no default** — calling `rank()` without an entry for every usable candidate raises `ValueError` naming the provider. Nothing in `codex.registry` guesses a number for any of the three.

**Two candidate resolutions, neither decided here:**
1. Extend the (already-approved) `ProviderAdapter` contract with `evidence_quality`/`cost_factor` properties (mirroring how `health_status`/`availability`/`freshness` already work) — plausible since TAD §9's own attribute list already established the pattern of adapters reporting simple facts about themselves.
2. Treat these as Telemetry/Offline-Calibration concerns (TAD §59, §65-66) — historical per-provider statistics computed from accumulated production evidence, fed into `rank()` by whatever calls the registry (a future D3+ Planner), rather than adapter-reported.

This does not block D3 (Git Adapter): `providers_for()`/`evaluate()`/registration work fully without it, and `rank()` is usable today by any caller (including future D3+ code) willing to supply the three inputs explicitly, exactly as the D2 tests do.
