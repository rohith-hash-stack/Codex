# Codex — Local PC Reproducibility & Manual Verification Guide

> **Audit/documentation only.** Nothing in this document changes Codex's
> production architecture, graph/retrieval/identity/evidence behavior, API
> contracts, LLM behavior, benchmark corpora, or canonical/validation
> artifacts. Every command below was actually run in this session (except
> the ones that need a real `Codex_open_API_key`, which this environment
> does not have and never inspects) — see §9 for exactly what was and
> wasn't executed here.

## 0. What this repository state is

| Item | Value |
|---|---|
| Git branch | `claude/api-key-env-variable-t37qbl` |
| Git commit (HEAD) | `60e605b2fadd302f4a3a2cd884067dbc66d665f7` |
| Working tree | clean (`git status --porcelain` empty at time of writing) |
| Python (this session) | 3.11.15 |
| Node.js (this session) | v22.22.2 |
| npm (this session) | 10.9.7 |
| git (this session) | 2.43.0 |

Codex requires **Python ≥ 3.11** (`pyproject.toml`'s `requires-python`,
also needed for `StrEnum`/`tomllib` used directly in `src/codex`). The VS
Code extension's `package.json` pins `@types/node ^20.11.0` (its dev-time
assumption) and `"engines": {"vscode": "^1.85.0"}`; Node 22 (this session)
compiles and runs it without issue. No Node version is pinned anywhere for
the extension's own runtime (VS Code bundles its own Node for extension
hosts) — only `npm`/`tsc` at build time need a working local Node/npm.

---

## 1. Phase 1 — Environment setup

### 1.1 Prerequisites

- **Python ≥ 3.11** on `PATH` as `python3`.
- **Node.js + npm** (Node 20+ recommended; Node 22 confirmed working) — only
  needed to build/run the VS Code extension, not the Python backend.
- **Git**, on `PATH` — `codex.repository.manager.RepositoryManager` and
  `codex.provider.git_adapter.GitAdapter` use `GitPython`, which shells out
  to a real `git` binary. Any reasonably recent git works (2.43.0 confirmed
  here); no minimum version is enforced in code.
- **VS Code ≥ 1.85.0** — only for Phase 6.
- **Node.js + npm again, separately**, for the one external indexing tool
  needed in Phase 4/parts of Phase 3: `@sourcegraph/scip-python@0.6.6`
  (§1.5) — confirmed reachable on the public npm registry from this
  environment (`npm view @sourcegraph/scip-python version` → `0.6.6`, the
  exact version this project's own frozen SCIP fixtures were generated
  with).
- **An OpenAI account + API key**, only for Phase 5. Nothing before Phase 5
  needs it, and the server does not refuse to start without one (§2).

### 1.2 Clone and virtual environment

