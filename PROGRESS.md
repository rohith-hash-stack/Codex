# Codex — Progress Log

> Status and open-items tracker. Companion to [docs/HLRD.md](docs/HLRD.md) and [docs/TAD.md](docs/TAD.md).

---

## Current Status

**Phase:** Architecture baseline — no implementation yet.

| Date (UTC) | Milestone |
|---|---|
| 2026-08-30 12:42 | Repository initialized (was empty, no prior commits) |
| 2026-08-30 12:47 | HLRD v1.0 committed — `docs/HLRD.md` (`4bd76c4`) |
| 2026-08-30 12:49 | TAD v1.0 committed — `docs/TAD.md` (`bce94fa`) |

Both documents are marked **FROZEN / ARCHITECTURE BASELINE ESTABLISHED** by their authors. No source code, schemas, or provider adapters exist yet — `src/` has not been created.

---

## Where We Are

1. **HLRD v1.0 (closed)** — problem statement, vision, provider strategy (SCIP + CodeQL + Git + one repo-intelligence provider, runtime optional), canonical graph ontology, entity resolution, evidence provenance/versioning, query understanding → planning → retrieval → ranking → MSS → coverage → context → LLM → verification pipeline, learning/feedback boundaries, 12 architectural invariants (INV-001..012), provisional V1 performance/success targets.
2. **TAD v1.0 (closed)** — 18 logical components, DTD-01..05 pipeline (canonical graph, query understanding, planner, evidence selection/MSS, verification), provider adapter contract, capability registry, evidence/cohort/status model, graph versioning + concurrency model (immutable versioned reads), ranking formulas, budget-aware planning, failure taxonomy, 15 frozen architectural invariants, dependency rules (including forbidden edges, e.g. Planner → LLM), testing strategy, implementation phases 1–6.
3. **Not yet started:** any ADR, any code, any storage/provider technology selection, benchmark corpus, or deployment work.

---

## Open Items

### ADRs (17 required, TAD §77) — none written

| ID | Decision | Status |
|---|---|---|
| ADR-001 | Graph storage technology | Not started |
| ADR-002 | Evidence storage technology | Not started |
| ADR-003 | Artifact storage technology | Not started |
| ADR-004 | SCIP integration strategy | Not started |
| ADR-005 | CodeQL integration strategy | Not started |
| ADR-006 | Sourcegraph/RepoGraph integration strategy | Not started |
| ADR-007 | SLM selection | Not started |
| ADR-008 | LLM selection | Not started |
| ADR-009 | Embedding strategy | Not started |
| ADR-010 | Search/ranking engine | Not started |
| ADR-011 | Cache technology | Not started |
| ADR-012 | Graph versioning strategy | Not started |
| ADR-013 | Historical graph reconstruction | Not started |
| ADR-014 | Runtime adapter strategy | Not started |
| ADR-015 | API protocol | Not started |
| ADR-016 | Authentication/authorization | Not started |
| ADR-017 | Deployment architecture | Not started |

### Research / Benchmark validation (TAD §84, marked 🟡)

- Benchmark repository corpus (small/medium/large, ground truth) — not started.
- Calibration of query-complexity weights, SLM confidence thresholds, ranking weights, completeness thresholds — not started (currently only initial/placeholder values in the TAD).
- Performance validation against V1 targets (p95 latency < 5s, graph update < 10min/1k files, LLM tokens/query < 4,000, Precision@10 > 0.80, Recall@10 > 0.75, factual accuracy > 0.85, traceability ≥ 90%) — not started.

### Implementation (TAD §80, Phase 1 not started)

- Phase 1 — Foundation: Repository Manager, canonical ontology, graph storage abstraction, evidence model, versioning.
- Phase 2 — Providers: SCIP, CodeQL, Git, Sourcegraph/RepoGraph adapters.
- Phase 3 — Intelligence: Capability Registry, Entity Resolution, DTD-02/03/04.
- Phase 4 — Reasoning: LLM Gateway, EvidencePackage, structured claims, DTD-05.
- Phase 5 — Validation: benchmark repos, ground truth, metrics, calibration, failure testing.
- Phase 6 — Production hardening: security, observability, scaling, caching, incremental indexing, version management, rollback.

None of Phase 1–6 has begun; there is no repository scaffolding (no language/toolchain chosen, no `src/` tree, no CI).

---

## Immediate Next Decision

Per open discussion: start with **ADRs**, **project scaffolding**, or a **vertical-slice prototype** (e.g. minimal Git adapter → evidence normalization → canonical graph stub) — not yet decided.

---

## Change Log

- **2026-08-30** — Repo initialized; HLRD v1.0 and TAD v1.0 committed as frozen baseline docs; this PROGRESS.md created to track open items.
