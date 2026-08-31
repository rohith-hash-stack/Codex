"""Query-time provider selection (TAD §31; directive D9 Part 5).

Thin orchestration only -- **no scoring algorithm lives here**. Every
decision is delegated to the already-closed D2 `CapabilityRegistry.rank()`
(ADR-018), which already implements TAD §31's formula, exclusion rule, and
deterministic tie-breaking. This module must never grow its own ranking
logic (`docs/architecture-conformance-audit.md` §R.4): duplicating D2 here
would be exactly the collapse directive Part 5 forbids.

Ingestion-time provider selection (`CapabilityRegistry.evaluate()`, driving
`IngestionPipeline` -- every usable provider runs so contradictory evidence
reaches the graph) is a distinct operation from this module's query-time
selection (`CapabilityRegistry.rank()` -- which already-ingested provider's
evidence to prefer when ranking). The two are never collapsed here.
"""

from __future__ import annotations

from codex.provider.capability import Capability
from codex.registry.registry import CapabilityRegistry
from codex.repository.models import RepositoryMetadata


def select_providers(
    registry: CapabilityRegistry,
    required_capabilities: list[Capability],
    repository: RepositoryMetadata,
) -> dict[str, list[str]]:
    """Rank usable providers per required capability, best first.

    A capability with no usable provider maps to `[]` -- the same
    `NOT_SUPPORTED`/`UNAVAILABLE` distinction `codex.coverage` already
    makes; this function never raises for that case, only for whatever
    `CapabilityRegistry.rank()` itself would raise (a usable provider
    missing a `ProviderScoreProfile` -- an existing D2 invariant, not
    something this module changes).
    """
    return {
        capability.value: [
            evaluation.provider_name for evaluation in registry.rank(capability, repository)
        ]
        for capability in required_capabilities
    }


__all__ = ["select_providers"]