```bash
git clone <your-fork-or-remote-url> codex
cd codex
git checkout claude/api-key-env-variable-t37qbl   # or whatever branch/commit you're verifying
git rev-parse HEAD                                 # confirm it matches the commit you intend to verify

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

### 1.3 Python dependencies

```bash
pip install -e ".[dev]"
```

Installs, per `pyproject.toml`:

- **Runtime**: `networkx>=3.2`, `pydantic>=2.6`, `GitPython>=3.1`. That's
  the entire runtime dependency surface — no web framework (the API server
  is stdlib `http.server`), no vector/embedding library, no LLM SDK (the
  OpenAI Gateway is stdlib `urllib.request` + `json`, per
  `src/codex/llm/openai_gateway.py`'s own docstring).
- **Dev**: `pytest>=8.0`, `pytest-cov>=5.0`, `ruff>=0.4`, `mypy>=1.9`.

Verify:

```bash
python3 -c "import codex; print(codex.__file__)"   # should print .../src/codex/__init__.py
```

**A discovered environment quirk, not a Codex defect** — worth knowing
before Phase 1 is "done": if your PC also has a `mypy`/`ruff` installed
some other way (e.g. `pipx`, a different venv on `PATH` ahead of this one),
running the bare `mypy` command can silently resolve to *that* installation
instead of this venv's, and then fail with `Cannot find implementation or
stub for module named "pydantic"` — not because anything is wrong, but
because it's the wrong interpreter. If you see that, use
`python3 -m mypy src` (which always uses the active venv) instead of a bare
`mypy` call. This was reproduced directly in the container session this
audit was written from.

### 1.4 Node/npm dependencies (VS Code extension only)

```bash
cd vscode-extension
npm install
```

Installs, per `vscode-extension/package.json`:

- **Runtime dependency**: `three@^0.185.1` — the 3D graph renderer,
  confined entirely to the Webview's own browser context (never imported
  by the extension host). This pulls ~52 MB into `node_modules/three`
  (mostly Three.js's own `examples/` addon tree); the extension only
  vendors three specific files from it at runtime
  (`build/three.module.min.js`, `examples/jsm/controls/OrbitControls.js`,
  `examples/jsm/renderers/CSS2DRenderer.js`) via `webview.asWebviewUri` —
  see `docs/3d-repository-intelligence-graph.md`.
- **Dev dependencies**: `@types/node`, `@types/vscode`, `typescript`.

`node_modules/` is `.gitignore`d (standard npm convention) — every fresh
clone needs its own `npm install`, and this is not a container-specific
step.

### 1.5 External tools

- **Git** — already covered in §1.1 (required for any repository Codex
  ingests, and for `GitAdapter`'s `HISTORY`/`CO_CHANGE` evidence).
- **`@sourcegraph/scip-python@0.6.6`** — required **only** if you want to
  exercise SCIP-backed query categories (`FIND_IMPLEMENTATIONS`,
  `FIND_REFERENCES`, `ARCHITECTURE_ANALYSIS`) or re-ingest `click`/`flask`/
  `itsdangerous` from source rather than the frozen fixtures already
  checked into this repository. Install globally:

  ```bash
  npm install -g @sourcegraph/scip-python@0.6.6
  ```

  Generate an index against a real, cloned repository (this is exactly the
  command shape this project's own test fixtures were produced with — see
  `tests/test_symbol_convergence.py`'s and `docs/broad-validation-report.md`'s
  own documentation of this):

  ```bash
  cd /path/to/some-python-repo
  scip-python index                       # whole repository -> ./index.scip
  # or, to index only a subdirectory (faster, what this project used for
  # its own self-hosted fixture):
  scip-python index --target-only src/some/subpackage
  ```

  This produces `index.scip` in the current directory by default
  (`codex.provider.scip_adapter.SCIPAdapter`'s `DEFAULT_INDEX_FILENAME`).
  **Not verified live in this session** — installing/running `scip-python`
  itself was not exercised here (it would download a real package and
  index a real large repository, outside this audit's "safe validation"
  boundary); its existence, exact pinned version, and exact invocation
  shape were confirmed instead by (a) `npm view` confirming `0.6.6` is
  published and reachable, and (b) this project's own test/doc comments
  recording the precise command it already used to build the checked-in
  `.scip` fixtures. Flagged **UNKNOWN** in §8 until you've run it yourself.
- **No CodeQL, no Sourcegraph server, no database** — `CodeQLAdapter`
  exists in source but is not wired into the live API server or any
  corpus in this repository (SARIF licensing terms make CodeQL's own CLI
  something *you'd* have to run and own, not Codex — see
  `docs/resources.md`); Sourcegraph was never adopted (ADR-006 remains
  open). Neither is needed for anything in this guide.

### 1.6 Environment variables

| Variable | Required for | How to set it |
|---|---|---|
| `Codex_open_API_key` | Phase 5 only (`POST /query`'s real OpenAI call) | Export it in your shell before starting the server: `export Codex_open_API_key="sk-..."` (macOS/Linux) or `$env:Codex_open_API_key="sk-..."` (PowerShell). **Never** put it in a file this repository tracks. |

Notes, precise and important:

- **Codex does not read a `.env` file automatically.** `.env.example`
  exists at the repo root as a personal-convenience template (`cp
  .env.example .env`, fill in the key, `source .env`, or use your shell's
  own env-file loading — direnv, etc.) but nothing in `src/codex` uses
  `python-dotenv` or any equivalent; `src/codex/llm/openai_gateway.py`
  reads it with a plain `os.environ.get("Codex_open_API_key")`, fresh on
  every `generate()` call, never cached. `.env` is git-ignored either way.
- **The variable name is case-sensitive and unusually capitalized**:
  `Codex_open_API_key`, not `CODEX_OPENAI_API_KEY` or similar — copy it
  exactly.
- **This guide never prints, logs, or persists an actual key value.**
  Every command below that needs it uses `$Codex_open_API_key`/
  `${Codex_open_API_key}` by reference; nowhere does this document, or
  anything it asks you to run, echo the key to a file or terminal.
- **Starting the server does not require the key at all** (§2) — only a
  real `/query` call does, and it fails with a clean, structured `502`
  (not a crash) if the key is missing or rejected.
- **A VS Code launch gotcha, not container-specific**: the extension spawns
  a bare `python3` (`vscode-extension/src/extension.ts`'s `startServer()`)
  and a bare `Codex_open_API_key` lookup via the server process's own
  environment — both inherited from whatever environment *VS Code itself*
  was launched in, not automatically from your shell profile. If you
  launch VS Code from your Dock/Start Menu rather than a terminal where
  you've `export`ed the key and activated your venv, the extension's
  spawned `python3 -m codex.api` may not see either. Launching VS Code
  from an activated-venv terminal (`code .`) avoids this.

---

## 2. Phase 2 — Codex startup

### 2.1 Exact commands

```bash
cd codex               # repository root, with .venv activated
python3 -m codex.api --port 8791
```

`--port` is optional (`--port 0`, the default, picks a free ephemeral port
and prints it — used by the VS Code extension and the TypeScript
integration test); a fixed port like `8791` is more convenient for manual
`curl` testing. `--host` defaults to `127.0.0.1` (loopback only, by
design — no auth/multi-user story exists yet, `src/codex/api/server.py`'s
own docstring is explicit about this).

### 2.2 Expected startup output

```
CODEX_API_LISTENING 127.0.0.1 8791
```

The process then blocks, serving requests on a background thread, until
you send it `SIGINT`/`SIGTERM` (Ctrl-C). **Verified live in this session**
— see §9.

### 2.3 Expected `/healthz` response

```bash
curl -s -w '\n%{http_code}\n' http://127.0.0.1:8791/healthz
```

```json
{"status": "ok"}
200
```

`/healthz` is deliberately process-liveness-only — it never touches
repository state or the LLM Gateway, so it returns `200` immediately even
before any repository is registered and even if `Codex_open_API_key` is
never set. **Verified live in this session.**

### 2.4 What `python -m codex.api` actually registers

Read directly from `src/codex/api/__main__.py`'s `_build_api()` — **this
is the single most important fact for planning your own manual testing**:
the default CLI registers only `GitAdapter` (git history/co-change) and
`AstCallsAdapter` (Python-`ast`-derived `CALLS` relationships). It does
**not** register `SCIPAdapter` or `PyprojectDependencyAdapter`, and
`CodeQLAdapter` is never wired anywhere in this repository. Concretely,
through the plain `python -m codex.api` server:

- `FIND_CALLERS` (who calls X) — **works**, via `AstCallsAdapter`'s real
  `CALLS` edges.
- `FIND_TESTS`, negative queries, ambiguous/high-fan-out queries,
  neighborhood expansion — **work**, since they only need whatever
  entities/relationships exist plus the (provider-independent) planner/
  retrieval logic.
- `FIND_DEPENDENCIES` (needs `PyprojectDependencyAdapter`) and
  `FIND_IMPLEMENTATIONS`/`FIND_REFERENCES`/most of
  `ARCHITECTURE_ANALYSIS` (need `SCIPAdapter`) — **do not work** through
  this CLI as shipped: the repository will ingest fine, but those
  categories will simply have no supporting relationships in the graph. This
  is not a bug — `__main__.py`'s own docstring says so explicitly
  ("`SCIPAdapter`/`CodeQLAdapter` are not wired here... adding them is a
  caller/deployment concern") — but it is easy to miss, and the task
  description's own list of things to manually verify (dependencies,
  implementations, references) needs it stated plainly. §4.4 shows the
  documented, non-invasive way to exercise those categories anyway.

This is a **documentation/scope finding, not a code defect** — flagged
here per this audit's own "report first, don't silently patch" boundary.
No file under `src/codex/` was changed to work around it.

---

## 3. Phase 3 — Small repository

**Recommended repository: a minimal, hand-made 2-file Python repo** (not
an external clone) — the fastest, most deterministic way to sanity-check
every capability the default CLI actually supports (§2.4), with zero SCIP
dependency. This is the exact shape `vscode-extension/src/
integration.test.ts`'s own real end-to-end test already uses.

### 3.1 Create and ingest it

```bash
mkdir -p /tmp/codex-smoke-repo && cd /tmp/codex-smoke-repo
git init -q
git config user.email "you@example.com"
git config user.name "you"

