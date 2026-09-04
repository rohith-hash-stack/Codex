# 3D Repository Intelligence Graph Milestone

Builds the first 3D interactive graph renderer on top of the UI Integration
Milestone's already-validated architecture (`docs/ui-integration-milestone.md`).
Per the explicit directive, **this is a visualization milestone, not a
graph/retrieval milestone**: no graph storage was accessed directly, no
Python backend file was modified, no graph ontology or relationship semantics
were added, and no retrieval/ranking/identity/evidence/LLM behavior changed.
The 2D SVG renderer (`webviewAssets.ts`'s `layout2D`/`render2D`) is kept,
unmodified, as the fallback.

```
repository -> ask a question -> see grounded evidence
    -> visualize the relevant subgraph (3D, or 2D fallback)
    -> explore/expand the repository graph interactively
```

## 1. Chosen 3D technology, and why

**Three.js `0.185.1`** (`vscode-extension/node_modules/three`, the one new
runtime dependency this milestone adds). Considered and rejected:

- **Hand-rolled raw WebGL.** Reimplementing a scene graph, camera controls
  (orbit/pan/zoom), and mouse-ray picking correctly from scratch is
  substantial, error-prone surface for a "first version, refine later"
  milestone — the opposite of "smallest appropriate technology."
- **A graph-specific framework** (e.g. `3d-force-graph`). Itself wraps
  Three.js plus an additional force-simulation dependency, adding more
  surface than this milestone's own deterministic, hop-distance-based
  layout (§3) actually needs — a force-directed physics simulation isn't
  part of the spec.

Three.js is confined entirely behind the renderer boundary: it is loaded
*only* inside the Ask Codex Webview's own browser context (as local, vendored
ES modules served through `webview.asWebviewUri`, never a network CDN — see
`askPanel.ts`'s own docstring), never imported by any Node/extension-host/
`tsc`-compiled code path. `localResourceRoots` is scoped to the extension's
own directory only.

## 2. Renderer architecture

The UI Integration Milestone already established the seam this milestone
fills in:

```
API response (VisualizationGraph)
    -> graphModel.ts: buildGraphModel()      -- pure, lossless projection (unchanged)
    -> layout3D.ts: layout3D(model)          -- {x, y, z} per node   (NEW, host-side, unit-tested)
    -> media/graph3d.mjs: render(...)        -- draws with Three.js  (NEW, Webview-only)
```

`graphModel.ts` was extended, not replaced: it gained `NodeKind` and
`classifyNodeKind()`, a direct, exhaustive, lossless mapping from the
server's real `codex.ontology.entities.BaseEntityType` values (e.g.
`FILE`/`MODULE`/`NAMESPACE` → `module`, `CLASS`/`INTERFACE` → `class`,
`FUNCTION`/`METHOD` → `function`, `TEST` → `test`, `EXTERNAL_LIBRARY` →
`external`, everything else → `other`) — grounded entirely in vocabulary the
API already returns, inventing nothing.

**Layout/render split, and why.** 3D layout (pure position arithmetic) and
3D rendering (Three.js/WebGL/DOM) are separable, and were deliberately kept
in two files:

- **`src/layout3D.ts`** — the canonical, unit-tested implementation. No
  Three.js/DOM/WebGL dependency at all, so it runs under Node's built-in
  test runner exactly like `graphModel.ts` itself.
- **`media/graph3d.mjs`** — a browser-only ES module (deliberately *not*
  TypeScript, *not* compiled by `tsc`, *not* run by `node --test`) that
  *ports* the same algorithm to actually draw with. This mirrors this
  project's own pre-existing precedent: `webviewAssets.ts`'s
  `GRAPH_RENDERER_SCRIPT` already duplicates small logic in plain browser JS
  because there is no module loader shared between the extension host and
  the Webview's own JS realm. Every mirrored constant/function says so in
  its own comment, pointing back at the tested original.

