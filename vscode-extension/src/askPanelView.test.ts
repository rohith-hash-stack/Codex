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
import { CLIENT_SCRIPT, renderShellHtml } from "./askPanelView";
import { GRAPH_RENDERER_SCRIPT } from "./webviewAssets";

test("CLIENT_SCRIPT is syntactically valid JavaScript", () => {
  assert.doesNotThrow(() => new Function(CLIENT_SCRIPT));
});

test("GRAPH_RENDERER_SCRIPT is syntactically valid JavaScript", () => {
  assert.doesNotThrow(() => new Function(GRAPH_RENDERER_SCRIPT));
});

test("renderShellHtml embeds the repository id and both scripts without breaking the document", () => {
  const html = renderShellHtml("my-repo");
  assert.match(html, /<!DOCTYPE html>/);
  assert.match(html, /my-repo/);
  // Both scripts must appear as literal <script> bodies, not truncate
  // the document via a stray "</script>" inside their own string content.
  const scriptOpenCount = (html.match(/<script>/g) || []).length;
  const scriptCloseCount = (html.match(/<\/script>/g) || []).length;
  assert.equal(scriptOpenCount, 2);
  assert.equal(scriptCloseCount, 2);
});

test("renderShellHtml escapes a repository id containing HTML-significant characters", () => {
  const html = renderShellHtml('<img src=x onerror="alert(1)">');
  assert.ok(!html.includes('<img src=x onerror="alert(1)">'));
  assert.match(html, /&lt;img/);
});