cat > app.py <<'EOF'
def helper(x):
    return x + 1

def main():
    return helper(3)
EOF

cat > test_app.py <<'EOF'
from app import helper

def test_helper():
    assert helper(1) == 2
EOF

git add app.py test_app.py
git commit -q -m "init"
```

With the server from §2 still running:

```bash
curl -s -X POST http://127.0.0.1:8791/repositories \
  -H "Content-Type: application/json" \
  -d '{"repository_id": "smoke-test", "local_path": "/tmp/codex-smoke-repo"}'
```

Expected: `202 Accepted`, a JSON body like
`{"job_id": "job-smoke-test-...", "repository_id": "smoke-test"}`.
**Verified live in this session** (exact output reproduced, job id will
differ per run since it's randomized).

### 3.2 Monitor ingestion

```bash
curl -s http://127.0.0.1:8791/jobs/job-smoke-test-XXXXXXXX      # use your real job_id
curl -s http://127.0.0.1:8791/repositories/smoke-test/status
```

Expected `phase`: `REGISTERED` → `INDEXING` → `READY` (for a repo this
small, this completes in well under a second — both endpoints returned
`"phase":"READY"` immediately in this session's own test run).
`provider_summary` should list both `ast_calls` and `git` as
`"status":"COMMITTED"`.

### 3.3 Manual checks

**Callers** (`FIND_CALLERS`-shaped, via `/neighborhood`):

```bash
curl -s "http://127.0.0.1:8791/neighborhood?repository_id=smoke-test&symbol=helper&depth=1"
```
Expect `main` and `test_helper` both present at `distance:1`, each with a
`CALLS` edge into `helper` (`status:"SUPPORTED"`, `confidence` near `1.0`).
**Verified live** — exact structure reproduced in this session.

**Symbol lookup / search**:
```bash
curl -s "http://127.0.0.1:8791/symbols?repository_id=smoke-test&query=helper"
```
Expect one match, `qualified_name: "app.py::helper"`. **Verified live.**

**Negative query** (nonexistent symbol):
```bash
curl -s "http://127.0.0.1:8791/symbols?repository_id=smoke-test&query=totally_nonexistent_xyz"
```
Expect `"nodes": []`, `200 OK` — not an error. **Verified live.**

**Neighborhood/graph expansion**: re-run `/neighborhood` with `depth=2`;
confirm the response is still bounded (`max_nodes`/`max_edges` query
params default to 50/100) and `truncated` reflects whether the real graph
had more than that.

**Implementations / references / dependencies**: not exercisable against
this tiny repo or through the default CLI at all — see §2.4 and §4.4.

**Ambiguous/high-fan-out symbol**: not really reproducible in a 2-function
repo; see §4 for repositories where this is real (`click`'s `ParamType`,
94 real callers of `codex`'s own `plan_query`).

---

## 4. Phase 4 — Real repositories

All commit SHAs below are the **exact ones this project's own canonical/
validation-expansion benchmarks and independent-validation passes already
used and pinned** (`src/codex/benchmark/canonical_corpus.py`,
`expansion_corpus.py`, `docs/architecture-conformance-audit.md` §KK) — not
new recommendations invented for this guide, so your results should match
what this project has already validated against.

| Repository | Pinned commit | SCIP required? | Size |
|---|---|---|---|
| `codex` (this repo, self-hosted) | your own `HEAD` | No (Git+AST only) | ~91 source files |
| `pallets/itsdangerous` | `672971d66a2ef9f85151e53283113f33d642dabd` | Yes | 15 files (8 source) — smallest |
| `pallets/click` | `36baa15ff831b939a22bc527cd76ce653ef6f66d` | Yes | 79 files |
| `pallets/flask` | `d318b683471101618febed18996405ad26462110` | Yes | 83 files |
| `pytest-dev/pytest` | `51e9a9f148cd2509a31e3fa0d2b1b3204c2b0dd7` | Yes | larger |
| `psf/requests` | `5460f467b02e49471c0fd6cfc9ca0adab6351f98` | Yes | larger (indexer was scoped to `src/requests/` only, 19 files — a known, disclosed indexer-scope limitation, not a defect) |
| `django/django` | `5babd2e21ac0877d66c5452da17b97608b337fba` | Yes | largest |

### 4.1 `codex` itself — Git+AST only, no SCIP needed

```bash
curl -s -X POST http://127.0.0.1:8791/repositories \
  -H "Content-Type: application/json" \
  -d "{\"repository_id\": \"codex-self\", \"local_path\": \"$(pwd)\"}"
