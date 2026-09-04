# UI Integration Milestone: Build First, Enhance Later

Extends the existing VS Code extension (`vscode-extension/`, `docs/vscode-nervous-system-architecture.md`) — improved, not replaced — into the first genuinely useful Codex experience:

    repository -> ingestion status -> ask question -> grounded answer -> inspect evidence -> explore graph

The Codex API (`docs/api-query-integration.md`, `docs/api-hardening-audit.md`) is the *only* boundary the UI crosses. No backend file was modified to build this milestone (see "Backend changes" below for the one exception: none were needed — a discovered gap was worked around client-side instead).

## 1. What was implemented

**`codex.askQuestion`** ("Codex: Ask a Question") — a new, primary Webview panel (`vscode-extension/src/askPanel.ts` + `askPanelView.ts`) implementing every required capability:

1. **Repository selection/registration and ingestion status.** The panel opens against the current workspace folder, pings `GET /healthz` and `GET /repositories/{id}/status` immediately, and exposes an "Index / Re-index" button (`POST /repositories` + polling `GET /jobs/{id}`) with live phase feedback. The "Ask" button is disabled until the repository reports `READY`.
2. **Query input using `POST /query`.** A text box + button (also Enter-to-submit) calls `CodexClient.ask()`.
3. **Grounded answer display.** `AskResponse.answer` rendered as the narrative.
4. **Clear separation of answer / evidence / status**, exactly as required — three visually distinct sections in that order: **Status** (badges for `AskStatus`, intent, plan status, provider/model/served-model/tokens/finish-reason, `query_id`/`run_id`), **Answer** (the narrative text alone), **Evidence** (claims table, entity chips, relationship table) — never merged into one undifferentiated block.
5. **Evidence inspection**: every claim's subject/object, every retrieved entity, every retrieved relationship (with `EvidenceStatus`/confidence), exactly as `EvidenceContextSummary` returned them.
6. **Interactive graph exploration through `GET /neighborhood`**, in two modes sharing one renderer: an **evidence snapshot** (the graph the LLM actually received, seed nodes highlighted) and an **explorer** (search a symbol → disambiguate if multiple matches → progressively expand via real `/neighborhood` calls on node click).
7. **Navigation from answer/evidence → graph node**: clicking a claim's subject/object chip or an entity chip highlights and scrolls to that node in the graph panel; clicking any graph node explores further from it.
8. **Handling of indexing, ambiguity, negative queries, timeout, malformed output, and API failures**, all sourced from real server signals, never invented:
   - *Indexing*: `askBtn` gated on repository phase; a `409` mid-request still surfaces a clear "still indexing" message (defense in depth, not the only gate).
   - *Ambiguity*: the real `"ambiguous target: N distinct entities..."` limitation string is surfaced verbatim with a ⚠ marker.
   - *Negative queries*: the real `negative_query_result=` signal (ℹ marker) plus an honest "Codex found no supporting relationships" note when `evidence_context.relationships` is empty.
   - *Timeout / malformed / budget-exceeded*: distinct `AskStatus` badges, each with a plain-language explanation of what happened.
   - *API failures*: every HTTP status the real server can return (`400`/`404`/`409`/`502`/`503`, plus transport failures) gets its own explanation (`CodexApiError.status`, new on the client).

The original two commands (**Codex: Index Repository**, **Codex: Explore Symbol Neighborhood**) are kept, unmodified in behavior — the Ask panel is additive, not a replacement, and its embedded Search/graph reuses the identical renderer `neighborhoodPanel.ts` was refactored to use (one graph-drawing implementation, not two).

## 2. API endpoints consumed

Every one of `docs/api-query-integration.md`'s endpoints: `POST /repositories`, `GET /jobs/{id}`, `GET /repositories/{id}/status`, `GET /symbols`, `GET /neighborhood`, `POST /query`, `GET /healthz`. `CodexClient` (`vscode-extension/src/codexClient.ts`) gained `ask()` and `healthz()`, plus full TypeScript types mirroring `codex.api.contracts` field-for-field (`AskResponse`, `Claim`, `EvidenceContextSummary`, `ModelMetadata`, `AskStatus`), and `CodexApiError` now carries the real HTTP status code so the UI can distinguish failure kinds without re-parsing message text.

## 3. Graph visualization approach and 3D readiness

**This milestone ships 2D only** (plain SVG, no charting/3D library dependency) — exactly as instructed: "a polished interactive 2D graph is acceptable... do not make 3D a blocker."

**The architecture is 3D-ready by construction**, via a strict three-layer separation, new this milestone:

```
API response (VisualizationGraph)
    -> graphModel.ts: buildGraphModel()   -- pure, lossless data projection (no position, no color)
    -> layout2D(model)                    -- {x, y} per node   (webviewAssets.ts, this milestone)
    -> render2D(model, positions)         -- draws SVG          (webviewAssets.ts, this milestone)
```

`buildGraphModel`/`buildGraphModelFromEvidence` (`graphModel.ts`) never invent a node, edge, or field — every `GraphModel`/`GraphModelNode`/`GraphModelEdge` field is copied verbatim from the server's own `VisualizationNode`/`VisualizationEdge`/`Claim`. A future 3D milestone adds a sibling `layout3D(model)` (producing `{x, y, z}`) and a `render3D(...)` (e.g. Three.js/WebGL), both consuming the *identical* `GraphModel` this milestone already produces and serializes into every graph-bearing Webview — no change to `graphModel.ts`, to how the extension host builds/sends graph data, or to any API call. This is documented directly in `webviewAssets.ts`'s own module docstring, not just here.

