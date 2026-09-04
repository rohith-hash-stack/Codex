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

/**
 * `POST /query` wire contracts (API Integration Milestone,
 * `docs/api-query-integration.md`). Field-for-field projections of
 * `codex.api.contracts` -- no field is renamed, dropped, or given
 * different semantics here; this file only adds TypeScript types over
 * the same JSON shape the server already returns.
 */

/** Mirrors `codex.llm.schema.Claim` exactly -- `predicate` is either a
 * real `RelationshipType` or one of the three query-time DERIVED
 * predicates (`REACHES`/`TRANSITIVE_CALLS`/`INDIRECTLY_DEPENDS_ON`);
 * left as `string` here rather than a duplicated enum, matching this
 * client's existing convention for `VisualizationEdge.relationship_type`. */
export interface Claim {
  subject: string;
  predicate: string;
  object: string;
  claim_type: "FACT" | "DERIVED" | "INFERENCE" | "UNKNOWN";
}

export interface EvidenceContextSummary {
  graph_version: GraphVersionRef | null;
  entities: VisualizationNode[];
  relationships: VisualizationEdge[];
  evidence_count: number;
  coverage: Record<string, string>;
  limitations: string[];
  partial: boolean;
}

export interface ModelMetadata {
  provider: string;
  requested_model: string;
  served_model: string | null;
  usage_prompt_tokens: number | null;
  usage_completion_tokens: number | null;
  usage_total_tokens: number | null;
  finish_reason: string | null;
}

/** Mirrors `codex.api.contracts.AskStatus` exactly -- see that enum's
 * own docstring for what each value means and why (legitimate LLM
 * outcomes only; repository/config/upstream failures are raised as
 * distinct HTTP statuses instead, never folded into this one). */
export type AskStatus =
  | "OK"
  | "UNDERSTANDING_INCOMPLETE"
  | "MALFORMED_OUTPUT"
  | "LLM_TIMEOUT"
  | "LLM_BUDGET_EXCEEDED"
  | "CLAIMS_NOT_GROUNDED";

export interface AskResponse {
  repository_id: string;
  query_text: string;
  query_id: string;
  run_id: string;
  status: AskStatus;
  intent: string | null;
  plan_status: string | null;
  answer: string | null;
  claims: Claim[];
  evidence_context: EvidenceContextSummary;
  model: ModelMetadata;
  detail: string | null;
}

export interface AskOptions {
  tokenBudget?: number;
  latencyBudgetMs?: number;
}

interface ApiError {
  error?: string;
}

/** Carries the real HTTP status alongside the server's own `{"error":
 * "..."}` message, so a caller can distinguish e.g. a `409` (repository
 * not ready -- worth a "still indexing, try again" message) from a
 * `502` (upstream LLM failure -- worth a different one) without
 * re-parsing the message text. `status` is `0` for a transport-level
 * failure that never reached the server at all (connection refused,
 * DNS, etc.). */
export class CodexApiError extends Error {
  constructor(
    message: string,
    readonly status: number = 0
  ) {
    super(message);
  }
}

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
            reject(new CodexApiError(message, status));
          }
        });
      });
      req.on("error", (err) => reject(new CodexApiError(err.message, 0)));
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

  /** `POST /query`: repository -> query -> intent/evidence requirements
   * -> targeted graph retrieval -> minimal sufficient grounded context
   * -> LLM -> grounded answer. Every field on the resolved
   * `AskResponse` comes straight from the server; this method performs
   * no interpretation of its own. */
  ask(repositoryId: string, queryText: string, options: AskOptions = {}): Promise<AskResponse> {
    const body: Record<string, unknown> = {
      repository_id: repositoryId,
      query_text: queryText,
    };
    if (options.tokenBudget !== undefined) {
      body.token_budget = options.tokenBudget;
    }
    if (options.latencyBudgetMs !== undefined) {
      body.latency_budget_ms = options.latencyBudgetMs;
    }
    return this.request<AskResponse>("POST", "/query", body);
  }

  /** `GET /healthz`: process-level liveness only, independent of any
   * repository or Gateway state (`codex.api.server`'s own documented
   * distinction from `/repositories/{id}/status`). */
  healthz(): Promise<{ status: string }> {
    return this.request<{ status: string }>("GET", "/healthz");
  }
}