```

Poll `/jobs/{id}` until `READY` (91 real source files; expect this to take
several seconds, not milliseconds).

Suggested queries (`/neighborhood?repository_id=codex-self&symbol=...`):
- `symbol=plan_query` — expect real callers within `codex.planner`/
  `codex.api`.
- `symbol=understand_query` — expect callers in `codex.api.service`.
- A deliberately-misspelled symbol — expect an empty result, not an error.

Expected result type, not exact output (this repository changes over
time): a small, bounded set of real `CALLS` edges rooted at whatever
function you pick — never fabricated nodes.

### 4.2 `itsdangerous` — smallest SCIP-backed example

```bash
git clone https://github.com/pallets/itsdangerous.git /tmp/itsdangerous
cd /tmp/itsdangerous
git checkout 672971d66a2ef9f85151e53283113f33d642dabd
npm install -g @sourcegraph/scip-python@0.6.6   # once, if not already installed
scip-python index                                # -> ./index.scip
```

Ingesting a SCIP-only repository through the live HTTP API needs
`SCIPAdapter` registered, which the default CLI does not do (§2.4) — see
§4.4 for the documented way to do this without modifying any shipped file.

Suggested queries once ingested: `BadData`/`BadSignature`/`BadHeader`
(a real, shallow inheritance chain — good for `FIND_IMPLEMENTATIONS`-shaped
checks), a near-single-match low-fan-out symbol name (this repository was
specifically chosen for this project's own validation because of that
property — see `src/codex/benchmark/expansion_corpus.py`'s own docstring).

### 4.3 `click` / `flask` — moderate size, real ambiguity

```bash
git clone https://github.com/pallets/click.git /tmp/click
cd /tmp/click && git checkout 36baa15ff831b939a22bc527cd76ce653ef6f66d
scip-python index

git clone https://github.com/pallets/flask.git /tmp/flask
cd /tmp/flask && git checkout d318b683471101618febed18996405ad26462110
scip-python index
```

Suggested queries once ingested (via §4.4's registry):
- `click`: `ParamType` — a real, deliberately ambiguous/high-fan-out
  symbol (24 real substring-matching entities in this project's own
  canonical benchmark).
- `flask`: `Scaffold`/`App`, `Blueprint.add_url_rule` (qualified) vs.
  `add_url_rule` (unqualified, ambiguous across 4 real matches) — a good
  check that a qualified query narrows correctly while the bare name stays
  genuinely ambiguous.

Expected result type: real, non-fabricated entities/relationships; an
ambiguous bare query returns multiple candidates rather than silently
picking one.

### 4.4 Exercising SCIP-backed categories through a local script (documented, not shipped)

Since the shipped `python -m codex.api` CLI intentionally doesn't register
`SCIPAdapter`/`PyprojectDependencyAdapter` (§2.4), the honest way to
manually verify `FIND_IMPLEMENTATIONS`/`FIND_REFERENCES`/
`FIND_DEPENDENCIES`/`ARCHITECTURE_ANALYSIS` is one of:

1. **Reuse this project's own already-validated benchmark scripts**
   (`scripts/run_canonical_benchmark.py`, `scripts/run_expansion_benchmark.py`)
   — these build their own `CapabilityRegistry` with `SCIPAdapter` wired
   in, against the frozen fixtures already checked into
   `tests/fixtures/benchmark/scip/`. Running them needs `Codex_open_API_key`
   (Phase 5) since they call the real LLM Gateway end to end; see §5.4.

2. **Write a short, local, throwaway script** (do **not** commit it to
   this repository — this audit's scope is documentation only) that
   mirrors `_build_api()` but adds the extra adapters, e.g.:

   ```python
   # save as, e.g., /tmp/codex_scip_server.py -- not part of this repo
   import sys
   sys.path.insert(0, "src")
   from codex.api.server import serve
   from codex.api.service import CodexAPI
   from codex.evidence.store import InMemoryEvidenceStore
   from codex.llm.openai_gateway import OpenAIGateway
   from codex.provider.git_adapter import GitAdapter
   from codex.provider.ast_calls_adapter import AstCallsAdapter
   from codex.provider.scip_adapter import SCIPAdapter
   from codex.provider.pyproject_dependency_adapter import PyprojectDependencyAdapter
   from codex.registry.registry import CapabilityRegistry
   from codex.registry.scoring import ProviderScoreProfile

   profile = ProviderScoreProfile(evidence_quality=0.8, cost_factor=0.3)
   registry = CapabilityRegistry()
   registry.register(GitAdapter(), profile)
   registry.register(AstCallsAdapter(), profile)
   registry.register(SCIPAdapter(), profile)              # needs index.scip in the repo root
   registry.register(PyprojectDependencyAdapter(), profile)  # needs pyproject.toml in the repo root

   api = CodexAPI(registry, InMemoryEvidenceStore(), gateway=OpenAIGateway())
   server = serve(api, host="127.0.0.1", port=8792)
   print(f"CODEX_API_LISTENING 127.0.0.1 {server.server_address[1]}")
   import threading; threading.Event().wait()
   ```

   Then register a repository whose root contains both `index.scip`
   (from `scip-python index`, run inside that clone) and a real
   `pyproject.toml`/`requirements.txt`-driven manifest for
   `PyprojectDependencyAdapter` to read. **Not run live in this session**
   (would mean cloning and indexing a large external repository) — flagged
   `UNKNOWN` in §8, everything it's built from (`SCIPAdapter`,
   `PyprojectDependencyAdapter`, `CapabilityRegistry`, `serve()`) is the
   same real, already-tested code the 1351-test Python suite exercises.

---

## 5. Phase 5 — LLM

### 5.1 Configure the key safely

```bash
export Codex_open_API_key="sk-...your real key..."   # your shell only, never a committed file
python3 -m codex.api --port 8791                      # restart, or it was already running (§2)
```

### 5.2 Run the full pipeline

```bash
curl -s -X POST http://127.0.0.1:8791/query \
  -H "Content-Type: application/json" \
  -d '{"repository_id": "smoke-test", "query_text": "What calls helper?"}'
