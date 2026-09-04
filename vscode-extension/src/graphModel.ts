/**
 * Renderer-agnostic graph data model (UI Integration Milestone).
 *
 * The directive requires the visualization *architecture* to be
 * 3D-capable without building 3D now: "structure components/data
 * models so a future 3D renderer can consume the same API graph data"
 * and "do not invent graph semantics for visualization... nodes/edges
 * must come directly from API responses."
 *
 * This module is the seam that makes that true. `buildGraphModel` is a
 * pure, lossless projection of a `VisualizationGraph` (the server's own
 * response, from `/symbols` or `/neighborhood`) into a normalized shape
 * that carries no rendering decision at all -- no position, no color,
 * no layout. A 2D SVG renderer (`webviewAssets.ts`, this milestone) and
 * a hypothetical future 3D/WebGL renderer would both start from the
 * identical `GraphModel` this function produces; only the *layout*
 * function (2D radial today; a 3D equivalent later) and the *renderer*
 * differ. Nothing here is invented: every field is copied verbatim
 * from the server's own `VisualizationNode`/`VisualizationEdge`.
 */

import { AskResponse, Claim, EvidenceContextSummary, VisualizationGraph, VisualizationNode } from "./codexClient";

export interface GraphModelNode {
  id: string;
  name: string;
  qualifiedName: string;
  nodeType: string;
  roles: string[];
  language: string | null;
  distance: number;
  sourceLocation: VisualizationNode["source_location"];
}

export interface GraphModelEdge {
  id: string;
  source: string;
  target: string;
  relationshipType: string;
  status: string;
  confidence: number;
  evidenceCount: number;
}

export interface GraphModel {
  center: string;
  nodes: GraphModelNode[];
  edges: GraphModelEdge[];
  graphVersionId: string | null;
  requestedDepth: number;
  truncated: boolean;
}

/** Straight, lossless projection -- no field is derived, guessed, or
 * recomputed. A future 3D renderer consumes this exact type. */
export function buildGraphModel(graph: VisualizationGraph): GraphModel {
  return {
    center: graph.center,
    nodes: graph.nodes.map((node) => ({
      id: node.id,
      name: node.name,
      qualifiedName: node.qualified_name,
      nodeType: node.node_type,
      roles: node.roles,
      language: node.language,
      distance: node.distance,
      sourceLocation: node.source_location,
    })),
    edges: graph.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      relationshipType: edge.relationship_type,
      status: edge.status,
      confidence: edge.confidence,
      evidenceCount: edge.evidence_count,
    })),
    graphVersionId: graph.graph_version?.version_id ?? null,
    requestedDepth: graph.requested_depth,
    truncated: graph.truncated,
  };
}

/** Same projection, sourced from `AskResponse.evidence_context`
 * instead of a `/symbols`/`/neighborhood` response -- the *other*
 * place a `VisualizationNode`/`VisualizationEdge` list already reaches
 * the client (API Integration Milestone). Same model, same renderer;
 * this is exactly the "no disconnected UI architecture" requirement
 * applied to data, not just code. */
export function buildGraphModelFromEvidence(evidence: EvidenceContextSummary, center: string): GraphModel {
  return buildGraphModel({
    center,
    nodes: evidence.entities,
    edges: evidence.relationships,
    graph_version: evidence.graph_version,
    requested_depth: 0,
    truncated: evidence.partial,
  });
}

/**
 * Cross-references one claim endpoint's free-text `subject`/`object`
 * string against the real entities the server actually returned, by
 * exact match on `id` (canonical_id), `qualifiedName`, or `name` -- the
 * same three identifier shapes a real model response has been observed
 * to use (`scripts/analyze_expansion_run.py`'s own established
 * resolver, ported here for the UI's identical purpose: telling a
 * grounded claim endpoint apart from one with no matching evidence
 * entity). This is presentation-layer cross-referencing against data
 * the server already sent -- never a new resolution algorithm, and
 * never a verdict of "fabricated": a claim endpoint that doesn't match
 * is reported neutrally as "not found among retrieved evidence", since
 * that is what is actually known, not asserted further.
 */
export function resolveClaimEndpoint(text: string, nodes: GraphModelNode[]): GraphModelNode | undefined {
  return nodes.find((node) => node.id === text || node.qualifiedName === text || node.name === text);
}

export interface ClaimGrounding {
  claim: Claim;
  subjectNode: GraphModelNode | undefined;
  objectNode: GraphModelNode | undefined;
  /** True only when both endpoints resolve to a real entity in the
   * evidence the server returned -- an observational fact about this
   * response, not a claim about the relationship's own truth (a real
   * edge between two real entities is checked separately, by whether
   * the claim's predicate matches a real `GraphModelEdge` between
   * them -- see `edgeExistsBetween`). */
  endpointsGrounded: boolean;
}

/** Whether a real edge with this claim's predicate exists between its
 * two resolved endpoints, among the edges the server actually
 * returned -- again, cross-referencing only, never new retrieval. */
export function edgeExistsBetween(
  subjectId: string | undefined,
  objectId: string | undefined,
  predicate: string,
  edges: GraphModelEdge[]
): boolean {
  if (!subjectId || !objectId) {
    return false;
  }
  return edges.some(
    (edge) => edge.source === subjectId && edge.target === objectId && edge.relationshipType === predicate
  );
}

/** Ties every claim in an `AskResponse` back to the real evidence
 * entities/edges the server returned, for the UI's evidence panel. */
export function groundClaims(response: AskResponse): ClaimGrounding[] {
  const model = buildGraphModelFromEvidence(response.evidence_context, response.query_text);
  return response.claims.map((claim) => {
    const subjectNode = resolveClaimEndpoint(claim.subject, model.nodes);
    const objectNode = resolveClaimEndpoint(claim.object, model.nodes);
    return {
      claim,
      subjectNode,
      objectNode,
      endpointsGrounded: subjectNode !== undefined && objectNode !== undefined,
    };
  });
}
