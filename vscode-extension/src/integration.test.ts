/**
 * Real end-to-end integration test (UI Integration Milestone): spawns
 * the actual `python3 -m codex.api` server (the same command
 * `extension.ts` itself spawns) against a real temporary git
 * repository, and drives the real `CodexClient` over real HTTP against
 * it -- register -> ingest -> status -> symbols -> neighborhood ->
 * healthz, exactly the lifecycle+lookup+neighborhood surface this
 * extension already used before this milestone, now also proving the
 * TypeScript client parses every real response correctly.
 *
 * Deliberately does **not** call `/query`: that requires a configured
 * `OpenAIGateway` and a real `Codex_open_API_key` to produce anything
 * but a `502`/`503`, and this project's own security discipline
 * (`docs/api-hardening-audit.md`) is explicit that no test should
 * depend on a real API key being present or risk touching it. The
 * `/query` *client-side* contract (request shape, response parsing,
 * every error status) is already fully covered by `codexClient.test.ts`
 * against a fake server; the real backend's own `/query` behavior is
 * already covered by the Python test suite (1341 tests) and does not
 * need re-proving from the TypeScript side.
 *
 * Skips itself gracefully (a passing no-op, not a failure) if `python3`
 * or the `codex` package is not importable in this environment, so
 * this file does not break CI on a machine without the Python backend
 * installed alongside the extension.
 */

import assert from "node:assert/strict";
import { ChildProcessWithoutNullStreams, spawn, spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { after, before, test } from "node:test";
import { CodexClient } from "./codexClient";

let backendAvailable = false;
let repoDir: string;
let serverProcess: ChildProcessWithoutNullStreams | undefined;
let client: CodexClient;

function repoRoot(): string {
  // vscode-extension/src/integration.test.ts -> repository root
  return path.resolve(__dirname, "..", "..", "..");
}

before(async () => {
  const check = spawnSync("python3", ["-c", "import codex.api"], {
    cwd: repoRoot(),
    env: { ...process.env, PYTHONPATH: path.join(repoRoot(), "src") },
  });
  backendAvailable = check.status === 0;
  if (!backendAvailable) {
    return;
  }

  repoDir = fs.mkdtempSync(path.join(os.tmpdir(), "codex-ui-it-"));
  spawnSync("git", ["init", "-q"], { cwd: repoDir });
  fs.writeFileSync(
    path.join(repoDir, "app.py"),
    "def helper():\n    return 1\n\n\ndef main():\n    return helper()\n"
  );
  spawnSync("git", ["-c", "user.email=t@example.com", "-c", "user.name=t", "add", "app.py"], {
    cwd: repoDir,
  });
  spawnSync(
    "git",
    ["-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "init"],
    { cwd: repoDir }
  );

  const baseUrl = await new Promise<string>((resolve, reject) => {
    const proc = spawn("python3", ["-m", "codex.api", "--port", "0"], {
      cwd: repoRoot(),
      env: { ...process.env, PYTHONPATH: path.join(repoRoot(), "src") },
    });
    serverProcess = proc;
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        reject(new Error("timed out waiting for the Codex API server to start"));
      }
    }, 15000);
    proc.stdout.on("data", (chunk: Buffer) => {
      if (settled) return;
      const match = /CODEX_API_LISTENING (\S+) (\d+)/.exec(chunk.toString());
      if (match) {
        settled = true;
        clearTimeout(timer);
        resolve(`http://${match[1]}:${match[2]}`);
      }
    });
    proc.on("error", (err) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(err);
      }
    });
  });
  client = new CodexClient(baseUrl);
});

after(() => {
  serverProcess?.kill();
  if (repoDir && fs.existsSync(repoDir)) {
    fs.rmSync(repoDir, { recursive: true, force: true });
  }
});

test("healthz reports ok against the real server, independent of any repository", async (t) => {
  if (!backendAvailable) {
    t.skip("codex.api not importable in this environment");
    return;
  }
  const result = await client.healthz();
  assert.equal(result.status, "ok");
});

test("register -> ingest -> status -> symbols -> neighborhood, end to end against the real backend", async (t) => {
  if (!backendAvailable) {
    t.skip("codex.api not importable in this environment");
    return;
  }

  const registered = await client.registerAndIndex("ui-it-repo", repoDir);
  assert.ok(registered.job_id);

  const final = await client.waitForJob(registered.job_id);
  assert.equal(final.phase, "READY", final.detail ?? "");
  assert.ok(final.result);
  assert.equal(final.result?.phase, "READY");

  const status = await client.getRepositoryStatus("ui-it-repo");
  assert.equal(status.phase, "READY");
  assert.ok(status.graph_version_id);

  const lookup = await client.lookupSymbols("ui-it-repo", "helper");
  assert.equal(lookup.requested_depth, 0);
  assert.equal(lookup.edges.length, 0);
  assert.ok(lookup.nodes.some((n) => n.qualified_name.endsWith("helper")));

  const neighborhood = await client.getNeighborhood("ui-it-repo", "helper", 1);
  assert.ok(neighborhood.nodes.some((n) => n.qualified_name.endsWith("helper")));
  assert.ok(neighborhood.nodes.some((n) => n.qualified_name.endsWith("main")));
  assert.ok(neighborhood.edges.some((e) => e.relationship_type === "CALLS"));
});

test(
  "an entity's own full qualified_name now resolves directly via /neighborhood " +
    "(Canonical Identity Resolution fix -- AskPanel.runExpand no longer needs a bare-name fallback)",
  async (t) => {
    if (!backendAvailable) {
      t.skip("codex.api not importable in this environment");
      return;
    }
    const helperNode = (await client.lookupSymbols("ui-it-repo", "helper")).nodes.find((n) =>
      n.qualified_name.endsWith("helper")
    );
    assert.ok(helperNode, "setup: expected a real 'helper' entity from the previous test's repository");

    // Previously (UI Integration Milestone) this resolved zero nodes:
    // codex.planner.retrieval._resolve_one_target narrowed the
    // qualified_name axis to occurrences within just the *symbol*
    // portion (_symbol_path) before ever checking for an exact match,
    // so an entity's own full, file-path-prefixed qualified_name was
    // never "in" its own stripped-down slice. Fixed at the source
    // (docs/canonical-identity-resolution-fix.md) by computing the
    // exact-match check against the raw, unnarrowed lookup first.
    const byFullQualifiedName = await client.getNeighborhood(
      "ui-it-repo",
      helperNode!.qualified_name,
      1
    );
    assert.ok(
      byFullQualifiedName.nodes.some((n) => n.qualified_name.endsWith("helper")),
      "an entity's own full qualified_name must resolve to that entity directly, with no fallback"
    );
    assert.ok(byFullQualifiedName.nodes.some((n) => n.qualified_name.endsWith("main")));
    assert.ok(byFullQualifiedName.edges.some((e) => e.relationship_type === "CALLS"));
  }
);

test("querying an unknown repository returns a structured 404, not a transport error", async (t) => {
  if (!backendAvailable) {
    t.skip("codex.api not importable in this environment");
    return;
  }
  await assert.rejects(() => client.getNeighborhood("no-such-repo", "x"), (err: unknown) => {
    return err instanceof Error && /repository/i.test(err.message);
  });
});
