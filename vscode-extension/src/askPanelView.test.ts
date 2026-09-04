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

test("renderShellHtml escapes a repository id containing HTML-significant characters", () => {
  const html = renderShellHtml('<img src=x onerror="alert(1)">', FAKE_URIS);
  assert.ok(!html.includes('<img src=x onerror="alert(1)">'));
  assert.match(html, /&lt;img/);
});
