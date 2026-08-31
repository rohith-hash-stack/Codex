"""Telemetry data model (TAD component #16; TAD §55, §60, §64-65; HLRD
§46, §52-53; directive D11).

**Scope, per the approved D11 decisions** (`docs/architecture-conformance-
audit.md` §X, and the explicit D11-implementation directive):

1. Telemetry Store only -- no Artifact Store (TAD component #17).
2. `codex.planner.planner.GraphVersionMismatchError` remains D9's own
   detection mechanism for TAD §55's `CONCURRENT_UPDATE_DETECTED`.
   This module only *records* the event a caller already detected --
   see `codex.telemetry.mapping.failure_event_from_graph_version_mismatch`.
   Telemetry never re-derives or re-checks a graph-version match itself.
3. `QueryTelemetryEvent`'s field list is **exactly** TAD §65's named
   set -- no HLRD-only field (HLRD §52's "ranking signals", "selected
   subgraph"/"coverage", "answer outcome" are deliberately absent; see
   `docs/architecture-conformance-audit.md` §X.6/X.8 item 3) is added
   unless TAD is explicitly amended later.
4. `FeedbackRecord` defines the *shape* TAD §60's feedback examples
   take (thumbs up/down, correction, click-through, follow-up query,
   explicit disambiguation -- TAD §60's own list, not HLRD §46's
   slightly different superset, per the same "TAD §65 is canonical"
   discipline). No collection endpoint/producer exists anywhere --
   record/store infrastructure only, per the approved decision 4.

**Determinism:** every event's `event_id` is a deterministic hash of
its own defining fields (matching `codex.ontology.entities.
build_canonical_id`'s precedent) -- never a random UUID. Two events
built from identical inputs (including an identical explicit `now`)
are byte-for-byte identical.

**What this module deliberately does NOT do:** it does not compute
"candidate counts" or contradiction/unsupported-claim counts from raw
pipeline objects (that would put business-logic interpretation inside
Telemetry, which the approved directive rules out -- "Telemetry must
not own detection/recovery"). Every field here is a plain, caller-
supplied value; the caller (a future orchestration boundary) is
responsible for deriving it from whatever it already computed.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from codex.graph.version import GraphVersion
from codex.planner.models import RetrievalPlan
from codex.query_understanding.models import QueryContract
from codex.verification.state import VerificationStatus


class FailureCode(StrEnum):
    """TAD §64's failure taxonomy, verbatim -- the exact eleven named
    codes, no more, no fewer. "Every failure should be observable"
    (TAD §64) is what this enum exists to satisfy; Telemetry does not
    decide which code applies (the caller, who already detected the
    failure, does) -- it only gives the code a place to be recorded."""

    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PARTIAL_PROVIDER_RESULT = "PARTIAL_PROVIDER_RESULT"
    ENTITY_UNRESOLVED = "ENTITY_UNRESOLVED"
    GRAPH_VERSION_CONFLICT = "GRAPH_VERSION_CONFLICT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    PLAN_BLOCKED = "PLAN_BLOCKED"
    PLAN_UNSUPPORTED = "PLAN_UNSUPPORTED"
    LLM_SCHEMA_FAILURE = "LLM_SCHEMA_FAILURE"
    VERIFICATION_FAILURE = "VERIFICATION_FAILURE"
    CONCURRENT_UPDATE_DETECTED = "CONCURRENT_UPDATE_DETECTED"


class FeedbackKind(StrEnum):
    """TAD §60's feedback examples, verbatim."""

    THUMBS_UP = "THUMBS_UP"
    THUMBS_DOWN = "THUMBS_DOWN"
    CORRECTION = "CORRECTION"
    CLICK_THROUGH = "CLICK_THROUGH"
    FOLLOW_UP_QUERY = "FOLLOW_UP_QUERY"
    EXPLICIT_DISAMBIGUATION = "EXPLICIT_DISAMBIGUATION"


class FeedbackRecord(BaseModel):
    """TAD §60/§65 "user feedback" -- the record *shape* only. Nothing
    in this codebase produces one yet (no API/UI collection surface
    exists, ADR-015 still open) -- a caller constructs this directly
    when/if a real feedback source exists. "Feedback is a learning
    signal, not automatically ground truth" (HLRD §46) -- stored as
    reported, never validated or promoted to fact here."""

    kind: FeedbackKind
    detail: str | None = None


def _event_id(*parts: str) -> str:
    """Deterministic event identity -- a content hash of the event's
    own defining fields, matching `build_canonical_id`'s precedent.
    Never a random UUID: two events built from identical inputs
    (including an identical explicit `now`) get the identical id."""
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"telemetry:{digest[:32]}"


