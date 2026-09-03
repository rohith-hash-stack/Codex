# API Integration Milestone: `POST /query`

Extends `codex.api` (`docs/vscode-nervous-system-architecture.md`) from lifecycle + lookup +
neighborhood into the real Codex user flow:

    repository -> query -> intent/evidence requirements -> targeted graph retrieval
        -> minimal sufficient grounded context -> LLM -> grounded answer

This document records the design decisions this milestone made, so a future change can see the
reasoning rather than re-deriving it. It does not restate `docs/vscode-nervous-system-
architecture.md`'s own boundary (still true for `/repositories`, `/symbols`, `/neighborhood`);
it only covers what `/query` adds.

## 1. Pipeline wiring -- no parallel logic

`CodexAPI.ask()` calls the real, unmodified pipeline in exactly this order and nothing else:

    understand_query (D8)  ->  plan_query / execute_query (D9)  ->  LLMGateway.generate (D10)

No file under `src/codex/query_understanding/`, `src/codex/planner/`, `src/codex/llm/gateway.py`,
`src/codex/llm/schema.py`, or `src/codex/llm/openai_gateway.py` was modified by this milestone.
`ask()` reuses `codex.planner.cache.compute_query_identity` for `AskResponse.query_id` -- the same
deterministic content hash `codex.benchmark.harness` already uses for the identical purpose.

## 2. Contracts (`codex.api.contracts`)

`AskRequest` / `AskResponse` / `EvidenceContextSummary` / `ModelMetadata` / `AskStatus` are new.
`EvidenceContextSummary` reuses the existing `VisualizationNode`/`VisualizationEdge`/
`GraphVersionRef` shapes `/neighborhood` already returns rather than inventing a parallel evidence
representation -- the real `EvidencePackage` (TAD §42) the LLM received, projected through the
same lens. `AskResponse.claims` carries `codex.llm.schema.Claim` directly, unmodified.

**No `model`/`provider` override field on `AskRequest`.** No `LLMGateway` this project ships
supports per-request reconfiguration (`OpenAIGateway.model` is fixed at construction) --
accepting such a field would silently promise behavior nothing implements. What actually served
the request is always reported back on `AskResponse.model` (`ModelMetadata`) instead: this is the
"optional model/provider configuration where already supported" requirement's honest answer for
what is supported today.

## 3. `AskStatus` -- what's in-band data vs. what's a raised error

D8's `UnderstandingStatus` and D10's `GenerationStatus` already establish this project's
discipline: represent legitimate outcomes as *data*, never as exceptions. `AskStatus` extends that
discipline one layer up to the query/ask boundary:

| `AskStatus`               | Source                                                     |
|----------------------------|-------------------------------------------------------------|
| `OK`                       | `GenerationStatus.OK`                                       |
| `UNDERSTANDING_INCOMPLETE` | `understand_query` returned `SLM_UNAVAILABLE`/`LLM_ESCALATION_REQUIRED` -- no plan/evidence/LLM call was attempted |
| `MALFORMED_OUTPUT`         | `GenerationStatus.MALFORMED_OUTPUT`                          |
| `LLM_TIMEOUT`               | `GenerationStatus.TIMEOUT`                                   |
| `LLM_BUDGET_EXCEEDED`      | `GenerationStatus.BUDGET_EXCEEDED`                            |

Everything in this table is returned inside a normal `200 OK` `AskResponse` -- never raised --
because the Gateway Protocol itself already represents these as data, and the API layer's job is
to pass that through honestly, not re-wrap it as an HTTP error.

## 4. What *is* raised, and where it's classified

A handful of outcomes are genuine preconditions or upstream failures, not legitimate query
results, and are raised as exceptions from `CodexAPI.ask()`:

| Exception                    | HTTP status | Meaning                                                |
|-------------------------------|-------------|---------------------------------------------------------|
| `RepositoryNotFoundError`     | 404         | `repository_id` was never registered (existing, reused) |
| `RepositoryNotReadyError`     | 409         | registered, but no successful ingestion yet              |
| `LLMNotConfiguredError`       | 503         | this `CodexAPI` instance has no `LLMGateway` configured   |
| *(malformed request body)*    | 400         | missing `repository_id`/`query_text`, non-integer budget |
| `OpenAIAuthenticationError`   | 502         | the configured Gateway's own auth/credential failure       |
| `OpenAIGatewayError`          | 502         | the configured Gateway's own other transport/HTTP failure |

