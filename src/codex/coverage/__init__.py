"""The Coverage / Completeness Engine (TAD §33-35; gap-closure directive Gap B)."""

from codex.coverage.engine import (
    CapabilityCoverage,
    CompletenessLevel,
    NegativeQueryCoverage,
    classify_capability_coverage,
    evaluate_negative_query_coverage,
    is_exhaustive_coverage,
    is_provider_coverage_complete,
)

__all__ = [
    "CapabilityCoverage",
    "CompletenessLevel",
    "NegativeQueryCoverage",
    "classify_capability_coverage",
    "evaluate_negative_query_coverage",
    "is_exhaustive_coverage",
    "is_provider_coverage_complete",
]