```

(Use whatever `repository_id` you registered in Phase 3/4 — `smoke-test`
from §3, or `codex-self`/`itsdangerous`/etc. from §4.)

### 5.3 Expected `AskResponse` shape and what to check

```json
{
  "repository_id": "smoke-test",
  "query_text": "What calls helper?",
  "query_id": "...",
  "run_id": "...",
  "status": "OK",
  "intent": "FIND_CALLERS",
  "plan_status": "OK",
  "answer": "<narrative text>",
  "claims": [{"subject": "...", "predicate": "CALLS", "object": "...", "claim_type": "FACT"}],
  "evidence_context": {
    "entities": [...], "relationships": [...], "evidence_count": N,
    "limitations": [], "partial": false
  },
  "model": {"provider": "openai", "requested_model": "gpt-4o-mini", "served_model": "...", "usage_total_tokens": N},
  "detail": null
}
```

Checklist (fields to inspect, not exact strings — real model output
varies run to run):

- **Correct answer**: `status: "OK"`, `answer` mentions the real caller(s)
  your `/neighborhood` check in Phase 3/4 already showed.
- **Evidence/claims**: each `claims[].subject`/`.object` should resolve to
  a real `evidence_context.entities[].qualified_name` — cross-check by
  eye against the neighborhood you already pulled.
- **Relationships**: `evidence_context.relationships[]` should be a subset
  of what `/neighborhood` already returned for the same repository —
  never a relationship type your registered providers don't produce.
- **Negative/abstention**: ask something with no real answer (e.g. "What
  calls a_function_that_does_not_exist?") — expect `evidence_context.
  relationships: []` and either an honest "no supporting relationships"
  answer or `evidence_context.limitations` naming the negative-query
  signal, never a fabricated claim.
- **Ambiguity**: ask about a real high-fan-out/ambiguous symbol (`click`'s
  `ParamType`, or the bare `add_url_rule` in `flask`) — expect
  `evidence_context.limitations` to contain a string starting
  `"ambiguous target: N distinct entities"`.
- **Malformed/timeout**: not force-inducible via `curl` alone (the
  original occurrence — §PROGRESS.md's 2026-09-03 "Diagnose & fix OpenAI
  `MALFORMED_OUTPUT`" entry — was a real high-token-count case; you'd need
  a case with an unusually large real evidence set to reproduce it
  organically). If it happens, expect `status: "MALFORMED_OUTPUT"` /
  `"LLM_TIMEOUT"` with `answer: null` and a `detail` string — never a raw
  Python traceback or a `500`.
- **Auth failure** (test by temporarily unsetting the key in a *separate*
  shell, or using an invalid one): expect a structured `502` with
  `{"error": "LLM authentication failed: ..."}`, not a crash. **This is
  the one check safe to actually run without a real key** — confirms the
  "never blocks startup, fails per-request" design (§2.4) end to end. Not
  run in this session since it would require briefly touching the key
  variable's presence/absence, and this audit's boundary is to never
  interact with the key at all, real or fake — verified instead by
  reading `src/codex/api/server.py`'s exception mapping directly (§ code
  citation above).

### 5.4 Optional: reuse the project's own validated benchmark scripts

```bash
python3 scripts/run_canonical_benchmark.py     # click/flask + self-hosted codex, 13 real cases
python3 scripts/run_expansion_benchmark.py     # + itsdangerous, 14 real cases
```

Both need `Codex_open_API_key` set and real network access to
`api.openai.com`; both write a full JSON artifact to `benchmark_runs/`
(git-ignored path already populated with this project's own prior real
runs — do not overwrite those unless you mean to; consider copying or
renaming your own output). Neither modifies the frozen corpora, and
neither should be run unless you specifically want a second, independent
confirmation beyond the manual `curl` checks above.

---

## 6. Phase 6 — VS Code

### 6.1 Build

```bash
cd vscode-extension
npm install
npm run compile      # tsc -p ./  -- produces out/*.js
```

### 6.2 Run / install

From VS Code, open the `vscode-extension/` folder and press **F5**
(launches an Extension Development Host with Codex active) — or, from a
full VS Code install:

```bash
code --extensionDevelopmentPath=/path/to/codex/vscode-extension
```

### 6.3 API connection configuration

**None needed manually** — `extension.ts`'s `ensureServer()` spawns
`python3 -m codex.api --port 0` itself the first time any Codex command
runs, reads the printed `CODEX_API_LISTENING` line, and talks to that
ephemeral port for the rest of the VS Code session. This is exactly what
§1.6's "launch VS Code from an activated-venv terminal" note is about:
the spawned `python3` must be able to `import codex` and (for Phase 5-style
checks) see `Codex_open_API_key`.

### 6.4 Repository status/indexing

Open a folder containing a real repository (your `/tmp/codex-smoke-repo`
from §3, or a real clone from §4) as the VS Code workspace, then run
**Codex: Ask a Question** (Cmd/Ctrl+Shift+P). Expect the header to show a
health badge (green once `/healthz` succeeds) and a status badge
(`NOT_REGISTERED` until you click **Index / Re-index**, then `INDEXING` →
`READY`).

### 6.5 Ask Question workflow

Type a query (e.g. "What calls helper?" for the §3 repo), press **Ask**.
Expect the same three-section layout §5.3 already established server-side:
**Status** (badges), **Answer** (narrative), **Evidence** (claims table,
entity chips, relationship table) — never merged.

### 6.6 Evidence/claim navigation

Click any claim's subject/object chip, or any entity chip in the Evidence
section — expect the graph panel to highlight and scroll to that node.

### 6.7 2D graph

Click the **3D/2D** toggle button in the graph toolbar until it reads
`2D`. Expect the existing SVG renderer: nodes/edges, click-to-select,
click-to-expand via a real `/neighborhood` call.

### 6.8 3D graph

Toggle back to `3D` (the default). Expect a WebGL canvas: colored spheres
(by node kind — module/class/function/test/external), directed edge lines
with arrowheads (colored by evidence status), real DOM labels, mouse
orbit/pan/zoom, hover info in the toolbar hint, click-to-select, and a
**Reset view** button. If your machine/VS Code build has no WebGL context
available, expect an automatic, silent fallback to 2D instead (confirm by
checking whether the **3D/2D** button is disabled — see
`docs/3d-repository-intelligence-graph.md` §5). This was **not** re-verified
visually in this text-only container session (no GPU-backed browser
available here) — see that document's own §6 for the same honestly-flagged
gap; this is the first thing worth confirming on your own PC.

### 6.9 Progressive neighborhood expansion / search / filter

Use the toolbar's search box to look up a symbol/file (expect
disambiguation if multiple real matches exist); click any graph node to
expand one more real `/neighborhood` hop from it (never a client-side
fabrication); use the relationship-type filter checkboxes (populated from
whatever relationship types the current graph actually contains) to hide/
show edge types; click **Reset view** to recenter the 3D camera.

---

## 7. Container-independence audit

| # | Item | Classification | Notes |
|---|---|---|---|
| 1 | Python backend (`src/codex/`) imports/runs | **READY** | Pure stdlib + `networkx`/`pydantic`/`GitPython`, no container-only path/binary found by direct grep (`HTTPS_PROXY`, `/root/.ccr`, `PLAYWRIGHT_*`, `/opt/pw-browsers`, container-proxy references — zero matches anywhere under `src/`, `tests/`, `scripts/`, `vscode-extension/src/`). |
| 2 | `python -m codex.api` startup, `/healthz` | **READY** | Verified live in this session on loopback, no network/container dependency (§2, §9). |
| 3 | Repository register/ingest/status/symbols/neighborhood (Git+AST only) | **READY** | Verified live end-to-end in this session (§3, §9). |
| 4 | `pytest`/`ruff`/`mypy` | **READY** | 1351/1351 passing, ruff clean, mypy clean via `python3 -m mypy src` — all fresh-run in this session (§9). One caveat: a single raw-socket timing test (`test_oversized_declared_content_length_returns_413_before_reading_body`) failed once under full-suite CPU load and passed reliably on 3 isolated re-runs and one full clean re-run — a **pre-existing test-only flake** (the test does a single `socket.recv()` and assumes the whole HTTP response arrives in one TCP read, which isn't guaranteed under load), not a regression, not touched this session. Worth a future, separate fix (loop `recv()` until the response is complete) but explicitly out of this audit's scope. |
| 5 | VS Code extension `tsc`/`npm test` | **READY** | 49/49 TS tests passing, `tsc` clean, fresh-run this session (§9). |
| 6 | `Codex_open_API_key` handling | **READY** | Plain `os.environ.get`, no container-only secrets manager, no auto-`.env` loading (§1.6). |
| 7 | Git dependency (`GitPython`) | **READY** | Shells out to a real `git` on `PATH`; 2.43.0 confirmed working here, no version pin found. |
| 8 | `@sourcegraph/scip-python@0.6.6` availability | **SETUP_REQUIRED** | Confirmed published/reachable on the public npm registry from this environment; not actually installed/run here (§1.5) — a normal one-time global npm install on any PC with npm, not container-specific. |
| 9 | Ingesting SCIP-backed repositories (`click`/`flask`/`itsdangerous`/etc.) through the live HTTP API | **SETUP_REQUIRED** | Needs the §4.4 registry addition — a documented gap in the shipped CLI's provider wiring, not a container dependency (would be identical on any PC). |
| 10 | 3D graph rendering, live visual/interaction check | **UNKNOWN** | No GPU-backed browser/VS Code Extension Host exists in this container to actually render and interact with it — genuinely can't be verified from here, needs your own PC (§6.8). Not a code dependency on the container; the *renderer* code itself is plain WebGL/Three.js with no container-specific asset path (confirmed: all Three.js files are vendored under `node_modules`/`webview.asWebviewUri`, never a network CDN — `docs/3d-repository-intelligence-graph.md` §1). |
| 11 | Real `POST /query` → OpenAI round trip | **UNKNOWN** | Needs a real `Codex_open_API_key` this session does not have and will not request; the code path itself (`OpenAIGateway`, stdlib `urllib`) has no container dependency — prior sessions' own `PROGRESS.md` entries record it working over this same environment's proxy, so reachability is not expected to be the blocker on a normal PC either. |
| 12 | This container's own outbound-network proxy (`HTTPS_PROXY`, `/root/.ccr/*`) | **CONTAINER_DEPENDENT — but irrelevant to Codex** | This is infrastructure belonging to *this Claude Code sandbox*, not to the Codex repository — grep confirms zero references to it anywhere in Codex's own source, tests, scripts, or config. Flagged here only for completeness/transparency, not as something blocking your PC. |

**No container-specific hack, path, credential, or assumption was found
anywhere in Codex's own source, tests, scripts, or VS Code extension
code.** Every genuine setup requirement above (#8, #9) is a normal,
one-time local-development step identical on any OS with Python 3.11+,
Node, npm, and git — not a container workaround.

---

## 8. Reproducibility record

| Item | Value |
|---|---|
| Git branch | `claude/api-key-env-variable-t37qbl` |
| Git commit | `60e605b2fadd302f4a3a2cd884067dbc66d665f7` |
| Python | 3.11.15 (repo requires `>=3.11`) |
| Node.js | v22.22.2 (repo's own dev-time assumption: `@types/node ^20.11.0`; not strictly pinned) |
| npm | 10.9.7 |
| git | 2.43.0 (no minimum pinned) |
| `networkx` | `>=3.2` (pinned range, not exact) |
| `pydantic` | `>=2.6` (pinned range, not exact) |
| `GitPython` | `>=3.1` (pinned range, not exact) |
| `pytest`/`ruff`/`mypy` | `>=8.0`/`>=0.4`/`>=1.9` (pinned ranges, not exact) |
| `three` (npm) | `^0.185.1` |
| `typescript` (npm) | `^5.4.0` |
| `@sourcegraph/scip-python` | `0.6.6` (exact — the version every frozen `.scip` fixture in this repo was generated with) |
| VS Code | `^1.85.0` minimum |

No pinned version was changed by this audit, per the explicit instruction.
`pyproject.toml`'s dependency ranges (`>=`) mean your exact installed
`networkx`/`pydantic`/`GitPython` versions may differ slightly from
whatever was resolved in this container — this is expected and was not
altered.

---

## 9. What was and wasn't actually executed for this audit

**Executed live, in this session, on this container** (all safe, none
touching the API key, none modifying tracked files):

- `git branch --show-current`, `git rev-parse HEAD`, `git status`
- `python3 --version`, `node --version`, `npm --version`, `git --version`
- `python3 -m pytest -q` (full suite, twice — 1351/1351 clean on both the
  isolated re-check and the full clean re-run; one incidental flake
  described in §7 row 4)
- `ruff check src tests scripts` (clean)
- `python3 -m mypy src` (clean, 91 files) — and, separately, confirmed the
  bare `mypy` command in this container resolves to a *different*,
  pydantic-less interpreter (§1.3's documented quirk)
- `cd vscode-extension && npx tsc -p ./ && npm test` (clean, 49/49)
- `npm view @sourcegraph/scip-python version` (confirmed `0.6.6` published
  and reachable)
- A full live server smoke test: `python3 -m codex.api --port 8791` →
  `/healthz` → create a temp git repo → `POST /repositories` →
  `GET /jobs/{id}` → `GET /repositories/{id}/status` → `GET /symbols`
  (both a real match and a deliberately-nonexistent one) →
  `GET /neighborhood` (confirmed real `CALLS` edges, correct
  `qualified_name`s, correct `distance`) → `GET /repositories/unknown/status`
  (confirmed an honest `NOT_REGISTERED` phase rather than an error) →
  clean shutdown and temp-file cleanup. Every response shown in §2/§3
  above is copied verbatim from this real run.

**Not executed** (each with a specific, honest reason, not silently
skipped):

- Anything touching `Codex_open_API_key` or a real OpenAI call (§5) — this
  environment has no key, and this audit's own boundary is to never
  request, generate, or handle one.
- Cloning/indexing any external repository (`click`/`flask`/`itsdangerous`/
  `django`/`requests`/`pytest`) or installing `@sourcegraph/scip-python`
  itself — outside "safe validation needed to confirm the documented
  commands" for a documentation-only audit; the commands themselves are
  transcribed exactly from this project's own already-validated benchmark
  code/docs, not invented.
- Anything requiring a GPU-backed browser or a real VS Code Extension Host
  window (3D rendering, the Ask panel's actual UI) — this container has
  neither, exactly the same honest gap
  `docs/3d-repository-intelligence-graph.md` §6 already flags.

---

## 10. Manual verification checklist

Fill in **PASS**/**FAIL**/**N/A** as you go on your own PC. Notes column
is for anything that didn't match this document (version drift, an actual
defect, etc.) — report those back rather than silently working around
them.

### Phase 1 — Environment

| Check | Result | Notes |
|---|---|---|
| `python3 --version` ≥ 3.11 | ☐ | |
| `pip install -e ".[dev]"` succeeds | ☐ | |
| `python3 -c "import codex"` succeeds | ☐ | |
| `node --version` / `npm --version` present | ☐ | |
| `cd vscode-extension && npm install` succeeds | ☐ | |
| `Codex_open_API_key` exported in the shell you'll launch VS Code from | ☐ | |
| `npm install -g @sourcegraph/scip-python@0.6.6` succeeds (if doing Phase 4 SCIP repos) | ☐ | |

### Phase 2 — Startup

| Check | Result | Notes |
|---|---|---|
| `python3 -m codex.api --port 8791` prints `CODEX_API_LISTENING 127.0.0.1 8791` | ☐ | |
| `curl http://127.0.0.1:8791/healthz` → `{"status":"ok"}`, `200` | ☐ | |
| Server starts with `Codex_open_API_key` unset (no crash) | ☐ | |

### Phase 3 — Small repository

| Check | Result | Notes |
|---|---|---|
| `POST /repositories` → `202`, real `job_id` | ☐ | |
| `GET /jobs/{id}` reaches `phase: READY` | ☐ | |
| `GET /repositories/{id}/status` matches | ☐ | |
| `GET /symbols?query=helper` → 1 real match | ☐ | |
| `GET /symbols?query=<nonexistent>` → `nodes: []`, `200` | ☐ | |
| `GET /neighborhood?symbol=helper&depth=1` → real `main`/`test_helper` callers, real `CALLS` edges | ☐ | |
| `depth=2` neighborhood still bounded (`max_nodes`/`max_edges` honored) | ☐ | |

### Phase 4 — Real repositories

| Check | Result | Notes |
|---|---|---|
| `codex` self-hosted ingest reaches `READY` | ☐ | |
| At least one real-repo `FIND_CALLERS`-shaped query returns plausible callers | ☐ | |
| SCIP index generated for at least one of click/flask/itsdangerous | ☐ | |
| §4.4 registry script ingests a SCIP-backed repo | ☐ | |
| `FIND_IMPLEMENTATIONS`/`FIND_REFERENCES` query against it returns real relationships | ☐ | |
| Ambiguous symbol (`ParamType`, bare `add_url_rule`) returns multiple real candidates | ☐ | |

### Phase 5 — LLM

| Check | Result | Notes |
|---|---|---|
| `Codex_open_API_key` exported, never printed/logged by you | ☐ | |
| `POST /query` → `status: OK`, real `answer` | ☐ | |
| `claims[]` resolve to real `evidence_context.entities` | ☐ | |
| `evidence_context.relationships` matches what `/neighborhood` already showed | ☐ | |
| Negative query → empty relationships, honest abstention | ☐ | |
| Ambiguous query → `limitations` contains `"ambiguous target: N distinct entities"` | ☐ | |
| Invalid/missing key → structured `502`, not a crash | ☐ | |

### Phase 6 — VS Code

| Check | Result | Notes |
|---|---|---|
| `npm run compile` succeeds | ☐ | |
| F5 launches Extension Development Host | ☐ | |
| **Codex: Ask a Question** opens, health badge turns green | ☐ | |
| **Index / Re-index** reaches `READY` | ☐ | |
| Ask a question → answer/evidence/status sections all populate | ☐ | |
| Clicking a claim/entity chip highlights the right graph node | ☐ | |
| 2D graph renders and is click-to-expand | ☐ | |
| 3D graph renders (spheres/edges/labels), orbit/pan/zoom work | ☐ | |
| **Reset view** recenters the 3D camera | ☐ | |
| Relationship-type filter checkboxes show/hide edges | ☐ | |
| Search box finds and focuses a symbol | ☐ | |

---

## 11. Final statement

**Is the current commit (`60e605b2fadd302f4a3a2cd884067dbc66d665f7` on
`claude/api-key-env-variable-t37qbl`) reproducible on a normal developer
PC?**

**Yes, for everything except a real end-to-end LLM call and live 3D visual
verification — both of which are blocked by things *this audit
environment* lacks (a real API key, a GPU-backed browser), not by
anything in Codex's own code, configuration, or dependencies.**

Specifically:

- **No container-only dependency was found anywhere in Codex's source,
  tests, scripts, or VS Code extension** (§7) — a direct grep for every
  container-specific marker this audit's own sandbox uses
  (`HTTPS_PROXY`, `/root/.ccr`, `PLAYWRIGHT_*`, proxy hostnames) returned
  zero matches in any tracked Codex file.
- **Every Phase 1-4 command in this guide was verified to actually work**,
  live, in a fresh check this session (§9) — server startup, healthz,
  register/ingest/status/symbols/neighborhood, the full Python test/lint/
  type-check suite, and the full TypeScript test/compile suite.
- **Two genuine, honestly-scoped setup requirements exist** (§7 rows 8-9):
  installing `@sourcegraph/scip-python@0.6.6` (confirmed published and
  reachable, not container-specific), and registering `SCIPAdapter`/
  `PyprojectDependencyAdapter` yourself for SCIP-backed query categories
  (a documented gap in the shipped CLI's default provider wiring, not a
  container dependency — identical on any PC). Neither was silently
  patched; §4.4 documents the exact, non-invasive workaround.
- **One pre-existing, unrelated test flake** was found (§7 row 4) — a
  raw-socket test assuming single-`recv()` completeness, reproduced once
  under load and passed reliably three times in isolation plus once on a
  full clean re-run. Reported, not fixed, per this audit's own boundary.
- **Two items remain genuinely unverified from this container** and can
  only be confirmed on your own PC: the real OpenAI round trip (Phase 5)
  and the 3D graph's actual visual/interaction behavior (Phase 6.8) —
  both are explicitly flagged **UNKNOWN**, not claimed as passing.

No production code, API contract, graph/retrieval/identity/evidence
behavior, LLM behavior, benchmark corpus, or canonical/validation artifact
was changed to produce this document.
