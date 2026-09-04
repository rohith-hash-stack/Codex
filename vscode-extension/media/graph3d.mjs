/**
 * 3D graph renderer (3D Repository Intelligence Graph Milestone).
 *
 * Loaded *only* inside the Ask Codex Webview, as a browser ES module --
 * never in the extension host, never compiled by `tsc`, never touching
 * Node/VS Code APIs. Imports `three` and two of its own small addons
 * (`OrbitControls`, `CSS2DRenderer`) via an import map `askPanelView.ts`
 * embeds, all resolved to local files under this extension's own
 * `node_modules/three` through `webview.asWebviewUri` -- nothing is
 * ever fetched from a network CDN (`askPanel.ts`'s own docstring has
 * the full isolation rationale).
 *
 * **Why Three.js**: the smallest realistic way to get real WebGL 3D
 * (nodes, directed edges, camera orbit/pan/zoom, mouse picking) without
 * hand-rolling a WebGL pipeline from scratch -- a large, well-tested
 * surface this milestone would otherwise have to reimplement and debug
 * from zero. No graph-specific framework (e.g. 3d-force-graph) was
 * added on top of it: this module's own `layout()` (a plain port of
 * the host-side, unit-tested `layout3D.ts` -- Three.js has no float
 * math or DOM dependency requirement here, but a *second*, separately
 * tested implementation would drift, so this one is a deliberate,
 * documented mirror, exactly like `webviewAssets.ts`'s existing
 * `layout2D`/`render2D` split) computes positions directly.
 *
 * **Data discipline**: this module never invents a node, edge, or
 * relationship -- it only draws the `GraphModel`-shaped plain object
 * (`{center, nodes, edges, requestedDepth, truncated}`) the app script
 * (`askPanelView.ts`'s `CLIENT_SCRIPT`) already built from a real API
 * response, the identical object also handed to the 2D renderer
 * (`CodexGraph.render2D`). Node "kind" grouping comes only from the
 * server's own `nodeType` (`BaseEntityType`); edge color/highlighting
 * comes only from the server's own `relationshipType`/`status`/
 * `confidence`/`evidenceCount` -- nothing here is a new semantic label.
 */

import * as THREE from "three";
import { CSS2DObject, CSS2DRenderer } from "three/addons/renderers/CSS2DRenderer.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

// -- Layout (mirrors layout3D.ts -- see that file's own tests) ------------

const KIND_Y_BAND = { module: 200, class: 120, function: 40, test: -40, external: -120, other: -200 };
const RING_RADIUS_STEP = 120;
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

const MODULE_TYPES = new Set(["REPOSITORY", "DIRECTORY", "FILE", "MODULE", "NAMESPACE"]);
const CLASS_TYPES = new Set(["CLASS", "INTERFACE"]);
const FUNCTION_TYPES = new Set(["FUNCTION", "METHOD"]);

export function classifyNodeKind(node) {
  if (node.nodeType === "TEST") return "test";
  if (node.nodeType === "EXTERNAL_LIBRARY") return "external";
  if (MODULE_TYPES.has(node.nodeType)) return "module";
  if (CLASS_TYPES.has(node.nodeType)) return "class";
  if (FUNCTION_TYPES.has(node.nodeType)) return "function";
  return "other";
}

function layout(model) {
  const positions = new Map();
  const byDistance = new Map();
  for (const node of model.nodes) {
    const bucket = byDistance.get(node.distance) || [];
    bucket.push(node);
    byDistance.set(node.distance, bucket);
  }
  for (const [distance, bucket] of byDistance) {
    const radius = distance === 0 ? 0 : RING_RADIUS_STEP * distance;
    const kindOrder = ["module", "class", "function", "test", "external", "other"];
    const grouped = [];
    for (const kind of kindOrder) {
      for (const node of bucket) {
        if (classifyNodeKind(node) === kind) grouped.push(node);
      }
    }
    grouped.forEach((node, index) => {
      const angle = GOLDEN_ANGLE * index;
      positions.set(node.id, {
        x: radius * Math.cos(angle),
        y: KIND_Y_BAND[classifyNodeKind(node)],
        z: radius * Math.sin(angle),
      });
    });
  }
  return positions;
}

// -- Visual encoding (all derived from real API fields only) --------------

const KIND_COLOR = {
  module: 0x4f9dff,
  class: 0xba7dff,
  function: 0x6bbf6b,
  test: 0xffb84f,
  external: 0x999999,
  other: 0xcccccc,
};

const STATUS_COLOR = {
  SUPPORTED: 0x4caf50,
  WEAKLY_SUPPORTED: 0x8bc34a,
  DISPUTED: 0xff9800,
  UNRESOLVED: 0x9e9e9e,
  CONTRADICTED: 0xf44336,
  UNSUPPORTED: 0x757575,
};

const LARGE_GRAPH_NODE_THRESHOLD = 250;

/** Mirrors `layout3D.ts`'s `selectGraphForRender` exactly (same
 * threshold, same "closest N by distance" tie-break, same filter
 * semantics) -- that function is the tested, canonical one; this is
 * the untestable-in-Node browser copy, per this file's own docstring
 * on why the two can't share one module. */
