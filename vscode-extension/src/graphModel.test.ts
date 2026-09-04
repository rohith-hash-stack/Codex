/**
 * Unit tests for `graphModel.ts` (UI Integration Milestone). Pure
 * functions, no VS Code API dependency -- run with Node's built-in
 * test runner (`node --test`), no new devDependency added, matching
 * this project's existing "no unnecessary dependencies" discipline.
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { AskResponse, VisualizationGraph } from "./codexClient";
import { buildGraphModel, buildGraphModelFromEvidence, edgeExistsBetween, groundClaims, resolveClaimEndpoint } from "./graphModel";

function makeGraph(): VisualizationGraph {
  return {
    center: "foo",
    nodes: [
      {
        id: "codex:aaa",
        name: "foo",
        qualified_name: "pkg.foo",
        node_type: "FUNCTION",
        roles: [],
        language: "python",
        source_location: null,
        distance: 0,
      },
      {
        id: "codex:bbb",
        name: "bar",
        qualified_name: "pkg.bar",
        node_type: "FUNCTION",
        roles: [],
        language: "python",
        source_location: null,
        distance: 1,
      },
    ],
    edges: [
      {
        id: "codex:aaa|CALLS|codex:bbb",
        source: "codex:aaa",
        target: "codex:bbb",
        relationship_type: "CALLS",
        status: "SUPPORTED",
        confidence: 0.9,
        evidence_count: 1,
      },
    ],
    graph_version: { version_id: "v1", repository_id: "repo1", repository_revision: "abc123" },
    requested_depth: 1,
    truncated: false,
  };
}

test("buildGraphModel is a lossless, field-for-field projection", () => {
  const graph = makeGraph();
  const model = buildGraphModel(graph);

  assert.equal(model.center, "foo");
  assert.equal(model.nodes.length, 2);
  assert.equal(model.nodes[0].id, "codex:aaa");
  assert.equal(model.nodes[0].qualifiedName, "pkg.foo");
  assert.equal(model.nodes[1].distance, 1);
  assert.equal(model.edges.length, 1);
  assert.equal(model.edges[0].relationshipType, "CALLS");
  assert.equal(model.edges[0].confidence, 0.9);
  assert.equal(model.graphVersionId, "v1");
  assert.equal(model.requestedDepth, 1);
  assert.equal(model.truncated, false);
});

test("buildGraphModel never invents a node or edge not in the source graph", () => {
  const graph = makeGraph();
  const model = buildGraphModel(graph);
  assert.equal(model.nodes.length, graph.nodes.length);
  assert.equal(model.edges.length, graph.edges.length);
});

test("buildGraphModel handles a null graph_version honestly", () => {
  const graph = makeGraph();
  graph.graph_version = null;
  const model = buildGraphModel(graph);
  assert.equal(model.graphVersionId, null);
});

test("resolveClaimEndpoint matches by canonical_id", () => {
  const model = buildGraphModel(makeGraph());
  const node = resolveClaimEndpoint("codex:aaa", model.nodes);
  assert.ok(node);
  assert.equal(node?.name, "foo");
});

test("resolveClaimEndpoint matches by qualified_name", () => {
  const model = buildGraphModel(makeGraph());
  const node = resolveClaimEndpoint("pkg.bar", model.nodes);
  assert.ok(node);
  assert.equal(node?.id, "codex:bbb");
});

test("resolveClaimEndpoint matches by bare name", () => {
  const model = buildGraphModel(makeGraph());
  const node = resolveClaimEndpoint("foo", model.nodes);
  assert.ok(node);
  assert.equal(node?.id, "codex:aaa");
});

test("resolveClaimEndpoint returns undefined for text matching no real entity -- never fabricates a match", () => {
  const model = buildGraphModel(makeGraph());
  const node = resolveClaimEndpoint("totally_unrelated_symbol_xyz", model.nodes);
  assert.equal(node, undefined);
});

test("edgeExistsBetween finds a real edge by resolved endpoints and predicate", () => {
  const model = buildGraphModel(makeGraph());
  assert.equal(edgeExistsBetween("codex:aaa", "codex:bbb", "CALLS", model.edges), true);
});

test("edgeExistsBetween is false for a predicate that doesn't match any real edge", () => {
  const model = buildGraphModel(makeGraph());
  assert.equal(edgeExistsBetween("codex:aaa", "codex:bbb", "IMPLEMENTS", model.edges), false);
});

test("edgeExistsBetween is false when either endpoint failed to resolve", () => {
  const model = buildGraphModel(makeGraph());
  assert.equal(edgeExistsBetween(undefined, "codex:bbb", "CALLS", model.edges), false);
});

function makeAskResponse(): AskResponse {
  return {
    repository_id: "repo1",
    query_text: "What calls bar?",
    query_id: "q1",
    run_id: "r1",
    status: "OK",
    intent: "FIND_CALLERS",
    plan_status: "OK",
    answer: "foo calls bar",
    claims: [
      { subject: "pkg.foo", predicate: "CALLS", object: "pkg.bar", claim_type: "FACT" },
      { subject: "nonexistent_thing", predicate: "CALLS", object: "pkg.bar", claim_type: "FACT" },
    ],
    evidence_context: {
      graph_version: { version_id: "v1", repository_id: "repo1", repository_revision: "abc123" },
      entities: makeGraph().nodes,
      relationships: makeGraph().edges,
      evidence_count: 1,
      coverage: {},
      limitations: [],
      partial: false,
    },
    model: {
      provider: "openai",
      requested_model: "gpt-4o-mini",
      served_model: "gpt-4o-mini-2024-07-18",
      usage_prompt_tokens: 100,
      usage_completion_tokens: 20,
      usage_total_tokens: 120,
      finish_reason: "stop",
    },
    detail: null,
  };
}

test("groundClaims marks a claim with two real endpoints as grounded", () => {
  const groundings = groundClaims(makeAskResponse());
  assert.equal(groundings[0].endpointsGrounded, true);
  assert.equal(groundings[0].subjectNode?.id, "codex:aaa");
  assert.equal(groundings[0].objectNode?.id, "codex:bbb");
});

test("groundClaims marks a claim referencing a non-evidence entity as ungrounded, not fabricated-away", () => {
  const groundings = groundClaims(makeAskResponse());
  assert.equal(groundings[1].endpointsGrounded, false);
  assert.equal(groundings[1].subjectNode, undefined);
  assert.equal(groundings[1].objectNode?.id, "codex:bbb");
});

test("buildGraphModelFromEvidence projects EvidencePackage-shaped data the same way as a /neighborhood graph", () => {
  const response = makeAskResponse();
  const model = buildGraphModelFromEvidence(response.evidence_context, response.query_text);
  assert.equal(model.nodes.length, 2);
  assert.equal(model.edges.length, 1);
  assert.equal(model.requestedDepth, 0);
  assert.equal(model.truncated, false);
});
