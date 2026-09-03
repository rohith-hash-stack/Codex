# Reproducible LLM Benchmark Specification

> Companion to [PROGRESS.md](../PROGRESS.md). Establishes the schema,
> versioning dimensions, and promotion criteria for a real-LLM Codex
> benchmark, ahead of the OpenAI `LLMGateway` integration checkpoint.

## 1. Four things that must never be conflated

| # | Name | What it actually is | Where it lives |
|---|---|---|---|
| 1 | **Historical conversation-level Sonnet evidence** | A prior claim of "50 queries × 5 repositories, Claude Sonnet" — no query list, repository set, ground truth, or model output for this exists anywhere in this repository or on this filesystem. Not reproducible, not diffable, not a baseline. | Nowhere (conversation only) |
| 2 | **Existing deterministic retrieval benchmarks** | The 24-query real-repository benchmark (`docs/architecture-conformance-audit.md` §II/§JJ, narrative only, no LLM involved) and the 5-repository symbol-extraction fidelity register (`docs/python-fidelity-gap-register.md`). Both measure retrieval/extraction correctness, never LLM answer quality. | `docs/architecture-conformance-audit.md`, `docs/python-fidelity-gap-register.md` |
| 3 | **Real OpenAI development-baseline attempt** (this checkpoint) | `codex.llm.openai_gateway.OpenAIGateway` (a real, concrete, vendor-backed `LLMGateway`) driven against the frozen `codex-self-dev-v0` corpus via `scripts/run_openai_benchmark.py`. Every reproducibility dimension recorded correctly; retrieval/scoring ran fully and correctly for all 4 cases. **The actual OpenAI request itself could not complete**: this sandboxed environment's egress proxy returns a hard `403` ("organization policy") on any `CONNECT` to `api.openai.com` — confirmed independently via `curl` before any code was written, not a code defect. See §6. | `src/codex/llm/openai_gateway.py`, `scripts/run_openai_benchmark.py` |
| 4 | **Future canonical LLM benchmark** | Does not exist yet. Requires: a real, completed OpenAI (or other vendor) response against this or a broader corpus (blocked in this environment per §6, not blocked architecturally), plus the promotion criteria in §4 below. | Not started |

