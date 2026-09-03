# Reproducible LLM Benchmark Specification

> Companion to [PROGRESS.md](../PROGRESS.md). Establishes the schema,
> versioning dimensions, and promotion criteria for a real-LLM Codex
> benchmark, ahead of the OpenAI `LLMGateway` integration checkpoint.

## 1. Four things that must never be conflated

| # | Name | What it actually is | Where it lives |
|---|---|---|---|
| 1 | **Historical conversation-level Sonnet evidence** | A prior claim of "50 queries × 5 repositories, Claude Sonnet" — no query list, repository set, ground truth, or model output for this exists anywhere in this repository or on this filesystem. Not reproducible, not diffable, not a baseline. | Nowhere (conversation only) |
| 2 | **Existing deterministic retrieval benchmarks** | The 24-query real-repository benchmark (`docs/architecture-conformance-audit.md` §II/§JJ, narrative only, no LLM involved) and the 5-repository symbol-extraction fidelity register (`docs/python-fidelity-gap-register.md`). Both measure retrieval/extraction correctness, never LLM answer quality. | `docs/architecture-conformance-audit.md`, `docs/python-fidelity-gap-register.md` |
| 3 | **New development corpus** (this milestone) | `codex.benchmark.dev_corpus.build_development_corpus` — 4 cases, 3 real `Intent` categories (`FIND_CALLERS`, `FIND_TESTS`, `FIND_DEPENDENCIES`) plus one negative/abstention case, self-hosted against Codex's own real source via `AstCallsAdapter`/`PyprojectDependencyAdapter`. Ground truth derived mechanically from real graph relationships, frozen at commit `b01755b1f8bb1f8243360414e1bc736301d399be`. Proves the harness machinery end-to-end with a `FakeLLMGateway` stub — **no real LLM has been called against it.** | `src/codex/benchmark/`, `tests/fixtures/benchmark/codex_self_dev_corpus.json` |
| 4 | **Future canonical LLM baseline** | Does not exist yet. Requires: a concrete, vendor-backed `LLMGateway` implementation (next checkpoint), at least one real `ModelRunRecord` produced against a corpus whose scale/repository-diversity has been explicitly reviewed and promoted past "development" status (§4 below). | Not started |

## 2. Corpus schema

Reuses `codex.evaluation.models.{BenchmarkCorpus, BenchmarkCase, GroundTruthLabel}` verbatim (D13-C) — never duplicated. `codex.benchmark.models.DevelopmentCorpus` wraps a `BenchmarkCorpus` with one field it cannot itself carry (`codex.evaluation`'s own boundary tests forbid it from importing `codex.query_understanding`):

- `categories: dict[query_id, Intent]` — the query's real, Tier-0-resolved intent category.

Each `BenchmarkCase` already carries `repository_id`, `repository_revision` (an exact commit SHA, never a branch), and `query_text`. Each `GroundTruthLabel` carries `relevant_entity_ids` (canonical graph ids), `should_abstain`, and `expected_verification_status`.

**Ground truth is always derived mechanically from the pinned repository's real, committed graph relationships** (`GraphReader.get_relationships()`) — never from D9's ranked retrieval output (which would test D9 against itself) and never from an LLM. See `codex.benchmark.dev_corpus`'s per-case `ground_truth` closures for the exact real graph fact backing each case.

## 3. Reproducibility dimensions

A `codex.benchmark.models.ModelRunRecord` pins every dimension required for two runs to be honestly comparable:

| Dimension | Field | Source |
|---|---|---|
| Corpus identity | `corpus_version` | `BenchmarkCorpus.corpus_version` |
| Repository identity | `repository_id`, `repository_revision` | `RepositoryMetadata` |
| Request-construction recipe | `prompt_template_version` | `codex.benchmark.harness.PROMPT_TEMPLATE_VERSION` — versions the `LLMRequest` construction recipe (query text + real `EvidencePackage` + `StructuredAnswer.model_json_schema()` + contract budgets). Not a wire-level prompt string — no concrete Gateway exists yet to define one; a future Gateway's own prompt-construction version must be captured alongside this one. |
| Retrieval procedure | `context_construction_version` | `codex.benchmark.harness.CONTEXT_CONSTRUCTION_VERSION` — versions the D8→D9 call sequence (`understand_query` → `plan_query` → `execute_query`). |
| Retrieval data snapshot (per case) | `CaseRunResult.retrieval_context_version` | D9's own real `GraphVersion.version_id` (TAD §19's composite key: repository + revision + provider versions + schema/policy version). |
| Model/provider identity | `model_id`, `provider` | Caller-supplied |
| Raw output | `CaseRunResult.raw_model_output` | Captured verbatim, before any parsing (D10.2's "never re-parse prose" discipline extended to "always keep the pre-parse original") |

`run_id` is a deterministic SHA-256 hash of every dimension above except the per-case ones — two runs sharing every dimension get the identical `run_id` (proven by `tests/test_benchmark_harness.py::test_run_corpus_is_reproducible_across_two_independent_runs`).

## 4. Promotion criteria: development → canonical

The development corpus must **not** be treated as canonical until:

1. Query/repository diversity has been explicitly reviewed (more than one real repository; ideally repositories the two current dependency-free providers, `AstCallsAdapter`/`PyprojectDependencyAdapter`, were not implicitly tuned against).
2. At least one additional real capability provider is wired in (e.g. `SCIPAdapter` for `SYMBOL_REFERENCE`/`IMPLEMENTATION`, or `GitAdapter` for `HISTORY`), unlocking `FIND_IMPLEMENTATIONS`, `CODE_LOOKUP`, `ARCHITECTURE_ANALYSIS`, `HISTORY_ANALYSIS` — categories the current corpus honestly cannot back.
3. A real `LLMGateway` has run against it and the D10 Verification Engine has been wired into the harness, making `CLAIM_VERIFICATION_ACCURACY`/`ABSTENTION_PRECISION` evaluable (both are `NOT_EVALUABLE` today — correctly, since no verification step runs in this milestone).
4. A human reviewer signs off — mirroring this project's own established "stop for review before the next phase" discipline (see every D1-D13 milestone in `PROGRESS.md`).

None of these four have happened yet. `CORPUS_VERSION = "codex-self-dev-v0"` reflects that honestly.

## 5. What this milestone deliberately did not do

- No concrete OpenAI (or any vendor) `LLMGateway` implementation — `codex.benchmark.harness.run_corpus` takes the D10 `LLMGateway` Protocol as a plain parameter; every test passes `tests/fake_llm_gateway.FakeLLMGateway`, a deterministic, in-memory stub. `tests/test_benchmark_no_external_calls.py` structurally proves no networking/provider-SDK import exists anywhere under `codex.benchmark`.
- No change to graph ontology, ingestion, SCIP/AST identity merging, high-fan-out resolution, query understanding, query-shaped traversal, REFERENCES traversal, or production retrieval behavior — every D1-D10 function this package calls is used exactly as already shipped.
- No wiring of the D10 Verification Engine into the harness (an explicit, deferred, non-blocking gap — see §4 item 3).
