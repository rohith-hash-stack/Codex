# Codex Repository Nervous System (VS Code extension)

The first user-facing Codex client. As of the UI Integration Milestone
(`../docs/ui-integration-milestone.md`) and the 3D Repository Intelligence
Graph Milestone (`../docs/3d-repository-intelligence-graph.md`), its primary
workflow is:

```
repository -> ingestion status -> ask a question -> grounded answer
        -> inspect deterministic evidence -> explore the graph
```

speaking only to the local Codex API over HTTP:

```
VS Code -> Codex API (local HTTP) -> Codex intelligence graph
        -> POST /query (D8 -> D9 -> D10 pipeline) -> grounded answer + evidence
        -> GET /neighborhood -> bounded, visualization-ready nodes/edges
        -> rendered/explorable in a VS Code Webview
```

See `../docs/vscode-nervous-system-architecture.md` for the original MVP
design (repository lifecycle + lookup + neighborhood) and
`../docs/ui-integration-milestone.md` for what this milestone added on top of
it.

## Prerequisites

- `python3` on `PATH` with the `codex` package installed (`pip install -e .`
  from the repository root — see the top-level `README.md`).
- Node.js + npm, for compiling the extension itself.

## Build

```
cd vscode-extension
npm install
npm run compile
```

Then run the extension in a VS Code Extension Development Host (`F5` from this
folder in VS Code, or `code --extensionDevelopmentPath=.` from a full VS Code
install).

## Commands

- **Codex: Ask a Question** (primary) — opens one panel with repository
  status/indexing, a natural-language query box (`POST /query`), a clearly
  separated answer/evidence/status view, and an embedded, interactive graph
  (evidence snapshot or a `/neighborhood`-driven explorer), rendered in **3D**
  by default (Three.js — pan/orbit/zoom, node selection, hover info, a
  relationship-type filter, an explicit large-graph choice above 250 nodes)
  with an automatic, and manually-togglable (`3D`/`2D` button in the graph
  toolbar), fallback to the original 2D SVG renderer whenever WebGL isn't
  available. See `../docs/ui-integration-milestone.md` for the original
  walkthrough and `../docs/3d-repository-intelligence-graph.md` for the 3D
  renderer's own architecture and capabilities.
- **Codex: Index Repository** — registers the current workspace folder with
  Codex and starts ingestion (Git history + Python AST-derived symbols/calls)
  in the background, showing progress without blocking the editor. The Ask
  panel also exposes this as its own "Index / Re-index" button.
- **Codex: Explore Symbol Neighborhood** — looks up a symbol/file by name and
  opens its bounded neighborhood (callers, callees, references, containment)
  in its own interactive graph view. Click a frontier node to progressively
  reveal more of the graph — never the whole repository graph at once. The
  Ask panel's own embedded "Search" box covers the same flow inline.

## Testing

```
npm test
```

Runs (Node's built-in test runner, no new devDependency):
- `graphModel.test.ts` — pure unit tests for the renderer-agnostic graph data
  model (`graphModel.ts`), including `classifyNodeKind`'s mapping from the
  real `BaseEntityType` enum.
- `layout3D.test.ts` — pure unit tests for the 3D layout algorithm, the
  explicit large-graph selection/filtering logic (`selectGraphForRender`),
  and the 2D/3D renderer fallback decision (`decideRenderMode`) — all with
  zero Three.js/DOM dependency, so they run under plain Node.
- `codexClient.test.ts` — the HTTP client against a fake in-process server,
  covering every `POST /query` outcome and error status.
- `askPanelView.test.ts` — the generated Webview HTML/script is well-formed
  JavaScript, including the new 3D `<script type="importmap">`/
  `<script type="module">` tags.
- `integration.test.ts` — a **real** `python3 -m codex.api` server, driven
  end to end (register/ingest/status/symbols/neighborhood/healthz). Skips
  itself if `codex.api` isn't importable in the current environment. Does
  not call `/query` (that needs a real `Codex_open_API_key`); the client-side
  `/query` contract is covered by `codexClient.test.ts` instead.

`media/graph3d.mjs` (the actual Three.js scene-construction/interaction
code) is deliberately *not* run by `node --test` — it only runs inside a
real Webview's browser context (WebGL, `document`, `ResizeObserver`). It
mirrors `layout3D.ts`'s tested logic exactly (each mirrored piece says so in
its own comment) rather than reimplementing it untested.

## What this milestone (and the ones before it) deliberately did not do

Authentication, marketplace packaging, multi-repository/multi-window
support, persisted conversation history, or any change to the Codex API or
the deterministic graph/retrieval/identity/evidence/LLM layers. Live,
in-VS-Code visual verification of the 3D renderer (frame rate, pan/orbit/
zoom feel) has not been captured yet — see
`../docs/3d-repository-intelligence-graph.md` §6/§11.
