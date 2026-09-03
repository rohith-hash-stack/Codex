# API Hardening & Contract Audit

A focused audit of `src/codex/api/` after the API Integration Milestone (`docs/api-query-integration.md`) — not a redesign. Four genuine defects were found and fixed, each isolated with its own regression test. No deterministic graph/retrieval/identity/evidence/LLM/benchmark code was touched.

## Security incident (unrelated to code)

During the prior session, a diagnostic shell command (`${VAR:-fallback}`, which returns the variable's own value when set, not the fallback) printed the real `Codex_open_API_key` value into the session transcript. **That key must be treated as compromised: rotate/revoke it in the OpenAI dashboard and replace `Codex_open_API_key` with a new value.** No code change addresses this — it is a credential-hygiene action outside the codebase. This audit did not print, log, or persist the key at any point (verified by grep across `src/codex/api/`: the only match is a documentation reference to the variable name in `__main__.py`, no value).

## Findings and fixes

### 1. Repository status disagreed with job status during active ingestion (contract/lifecycle defect)

`get_repository_status` (backing `GET /repositories/{id}/status`, and — critically — `ask()`'s `RepositoryNotReadyError.phase`, the detail reported on a `/query` `409`) never consulted `self._active_jobs`, so it reported `REGISTERED` for the *entire duration* of a real ingestion run, while `get_job_status` correctly reported `INDEXING` for the identical repository. Reproduced live before fixing (`get_repository_status` → `REGISTERED`, `get_job_status` → `INDEXING`, same repository, same instant). **Fix**: `get_repository_status` now checks `self._active_jobs` (already the R1 singleflight's own source of truth) and reports `INDEXING` when a job is in flight. Verified end-to-end over real HTTP: a `/query` sent during active ingestion now returns `409` with `"...phase=INDEXING"` in the body, not the previous misleading `"...phase=REGISTERED"`.

### 2. Unbounded request body (security: resource exhaustion)

`_post_repositories`/`_post_query` read `Content-Length` bytes from the socket with no upper bound — a client could declare an arbitrary `Content-Length` and force unbounded memory allocation. **Fix**: a shared `_read_json_object_body` helper (replacing the two near-duplicated copies of this logic) rejects any declared body over `MAX_REQUEST_BODY_BYTES` (1 MiB) with `413`, before reading any of it into memory, and closes the connection rather than leaving it in an inconsistent state. Verified live via a raw socket: a request declaring a 5,000,000-byte body (never actually sent) is rejected immediately with `413`.

### 3. Non-object/wrong-typed request fields produced `500`s with internal error text (contract/security defect)

A syntactically valid JSON body that was not a JSON object (e.g. a bare string) reached `body.get(...)` unguarded and raised `AttributeError`, caught only by the last-resort handler → `500` with the raw Python exception message (`"'str' object has no attribute 'get'"`). Similarly, a truthy but wrong-typed required field (e.g. `"repository_id": 123`) reached `RepositoryManager`/deeper code and surfaced as `500` with a raw internal message (`"expected str, bytes or os.PathLike object, not int"`). Both reproduced live before fixing. **Fix**: `_read_json_object_body` rejects a non-dict body with a structured `400`; new `_require_body_str`/`_optional_body_str` helpers reject non-string required/optional fields with a structured `400`, replacing the old truthiness-only checks in both `_post_repositories` and `_post_query`.

### 4. Concurrent `/query` requests could read back a different request's LLM metadata (concurrency defect)

`OpenAIGateway` records per-call metadata (`served_model`, token usage, `finish_reason`) on `self.last_response_metadata` — an instance attribute, not a return value, explicitly documented as an "opt-in side channel." That module was only ever exercised sequentially before (the benchmark harness processes cases one at a time); `codex.api`'s `ThreadingHTTPServer` is the first caller to share one Gateway instance across concurrent threads. `generate()`'s blocking network call releases the GIL, opening a real window in which a second, concurrent `generate()` call can overwrite `last_response_metadata` between the first call returning and `CodexAPI.ask()` reading it back — silently attributing one request's served-model/usage metadata to a different request's response. **Fix**: `CodexAPI.ask()` now serializes exactly the `generate()` call and its immediate metadata read under a new `self._llm_lock` — retrieval and planning stay fully concurrent; only the LLM-call-plus-metadata-read pair is atomic. This fix stays entirely inside `codex.api.service`; `codex.llm.openai_gateway` was not touched. Proven with a deterministic (event-coordinated, not timing-dependent) regression test: reverting the lock makes the test fail reliably (thread A reads thread B's marker) in well under a second; with the lock, 5/5 repeated runs pass.

## Items reviewed, no defect found

- **Repository/path boundaries**: `RepositoryManager.register`/`clone` require the given path to already be (or become, via clone) a real git repository; no endpoint ever returns file *content*, only `SourceLocation` (path + line/col) — `EvidencePackage`'s own docstring already documents this scope decision. Given the server binds to `127.0.0.1` only (single local trusted user, per the original MVP design), this is an accepted scope boundary, not an escape.
- **VS Code extension boundary**: read `vscode-extension/src/*.ts` in full. `codexClient.ts` imports only Node's `http` module; `extension.ts`'s one `child_process` use is a fixed, hardcoded `python3 -m codex.api --port 0` spawn (never built from a server response or user input) to launch/own the local server process. The extension cannot reach graph storage or provider internals directly.
- **Repository singleflight (R1) / current-HEAD re-resolution (R2)**: neither `start_ingestion` nor `_resolve_fresh_metadata` was modified; all pre-existing R1/R2 regression tests (`tests/test_api_r1_r2_regression.py`) still pass unmodified.
- **Grounding integrity**: `CodexAPI.ask()` still calls exactly `understand_query` → `plan_query`/`execute_query` → `LLMGateway.generate`, in that order, with no file under `query_understanding/`, `planner/`, or `llm/` touched. `AskResponse.claims`/`evidence_context` carry the real `Claim`/`CanonicalRelationship`/`Evidence` fields (canonical IDs, relationship types, `EvidenceStatus`, confidence) unmodified; negative-query and ambiguity behavior (the real `negative_query_candidate`/`negative_query_result`, the "ambiguous target: N distinct entities" limitation string) still pass through verbatim — covered by the existing `tests/test_api_ask.py` suite, unaffected by this audit's changes.
- **`max_nodes`/`max_edges`/`limit` on `/symbols`/`/neighborhood`**: no upper bound on caller-supplied values. Flagged as a low-severity, pre-existing (not newly introduced) design tradeoff consistent with the documented single-local-user MVP threat model — left unfixed as out of scope for a "focused audit, not a redesign," distinct in kind from the unbounded-`Content-Length` defect (which is exploitable before any legitimate processing even begins, with no valid use case for an unbounded value).

## `GET /healthz`

Added: process-level liveness only, deliberately independent of repository or Gateway state — it never calls into `CodexAPI` at all, so it can never fail because a repository isn't ready or no Gateway is configured. `GET /repositories/{id}/status` already answers per-repository readiness; a real `/query` call already proves LLM reachability. `/healthz` answers only "is this server process up and dispatching requests."

## Regression

- Full suite: **1341/1341 passing** (was 1333; +8 new hardening tests in `tests/test_api_hardening_audit.py`).
- `ruff check src tests scripts`: clean.
- `mypy src`: clean, 91 source files.
- `tests/test_benchmark_canonical_corpus.py` / `tests/test_benchmark_expansion_corpus.py` / `tests/test_benchmark_dev_corpus.py`: 22/22 passing — both frozen corpora (`codex-canonical-v1`, `validation-expansion-v1`) still reconstruct byte-identically from real ingestion.
- Frozen artifacts confirmed byte-unchanged (`git diff --stat` empty): both corpus fixtures, both OpenAI run artifacts, all SCIP fixtures.
- No file under `src/codex/{provider,resolution,reconciliation,query_understanding,planner,graph,evidence,ontology,llm,ingestion,coverage,repository,registry,benchmark,evaluation}` was modified — only `src/codex/api/{server,service}.py` and one new test file.

## Remaining API work

- **ADR-016 (authentication/authorization)**: still open, explicitly out of scope for this audit. The server remains single-local-user, `127.0.0.1`-only, no auth layer.
- **ADR-017 (deployment architecture)**: still open, explicitly out of scope. No persistence, no multi-user, no remote-deployment hardening (e.g. TLS, rate limiting beyond the body-size cap added here) exists yet.
- Per-request model/provider override remains unimplemented by design (no Gateway this project ships supports it; see `docs/api-query-integration.md` §2).
- The `gpt-4o-mini` low-relationship-density fabrication limitation (`docs/broad-validation-report.md`) is unchanged and was not touched — confirmed out of scope per this audit's explicit instruction.
