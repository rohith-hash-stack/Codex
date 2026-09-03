# Codex VS Code Extension + Nervous-System Visualization — Implementation Plan

**Status:** Approved scope change, first vertical slice implemented this cycle.
**Baseline preserved:** Python Identity Fidelity PASS, Python Retrieval Fidelity PASS, Python
Relationship Fidelity PASS, 125-query retrieval audit PASS (0 genuine defects), FND-1/FND-2/FND-3
resolved, full suite 1176 passed, ruff PASS, mypy PASS, as of `main@d0ac912`. Nothing in
`codex.ingestion`, `codex.provider`, `codex.resolution`, `codex.reconciliation`,
`codex.query_understanding`, or `codex.planner`'s existing retrieval/ranking logic is modified by
this change — see §12.

This document is the required pre-implementation plan (12 sections) plus the closing Architecture
Decision Record for the scope change described in the product direction: Codex's first
user-facing product is a VS Code extension, and Codex is to be treated architecturally as a
repository nervous system whose structure a user can progressively explore.

---

## 1. Updated Target Architecture

```
                    VS Code
                       │
                 Codex Extension            (TypeScript, thin client)
                       │
                 Codex API Layer            (new: src/codex/api/)
                       │
          Repository Intelligence           (existing, UNCHANGED)
                 ┌─────┴─────┐
                 │           │
              Graph       Retrieval
                 │           │
                 └─────┬─────┘
                       │
                  LLM / Agent               (existing codex.llm/codex.verification, deferred wiring)
```

This is TAD §70's own deployment diagram (API Gateway → Query Service → {Understanding, Planner,
Verification} → {Graph/Store, LLM}), already recommended as the target shape in the prior "API
Architecture Map" delivered this engagement (`docs/` audit, `main@d0ac912`), now narrowed to the
concrete first client. Nothing below the Codex API Layer changes. The only new production code is
the API Layer itself and its transport; the VS Code extension is a new, separate artifact
(`vscode-extension/`) outside `src/codex/`.

**Two flows, kept separate, exactly as TAD §71 requires:**

- **Lifecycle/ingestion (write path):** `Repository → RepositoryManager → IngestionPipeline →
  Provider Adapters → Evidence Store → new, immutable GraphVersion`.
- **Query/exploration (read path):** `VS Code → Codex API → Query Understanding / Planner /
  Retrieval → locked GraphVersion → EvidencePackage or VisualizationGraph`.

An active query locks one `graph_version`; ingestion publishes the next one independently
(TAD §71, already implemented, unchanged).

---

## 2. API Boundary Definition

New package: **`src/codex/api/`** — `contracts.py` (wire-shaped Pydantic models), `service.py`
(the `CodexAPI` facade, plus a minimal ingestion job tracker), `server.py` (stdlib-only local HTTP
JSON transport). This is the "clean API boundary between the Codex intelligence engine and the VS
Code extension" the directive requires.

