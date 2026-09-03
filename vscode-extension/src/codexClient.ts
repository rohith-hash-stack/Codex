/**
 * Thin HTTP client for the Codex API (VS Code + Nervous-System scope
 * change; see docs/vscode-nervous-system-architecture.md §4).
 *
 * Uses only Node's built-in `http` module -- no axios/node-fetch
 * dependency, per the "no unnecessary frameworks/dependencies"
 * constraint. Speaks exclusively the JSON shapes `codex.api.contracts`
 * defines; holds no retrieval/graph logic of its own (the extension
 * boundary rule: "every decision about what is related to what is made
 * server-side").
 */

import * as http from "http";

export interface SourceLocation {
  file_path: string;
  start_line: number;
  end_line: number;
  start_column: number | null;
  end_column: number | null;
}

export interface VisualizationNode {
  id: string;
  name: string;
  qualified_name: string;
  node_type: string;
  roles: string[];
  language: string | null;
  source_location: SourceLocation | null;
  distance: number;
}

export interface VisualizationEdge {
  id: string;
  source: string;
  target: string;
  relationship_type: string;
  status: string;
  confidence: number;
  evidence_count: number;
}

export interface GraphVersionRef {
  version_id: string;
  repository_id: string;
  repository_revision: string;
}

export interface VisualizationGraph {
  center: string;
  nodes: VisualizationNode[];
  edges: VisualizationEdge[];
  graph_version: GraphVersionRef | null;
  requested_depth: number;
  truncated: boolean;
}

export interface IngestionJobHandle {
  job_id: string;
  repository_id: string;
}

export interface ProviderSummary {
  provider_name: string;
  status: string;
  entities_upserted: number;
  evidence_upserted: number;
  detail: string | null;
}

export interface RepositoryStatus {
  repository_id: string;
  phase: string;
  head_revision: string | null;
  graph_version_id: string | null;
  provider_summary: ProviderSummary[];
  error_detail: string | null;
}

export interface IngestionJobStatus {
  job_id: string;
  repository_id: string;
  phase: string;
  detail: string | null;
  result: RepositoryStatus | null;
}

interface ApiError {
  error?: string;
}

export class CodexApiError extends Error {}

export class CodexClient {
  constructor(private readonly baseUrl: string) {}

  private request<T>(method: string, path: string, body?: unknown): Promise<T> {
    return new Promise((resolve, reject) => {
      const url = new URL(path, this.baseUrl);
      const payload =
        body !== undefined ? Buffer.from(JSON.stringify(body), "utf-8") : undefined;
      const headers: Record<string, string | number> = {};
      if (payload) {
        headers["Content-Type"] = "application/json";
        headers["Content-Length"] = payload.length;
      }
      const req = http.request(url, { method, headers }, (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk: Buffer) => chunks.push(chunk));
        res.on("end", () => {
          const text = Buffer.concat(chunks).toString("utf-8");
          const status = res.statusCode ?? 0;
          let parsed: unknown;
          try {
            parsed = text.length > 0 ? JSON.parse(text) : {};
          } catch {
            parsed = { error: text };
          }
          if (status >= 200 && status < 300) {
            resolve(parsed as T);
          } else {
            const message = (parsed as ApiError).error ?? `HTTP ${status}`;
            reject(new CodexApiError(message));
          }
        });
      });
      req.on("error", (err) => reject(new CodexApiError(err.message)));
      if (payload) {
        req.write(payload);
      }
      req.end();
    });
  }

  registerAndIndex(repositoryId: string, localPath: string): Promise<IngestionJobHandle> {
    return this.request<IngestionJobHandle>("POST", "/repositories", {
      repository_id: repositoryId,
      local_path: localPath,
    });
  }

  getJobStatus(jobId: string): Promise<IngestionJobStatus> {
    return this.request<IngestionJobStatus>("GET", `/jobs/${encodeURIComponent(jobId)}`);
  }

  /** Polls until the job reaches READY/FAILED. `onUpdate` fires on every poll, letting a caller report progress without blocking. */
  async waitForJob(
    jobId: string,
    onUpdate?: (status: IngestionJobStatus) => void
  ): Promise<IngestionJobStatus> {
    for (;;) {
      const status = await this.getJobStatus(jobId);
      onUpdate?.(status);
      if (status.phase === "READY" || status.phase === "FAILED") {
        return status;
      }
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
  }

  getRepositoryStatus(repositoryId: string): Promise<RepositoryStatus> {
    return this.request<RepositoryStatus>(
      "GET",
      `/repositories/${encodeURIComponent(repositoryId)}/status`
    );
  }

  lookupSymbols(repositoryId: string, query: string, limit = 25): Promise<VisualizationGraph> {
    const qs = new URLSearchParams({
      repository_id: repositoryId,
      query,
      limit: String(limit),
    }).toString();
    return this.request<VisualizationGraph>("GET", `/symbols?${qs}`);
  }

  getNeighborhood(
    repositoryId: string,
    symbol: string,
    depth = 1
  ): Promise<VisualizationGraph> {
    const qs = new URLSearchParams({
      repository_id: repositoryId,
      symbol,
      depth: String(depth),
    }).toString();
    return this.request<VisualizationGraph>("GET", `/neighborhood?${qs}`);
  }
}