**Spatial semantics** (`layout3D.ts`): hop-distance from the query center
(`GraphModelNode.distance`, a real API field) becomes radial distance —
concentric XZ-plane rings, `RING_RADIUS_STEP * distance`, nodes at distance 0
sit at the ring's own center. Around each ring, nodes are spread by the
golden angle (`Math.PI * (3 - Math.sqrt(5))`, the same deterministic constant
a Fibonacci-sphere distribution uses) so same-ring nodes never collide, with
same-`NodeKind` nodes grouped into contiguous angular arcs for
readability. Each `NodeKind` also gets its own fixed vertical (Y) band
(module/class/function/test/external/other, all mutually distinct) so
modules, classes, functions, tests, and external dependencies visually
separate into stable layers instead of "random depth." Both inputs
(`distance`, `nodeType`) are real, already-returned API facts — nothing here
invents a relationship or groups nodes by anything the server didn't already
report, and the algorithm is fully deterministic (no `Math.random`): the
same model always produces the same view.

**Large-graph selection** (`layout3D.ts`'s `selectGraphForRender`, also
mirrored in `graph3d.mjs`): above `LARGE_GRAPH_NODE_THRESHOLD = 250` nodes,
selection refuses to pick a silent default — it returns `{rendered: false,
totalNodes}` instead of drawing a truncated subset, so the Webview app script
can show an explicit "Render all N nodes" / "Show closest 150" choice to the
user (`#largeGraphNotice` in `askPanelView.ts`). Neither the 250 cutoff nor
any closest-N cap ever drops data the user didn't explicitly ask to drop.

**Renderer fallback** (`layout3D.ts`'s `decideRenderMode`, mirrored in the
Webview's `graph3dAvailable()`/`renderGraph()`): 3D is used only when the
user prefers it *and* `graph3d.mjs` genuinely finished loading (a real,
independently-loaded ES module — `window.CodexGraph3D` exists) *and* this
Webview genuinely reports a WebGL context (`isSupported()`, checked
independently of whether the module import itself succeeded). Any one being
false falls back to the pre-existing 2D SVG renderer automatically. A manual
2D/3D toggle button (`#renderModeBtn`) always lets the user override the
automatic choice, and is itself disabled when 3D genuinely isn't usable in
the current environment.

**Module loading.** Modern Three.js (r150+) ships only ES-module addons, so
`OrbitControls`/`CSS2DRenderer` are loaded via a native
`<script type="importmap">` (mapping the bare `"three"`/`"three/addons/..."`
specifiers their own source files use to local `asWebviewUri` URLs) plus a
`<script type="module">` entry point — no bundler needed. Because module
scripts load/execute asynchronously (unlike the pre-existing inline classic
`<script>` blocks), the entry point dispatches a `codex-graph3d-ready`
window event the app script listens for, so a fast page-load-to-graph
sequence still gets 3D even if the module finishes loading slightly after
the first render attempt.

No custom Content-Security-Policy `<meta>` tag was added: the pre-existing
inline scripts already function correctly today without one, so VS Code's
own default Webview CSP already permits what's needed; adding a new CSP
surface risked silently breaking the already-working inline scripts for no
benefit, when `localResourceRoots` + `webview.asWebviewUri()` already keep
every asset local and vendored.

## 3. Graph interaction capabilities delivered

- **3D nodes and directed edges.** Nodes are spheres colored by
  `NodeKind`; edges are lines with a small cone arrowhead near the target
  end (directed), colored by the real `EvidenceStatus` (`SUPPORTED` →
  green ... `CONTRADICTED` → red) and opacity-scaled by the real
  `confidence` value — both derived only from fields the API already
  returns, never invented, so **the graph never implies an edge is
  evidence-supported unless the API says so**.
- **Pan/orbit/zoom.** `OrbitControls` with damping enabled, bounded
  min/max distance.
- **Node selection.** Click a node (raycaster-based picking) to select it —
  a larger radius and an emissive highlight distinguish it visually.
- **Hover information.** Pointer-move raycasting reports the hovered node's
  `qualifiedName`/`nodeType` into the toolbar hint area (`#graphHint`).
  Every node also carries a permanent `CSS2DObject` DOM label (its `name`),
  so labels stay legible without needing to hover at all.
- **Search and focus.** Reuses the existing `/symbols` search box and
  claim/entity-chip click targets unchanged — `focusEvidenceNode` re-selects
  and re-renders exactly as it did for the 2D renderer.
- **Progressive neighborhood expansion.** Clicking a node calls the real
  `GET /neighborhood` (via the existing `expand` message), never a
  client-side fabrication of new nodes/edges.
- **Collapse/reset.** A "Reset view" button restores the initial camera
  orbit/target (`resetView`); switching queries/searches naturally replaces
  the drawn subgraph.
- **Relationship-type filtering.** A checkbox row (`#relFilter`), populated
  from the distinct `relationshipType` values actually present in the
  current model, filters which edges draw — a pure display filter over
  already-fetched data, never a new server call.
- **Evidence highlighting.** In evidence mode, the query's seed nodes
  (`distance === 0`, the entities the LLM's evidence context actually
  contained) render with a distinct highlight — identical semantics to the
  2D renderer's own `highlightIds`, just drawn differently.
- **Navigation from graph node back to source/evidence.** Clicking a node
  shows its real `sourceLocation` (`file_path:line`) in the toolbar hint;
  if the clicked node (while exploring) is also part of the original
  evidence set, the view jumps back into evidence mode centered on it,
  rather than only ever drilling further outward.
- **Readable labels/tooltips.** Real DOM (`CSS2DObject`) labels, not
  billboarded sprite textures — crisp at any zoom level, themed via VS
  Code's own CSS custom properties.
- **Large neighborhoods.** See §2's `selectGraphForRender` — explicit,
  user-controlled, never a silent drop.

## 4. Performance observations

Tested against three shapes of `GraphModel`, exercised through
`layout3D.test.ts`/`layout3D.ts`'s own logic (identical to what
`graph3d.mjs` runs in the browser, since it mirrors the same functions):

