"""A deterministic, test-only fake ``SLMAdapter`` (directive D8 Phase 7:
no real SLM ships with D8). Never used in production code."""

from __future__ import annotations

from collections.abc import Sequence

from codex.coverage.engine import CompletenessLevel
from codex.query_understanding.models import Intent, TemporalDimension
from codex.query_understanding.slm import SLMInterpretation
from codex.query_understanding.tier0 import Tier0Candidate


class FakeSLMAdapter:
    """Returns a pre-configured ``SLMInterpretation`` regardless of input,
    or one derived from a caller-supplied function -- deliberately
    trivial, proving only that the engine correctly *uses* whatever an
    ``SLMAdapter`` returns, never that a real interpretation model
    exists."""

    def __init__(self, interpretation: SLMInterpretation) -> None:
        self._interpretation = interpretation
        self.calls: list[str] = []

    def interpret(
        self, query_text: str, tier0_candidates: Sequence[Tier0Candidate]
    ) -> SLMInterpretation:
        self.calls.append(query_text)
        return self._interpretation


def make_interpretation(
    *,
    intent: Intent = Intent.FIND_CALLERS,
    targets: list[str] | None = None,
    confidence: float = 0.9,
    ambiguity: float = 0.1,
    completeness_requirement: CompletenessLevel = CompletenessLevel.LOW,
    temporal_dimension: TemporalDimension = TemporalDimension.NONE,
    reasoning_requirement: float = 0.0,
) -> SLMInterpretation:
    return SLMInterpretation(
        intent=intent,
        targets=targets or [],
        confidence=confidence,
        ambiguity=ambiguity,
        completeness_requirement=completeness_requirement,
        temporal_dimension=temporal_dimension,
        reasoning_requirement=reasoning_requirement,
    )
