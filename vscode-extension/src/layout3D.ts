/**
 * Pure 3D layout computation (3D Repository Intelligence Graph
 * Milestone). Deliberately has no Three.js/WebGL/DOM dependency at all
 * -- it is plain arithmetic over a `GraphModel`, unit-testable with a
 * plain Node test runner exactly like `graphModel.ts` itself. The
 * browser-side 3D renderer (`media/graph3d.mjs`, loaded only inside the
 * Webview) ports this same algorithm to draw with, since Three.js
 * objects cannot cross the extension-host/Webview boundary -- mirroring
 * `webviewAssets.ts`'s own established `layout2D`/`render2D` split
 * (this file is the "layout" half; the Webview module is the
 * "renderer" half). Keeping the algorithm itself in one tested,
 * canonical TypeScript implementation means every positioning decision
 * is verified here, not only eyeballed in a running Webview.
 *
 * Spatial semantics (per the directive): hop-distance from the query
 * center becomes radial distance (concentric rings, like the existing
 * 2D layout) instead of "random depth" -- and each node's `NodeKind`
 * (`graphModel.ts`'s own direct `BaseEntityType` classification, not an
 * invented category) becomes a fixed vertical band, so modules/classes/
 * functions/tests/external dependencies visually separate into layers
 * a user can actually read. Both inputs (`distance`, `nodeType`) are
 * real API-returned facts; nothing here invents a relationship or
 * groups nodes by anything the server didn't already report.
 */

import { classifyNodeKind, GraphModel, GraphModelEdge, GraphModelNode, NodeKind } from "./graphModel";

export interface Position3D {
  x: number;
  y: number;
  z: number;
}

/** Above this many nodes, `selectGraphForRender` refuses to pick a
 * silent default -- the "make limits explicit and user-controlled"
 * directive. Mirrored in `media/graph3d.mjs` (see that file's own
 * docstring on why it can't simply import this module). */
export const LARGE_GRAPH_NODE_THRESHOLD = 250;

export interface RenderSelectionOptions {
  /** Bypasses the large-graph guard entirely -- the user explicitly
   * chose "render everything". */
  forceRenderAll?: boolean;
  /** When the large-graph guard applies and the user chose "closest N"
   * instead, caps to the `maxNodes` lowest-`distance` nodes. Also
   * usable below the threshold as a plain display cap. */
  maxNodes?: number | null;
  /** When set, only edges whose `relationshipType` is in the set are
   * kept (and, transitively, only nodes still reachable by a kept edge
   * -- see `nodes` below) -- a display filter over data already
   * fetched, never a new server call or an invented relationship. */
  relationshipTypeFilter?: Set<string> | null;
}

export interface RenderSelection {
  /** `false` means: too many nodes and the caller made no explicit
   * choice (`forceRenderAll`/`maxNodes`) -- draw nothing and surface
   * `totalNodes` so the UI can ask the user, rather than silently
   * dropping data. */
  rendered: boolean;
  totalNodes: number;
  nodes: GraphModelNode[];
  edges: GraphModelEdge[];
}

/**
 * Pure selection of which nodes/edges a render pass should actually
 * draw -- the large-graph guard, the "closest N" cap, and
 * relationship-type filtering, all as one testable function with zero
 * Three.js/DOM dependency. Never drops data silently: either every
 * node draws, or the caller's explicit `maxNodes`/`forceRenderAll`
 * choice decides what does.
 */
export function selectGraphForRender(model: GraphModel, opts: RenderSelectionOptions = {}): RenderSelection {
  const totalNodes = model.nodes.length;
  if (!opts.forceRenderAll && !opts.maxNodes && totalNodes > LARGE_GRAPH_NODE_THRESHOLD) {
    return { rendered: false, totalNodes, nodes: [], edges: [] };
  }

  let nodes = model.nodes;
  if (opts.maxNodes && nodes.length > opts.maxNodes) {
    nodes = [...nodes].sort((a, b) => a.distance - b.distance).slice(0, opts.maxNodes);
  }
  const nodeIds = new Set(nodes.map((n) => n.id));
  let edges = model.edges.filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target));
  if (opts.relationshipTypeFilter) {
    const filter = opts.relationshipTypeFilter;
    edges = edges.filter((e) => filter.has(e.relationshipType));
  }
  return { rendered: true, totalNodes, nodes, edges };
}

