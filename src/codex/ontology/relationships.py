"""Canonical relationship ontology (HLRD §16, TAD §14)."""

from __future__ import annotations

from enum import StrEnum


class RelationshipType(StrEnum):
    """Persisted relationship predicates.

    Derived relationships (see ``DERIVED_RELATIONSHIP_TYPES``) are
    computed at query time in V1 rather than materialized here
    (TAD §14).
    """

    CONTAINS = "CONTAINS"
    IMPORTS = "IMPORTS"
    DEPENDS_ON = "DEPENDS_ON"
    CALLS = "CALLS"
    REFERENCES = "REFERENCES"
    IMPLEMENTS = "IMPLEMENTS"
    EXTENDS = "EXTENDS"
    OVERRIDES = "OVERRIDES"
    TESTED_BY = "TESTED_BY"
    CONFIGURED_BY = "CONFIGURED_BY"
    EXPOSES = "EXPOSES"
    CONSUMES = "CONSUMES"
    PERSISTS_TO = "PERSISTS_TO"
    CO_CHANGED_WITH = "CO_CHANGED_WITH"
    OBSERVED_CALL = "OBSERVED_CALL"
    READS = "READS"
    WRITES = "WRITES"


DERIVED_RELATIONSHIP_TYPES = frozenset(
    {"REACHES", "TRANSITIVE_CALLS", "INDIRECTLY_DEPENDS_ON"}
)
"""Relationship kinds computed at query time, never persisted (TAD §14)."""
