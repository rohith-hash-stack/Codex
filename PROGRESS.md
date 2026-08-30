# Codex — Progress Log

> Status and open-items tracker. Companion to [docs/HLRD.md](docs/HLRD.md) and [docs/TAD.md](docs/TAD.md).

---

## Current Status

**Phase:** Phase D, D1 (ProviderAdapter Contract) complete and **approved**, including a follow-up clarification. Phase 1 Foundation (TAD §80) is done; D2 (Capability Registry) awaits explicit go-ahead.

| Date (UTC) | Milestone |
|---|---|
| 2026-08-30 12:42 | Repository initialized (was empty, no prior commits) |
| 2026-08-30 12:47 | HLRD v1.0 committed — `docs/HLRD.md` (`4bd76c4`) |
| 2026-08-30 12:49 | TAD v1.0 committed — `docs/TAD.md` (`bce94fa`) |
| 2026-08-30 12:54 | `PROGRESS.md` added — `6038e2b` |
| 2026-08-30 12:59 | Project scaffolding + Phase 1 foundation code landed: pyproject/CI, `codex.ontology`, `codex.evidence`, `codex.graph` (in-memory), `codex.repository`, 17 passing tests, clean `ruff`/`mypy` |
| 2026-08-30 13:18 | Provider format research landed — `docs/research/provider-formats.md` (SCIP `scip.proto` schema, CodeQL SARIF v2.1.0 shape, both HLRD-referenced RepoGraph implementations) |
| 2026-08-30 13:30 | Architecture Conformance Audit landed — `docs/architecture-conformance-audit.md` (traceability matrix, ADR classification, 6 cross-document contradictions found, ordered implementation plan). No production code touched — audit-only, per directive. |
| 2026-08-30 13:42 | **Architecture Reconciliation landed — all P0 items resolved.** `docs/architecture-reconciliation.md` (new); `docs/HLRD.md` and `docs/TAD.md` amended in place with the resolved decisions (not just recorded here); `docs/architecture-conformance-audit.md` updated to reflect resolution; `docs/resources.md` (new) — external resource ledger with 2 material license findings. Still no production code touched — `src/`/`tests/` unchanged, confirmed by a clean re-run (17 tests, ruff, mypy). |
| 2026-08-30 13:55 | `docs/policy-external-references.md` (new) — formal clean-room policy: study public specs/behavior, record in `docs/resources.md`, design and implement independently, never copy third-party code/tests, STOP for an explicit license decision if independent implementation isn't viable. Cross-linked from `docs/resources.md`. No code change — Phase 1 was already compliant by construction (written from spec text, not derived from any external repo). |
| 2026-08-30 14:09 | **D1 — ProviderAdapter Contract complete.** `codex.provider.capability` (`Capability` enum, 10 members, each traced to a spec source) and `codex.provider.contract` (`ProviderAdapter` protocol, `ProviderHealthStatus`, `ValidationResult`, `ProviderEligibility`/`EligibilityStatus`, `ProviderExtractionError`/`ProviderFailureReason`, `ExtractionResult`, `NormalizedEvidence`) — commits `aa6245d` (contract) and `aad1544` (21 behavioral tests via a test-only `FakeProviderAdapter`, not a real provider). Also closed part of a tracked gap: `Evidence.raw_reference` now validates its URI scheme. 38 tests total, 100% coverage on `codex.provider`, clean ruff/mypy. No Git/SCIP adapter yet — D2 (Capability Registry) is next; per the Phase D directive, implementation stops here for review. |
| 2026-08-30 14:16 | **D1 approved, with one clarification applied.** `health_status`/`availability` (flagged as underspecified in TAD §9 at the D1 report) resolved per explicit review instructions: `ProviderHealthStatus` is now `HEALTHY`/`DEGRADED`/`UNHEALTHY`/`UNKNOWN` (was `HEALTHY`/`DEGRADED`/`UNREACHABLE`); `availability` changed from a bare `bool` property to `availability(capability, repository) -> float` in `[0.0, 1.0]` — an adapter-reported fact per capability/repository, never aggregated into a selection decision here (that stays D2's job). 4 new tests prove the two signals are structurally independent (a HEALTHY provider can report 0.0 availability; an UNHEALTHY one can report 1.0). 41 tests total, still 100% coverage on `codex.provider`, clean ruff/mypy. No D2/provider-specific logic added. |

Both HLRD and TAD are marked **FROZEN / ARCHITECTURE BASELINE ESTABLISHED** by their authors, with a recorded amendment log covering the reconciled clauses. `src/codex/` now has Phase 1 Foundation plus D1's `ProviderAdapter` contract (approved); no concrete provider adapter (Git/SCIP) exists yet. **No P0 items remain. D1 approved — awaiting explicit go-ahead before D2 (Capability Registry).**

---

## Where We Are

1. **HLRD v1.0 (closed)** — problem statement, vision, provider strategy (SCIP + CodeQL + Git + one repo-intelligence provider, runtime optional), canonical graph ontology, entity resolution, evidence provenance/versioning, query understanding → planning → retrieval → ranking → MSS → coverage → context → LLM → verification pipeline, learning/feedback boundaries, 12 architectural invariants (INV-001..012), provisional V1 performance/success targets.
2. **TAD v1.0 (closed)** — 18 logical components, DTD-01..05 pipeline (canonical graph, query understanding, planner, evidence selection/MSS, verification), provider adapter contract, capability registry, evidence/cohort/status model, graph versioning + concurrency model (immutable versioned reads), ranking formulas, budget-aware planning, failure taxonomy, 15 frozen architectural invariants, dependency rules (including forbidden edges, e.g. Planner → LLM), testing strategy, implementation phases 1–6.
3. **Phase 1 — Foundation (TAD §80): in progress.** Scaffolded as a Python project (`pyproject.toml`, `ruff`/`mypy`/`pytest`, GitHub Actions CI) with four modules under `src/codex/`:
   - `ontology/` — `BaseEntityType`, `CommonRole`, `RelationshipType`, `RepositorySymbol`, `SourceLocation`, and `build_canonical_id()` (HLRD §16-18, TAD §12-14).
   - `evidence/` — `Evidence`, `EvidenceCohort`, `EvidenceStatus`, `CoverageStatus`, `CanonicalRelationship`, and an `InMemoryEvidenceStore` behind an `EvidenceStore` protocol (TAD §15-18).
   - `graph/` — `GraphVersion`, and `GraphReader`/`GraphStore` protocols with a NetworkX-backed `InMemoryGraphStore` (TAD §12, §19-20, §53, §62).
   - `repository/` — `RepositoryManager` (register/clone, HEAD revision, changed-file detection between revisions) and its models (TAD §7, §72).

   17 tests pass; `ruff` and `mypy` are clean. Storage/provider technology is still whatever the in-memory Phase 1 defaults are — no ADR has selected a real backend yet (deliberate, per TAD §77).
4. **Provider format research (done, see [docs/research/provider-formats.md](research/provider-formats.md)):** pulled the real SCIP protobuf schema, CodeQL's SARIF v2.1.0 output shape, and both HLRD-referenced RepoGraph reference implementations before writing any adapter code — so adapter contracts match reality instead of guesses. Notable findings: SCIP gives no direct call edges (must be derived from occurrence roles + enclosing ranges); CodeQL's single-entity `problem` findings don't fit the `subject/predicate/object` evidence shape as cleanly as its `path-problem` data-flow queries do (flagged for ADR-005, not resolved); `SillySerpent/Repograph` is architecturally the closest existing analog to Codex (Kuzu-backed graph store, git co-change coupling, static+runtime split, MCP server interface) and adds **Kuzu** as an ADR-001 storage candidate and **MCP** as an ADR-015 API candidate. Sourcegraph's own Code Navigation API docs were **not** reachable from this environment (egress-blocked) — still open before ADR-006.
5. **Architecture Conformance Audit (done, see [docs/architecture-conformance-audit.md](architecture-conformance-audit.md)):** re-read HLRD + TAD in full against the actual code/tests, built a requirement traceability matrix (4 IMPLEMENTED, 10 PARTIALLY_IMPLEMENTED, 9 NOT_IMPLEMENTED, 1 spec-level CONTRADICTED), classified all 17 TAD §77 ADR candidates (14 genuinely open, 2 — ADR-012/013 — actually already closed by TAD and should fold into ADR-001/002, not be reopened), and found 6 cross-document contradictions (**C-1** through **C-6**). Two of those (C-1, C-2) are places Phase 1 code already made a silent, undocumented choice that happened to be defensible — now recorded explicitly rather than left as drift. One (**C-3**) is serious: HLRD and TAD define **three different, unreconciled enumerations** of the final verification/answer state (4 values vs. 6 values vs. 3 pipeline buckets, no stated mapping) — this blocks Phase 9 (Verification Engine) until resolved and is flagged as needing an explicit human decision, not guessed at. Also flagged that "DTD-01..05" referenced throughout HLRD/TAD (and this directive) **do not exist as separate documents** — they're inline labels inside TAD only.
6. **Architecture Reconciliation (done, see [docs/architecture-reconciliation.md](architecture-reconciliation.md)):** resolved all three contradictions from the audit, with the resolutions written into the actual specification (not just tracked here). **C-1**: `TESTED_BY` is canonical (production code → test); HLRD §16 amended, no code change needed. **C-2**: generic `provider_versions: dict[str, str]` is canonical; TAD §19 amended to drop the named-provider struct, no code change needed. **C-3**: TAD §50's six-value taxonomy (`VERIFIED, PARTIALLY_VERIFIED, QUALIFIED, DISPUTED, INCONCLUSIVE, REJECTED`) is now explicitly the canonical *internal* verification model, with two tested mapping functions required when Phase 9 is built — one down to HLRD's four-value presentation label, one down to TAD §5's three-bucket pipeline routing view; both docs amended with the mapping tables. Also resolved: DTD-01..05 stay embedded in TAD as section ranges (mapping table recorded, no separate documents), and ADR-012/ADR-013 are now formally marked CLOSED/SUPERSEDED in TAD §77 itself. A new `docs/resources.md` external-resource ledger also surfaced two material license findings: **CodeQL's free tier doesn't cover private-repo analysis** (needs a GHAS license — affects ADR-005 and Capability Registry per-repo availability), and **`SillySerpent/Repograph` is AGPL-3.0** (confirms "reuse the idea, never the code" is a license requirement, not just a preference).
7. **Clean-room implementation policy adopted (see [docs/policy-external-references.md](policy-external-references.md)):** formalizes the process already followed for research so far — `Reference → Understand → Specify → Independently Implement → Test`, never `Reference → Copy → Modify → Integrate`. Binding on every future provider adapter: study public specs/behavior only, record the resource in `docs/resources.md`, design the Codex interface independently, never copy third-party source or tests, and STOP for an explicit license decision before incorporating any third-party code. Both license gates already on record (CodeQL private-repo restriction, `SillySerpent/Repograph`'s AGPL-3.0) are cited as worked examples of what this triggers.
8. **D1 — ProviderAdapter Contract (done and approved, see `codex.provider`):** the shared contract every future adapter (Git, SCIP, CodeQL, Sourcegraph/RepoGraph) will implement. `ProviderAdapter` is a `Protocol` (matching the existing `GraphStore`/`EvidenceStore` pattern) exposing identity/version/capabilities/health/availability/freshness plus `validate()`, `check_eligibility()`, `extract()`, `normalize()`. Deliberately scoped: eligibility is adapter-*reported* only — the Capability Registry's SUPPORTED/AVAILABLE/UNAVAILABLE/INELIGIBLE aggregation logic is D2's job, not built here. `extract()`/`normalize()` reuse Phase 1's `RepositoryMetadata`/`Evidence`/`EvidenceCohort`/`RepositorySymbol` directly. Proven with 25 behavioral tests against a test-only `FakeProviderAdapter` (not a real provider), including a follow-up clarification: `health_status` (`ProviderHealthStatus`: HEALTHY/DEGRADED/UNHEALTHY/UNKNOWN) is the provider's own operational condition; `availability(capability, repository) -> float [0,1]` is a separate, adapter-reported readiness signal per capability/repository, structurally independent of health (tests prove a HEALTHY provider can report 0.0 availability and vice versa). 100% coverage on `codex.provider`.
9. **Not yet started:** any ADR, provider adapters proper (SCIP/CodeQL/Git-evidence/Sourcegraph — D1 is the shared contract only), Capability Registry (D2, awaiting go-ahead), ingestion pipeline (D4), Entity Resolution, DTD-02..05 (query understanding/planning/retrieval/verification), LLM Gateway, benchmark corpus, or deployment work.

---

## Open Items

### P0 — architectural correctness blockers — ALL RESOLVED 2026-08-30

| ID | Item | Resolution |
|---|---|---|
| C-1 | `RelationshipType.TESTS` (HLRD §16) vs `TESTED_BY` (TAD §14) — inverted direction, not just naming | ✅ **Resolved** — `TESTED_BY` canonical. HLRD §16 amended. No code change needed. |
| C-2 | `GraphVersion` generic `provider_versions` dict (code) vs TAD §19's literal named-provider struct | ✅ **Resolved** — generic dict canonical. TAD §19 amended. No code change needed. |
| C-3 | Three unreconciled "final verification state" enumerations across HLRD §42-43 / TAD §50 / TAD §5 | ✅ **Resolved** — TAD §50's 6-value enum is canonical/internal; HLRD §42-43 and TAD §5 amended with tested mapping tables. Binding once Phase 9 (Verification Engine) is built. |
| C-5 | "DTD-01..05" referenced as if separate documents; none exist beyond TAD's inline sections | ✅ **Resolved** — stay embedded in TAD; explicit section-range mapping recorded in `docs/architecture-reconciliation.md` §5. |

Full resolutions: [`docs/architecture-reconciliation.md`](architecture-reconciliation.md).

### ADRs (17 candidates, TAD §77) — reclassified by the audit ([full reasoning](architecture-conformance-audit.md#d-adr-audit))

| ID | Decision | Class | Status |
|---|---|---|---|
| ADR-001 | Graph storage technology | C — genuine open decision | Not started (candidates: Neo4j, Dgraph, NetworkX, **Kuzu**) |
| ADR-002 | Evidence storage technology | C — genuine open decision | Not started |
| ADR-003 | Artifact storage technology | C — genuine open decision | Not started |
| ADR-004 | SCIP integration strategy | C — genuine open decision | Not started (wire format now known) |
| ADR-005 | CodeQL integration strategy | C — genuine open decision | Not started (evidence-shape mismatch found, see research notes) |
| ADR-006 | Sourcegraph/RepoGraph integration strategy | C — genuine open decision | Not started, blocked on Sourcegraph API research gap |
| ADR-007 | SLM selection | C + G — needs benchmark data | Not started |
| ADR-008 | LLM selection | C + G — needs benchmark data | Not started |
| ADR-009 | Embedding strategy | B (mandatory? No — already closed) + C (which tech, if ever) | P3, correctly deprioritized |
| ADR-010 | Search/ranking engine | B (formula fixed by TAD §36-38) + C (infra only) | Formula closed; infra choice open, low priority |
| ADR-011 | Cache technology | C — genuine open decision | Not started |
| ADR-012 | Graph versioning strategy | **B — CLOSED/SUPERSEDED, now formal in TAD §77 itself** | Do not reopen; residual tech question folds into ADR-001/002 |
| ADR-013 | Historical graph reconstruction | **B — CLOSED/SUPERSEDED, now formal in TAD §77 itself** | Do not reopen; folds into ADR-001/002 |
| ADR-014 | Runtime adapter strategy | B (optional — already closed) + C (which provider, P3) | Policy closed; provider choice low priority |
| ADR-015 | API protocol | C — genuine open decision | Not started (candidates: REST/GraphQL/gRPC + **MCP**) |
| ADR-016 | Authentication/authorization | C — genuine open decision | Not started |
| ADR-017 | Deployment architecture | C — genuine open decision | Not started |

### External resource ledger

[`docs/resources.md`](resources.md) — every external technology referenced by HLRD/TAD, with an explicit "what Codex adopts vs. does not" column, license notes, and inspection status. 5 resources actually inspected (SCIP, CodeQL, both RepoGraph implementations, Git), 7 honestly marked not-yet-inspected (Sourcegraph only partially, Tree-sitter, GraphRAG, TransE, LangChain, LlamaIndex, OpenTelemetry, scikit-learn) rather than assumed.

### Research / Benchmark validation (TAD §84, marked 🟡)

- Benchmark repository corpus (small/medium/large, ground truth) — not started.
- Calibration of query-complexity weights, SLM confidence thresholds, ranking weights, completeness thresholds — not started (currently only initial/placeholder values in the TAD).
- Performance validation against V1 targets (p95 latency < 5s, graph update < 10min/1k files, LLM tokens/query < 4,000, Precision@10 > 0.80, Recall@10 > 0.75, factual accuracy > 0.85, traceability ≥ 90%) — not started.

### Implementation (TAD §80)

- **Phase 1 — Foundation: in progress.** Done: canonical ontology, evidence model, graph storage abstraction (in-memory), `GraphVersion`, Repository Manager (register/clone, HEAD revision, changed-file diff). Remaining: Entity Resolution is not implemented (only the `build_canonical_id()` join key exists — no reconciliation across providers yet, since no providers exist yet), and no incremental-update pipeline wires `RepositoryManager.detect_changed_files()` into a graph rebuild.
- Phase 2 — Providers: SCIP, CodeQL, Git-evidence, Sourcegraph/RepoGraph adapters. Not started.
- Phase 3 — Intelligence: Capability Registry, Entity Resolution, DTD-02/03/04 (query understanding, planner, evidence selection/MSS). Not started.
- Phase 4 — Reasoning: LLM Gateway, EvidencePackage, structured claims, DTD-05 (verification). Not started.
- Phase 5 — Validation: benchmark repos, ground truth, metrics, calibration, failure testing. Not started.
- Phase 6 — Production hardening: security, observability, scaling, caching, incremental indexing, version management, rollback. Not started.

---

## Immediate Next Decision

**D1 (ProviderAdapter Contract) is approved,** including the `health_status`/`availability` clarification. Per explicit instruction, implementation halts here again for review — **D2 (Capability Registry) has not started and awaits explicit approval.** Once approved, the order continues: D2 Capability Registry → D3 Git Adapter → D4 Ingestion Pipeline → D5 SCIP Adapter, each phase gated on lint/typecheck/tests/traceability-update/PROGRESS-update before the next begins.

---

## Change Log

- **2026-08-30 12:42–12:54** — Repo initialized; HLRD v1.0 and TAD v1.0 committed as frozen baseline docs; PROGRESS.md created to track open items.
- **2026-08-30 12:59** — Scaffolded the project (Python, `pyproject.toml`, ruff/mypy/pytest, GitHub Actions CI) and built Phase 1 foundation: `codex.ontology`, `codex.evidence`, `codex.graph` (NetworkX in-memory store), `codex.repository` (Repository Manager). 17 tests passing, lint/type-check clean.
- **2026-08-30 13:18** — Researched real provider formats before writing adapter code: SCIP `scip.proto` schema, CodeQL SARIF v2.1.0 output shape, and both RepoGraph reference implementations from the HLRD Resource Map. Findings written to `docs/research/provider-formats.md`; surfaces two new ADR-001/ADR-015 candidates (Kuzu, MCP) and one open gap (Sourcegraph API docs unreachable from this environment).
- **2026-08-30 13:30** — Ran a full architecture conformance audit per an explicit directive: re-read HLRD/TAD against the code, built a requirement traceability matrix, classified all 17 ADR candidates (2 reclassified as already-closed), and found 6 cross-document contradictions — most notably three inconsistent verification-state enumerations (C-3) that block Phase 9 until resolved. No production code changed this pass, by design.
- **2026-08-30 13:42** — Reconciled all P0 findings per a follow-up directive with explicit resolutions: `TESTED_BY` canonical (C-1), generic `provider_versions` dict canonical (C-2), TAD §50's 6-value verification enum canonical internally with tested mapping functions to HLRD's 4-value presentation label and TAD §5's 3-bucket routing view (C-3), DTD-01..05 confirmed to stay embedded in TAD (C-5), ADR-012/013 formally closed in TAD §77. Wrote the resolutions into `docs/HLRD.md`/`docs/TAD.md` themselves (not just PROGRESS.md), added `docs/architecture-reconciliation.md` and `docs/resources.md` (which surfaced two license findings: CodeQL's free tier excludes private repos; `SillySerpent/Repograph` is AGPL-3.0), and updated `docs/architecture-conformance-audit.md` to reflect the resolved state. No production code changed — confirmed with a clean re-run (17 tests, ruff, mypy).
- **2026-08-30 13:55** — Adopted a formal clean-room implementation policy (`docs/policy-external-references.md`) per an explicit directive: study public specs/behavior, record in `docs/resources.md`, design and implement independently, never copy third-party code or tests, STOP for an explicit license decision if independent implementation isn't viable. Formalizes what Phase 1/research already did in practice; cross-linked from `docs/resources.md`. No code change.
- **2026-08-30 14:09** — Implemented D1 (ProviderAdapter Contract) per the Phase D directive, in two commits as instructed: `aa6245d` (`codex.provider.{capability,contract}` — the shared `ProviderAdapter` protocol, `Capability` enum, health/eligibility/extraction/normalization types) and `aad1544` (21 behavioral tests against a test-only `FakeProviderAdapter`, covering all 18 named behaviors plus 2 extra). Also validated `Evidence.raw_reference`'s URI scheme, closing part of a previously-tracked gap. Updated `docs/architecture-conformance-audit.md`'s traceability matrix, gap list, and ordered plan to mark D1 IMPLEMENTED. 38 tests total, 100% coverage on `codex.provider`, clean ruff/mypy. No Git/SCIP/Capability-Registry code — stopped here for review, per the directive's explicit instruction.
- **2026-08-30 14:16** — D1 approved; applied the one requested clarification and nothing else. `ProviderHealthStatus.UNREACHABLE` → `UNHEALTHY`, plus a new `UNKNOWN` member; `availability` changed from a bare `bool` property to `availability(capability, repository) -> float` in `[0.0, 1.0]`, an adapter-reported per-capability/repository fact, explicitly not an aggregated selection score (D2 still owns that). Updated `codex.provider.contract`, `tests/fake_provider_adapter.py`, and `tests/test_provider_contract.py` (4 new tests proving health and availability are never derived from one another), plus a new interpretation note in `docs/architecture-conformance-audit.md`. 41 tests total, 100% coverage on `codex.provider`, clean ruff/mypy. No D2/provider-specific logic added — stopped again for the next explicit approval.