**Rule enforced by construction:** `codex.api` imports `codex.graph.store.GraphReader`,
`codex.planner.retrieval.{resolve_targets, bounded_traversal}`, `codex.repository.manager`,
`codex.registry.registry`, and `codex.ingestion.pipeline` — it never imports a concrete storage
type (`InMemoryGraphStore`'s internal `networkx` object), never imports a `ProviderAdapter`
subclass, and never constructs a `RetrievalPlan`/calls `plan_query`/`execute_query` itself for the
neighborhood operation (that pipeline answers *NL queries*; neighborhood exploration is a distinct,
narrower graph operation — see §7). This mirrors exactly the boundary the prior API Architecture
Map recommended (§5 of that report: "every exposed graph operation must go through `GraphReader`,
never the storage-technology-specific object behind it") and the "Do NOT couple the VS Code
extension directly to graph storage or provider internals" constraint restated in this directive.

**Reused, not reimplemented:** candidate resolution reuses `codex.planner.retrieval.resolve_targets`
(the same deterministic, boundary-aligned name-matching already used by every NL query in
production); neighborhood traversal reuses `codex.planner.retrieval.bounded_traversal` (the same
pure, exported function `codex.evaluation.observer.observe_ranked_candidates` already calls a
second time from outside the main pipeline, for the same "replay the real retrieval engine, do not
reinvent it" reason). No retrieval/ranking/traversal logic is duplicated or forked.

---

## 3. API Contract Proposal

Conceptual contracts, backed by concrete Pydantic models in `codex.api.contracts` (implemented this
cycle — see §9 for exactly which operations are wired to a transport in the MVP):

- `RepositoryStatus{repository_id, phase, head_revision, graph_version_id, provider_summary[],
  error_detail}` — `phase ∈ {NOT_REGISTERED, CLONING, INDEXING, READY, FAILED}`.
- `IngestionJobHandle{job_id, repository_id}` / `IngestionJobStatus{job_id, phase, detail,
  result?: RepositoryStatus}` — the non-blocking progress mechanism.
- `VisualizationNode{id, name, qualified_name, node_type, roles[], language, source_location?,
  distance}` — `node_type` reuses `codex.ontology.entities.BaseEntityType` directly (no parallel
  enum invented).
- `VisualizationEdge{id, source, target, relationship_type, status, confidence, evidence_count}` —
  `relationship_type` reuses `codex.ontology.relationships.RelationshipType` directly; `status`/
  `confidence` are `CanonicalRelationship.status`/`.confidence`, already-computed Reconciliation
  output, not re-derived.
- `VisualizationGraph{center, nodes[], edges[], graph_version, requested_depth, truncated}` — the
  single shared response shape for **both** symbol/file lookup (nodes only, zero edges, depth 0)
  and neighborhood exploration (nodes + edges, depth ≥ 1). One contract, not two, per "prefer small,
  stable contracts over a large speculative API."

No implementation code is embedded in this section; the actual models are in `src/codex/api/
contracts.py`, reviewed in §9 alongside the code that produces them.

---

## 4. VS Code Extension Boundary

The extension (`vscode-extension/`, TypeScript, outside `src/codex/`) talks to Codex **only**
through HTTP JSON calls to the local API server (`codex.api.server`) — never imports Python code,
never reads the graph store's files directly, never assumes a storage technology. It holds no
retrieval, ranking, or identity logic of its own: every decision about *what* is related to *what*
is made server-side. The extension's own responsibilities are strictly presentation and user
interaction: sending a query/symbol/file, rendering the returned `VisualizationGraph`, and letting
the user request the next expansion (a new API call), never expanding client-side from cached data
it wasn't given. This satisfies "Do not bypass the intelligence/retrieval layer" and "Do not expose
graph storage directly to the extension" by construction — there is no code path in the extension
capable of doing either.

---

## 5. Visualization Data Contract

`VisualizationGraph` (§3) directly enumerates every concept the directive requires: `nodes`/`edges`
(explicit lists), node identity (`canonical_id` as `id`)/type (`BaseEntityType`), source location
(`SourceLocation`, already on every `RepositorySymbol`), relationship type (`RelationshipType`),
confidence/evidence (`CanonicalRelationship.confidence`/`.status`, already-reconciled), grouping/
hierarchy (`node_type`/`roles` let a client group by kind; `CONTAINS` edges, already a persisted
`RelationshipType`, let a client render the containment tree the directive's own
`Repository → subsystem → module → class → method` diagram describes — no new hierarchy field is
invented, containment is already a first-class relationship), and expansion state (`distance`
from the query center, plus `truncated` at the graph level — a client renders a node at the current
traversal frontier as expandable when its `distance == requested_depth`, without the server needing
a separate boolean per node). Nothing beyond this is exposed: no raw graph-store node/edge objects,
no `networkx` shapes, no provider-internal identifiers beyond `canonical_id` (which is already the
stable, provider-independent identity every part of this engagement's fidelity work has validated).

---

## 6. Repository Lifecycle Flow

```
VS Code: "Add repository" (path or URL)
   → POST /repositories  {path_or_url, revision?}
   → CodexAPI.register_repository()  [RepositoryManager.register/clone, synchronous, fast]
   → CodexAPI.start_ingestion(repository_id)  [spawns a background thread, returns immediately]
   → 202 {job_id}
VS Code polls: GET /jobs/{job_id}
   → {phase: INDEXING} ... {phase: READY, result: RepositoryStatus}
```

Ingestion itself (`IngestionPipeline.run`) is entirely unchanged; the job tracker
(`codex.api.service.IngestionJobTracker`, stdlib `threading` only, no new dependency) runs it off
the calling thread and exposes phase transitions, which is what makes this genuinely non-blocking
for the VS Code client rather than merely documented as a future concern.

---

## 7. Query/Retrieval Flow

The full NL-query pipeline (`understand_query → plan_query → execute_query → verify_claims →
build_final_answer`) is unchanged and **not** wired to the extension in this cycle's MVP (§9/§10) —
it is the natural next API once the vertical slice below is proven, and nothing in `codex.api`
forecloses it (a future `POST /query` endpoint would call this exact chain and map `EvidencePackage`
onto the same `VisualizationGraph` contract). This cycle wires the narrower, visualization-first
path the milestone actually requires:

```
VS Code: user searches/selects a symbol
   → GET /symbols?repository_id=...&query=...
   → CodexAPI.lookup_symbols()  [resolve_targets(graph, [query])]
   → VisualizationGraph{nodes: matches, edges: [], depth: 0}
```

---

## 8. Graph Exploration Flow

```
VS Code: user picks one node (or expands the frontier)
   → GET /neighborhood?repository_id=...&symbol=...&depth=1
   → CodexAPI.get_neighborhood()
        seeds = resolve_targets(graph, [symbol])          (candidate generation, reused)
        traversal = bounded_traversal(graph, seeds, ..., depth, max_nodes, max_edges)  (reused)
   → VisualizationGraph{nodes, edges, truncated, requested_depth}
VS Code renders nodes/edges; user clicks an expandable (distance == depth) node
   → GET /neighborhood?...&symbol=<that node's id>&depth=1   (progressive re-query, not client-side expansion)
```

This is the literal "give me the relevant neighborhood around symbol X" operation the directive
asks for, never "download the entire repository graph" — `max_nodes`/`max_edges` bound every call,
`truncated` tells the client honestly when the neighborhood was larger than the budget (the same
honesty discipline `EvidencePackage.partial`/`.limitations` already apply to NL queries).

---

## 9. Initial VS Code MVP Scope

**Implemented this cycle** (the "minimum coherent vertical slice"):

- `codex.api.contracts` — all models in §3.
- `codex.api.service.CodexAPI` — `register_repository`, `start_ingestion`/`get_job_status`
  (background-thread job tracker), `get_repository_status`, `lookup_symbols`, `get_neighborhood`.
- `codex.api.server` — stdlib `http.server`-based local JSON transport exposing exactly:
  `POST /repositories`, `GET /jobs/{job_id}`, `GET /repositories/{id}/status`, `GET /symbols`,
  `GET /neighborhood`. No web framework dependency added (constraint: "no unnecessary
  frameworks/dependencies") — `http.server`/`json` are stdlib.
- `vscode-extension/` — a minimal TypeScript extension: a command to point Codex at a workspace
  folder and start indexing, a symbol-search quick-pick backed by `/symbols`, and a Webview panel
  that calls `/neighborhood` and renders nodes/edges as a simple interactive SVG graph (no charting
  library dependency — vanilla DOM/SVG), with click-to-expand driving a new `/neighborhood` request
  per §8.
- Tests: `tests/test_api_contracts.py`, `tests/test_api_service.py`, `tests/test_api_server.py` —
  built on the existing `tests/planner_fixtures.py`/`DeterministicFakeAdapter` pattern already used
  by every planner test in this codebase, not a new fixture style.

**End-to-end milestone this proves:** VS Code → Codex API → Codex intelligence graph → bounded
neighborhood retrieval → visualization-ready nodes/edges → rendered and progressively explorable in
VS Code. See Final Deliverable for acceptance criteria.

---

## 10. Deferred Functionality

Explicitly **not** built this cycle (future architecture, preserved not implemented):

- `POST /query` (full NL query, verification-backed answers) — the chain exists and works
  end-to-end (this whole engagement's own validation proves it); wiring it to the API is the
  natural Phase 2, not attempted now to keep this slice minimal.
- Callers/callees/references/implements/inheritance as *dedicated* endpoints — all are already
  answerable via `get_neighborhood(..., relationship_types=[CALLS])` etc.; the MVP exposes the one
  general neighborhood operation rather than one endpoint per relationship type, per "prefer small,
  stable contracts."
- Dependency exploration beyond one hop of `DEPENDS_ON`/`IMPORTS` — same reasoning, reachable via
  the general neighborhood operation once relationship-type filtering is added to the extension UI.
- Semantic/conceptual queries, future LLM context retrieval — depend on `POST /query` (above).
- Session context wiring (`codex.query_understanding.session.SessionContext` remains built but
  unused, as documented in the prior API Architecture Map).
- Authentication/authorization, multi-user/multi-machine deployment, TLS — the MVP server binds to
  `127.0.0.1` only, single local user, matching "first VS Code milestone," not a production
  deployment; HLRD §50's security controls remain a flagged, unimplemented gap (unchanged from the
  prior audit).
- Real ingestion progress *percentage* (only coarse phase transitions are implemented) — a true
  percentage would require `IngestionPipeline` to report per-provider progress, which does not
  exist today and is out of scope for this slice (`IngestionPipeline` itself is not modified, per
  §12).
- Coordinator/Planning/QA agent clients (HLRD §60) — explicitly future, unblocked by this change
  but not implemented (no Planning/QA/Architecture Graphs exist to serve).
- VS Code marketplace packaging/publishing, telemetry, settings UI beyond the one workspace-folder
  command needed for the demo.

---

## 11. Required Tests

- **Contract tests** (`tests/test_api_contracts.py`): every `VisualizationNode`/`VisualizationEdge`
  field round-trips from a real `RepositorySymbol`/`CanonicalRelationship` (built via the existing
  `planner_fixtures.build_graph` helper) without inventing data; `node_type`/`relationship_type`
  are the real ontology enums, not strings.
- **Service tests** (`tests/test_api_service.py`), all against a real small deterministic graph
  built with `DeterministicFakeAdapter` (the same fixture every planner test already uses — no new
  fake provider invented):
  - `lookup_symbols` returns the real matching entities, zero edges, depth 0; empty query / no
    match returns an empty `VisualizationGraph`, never a fabricated node (mirrors the negative-query
    honesty discipline already validated in the 125-query audit).
  - `get_neighborhood` at depth 1 / depth 2 matches `bounded_traversal`'s own real output
    bit-for-bit (same "replay the real function" verification style `test_evaluation_integration.py`
    already uses for the observer).
  - `get_neighborhood` respects `max_nodes`/`max_edges` and reports `truncated=True` correctly.
  - `get_repository_status` reflects real `IngestionResult.provider_outcomes`, not invented data.
  - `IngestionJobTracker`: `start_ingestion` returns immediately (does not block on the real
    ingestion call), `get_job_status` observes `INDEXING → READY` (or `→ FAILED` on a provider
    error) deterministically.
  - **FND-1/FND-2/FND-3 non-regression**: a small nested-scope/redefinition fixture reproducing each
    fix's shape, run through `get_neighborhood`, confirms distinct entities remain distinct through
    this new path — the same guarantee already proven for the query pipeline, now proven for this
    one too, without re-touching `scip_adapter.py` itself.
- **Server tests** (`tests/test_api_server.py`): a real `http.server` instance on an ephemeral port,
  exercised via `http.client`/`urllib`, confirming each endpoint returns the same data
  `CodexAPI`'s methods return directly (the transport adds nothing, drops nothing) and that
  malformed/missing query parameters return a structured 4xx, never a stack trace.
- **Full-suite non-regression** (§12): `pytest`, `ruff`, `mypy` all still pass with the new package
  added, and the existing 1176 tests are unaffected (no existing file outside `src/codex/api/` and
  its tests is modified).

---

## 12. Migration/Integration Risks

- **Risk: accidentally duplicating retrieval logic.** Mitigated by construction — `codex.api`
  calls `resolve_targets`/`bounded_traversal` directly rather than reimplementing candidate
  resolution or traversal (§2, §8).
- **Risk: leaking storage internals through the API.** Mitigated by typing every `codex.api`
  function against `GraphReader` (Protocol), never `InMemoryGraphStore` or its `networkx` object
  (§2, §5).
- **Risk: the job tracker introducing nondeterminism into tests.** Mitigated by testing
  `IngestionJobTracker` with a real but fast `DeterministicFakeAdapter`-backed ingestion and
  polling `get_job_status` to a terminal phase with a bounded timeout, the same pattern already
  used for any other background-thread test in Python's stdlib testing idioms — no new test
  infrastructure invented.
- **Risk: VS Code extension coupling to a specific transport.** Mitigated by keeping the HTTP
  JSON shape identical to `codex.api.contracts`' Pydantic field names, so a future transport swap
  (stdio/MCP, per the prior API Architecture Map's Phase-4 discussion) would change only
  `codex/api/server.py` and the extension's one client module, never `contracts.py`/`service.py`.
- **Risk: regressing the validated Python fidelity baseline.** Mitigated structurally — no file
  under `src/codex/provider/`, `src/codex/resolution/`, `src/codex/reconciliation/`,
  `src/codex/query_understanding/`, or `src/codex/planner/` is modified by this change; the new
  `codex.api` package only *calls* already-validated, unmodified functions. Full suite/ruff/mypy
  are re-run after implementation (§11) as the final proof, not assumed.
- **Risk: scope creep into the deferred items in §10.** Mitigated by treating this document as the
  boundary — anything not listed in §9 is explicitly out of scope for this cycle regardless of how
  small it looks once the API layer exists.

---

## Codex VS Code + Nervous-System Architecture Decision

**What is being implemented now:** a new `src/codex/api/` package (contracts + a `CodexAPI` facade
+ a stdlib-only local HTTP JSON transport) exposing repository registration, non-blocking ingestion
status, symbol/file lookup, and bounded graph-neighborhood retrieval; and a minimal TypeScript
VS Code extension that calls it and renders the result as an explorable node/edge graph. This is
the smallest slice that proves the full `VS Code → Codex API → intelligence graph → bounded
neighborhood → visualization-ready nodes/edges → render/explore` architecture end-to-end.

**API boundary:** `codex.api` sits directly above the existing, unmodified `GraphReader` Protocol
and the existing, unmodified `resolve_targets`/`bounded_traversal` retrieval functions — never above
a concrete storage type, never above a `ProviderAdapter`. It is a facade, not a new intelligence
layer: no retrieval, ranking, or identity decision is made inside `codex.api` itself.

**VS Code boundary:** the extension speaks only HTTP JSON to `codex.api.server`; it holds no
retrieval/graph logic and never reads repository or graph-store files directly.

**Visualization boundary:** exactly one contract, `VisualizationGraph` (nodes/edges/identity/type/
source-location/relationship-type/confidence/distance/truncated), serves both lookup and
neighborhood-exploration responses; nothing beyond it (no raw graph-store shapes) ever crosses the
API boundary.

**What remains internal:** graph mutation, direct provider-adapter invocation, `ArtifactStore`
access, `LLMGateway` invocation (outbound-only), `CapabilityRegistry` internals beyond a narrow
provider-outcome summary, `codex.coverage`/`codex.reconciliation` as standalone endpoints — all per
the prior API Architecture Map's §5, unchanged by this cycle.

**What is deferred:** `POST /query` (full NL query + verification), dedicated
callers/callees/references/implements endpoints (subsumed by the one neighborhood operation),
session-context wiring, auth/multi-user deployment, real ingestion progress percentages,
Coordinator/agent clients — full list in §10.

**First end-to-end milestone:** register a small local repository through the extension, watch
ingestion status move to `READY` without blocking the VS Code UI, search for a symbol, open its
neighborhood in the Webview, and click an expandable node to progressively reveal more of the
graph — all backed by real `IngestionPipeline`/`GraphReader`/retrieval output, none of it mocked in
the extension.

**Acceptance criteria:**
1. `pytest`/`ruff`/`mypy` all pass, including the new `codex.api` tests, with the existing 1176
   tests unmodified and unaffected.
2. `get_neighborhood`'s output is provably identical to a direct `resolve_targets`+
   `bounded_traversal` call on the same graph (no divergent logic).
3. The HTTP server round-trips every `codex.api.contracts` field without loss or invention.
4. The VS Code extension can register a repository, poll job status to `READY`, look up a symbol,
   and render its neighborhood, calling only the four documented HTTP endpoints — no direct Python
   or graph-store access from TypeScript.
5. No file under `src/codex/{provider,resolution,reconciliation,query_understanding,planner,
   ingestion,ontology,evidence,graph}` is modified — the Python fidelity baseline is untouched by
   construction, confirmed by `git diff --stat` against `main@d0ac912` showing only additions under
   `src/codex/api/`, `tests/`, `vscode-extension/`, and this document.