**Key boundary decision**: `CodexAPI.ask()` (in `codex.api.service`) does **not** catch a Gateway
exception -- it lets it propagate exactly like `RepositoryManager`/`IngestionPipeline` failures
already do elsewhere in that same class (`GitRevisionResolutionError` is the existing precedent).
`codex.api.server` -- the transport/wiring layer, which already imports concrete provider
adapters in `codex.api.__main__` -- is where `OpenAIAuthenticationError`/`OpenAIGatewayError` get
imported and mapped to HTTP `502`. This keeps `codex.api.service`'s core orchestration logic free
of any concrete-Gateway import (matching D10's Protocol-only design), while still giving `/query`
real, distinguishable HTTP semantics for the one concrete Gateway this project ships. A future
Gateway's own exception types would be added the same way, at the same layer, without touching
`service.py`.

`LLMNotConfiguredError` is checked before repository readiness in `ask()` -- a missing Gateway is
a deployment/configuration precondition, independent of which repository was asked about.

## 5. Grounding integrity (requirement 3)

`ask()` performs no retrieval-side compensation for weak evidence: it does not lower a threshold,
widen a traversal, or suppress/alter a `StructuredAnswer`'s claims to make an answer look better
grounded. The known `gpt-4o-mini` low-relationship-density fabrication limitation
(`docs/broad-validation-report.md`) is therefore reachable through `/query` exactly as it is
through the benchmark harness -- unchanged, and deliberately not special-cased here. Negative and
ambiguous queries retain their existing D9 behavior verbatim (`negative_query_candidate`/
`negative_query_result`, the "ambiguous target: N distinct entities match this query" limitation
string) -- surfaced in `AskResponse.evidence_context.limitations`, never rewritten.

## 6. Health/readiness (requirement 6)

No new health/readiness endpoint was added this milestone. `GET /repositories/{id}/status`
already answers "is this repository ready to be queried" per-repository (reused by `ask()` itself
via `_ingestion_result_for`); a process-level `/healthz` is a deployment-target concern properly
scoped to ADR-017 (deployment architecture), still an open, separate milestone per `PROGRESS.md`.
Adding one now, ahead of a real deployment target, would be speculative.

## 7. Validation performed

- All pre-existing 41 API tests (`test_api_contracts.py`, `test_api_server.py`,
  `test_api_service.py`, `test_api_r1_r2_regression.py`) pass unmodified.
- 17 new tests: `tests/test_api_ask.py` (11, service-level, `FakeLLMGateway`/scripted exceptions)
  and 6 new cases appended to `tests/test_api_server.py` (HTTP-level, including a `502`/`Traceback`
  check). Full suite: 1333/1333 passing (was 1316), ruff clean, mypy clean (91 source files).
- A real, live smoke test (`api.ask()` through the real `OpenAIGateway`, a real temporary git
  repository, `GitAdapter`+`AstCallsAdapter`) confirmed the full chain end-to-end: real
  `gpt-4o-mini-2024-07-18` response, `finish_reason: stop`, one correct grounded claim
  (`main CALLS helper`), deterministic `query_id`/`run_id`. Not part of the automated suite (it
  requires real network + a real API key) -- a one-off proof the wiring is genuine, matching the
  pattern already established for `codex-canonical-v1`'s own first real run.
- `codex-canonical-v1`, `validation-expansion-v1`, `codex-self-dev-v0`, and every SCIP fixture
  confirmed byte-unchanged (`git diff --stat` empty). No file under `src/codex/provider/`,
  `src/codex/resolution/`, `src/codex/reconciliation/`, `src/codex/query_understanding/`,
  `src/codex/planner/`, `src/codex/graph/`, `src/codex/evidence/`, `src/codex/ontology/`,
  `src/codex/llm/`, or `src/codex/benchmark/` was modified.
