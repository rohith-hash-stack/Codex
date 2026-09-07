# CODEX V1 WINDOWS + UI RUNTIME VALIDATION REPORT

**Scope:** Windows OS compatibility, VS Code extension GUI/runtime, 2D graph runtime, 3D graph runtime — the four remaining validation gaps identified at V1 architecture closure (commit `e66032c`).

**Baseline:** V1 READY WITH DOCUMENTED LIMITATIONS, zero release blockers, 1387/1387 Python tests, 49/49 TypeScript tests, Ruff clean, mypy clean, tsc clean. This phase did not reopen graph/retrieval/grounding architecture; no genuine release-blocking defect in that subsystem was found or claimed.

**Method note:** this phase's own mandate was "evidence, not commits." Two genuine, reproduced UI-rendering defects were found via real browser execution (never source inspection alone) and were fixed under the phase's own strict change policy (reproduced defect, root cause established, smallest safe fix, regression coverage added). Everything else in this report is evidence only — no speculative or defensive changes were made anywhere else in the codebase.

---

## 1. Executive verdict

**PASS WITH DOCUMENTED LIMITATIONS.**

2D and 3D graph rendering were verified with real, newly-obtained headless-Chromium runtime evidence (not source/unit tests alone) — one real defect was found and fixed in each, both confirmed fixed by re-running the same real-browser test. Windows OS execution and the VS Code Extension Host GUI could not be executed in this container (no Windows machine, no VS Code/Electron installation exist here) and are honestly reported as UNVERIFIED, with static/code-review evidence reported separately and not inflated into a PASS. No defect found is a release blocker; no protected architecture (graph/retrieval/grounding/ontology) was touched.

---

## 2. Windows matrix

No genuine Windows environment is available in this container (`uname -a` → `Linux 6.18.44-fc-v24`; `platform.system()` → `Linux`). Every row below is **UNVERIFIED by execution**; where a static/code-review substitute was performed, it is reported as such, never as PASS.

| Area | Result | Evidence | Defect? | Blocker? |
|---|---|---|---|---|
| Python installation/import on Windows | UNVERIFIED (no Windows env) | — | No | No |
| Editable/package install on Windows | UNVERIFIED (no Windows env) | — | No | No |
| CLI startup on Windows | UNVERIFIED (no Windows env) | — | No | No |
| API `/healthz` on Windows | UNVERIFIED (no Windows env) | — | No | No |
| Repository ingestion, AST extraction, CALLS/REFERENCES evidence on Windows | UNVERIFIED (no Windows env) | Confirmed on Linux this cycle: real ingestion of `/tmp/codex_ui_runtime_repo` (a fresh 2-file, 2-function real git repo) produced correct entities/CALLS edges through `_build_api()`'s real registry | No | No |
| Dependency extraction on Windows | UNVERIFIED (no Windows env) | Pre-existing `tests/test_api_dependency_provider_integration.py` covers this on Linux only | No | No |
| `/query`, negative query, ambiguous query, real OpenAI call, grounding verification on Windows | UNVERIFIED (no Windows env) | Confirmed on Linux this cycle: real OpenAI call against the fresh repo returned `OK` with 3 correctly grounded CALLS claims, and separately surfaced a real ambiguity notice ("3 distinct entities match this query") — both real invariants observed live, on Linux | No | No |
| Filesystem behavior — Windows paths, spaces in paths, nested dirs, temp dirs, file create/delete | Static review only (not executed) | `grep` across `src/codex`: every path-handling site (`repository/manager.py`, `repository/models.py`, `provider/ast_calls_adapter.py`, `provider/pyproject_dependency_adapter.py`, `provider/scip_adapter.py`, `provider/codeql_adapter.py`, `benchmark/*.py`) imports `pathlib.Path`, none does manual `os.path.join`/string concatenation with a hardcoded `/`. The one hardcoded `"/".split(...)` hit (`planner/ranking.py:163`) splits a logical `qualified_name` identifier, not a filesystem path — not a Windows risk. No `subprocess`, `shell=True`, or `os.system` usage anywhere in `src/codex`. This is source-level reassurance only, not proof of Windows behavior. | No (none found) | No |
| Subprocess invocation | Static review only | No subprocess usage exists in `src/codex` production code (the VS Code extension's `cp.spawn("python3", ...)` is the one subprocess call in the whole system — see VS Code matrix; also unverified on Windows) | No | No |
| Git repository init/cleanup, "WinError 5" | Researched and classified, **not reproduced** | See dedicated write-up below | Possible (unconfirmed) | No (not reproduced, so not actionable as a blocker) |

### WinError 5 investigation

"WinError 5: Access is denied" on GitPython + Windows is a well-documented issue class (numerous long-standing reports against `gitpython-developers/GitPython`): a `git.Repo` instance can keep OS-level file handles open (via its internal object-database/pack-file access and any spawned `git.exe` subprocess) until the `Repo` object is explicitly closed or garbage-collected. On Windows — unlike POSIX, where an open file handle doesn't block deletion — a caller that then tries to delete or rename that same directory (e.g., a test's `tempfile.TemporaryDirectory()` cleanup, or `shutil.rmtree`) can hit `PermissionError [WinError 5]` because the OS itself refuses to remove a file that still has an open handle.