Delivered this milestone: search (symbol/file lookup, with disambiguation when multiple match), selection (click-to-select with a highlighted ring), expansion (progressive `/neighborhood` calls, never client-side fabrication), relationship display (edges with type/confidence tooltips + a table view), and evidence highlighting (the evidence-mode graph highlights the query's seed nodes; claim endpoints that don't resolve to a real evidence entity are visually marked "ungrounded" rather than silently linked).

## 4. A discovered backend gap (not fixed here — by design)

Building real navigation from an evidence entity to its `/neighborhood` surfaced a genuine, independently-reproducible retrieval gap: **searching `/neighborhood` (or `/symbols`) for an entity's own full `qualified_name` can resolve zero candidates**, when that `qualified_name` has `AstCallsAdapter`'s `"<file>::<symbol>"` shape (e.g. `"app.py::helper"`). Root cause, traced to `codex.planner.retrieval._resolve_one_target`: the `qualified_name` axis is narrowed to occurrences within just the *symbol* portion (`_symbol_path` strips the file-path prefix) — but the check still compares against the *unstripped* target string, so `"app.py::helper" in "helper"` is false. Reproduced directly against `CodexAPI.get_neighborhood`, with no UI code involved at all (see `docs/ui-integration-milestone.md`'s own verification trail below, and `integration.test.ts`'s dedicated regression test that locks in the gap's existence and the workaround's effect).

**Per this milestone's explicit boundary — "do NOT modify validated backend layers merely for UI convenience" — this was *not* fixed in `codex.planner.retrieval`.** Instead, `AskPanel.runExpand` (`askPanel.ts`) retries once with the entity's bare `name` (data already in hand, no new server call shape, no new resolution algorithm) whenever the primary `qualified_name` lookup resolves zero nodes. Verified live end-to-end: `GET /neighborhood?symbol=app.py::helper` → `0` nodes; the same panel's fallback → `GET /neighborhood?symbol=helper` → `2` nodes, `1` edge, correctly re-centered. This is flagged here explicitly as a **candidate for a real backend fix in a future, dedicated milestone** (narrow: `_resolve_one_target`'s exact-match check should compare `target` against `entity.qualified_name` directly, not only against `_symbol_path(entity.qualified_name)`) — not silently patched, not left unreported.

## 5. Test results

**TypeScript** (`npm test`, Node's built-in test runner — no new devDependency, matching this project's "dependency-free by design" precedent): **30/30 passing**.
- `graphModel.test.ts` (14): pure unit tests — lossless projection, claim-endpoint resolution (id/qualified_name/name), edge cross-referencing, never fabricating a match.
- `codexClient.test.ts` (9): against a fake in-process HTTP server — every `/query` request shape and every real error status (`400`/`409`/`502`/`503`) the server can return.
- `askPanelView.test.ts` (4): the generated Webview HTML/script is syntactically valid, properly escaped, and structurally well-formed.
- `integration.test.ts` (4, including the new discovered-gap regression test): a **real** `python3 -m codex.api` server, driven end to end — register → ingest → status → symbols → neighborhood → healthz, plus the qualified-name-vs-name gap. Skips itself gracefully if `codex.api` isn't importable. Deliberately never calls `/query` (would need a real `Codex_open_API_key`); that contract is fully covered by `codexClient.test.ts` against a fake server instead, consistent with this project's security discipline of never depending on a real key being present.

**Python backend** (unchanged, re-run per the directive): **1341/1341 passing**, `ruff check src tests scripts` clean, `mypy src` clean (91 source files) — zero regression, as expected since no backend file was touched.

**Live end-to-end smoke test** (manual, not part of the automated suite — same pattern as prior milestones' own live smoke tests, since it needs a real network call and a real API key): real `python3 -m codex.api` server, real temporary git repository, real `POST /query` against `gpt-4o-mini-2024-07-18` — confirmed a correct grounded answer, a correctly grounded claim, real token usage, and (after the fallback fix) correct evidence-to-graph navigation.

## 6. API changes

**None.** No file under `src/codex/api/` (or any other backend package) was modified. The discovered gap (§4) was worked around entirely client-side.

## 7. Remaining UX/3D work

- **3D rendering** itself: a `layout3D`/`render3D` pair, e.g. via Three.js loaded from the CDN allowlist this environment supports, or a vendored WebGL renderer — the data model is ready; this milestone deliberately did not build it.
- **The `_resolve_one_target` qualified_name gap** (§4): a real, narrow backend fix candidate for a dedicated follow-up milestone, not bundled into this one.
- **Search across `/symbols` result pagination** — today's search caps at 30 chips; a repository with more matches would benefit from a "show more" affordance.
- **Multi-repository support in one panel** — today one Ask panel is scoped to one workspace folder, matching the existing extension's own single-workspace assumption; a multi-root workspace would need a repository picker.
- **Persisted conversation history** — each `/query` response is shown fresh; no history/thread view exists yet.
- ADR-016 (auth/authz) and ADR-017 (deployment) remain untouched, unrelated to this milestone.
