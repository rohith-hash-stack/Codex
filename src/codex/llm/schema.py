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

from pydantic import BaseModel, Field

from codex.ontology.relationships import RelationshipType


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

    `predicate` is deliberately typed as the canonical `RelationshipType`
    (TAD §14, already closed) rather than an open string -- an LLM claim
    about a predicate the ontology does not recognize is a genuine
    schema-validation failure (directive D10.2: "Malformed structured
    output... never pass malformed claims to verification"), not a
    silently-accepted free-text predicate.
    """

    subject: str
    predicate: RelationshipType
    object: str
    claim_type: ClaimType


class StructuredAnswer(BaseModel):
    """TAD §44's top-level V1 structured LLM response."""

    explanation: str
    claims: list[Claim] = Field(default_factory=list)


__all__ = ["Claim", "ClaimType", "StructuredAnswer"]
