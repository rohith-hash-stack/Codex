# Codex Repository Nervous System (VS Code extension, MVP)

First user-facing Codex client, proving the architecture:

```
VS Code -> Codex API (local HTTP) -> Codex intelligence graph
        -> bounded neighborhood retrieval -> visualization-ready nodes/edges
        -> rendered/explorable in a VS Code Webview
```

See `../docs/vscode-nervous-system-architecture.md` for the full design and the
scope this MVP deliberately does and does not cover.

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

- **Codex: Index Repository** — registers the current workspace folder with
  Codex and starts ingestion (Git history + Python AST-derived symbols/calls)
  in the background, showing progress without blocking the editor.
- **Codex: Explore Symbol Neighborhood** — looks up a symbol/file by name and
  opens its bounded neighborhood (callers, callees, references, containment)
  in an interactive graph view. Click an orange (frontier) node to
  progressively reveal more of the graph — never the whole repository graph
  at once.

## What this MVP does not do

No settings UI, no marketplace packaging, no NL query panel, no LLM/agent
wiring, no authentication. See
`../docs/vscode-nervous-system-architecture.md` §10 ("Deferred
Functionality") for the complete list and the reasoning behind each.