**Development corpus recap** (unchanged from the prior checkpoint): `codex.benchmark.dev_corpus.build_development_corpus` — 4 cases, 3 real `Intent` categories (`FIND_CALLERS`, `FIND_TESTS`, `FIND_DEPENDENCIES`) plus one negative/abstention case, self-hosted against Codex's own real source via `AstCallsAdapter`/`PyprojectDependencyAdapter`. Ground truth derived mechanically from real graph relationships, frozen at commit `b01755b1f8bb1f8243360414e1bc736301d399be`. Untouched by this checkpoint (`git diff` on the fixture is empty).

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
| Model/provider identity (requested) | `model_id`, `provider` | Caller-supplied — for `OpenAIGateway`, read from `gateway.requested_model`/`gateway.provider` so the recorded value can never drift from what was actually configured |
| Model/provider identity (served) | `CaseRunResult.served_model` | The exact model string the provider's own response reports (`OpenAIGateway.last_response_metadata.served_model`) — never assumed to equal the requested `model_id`; `None` for gateways that don't expose it |
| Token usage | `CaseRunResult.usage_prompt_tokens`/`usage_completion_tokens`/`usage_total_tokens`/`llm_tokens` | The provider's own reported usage, when supplied — never estimated or fabricated |
| Raw output | `CaseRunResult.raw_model_output` | Captured verbatim, before any parsing (D10.2's "never re-parse prose" discipline extended to "always keep the pre-parse original") |
| Gateway-level failure | `CaseRunResult.error` / `generation_status=None` | Set when `LLMGateway.generate()` itself raised (missing credentials, transport/auth failure) rather than returning a `GenerationStatus`-representable result — captured per case, never silently dropped, never crashes the whole run |

`run_id` is a deterministic SHA-256 hash of every dimension above except the per-case ones — two runs sharing every dimension get the identical `run_id` (proven by `tests/test_benchmark_harness.py::test_run_corpus_is_reproducible_across_two_independent_runs`).

## 4. Promotion criteria: development → canonical

The development corpus must **not** be treated as canonical until:

1. Query/repository diversity has been explicitly reviewed (more than one real repository; ideally repositories the two current dependency-free providers, `AstCallsAdapter`/`PyprojectDependencyAdapter`, were not implicitly tuned against).
2. At least one additional real capability provider is wired in (e.g. `SCIPAdapter` for `SYMBOL_REFERENCE`/`IMPLEMENTATION`, or `GitAdapter` for `HISTORY`), unlocking `FIND_IMPLEMENTATIONS`, `CODE_LOOKUP`, `ARCHITECTURE_ANALYSIS`, `HISTORY_ANALYSIS` — categories the current corpus honestly cannot back.
3. A real `LLMGateway` has run against it and the D10 Verification Engine has been wired into the harness, making `CLAIM_VERIFICATION_ACCURACY`/`ABSTENTION_PRECISION` evaluable (both are `NOT_EVALUABLE` today — correctly, since no verification step runs in this milestone).
4. A human reviewer signs off — mirroring this project's own established "stop for review before the next phase" discipline (see every D1-D13 milestone in `PROGRESS.md`).

None of these four have happened yet. `CORPUS_VERSION = "codex-self-dev-v0"` reflects that honestly.

## 5. What the infrastructure milestone deliberately did not do

- No concrete OpenAI (or any vendor) `LLMGateway` implementation — `codex.benchmark.harness.run_corpus` took the D10 `LLMGateway` Protocol as a plain parameter; every test passed `tests/fake_llm_gateway.FakeLLMGateway`, a deterministic, in-memory stub. `tests/test_benchmark_no_external_calls.py` structurally proves no networking/provider-SDK import exists anywhere under `codex.benchmark`.
- No change to graph ontology, ingestion, SCIP/AST identity merging, high-fan-out resolution, query understanding, query-shaped traversal, REFERENCES traversal, or production retrieval behavior — every D1-D10 function this package calls is used exactly as already shipped.
- No wiring of the D10 Verification Engine into the harness (an explicit, deferred, non-blocking gap — see §4 item 3, still true after this checkpoint).

## 6. OpenAI Gateway architecture (this checkpoint)

`src/codex/llm/openai_gateway.py` — `OpenAIGateway` implements `LLMGateway.generate(request) -> LLMGenerationResult` exactly, so it drops into `run_corpus` unchanged. Design points:

- **Single hardcoded endpoint**: `CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"`, a module constant never assembled from configuration — structurally proven the only URL literal in the module (`tests/test_openai_gateway.py::test_gateway_never_imports_anthropic_and_the_only_url_literal_is_openai`).
- **No fallback, by construction**: the module imports nothing from any other provider's SDK, so there is no second code path `generate()` could switch to. `OpenAIAuthenticationError` (missing `Codex_open_API_key`, or an HTTP 401/403) is *raised*, never converted into a quiet degraded result.
- **Dependency-free**: stdlib `urllib.request`/`json` only — no `openai` or `requests` package added, matching D5/D7's own precedent of avoiding new dependencies where stdlib suffices.
- **Secret handling**: the key is read from `os.environ["Codex_open_API_key"]` inside `generate()` on every call, never cached, never logged. Every error path routes its message through `_redact` (strips `Bearer <token>` and `sk-...`-shaped substrings) before it is ever stored or raised.
- **`GenerationStatus` fidelity**: a genuine timeout (`request.latency_budget_ms`-scale, or the fixed 60s socket timeout) maps to the real `GenerationStatus.TIMEOUT` value; a transport/auth failure that never reached the model at all is deliberately *not* squeezed into one of D10's four closed enum values — it raises `OpenAIGatewayError`/`OpenAIAuthenticationError` instead, since none of `OK`/`MALFORMED_OUTPUT`/`TIMEOUT`/`BUDGET_EXCEEDED` honestly describes "never got a response."
- **Metadata side-channel**: `OpenAIGateway.last_response_metadata` (a `ResponseMetadata` with `served_model`/`usage_*`) is populated per call — an explicit, opt-in extension read via `getattr`, not a change to the `LLMGateway` Protocol or `LLMGenerationResult` itself.
- **Harness integration**: `run_corpus` now wraps `gateway.generate()` in a per-case `try/except`, so one case's gateway failure is captured into `CaseRunResult.error` (with `generation_status=None`) instead of aborting the whole corpus run or being silently dropped — proven by `tests/test_benchmark_harness.py::test_run_corpus_captures_a_gateway_failure_per_case_without_aborting_the_run`.

## 7. First two real-run attempts: blocked by environment network policy

Across two checkpoints, `curl -sS https://api.openai.com/v1/models` failed both times with `CONNECT tunnel failed, response 403`; the session's own egress-proxy status endpoint confirmed a hard **organization policy denial** (`api.openai.com` absent from the proxy's allowlist). Both times, `scripts/run_openai_benchmark.py` was run anyway against the real, frozen `codex-self-dev-v0` corpus: every reproducibility dimension recorded correctly, real D8/D9 retrieval ran for all 4 cases, real `PRECISION_AT_10`/`RECALL_AT_10`/`MRR` were computed — and all 4 cases' `CaseRunResult.error` read the proxy's own transport-failure message, `generation_status=None`, no OpenAI response ever obtained. No workaround was attempted (TLS verification/`HTTPS_PROXY` are never bypassed). This proved the pipeline was wired correctly end-to-end; the one link those two sessions couldn't complete was the outbound call itself.