/** Fixed vertical band per `NodeKind` -- deliberately a constant
 * lookup table, not computed, so layering is stable and predictable
 * across every graph a user ever views. */
export const KIND_Y_BAND: Record<NodeKind, number> = {
  module: 200,
  class: 120,
  function: 40,
  test: -40,
  external: -120,
  other: -200,
};

const RING_RADIUS_STEP = 120;
/** The golden angle -- a standard, deterministic choice (not
 * `Math.random`) for spreading points evenly around a ring without
 * long-range alignment artifacts (the same constant a Fibonacci-sphere
 * point distribution uses). */
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

/**
 * Computes one deterministic `{x, y, z}` per node in `model`.
 *
 * - `x`/`z`: `distance`-based concentric ring (radius `0` for the
 *   query center itself, `RING_RADIUS_STEP * distance` for every hop
 *   beyond it), with nodes spread around the ring by the golden angle
 *   so same-ring nodes never overlap and same-kind nodes on the same
 *   ring cluster into contiguous angular arcs (grouped, not
 *   interleaved) -- an explicit design choice for readability, not an
 *   artifact of iteration order.
 * - `y`: `KIND_Y_BAND[classifyNodeKind(node)]`, a fixed layer.
 *
 * Pure and deterministic: identical `model` in, identical positions
 * out, every time -- required for both testability and a stable view
 * across repeated renders of the same data (never `Math.random`).
 */
export function layout3D(model: GraphModel): Map<string, Position3D> {
  const positions = new Map<string, Position3D>();
  const byDistance = new Map<number, GraphModelNode[]>();
  for (const node of model.nodes) {
    const bucket = byDistance.get(node.distance) ?? [];
    bucket.push(node);
    byDistance.set(node.distance, bucket);
  }

  for (const [distance, bucket] of byDistance) {
    const radius = distance === 0 ? 0 : RING_RADIUS_STEP * distance;
    const ordered = _groupByKindPreservingOrder(bucket);
    ordered.forEach((node, index) => {
      const angle = GOLDEN_ANGLE * index;
      const y = KIND_Y_BAND[classifyNodeKind(node)];
      positions.set(node.id, {
        x: radius * Math.cos(angle),
        y,
        z: radius * Math.sin(angle),
      });
    });
  }
  return positions;
}

/**
 * The renderer fallback decision (2D/3D toggle requirement): 3D is used
 * only when the user prefers it *and* the module genuinely finished
 * loading *and* this Webview genuinely has a WebGL context -- any one
 * of those being false falls back to the pre-existing 2D SVG renderer,
 * never a blocker. Pure and trivially testable in isolation; the
 * Webview's own `graph3dAvailable()`/`renderGraph()` (`askPanelView.ts`'s
 * `CLIENT_SCRIPT`) mirror this exact rule (`moduleLoaded` there is
 * `typeof window.CodexGraph3D !== "undefined"`, `webglSupported` is
 * `window.CodexGraph3D.isSupported()`) since the two can't share one
 * module across the extension-host/Webview boundary.
 */
export function decideRenderMode(
  preference: "3d" | "2d",
  moduleLoaded: boolean,
  webglSupported: boolean
): "3d" | "2d" {
  return preference === "3d" && moduleLoaded && webglSupported ? "3d" : "2d";
}

/** Stable-sorts `nodes` so same-`NodeKind` nodes become contiguous
 * (each kind's own relative order preserved) -- purely a display
 * grouping over data the model already carries, not a new
 * classification and not a mutation of `nodes` itself. */
function _groupByKindPreservingOrder(nodes: GraphModelNode[]): GraphModelNode[] {
  const buckets = new Map<NodeKind, GraphModelNode[]>();
  for (const node of nodes) {
    const kind = classifyNodeKind(node);
    const bucket = buckets.get(kind) ?? [];
    bucket.push(node);
    buckets.set(kind, bucket);
  }
  const kindOrder: NodeKind[] = ["module", "class", "function", "test", "external", "other"];
  const ordered: GraphModelNode[] = [];
  for (const kind of kindOrder) {
    const bucket = buckets.get(kind);
    if (bucket) {
      ordered.push(...bucket);
    }
  }
  return ordered;
}
