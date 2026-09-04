/**
 * Tests for `CodexClient` (UI Integration Milestone) against a real
 * (fake, in-process) HTTP server built from Node's own `http` module --
 * no new devDependency, mirroring the "dependency-free by design"
 * precedent already established on the Python side (`codex.llm.
 * openai_gateway`'s own docstring). Proves the client's request/
 * response handling for every new endpoint (`/query`, `/healthz`) and
 * every error status the real `codex.api.server` can return, without
 * needing the real Python backend or a real LLM.
 */

import assert from "node:assert/strict";
import * as http from "node:http";
import { AddressInfo } from "node:net";
import { after, before, test } from "node:test";
import { AskResponse, CodexApiError, CodexClient } from "./codexClient";

let server: http.Server;
let client: CodexClient;
let lastRequest: { method: string; url: string; body: unknown } | undefined;
let nextResponse: { status: number; body: unknown } = { status: 200, body: {} };

before(async () => {
  server = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf-8");
      lastRequest = {
        method: req.method ?? "",
        url: req.url ?? "",
        body: raw ? JSON.parse(raw) : undefined,
      };
      const payload = JSON.stringify(nextResponse.body);
      res.writeHead(nextResponse.status, { "Content-Type": "application/json" });
      res.end(payload);
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as AddressInfo;
  client = new CodexClient(`http://127.0.0.1:${address.port}`);
});

after(async () => {
  await new Promise<void>((resolve) => server.close(() => resolve()));
});

test("healthz() calls GET /healthz and returns the parsed body", async () => {
  nextResponse = { status: 200, body: { status: "ok" } };
  const result = await client.healthz();
  assert.equal(result.status, "ok");
  assert.equal(lastRequest?.method, "GET");
  assert.equal(lastRequest?.url, "/healthz");
});

test("ask() posts repository_id and query_text to /query", async () => {
  const askResponse: AskResponse = {
    repository_id: "repo1",
    query_text: "What calls bar?",
    query_id: "q1",
    run_id: "r1",
    status: "OK",
    intent: "FIND_CALLERS",
    plan_status: "OK",
    answer: "foo calls bar",
    claims: [],
    evidence_context: {
      graph_version: null,
      entities: [],
      relationships: [],
      evidence_count: 0,
      coverage: {},
      limitations: [],
      partial: false,
    },
    model: {
      provider: "openai",
      requested_model: "gpt-4o-mini",
      served_model: "gpt-4o-mini-2024-07-18",
      usage_prompt_tokens: 10,
      usage_completion_tokens: 5,
      usage_total_tokens: 15,
      finish_reason: "stop",
    },
    detail: null,
  };
  nextResponse = { status: 200, body: askResponse };
  const result = await client.ask("repo1", "What calls bar?");
  assert.equal(lastRequest?.method, "POST");
  assert.equal(lastRequest?.url, "/query");
  assert.deepEqual(lastRequest?.body, { repository_id: "repo1", query_text: "What calls bar?" });
  assert.equal(result.status, "OK");
  assert.equal(result.answer, "foo calls bar");
});

test("ask() includes optional token_budget/latency_budget_ms only when given", async () => {
  nextResponse = { status: 200, body: { ...emptyOkAskResponse() } };
  await client.ask("repo1", "q", { tokenBudget: 2000, latencyBudgetMs: 3000 });
  assert.deepEqual(lastRequest?.body, {
    repository_id: "repo1",
    query_text: "q",
    token_budget: 2000,
    latency_budget_ms: 3000,
  });
});

test("a 409 (repository not ready) rejects with CodexApiError carrying status 409", async () => {
  nextResponse = { status: 409, body: { error: "repository 'repo1' is not ready for querying yet (phase=INDEXING)" } };
  await assert.rejects(
    () => client.ask("repo1", "q"),
    (err: unknown) => {
      assert.ok(err instanceof CodexApiError);
      assert.equal(err.status, 409);
      assert.match(err.message, /phase=INDEXING/);
      return true;
    }
  );
});

test("a 503 (no LLM configured) rejects with CodexApiError carrying status 503", async () => {
  nextResponse = { status: 503, body: { error: "this CodexAPI instance was constructed without an LLMGateway" } };
  await assert.rejects(
    () => client.ask("repo1", "q"),
    (err: unknown) => {
      assert.ok(err instanceof CodexApiError);
      assert.equal((err as CodexApiError).status, 503);
      return true;
    }
  );
});

test("a 502 (upstream LLM failure) rejects with CodexApiError carrying status 502", async () => {
  nextResponse = { status: 502, body: { error: "LLM authentication failed: ..." } };
  await assert.rejects(
    () => client.ask("repo1", "q"),
    (err: unknown) => {
      assert.ok(err instanceof CodexApiError);
      assert.equal((err as CodexApiError).status, 502);
      return true;
    }
  );
});

test("a 400 (invalid request) rejects with CodexApiError carrying status 400", async () => {
  nextResponse = { status: 400, body: { error: "'repository_id' is required and must be a string" } };
  await assert.rejects(
    () => client.lookupSymbols("", ""),
    (err: unknown) => {
      assert.ok(err instanceof CodexApiError);
      assert.equal((err as CodexApiError).status, 400);
      return true;
    }
  );
});

test("registerAndIndex posts to /repositories with the right shape", async () => {
  nextResponse = { status: 202, body: { job_id: "job-1", repository_id: "repo1" } };
  const handle = await client.registerAndIndex("repo1", "/tmp/repo");
  assert.equal(lastRequest?.method, "POST");
  assert.equal(lastRequest?.url, "/repositories");
  assert.deepEqual(lastRequest?.body, {
    repository_id: "repo1",
    local_path: "/tmp/repo",
  });
  assert.equal(handle.job_id, "job-1");
});

test("getNeighborhood builds the expected query string", async () => {
  nextResponse = { status: 200, body: emptyGraph() };
  await client.getNeighborhood("repo1", "pkg.foo", 2);
  assert.equal(lastRequest?.method, "GET");
  assert.equal(lastRequest?.url, "/neighborhood?repository_id=repo1&symbol=pkg.foo&depth=2");
});

function emptyGraph() {
  return { center: "x", nodes: [], edges: [], graph_version: null, requested_depth: 0, truncated: false };
}

function emptyOkAskResponse(): AskResponse {
  return {
    repository_id: "repo1",
    query_text: "q",
    query_id: "q1",
    run_id: "r1",
    status: "OK",
    intent: null,
    plan_status: null,
    answer: null,
    claims: [],
    evidence_context: {
      graph_version: null,
      entities: [],
      relationships: [],
      evidence_count: 0,
      coverage: {},
      limitations: [],
      partial: false,
    },
    model: {
      provider: "openai",
      requested_model: "gpt-4o-mini",
      served_model: null,
      usage_prompt_tokens: null,
      usage_completion_tokens: null,
      usage_total_tokens: null,
      finish_reason: null,
    },
    detail: null,
  };
}
