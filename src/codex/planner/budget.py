"""Budget-aware planning (TAD §32, §41; directive D9 Part 7).

TAD §41 gives the token-budget ceiling formula explicitly
(``max_nodes = min(100, token_budget/average_node_cost)``,
``max_edges = min(250, token_budget/average_edge_cost)``) but never
defines ``average_node_cost``/``average_edge_cost`` numerically -- the
two module-level constants below are a **documented calibration point**
(same precedent as ADR-018's freshness half-life and D8's session-decay
half-life), not a claimed-final cost model.

TAD gives **no** analogous formula relating ``latency_budget_ms`` to a
node/edge ceiling at all (§32 only describes the five-step pruning
*procedure*). Rather than inventing an unrelated cost model, this module
ties latency purely to ``traversal_depth`` -- the one Planner-controlled,
discrete knob TAD §32 step 1 ("reduce traversal depth") already names as
the first pruning response -- via one further labeled calibration
constant. This is the single most judgment-laden call in D9
(`docs/architecture-conformance-audit.md` §R.2/§R.6): flagged here with
maximum visibility, not silently applied.
"""

from __future__ import annotations

from typing import Final

from codex.planner.models import RetrievalBudget

DEFAULT_AVERAGE_NODE_COST_TOKENS: Final[int] = 50
"""Calibration point -- TAD §41 gives the formula, not this constant."""

DEFAULT_AVERAGE_EDGE_COST_TOKENS: Final[int] = 20
"""Calibration point -- TAD §41 gives the formula, not this constant."""

MAX_NODES_CEILING: Final[int] = 100
"""TAD §41's own literal ceiling."""

MAX_EDGES_CEILING: Final[int] = 250
"""TAD §41's own literal ceiling."""

DEFAULT_LATENCY_MS_PER_DEPTH_LEVEL: Final[int] = 1500
"""Calibration point (not from TAD -- see module docstring): the latency
cost budgeted for one additional traversal-depth level."""


def token_derived_ceiling(token_budget: int) -> tuple[int, int]:
    """TAD §41's exact formula, using this module's calibration constants."""
    max_nodes = min(MAX_NODES_CEILING, token_budget // DEFAULT_AVERAGE_NODE_COST_TOKENS)
    max_edges = min(MAX_EDGES_CEILING, token_budget // DEFAULT_AVERAGE_EDGE_COST_TOKENS)
    return max(max_nodes, 0), max(max_edges, 0)


def latency_derived_depth_ceiling(latency_budget_ms: int, requested_depth: int) -> int:
    """The maximum traversal depth `latency_budget_ms` can afford, never
    exceeding what was actually requested."""
    afforded = latency_budget_ms // DEFAULT_LATENCY_MS_PER_DEPTH_LEVEL
    return max(0, min(requested_depth, afforded))


def compute_budget(token_budget: int, latency_budget_ms: int) -> RetrievalBudget:
    max_nodes, max_edges = token_derived_ceiling(token_budget)
    return RetrievalBudget(
        token_budget=token_budget,
        latency_budget_ms=latency_budget_ms,
        max_nodes=max_nodes,
        max_edges=max_edges,
    )


__all__ = [
    "DEFAULT_AVERAGE_EDGE_COST_TOKENS",
    "DEFAULT_AVERAGE_NODE_COST_TOKENS",
    "DEFAULT_LATENCY_MS_PER_DEPTH_LEVEL",
    "MAX_EDGES_CEILING",
    "MAX_NODES_CEILING",
    "compute_budget",
    "latency_derived_depth_ceiling",
    "token_derived_ceiling",
]