function selectGraphForRender(model, opts) {
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
    edges = edges.filter((e) => opts.relationshipTypeFilter.has(e.relationshipType));
  }
  return { rendered: true, totalNodes, nodes, edges };
}

/** WebGL availability, checked independently of whether the Three.js
 * import itself even succeeded -- the app script also treats "this
 * module never finished loading at all" as a fallback trigger; this
 * export covers the narrower "loaded fine, but no WebGL context here"
 * case. */
export function isSupported() {
  try {
    const canvas = document.createElement("canvas");
    return !!(
      window.WebGLRenderingContext &&
      (canvas.getContext("webgl2") || canvas.getContext("webgl"))
    );
  } catch {
    return false;
  }
}

/** Creates the persistent Three.js scene/camera/renderer/controls bound
 * to `container`. Call once per container; reuse the returned state
 * across multiple `render()` calls (progressive expansion, filtering,
 * selection) rather than re-creating the whole scene each time. */
export function mount(container) {
  const width = container.clientWidth || 900;
  const height = container.clientHeight || 560;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(55, width / height, 0.1, 5000);
  camera.position.set(260, 220, 260);

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setSize(width, height);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  container.appendChild(renderer.domElement);

  const labelRenderer = new CSS2DRenderer();
  labelRenderer.setSize(width, height);
  labelRenderer.domElement.style.position = "absolute";
  labelRenderer.domElement.style.top = "0";
  labelRenderer.domElement.style.pointerEvents = "none";
  container.style.position = "relative";
  container.appendChild(labelRenderer.domElement);

  scene.add(new THREE.AmbientLight(0xffffff, 0.9));
  const dirLight = new THREE.DirectionalLight(0xffffff, 0.6);
  dirLight.position.set(200, 400, 300);
  scene.add(dirLight);

  const controls = new OrbitControls(camera, labelRenderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.minDistance = 20;
  controls.maxDistance = 2500;

  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();

  const state = {
    container,
    scene,
    camera,
    renderer,
    labelRenderer,
    controls,
    raycaster,
    pointer,
    nodeMeshes: new Map(),
    edgeGroup: null,
    nodeGroup: null,
    disposed: false,
    hoveredId: null,
    opts: {},
    resizeObserver: null,
  };

  const animate = () => {
    if (state.disposed) return;
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
    labelRenderer.render(scene, camera);
  };
  requestAnimationFrame(animate);

  const onPointerMove = (event) => {
    _updatePointer(state, event);
    const hit = _pickNode(state);
    const id = hit ? hit.userData.nodeId : null;
    if (id !== state.hoveredId) {
      state.hoveredId = id;
      if (state.opts.onNodeHover) state.opts.onNodeHover(id);
    }
  };
  const onClick = (event) => {
    _updatePointer(state, event);
    const hit = _pickNode(state);
    if (hit && state.opts.onNodeClick) state.opts.onNodeClick(hit.userData.nodeId);
  };
  renderer.domElement.addEventListener("pointermove", onPointerMove);
  renderer.domElement.addEventListener("click", onClick);

  const resizeObserver = new ResizeObserver(() => _resize(state));
  resizeObserver.observe(container);
  state.resizeObserver = resizeObserver;

  return state;
}

function _resize(state) {
  const width = state.container.clientWidth || 900;
  const height = state.container.clientHeight || 560;
  state.camera.aspect = width / height;
  state.camera.updateProjectionMatrix();
  state.renderer.setSize(width, height);
  state.labelRenderer.setSize(width, height);
}

function _updatePointer(state, event) {
  const rect = state.renderer.domElement.getBoundingClientRect();
  state.pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
  state.pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
}

function _pickNode(state) {
  state.raycaster.setFromCamera(state.pointer, state.camera);
  const meshes = Array.from(state.nodeMeshes.values());
  const hits = state.raycaster.intersectObjects(meshes, false);
  return hits.length > 0 ? hits[0].object : null;
}

/**
 * (Re)draws `model` into the scene `mount()` created. `opts`:
 *   - `selectedId`: string | null
 *   - `highlightIds`: string[] -- evidence-supported nodes (never drawn
 *     as highlighted unless the caller says so, per the directive's
 *     "never imply an edge/node is evidence-supported unless the API
 *     says so" -- callers only ever pass ids that came from a real
 *     `EvidenceContextSummary`).
 *   - `relationshipTypeFilter`: Set<string> | null -- when set, only
 *     edges whose `relationshipType` is in the set are drawn (and only
 *     nodes still reachable by a drawn edge, or within `highlightIds`/
 *     `selectedId`, or at distance 0, remain visible) -- a display
 *     filter over data already fetched, never a new server call.
 *   - `onNodeClick(id)`, `onNodeHover(id | null)`.
 *   - `forceRenderAll`: boolean -- bypasses the large-graph guard (see
 *     `LARGE_GRAPH_NODE_THRESHOLD`).
 *   - `maxNodes`: number | null -- when the large-graph guard applies
 *     and the caller chose "closest N" instead of "render all", caps
 *     to the `maxNodes` lowest-`distance` nodes (ties broken by the
 *     model's own existing order) -- explicit and caller-controlled,
 *     never a silent drop.
 * Returns `{ rendered: boolean, totalNodes: number, drawnNodes: number }`
 * so the caller can show a large-graph notice when `rendered` is false.
 */
export function render(state, model, opts) {
  opts = opts || {};
  state.opts = opts;

  const selection = selectGraphForRender(model, opts);
  if (!selection.rendered) {
    return { rendered: false, totalNodes: selection.totalNodes, drawnNodes: 0 };
  }
  const { nodes, edges } = selection;

  _clearGroup(state);
  const nodeGroup = new THREE.Group();
  const edgeGroup = new THREE.Group();
  state.nodeGroup = nodeGroup;
  state.edgeGroup = edgeGroup;
  state.nodeMeshes = new Map();
  state.scene.add(nodeGroup);
  state.scene.add(edgeGroup);

  const positions = layout({ ...model, nodes });
  const highlighted = new Set(opts.highlightIds || []);

  for (const node of nodes) {
    const p = positions.get(node.id);
    const kind = classifyNodeKind(node);
    const isSelected = node.id === opts.selectedId;
    const isHighlighted = highlighted.has(node.id);
    const radius = isSelected ? 9 : isHighlighted ? 7.5 : 6;
    const geometry = new THREE.SphereGeometry(radius, 20, 16);
    const material = new THREE.MeshStandardMaterial({
      color: KIND_COLOR[kind],
      emissive: isSelected ? 0xffffff : isHighlighted ? 0x333300 : 0x000000,
      emissiveIntensity: isSelected ? 0.4 : 0.6,
      roughness: 0.5,
      metalness: 0.1,
    });
    const mesh = new THREE.Mesh(geometry, material);
    mesh.position.set(p.x, p.y, p.z);
    mesh.userData.nodeId = node.id;
    nodeGroup.add(mesh);
    state.nodeMeshes.set(node.id, mesh);

    const label = document.createElement("div");
    label.textContent = node.name;
    label.style.cssText =
      "font-size:10px;color:var(--vscode-editor-foreground,#ccc);background:rgba(0,0,0,0.35);" +
      "padding:1px 4px;border-radius:2px;white-space:nowrap;pointer-events:none;";
    const labelObject = new CSS2DObject(label);
    labelObject.position.set(0, radius + 4, 0);
    mesh.add(labelObject);
  }

  for (const edge of edges) {
    const source = positions.get(edge.source);
    const target = positions.get(edge.target);
    if (!source || !target) continue;
    const color = STATUS_COLOR[edge.status] ?? 0x888888;
    const points = [
      new THREE.Vector3(source.x, source.y, source.z),
      new THREE.Vector3(target.x, target.y, target.z),
    ];
    const lineGeometry = new THREE.BufferGeometry().setFromPoints(points);
    const opacity = 0.25 + 0.65 * Math.max(0, Math.min(1, edge.confidence));
    const lineMaterial = new THREE.LineBasicMaterial({ color, transparent: true, opacity });
    edgeGroup.add(new THREE.Line(lineGeometry, lineMaterial));

    // A small cone arrowhead near the target end -- directed edges, per
    // the directive's explicit "3D nodes and directed edges" requirement.
    const dir = new THREE.Vector3().subVectors(points[1], points[0]);
    const length = dir.length();
    if (length > 0.001) {
      dir.normalize();
      const coneLength = Math.min(10, length * 0.2);
      const cone = new THREE.Mesh(
        new THREE.ConeGeometry(2.2, coneLength, 10),
        new THREE.MeshBasicMaterial({ color, transparent: true, opacity })
      );
      const arrowPos = new THREE.Vector3().copy(points[1]).addScaledVector(dir, -8);
      cone.position.copy(arrowPos);
      cone.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir);
      edgeGroup.add(cone);
    }
  }

  return { rendered: true, totalNodes: selection.totalNodes, drawnNodes: nodes.length };
}

function _clearGroup(state) {
  for (const group of [state.nodeGroup, state.edgeGroup]) {
    if (!group) continue;
    for (const child of [...group.children]) {
      group.remove(child);
      if (child.geometry) child.geometry.dispose();
      if (child.material) child.material.dispose();
    }
    state.scene.remove(group);
  }
}

/** Resets the camera to its initial orbit position -- the "reset/
 * recenter" requirement. */
export function resetView(state) {
  state.camera.position.set(260, 220, 260);
  state.controls.target.set(0, 0, 0);
  state.controls.update();
}

export function dispose(state) {
  state.disposed = true;
  if (state.resizeObserver) state.resizeObserver.disconnect();
  _clearGroup(state);
  state.renderer.dispose();
  if (state.renderer.domElement.parentElement === state.container) {
    state.container.removeChild(state.renderer.domElement);
  }
  if (state.labelRenderer.domElement.parentElement === state.container) {
    state.container.removeChild(state.labelRenderer.domElement);
  }
}
