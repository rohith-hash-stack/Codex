/**
 * Shared Webview assets (UI Integration Milestone): CSS, HTML escaping,
 * and the graph renderer script, used by both `neighborhoodPanel.ts`
 * and `askPanel.ts` so there is exactly one graph-drawing
 * implementation in this extension, not two independently-maintained
 * copies (the directive's "improve rather than creating a disconnected
 * UI architecture", applied within the extension itself).
 *
 * No charting/graph-layout/3D library dependency for this milestone --
 * plain SVG plus a small hand-rolled radial layout, exactly as
 * `neighborhoodPanel.ts` already established. `GRAPH_RENDERER_SCRIPT`
 * is intentionally structured in three separable layers so a future
 * milestone can add 3D without touching this milestone's code:
 *
 *   CodexGraph.model   -- already produced by `graphModel.ts` (data only)
 *   CodexGraph.layout2D(model)  -- {x, y} per node id (this milestone)
 *   CodexGraph.render2D(model, positions, ...)  -- draws SVG (this milestone)
 *
 * A future `CodexGraph.layout3D(model)` producing `{x, y, z}` and a
 * `render3D(...)` (e.g. Three.js/WebGL) would sit alongside these,
 * consuming the identical `model` this milestone already serializes
 * into every graph-bearing Webview -- no change to `graphModel.ts` or
 * to how the extension host builds/sends that data.
 */

export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function renderMessageHtml(message: string, isError = false): string {
  const color = isError ? "var(--vscode-errorForeground, #f66)" : "inherit";
  return `<!DOCTYPE html><html><body style="font-family:var(--vscode-font-family,sans-serif);color:${color};padding:16px;">${escapeHtml(
    message
  )}</body></html>`;
}

/** Shared look across every Codex Webview panel. */
export const SHARED_STYLES = `
  body { margin: 0; font-family: var(--vscode-font-family, sans-serif); background: var(--vscode-editor-background, #1e1e1e); color: var(--vscode-editor-foreground, #ccc); font-size: 13px; }
  button { font-family: inherit; font-size: inherit; background: var(--vscode-button-background, #0e639c); color: var(--vscode-button-foreground, #fff); border: none; padding: 4px 10px; border-radius: 2px; cursor: pointer; }
  button:hover { background: var(--vscode-button-hoverBackground, #1177bb); }
  button:disabled { opacity: 0.5; cursor: default; }
  button.secondary { background: var(--vscode-button-secondaryBackground, #3a3d41); color: var(--vscode-button-secondaryForeground, #ccc); }
  input[type="text"] { font-family: inherit; font-size: inherit; background: var(--vscode-input-background, #3c3c3c); color: var(--vscode-input-foreground, #ccc); border: 1px solid var(--vscode-input-border, transparent); padding: 4px 6px; border-radius: 2px; }
  .badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
  .badge.ok { background: #2ea04326; color: #4caf50; }
  .badge.warn { background: #d2992226; color: #e0a030; }
  .badge.error { background: #f8514926; color: #f85149; }
  .badge.neutral { background: var(--vscode-badge-background, #4d4d4d); color: var(--vscode-badge-foreground, #fff); }
  .muted { opacity: 0.7; }
  .section { border-bottom: 1px solid var(--vscode-panel-border, #444); padding: 10px 14px; }
  .section h3 { margin: 0 0 6px 0; font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; opacity: 0.75; }
  .node-chip { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; margin: 2px; border-radius: 3px; background: var(--vscode-list-hoverBackground, #2a2d2e); cursor: pointer; border: 1px solid transparent; }
  .node-chip:hover { border-color: var(--vscode-focusBorder, #007fd4); }
  .node-chip.ungrounded { border: 1px dashed #e0a030; opacity: 0.85; }
  table.claims { width: 100%; border-collapse: collapse; font-size: 12px; }
  table.claims th, table.claims td { text-align: left; padding: 3px 6px; border-bottom: 1px solid var(--vscode-panel-border, #333); }
  table.claims th { opacity: 0.7; font-weight: 600; }
`;

/**
 * The graph model + layout + render script, exposed on `window.CodexGraph`.
 * Consumers embed one `<script>${GRAPH_RENDERER_SCRIPT}</script>` tag,
 * then call `CodexGraph.render2D(containerEl, model, opts)`.
 */
