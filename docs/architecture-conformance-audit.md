# Codex — Architecture Conformance Audit

> Produced per the Architecture Conformance, Research Traceability & Ordered Implementation Directive. Covers HLRD v1.0, TAD v1.0, the existing `src/codex/` code, tests, and `docs/research/provider-formats.md`. No production code was written or modified while producing this audit.

Audited: 2026-08-30 (commit `13bbaf6`).

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
| Implementation | Phase 1 (Foundation) only: `codex.ontology`, `codex.evidence`, `codex.graph`, `codex.repository`. 0 of TAD's 18 components (§6) beyond these four have any code. |

---

## B. Requirement Traceability Matrix

Only major architectural requirements are shown (not every clause). "Module" is blank where nothing implements the requirement.

| Req ID | HLRD | TAD | Module | Test(s) | Status | Evidence |
|---|---|---|---|---|---|---|
| Base Type + Role ontology | §16-17 | §12-13 | `codex.ontology.entities` | `test_ontology.py` | **IMPLEMENTED** | `BaseEntityType`, free-string `roles[]`, `RepositorySymbol.has_role()` |
| Canonical relationship types | §16 | §14 | `codex.ontology.relationships` | none dedicated | **PARTIALLY_IMPLEMENTED** | All TAD §14 types + HLRD's extras implemented, but see **C-1**: silently chose TAD's `TESTED_BY` over HLRD's `TESTS` without recording the decision |
| Canonical identity / join key | §18 | §12 | `codex.ontology.entities.build_canonical_id` | `test_ontology.py` | **IMPLEMENTED** (join key only) | Deterministic hash of repo+revision+type+language+qualified_name |
| Entity Resolution (multi-provider reconciliation) | §19 | component #7 | — | — | **NOT_IMPLEMENTED** | Only the join-key primitive exists; no reconciliation logic, and moot with zero providers |
| Evidence model (13 fields) | §15 (implied) | §15 | `codex.evidence.model.Evidence` | `test_evidence.py` | **IMPLEMENTED** | All TAD §15 fields present verbatim |
| Evidence independence default | — | §16 | `Evidence.effective_independence_group` | `test_evidence.py` | **IMPLEMENTED** | Defaults to `provider_default:{provider}` when unset |
| EvidenceCohort (successful/failed/partial capability lists) | — | §17 | `codex.evidence.model.EvidenceCohort` | `test_evidence.py` | **PARTIALLY_IMPLEMENTED** | Data container correct; nothing yet *consumes* it to drive negative-query `INCONCLUSIVE` logic (no Planner exists) |
| Evidence Status 6-way taxonomy, DISPUTED ≠ UNRESOLVED | — | §18 | `codex.evidence.model.EvidenceStatus` | none dedicated | **PARTIALLY_IMPLEMENTED** | Enum shape correct and distinct; no reconciliation logic yet assigns these, so the *behavioral* distinction is unverified |
| `raw_reference` resolvable URI (artifact://, s3://, file://) | — | §16, §52 | `Evidence.raw_reference` | none | **NOT_IMPLEMENTED** | Field is an unvalidated `str \| None`; no Artifact Store or resolution service exists |
| GraphVersion composite key | §19 (via TAD) | §19 | `codex.graph.version.GraphVersion` | `test_graph_store.py` | **PARTIALLY_IMPLEMENTED** | Deviates from TAD §19's literal field names — see **C-2** |
| Graph version immutable once published | invariant | §20, invariant #4 | `GraphVersion.publish()` | `test_graph_store.py` | **PARTIALLY_IMPLEMENTED** | Model-level copy-on-publish works; nothing enforces immutability at the store level |
| Graph version locking through Planning→Retrieval→MSS→Verification | — | §20 | — | — | **NOT_IMPLEMENTED** | No Planner/Retrieval/Verification exist yet to lock a version through |
| Graph mutation boundary (ingestion-only writes) | — | §62, invariants #6-8 | `codex.graph.store.{GraphReader,GraphStore}` | none dedicated | **PARTIALLY_IMPLEMENTED** | Interfaces are structurally split; nothing runtime-enforces it (typing convention only), and it's moot until Query/Verification code exists |
| Historical storage = snapshot + diffs | — | §21 | — | — | **NOT_IMPLEMENTED** | No snapshotting or reconstruction exists |
| Repository Manager: register/clone/revision/diff | — | §7 | `codex.repository.manager.RepositoryManager` | `test_repository_manager.py` (3 tests, real git repos, no network) | **PARTIALLY_IMPLEMENTED** | All done except "triggering indexing" (explicit TAD §7 responsibility) — no ingestion pipeline exists to trigger |
| Repository Manager does not interpret queries | — | §7 | (absence of query code in this module) | — | **IMPLEMENTED** | Satisfied by construction |
| Provider Adapter contract (`extract/validate/normalize`, capabilities) | — | §9 | — | — | **NOT_IMPLEMENTED** | No `ProviderAdapter` protocol exists at all |
| Capability Registry | — | §10 | — | — | **NOT_IMPLEMENTED** | — |
| Relationship Reconciliation / contradiction score | §20 | §38, §73 | `CanonicalRelationship` (shape only) | `test_evidence.py` (key only) | **PARTIALLY_IMPLEMENTED** | Data model exists (`supporting_evidence_ids`, `contradicting_evidence_ids`, `status`); zero logic computes them |
| External library package-qualified identity | — | §56 | `BaseEntityType.EXTERNAL_LIBRARY` | none | **PARTIALLY_IMPLEMENTED** | Ontology slot exists; nothing populates or validates `pypi:x@y`-style identifiers |
| DTD-02 Query Understanding (Tier-0 + SLM) | §24-28 | §22-28 | — | — | **NOT_IMPLEMENTED** | — |
| DTD-03 Query Planner + Planner/LLM boundary | §29-30 | §29-34 | — | — | **NOT_IMPLEMENTED** (boundary therefore **UNVERIFIED**, not proven) | Cannot test a boundary that doesn't exist yet |
| DTD-04 Retrieval/Ranking/MSS | §32-39 | §35-41 | — | — | **NOT_IMPLEMENTED** | — |
| LLM Gateway + structured claims | §41-45 | §42-45 | — | — | **NOT_IMPLEMENTED** | — |
| DTD-05 Verification Engine | §42-43 | §46-51 | — | — | **NOT_IMPLEMENTED**, and spec itself is **CONTRADICTED** — see **C-3** | Building this against the current spec would require guessing which of 3 status enumerations is authoritative |
| Telemetry Store | §52 | §65 | — | — | **NOT_IMPLEMENTED** | — |
| Artifact Store | — | §52-53 | — | — | **NOT_IMPLEMENTED** | — |

**Summary:** 4 of ~24 major requirements IMPLEMENTED, 10 PARTIALLY_IMPLEMENTED, 9 NOT_IMPLEMENTED, 1 effectively CONTRADICTED at the spec level (verification states). Nothing is marked implemented merely because a class exists — every "IMPLEMENTED" row has a passing behavioral test.

---

## C. Research Resource Audit

| Resource | Purpose | Codex adopts | Codex does NOT adopt | Section | Impact | Status |
|---|---|---|---|---|---|---|
| SCIP (`sourcegraph/scip`) | Precise code indexing format | The `scip.proto` schema as the wire format to normalize from (researched) | SCIP as the only indexing source; its 86-value `Kind` enum 1:1 (collapses into our 16 base types + roles) | HLRD §11, TAD §8-9 | ADR-004 | **RESEARCHED**; 0 adapter code |
| CodeQL | Static analysis / data-flow evidence | SARIF v2.1.0 as the output format to parse (researched) | CodeQL as a call-graph source (SCIP is cheaper for that); raw SARIF severity as confidence directly | HLRD §12, TAD §8-9 | ADR-005 | **RESEARCHED**; 0 adapter code |
| Git | Historical evidence, revision/diff detection | GitPython as the concrete library (already a dependency); commit/diff model | Treating git history as semantic truth (HLRD §13 explicit) | HLRD §13, TAD §7 | none open for the revision/diff part | **IMPLEMENTED** for revision/diff (`RepositoryManager`); a full evidence-emitting Git Adapter (e.g. `CO_CHANGED_WITH`) does not exist |
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
| ADR-012 | Graph Versioning Strategy | **B — reclassify, do not reopen** | TAD §19-21 and §71 already fully specify the strategy (composite version key, snapshot+diff, immutable-once-published, locked-version reads), and TAD §84 marks "Versioning" and "Historical graph" 🟢 CLOSED. As literally titled this is **not** a genuine open ADR. The only residual question — which storage engine holds snapshots/diffs — folds into ADR-001/ADR-002, making a separate ADR-012 partly a duplicate (**E**) of those. |
| ADR-013 | Historical Graph Reconstruction | **B — reclassify, same as ADR-012** | TAD §21 already specifies "nearest snapshot + changesets," marked 🟢 CLOSED in §84. Residual storage-tech question folds into ADR-001/002. |
| ADR-014 | Runtime Adapter Strategy | **B (policy) + C (which providers, P3)** | "Optional, provider-specific, no universal instrumentation" is already decided (HLRD §14, TAD §57, and directive §8-9 explicitly forbid reopening this). What's open — which concrete runtime provider(s), if any, V1 actually integrates — is real but low priority since the capability itself is optional. |
| ADR-015 | API Protocol | **C** | Genuinely open (TAD §69 explicitly defers it to "a dedicated technical design deliverable"). Research added **MCP** as a candidate alongside REST/GraphQL/gRPC. |
| ADR-016 | Authentication/Authorization | **C** | Genuinely open — neither HLRD's "Security and Trust" (§50) nor TAD's "Security Boundary" (§61) go beyond stating the LLM-access constraint; no auth model is specified anywhere. |
| ADR-017 | Deployment Architecture | **C** | Genuinely open (TAD §84: "Deployment technology 🟡 ADR"; §70 gives only a logical sketch). |

**Net effect:** 14 of 17 remain genuine open decisions (several narrower or lower-priority than their titles suggest), 2 (ADR-012, ADR-013) should be treated as already closed by TAD and folded into ADR-001/002 rather than reopened, and 1 (ADR-010) is half-closed (formula fixed, only infrastructure choice open). No ADR should currently be marked D outright, but ADR-010's sub-question of "which BM25 library" is implementation detail within an otherwise-real ADR.

---

## E. Cross-Document Contradiction Report

| ID | Doc A | Doc B | Conflict | Impact | Resolution | Code change needed | Doc change needed |
|---|---|---|---|---|---|---|---|
| **C-1** | HLRD §16 (`TESTS`) | TAD §14 (`TESTED_BY`) | Same concept, **inverted direction**, not just a naming difference: HLRD implies subject=test → object=code; TAD implies subject=code → object=test. | Medium — affects every test-coverage/adapter edge and TAD's own `TEST_COVERAGE` query intent (HLRD §28). | Current code (`codex.ontology.relationships.RelationshipType`) already silently chose TAD's `TESTED_BY` and omits `TESTS` — this was made during Phase 1 without recording it, which the directive explicitly says not to do. Recommend: keep `TESTED_BY` (consistent with `CONFIGURED_BY`/`PERSISTS_TO`'s "subject receives action" direction), update HLRD §16 to match. | None — code already correct if this resolution is accepted. | Update HLRD §16: `TESTS` → `TESTED_BY`. |
| **C-2** | TAD §19 (literal struct: `scip_index_version`, `codeql_snapshot_version`, `runtime_version`) | TAD invariant #1 / §8 / §75 ("canonical graph is provider-independent," "must never depend directly on provider-specific schemas") | TAD contradicts itself: §19's literal `GraphVersion` example hardcodes three specific provider names as struct fields, which a 4th provider would require a schema migration to accommodate — directly against the provider-independence invariant. | High if taken literally — silently reintroduces provider coupling into the one place (`GraphVersion`) that's supposed to be provider-agnostic. | Current code (`codex.graph.version.GraphVersion`) already resolved this correctly with a generic `provider_versions: dict[str, str]`, but did so silently. Recommend: keep the generic dict; treat TAD §19's struct as illustrative, not normative. | None — code already correct. | Add a clarifying note to TAD §19 that the struct is illustrative and the real schema is a generic provider→version mapping. |
| **C-3** | HLRD §42-43 (`FULLY_VERIFIED, PARTIALLY_VERIFIED, UNVERIFIED, CONTRADICTED` — 4 values) | TAD §50 (`VERIFIED, PARTIALLY_VERIFIED, QUALIFIED, DISPUTED, INCONCLUSIVE, REJECTED` — 6 values) **and** TAD §5's pipeline diagram (`VERIFIED, QUALIFIED, ABSTAIN` — 3 buckets) | **Three different, unreconciled enumerations of the same "final answer/verification state" concept**, with no stated mapping between them. Is TAD's `DISPUTED` the same as HLRD's `CONTRADICTED`? Does `INCONCLUSIVE` collapse into `UNVERIFIED`? What collapses TAD §50's 6 states into TAD §5's own 3-bucket diagram? | **High** — this directly gates Phase 9 (Verification Engine, DTD-05) and Phase 8's claim schema. No code exists yet, so there's no drift today, but building Phase 9 against this spec as-is means guessing. | **This is a genuine "architecture needs revision" case (directive §51) — flagged, not resolved.** Recommended path (not yet adopted): treat TAD §50's 6-state enum as the Verification Engine's authoritative internal result, define an explicit collapse to TAD §5's 3 pipeline buckets (`VERIFIED ← VERIFIED`; `QUALIFIED ← {PARTIALLY_VERIFIED, QUALIFIED, DISPUTED, INCONCLUSIVE}`; `ABSTAIN ← REJECTED`), and update HLRD §42 to reference TAD §50 instead of keeping its own divergent 4-value list. **Needs an explicit decision before Phase 9 starts.** | None yet — blocks Phase 9, not current code. | HLRD §42 and/or TAD §50/§5 need reconciling once a decision is made. |
| **C-4** | TAD §15 (`Evidence.confidence`, provider-reported) | TAD §25 (SLM confidence, calibrated probability), §31 (`evidence_quality`, registry-level), §48 (Verification `V`, composite formula) | Not a logical contradiction, but the word "confidence"/"quality" is reused across at least 4 distinct scoped values with different owners and formulas, with no namespacing convention. | Medium — documentation-clarity risk today (only `Evidence.confidence` exists in code); real implementation-bug risk once the other three exist (e.g. feeding `Evidence.confidence` where Verification's `V` is expected). | Recommend a terminology appendix in the TAD distinguishing the four scopes. No decision forced now. | None. | Suggested TAD addition, not required immediately. |
| **C-5** | This directive (assumes DTD-01..05 are separate documents) | Actual repo state (`docs/HLRD.md`, `docs/TAD.md` only) | See §0. Not an HLRD/TAD contradiction, but a mismatch between what's being asked for and what exists. | Low urgency for code, but blocks treating "DTD audit" as literally complete — there is nothing beyond TAD's own inline sections to audit. | Open: either TAD's inline DTD-0x sections are meant to permanently serve as the DTDs (no separate documents needed), or standalone documents were intended and never written. Cannot be inferred — needs a decision from whoever owns the doc set. | None. | Possibly none (if TAD's sections are accepted as sufficient) — or 5 new documents, if not. |
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

**Zero code exists** for 12 of TAD's 18 named components (§6): Provider Adapter Manager, Capability Registry, Entity Resolution Engine, Query Understanding Engine, SLM Gateway, Query Planner, Retrieval Engine, Ranking Engine, MSS Builder, LLM Gateway, Verification Engine, Telemetry Store, Artifact Store, Offline Calibration Pipeline. (That's 14, not 12 — TAD lists 18 components total; 4 have code, 14 don't.)

---

## G. Implementation Gap List

**P0 — architectural correctness blockers (resolve before more code):**
- Resolve **C-1** (`TESTS` vs `TESTED_BY` direction) — recommend accepting current code, updating HLRD §16.
- Resolve **C-2** (`GraphVersion` generic dict vs TAD §19's literal fields) — recommend accepting current code, annotating TAD §19.
- Resolve **C-3** (three inconsistent verification-state enumerations) — **genuinely needs a human decision**, not safe to implement Phase 9 against as-is.
- Decide **C-5** (should standalone DTD-01..05 documents be authored, or is TAD's inline treatment sufficient?).

**P1 — V1 functionality blockers (TAD §78 "Mandatory," nothing built yet):**
- `ProviderAdapter` contract/protocol (TAD §9).
- Git Adapter proper (evidence-emitting: `CO_CHANGED_WITH`, introductions/deletions — beyond `RepositoryManager`'s revision/diff plumbing, which is a prerequisite, not the adapter).
- Capability Registry (TAD §10).
- Ingestion pipeline wiring `RepositoryManager.detect_changed_files()` → provider extraction → graph upsert (TAD §72) — nothing currently connects Phase 1's pieces end-to-end.
- SCIP Adapter (informed by `docs/research/provider-formats.md`).
- Entity Resolution + Reconciliation Engine (contradiction-score formula, TAD §38) — meaningful only once ≥2 providers exist.
- CodeQL Adapter (resolve the single-entity-finding evidence-shape question as part of this work, per ADR-005).
- A repository-graph Adapter (Sourcegraph or RepoGraph-style) — blocked on the Sourcegraph research gap.
- Telemetry Store, Artifact Store, LLM Gateway, DTD-02..05 — all `NOT_IMPLEMENTED`.

**P2 — important, non-blocking for a minimal vertical slice:**
- Runtime Adapter (explicitly optional).
- Store-level enforcement of the graph mutation boundary (today it's a typing convention only).
- Artifact Store's actual URI-resolution service (`raw_reference` is currently an unvalidated string).
- Terminology cleanup for **C-4** (confidence overload).

**P3 — future optimization, correctly out of scope (TAD §3, §79):**
- Embeddings (technology choice only — "is it mandatory" is already closed: no).
- Relational/GNN embeddings, multi-repository federation, online learning beyond telemetry/cache tuning.

---

## H. Ordered Implementation Plan

Concretizes TAD §80's phases into the actual next steps given what exists today:

0. **Resolve P0 items** — documentation-only (C-1, C-2 write-ups above; C-3 and C-5 need an explicit decision from the user/architect before proceeding into Phase 4/9-relevant work). No code.
1. **`ProviderAdapter` contract + Capability Registry skeleton** (TAD §9-10) — nothing else in Phase 2 can start without this.
2. **Git Adapter** — lowest-risk first adapter: no external wire format, extends the already-tested `RepositoryManager`.
3. **Ingestion pipeline**: `ChangeSet` → `Evidence` → graph upsert, wiring existing Phase 1 pieces into one working, testable vertical slice for the Git Adapter before adding more providers.
4. **SCIP Adapter** — second provider; unlocks real Entity Resolution work (moot with only one provider).
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
