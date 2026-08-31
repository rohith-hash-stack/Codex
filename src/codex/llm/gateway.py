"""LLM Gateway contract (TAD §43; directive D10.1).

TAD §43: "The LLM Gateway: manages model invocation; validates
structured output; enforces token limits; records usage; prevents
arbitrary repository access. The LLM receives: Query + EvidencePackage
+ ResponseContract. It does not receive unrestricted repository
access."

Protocol/interface only -- no mandatory real LLM dependency (matching
D8's `SLMAdapter` precedent). `LLMRequest` closes over exactly TAD
§43's three inputs plus the token/latency budgets already carried by
`QueryContract`/`RetrievalPlan` (no new budget concept). The Gateway
itself is the one place structured-output validation happens (TAD
§43's own "validates structured output"), so `generate()` returns a
typed `LLMGenerationResult` that is honest about failure -- never a
fabricated `StructuredAnswer` when parsing/validation failed (same
"represent the state, don't fabricate" discipline as D8's
`UnderstandingStatus`).

**Boundary**: this module has no graph/provider/evidence-selection
responsibility -- `LLMRequest` carries only an already-assembled
`EvidencePackage` (D9's own output), never a `GraphReader`,
`CapabilityRegistry`, or `EvidenceStore` reference. A concrete Gateway
implementation cannot query the graph, select providers, or write
evidence even if it wanted to: the types it is given contain none of
those references.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field

from codex.llm.schema import StructuredAnswer
from codex.planner.mss import EvidencePackage


class GenerationStatus(StrEnum):
    """Honest representation of what `generate()` actually produced --
    never silently coerced to a fabricated `StructuredAnswer` (TAD
    §44's "Invalid/missing claims array -> reject" requirement)."""

    OK = "OK"
    """`answer` is populated and schema-valid."""

    MALFORMED_OUTPUT = "MALFORMED_OUTPUT"
    """Invalid JSON, or JSON that fails `StructuredAnswer`'s schema
    (missing/invalid claims array, TAD §44)."""

    TIMEOUT = "TIMEOUT"
    """The model did not respond within `latency_budget_ms`."""

    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    """The request could not be issued (or the response discarded)
    because it would exceed `token_budget`."""


class LLMRequest(BaseModel):
    """TAD §43's exact three inputs (`Query + EvidencePackage +
    ResponseContract`) plus the budgets already carried through
    `QueryContract`/`RetrievalPlan` (TAD §27, §41-42) -- no new budget
    concept introduced.

    `response_schema` **is** `ResponseContract` (D10 Decision 3): the
    JSON Schema descriptor constraining the model's output shape,
    represented directly from `StructuredAnswer.model_json_schema()`
    rather than as a separate, competing structure.
    """

    query_text: str
    """Treated as inert data throughout the gateway and by any
    concrete implementation -- never interpreted as an instruction
    (directive D10.9 security requirement)."""

    evidence_package: EvidencePackage
    response_schema: dict
    token_budget: int = Field(gt=0)
    latency_budget_ms: int = Field(gt=0)
    feedback: str | None = None
    """Set only on a re-synthesis request (D10.5/D10.7): deterministic
    instruction text (e.g. "remove claim X: contradicted by evidence"),
    never a request for the model to justify or preserve the removed
    claim."""


class LLMGenerationResult(BaseModel):
    """What one `generate()` call actually produced."""

    status: GenerationStatus
    answer: StructuredAnswer | None = None
    """Present only when `status is GenerationStatus.OK`."""

    raw_output: str | None = None
    """The model's raw text, retained only for telemetry/debugging when
    parsing failed -- never re-interpreted as instructions or re-parsed
    as claims by any other component (directive D10.2: "The verifier
    must NEVER parse claims back out of free-form explanatory prose")."""

    detail: str | None = None


class LLMGateway(Protocol):
    """Structured generation only -- no tool use, no file access, no
    provider/evidence selection. A concrete implementation manages
    model invocation and enforces `request.token_budget`/
    `.latency_budget_ms` itself; this Protocol makes no assumption
    about which model or vendor backs it (matching `ProviderAdapter`/
    `SLMAdapter`'s existing interface-only pattern)."""

    def generate(self, request: LLMRequest) -> LLMGenerationResult: ...


__all__ = [
    "GenerationStatus",
    "LLMGateway",
    "LLMGenerationResult",
    "LLMRequest",
]
