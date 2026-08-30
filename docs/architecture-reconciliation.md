# Codex — Architecture Reconciliation

> Phase A deliverable of the Architecture Reconciliation & Ordered Implementation directive. Resolves C-1, C-2, C-3 from `docs/architecture-conformance-audit.md`, maps DTD-01..05 onto TAD sections, dispositions all 17 ADR candidates, and records the canonical terminology binding on all future implementation. This document is the reconciliation record; the resolved decisions are then written into HLRD/TAD themselves (Phase B) and reflected back into the conformance audit (Phase C).

Reconciled: 2026-08-30. No production code (`src/`, `tests/`) changed as part of this document — resolutions below confirm existing code is already conformant (C-1, C-2) or don't yet apply to any existing code (C-3, since the Verification Engine isn't built).

---

## 1. Cross-document consistency sweep

Per the directive, the repository was searched for every occurrence of: `TESTS`, `TESTED_BY`, verification-state terms, `provider_versions`, `graph_version`, `EvidenceCohort`, `independence_group`, `failed_capabilities`, `partial_capabilities`, `raw_reference`, `QueryContract`, `RetrievalPlan`, `EvidencePackage`, `VerificationResult`, `Claim`, `ProviderAdapter`.

| Term | Canonical definition | Implementation | Tests | Contradiction? |
|---|---|---|---|---|
| `TESTS` / `TESTED_BY` | HLRD §16 says `TESTS`; TAD §14 says `TESTED_BY`; directions are inverse | `codex.ontology.relationships.RelationshipType.TESTED_BY` (TAD's name) | none dedicated | **Yes — C-1** |
| `provider_versions` / `graph_version` | TAD §19 gives a literal struct with named provider fields; TAD invariant #1 / §8 / §75 require provider independence | `codex.graph.version.GraphVersion.provider_versions: dict[str, str]` (generic) | `test_graph_store.py` | **Yes — C-2** (TAD self-contradicts; code already resolved it correctly but silently) |
| Verification states | HLRD §42-43 (4 values), TAD §50 (6 values), TAD §5 diagram (3 buckets) — three unreconciled enumerations | none (Verification Engine not built) | none | **Yes — C-3** |
| `EvidenceCohort` | TAD §17: provider, provider_version, snapshot_id, source_revision, observed_at, successful/failed/partial_capabilities[], coverage_status | `codex.evidence.model.EvidenceCohort` — all fields present, matches exactly | `test_evidence.py` | No — consistent |
| `independence_group` | TAD §16: default = `provider_default_family` (i.e., a value derived from the provider's own default family, not a literal constant) | `Evidence.effective_independence_group` → `f"provider_default:{provider}"` when unset | `test_evidence.py` (2 tests) | No — code is a concrete instantiation of TAD's symbolic default, not a deviation |
| `failed_capabilities` / `partial_capabilities` | TAD §17 | `EvidenceCohort.failed_capabilities`, `.partial_capabilities` | `failed_capabilities` exercised in `test_evidence_cohort_supports`; `partial_capabilities` has no dedicated assertion (minor test gap, not a contradiction) | No |
| `raw_reference` | TAD §16, §52: SHALL be a resolvable URI (`artifact://`, `s3://`, `file://`) | `Evidence.raw_reference: str \| None` — **unvalidated**, no scheme enforcement | none | No doc conflict — this is a tracked implementation gap (already P2 in the conformance audit), not a contradiction between documents |
| `QueryContract`, `RetrievalPlan`, `EvidencePackage` | Each defined exactly once in TAD (§27, part of §29-34, §42) with no competing HLRD-side shape | none exist in code | none | No |
| `VerificationResult` | **Does not exist as a named term anywhere** — TAD describes "Final Verification States" (§50) as an outcome, never names a `VerificationResult` type | none | none | No — naming gap for future Phase 9 work, not a contradiction |
| `Claim` | TAD §44-45 define `claims[]` shape and the `FACT/DERIVED/INFERENCE/UNKNOWN` classification once, consistently; HLRD refers to claims conceptually (§31, §43) without a competing schema | none exist in code | none | No |
| `ProviderAdapter` | TAD §8-9, one definition | none exists in code | none | No |

**Conclusion: exactly three genuine contradictions exist (C-1, C-2, C-3).** Everything else searched is either already consistent, or a tracked *implementation* gap rather than a *specification* conflict. No new contradiction IDs are introduced by this sweep.

---

## 2. C-1 resolution — relationship direction

**Canonical:** `TESTED_BY`. `production_code --TESTED_BY--> test`.

Do **not** add a separate `TESTS` relationship type to the persisted ontology. The inverse question ("what does this test test?") is answered by querying `TESTED_BY` edges by `object` instead of `subject` — this is a retrieval-time traversal direction, not a second ontology concept, and is consistent with TAD §14's own principle that only *forward* derived relationships get computed at query time rather than doubling up stored edge types.

**Code impact:** none. `codex.ontology.relationships.RelationshipType.TESTED_BY` already matches this exactly.

**Documentation impact:** HLRD §16's relationship list currently reads `TESTS` — amended to `TESTED_BY` (Phase B, below).

---

## 3. C-2 resolution — provider version representation

**Canonical:** `GraphVersion.provider_versions: dict[str, str]` — a generic provider→version mapping. The canonical graph version schema carries **no** hardcoded per-provider fields (no `scip_version`, `codeql_version`, `git_version`, `runtime_version`). Per-evidence provider identity/version already lives correctly on each `Evidence` record (TAD §15: `provider`, `provider_version`) — that's where provider-specific detail belongs; `GraphVersion` only needs to know *which* provider versions composed a given graph snapshot, generically.

This resolves an actual internal contradiction in the TAD itself: §19's illustrative struct named three specific providers directly in the schema, which conflicts with invariant #1 ("canonical graph is provider-independent"), §8 ("canonical engine must never depend directly on provider-specific schemas"), and the directive's own principle 2.2. The generic form is the only one consistent with those.

**Code impact:** none. `codex.graph.version.GraphVersion` already implements the generic form.

**Documentation impact:** TAD §19's struct is amended to the generic form, with a note that the earlier named-field version was illustrative and is superseded (Phase B, below).

---

## 4. C-3 resolution — verification state taxonomy

**One canonical internal enum**, per the directive:

```
VERIFIED             — all required claims supported by sufficient trusted evidence
PARTIALLY_VERIFIED   — some claims supported, others lack sufficient evidence
QUALIFIED            — answer is usable but carries explicit qualifications/limitations
DISPUTED             — credible evidence exists on both sides of a claim
INCONCLUSIVE         — evidence is insufficient to determine true or false
REJECTED             — the answer/claim failed an explicit verification/enforcement rule
```

This is TAD §50's six-value taxonomy, now made explicitly canonical and internal — it is what the (not-yet-built) Verification Engine, the LLM Gateway's response contract, telemetry, and answer contracts all use. It is **not** a second, competing enum alongside HLRD's four-value list.

HLRD §42-43's `FULLY_VERIFIED / PARTIALLY_VERIFIED / UNVERIFIED / CONTRADICTED` becomes a **presentation-layer mapping only**, applied when HLRD-level terminology is needed for reporting:

| Canonical (internal) | HLRD presentation label |
|---|---|
| `VERIFIED` | `FULLY_VERIFIED` |
| `PARTIALLY_VERIFIED` | `PARTIALLY_VERIFIED` |
| `QUALIFIED` | `PARTIALLY_VERIFIED` |
| `DISPUTED` | `CONTRADICTED` |
| `INCONCLUSIVE` | `UNVERIFIED` |
| `REJECTED` | `CONTRADICTED` |

TAD's own §5 pipeline diagram shows a third, coarser view — three terminal buckets (`VERIFIED / QUALIFIED / ABSTAIN`) at the very end of the pipeline. That's reconciled as a **routing view** derived from the same canonical enum, not a fourth competing taxonomy:

| Canonical (internal) | Pipeline routing bucket |
|---|---|
| `VERIFIED` | `VERIFIED` |
| `PARTIALLY_VERIFIED`, `QUALIFIED`, `DISPUTED`, `INCONCLUSIVE` | `QUALIFIED` |
| `REJECTED` | `ABSTAIN` |

**Code impact:** none today — the Verification Engine doesn't exist yet. **This resolution becomes binding the moment DTD-05/Phase 9 implementation starts**: the internal type must be the six-value enum, and both mapping tables above must be implemented as tested pure functions, not re-derived ad hoc.

**Documentation impact:** TAD §50 is amended to state explicitly that this is the canonical internal model; TAD §5's diagram gets a note pointing at the routing-bucket mapping; HLRD §42-43 is amended to mark its four-value list as a presentation mapping, with a pointer to TAD §50 (Phase B, below).

---

## 5. DTD mapping — DTD-01..05 as TAD section ranges

Per the directive (§7): DTD-01..05 remain logical sections embedded in TAD — **no separate documents are created.** Precise mapping, including where the pipeline's unlabeled LLM Reasoning stage sits:

| DTD | Name | TAD sections | Notes |
|---|---|---|---|
| DTD-01 | Canonical Graph / Evidence Model | §6 (Canonical Graph Engine, Evidence Store components), §11-21, §73-76 | Evidence Normalization through Historical Graph Storage, plus Provider Reconciliation, Truth Model, dependency rules, and invariants |
| DTD-02 | Query Understanding / Query Contract | §22-28 | Tier-0 detection through Session Context |
| DTD-03 | Planning / Retrieval Plan | §10, §29-34 | Capability Registry (a Planner dependency) plus Query Planner through Negative Query Planning |
| DTD-04 | Evidence Selection / Evidence Package | §35-42 | Retrieval Engine, Ranking, MSS, Dynamic Evidence Budget, through Evidence Package |
| — (unlabeled) | LLM Reasoning / LLM Gateway | §43-45 | Per TAD §5's own diagram, this sits **between** DTD-04 and DTD-05 but is not itself labeled as a DTD — it's the LLM Gateway, structured response, and claim model |
| DTD-05 | Verification / Answer | §46-51 | Verification Engine through Traceability |

If the repository later benefits from physically separating these into standalone files, that requires its own ADR first (directive §7) — not done here.

---

## 6. ADR disposition

Full reasoning already in `docs/architecture-conformance-audit.md` §D; disposition per the directive's two-bucket framing:

**Already decided by architecture — do not reopen:**
- Graph versioning semantics (TAD §19-21, §71) — closed.
- Historical reconstruction semantics (TAD §21) — closed.
- Canonical relationship semantics, now including §14's `TESTED_BY` direction post-C-1 — closed.
- Evidence independence default behavior (TAD §16) — closed.
- Verification boundary / LLM boundary (TAD §41, §61-62, invariants #6-9) — closed.
- Embeddings are optional, not mandatory, for V1 (TAD §3, §27, §34) — closed (only *which* embedding technology, if ever adopted, is open — P3).
- Runtime Adapter is optional, provider-specific, no universal instrumentation (HLRD §14, TAD §57, directive §33) — closed (only *which* runtime provider, if any, is open — P3).
- Ranking *formulas* — BM25, structural relevance, graph proximity, Jaccard constraint match (TAD §36-38) — closed (only ranking *infrastructure*, e.g. in-process vs. dedicated search service, is open).

**ADR-012 and ADR-013 — explicitly closed/superseded, per directive §8:**
- **ADR-012 (Graph Versioning Strategy)**: superseded by TAD §19-21, §71, now reinforced by this reconciliation's C-2 resolution. Not reopened. Any residual storage-engine question folds into ADR-001/ADR-002.
- **ADR-013 (Historical Graph Reconstruction)**: superseded by TAD §21. Not reopened. Residual storage-engine question folds into ADR-001/ADR-002.

**Genuine technical implementation choices — ADR required (14 of 17 candidates remain open):**
ADR-001 (graph storage), ADR-002 (evidence storage), ADR-003 (artifact storage), ADR-004 (SCIP integration strategy), ADR-005 (CodeQL integration strategy), ADR-006 (Sourcegraph/RepoGraph integration strategy), ADR-007 (SLM selection), ADR-008 (LLM selection), ADR-009 (embedding technology, if adopted — P3), ADR-010 (ranking infrastructure, narrower than its title), ADR-011 (cache technology), ADR-014 (runtime provider, if adopted — P3), ADR-015 (API protocol), ADR-016 (auth/authz), ADR-017 (deployment architecture).

None of these are drafted yet — none are needed before the next implementation steps (ProviderAdapter contract, Capability Registry, Git Adapter), which don't depend on any of them.

---

## 7. Unresolved questions

- **Sourcegraph Code Navigation API specifics** — `sourcegraph.com`'s detailed API docs remain blocked by this environment's egress proxy (a lightweight fetch of the `sourcegraph/sourcegraph-public-snapshot` README confirmed it exposes code nav + a GraphQL API, but not the schema). Blocks a fully-informed ADR-006; does not block Git/SCIP work (directive's provider order puts the repository-graph adapter at step 7, after Git and SCIP).
- **C-4 (confidence term overload)** — not addressed by this directive, remains open at low priority: `Evidence.confidence`, SLM confidence, `evidence_quality`, and Verification `V` are four distinct scoped values sharing overlapping terminology across TAD. Recommend a terminology appendix in TAD when Phase 4 (Query Understanding) or Phase 9 (Verification) work actually needs to disambiguate them in code.
- **OpenTelemetry, LangChain/LlamaIndex inspection** — both remain un-inspected (OpenTelemetry's docs site is also blocked by this environment's proxy; LangChain/LlamaIndex were correctly not pursued since the LLM Gateway isn't being built in this phase). Recorded honestly in `docs/resources.md` rather than claimed as adopted.

C-5 (whether DTD-01..05 should become standalone documents) is **not** left open — §5 above and the directive §7 resolve it: they stay embedded in TAD unless a future ADR decides otherwise.

---

## 8. Final canonical terminology

- `RelationshipType.TESTED_BY` — subject = code entity, object = test entity. `TESTS` is retired, not implemented.
- `GraphVersion.provider_versions: dict[str, str]` — generic; no per-provider named fields in the canonical schema.
- Verification: one canonical internal 6-value enum (`VERIFIED, PARTIALLY_VERIFIED, QUALIFIED, DISPUTED, INCONCLUSIVE, REJECTED`), plus two derived, tested mapping functions — one to HLRD's 4-value presentation label, one to TAD §5's 3-value pipeline routing bucket. Never re-derive these mappings ad hoc at a call site.
- "DTD-0x" always means a TAD section range (table in §5 above), never a separate file, unless a future ADR changes that.
- ADR-012 and ADR-013 are **closed/superseded**, not open ADRs — remove from any "pending ADRs" list; their residual technology question lives under ADR-001/ADR-002.

---

## 9. Implementation consequences

- **No source code changes are required by this reconciliation.** C-1 and C-2 confirm existing code (`codex.ontology.relationships`, `codex.graph.version`) was already conformant; C-3 doesn't yet apply to any code, since Verification Engine (DTD-05) hasn't been built.
- **Binding on future work:** when DTD-05/Phase 9 is eventually implemented, the six-value canonical enum and both mapping functions from §4 above are a concrete, testable requirement — not a design choice to be re-litigated at that time.
- **No new ADR is needed for DTD organization** — resolved directly by the directive (§7) and recorded in §5 above.
- **ADR-012/ADR-013 drop out of the "open ADRs" tracking** in `PROGRESS.md` and the conformance audit — they're closed, folded into ADR-001/002.
- **Ready to proceed to Phase D** (`ProviderAdapter` contract → Capability Registry → canonical ingestion boundary → Git Adapter → ingestion pipeline → SCIP Adapter) once this reconciliation and the Phase B/C document updates are reviewed — per the directive's final instruction, that implementation work has not started yet in this pass.
