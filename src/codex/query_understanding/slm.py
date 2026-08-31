"""The SLM boundary (TAD §22, §24-25; directive D8 Phase 7).

**Interface only — no real model dependency.** TAD §24 lists what the
SLM *determines* (intent, targets, relationships, constraints,
complexity, ambiguity, temporal requirements, completeness); TAD §25
requires its confidence to be "a calibrated probability... not raw
logits." Nothing in HLRD/TAD requires a specific SLM at D8 — introducing
one now would violate the directive's explicit "do NOT introduce a real
model dependency unless the specification explicitly requires one at
this phase" and `docs/policy-external-references.md`'s independent-
implementation discipline (there is nothing to independently implement
*from* here — a model isn't a public spec/format to study).

``SLMAdapter`` is a `Protocol`, matching the same shape D1's
`ProviderAdapter` already established for provider integration — a
consistent pattern, not a new one. A real adapter (calling an actual
SLM) is future work, explicitly out of D8's scope.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, Field

from codex.coverage.engine import CompletenessLevel
from codex.ontology.relationships import RelationshipType
from codex.query_understanding.models import Intent, TemporalDimension
from codex.query_understanding.tier0 import Tier0Candidate


class SLMInterpretation(BaseModel):
    """Structured SLM output (TAD §24's exact field list) that can be
    validated deterministically before entering a `QueryContract` --
    "structured interpretation data," per directive Phase 7, not free text.

    ``confidence`` is documented, by contract, as a calibrated
    probability (TAD §25) — this type can only enforce the ``[0,1]``
    range and field shape; it cannot mechanically prove a given
    ``SLMAdapter`` implementation actually calibrated its output rather
    than exposing raw model scores. That responsibility belongs to
    whichever adapter implementation eventually exists; this is recorded
    honestly rather than claimed as enforced.
    """

    intent: Intent
    targets: list[str] = Field(default_factory=list)
    relationship_types: list[RelationshipType] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    temporal_dimension: TemporalDimension = TemporalDimension.NONE
    completeness_requirement: CompletenessLevel = CompletenessLevel.LOW
    ambiguity: float = Field(ge=0.0, le=1.0)
    reasoning_requirement: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)


class SLMAdapter(Protocol):
    """What any future real SLM integration must implement. No
    implementation of this protocol ships in D8 — see this module's
    docstring."""

    def interpret(
        self, query_text: str, tier0_candidates: Sequence[Tier0Candidate]
    ) -> SLMInterpretation: ...


__all__ = ["SLMAdapter", "SLMInterpretation"]
