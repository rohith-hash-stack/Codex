# Codex — Progress Log

> Status and open-items tracker. Companion to [docs/HLRD.md](docs/HLRD.md) and [docs/TAD.md](docs/TAD.md).

---

## Current Status

**Phase:** Phase 1 — Foundation (TAD §80), in progress. First working code has landed.

| Date (UTC) | Milestone |
|---|---|
| 2026-08-30 12:42 | Repository initialized (was empty, no prior commits) |
| 2026-08-30 12:47 | HLRD v1.0 committed — `docs/HLRD.md` (`4bd76c4`) |
| 2026-08-30 12:49 | TAD v1.0 committed — `docs/TAD.md` (`bce94fa`) |
| 2026-08-30 12:54 | `PROGRESS.md` added — `6038e2b` |
| 2026-08-30 12:59 | Project scaffolding + Phase 1 foundation code landed: pyproject/CI, `codex.ontology`, `codex.evidence`, `codex.graph` (in-memory), `codex.repository`, 17 passing tests, clean `ruff`/`mypy` |
| 2026-08-30 13:18 | Provider format research landed — `docs/research/provider-formats.md` (SCIP `scip.proto` schema, CodeQL SARIF v2.1.0 shape, both HLRD-referenced RepoGraph implementations) |
| 2026-08-30 13:30 | Architecture Conformance Audit landed — `docs/architecture-conformance-audit.md` (traceability matrix, ADR classification, 6 cross-document contradictions found, ordered implementation plan). No production code touched — audit-only, per directive. |

Both HLRD and TAD are marked **FROZEN / ARCHITECTURE BASELINE ESTABLISHED** by their authors. Phase 1 foundation code now exists under `src/codex/`; Phases 2–6 (providers, intelligence, reasoning, validation, hardening) have not started. **4 P0 items now block further code** — see below.

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
6. **Not yet started:** any ADR, provider adapters (SCIP/CodeQL/Git-evidence/Sourcegraph), Capability Registry, Entity Resolution, DTD-02..05 (query understanding/planning/retrieval/verification), LLM Gateway, benchmark corpus, or deployment work.

---

## Open Items

### P0 — architectural correctness blockers (new, from the audit)

| ID | Item | Needs |
|---|---|---|
| C-1 | `RelationshipType.TESTS` (HLRD §16) vs `TESTED_BY` (TAD §14) — inverted direction, not just naming | Recommend: keep code's `TESTED_BY`, update HLRD §16. Awaiting sign-off. |
| C-2 | `GraphVersion` generic `provider_versions` dict (code) vs TAD §19's literal named-provider struct | Recommend: keep code (matches provider-independence invariant), annotate TAD §19 as illustrative. Awaiting sign-off. |
| C-3 | Three unreconciled "final verification state" enumerations across HLRD §42-43 / TAD §50 / TAD §5 | **Genuinely open — needs an explicit decision before Phase 9 (Verification Engine) starts.** See audit §E. |
| C-5 | "DTD-01..05" referenced as if separate documents; none exist beyond TAD's inline sections | Decide: author standalone DTD docs, or accept TAD's inline sections as sufficient. |

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
| ADR-012 | Graph versioning strategy | **B — reclassified, already closed by TAD §19-21,71** | Do not reopen; residual tech question folds into ADR-001/002 |
| ADR-013 | Historical graph reconstruction | **B — reclassified, already closed by TAD §21** | Do not reopen; folds into ADR-001/002 |
| ADR-014 | Runtime adapter strategy | B (optional — already closed) + C (which provider, P3) | Policy closed; provider choice low priority |
| ADR-015 | API protocol | C — genuine open decision | Not started (candidates: REST/GraphQL/gRPC + **MCP**) |
| ADR-016 | Authentication/authorization | C — genuine open decision | Not started |
| ADR-017 | Deployment architecture | C — genuine open decision | Not started |

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

**Paused for architecture conformance audit before more code (done — see [docs/architecture-conformance-audit.md](architecture-conformance-audit.md)).** Per the audit's ordered plan (§H), once the P0 items above are resolved, next is: `ProviderAdapter` contract + Capability Registry skeleton → Git Adapter → ingestion pipeline (`ChangeSet` → `Evidence` → graph upsert) → SCIP Adapter → Entity Resolution/Reconciliation Engine → CodeQL Adapter → repository-graph Adapter. C-3 specifically must be resolved before Phase 9 (Verification Engine), not before Phase 2 — it doesn't block the next steps above.

---

## Change Log

- **2026-08-30 12:42–12:54** — Repo initialized; HLRD v1.0 and TAD v1.0 committed as frozen baseline docs; PROGRESS.md created to track open items.
- **2026-08-30 12:59** — Scaffolded the project (Python, `pyproject.toml`, ruff/mypy/pytest, GitHub Actions CI) and built Phase 1 foundation: `codex.ontology`, `codex.evidence`, `codex.graph` (NetworkX in-memory store), `codex.repository` (Repository Manager). 17 tests passing, lint/type-check clean.
- **2026-08-30 13:18** — Researched real provider formats before writing adapter code: SCIP `scip.proto` schema, CodeQL SARIF v2.1.0 output shape, and both RepoGraph reference implementations from the HLRD Resource Map. Findings written to `docs/research/provider-formats.md`; surfaces two new ADR-001/ADR-015 candidates (Kuzu, MCP) and one open gap (Sourcegraph API docs unreachable from this environment).
- **2026-08-30 13:30** — Ran a full architecture conformance audit per an explicit directive: re-read HLRD/TAD against the code, built a requirement traceability matrix, classified all 17 ADR candidates (2 reclassified as already-closed), and found 6 cross-document contradictions — most notably three inconsistent verification-state enumerations (C-3) that block Phase 9 until resolved. No production code changed this pass, by design.
