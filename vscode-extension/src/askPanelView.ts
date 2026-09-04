/**
 * "Ask Codex" Webview content -- shell HTML + client script (UI
 * Integration Milestone). Split out from `askPanel.ts` deliberately:
 * this module has no dependency on the `vscode` module (only resolvable
 * inside a running VS Code Extension Host), so it can be unit-tested
 * with a plain Node test runner (`askPanel.test.ts`) the same way
 * `graphModel.ts`/`webviewAssets.ts` already are. `askPanel.ts` (the
 * `AskPanel` class, which genuinely needs `vscode.window.
 * createWebviewPanel` etc.) imports `renderShellHtml` from here.
 */

import { escapeHtml, GRAPH_RENDERER_SCRIPT, SHARED_STYLES } from "./webviewAssets";

export function renderShellHtml(repositoryId: string): string {
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8" />
<style>
  ${SHARED_STYLES}
  #header { display: flex; align-items: center; gap: 10px; padding: 8px 14px; border-bottom: 1px solid var(--vscode-panel-border, #444); }
  #header .repo { font-weight: 600; }
  #queryBar { display: flex; gap: 8px; padding: 10px 14px; border-bottom: 1px solid var(--vscode-panel-border, #444); }
  #queryInput { flex: 1; }
  #layout { display: flex; height: calc(100vh - 96px); }
  #left { flex: 1; min-width: 320px; overflow-y: auto; border-right: 1px solid var(--vscode-panel-border, #444); }
  #right { flex: 1; min-width: 320px; overflow-y: auto; display: flex; flex-direction: column; }
  #graphToolbar { display: flex; gap: 6px; padding: 8px 14px; border-bottom: 1px solid var(--vscode-panel-border, #444); align-items: center; }
  #graphContainer { flex: 1; overflow: auto; }
  #answerText { white-space: pre-wrap; line-height: 1.5; }
  .hint { font-size: 11px; opacity: 0.7; }
  .empty { padding: 24px 14px; opacity: 0.6; text-align: center; }
</style>
</head>
<body>
  <div id="header">
    <span class="repo">${escapeHtml(repositoryId)}</span>
    <span id="healthBadge" class="badge neutral">checking…</span>
    <span id="statusBadge" class="badge neutral">unknown</span>
    <button id="indexBtn" class="secondary">Index / Re-index</button>
    <button id="refreshBtn" class="secondary">Refresh</button>
    <span id="indexProgress" class="hint"></span>
  </div>
  <div id="queryBar">
    <input id="queryInput" type="text" placeholder="Ask a question about this repository… e.g. &quot;What calls X?&quot;" />
    <button id="askBtn">Ask</button>
  </div>
  <div id="layout">
    <div id="left">
      <div id="answerSection" class="empty">Ask a question to see a grounded answer here.</div>
    </div>
    <div id="right">
      <div id="graphToolbar">
        <input id="graphSearch" type="text" placeholder="Search a symbol or file…" style="flex:1;" />
        <button id="graphSearchBtn" class="secondary">Search</button>
        <span id="graphHint" class="hint"></span>
      </div>
      <div id="graphSearchResults"></div>
      <div id="graphContainer"><div class="empty">Evidence and explored neighborhoods appear here.</div></div>
    </div>
  </div>
  <script>${GRAPH_RENDERER_SCRIPT}</script>
  <script>${CLIENT_SCRIPT}</script>
</body>
</html>`;
}

/**
 * Webview client script. Plain JS (not TS) -- the Webview runs in its
 * own browser context with no module loader shared with the extension
 * host, exactly the constraint `neighborhoodPanel.ts` already
 * documents; `graphModel.ts`'s TS types describe this same shape for
 * the host side, and this script is the browser-side mirror of the
 * small, honest cross-referencing `graphModel.ts` performs (never a
 * new resolution algorithm -- see `CodexGraph.resolveClaimEndpoint`,
 * shared via `webviewAssets.ts`).
 */
export const CLIENT_SCRIPT = `
(function () {
  "use strict";
  const vscode = acquireVsCodeApi();
  let lastEvidenceModel = null; // {center, nodes, edges, requestedDepth, truncated}
  let explorerModel = null;
  let graphMode = "evidence"; // "evidence" | "explorer"
  let selectedNodeId = null;

  const el = (id) => document.getElementById(id);

  el("indexBtn").addEventListener("click", () => {
    el("indexBtn").disabled = true;
    el("indexProgress").textContent = "starting…";
    vscode.postMessage({ type: "index" });
  });
  el("refreshBtn").addEventListener("click", () => vscode.postMessage({ type: "refreshStatus" }));
  el("askBtn").addEventListener("click", submitAsk);
  el("queryInput").addEventListener("keydown", (e) => { if (e.key === "Enter") submitAsk(); });
  el("graphSearchBtn").addEventListener("click", submitGraphSearch);
  el("graphSearch").addEventListener("keydown", (e) => { if (e.key === "Enter") submitGraphSearch(); });

  function submitAsk() {
    const queryText = el("queryInput").value;
    if (!queryText.trim()) return;
    vscode.postMessage({ type: "ask", queryText });
  }

  function submitGraphSearch() {
    const query = el("graphSearch").value;
    if (!query.trim()) return;
    el("graphHint").textContent = "searching…";
    el("graphSearchResults").innerHTML = "";
    vscode.postMessage({ type: "search", query });
  }

  function exploreSymbol(qualifiedName) {
    el("graphHint").textContent = 'loading "' + qualifiedName + '"…';
    el("graphSearchResults").innerHTML = "";
    vscode.postMessage({ type: "expand", symbol: qualifiedName, depth: 1 });
  }

  function escapeHtml(value) {
    return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function statusBadgeClass(phase) {
    if (phase === "READY") return "ok";
    if (phase === "FAILED") return "error";
    if (phase === "INDEXING") return "warn";
    return "neutral";
  }

  function askStatusBadgeClass(status) {
    if (status === "OK") return "ok";
    if (status === "UNDERSTANDING_INCOMPLETE") return "warn";
    return "error";
  }

  function askStatusExplanation(status) {
    switch (status) {
      case "OK": return "A grounded answer was generated.";
      case "UNDERSTANDING_INCOMPLETE": return "Codex could not confidently determine what you're asking. Try rephrasing more specifically (name a concrete symbol, file, or relationship).";
      case "MALFORMED_OUTPUT": return "The model's response could not be parsed as valid structured output.";
      case "LLM_TIMEOUT": return "The model did not respond within the time budget.";
      case "LLM_BUDGET_EXCEEDED": return "The request would have exceeded the configured token budget.";
      default: return "";
    }
  }

  function apiErrorExplanation(status, message) {
    switch (status) {
      case 404: return "Repository not found on the Codex API server — index it first.";
      case 409: return "Repository is still indexing (or hasn't started) — please wait and try again.";
      case 503: return "No LLM provider is configured on the Codex API server.";
      case 502: return "The LLM provider request failed: " + message;
      case 400: return "Invalid request: " + message;
      case 0: return "Could not reach the Codex API server: " + message;
      default: return message;
    }
  }

  // -- Repository status / health -----------------------------------
  window.addEventListener("message", (event) => {
    const msg = event.data;
    switch (msg.type) {
      case "health":
        el("healthBadge").textContent = msg.healthy ? "server up" : "server unreachable";
        el("healthBadge").className = "badge " + (msg.healthy ? "ok" : "error");
        break;
      case "statusResult": {
        const s = msg.status;
        el("statusBadge").textContent = s.phase;
        el("statusBadge").className = "badge " + statusBadgeClass(s.phase);
        el("askBtn").disabled = s.phase !== "READY";
        el("indexBtn").disabled = false;
        el("indexProgress").textContent = "";
        break;
      }
      case "statusError":
        el("statusBadge").textContent = "unknown";
        el("statusBadge").className = "badge neutral";
        break;
      case "indexProgress":
        el("indexProgress").textContent = msg.phase + (msg.detail ? " — " + msg.detail : "");
        break;
      case "indexError":
        el("indexBtn").disabled = false;
        el("indexProgress").textContent = "index failed: " + apiErrorExplanation(msg.status, msg.message);
        break;
      case "askStarted":
        el("askBtn").disabled = true;
        el("answerSection").innerHTML = '<div class="section"><span class="badge neutral">asking…</span></div>';
        break;
      case "askResult":
        el("askBtn").disabled = false;
        renderAskResult(msg.response);
        break;
      case "askError":
        el("askBtn").disabled = false;
        renderAskError(msg.status, msg.message);
        break;
      case "graphLoading":
        el("graphHint").textContent = 'loading "' + msg.symbol + '"…';
        break;
      case "graphResult":
        graphMode = "explorer";
        explorerModel = toModel(msg.graph);
        el("graphHint").textContent = explorerModel.truncated ? "truncated by budget" : "";
        renderGraph();
        break;
      case "graphError":
        el("graphHint").textContent = apiErrorExplanation(msg.status, msg.message);
        break;
      case "searchResult":
        renderSearchResults(msg.graph);
        break;
      case "searchError":
        el("graphHint").textContent = apiErrorExplanation(msg.status, msg.message);
        break;
      case "fatalError":
        el("answerSection").innerHTML = '<div class="section"><span class="badge error">error</span> ' + escapeHtml(msg.message) + "</div>";
        break;
    }
  });

  function renderSearchResults(graph) {
    if (graph.nodes.length === 0) {
      el("graphHint").textContent = 'no matches for "' + graph.center + '"';
      el("graphSearchResults").innerHTML = "";
      return;
    }
    if (graph.nodes.length === 1) {
      exploreSymbol(graph.nodes[0].qualified_name);
      return;
    }
    el("graphHint").textContent = graph.nodes.length + " matches — pick one:";
    const chips = graph.nodes
      .slice(0, 30)
      .map(
        (n) =>
          '<span class="node-chip" data-qn="' + escapeHtml(n.qualified_name) + '" title="' + escapeHtml(n.qualified_name) + '">' +
          escapeHtml(n.name) + ' <span class="muted">' + escapeHtml(n.node_type) + "</span></span>"
      )
      .join("");
    el("graphSearchResults").innerHTML = chips;
    el("graphSearchResults").querySelectorAll("[data-qn]").forEach((chip) => {
      chip.addEventListener("click", () => exploreSymbol(chip.getAttribute("data-qn")));
    });
  }

  function toModel(graph) {
    return {
      center: graph.center,
      nodes: graph.nodes.map((n) => ({ id: n.id, name: n.name, qualifiedName: n.qualified_name, nodeType: n.node_type, roles: n.roles, language: n.language, distance: n.distance, sourceLocation: n.source_location })),
      edges: graph.edges.map((e) => ({ id: e.id, source: e.source, target: e.target, relationshipType: e.relationship_type, status: e.status, confidence: e.confidence, evidenceCount: e.evidence_count })),
      graphVersionId: graph.graph_version ? graph.graph_version.version_id : null,
      requestedDepth: graph.requested_depth,
      truncated: graph.truncated,
    };
  }

  function modelFromEvidence(evidenceContext, center) {
    return toModel({
      center: center,
      nodes: evidenceContext.entities,
      edges: evidenceContext.relationships,
      graph_version: evidenceContext.graph_version,
      requested_depth: 0,
      truncated: evidenceContext.partial,
    });
  }

  // -- Answer / Evidence / Status rendering ---------------------------
  function renderAskError(status, message) {
    el("answerSection").innerHTML =
      '<div class="section"><span class="badge error">request failed</span><div style="margin-top:6px;">' +
      escapeHtml(apiErrorExplanation(status, message)) +
      "</div></div>";
  }

  function renderAskResult(response) {
    lastEvidenceModel = modelFromEvidence(response.evidence_context, response.query_text);
    graphMode = "evidence";
    selectedNodeId = null;

    const claimGroundings = response.claims.map((claim) => {
      const subjectNode = CodexGraph.resolveClaimEndpoint(claim.subject, lastEvidenceModel.nodes);
      const objectNode = CodexGraph.resolveClaimEndpoint(claim.object, lastEvidenceModel.nodes);
      return { claim, subjectNode, objectNode };
    });

    const html = [];

    // -- 1. Status / model metadata (clearly separated, per directive) --
    html.push('<div class="section">');
    html.push('<h3>Status</h3>');
    html.push('<span class="badge ' + askStatusBadgeClass(response.status) + '">' + response.status + "</span>");
    if (response.intent) html.push(' <span class="badge neutral">' + escapeHtml(response.intent) + "</span>");
    if (response.plan_status) html.push(' <span class="badge neutral">plan: ' + escapeHtml(response.plan_status) + "</span>");
    const explanation = askStatusExplanation(response.status);
    if (explanation) html.push('<div class="hint" style="margin-top:4px;">' + escapeHtml(explanation) + "</div>");
    if (response.detail) html.push('<div class="hint">' + escapeHtml(response.detail) + "</div>");
    html.push(
      '<div class="hint" style="margin-top:6px;">provider: ' + escapeHtml(response.model.provider) +
      " · requested: " + escapeHtml(response.model.requested_model) +
      (response.model.served_model ? " · served: " + escapeHtml(response.model.served_model) : "") +
      (response.model.usage_total_tokens != null ? " · tokens: " + response.model.usage_total_tokens : "") +
      (response.model.finish_reason ? " · finish: " + escapeHtml(response.model.finish_reason) : "") +
      "</div>"
    );
    html.push('<div class="hint">query_id: ' + escapeHtml(response.query_id || "—") + " · run_id: " + escapeHtml(response.run_id || "—") + "</div>");
    html.push("</div>");

    // -- 2. Answer / narrative --------------------------------------
    html.push('<div class="section">');
    html.push("<h3>Answer</h3>");
    if (response.answer) {
      html.push('<div id="answerText">' + escapeHtml(response.answer) + "</div>");
    } else {
      html.push('<div class="muted">No answer was generated for this request.</div>');
    }
    html.push("</div>");

    // -- Ambiguity / negative-query notices (verbatim server signals) --
    const limitations = response.evidence_context.limitations || [];
    if (limitations.length > 0) {
      html.push('<div class="section">');
      html.push("<h3>Notices</h3>");
      limitations.forEach((lim) => {
        const icon = lim.indexOf("ambiguous target") === 0 || lim.indexOf("ambiguous target") >= 0 ? "⚠" : lim.indexOf("negative_query_result=") >= 0 ? "ℹ" : "•";
        html.push('<div class="hint">' + icon + " " + escapeHtml(lim) + "</div>");
      });
      if (response.evidence_context.relationships.length === 0) {
        html.push('<div class="hint">Codex found no supporting relationships for this query — the answer above reflects that.</div>');
      }
      html.push("</div>");
    }

    // -- 3. Deterministic graph evidence (claims, entities, edges) ---
    html.push('<div class="section">');
    html.push("<h3>Evidence — Claims</h3>");
    if (claimGroundings.length === 0) {
      html.push('<div class="muted">No claims were returned.</div>');
    } else {
      html.push('<table class="claims"><thead><tr><th>Subject</th><th>Predicate</th><th>Object</th><th>Type</th></tr></thead><tbody>');
      claimGroundings.forEach((g, i) => {
        const subjClass = g.subjectNode ? "node-chip" : "node-chip ungrounded";
        const objClass = g.objectNode ? "node-chip" : "node-chip ungrounded";
        const subjTitle = g.subjectNode ? "Click to view in graph" : "Not found among retrieved evidence";
        const objTitle = g.objectNode ? "Click to view in graph" : "Not found among retrieved evidence";
        html.push(
          "<tr>" +
          '<td><span class="' + subjClass + '" title="' + escapeHtml(subjTitle) + '" data-claim="' + i + '" data-endpoint="subject">' + escapeHtml(g.claim.subject) + "</span></td>" +
          "<td>" + escapeHtml(g.claim.predicate) + "</td>" +
          '<td><span class="' + objClass + '" title="' + escapeHtml(objTitle) + '" data-claim="' + i + '" data-endpoint="object">' + escapeHtml(g.claim.object) + "</span></td>" +
          "<td>" + escapeHtml(g.claim.claim_type) + "</td>" +
          "</tr>"
        );
      });
      html.push("</tbody></table>");
    }
    html.push('<h3 style="margin-top:10px;">Evidence — Entities (' + response.evidence_context.entities.length + ")</h3>");
    response.evidence_context.entities.slice(0, 60).forEach((n) => {
      html.push('<span class="node-chip" data-node="' + escapeHtml(n.id) + '" title="' + escapeHtml(n.qualified_name) + '">' + escapeHtml(n.name) + " <span class=\\"muted\\">" + escapeHtml(n.node_type) + "</span></span>");
    });
    html.push('<h3 style="margin-top:10px;">Evidence — Relationships (' + response.evidence_context.relationships.length + ")</h3>");
    if (response.evidence_context.relationships.length > 0) {
      html.push('<table class="claims"><thead><tr><th>Relationship</th><th>Status</th><th>Confidence</th></tr></thead><tbody>');
      response.evidence_context.relationships.slice(0, 40).forEach((e) => {
        html.push("<tr><td>" + escapeHtml(e.relationship_type) + "</td><td>" + escapeHtml(e.status) + "</td><td>" + e.confidence.toFixed(2) + "</td></tr>");
      });
      html.push("</tbody></table>");
    }
    if (response.evidence_context.partial) {
      html.push('<div class="hint" style="margin-top:6px;">⚠ Evidence is partial (retrieval budget reached before every relevant edge was collected).</div>');
    }
    html.push("</div>");

    el("answerSection").innerHTML = html.join("");

    // Wire claim-endpoint / entity-chip clicks -> highlight in graph.
    el("answerSection").querySelectorAll("[data-claim]").forEach((chip) => {
      chip.addEventListener("click", () => {
        const i = parseInt(chip.getAttribute("data-claim"), 10);
        const endpoint = chip.getAttribute("data-endpoint");
        const node = endpoint === "subject" ? claimGroundings[i].subjectNode : claimGroundings[i].objectNode;
        if (node) focusEvidenceNode(node.id);
      });
    });
    el("answerSection").querySelectorAll("[data-node]").forEach((chip) => {
      chip.addEventListener("click", () => focusEvidenceNode(chip.getAttribute("data-node")));
    });

    graphMode = "evidence";
    renderGraph();
  }

  function focusEvidenceNode(nodeId) {
    graphMode = "evidence";
    selectedNodeId = nodeId;
    renderGraph();
    const container = el("graphContainer");
    if (container) container.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  // -- Graph panel (evidence snapshot or /neighborhood explorer) -----
  function renderGraph() {
    const model = graphMode === "evidence" ? lastEvidenceModel : explorerModel;
    const container = el("graphContainer");
    if (!model || model.nodes.length === 0) {
      container.innerHTML = '<div class="empty">' + (graphMode === "evidence" ? "No evidence entities to show yet." : "No neighborhood loaded yet.") + "</div>";
      return;
    }
    const highlightIds = graphMode === "evidence" ? model.nodes.filter((n) => n.distance === 0).map((n) => n.id) : [];
    CodexGraph.render2D(container, model, {
      height: container.clientHeight || 480,
      selectedId: selectedNodeId,
      highlightIds: highlightIds,
      onNodeClick: (id) => {
        // Both modes behave the same way: select the node, then
        // explore further from it via a real /neighborhood call
        // (never a client-side expansion of data the server didn't
        // send).
        selectedNodeId = id;
        const node = model.nodes.find((n) => n.id === id);
        if (node) {
          el("graphHint").textContent = 'exploring "' + node.qualifiedName + '"…';
          vscode.postMessage({ type: "expand", symbol: node.qualifiedName, depth: 1 });
        }
      },
    });
  }

  vscode.postMessage({ type: "ready" });
})();
`;
