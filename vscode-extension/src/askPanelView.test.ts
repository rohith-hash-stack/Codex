/**
 * Tests for `askPanelView.ts` -- the "Ask Codex" Webview's generated
 * markup/script (UI Integration Milestone). This module deliberately
 * has no dependency on `vscode` (only resolvable inside a running VS
 * Code Extension Host), unlike `askPanel.ts`'s `AskPanel` class itself
 * (untestable with a plain Node test runner) -- so this file can prove
 * the generated Webview HTML/JS is well-formed with `node --test`.
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import vm from "node:vm";
import { CLIENT_SCRIPT, Graph3DUris, renderShellHtml } from "./askPanelView";
import { GRAPH_RENDERER_SCRIPT } from "./webviewAssets";

const FAKE_URIS: Graph3DUris = {
  three: "https://example.invalid/three.module.min.js",
  orbitControls: "https://example.invalid/OrbitControls.js",
  css2dRenderer: "https://example.invalid/CSS2DRenderer.js",
  graph3d: "https://example.invalid/graph3d.mjs",
};

test("CLIENT_SCRIPT is syntactically valid JavaScript", () => {
  assert.doesNotThrow(() => new Function(CLIENT_SCRIPT));
});

test("GRAPH_RENDERER_SCRIPT is syntactically valid JavaScript", () => {
  assert.doesNotThrow(() => new Function(GRAPH_RENDERER_SCRIPT));
});

test("renderShellHtml embeds the repository id and every script without breaking the document", () => {
  const html = renderShellHtml("my-repo", FAKE_URIS);
  assert.match(html, /<!DOCTYPE html>/);
  assert.match(html, /my-repo/);
  // Every opened <script ...> (the two inline classic scripts, plus the
  // new importmap + module scripts) must have a matching close, not
  // truncate the document via a stray "</script>" inside their own
  // string content.
  const scriptOpenCount = (html.match(/<script[ >]/g) || []).length;
  const scriptCloseCount = (html.match(/<\/script>/g) || []).length;
  assert.equal(scriptOpenCount, 4);
  assert.equal(scriptCloseCount, 4);
  assert.match(html, /"three":\s*"https:\/\/example\.invalid\/three\.module\.min\.js"/);
});

/**
 * Windows + UI Runtime Compatibility Validation phase: real headless-
 * Chromium evidence (see docs/v1-windows-ui-runtime-validation-report.md)
 * showed that a real ask()-evidence-context graph -- where every entity
 * reports `distance: 0` (`EvidenceContextSummary` does not track
 * per-entity hop distance, api/contracts.py) -- rendered all of its
 * nodes stacked on the exact same point and every edge as a zero-length,
 * invisible line. `layout2D` alone (no browser needed) reproduces and
 * proves the fix for the root cause: it collapsed every distance-0
 * bucket to `radius = 0`, correct for a single seed node but wrong once
 * multiple nodes share distance 0.
 */
test("layout2D spreads multiple distance-0 nodes instead of collapsing them onto one point", () => {
  const sandbox: { window: Record<string, unknown> } = { window: {} };
  vm.createContext(sandbox);
  vm.runInContext(GRAPH_RENDERER_SCRIPT, sandbox);
  const CodexGraph = sandbox.window.CodexGraph as {
    layout2D: (
      model: { nodes: Array<{ id: string; distance: number }> },
      width: number,
      height: number
    ) => Map<string, { x: number; y: number }>;
  };

  const model = {
    nodes: [
      { id: "a", distance: 0 },
      { id: "b", distance: 0 },
      { id: "c", distance: 0 },
      { id: "d", distance: 0 },
    ],
  };
  const positions = CodexGraph.layout2D(model, 640, 480);
  const unique = new Set(Array.from(positions.values()).map((p) => `${p.x},${p.y}`));
  assert.equal(unique.size, 4, "every node sharing distance 0 must get a distinct position");

  // A single distance-0 node (the ordinary /neighborhood seed case) must
  // remain exactly centered -- unchanged, additive-only fix. Compared
  // field-by-field, not via deepEqual: the position object was created
  // inside a separate vm realm, so it fails a cross-realm prototype
  // check despite being structurally identical.
  const single = CodexGraph.layout2D({ nodes: [{ id: "only", distance: 0 }] }, 640, 480);
  const singlePos = single.get("only") as { x: number; y: number };
  assert.equal(singlePos.x, 320);
  assert.equal(singlePos.y, 240);
});

test("renderShellHtml escapes a repository id containing HTML-significant characters", () => {
  const html = renderShellHtml('<img src=x onerror="alert(1)">', FAKE_URIS);
  assert.ok(!html.includes('<img src=x onerror="alert(1)">'));
  assert.match(html, /&lt;img/);
});
