/**
 * Nervous-system neighborhood Webview (VS Code + Nervous-System scope
 * change; see docs/vscode-nervous-system-architecture.md §5, §8).
 *
 * Renders one `VisualizationGraph` as an interactive graph: nodes
 * placed on concentric rings by `distance` from the query center,
 * edges drawn between them, and a click on a frontier node (distance
 * == requested_depth) issues a *new* `/neighborhood` request centered
 * on that node -- progressive exploration, never a client-side
 * expansion of data the server didn't send (the VS Code boundary
 * rule: "never expanding client-side from cached data it wasn't
 * given"). Layout/rendering delegated to `webviewAssets.ts`'s shared
 * `CodexGraph` renderer (UI Integration Milestone) -- the same one
 * `askPanel.ts`'s embedded graph view uses, so this extension has
 * exactly one graph-drawing implementation, not two.
 */

import * as vscode from "vscode";
import { CodexClient, VisualizationGraph } from "./codexClient";
import { buildGraphModel } from "./graphModel";
import { escapeHtml, GRAPH_RENDERER_SCRIPT, renderMessageHtml, SHARED_STYLES } from "./webviewAssets";

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

  static async show(client: CodexClient, repositoryId: string, symbol: string): Promise<void> {
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

function renderGraphHtml(graph: VisualizationGraph): string {
  const model = buildGraphModel(graph);
  const truncatedNotice = graph.truncated
    ? ' <span style="color:#ffb84f;">(truncated — neighborhood larger than the retrieval budget)</span>'
    : "";

  return `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8" />
<style>
  ${SHARED_STYLES}
  #toolbar { padding: 8px 12px; border-bottom: 1px solid var(--vscode-panel-border, #444); }
  #hint { font-size: 11px; opacity: 0.8; }
  #graph { }
</style>
</head>
<body>
  <div id="toolbar">
    <strong>${escapeHtml(graph.center)}</strong> — ${graph.nodes.length} nodes, ${graph.edges.length} edges${truncatedNotice}
    <div id="hint">Click an orange (frontier) node to progressively reveal its neighborhood.</div>
  </div>
  <div id="graph"></div>
  <script>${GRAPH_RENDERER_SCRIPT}</script>
  <script>
    const vscode = acquireVsCodeApi();
    const model = ${JSON.stringify(model)};
    CodexGraph.render2D(document.getElementById('graph'), model, {
      height: 620,
      onNodeClick: (id) => vscode.postMessage({ type: 'expand', id }),
    });
  </script>
</body>
</html>`;
}
