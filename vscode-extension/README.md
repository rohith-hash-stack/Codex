# Codex Repository Nervous System (VS Code extension)

The first user-facing Codex client. As of the UI Integration Milestone
(`../docs/ui-integration-milestone.md`), its primary workflow is:

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
  (evidence snapshot or a `/neighborhood`-driven explorer). See
  `../docs/ui-integration-milestone.md` for the full walkthrough.
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
  model (`graphModel.ts`).
- `codexClient.test.ts` — the HTTP client against a fake in-process server,
  covering every `POST /query` outcome and error status.
- `askPanelView.test.ts` — the generated Webview HTML/script is well-formed
  JavaScript.
- `integration.test.ts` — a **real** `python3 -m codex.api` server, driven
  end to end (register/ingest/status/symbols/neighborhood/healthz). Skips
  itself if `codex.api` isn't importable in the current environment. Does
  not call `/query` (that needs a real `Codex_open_API_key`); the client-side
  `/query` contract is covered by `codexClient.test.ts` instead.

## What this milestone deliberately did not do

3D graph rendering (the data model is structured for it — see
`../docs/ui-integration-milestone.md` §"Graph visualization" — but 2D SVG is
this milestone's actual renderer), authentication, marketplace packaging,
multi-repository/multi-window support, or any change to the Codex API or the
deterministic graph/retrieval/identity/evidence/LLM layers.