## 8. Third attempt: real OpenAI baseline established

On a third checkpoint the same `curl` succeeded (`HTTP_STATUS:401` — a real response *from OpenAI*, not a proxy rejection — followed by a real `200` once the key was attached), confirming the proxy allowlist now permits `api.openai.com`. `scripts/run_openai_benchmark.py` was re-run, unmodified, against the same frozen `codex-self-dev-v0` corpus and the same pinned revision `b01755b1f8bb1f8243360414e1bc736301d399be`:

| query_id (prefix) | query_text | `generation_status` | `served_model` | `usage_total_tokens` |
|---|---|---|---|---|
| `a12d6237` | What calls build_canonical_id? | `MALFORMED_OUTPUT` | `gpt-4o-mini-2024-07-18` | 17827 |
| `054eb68a` | Which tests call compute_query_identity? | `OK` | `gpt-4o-mini-2024-07-18` | 5744 |
| `e4a6fad2` | What does codex depend on? | `OK` | `gpt-4o-mini-2024-07-18` | 6177 |
| `c5e94156` | What calls this_function_does_not_exist_anywhere_xyz? | `OK` | `gpt-4o-mini-2024-07-18` | 984 |

`run_id: run:a92e1d4c7b49b6a168ceed263a139231` (identical to both earlier, connectivity-blocked attempts — proving `run_id`'s determinism holds regardless of whether the call itself succeeds, since it hashes only the configuration dimensions, never the outcome). Requested model `gpt-4o-mini`; served model `gpt-4o-mini-2024-07-18` for every successful case — recorded from the response, never assumed. No Anthropic host was contacted (the proxy's own relay log names only `api.openai.com` throughout). No API key value appeared anywhere in the run's output (independently verified by direct string search against the real env var value).

**One real, diagnosable failure**: the `build_canonical_id` case — by far the largest real evidence package (27 real callers) — came back `MALFORMED_OUTPUT` at 17827 total tokens, an order of magnitude more than the other three cases (984-6177). The likely cause is `codex.llm.openai_gateway.DEFAULT_MAX_COMPLETION_TOKENS = 1024`, a fixed cap chosen deliberately *not* derived from `token_budget` (§6) — for a query needing to describe 27 entities, 1024 completion tokens plausibly truncates the JSON mid-object, producing invalid JSON that `StructuredAnswer.model_validate_json` correctly rejects as `MALFORMED_OUTPUT` rather than silently accepting a corrupted parse. This is a **prompt/completion-budget tuning question for the concrete Gateway, not a corpus, retrieval, or graph defect** — retrieval itself succeeded for this case (its `retrieval_context_version` and evidence collection were correct; only the completion was cut short). Per this checkpoint's own scope, no fix was applied — flagged for the next review, not silently patched.

`PRECISION_AT_10=0.5`, `RECALL_AT_10=0.778`, `MRR=0.5` (unchanged from both prior attempts — real retrieval is independent of whether the LLM call itself succeeds). `CLAIM_VERIFICATION_ACCURACY`/`ABSTENTION_PRECISION` remain `NOT_EVALUABLE` (no Verification Engine wiring yet, per §5).

**This is the first real OpenAI development baseline** — distinct from a canonical benchmark (§4's promotion criteria are still unmet) and from the historical, unrecoverable "50×5 Sonnet" conversation-level claim (§1).