- **Small** (3 nodes, 1 edge — a typical single-hop evidence snapshot):
  positions computed in under a millisecond; trivially fast to render.
- **Medium** (12 nodes on one ring — a typical multi-caller neighborhood):
  no position collisions (regression-tested), golden-angle spread keeps
  same-kind nodes visually grouped without overlap.
- **High-node/high-edge** (251+ nodes — above `LARGE_GRAPH_NODE_THRESHOLD`):
  `selectGraphForRender` refuses to draw anything until the user picks
  "render all" or "closest 150," exactly the explicit-limits requirement —
  verified both above and at the threshold boundary
  (`LARGE_GRAPH_NODE_THRESHOLD` itself renders fully; one more does not).

**What could not be measured in this environment**: actual browser frame
rates / GPU behavior. This sandboxed environment has no VS Code Extension
Host or GPU-backed browser to launch and profile live — see §6.

## 5. Fallback behavior

`graph3d.mjs`'s `isSupported()` checks real WebGL canvas-context
availability, independent of whether the module import itself even
succeeded. The Webview's `graph3dAvailable()` requires both
`window.CodexGraph3D` to exist (the module finished loading) *and*
`isSupported()` to be true before ever attempting 3D; either being false
routes to the unmodified `CodexGraph.render2D` (SVG) path automatically, no
error surfaced to the user. The render-mode toggle button reflects and lets
the user override this at any time, and disables itself when 3D genuinely
isn't available so the user isn't offered a mode that cannot work.

## 6. Screenshots / demo evidence

**Not captured.** This is a headless, sandboxed development environment
with no VS Code Extension Host and no GPU-backed browser available to launch
and screenshot — the same honest limitation this project's prior milestones
have flagged rather than claiming unverifiable success. What *was* verified
directly: `tsc` compiles the whole extension cleanly, every TypeScript test
passes (including the new layout/selection/fallback-decision logic that
`graph3d.mjs` mirrors), and the vendored Three.js/OrbitControls/CSS2DRenderer
files the Webview's import map points at are present on disk at the exact
paths `askPanel.ts` computes. Live, in-VS-Code visual verification (pan/
orbit/zoom feel, label legibility, actual frame rate) is recommended before
this ships to end users, and is flagged here explicitly rather than silently
skipped.

## 7. Tests and validation results

**TypeScript** (`npm test`, Node's built-in test runner — no new
devDependency for testing itself): **49/49 passing** (was 30 before this
milestone; +19 new):

