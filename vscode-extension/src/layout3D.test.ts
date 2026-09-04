/**
 * Unit tests for `layout3D.ts` (3D Repository Intelligence Graph
 * Milestone) -- pure arithmetic, no Three.js/DOM dependency, run with
 * Node's built-in test runner.
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { GraphModel, GraphModelEdge, GraphModelNode } from "./graphModel";
import { decideRenderMode, KIND_Y_BAND, LARGE_GRAPH_NODE_THRESHOLD, layout3D, selectGraphForRender } from "./layout3D";

function node(overrides: Partial<GraphModelNode> & { id: string }): GraphModelNode {
  return {
    name: overrides.id,
    qualifiedName: overrides.id,
    nodeType: "FUNCTION",
    roles: [],
    language: null,
    distance: 0,
    sourceLocation: null,
    ...overrides,
  };
}

function edge(overrides: Partial<GraphModelEdge> & { id: string; source: string; target: string }): GraphModelEdge {
  return {
    relationshipType: "CALLS",
    status: "SUPPORTED",
    confidence: 1,
    evidenceCount: 1,
    ...overrides,
  };
}

function model(nodes: GraphModelNode[], edges: GraphModelEdge[] = []): GraphModel {
  return {
    center: "x",
    nodes,
    edges,
    graphVersionId: null,
    requestedDepth: nodes.length ? Math.max(...nodes.map((n) => n.distance)) : 0,
    truncated: false,
  };
}

test("every node in the model receives a position", () => {
  const m = model([
    node({ id: "a", distance: 0 }),
    node({ id: "b", distance: 1 }),
    node({ id: "c", distance: 2 }),
  ]);
  const positions = layout3D(m);
  assert.equal(positions.size, 3);
  assert.ok(positions.has("a"));
  assert.ok(positions.has("b"));
  assert.ok(positions.has("c"));
});

test("the query center (distance 0) sits at the origin ring (radius 0)", () => {
  const m = model([node({ id: "center", distance: 0 })]);
  const p = layout3D(m).get("center")!;
  assert.equal(p.x, 0);
  assert.equal(p.z, 0);
});

test("radial distance from the origin increases monotonically with hop distance", () => {
  const m = model([
    node({ id: "d1", distance: 1 }),
    node({ id: "d2", distance: 2 }),
    node({ id: "d3", distance: 3 }),
  ]);
  const positions = layout3D(m);
  const radius = (id: string) => {
    const p = positions.get(id)!;
    return Math.sqrt(p.x * p.x + p.z * p.z);
  };
  assert.ok(radius("d1") < radius("d2"));
  assert.ok(radius("d2") < radius("d3"));
});

test("distinct node kinds are placed on distinct, fixed vertical bands", () => {
  const m = model([
    node({ id: "mod", distance: 1, nodeType: "MODULE" }),
    node({ id: "cls", distance: 1, nodeType: "CLASS" }),
    node({ id: "fn", distance: 1, nodeType: "FUNCTION" }),
    node({ id: "test", distance: 1, nodeType: "TEST" }),
    node({ id: "ext", distance: 1, nodeType: "EXTERNAL_LIBRARY" }),
  ]);
  const positions = layout3D(m);
  assert.equal(positions.get("mod")!.y, KIND_Y_BAND.module);
  assert.equal(positions.get("cls")!.y, KIND_Y_BAND.class);
  assert.equal(positions.get("fn")!.y, KIND_Y_BAND.function);
  assert.equal(positions.get("test")!.y, KIND_Y_BAND.test);
  assert.equal(positions.get("ext")!.y, KIND_Y_BAND.external);
  // Every band is genuinely distinct -- no two kinds silently collapse
  // onto the same height.
  const heights = new Set(Object.values(KIND_Y_BAND));
  assert.equal(heights.size, Object.keys(KIND_Y_BAND).length);
});

test("nodes on the same ring never collide at the same position", () => {
  const nodes = Array.from({ length: 12 }, (_, i) => node({ id: `n${i}`, distance: 1 }));
  const positions = layout3D(model(nodes));
  const seen = new Set<string>();
  for (const n of nodes) {
    const p = positions.get(n.id)!;
    const key = `${p.x.toFixed(3)},${p.y.toFixed(3)},${p.z.toFixed(3)}`;
    assert.ok(!seen.has(key), `duplicate position for ${n.id}`);
    seen.add(key);
  }
});

test("deterministic across repeated calls with the same model", () => {
  const m = model([
    node({ id: "a", distance: 0 }),
    node({ id: "b", distance: 1, nodeType: "CLASS" }),
    node({ id: "c", distance: 1, nodeType: "FUNCTION" }),
  ]);
  const first = layout3D(m);
  const second = layout3D(m);
  for (const [id, p1] of first) {
    const p2 = second.get(id)!;
    assert.deepEqual(p1, p2);
  }
});

test("an empty graph model produces an empty position map, not an error", () => {
  const positions = layout3D(model([]));
  assert.equal(positions.size, 0);
});

test("a single node at distance 0 does not throw and lands at the origin ring", () => {
  const positions = layout3D(model([node({ id: "solo", distance: 0 })]));
  const p = positions.get("solo")!;
  assert.equal(p.x, 0);
  assert.equal(p.z, 0);
});

// -- selectGraphForRender (3D Repository Intelligence Graph Milestone) ----

function manyNodes(count: number): GraphModelNode[] {
  return Array.from({ length: count }, (_, i) => node({ id: `n${i}`, distance: i % 5 }));
}

test("selectGraphForRender renders everything when at or under the large-graph threshold", () => {
  const m = model(manyNodes(LARGE_GRAPH_NODE_THRESHOLD));
  const result = selectGraphForRender(m, {});
  assert.equal(result.rendered, true);
  assert.equal(result.nodes.length, LARGE_GRAPH_NODE_THRESHOLD);
});

test("selectGraphForRender refuses to pick a silent default above the threshold -- never drops data quietly", () => {
  const m = model(manyNodes(LARGE_GRAPH_NODE_THRESHOLD + 1));
  const result = selectGraphForRender(m, {});
  assert.equal(result.rendered, false);
  assert.equal(result.totalNodes, LARGE_GRAPH_NODE_THRESHOLD + 1);
  assert.equal(result.nodes.length, 0);
  assert.equal(result.edges.length, 0);
});

test("selectGraphForRender renders all nodes above the threshold when forceRenderAll is explicitly chosen", () => {
  const m = model(manyNodes(LARGE_GRAPH_NODE_THRESHOLD + 1));
  const result = selectGraphForRender(m, { forceRenderAll: true });
  assert.equal(result.rendered, true);
  assert.equal(result.nodes.length, LARGE_GRAPH_NODE_THRESHOLD + 1);
});

test("selectGraphForRender caps to the closest maxNodes by hop distance when explicitly chosen", () => {
  const m = model(manyNodes(LARGE_GRAPH_NODE_THRESHOLD + 1));
  const result = selectGraphForRender(m, { maxNodes: 10 });
  assert.equal(result.rendered, true);
  assert.equal(result.nodes.length, 10);
  for (const n of result.nodes) assert.ok(n.distance <= 1, `expected only near nodes, got distance=${n.distance}`);
});

test("selectGraphForRender drops edges whose endpoint was excluded by maxNodes -- never a dangling edge", () => {
  const nodes = [node({ id: "a", distance: 0 }), node({ id: "b", distance: 5 })];
  const edges = [edge({ id: "a-b", source: "a", target: "b" })];
  const result = selectGraphForRender(model(nodes, edges), { maxNodes: 1 });
  assert.equal(result.nodes.length, 1);
  assert.equal(result.edges.length, 0);
});

test("selectGraphForRender applies relationshipTypeFilter as a pure display filter over already-fetched edges", () => {
  const nodes = [node({ id: "a" }), node({ id: "b" }), node({ id: "c" })];
  const edges = [
    edge({ id: "a-b", source: "a", target: "b", relationshipType: "CALLS" }),
    edge({ id: "a-c", source: "a", target: "c", relationshipType: "IMPORTS" }),
  ];
  const result = selectGraphForRender(model(nodes, edges), { relationshipTypeFilter: new Set(["CALLS"]) });
  assert.equal(result.edges.length, 1);
  assert.equal(result.edges[0].relationshipType, "CALLS");
  // Filtering edges never removes nodes -- only which relationships draw.
  assert.equal(result.nodes.length, 3);
});

test("selectGraphForRender on an empty model renders trivially, not an error", () => {
  const result = selectGraphForRender(model([]), {});
  assert.equal(result.rendered, true);
  assert.equal(result.totalNodes, 0);
  assert.equal(result.nodes.length, 0);
  assert.equal(result.edges.length, 0);
});

// -- decideRenderMode (renderer fallback behavior) -------------------------

test("decideRenderMode uses 3D only when preferred, loaded, and WebGL-capable -- all three required", () => {
  assert.equal(decideRenderMode("3d", true, true), "3d");
  assert.equal(decideRenderMode("3d", false, true), "2d");
  assert.equal(decideRenderMode("3d", true, false), "2d");
  assert.equal(decideRenderMode("3d", false, false), "2d");
});

test("decideRenderMode respects an explicit 2D preference even when 3D is fully available", () => {
  assert.equal(decideRenderMode("2d", true, true), "2d");
});
