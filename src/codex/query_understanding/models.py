"""Query Understanding data model (HLRD §24-29; TAD §22-28; directive D8).

`QueryContract` is TAD §27's own struct, field-for-field — see
`docs/architecture-conformance-audit.md` §P.1 for the full traceability
of every field to its source section, including the two fields
deliberately **not** added (`reasoning_requirement` as a standalone
field, `graph_version`) because TAD's own struct omits them.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from codex.coverage.engine import CompletenessLevel
from codex.ontology.relationships import RelationshipType
from codex.provider.capability import Capability


class Intent(StrEnum):
    """Query intent vocabulary (HLRD §28, §30; TAD §23's worked example).

    Uses the specific, operation-shaped vocabulary HLRD §30/TAD §23
    demonstrate concretely flowing into a `QueryContract` — see the audit
    doc's "Granularity note" for why HLRD §28's broader categories are
    not a separate field. Only intents with a genuine, testable Tier-0
    deterministic pattern are matched in `codex.query_understanding.tier0`;
    the remaining vocabulary members exist for the SLM/planner boundary
    (HLRD §30) and for `UNKNOWN`'s explicit "no match" representation.
    """

    FIND_CALLERS = "FIND_CALLERS"
    TRACE_EXECUTION = "TRACE_EXECUTION"
    FIND_IMPLEMENTATIONS = "FIND_IMPLEMENTATIONS"
    FIND_TESTS = "FIND_TESTS"
    FIND_IMPACT = "FIND_IMPACT"
    FIND_DEPENDENCIES = "FIND_DEPENDENCIES"
    HISTORY_ANALYSIS = "HISTORY_ANALYSIS"
    ARCHITECTURE_ANALYSIS = "ARCHITECTURE_ANALYSIS"
    CODE_LOOKUP = "CODE_LOOKUP"
    UNKNOWN = "UNKNOWN"
    """No deterministic or SLM-provided intent could be established —
    an explicit, honest "no match" value, never silently coerced to
    some other intent."""


class TemporalDimension(StrEnum):
    """TAD §27 lists `temporal_dimension` as a `QueryContract` field and a
    complexity factor but never enumerates its values anywhere in HLRD or
    TAD — this enum is a documented **implementation detail** (directive
    Phase 19, category 1: no downstream component's contract depends on
    the exact label set yet), not an invented architectural decision."""

    NONE = "NONE"
    POINT_IN_TIME = "POINT_IN_TIME"
    RANGE = "RANGE"
    HISTORICAL = "HISTORICAL"


class AmbiguityCandidate(BaseModel):
    """One ranked alternative interpretation (HLRD §27's worked example:
    ranked alternatives with per-candidate confidence)."""

    intent: Intent
    target: str
    confidence: float = Field(ge=0.0, le=1.0)


class ComplexityFactors(BaseModel):
    """The six normalized inputs to TAD §26's complexity formula. Each
    factor is independently normalized to [0,1] by whoever derives it
    (Tier-0 or the SLM) — this type only carries the already-normalized
    values, it does not compute them (that's `codex.query_understanding.
    complexity.compute_complexity`)."""

    intent_count: float = Field(ge=0.0, le=1.0)
    target_count: float = Field(ge=0.0, le=1.0)
    relationship_depth: float = Field(ge=0.0, le=1.0)
    ambiguity: float = Field(ge=0.0, le=1.0)
    temporal_dimension: float = Field(ge=0.0, le=1.0)
    reasoning_requirement: float = Field(ge=0.0, le=1.0)


class QueryContract(BaseModel):
    """TAD §27's contract, verbatim field-for-field (see this module's
    docstring). The validated output of Query Understanding — the sole
    boundary object handed to the (not-yet-implemented) Query Planner.

    Deliberately does **not** carry: `reasoning_requirement` as a
    standalone field (TAD §26 places it inside complexity only) or
    `graph_version` (Planner-stage concern, TAD §29) — see
    `docs/architecture-conformance-audit.md` §P.1.
    """

    intent: Intent
    targets: list[str] = Field(default_factory=list)
    relationship_types: list[RelationshipType] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    temporal_dimension: TemporalDimension = TemporalDimension.NONE
    complexity: float = Field(ge=0.0, le=1.0)
    ambiguity: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    completeness_requirement: CompletenessLevel = CompletenessLevel.LOW
    required_evidence: list[Capability] = Field(default_factory=list)
    token_budget: int = Field(gt=0)
    latency_budget_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def _validate_contract_consistency(self) -> QueryContract:
        """Directive Phase 10: reject contradictory contract fields, not
        just individually-invalid ones. `UNKNOWN` intent with a non-empty
        `targets`/`relationship_types` is contradictory — an unresolved
        query has nothing concrete to target."""
        if self.intent is Intent.UNKNOWN and (self.targets or self.relationship_types):
            raise ValueError(
                "UNKNOWN intent must not carry targets or relationship_types "
                "(a genuinely unresolved query has no concrete target)"
            )
        return self


__all__ = [
    "AmbiguityCandidate",
    "ComplexityFactors",
    "Intent",
    "QueryContract",
    "TemporalDimension",
]