class QueryTelemetryEvent(BaseModel):
    """TAD §65's observability field list, verbatim, one record per
    completed query lifecycle. `graph_version`/`selected_providers`
    are also reachable through `retrieval_plan` (which embeds the full
    `GraphVersion`) -- both are kept as separate top-level fields too
    because TAD §65 names them as independent capture targets
    alongside `retrieval_plan`, matching how e.g. `EvidenceCohort`
    already carries fields also reachable through its own nested
    values, for direct query/filtering convenience.

    `candidate_count`/`mss_size` are TAD §65's two separately-named
    fields ("candidate counts", "MSS size") -- V1's `execute_query()`
    (D9) exposes only the final `EvidencePackage`, never a separate
    pre-MSS candidate set, so both are computed from the same source
    in this implementation. Recorded honestly, not silently merged
    into one field -- see `docs/architecture-conformance-audit.md`
    §X.5 for the reasoning this documents.

    `llm_tokens`/`latency_ms` are `None`-able because no wall-clock
    timing or token-count instrumentation exists anywhere in D1-D10
    today (confirmed by grep during the D11 pre-implementation audit)
    -- a caller with real measurements supplies them; this module
    never fabricates a value.
    """

    event_id: str
    recorded_at: datetime

    # TAD §65: query_id, repository_id, graph_version
    query_id: str
    repository_id: str
    graph_version_id: str

    # TAD §65: query_contract
    query_contract: QueryContract

    # TAD §65: selected_providers
    selected_providers: dict[str, list[str]] = Field(default_factory=dict)

    # TAD §65: retrieval_plan
    retrieval_plan: RetrievalPlan

    # TAD §65: candidate counts, MSS size
    candidate_count: int = Field(ge=0)
    mss_size: int = Field(ge=0)

    # TAD §65: LLM calls, LLM tokens
    llm_calls: int = Field(ge=0)
    llm_tokens: int | None = Field(default=None, ge=0)

    # TAD §65: latency
    latency_ms: float | None = Field(default=None, ge=0.0)

    # TAD §65: verification result
    verification_result: VerificationStatus | None = None

    # TAD §65: unsupported claims, contradictions
    unsupported_claim_count: int = Field(default=0, ge=0)
    contradiction_count: int = Field(default=0, ge=0)

    # TAD §65: cache hits
    cache_hit: bool = False

    # TAD §65: provider failures
    provider_failure_count: int = Field(default=0, ge=0)

    # TAD §65: user feedback
    user_feedback: FeedbackRecord | None = None

    @staticmethod
    def build(
        *,
        query_id: str,
        graph_version: GraphVersion,
        query_contract: QueryContract,
        retrieval_plan: RetrievalPlan,
        candidate_count: int,
        mss_size: int,
        llm_calls: int,
        llm_tokens: int | None = None,
        latency_ms: float | None = None,
        verification_result: VerificationStatus | None = None,
        unsupported_claim_count: int = 0,
        contradiction_count: int = 0,
        cache_hit: bool = False,
        provider_failure_count: int = 0,
        user_feedback: FeedbackRecord | None = None,
        now: datetime | None = None,
    ) -> QueryTelemetryEvent:
        """Construct a `QueryTelemetryEvent` with a deterministic
        `event_id` derived from its own defining fields -- the
        preferred construction path (direct `QueryTelemetryEvent(...)`
        construction requires supplying `event_id`/`recorded_at`
        yourself)."""
        recorded_at = now or datetime.now(UTC)
        event_id = _event_id(
            "query",
            query_id,
            graph_version.version_id,
            recorded_at.isoformat(),
        )
        return QueryTelemetryEvent(
            event_id=event_id,
            recorded_at=recorded_at,
            query_id=query_id,
            repository_id=graph_version.repository_id,
            graph_version_id=graph_version.version_id,
            query_contract=query_contract,
            selected_providers=retrieval_plan.selected_providers,
            retrieval_plan=retrieval_plan,
            candidate_count=candidate_count,
            mss_size=mss_size,
            llm_calls=llm_calls,
            llm_tokens=llm_tokens,
            latency_ms=latency_ms,
            verification_result=verification_result,
            unsupported_claim_count=unsupported_claim_count,
            contradiction_count=contradiction_count,
            cache_hit=cache_hit,
            provider_failure_count=provider_failure_count,
            user_feedback=user_feedback,
        )


class FailureTelemetryEvent(BaseModel):
    """TAD §64's failure taxonomy, recorded generically -- "every
    failure should be observable" (TAD §64, verbatim). `query_id`/
    `graph_version_id` are optional because not every failure code is
    query-scoped (e.g. `PROVIDER_UNAVAILABLE` can occur during
    ingestion, before any query exists)."""

    event_id: str
    recorded_at: datetime
    code: FailureCode
    repository_id: str
    query_id: str | None = None
    graph_version_id: str | None = None
    detail: str = ""

    @staticmethod
    def build(
        *,
        code: FailureCode,
        repository_id: str,
        query_id: str | None = None,
        graph_version_id: str | None = None,
        detail: str = "",
        now: datetime | None = None,
    ) -> FailureTelemetryEvent:
        """Construct a `FailureTelemetryEvent` with a deterministic
        `event_id`. This is a pure recording function: it never
        inspects `detail`/re-derives whether the failure actually
        occurred -- the caller has already detected it."""
        recorded_at = now or datetime.now(UTC)
        event_id = _event_id(
            "failure",
            code.value,
            repository_id,
            query_id or "",
            graph_version_id or "",
            recorded_at.isoformat(),
        )
        return FailureTelemetryEvent(
            event_id=event_id,
            recorded_at=recorded_at,
            code=code,
            repository_id=repository_id,
            query_id=query_id,
            graph_version_id=graph_version_id,
            detail=detail,
        )


__all__ = [
    "FailureCode",
    "FailureTelemetryEvent",
    "FeedbackKind",
    "FeedbackRecord",
    "QueryTelemetryEvent",
]
