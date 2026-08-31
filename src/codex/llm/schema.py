"""Structured Answer / Claim schema (TAD §44-45; directive D10.2).

TAD §44's exact V1 structured-output shape: `{"answer": "...", "claims":
[{"subject": "...", "predicate": "...", "object": "...", "claim_type":
"FACT"}]}`, strict-JSON-Schema-validated. This module is the Python
representation of that one schema -- **not** a new architectural
contract (D10 Decision 3: `ResponseContract` is TAD §44's own schema,
never a competing structure).

The `explanation` field is presentational and is **not** itself the
source of truth for verification (directive D10 Decision, Phase C.1):
the Verification Engine (D10.4) verifies only `claims[]`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from codex.ontology.relationships import DERIVED_RELATIONSHIP_TYPES, RelationshipType


class ClaimType(StrEnum):
    """TAD §45's four-value claim classification."""

    FACT = "FACT"
    """Direct graph fact, e.g. "A CALLS B"."""

    DERIVED = "DERIVED"
    """Computed from graph structure, e.g. "A REACHES C" (bounded traversal)."""

    INFERENCE = "INFERENCE"
    """Semantic/interpretive, e.g. "A appears to handle authentication" --
    never automatically VERIFIED (TAD §47) without explicit deterministic support."""

    UNKNOWN = "UNKNOWN"
    """The model did not (or could not) classify the claim -- an honest
    "unclassified" value, never silently coerced to FACT."""


class Claim(BaseModel):
    """TAD §44's per-claim struct: strict subject-predicate-object.

    `predicate` accepts either a persisted `RelationshipType` (TAD §14)
    or one of the three query-time-computed `DERIVED_RELATIONSHIP_TYPES`
    (TAD §14: `REACHES`/`TRANSITIVE_CALLS`/`INDIRECTLY_DEPENDS_ON`) --
    both halves of the *same* closed ontology TAD §14 defines. Rejecting
    the derived half would make it impossible to represent TAD §45's
    own worked example ("DERIVED: A REACHES C"). A predicate outside
    both sets is a genuine schema-validation failure (directive D10.2:
    "Malformed structured output... never pass malformed claims to
    verification"), not a silently-accepted free-text predicate.
    """

    subject: str
    predicate: RelationshipType | str
    object: str
    claim_type: ClaimType

    @field_validator("predicate", mode="before")
    @classmethod
    def _validate_predicate(cls, value: Any) -> RelationshipType | str:
        if isinstance(value, RelationshipType):
            return value
        if isinstance(value, str):
            try:
                return RelationshipType(value)
            except ValueError:
                pass
            if value in DERIVED_RELATIONSHIP_TYPES:
                return value
        raise ValueError(
            f"predicate {value!r} is neither a RelationshipType nor one of "
            f"DERIVED_RELATIONSHIP_TYPES {sorted(DERIVED_RELATIONSHIP_TYPES)}"
        )


class StructuredAnswer(BaseModel):
    """TAD §44's top-level V1 structured LLM response."""

    explanation: str
    claims: list[Claim] = Field(default_factory=list)


__all__ = ["Claim", "ClaimType", "StructuredAnswer"]