- `layout3D.test.ts` (new, 21 tests): every node gets a position;
  distance-0 nodes sit at the origin ring; radial distance increases
  monotonically with hop distance; distinct `NodeKind`s land on distinct,
  mutually-exclusive fixed Y bands; no same-ring position collisions;
  determinism across repeated calls; empty-model and single-node edge
  cases don't throw; `selectGraphForRender`'s large-graph guard (at/above/
  forced-past the threshold), closest-N capping (including that a capped
  node's own edges are dropped, never left dangling), relationship-type
  filtering (edges only, never nodes), and empty-model handling;
  `decideRenderMode`'s full 2x2x2 fallback-decision truth table.
- `graphModel.test.ts` (+2): `classifyNodeKind` maps every real
  `BaseEntityType` value correctly; an unrecognized future type falls
  through to `other` rather than throwing or silently misclassifying.
- `askPanelView.test.ts` (updated): `renderShellHtml` now takes the new
  `Graph3DUris` parameter; script-tag balance re-verified against the two
  new script tags (`importmap` + `module`) alongside the two pre-existing
  inline scripts; HTML-escaping regression preserved.
- `codexClient.test.ts`, `integration.test.ts` — unchanged, still passing.

**Python backend** (unchanged, re-run per the directive): **1351/1351
passing**, `ruff check src tests scripts` clean, `mypy src` clean (91 source
files, run via `python3 -m mypy` — this environment's bare `mypy` on `PATH`
resolves to a different, pydantic-less interpreter than the one `pytest`
uses; `python3 -m mypy` is the correct invocation and the one that matters)
— zero regression, as expected since no file under `src/codex/` was touched
this milestone (`git status`/`git diff --stat` confirm this directly).
`tests/test_benchmark_canonical_corpus.py` and
`tests/test_benchmark_expansion_corpus.py` (the canonical-v1/
validation-expansion-v1 regression suites) ran as part of the full 1351,
all passing; every frozen fixture/benchmark-run artifact under
`tests/fixtures/benchmark/` and `benchmark_runs/` is byte-unchanged (no diff
at all — this milestone never touched them).

## 8. Dependencies added

**`three@0.185.1`** (`vscode-extension/package.json`), the one new runtime
dependency, justified in §1 and confined entirely behind the Webview
renderer boundary — never imported by `tsc`-compiled/extension-host code.
No new *test* dependency: `layout3D.test.ts` uses the same Node built-in
`node:test`/`node:assert` this project already used for every other TS test
file.

## 9. Confirmation: backend, semantics, retrieval, identity, evidence, LLM, and frozen benchmarks unchanged

- **No file under `src/codex/`** was created, modified, or deleted this
  milestone (`git status --porcelain -- src/codex` is empty).
- **No graph storage was accessed directly** — every fact this renderer
  draws already passed through the existing `CodexClient` HTTP calls
  (`POST /query`, `GET /neighborhood`) exactly as the UI Integration
  Milestone established; `graphModel.ts`'s pure, lossless projection is
  unmodified except for the additive `NodeKind`/`classifyNodeKind` export.
- **No new relationship semantics were added** — `classifyNodeKind` is a
  direct, exhaustive mapping from the real `BaseEntityType` enum already
  returned by the API; edge color/opacity come only from the real
  `relationshipType`/`status`/`confidence`/`evidenceCount` fields.
- **`/neighborhood` and `/query` were not modified** in any way to make
  visualization easier — no new query parameter, no new response field, no
  behavior change.
- **No UI-side inference of relationships** — every drawn edge is a real
  `GraphModelEdge` the server returned; `selectGraphForRender`'s filtering
  only ever removes what's drawn, never invents what draws.
- **Retrieval/ranking/identity/evidence/LLM behavior**: untouched — the
  full 1351/1351 Python suite (which exercises all of these) re-ran with
  zero regression, and `codex-canonical-v1`/`validation-expansion-v1` and
  every other frozen benchmark artifact remain byte-identical.

## 10. Scope explicitly deferred (unchanged from the directive)

Authentication/authorization (ADR-016), deployment (ADR-017),
multi-repository architecture, conversation history, graph
learning/embeddings, automatic semantic inference, planning/QA graphs, agent
behavior — none of these were touched or started.

## 11. What a future milestone should do next

- **Live, in-VS-Code visual verification** (§6) — pan/orbit/zoom feel,
  label legibility at various zoom levels, actual frame rate on a real
  high-node neighborhood, and a real screenshot/recording pass.
- **Directed-edge legibility at scale** — cone arrowheads work well for a
  few dozen edges; a genuinely dense 150-250-edge graph may benefit from a
  thinner default line width or an opacity floor, tunable once real usage
  data exists.
- **Persisting the user's render-mode/relationship-filter choice** across
  panel reopens (currently resets each time the panel is created) — not
  requested by this milestone's own directive, a small future refinement.
- Everything already listed as deferred in
  `docs/ui-integration-milestone.md` §7 that this milestone didn't touch
  (multi-repo support, conversation history, the two named ADRs).