export const GRAPH_RENDERER_SCRIPT = `
window.CodexGraph = (function () {
  "use strict";

  // -- Layout (2D today; a layout3D(model) would sit alongside this,
  // producing {x, y, z} from the identical model, unmodified). --------
  function layout2D(model, width, height) {
    const centerX = width / 2;
    const centerY = height / 2;
    const byDistance = new Map();
    for (const node of model.nodes) {
      const bucket = byDistance.get(node.distance) || [];
      bucket.push(node);
      byDistance.set(node.distance, bucket);
    }
    const positions = new Map();
    for (const [distance, bucket] of byDistance) {
      const radius = distance === 0 ? 0 : 90 * distance;
      bucket.forEach((node, index) => {
        const angle = (2 * Math.PI * index) / Math.max(bucket.length, 1);
        positions.set(node.id, {
          x: centerX + radius * Math.cos(angle),
          y: centerY + radius * Math.sin(angle),
        });
      });
    }
    return positions;
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // -- Render (2D SVG today; a render3D(...) would sit alongside this,
  // consuming the same model + a layout3D(model) positions map). ------
  function render2D(container, model, opts) {
    opts = opts || {};
    const width = opts.width || container.clientWidth || 900;
    const height = opts.height || 560;
    const positions = layout2D(model, width, height);
    const highlighted = new Set(opts.highlightIds || []);
    const selected = opts.selectedId || null;

    const edgeSvg = model.edges
      .map((edge) => {
        const source = positions.get(edge.source);
        const target = positions.get(edge.target);
        if (!source || !target) return "";
        const tooltip = escapeHtml(edge.relationshipType + " (confidence " + edge.confidence.toFixed(2) + ", " + edge.status + ")");
        return '<line x1="' + source.x + '" y1="' + source.y + '" x2="' + target.x + '" y2="' + target.y + '" stroke="#888" stroke-width="1.5" marker-end="url(#codex-arrow)"><title>' + tooltip + "</title></line>";
      })
      .join("\\n");

    const nodeSvg = model.nodes
      .map((node) => {
        const position = positions.get(node.id);
        if (!position) return "";
        const isFrontier = model.requestedDepth > 0 && node.distance === model.requestedDepth;
        const isHighlighted = highlighted.has(node.id);
        const isSelected = node.id === selected;
        let fill = node.distance === 0 ? "#4f9dff" : isFrontier ? "#ffb84f" : "#6bbf6b";
        const strokeWidth = isSelected ? 3 : isHighlighted ? 2.5 : 1;
        const stroke = isSelected ? "#fff" : isHighlighted ? "#ffd166" : "#222";
        const label = escapeHtml(node.name);
        const title = escapeHtml(node.qualifiedName + " (" + node.nodeType + ")");
        return (
          '<g class="codex-node" data-id="' + escapeHtml(node.id) + '" transform="translate(' + position.x + "," + position.y + ')">' +
          '<circle r="16" fill="' + fill + '" stroke="' + stroke + '" stroke-width="' + strokeWidth + '"><title>' + title + "</title></circle>" +
          '<text text-anchor="middle" dy="30" font-size="11" fill="var(--vscode-editor-foreground, #ccc)">' + label + "</text>" +
          "</g>"
        );
      })
      .join("\\n");

    container.innerHTML =
      '<svg width="' + width + '" height="' + height + '" style="display:block;">' +
      '<defs><marker id="codex-arrow" markerWidth="8" markerHeight="8" refX="16" refY="3" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L0,6 L8,3 z" fill="#888" /></marker></defs>' +
      edgeSvg + nodeSvg +
      "</svg>";

    if (opts.onNodeClick) {
      container.querySelectorAll(".codex-node").forEach((el) => {
        el.style.cursor = "pointer";
        el.addEventListener("click", () => opts.onNodeClick(el.getAttribute("data-id")));
      });
    }
  }

  function resolveClaimEndpoint(text, nodes) {
    return nodes.find((n) => n.id === text || n.qualifiedName === text || n.name === text);
  }

  return { layout2D: layout2D, render2D: render2D, resolveClaimEndpoint: resolveClaimEndpoint };
})();
`;
