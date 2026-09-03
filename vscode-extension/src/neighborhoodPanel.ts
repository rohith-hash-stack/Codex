/**
 * Nervous-system neighborhood Webview (VS Code + Nervous-System scope
 * change; see docs/vscode-nervous-system-architecture.md §5, §8).
 *
 * Renders one `VisualizationGraph` as an interactive SVG: nodes placed
 * on concentric rings by `distance` from the query center, edges drawn
 * between them, and a click on a frontier node (distance ==
 * requested_depth) issues a *new* `/neighborhood` request centered on
 * that node -- progressive exploration, never a client-side expansion
 * of data the server didn't send (the VS Code boundary rule: "never
 * expanding client-side from cached data it wasn't given"). No
 * charting/graph-layout library dependency -- plain SVG plus a small
 * hand-rolled radial layout.
 */

import * as vscode from "vscode";
import { CodexClient, VisualizationGraph, VisualizationNode } from "./codexClient";

export class NeighborhoodPanel {
  private static current: NeighborhoodPanel | undefined;

  private readonly panel: vscode.WebviewPanel;

  private constructor(
    private readonly client: CodexClient,
    private readonly repositoryId: string
  ) {
    this.panel = vscode.window.createWebviewPanel(
      "codexNeighborhood",
      "Codex: Repository Neighborhood",
      vscode.ViewColumn.Beside,
      { enableScripts: true, retainContextWhenHidden: true }
    );
    this.panel.webview.onDidReceiveMessage((message: unknown) => {
      const msg = message as { type?: string; id?: string } | undefined;
      if (msg?.type === "expand" && typeof msg.id === "string") {
        void this.loadAndRender(msg.id, 1);
      }
    });
    this.panel.onDidDispose(() => {
      if (NeighborhoodPanel.current === this) {
        NeighborhoodPanel.current = undefined;
      }
    });
  }

  static async show(
    client: CodexClient,
    repositoryId: string,
    symbol: string
  ): Promise<void> {
    if (!NeighborhoodPanel.current) {
      NeighborhoodPanel.current = new NeighborhoodPanel(client, repositoryId);
    } else {
      NeighborhoodPanel.current.panel.reveal(vscode.ViewColumn.Beside);
    }
    await NeighborhoodPanel.current.loadAndRender(symbol, 1);
  }

  private async loadAndRender(symbol: string, depth: number): Promise<void> {
    this.panel.webview.html = renderMessageHtml(`Loading neighborhood of "${symbol}"…`);
    try {
      const graph = await this.client.getNeighborhood(this.repositoryId, symbol, depth);
      this.panel.title = `Codex: ${graph.center}`;
      this.panel.webview.html = renderGraphHtml(graph);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      this.panel.webview.html = renderMessageHtml(`Codex error: ${message}`, true);
    }
  }
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderMessageHtml(message: string, isError = false): string {
  const color = isError ? "var(--vscode-errorForeground, #f66)" : "inherit";
  return `<!DOCTYPE html><html><body style="font-family:sans-serif;color:${color};padding:16px;">${escapeHtml(
    message
  )}</body></html>`;
}

interface Position {
  x: number;
  y: number;
}

function layoutByDistance(nodes: VisualizationNode[], width: number, height: number): Map<string, Position> {
  const centerX = width / 2;
  const centerY = height / 2;
  const byDistance = new Map<number, VisualizationNode[]>();
  for (const node of nodes) {
    const bucket = byDistance.get(node.distance) ?? [];
    bucket.push(node);
    byDistance.set(node.distance, bucket);
  }
  const positions = new Map<string, Position>();
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

function renderGraphHtml(graph: VisualizationGraph): string {
  const width = 900;
  const height = 640;
  const positions = layoutByDistance(graph.nodes, width, height);

  const edgeSvg = graph.edges
    .map((edge) => {
      const source = positions.get(edge.source);
      const target = positions.get(edge.target);
      if (!source || !target) {
        return "";
      }
      const tooltip = escapeHtml(`${edge.relationship_type} (confidence ${edge.confidence.toFixed(2)})`);
      return `<line x1="${source.x}" y1="${source.y}" x2="${target.x}" y2="${target.y}" stroke="#888" stroke-width="1.5" marker-end="url(#arrow)"><title>${tooltip}</title></line>`;
    })
    .join("\n");

  const nodeSvg = graph.nodes
    .map((node) => {
      const position = positions.get(node.id);
      if (!position) {
        return "";
      }
      const isFrontier = graph.requested_depth > 0 && node.distance === graph.requested_depth;
      const fill = node.distance === 0 ? "#4f9dff" : isFrontier ? "#ffb84f" : "#6bbf6b";
      const label = escapeHtml(node.name);
      const title = escapeHtml(`${node.qualified_name} (${node.node_type})`);
      return `<g class="node" data-id="${escapeHtml(node.id)}" transform="translate(${position.x},${position.y})">
  <circle r="16" fill="${fill}" stroke="#222" stroke-width="1"><title>${title}</title></circle>
  <text text-anchor="middle" dy="30" font-size="11" fill="var(--vscode-editor-foreground, #ccc)">${label}</text>
</g>`;
    })
    .join("\n");

  const truncatedNotice = graph.truncated
    ? ' <span style="color:#ffb84f;">(truncated — neighborhood larger than the retrieval budget)</span>'
    : "";

  return `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8" />
<style>
  body { margin: 0; font-family: var(--vscode-font-family, sans-serif); background: var(--vscode-editor-background, #1e1e1e); color: var(--vscode-editor-foreground, #ccc); }
  #toolbar { padding: 8px 12px; border-bottom: 1px solid var(--vscode-panel-border, #444); }
  #hint { font-size: 11px; opacity: 0.8; }
  svg { display: block; }
  .node { cursor: pointer; }
  .node:hover circle { stroke: #fff; stroke-width: 2; }
</style>
</head>
<body>
  <div id="toolbar">
    <strong>${escapeHtml(graph.center)}</strong> — ${graph.nodes.length} nodes, ${graph.edges.length} edges${truncatedNotice}
    <div id="hint">Click an orange (frontier) node to progressively reveal its neighborhood.</div>
  </div>
  <svg width="${width}" height="${height}">
    <defs>
      <marker id="arrow" markerWidth="8" markerHeight="8" refX="16" refY="3" orient="auto" markerUnits="userSpaceOnUse">
        <path d="M0,0 L0,6 L8,3 z" fill="#888" />
      </marker>
    </defs>
    ${edgeSvg}
    ${nodeSvg}
  </svg>
  <script>
    const vscode = acquireVsCodeApi();
    document.querySelectorAll('.node').forEach((el) => {
      el.addEventListener('click', () => {
        vscode.postMessage({ type: 'expand', id: el.getAttribute('data-id') });
      });
    });
  </script>
</body>
</html>`;
}
