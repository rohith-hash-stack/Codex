"""Provider capability identifiers (TAD §9-10).

Each member is grounded in a capability TAD or ``docs/resources.md``
actually names — this is not an attempt at an exhaustive taxonomy.
Extend it only when a concrete adapter or spec section demands a new
identifier, per the "no silent architectural drift" rule.
"""

from __future__ import annotations

from enum import StrEnum


class Capability(StrEnum):
    """A single extractable unit of provider evidence."""

    SYMBOL_DEFINITION = "SYMBOL_DEFINITION"
    """TAD §9 (SCIPAdapter.capabilities example)."""

    SYMBOL_REFERENCE = "SYMBOL_REFERENCE"
    """TAD §9."""

    IMPLEMENTATION = "IMPLEMENTATION"
    """TAD §9."""

    CALL_RELATIONSHIP = "CALL_RELATIONSHIP"
    """TAD §9-10 (Capability Registry worked example)."""

    TYPE_RELATIONSHIP = "TYPE_RELATIONSHIP"
    """TAD §9."""

    DATA_FLOW = "DATA_FLOW"
    """CodeQL's distinguishing capability per docs/research/provider-formats.md."""

    SOURCE_LOCATION = "SOURCE_LOCATION"
    """Backs ``RepositorySymbol.source_location`` (TAD §12); named explicitly
    in this directive's Capability Registry worked example (§14)."""

    DEPENDENCY = "DEPENDENCY"
    """Backs ``RelationshipType.DEPENDS_ON``; named explicitly in this
    directive's Capability Registry worked example (§14)."""

    CO_CHANGE = "CO_CHANGE"
    """Backs ``RelationshipType.CO_CHANGED_WITH`` (HLRD §13, TAD §72) — Git-specific."""

    HISTORY = "HISTORY"
    """Historical repository state (HLRD §13, TAD §72) — Git-specific."""