Static review of `src/codex/provider/git_adapter.py` (`check_eligibility`, `extract`) shows every code path constructs a `git.Repo(repository.local_path)` and never calls `.close()` on it — the object is only released by CPython refcounting/GC. This is consistent with, but does not prove, the known root-cause pattern; nothing in this container can reproduce a Windows file-handle-lock error, since POSIX has no equivalent semantics.

**Classification: non-reproducible in this environment.** No production change was made for this. Per the phase's strict change policy, a fix requires a *reproduced* defect with an established root cause; speculatively adding `repo.close()` calls here, absent a real Windows repro, would be exactly the kind of "manufactured fix to eliminate the limitations section" the phase explicitly prohibits. **Recommendation for a future Windows-capable validation pass:** attempt to reproduce via a real Windows machine ingesting a temp repository and then deleting it immediately after; if reproduced, the smallest safe fix is almost certainly wrapping `git.Repo` usage in `git_adapter.py` with explicit `.close()` (GitPython's own documented mitigation), with a Windows-only regression test asserting the temp directory can be deleted immediately after `extract()`/`check_eligibility()` return.

---

## 3. VS Code matrix

No VS Code, VS Code Insiders, code-server, or Electron binary exists anywhere in this container — confirmed independently three ways: `which code code-server code-insiders electron` (all exit 1), a filesystem search for `*code*.AppImage`/`vscode*` (only hit: this project's own `vscode-extension/` source directory, not an installed binary), and `dpkg -l | grep -i "code\|electron"` (only unrelated false-positive package-description substring matches). Phase 2's actual requirement — launch a real Extension Host, activate the extension, click through the UI — **cannot be executed here**. Every row is honestly UNVERIFIED for GUI/runtime interaction; the code+test evidence that does exist is reported alongside, not conflated with it.

| Capability | Result | Runtime evidence | Defect? |
|---|---|---|---|
| Install/build extension | Code-level PASS | `npx tsc -p ./` compiles clean (see §8); `npm test` — 51/51 Node-test-runner tests pass | No |
| Launch Extension Host / activate extension | **UNVERIFIED** (no VS Code/Electron installation exists in this container) | — | N/A |
| Configure/connect to local Codex API | **UNVERIFIED** (GUI step) | Host-side spawn logic reviewed: `extension.ts`'s `startServer()` does `cp.spawn("python3", ["-m", "codex.api", "--port", "0"], {...})` inheriting the host process's environment — not exercised inside a real Extension Host this cycle | No |
| Clone/open a real repository, trigger ingestion, reach READY | **UNVERIFIED** (GUI step) | The underlying API path (`_build_api()` → ingest → READY) was exercised for real this cycle, just not through the extension's own UI | No |
| Submit representative queries, verify responses/errors/loading states display correctly | **Real-browser evidence obtained** (see §4/§5 — the Webview itself, not the Extension Host chrome around it) | Real `askResult` (OK status, real answer, real claims, real notices) rendered correctly into the actual `answerSection` DOM in headless Chromium | No |
| No uncaught extension-host exceptions | **UNVERIFIED** (Extension Host itself never ran) — Webview-side: no `pageerror` events in either 2D or 3D real-browser run this cycle | Two real-browser page loads + a real `askResult` message, zero `pageerror` events | No |
| API-key handling does not expose the secret | Code-level review only | `openai_gateway.py`'s `_redact` scrubs `sk-...`-shaped tokens from any logged/error text via `_KEY_LIKE_RE`; key is read once per call from `Codex_open_API_key` and never placed in a webview-bound payload | No |
| Behavior after API restart/reconnect | **UNVERIFIED** (GUI step) | — | N/A |

---

## 4. 2D visualization matrix

**Real browser execution achieved** — headless Chromium (Playwright, `/opt/pw-browsers`) loading the actual compiled `renderShellHtml()` output (built by the extension's own `npx tsc -p ./`) served over a local HTTP origin (matching the isolation a VS Code Webview's own `vscode-webview://` scheme provides; `file://` was tried first and rejected by Chromium's cross-origin ES-module policy, which is itself a real, useful piece of evidence about what does *not* work). Seeded with a real `askResult` payload from a real ingested repository and a real OpenAI call (status `OK`, 4 real entities, 3 real CALLS edges — see full transcript context).

| Capability | Result | Runtime evidence | Defect? |
|---|---|---|---|
| Graph loads, nodes appear | PASS (after fix) | Real DOM: 4 `<circle>` elements inside a real `<svg>` | No (was: see defect #1) |
| Labels readable | PASS | Screenshot shows `main`, `other_helper`, `helper`, `helpers.py` text labels under each node | No |
| Directed edges visually directional | PASS (after fix) | Real DOM: 3 non-zero-length `<line>` elements with `marker-end="url(#codex-arrow)"`, 1 real `<marker>` arrowhead `<defs>` | No (was: see defect #1) |
| CALLS relationships distinguishable | PASS | All 3 edges are CALLS (single-color line + tooltip `"CALLS (confidence 1.00, SUPPORTED)"`); the relationship-type filter checkbox for CALLS renders and is checkable | No |
| REFERENCES relationships distinguishable | Not exercised (no REFERENCES edges in this evidence set) — code path is identical/generic (`edge.relationshipType` drives tooltip text and would drive the filter checkbox set) | Static: `renderRelationshipFilter` groups by `Array.from(new Set(model.edges.map(e => e.relationshipType)))`, so a second relationship type produces a second, independent checkbox — not separately proven live | No |
| Node selection | Not exercised live this cycle (click handlers wired and present in DOM: `.codex-node` elements have `cursor:pointer` and a real click listener bound to `onNodeClick`) | DOM inspection confirms listeners attached | No |
| Filtering | Present, not exercised live (checkbox exists and toggles `relationshipTypeFilter`) | DOM inspection confirms the CALLS checkbox renders, checked by default | No |
| Expansion/frontier requests | Not exercised this cycle (requires a live `/neighborhood` round trip through the extension host, out of scope for a single evidence-context render) | — | No |
| Bounded graph updates (large-graph notice) | Not exercised (this evidence graph has only 4 nodes, under the notice threshold) | — | No |
| Empty graph behavior | Verified via source + prior unit tests (`renderGraph()` early-returns to a `.empty` div when `model.nodes.length === 0`) | Not re-derived live this cycle | No |
| Loading state | Verified via source (`askStarted` message sets `answerSection` to an "asking…" badge) | Not re-derived live this cycle | No |
| Error state | Verified via source (`askError`/`fatalError` message handlers) | Not re-derived live this cycle | No |
| No browser-console/runtime exceptions | **PASS** | Zero `pageerror` events across both the pre-fix and post-fix real-browser runs; only benign SwiftShader/software-WebGL informational warnings (expected for headless software rendering, unrelated to 2D/SVG at all) | No |

**Defect #1 (found and fixed this cycle):** see §6.

Screenshots captured: `screenshot_2d.png` (before and after the fix; after-fix version shows 4 distinct, correctly connected, labeled nodes).

---

## 5. 3D visualization matrix

**WebGL capability was explicitly tested, not assumed.** A minimal Playwright script queried `canvas.getContext('webgl2')` in the same headless Chromium build: result `{"supported": true, "version": "webgl2", "renderer": "ANGLE (... SwiftShader Device ..., SwiftShader driver)"}`. This is real software-rendered WebGL2 (Google's SwiftShader rasterizer), not GPU-accelerated hardware WebGL — a real and useful distinction, reported honestly rather than glossed over.

| Capability | Result | Runtime evidence | Defect? |
|---|---|---|---|
| WebGL capability detection | **PASS — tested directly, real result** | `document.createElement('canvas').getContext('webgl2')` succeeds in this Chromium build via SwiftShader; `window.CodexGraph3D.isSupported()` (the exact function the production client script calls) reflects this | No |
| Graph initialization | PASS | Real `<canvas>` element created inside `#graphContainer` (640×735), a real `THREE.Scene`/`PerspectiveCamera`/`WebGLRenderer` constructed (confirmed by directly calling `CodexGraph3D.mount()`/`render()` and inspecting the returned scene graph) | No (was: see defect #2) |
| Nodes render | PASS (after fix) | Real `THREE.Mesh` (sphere) objects at distinct, non-collinear positions confirmed by walking `scene.traverse()` | No (was: see defect #2) |
| Edges render | PASS | `ConeGeometry`-based arrowhead + `LineBasicMaterial` line segments visible in the zoomed screenshot, connecting `main→other_helper`, `main→helper`, `other_helper→helper` | No |
| Directional semantics remain understandable | PASS | Real screenshot (`screenshot_3d_zoomed.png`) shows visible cone arrowheads at each edge's target end | No |
| Labels/UI controls behave correctly | PASS | Real CSS2D text labels ("main", "other_helper", "helper") rendered above each sphere in the actual screenshot; toolbar `3D`/`Reset view` buttons present and enabled (`supports3D === true` disabled them correctly when false, enabled when true) | No |
| Rotation/zoom/pan/orbit | Zoom exercised live (real `page.mouse.wheel()` event through `OrbitControls`); rotation/pan not separately exercised this cycle | Post-zoom screenshot shows the camera moved closer and the same 3 connected nodes remained visible and correctly connected — orbit controls are real and responsive | No |
| Node interaction (click/hover) | Not exercised live this cycle (handlers wired: `onNodeClick`/`onNodeHover` passed into `CodexGraph3D.render`) | — | No |
| Filtering | Same relationship-type-filter mechanism as 2D (shared `relationshipTypeFilter` state, passed into `CodexGraph3D.render`'s `opts`) — not separately exercised live for 3D | — | No |
| Graph expansion | Not exercised (same reasoning as 2D) | — | No |
| Large graph behavior | Not exercised (4-node evidence graph, under threshold) | — | No |
| Unsupported-WebGL fallback | Verified via source + the pre-existing decision function (`decideRenderMode` requires preferred+loaded+WebGL-capable, all three) — this container's Chromium *does* support WebGL, so the "unsupported" branch itself was not exercised live | No |
| Browser console/runtime errors | **PASS** | Zero `pageerror` events in either pre-fix or post-fix 3D runs; only benign SwiftShader driver warnings ("GPU stall due to ReadPixels," "Automatic fallback to software WebGL has been deprecated") — expected, informational, specific to software rendering, not a Codex defect | No |

**Defect #2 (found and fixed this cycle):** see §6. Per this phase's explicit instruction, 3D is **not** declared PASS from source/unit tests alone — every row above marked PASS is backed by an actual, this-cycle, real-browser observation (mesh positions read directly off the live `THREE.Scene`, or a real screenshot).

Screenshots: `screenshot_3d.png` (default camera framing — before and after fix), `screenshot_3d_zoomed.png` (after a real simulated zoom, showing all 3 connected nodes clearly separated and labeled).

---

## 6. Defects discovered

### Defect #1 — 2D graph: all-nodes-collapse-to-one-point for any `ask()`-evidence graph

- **Reproduction:** Ingest any real repository, ask any query that returns `status: OK` with ≥2 entities in `evidence_context`, render the 2D graph. Every node renders at the exact same SVG coordinate (verified: all 4 real nodes at `translate(320,367.5)`, every edge a zero-length `<line x1="320" y1="367.5" x2="320" y2="367.5">`).
- **Environment:** Reproduced in real headless Chromium (not just source reading) against real evidence data from a real OpenAI call.
- **Root cause:** `EvidenceContextSummary` (api/contracts.py) documents that it never tracks per-entity hop distance and always reports `distance: 0` for every entity in an `ask()` response's evidence context (this is an intentional, documented boundary limitation, not itself a bug). `layout2D` (`webviewAssets.ts`) mapped `distance === 0` to `radius = 0` unconditionally — correct for the single-seed-node case a `/neighborhood` explorer graph has, wrong the moment more than one node shares distance 0, which is *every* `ask()`-evidence render.
- **Affected component:** VS Code extension Webview, 2D SVG renderer only (`vscode-extension/src/webviewAssets.ts`). No graph/retrieval/grounding/ontology code involved.
- **Severity:** Real UI usability defect (the single most common graph view — the answer evidence panel — was unreadable), not a release blocker (no data corruption, no incorrect claims, no crash).
- **Smallest safe fix:** distance-0 buckets with more than one node now get a small non-zero radius (`70`) so they spread onto their own ring, exactly like every other distance bucket; buckets of size 1 (the ordinary single-seed case) are completely unchanged (`radius = 0`, verified unchanged by an explicit test case).
- **Regression test:** `vscode-extension/src/askPanelView.test.ts` — `"layout2D spreads multiple distance-0 nodes instead of collapsing them onto one point"` (runs `GRAPH_RENDERER_SCRIPT` in a real `vm` sandbox, asserts 4 distinct positions for 4 distance-0 nodes, and asserts the single-node case is unchanged at the exact center).

### Defect #2 — 3D graph: the same collapse, in a separately-maintained mirror copy

- **Reproduction:** Same trigger as Defect #1, 3D renderer selected. Real `THREE.Scene` inspection showed all node meshes at `x=0, z=0` (only `y`, the node-kind band, differed).
- **Environment:** Reproduced in real headless Chromium with real WebGL2 (SwiftShader), by directly inspecting the live `THREE.Scene` object graph3d.mjs actually constructs — not source reading, not a Node unit test.
- **Root cause:** Identical to Defect #1, but in a second, independent implementation. `media/graph3d.mjs` is a browser-only ES module (its own docstring explains it cannot import the Node/TS-compiled `layout3D.ts` because the browser has no bridge into `tsc`'s output) and instead maintains "a plain port... a deliberate, documented mirror." Fixing only `layout3D.ts` (the host-side, Node-tested copy) would have left the actually-shipped browser code with the bug — exactly the kind of drift the module's own docstring warns about, and exactly why executing the real browser module mattered: reading `layout3D.ts` and its passing tests alone would have been reported as PASS while the real Webview was still broken.
- **Affected component:** VS Code extension Webview, 3D Three.js renderer (`vscode-extension/media/graph3d.mjs`) and its host-side, Node-tested mirror (`vscode-extension/src/layout3D.ts`). No graph/retrieval/grounding/ontology code involved.
- **Severity:** Same as Defect #1 — real UI usability defect, not a release blocker.
- **Smallest safe fix:** identical fix applied to both copies (`RING_RADIUS_STEP / 2` radius when a distance-0 bucket has more than one node; unchanged for the single-node case), keeping the two implementations in sync as the module's own docstring requires.
- **Regression test:** `vscode-extension/src/layout3D.test.ts` — `"multiple nodes sharing distance 0 spread onto their own ring instead of collapsing at the origin"` (Node-native, tests the TS copy directly). The browser-only `graph3d.mjs` copy has **no Node-native regression test** — this project's existing test infrastructure has no browser/DOM test runner, and adding one is outside this validation phase's minimal-fix scope. Its fix is verified instead by the real-browser evidence in §5 (live scene inspection, before/after) captured in this report; this absence of automated coverage for `graph3d.mjs` specifically is disclosed as a real, standing gap in §7, not silently left implicit.

No other defects were found. No fix was applied to any file outside `vscode-extension/{media/graph3d.mjs, src/webviewAssets.ts, src/layout3D.ts}` plus their test files.

---

## 7. Environment limitations

**Verified this cycle (real execution):**
- 2D graph rendering in real headless Chromium, real graph data, real defect found and fixed
- 3D graph rendering in real headless Chromium with real (software) WebGL2, real defect found and fixed
- WebGL2 capability detection (tested directly, not assumed)
- Real ingestion + real OpenAI call + real grounding pass (`OK`) and a real ambiguity surfacing, on Linux
- Reversed-claim → `CLAIMS_NOT_GROUNDED` grounding regression, re-run live against real evidence

**Unverified — environment genuinely unavailable, not converted to PASS:**
- Windows OS execution of any kind (no Windows machine in this container) — installation, CLI/API startup, ingestion, WinError 5 reproduction, filesystem behavior under Windows path/locking semantics
- VS Code/Electron Extension Host launch, extension activation, and all GUI-driven steps of Phase 2 (no VS Code/Electron binary installed in this container, confirmed three independent ways)
- 3D/2D node-click, hover, filter-toggle, expand, and large-graph-notice interactions (wired and present in the DOM/API, not individually exercised via simulated clicks this cycle)
- Hardware-accelerated (non-software) WebGL — this container's Chromium only offers SwiftShader software rendering; a real GPU-backed browser was not available to test

**Intentionally unsupported (by design, not a gap):** none newly identified this cycle beyond what V1 closure already documented.

---

## 8. Regression results

All re-run after the two production fixes (§6), not just before them:

| Suite | Result |
|---|---|
| Python full suite | **1387/1387 passed** (unchanged from baseline; no Python code was touched this cycle) |
| Ruff | **All checks passed** |
| mypy | **Success: no issues found in 91 source files** |
| TypeScript tests (`npm test`) | **51/51 passed** (49 pre-existing + 2 new regression tests added this cycle, one for each defect) |
| tsc (`npx tsc -p ./`) | **Clean**, both before and after the fixes |
| Grounding regression (reversed-claim proof, live OpenAI call) | **PASS** — reversed CALLS claim over real evidence → `CLAIMS_NOT_GROUNDED`, never `OK`; all 3 claims still visible, unmodified |

No regression suite changed behavior as a result of this cycle's fixes — they are presentation-layer-only (SVG/Three.js node coordinate geometry) and touch nothing Python-side.

---

## 9. Repository integrity

- **Final commit context:** working tree was at V1 closure baseline `e66032c` at the start of this cycle; this cycle's changes are staged as uncommitted production fixes plus this report (see below for what to commit).
- **Files changed (production):**
  - `vscode-extension/src/webviewAssets.ts` (+6/-1) — `layout2D` distance-0 fix
  - `vscode-extension/src/layout3D.ts` (+7/-1) — `layout3D` distance-0 fix
  - `vscode-extension/media/graph3d.mjs` (+7/-1) — mirrored fix in the browser-only copy
- **Files changed (tests):**
  - `vscode-extension/src/askPanelView.test.ts` (+30) — 2D regression test
  - `vscode-extension/src/layout3D.test.ts` (+18) — 3D regression test
- **Files changed (docs):** this report; a `PROGRESS.md` entry (added alongside).
- **Protected subsystems — confirmed untouched:** `src/codex/graph/`, `src/codex/verification/`, `src/codex/planner/`, `src/codex/query_understanding/`, `src/codex/ontology/`, `src/codex/evidence/`, `src/codex/api/service.py`, `src/codex/api/contracts.py`, `src/codex/api/__main__.py` — zero diffs (confirmed via `git status`/`git diff --stat`: only the 5 `vscode-extension` files above changed).
- **Architectural behavior changed:** **No.** Both fixes are pure client-side node-coordinate geometry in the Webview's graph renderers; no wire contract, evidence shape, retrieval budget, query-understanding logic, or grounding-verification logic changed. `EvidenceContextSummary.distance` remaining always-0 for `ask()` responses is unchanged and undisputed — the fix works entirely within that existing, documented boundary.
- **Temporary artifacts:** all scratch repos (`/tmp/codex_ui_runtime_repo`, `/tmp/codex_v1_audit`), the local test HTTP server (port 8743), and a temporary `vscode-extension/ui-runtime-check.html` test harness file were all removed before finishing; `git status` shows no stray untracked files.

---

## 10. Final V1 acceptance recommendation

1. **Is Windows compatibility verified?** No — genuinely unverified by execution (no Windows environment available). Static code review found no obvious Windows anti-patterns (universal `pathlib.Path` usage, no hardcoded separators on filesystem paths, no subprocess/shell usage in production code). The previously observed "WinError 5" is a well-documented GitPython/Windows issue class, plausible given `git_adapter.py` never calls `.close()` on its `git.Repo` instances, but non-reproducible here and **not fixed speculatively**.
2. **Is VS Code runtime verified?** No — genuinely unverified by GUI execution (no VS Code/Electron installation exists in this container). Code compiles clean and all 51 Node-test-runner tests pass; the Webview content itself (the part that *can* run in a plain browser) was verified live — see Q3/Q4.
3. **Is 2D graph runtime verified?** **Yes** — real headless-Chromium execution, real graph data, one real defect found and fixed, fix confirmed by re-running the same real-browser test.
4. **Is 3D graph runtime verified?** **Yes**, with real (software/SwiftShader) WebGL2 — a real GPU-accelerated browser was not available and is disclosed as such. One real defect (present in two independently-maintained copies of the same layout logic) found and fixed in both, confirmed by direct `THREE.Scene` inspection and screenshots before/after.
5. **Are any discovered defects release blockers?** No. Both were UI node-overlap/edge-invisibility issues in the graph visualization panel, now fixed with regression coverage; neither affected correctness of ingestion, retrieval, claims, or grounding.
6. **Does the existing V1 architecture remain valid?** Yes. No graph/retrieval/grounding/ontology/verification code was touched or needed to be.
7. **Does OpenAI Claim Grounding Integrity remain intact?** Yes — re-verified live this cycle: a real OpenAI call over a fresh repository produced a correctly grounded `OK` answer, and a reversed CALLS claim over the same real evidence still produces `CLAIMS_NOT_GROUNDED`, never `OK`.
8. **Can Codex now move to final V1 acceptance/sign-off?** For the graph-visualization UI (2D and 3D): yes, on the strength of real runtime evidence gathered and two real defects fixed this cycle. For Windows OS support and the VS Code Extension Host GUI specifically: **not yet on the basis of this phase** — those remain genuinely unverified pending access to a real Windows machine and a real VS Code installation; nothing in this phase's findings suggests either would fail, but nothing here proves they would pass either, and this report deliberately does not convert that absence of evidence into a PASS.
