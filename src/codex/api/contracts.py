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
from codex.llm.schema import Claim
from codex.ontology.entities import BaseEntityType, SourceLocation
from codex.ontology.relationships import RelationshipType
from codex.planner.models import PlanStatus
from codex.query_understanding.models import Intent


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


class AskStatus(StrEnum):
    """Honest representation of what `CodexAPI.ask()` produced -- the
    same "represent the state, never fabricate" discipline
    `codex.llm.gateway.GenerationStatus` and `codex.query_understanding.
    engine.UnderstandingStatus` already establish, carried one level up
    to the query/ask API boundary. A repository/precondition failure
    (unknown repository, not-ready, no Gateway configured, or an
    upstream Gateway exception such as an authentication/transport
    failure) is a structural error raised by `CodexAPI.ask()` and
    mapped to an HTTP status by `codex.api.server` -- never folded into
    this enum, which exists only for the legitimate outcomes of a
    request that *did* reach the LLM boundary."""

    OK = "OK"
    """A grounded `StructuredAnswer` was generated
    (`GenerationStatus.OK`)."""

    UNDERSTANDING_INCOMPLETE = "UNDERSTANDING_INCOMPLETE"
    """`understand_query` could not produce a `QueryContract` at all
    (Tier-0 ambiguous with no SLM configured, or SLM confidence too low
    to execute) -- no plan, no evidence, no LLM call was attempted.
    `AskResponse.detail` carries `UnderstandingStatus`'s own reason."""

    MALFORMED_OUTPUT = "MALFORMED_OUTPUT"
    """Mirrors `GenerationStatus.MALFORMED_OUTPUT` exactly."""

    LLM_TIMEOUT = "LLM_TIMEOUT"
    """Mirrors `GenerationStatus.TIMEOUT` exactly."""

    LLM_BUDGET_EXCEEDED = "LLM_BUDGET_EXCEEDED"
    """Mirrors `GenerationStatus.BUDGET_EXCEEDED` exactly."""


class AskRequest(BaseModel):
    """Wire contract for `POST /query` (API Integration Milestone).
    `token_budget`/`latency_budget_ms` override `understand_query`'s own
    defaults when given -- the same two budgets `QueryContract`/
    `LLMRequest` already carry, no new budget concept introduced.

    Deliberately **no** `model`/`provider` override field: no Gateway
    this project ships supports per-request reconfiguration
    (`OpenAIGateway`'s `model` is fixed at construction) -- accepting
    the field here would silently promise behavior nothing implements.
    What model/provider actually served the request is always reported
    back on `AskResponse.model` instead.
    """

    repository_id: str
    query_text: str
    token_budget: int | None = Field(default=None, gt=0)
    latency_budget_ms: int | None = Field(default=None, gt=0)


class EvidenceContextSummary(BaseModel):
    """The real `EvidencePackage` the LLM actually received (TAD §42),
    projected through the same `VisualizationNode`/`VisualizationEdge`
    shapes `/neighborhood` already returns -- not a parallel evidence
    representation. `node.distance` is always `0` here: `EvidencePackage`
    itself does not carry per-entity traversal distance (only
    `bounded_traversal`'s own internal result does, and that is not part
    of D9's closed `EvidencePackage` contract), so this is an honest
    "not tracked at this boundary", never a fabricated value.
    """

    graph_version: GraphVersionRef | None = None
    entities: list[VisualizationNode] = Field(default_factory=list)
    relationships: list[VisualizationEdge] = Field(default_factory=list)
    evidence_count: int = 0
    coverage: dict[str, str] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    partial: bool = False


class ModelMetadata(BaseModel):
    """What actually served the request. `requested_model` is what the
    configured Gateway was asked to use; `served_model` is what the
    provider's own response reported (`OpenAIGateway.ResponseMetadata.
    served_model`) -- never assumed equal, per that module's own
    documented distinction."""

    provider: str
    requested_model: str
    served_model: str | None = None
    usage_prompt_tokens: int | None = None
    usage_completion_tokens: int | None = None
    usage_total_tokens: int | None = None
    finish_reason: str | None = None


class AskResponse(BaseModel):
    """`POST /query` response: repository -> query -> intent/evidence
    requirements -> targeted graph retrieval -> minimal sufficient
    grounded context -> LLM -> grounded answer, in one contract. Every
    field beyond `answer`/`claims` is metadata *about* that pipeline's
    real, unmodified stages -- nothing here is computed independently
    of `understand_query`/`plan_query`/`execute_query`/`LLMGateway`.
    """

    repository_id: str
    query_text: str

    query_id: str
    """`compute_query_identity(contract)` -- `codex.planner.cache`'s own
    deterministic content hash. Empty only when `status is
    AskStatus.UNDERSTANDING_INCOMPLETE` (no `QueryContract` was ever
    produced)."""

    run_id: str
    """Deterministic per this request's own reproducibility dimensions
    (repository revision, query identity, provider, requested model) --
    two requests sharing all four get the identical id. Empty alongside
    `query_id` when understanding did not resolve."""

    status: AskStatus
    intent: Intent | None = None
    plan_status: PlanStatus | None = None

    answer: str | None = None
    """`StructuredAnswer.explanation` -- presentational only, never
    itself the source of truth for verification (`codex.llm.schema`'s
    own documented boundary). Present only when `status is
    AskStatus.OK`."""

    claims: list[Claim] = Field(default_factory=list)
    """The model's real `claims[]`, unmodified -- canonical
    subject/predicate/object triples straight from `StructuredAnswer`,
    never rewritten, filtered, or re-scored by the API layer."""

    evidence_context: EvidenceContextSummary = Field(default_factory=EvidenceContextSummary)
    model: ModelMetadata
    detail: str | None = None


__all__ = [
    "AskRequest",
    "AskResponse",
    "AskStatus",
    "EvidenceContextSummary",
    "GraphVersionRef",
    "IngestionJobHandle",
    "IngestionJobStatus",
    "ModelMetadata",
    "ProviderSummary",
    "RepositoryPhase",
    "RepositoryStatus",
    "VisualizationEdge",
    "VisualizationGraph",
    "VisualizationNode",
]
