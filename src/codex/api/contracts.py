"""Codex API wire contracts (VS Code + Nervous-System scope change).

See `docs/vscode-nervous-system-architecture.md` §3/§5 for the design
rationale. These are the *only* shapes that cross the Codex API boundary
-- no raw `GraphReader`/storage object, no `ProviderAdapter`-internal
type, is ever returned by `codex.api.service`. Every enum reused here
(`BaseEntityType`, `RelationshipType`, `EvidenceStatus`) is the existing
canonical ontology/evidence enum, never a parallel one invented for the
API layer (docs §3: "reuses ... directly, no parallel enum invented").
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from codex.evidence.model import EvidenceStatus
from codex.ontology.entities import BaseEntityType, SourceLocation
from codex.ontology.relationships import RelationshipType


class RepositoryPhase(StrEnum):
    """Coarse repository lifecycle phase (docs §6). No finer-grained
    percentage progress is reported in this cycle's MVP -- see docs §10
    ("Real ingestion progress percentage" is explicitly deferred)."""

    NOT_REGISTERED = "NOT_REGISTERED"
    REGISTERED = "REGISTERED"
    INDEXING = "INDEXING"
    READY = "READY"
    FAILED = "FAILED"


class ProviderSummary(BaseModel):
    """One provider's contribution to the most recent ingestion run
    (`ProviderRunOutcome`, narrowed to what a status view needs -- never
    the full outcome object, per the API-vs-internal boundary already
    established in the prior API Architecture Map)."""

    provider_name: str
    status: str
    entities_upserted: int = 0
    evidence_upserted: int = 0
    detail: str | None = None


class RepositoryStatus(BaseModel):
    """Repository Lifecycle API response (docs §3, §6)."""

    repository_id: str
    phase: RepositoryPhase
    head_revision: str | None = None
    graph_version_id: str | None = None
    provider_summary: list[ProviderSummary] = Field(default_factory=list)
    error_detail: str | None = None


class IngestionJobHandle(BaseModel):
    """Returned immediately by `start_ingestion` -- the non-blocking
    handle a VS Code client polls (docs §6)."""

    job_id: str
    repository_id: str


class IngestionJobStatus(BaseModel):
    """Polled via `get_job_status`. `result` is populated only once
    `phase` reaches a terminal state (`READY`/`FAILED`)."""

    job_id: str
    repository_id: str
    phase: RepositoryPhase
    detail: str | None = None
    result: RepositoryStatus | None = None


class VisualizationNode(BaseModel):
    """One graph entity, shaped for rendering (docs §3/§5). `node_type`
    is the real `BaseEntityType`, `source_location` the real
    `SourceLocation` already carried by every `RepositorySymbol` --
    neither is re-derived or approximated here."""

    id: str
    """`RepositorySymbol.canonical_id` -- the same provider-independent
    identity this entire engagement's fidelity work has validated."""

    name: str
    qualified_name: str
    node_type: BaseEntityType
    roles: list[str] = Field(default_factory=list)
    language: str | None = None
    source_location: SourceLocation | None = None
    distance: int = 0
    """Hops from the query center (0 for a lookup match or a traversal
    seed). A client treats a node with `distance == requested_depth` on
    the containing `VisualizationGraph` as its current expansion
    frontier (docs §5) -- no separate per-node boolean is carried."""


class VisualizationEdge(BaseModel):
    """One relationship, shaped for rendering. `relationship_type`/
    `status`/`confidence` are `CanonicalRelationship`'s own already-
    reconciled fields, never recomputed here."""

    id: str
    """Deterministic composite of (subject, predicate, object) -- stable
    across repeated calls against the same `graph_version`."""

    source: str
    target: str
    relationship_type: RelationshipType
    status: EvidenceStatus
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_count: int = 0


class GraphVersionRef(BaseModel):
    """Narrow projection of `GraphVersion` -- just enough for a client to
    know which snapshot a result came from (prior API Architecture Map,
    Risk #4: "no graph_version field on any existing type reaches an
    external caller today")."""

    version_id: str
    repository_id: str
    repository_revision: str


class VisualizationGraph(BaseModel):
    """The single shared response shape for both symbol/file lookup
    (nodes only, `edges=[]`, `requested_depth=0`) and neighborhood
    exploration (docs §3: "One contract, not two")."""

    center: str
    """The query text or canonical_id this graph was built around."""

    nodes: list[VisualizationNode] = Field(default_factory=list)
    edges: list[VisualizationEdge] = Field(default_factory=list)
    graph_version: GraphVersionRef | None = None
    requested_depth: int = 0
    truncated: bool = False
    """`True` iff `max_nodes`/`max_edges` stopped expansion before every
    reachable node/edge within `requested_depth` was visited -- the same
    honesty discipline `EvidencePackage.partial` already applies to NL
    queries, never silently hidden."""


__all__ = [
    "GraphVersionRef",
    "IngestionJobHandle",
    "IngestionJobStatus",
    "ProviderSummary",
    "RepositoryPhase",
    "RepositoryStatus",
    "VisualizationEdge",
    "VisualizationGraph",
    "VisualizationNode",
]
