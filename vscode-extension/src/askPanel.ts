/**
 * "Ask Codex" Webview panel (UI Integration Milestone).
 *
 * The primary Codex experience: repository -> ingestion status -> ask
 * question -> grounded answer -> inspect evidence -> explore graph, in
 * one panel. Speaks to Codex exclusively through `CodexClient`'s HTTP
 * calls -- no retrieval/ranking/identity/grounding decision is made
 * here; every fact shown (claims, entities, relationships, status,
 * model metadata) is exactly what `POST /query`/`/neighborhood`/
 * `/repositories/{id}/status`/`/healthz` returned, never recomputed or
 * reinterpreted beyond simple client-side cross-referencing (see
 * `graphModel.ts`).
 */

import * as vscode from "vscode";
import { AskResponse, CodexApiError, CodexClient, IngestionJobStatus } from "./codexClient";
import { renderShellHtml } from "./askPanelView";

interface RepoRef {
  repositoryId: string;
  localPath: string;
}

type InboundMessage =
  | { type: "ready" }
  | { type: "index" }
  | { type: "refreshStatus" }
  | { type: "ask"; queryText: string }
  | { type: "expand"; symbol: string; depth?: number }
  | { type: "search"; query: string };

export class AskPanel {
  private static current: AskPanel | undefined;

  private readonly panel: vscode.WebviewPanel;
  private disposed = false;

  private constructor(
    private readonly client: CodexClient,
    private readonly repo: RepoRef
  ) {
    this.panel = vscode.window.createWebviewPanel(
      "codexAsk",
      `Codex: Ask — ${repo.repositoryId}`,
      vscode.ViewColumn.Active,
      { enableScripts: true, retainContextWhenHidden: true }
    );
    this.panel.webview.html = renderShellHtml(repo.repositoryId);
    this.panel.webview.onDidReceiveMessage((message: unknown) => {
      void this.handleMessage(message as InboundMessage);
    });
    this.panel.onDidDispose(() => {
      this.disposed = true;
      if (AskPanel.current === this) {
        AskPanel.current = undefined;
      }
    });
  }

  static async show(client: CodexClient, repo: RepoRef): Promise<void> {
    if (AskPanel.current) {
      AskPanel.current.panel.reveal(vscode.ViewColumn.Active);
    } else {
      AskPanel.current = new AskPanel(client, repo);
    }
  }

  private post(message: Record<string, unknown>): void {
    if (!this.disposed) {
      void this.panel.webview.postMessage(message);
    }
  }

  private async handleMessage(message: InboundMessage): Promise<void> {
    try {
      switch (message.type) {
        case "ready":
          await this.sendHealthAndStatus();
          break;
        case "refreshStatus":
          await this.sendHealthAndStatus();
          break;
        case "index":
          await this.runIndex();
          break;
        case "ask":
          await this.runAsk(message.queryText);
          break;
        case "expand":
          await this.runExpand(message.symbol, message.depth ?? 1);
          break;
        case "search":
          await this.runSearch(message.query);
          break;
      }
    } catch (err) {
      this.post({ type: "fatalError", message: describeError(err) });
    }
  }

  private async sendHealthAndStatus(): Promise<void> {
    let healthy = false;
    try {
      const health = await this.client.healthz();
      healthy = health.status === "ok";
    } catch {
      healthy = false;
    }
    this.post({ type: "health", healthy });
    if (!healthy) {
      return;
    }
    try {
      const status = await this.client.getRepositoryStatus(this.repo.repositoryId);
      this.post({ type: "statusResult", status });
    } catch (err) {
      this.post({ type: "statusError", ...errorPayload(err) });
    }
  }

  private async runIndex(): Promise<void> {
    try {
      const handle = await this.client.registerAndIndex(this.repo.repositoryId, this.repo.localPath);
      await this.client.waitForJob(handle.job_id, (status: IngestionJobStatus) => {
        this.post({ type: "indexProgress", phase: status.phase, detail: status.detail });
      });
      const status = await this.client.getRepositoryStatus(this.repo.repositoryId);
      this.post({ type: "statusResult", status });
    } catch (err) {
      this.post({ type: "indexError", ...errorPayload(err) });
    }
  }

  private async runAsk(queryText: string): Promise<void> {
    if (!queryText.trim()) {
      return;
    }
    this.post({ type: "askStarted" });
    try {
      const response: AskResponse = await this.client.ask(this.repo.repositoryId, queryText);
      this.post({ type: "askResult", response });
    } catch (err) {
      this.post({ type: "askError", ...errorPayload(err) });
    }
  }

  /**
   * A node's full `qualified_name` (or a user-typed search string) is
   * passed straight through to `GET /neighborhood`. Earlier in the UI
   * Integration Milestone this needed a client-side retry against a
   * node's bare `name`, because `codex.planner.retrieval.
   * _resolve_one_target` could fail to resolve an entity's own full
   * `qualified_name` -- fixed at the source (Canonical Identity
   * Resolution fix, `docs/canonical-identity-resolution-fix.md`), so
   * this method no longer needs a fallback.
   */
  private async runExpand(symbol: string, depth: number): Promise<void> {
    this.post({ type: "graphLoading", symbol });
    try {
      const graph = await this.client.getNeighborhood(this.repo.repositoryId, symbol, depth);
      this.post({ type: "graphResult", graph });
    } catch (err) {
      this.post({ type: "graphError", ...errorPayload(err) });
    }
  }

  private async runSearch(query: string): Promise<void> {
    try {
      const graph = await this.client.lookupSymbols(this.repo.repositoryId, query);
      this.post({ type: "searchResult", graph });
    } catch (err) {
      this.post({ type: "searchError", ...errorPayload(err) });
    }
  }
}

function describeError(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function errorPayload(err: unknown): { message: string; status: number } {
  if (err instanceof CodexApiError) {
    return { message: err.message, status: err.status };
  }
  return { message: describeError(err), status: 0 };
}
