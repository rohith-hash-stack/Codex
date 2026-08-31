"""The Query Understanding Engine (TAD §22-28; HLRD §24-29; directive D8).

    User Query -> Normalization -> Deterministic Tier-0 Detection
        -> (high confidence -> QueryContract)
        -> (ambiguous/low confidence -> SLM -> calibrated interpretation -> QueryContract)

This module is the **only** entry point (`understand_query`) and
performs **no** graph retrieval, provider selection, evidence ranking,
graph traversal, LLM answer generation, verification, or repository
mutation (directive Phase 4) — confirmed structurally, not just by
convention: this package imports no *behavioral* module from
`codex.provider` (only the stateless `Capability` vocabulary enum, for
`QueryContract.required_evidence` — never `codex.provider.contract`, an
adapter, or the extraction machinery), and nothing at all from
`codex.registry`, `codex.graph`, `codex.ingestion`, `codex.resolution`,
or `codex.reconciliation` (see `tests/test_qu_boundaries.py`'s import-
graph assertion).

User query text is treated strictly as **data** throughout — Tier-0 only
ever runs fixed regex patterns over it and extracts substrings into
`targets`; nothing in this module interprets query text as instructions
that could change a `QueryContract`'s budgets, intent, or any other
field outside of what Tier-0/the SLM's own structured, validated output
explicitly derives (directive Phase 10).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from codex.provider.capability import Capability
from codex.query_understanding.complexity import compute_complexity
from codex.query_understanding.models import ComplexityFactors, Intent, QueryContract
from codex.query_understanding.session import SessionContext
from codex.query_understanding.slm import SLMAdapter, SLMInterpretation
from codex.query_understanding.tier0 import Tier0Candidate, detect

_REQUIRED_EVIDENCE: dict[Intent, frozenset[Capability]] = {
    Intent.FIND_CALLERS: frozenset({Capability.CALL_RELATIONSHIP, Capability.SYMBOL_REFERENCE}),
    Intent.TRACE_EXECUTION: frozenset({Capability.CALL_RELATIONSHIP, Capability.DATA_FLOW}),
    Intent.FIND_IMPLEMENTATIONS: frozenset({Capability.IMPLEMENTATION}),
    Intent.FIND_TESTS: frozenset({Capability.SYMBOL_REFERENCE}),
    Intent.FIND_IMPACT: frozenset(
        {Capability.CALL_RELATIONSHIP, Capability.DEPENDENCY, Capability.DATA_FLOW}
    ),
    Intent.FIND_DEPENDENCIES: frozenset({Capability.DEPENDENCY}),
    Intent.HISTORY_ANALYSIS: frozenset({Capability.HISTORY}),
    Intent.ARCHITECTURE_ANALYSIS: frozenset(
        {Capability.SYMBOL_DEFINITION, Capability.IMPLEMENTATION, Capability.TYPE_RELATIONSHIP}
    ),
    Intent.CODE_LOOKUP: frozenset({Capability.SYMBOL_DEFINITION}),
    Intent.UNKNOWN: frozenset(),
}
"""Which provider `Capability` values would be needed to answer a query
of this intent -- a documented **implementation detail** (directive
Phase 19, category 1), not an HLRD/TAD-specified formula: TAD §24 lists
what the SLM determines (intent, targets, relationships, constraints,
complexity, ambiguity, temporal requirements, completeness) and
`required_evidence` is notably **absent** from that list, confirming it
is derived elsewhere rather than an SLM output -- this mapping is that
derivation, applied uniformly to both the Tier-0 and SLM paths so
`required_evidence` reflects a query's intent regardless of which tier
resolved it. This states what evidence *would be needed*, not what is
currently *available* -- availability is the Coverage Engine's job
(`codex.coverage`), not Query Understanding's."""

DETERMINISTIC_THRESHOLD: float = 0.95
"""TAD §23: ">0.95 -> deterministic execution"."""

SLM_DISAMBIGUATION_FLOOR: float = 0.70
"""TAD §23: "0.70-0.95 -> SLM disambiguation"; below this, "SLM" is
invoked "without a deterministic prior intent" (TAD §23's own phrase)."""

SLM_EXECUTE_THRESHOLD: float = 0.85
"""TAD §25: ">0.85 -> execute"."""

SLM_ESCALATE_FLOOR: float = 0.50
"""TAD §25: "<0.50 -> escalate to LLM"."""

DEFAULT_TOKEN_BUDGET: int = 4000
"""HLRD's own V1 target ("LLM tokens/query < 4,000") reused as the
default when a caller supplies none -- not invented, cited from the
already-approved V1 performance targets."""

DEFAULT_LATENCY_BUDGET_MS: int = 5000
"""HLRD's own V1 target (p95 latency < 5s) reused as the default."""

_WHITESPACE_RE = re.compile(r"\s+")


class UnderstandingStatus(StrEnum):
    """Honest representation of what `understand_query` produced --
    directive Phase 7: "clearly represent the state rather than
    pretending raw scores are calibrated probabilities" generalizes here
    to "clearly represent the state rather than pretending a contract
    exists when the required downstream capability doesn't."""

    RESOLVED = "RESOLVED"
    """A valid, validated `QueryContract` was produced -- either
    deterministically (Tier-0 > 0.95) or via a configured SLM adapter
    whose calibrated confidence was >= 0.50 (TAD §25's own execute /
    execute-with-clarification bands)."""

    SLM_UNAVAILABLE = "SLM_UNAVAILABLE"
    """Tier-0 alone could not resolve the query (score <= 0.95) and no
    `SLMAdapter` was configured to escalate to -- no real SLM ships with
    D8 (directive Phase 7), so this is the expected outcome for any
    ambiguous query in the current system, not an error."""

    LLM_ESCALATION_REQUIRED = "LLM_ESCALATION_REQUIRED"
    """A configured SLM ran and its own calibrated confidence was
    `< 0.50` (TAD §25: "escalate to LLM") -- no LLM Gateway exists yet
    (correctly out of D8's scope), so this state is surfaced rather than
    silently downgraded to a low-confidence `QueryContract`."""


@dataclass(frozen=True)
class QueryUnderstandingResult:
    status: UnderstandingStatus
    contract: QueryContract | None
    tier0_candidates: tuple[Tier0Candidate, ...]
    detail: str | None = None


def _normalize(query_text: str) -> str:
    return _WHITESPACE_RE.sub(" ", query_text.strip())


def _ambiguity_from_candidates(candidates: list[Tier0Candidate]) -> float:
    """Deterministic ambiguity measure: how close the second-best
    candidate's score is to the best one's (documented calibration
    point -- TAD §26-27 name `ambiguity` as a factor/field but give no
    formula). No second candidate at all means no competing
    interpretation was found, i.e. zero ambiguity."""
    if len(candidates) < 2 or candidates[0].score <= 0.0:
        return 0.0
    return min(candidates[1].score / candidates[0].score, 1.0)


def _contract_from_tier0(
    candidate: Tier0Candidate,
    candidates: list[Tier0Candidate],
    *,
    token_budget: int,
    latency_budget_ms: int,
) -> QueryContract:
    ambiguity = _ambiguity_from_candidates(candidates)
    factors = ComplexityFactors(
        intent_count=min(len({c.intent for c in candidates}) / 5, 1.0),
        target_count=min(len(candidate.targets) / 5, 1.0),
        # Tier-0 never establishes multi-hop depth -- no graph access (directive Phase 4).
        relationship_depth=0.0,
        ambiguity=ambiguity,
        temporal_dimension=0.0,
        # A deterministic structural match needs no explanatory reasoning.
        reasoning_requirement=0.0,
    )
    return QueryContract(
        intent=candidate.intent,
        targets=list(candidate.targets),
        complexity=compute_complexity(factors),
        ambiguity=ambiguity,
        confidence=candidate.score,
        required_evidence=sorted(_REQUIRED_EVIDENCE.get(candidate.intent, frozenset())),
        token_budget=token_budget,
        latency_budget_ms=latency_budget_ms,
    )


def _contract_from_slm(
    interpretation: SLMInterpretation, *, token_budget: int, latency_budget_ms: int
) -> QueryContract:
    factors = ComplexityFactors(
        intent_count=min(1 / 5, 1.0) if interpretation.intent is not Intent.UNKNOWN else 0.0,
        target_count=min(len(interpretation.targets) / 5, 1.0),
        relationship_depth=min(len(interpretation.relationship_types) / 5, 1.0),
        ambiguity=interpretation.ambiguity,
        temporal_dimension=0.0 if interpretation.temporal_dimension.value == "NONE" else 1.0,
        reasoning_requirement=interpretation.reasoning_requirement,
    )
    return QueryContract(
        intent=interpretation.intent,
        targets=list(interpretation.targets),
        relationship_types=list(interpretation.relationship_types),
        constraints=list(interpretation.constraints),
        temporal_dimension=interpretation.temporal_dimension,
        complexity=compute_complexity(factors),
        ambiguity=interpretation.ambiguity,
        confidence=interpretation.confidence,
        completeness_requirement=interpretation.completeness_requirement,
        required_evidence=sorted(_REQUIRED_EVIDENCE.get(interpretation.intent, frozenset())),
        token_budget=token_budget,
        latency_budget_ms=latency_budget_ms,
    )


def understand_query(
    query_text: str,
    *,
    repository_id: str,
    session: SessionContext | None = None,
    slm_adapter: SLMAdapter | None = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    latency_budget_ms: int = DEFAULT_LATENCY_BUDGET_MS,
    now: datetime | None = None,
) -> QueryUnderstandingResult:
    """TAD §22's DTD-02 flow: Deterministic Intent Detector -> SLM ->
    QueryContract. "The SLM is not automatically invoked for every
    query" (TAD §22) -- only when Tier-0's top candidate scores at or
    below `DETERMINISTIC_THRESHOLD`.

    ``repository_id`` is required (never inferred from query text --
    directive Phase 10's data-not-instructions boundary) and, when
    ``session`` is supplied, must match the session's own scope (the
    session itself enforces this on `record()`; this function does not
    silently create cross-repository behavior).
    """
    if not repository_id:
        raise ValueError("repository_id must be non-empty")

    reference_time = now if now is not None else datetime.now(UTC)
    normalized = _normalize(query_text)
    candidates = detect(normalized)
    top = candidates[0] if candidates else None

    if top is not None and top.score > DETERMINISTIC_THRESHOLD:
        contract = _contract_from_tier0(
            top, candidates, token_budget=token_budget, latency_budget_ms=latency_budget_ms
        )
        if session is not None:
            session.record(
                repository_id=repository_id,
                query_text=normalized,
                intent=contract.intent,
                observed_at=reference_time,
            )
        return QueryUnderstandingResult(UnderstandingStatus.RESOLVED, contract, tuple(candidates))

    if slm_adapter is None:
        return QueryUnderstandingResult(
            UnderstandingStatus.SLM_UNAVAILABLE,
            None,
            tuple(candidates),
            detail=(
                f"Tier-0 top score "
                f"{top.score if top is not None else 0.0:.2f} <= {DETERMINISTIC_THRESHOLD}; "
                "no SLMAdapter configured to disambiguate."
            ),
        )

    interpretation = slm_adapter.interpret(normalized, candidates)
    if interpretation.confidence < SLM_ESCALATE_FLOOR:
        return QueryUnderstandingResult(
            UnderstandingStatus.LLM_ESCALATION_REQUIRED,
            None,
            tuple(candidates),
            detail=f"SLM confidence {interpretation.confidence:.2f} < {SLM_ESCALATE_FLOOR}.",
        )

    contract = _contract_from_slm(
        interpretation, token_budget=token_budget, latency_budget_ms=latency_budget_ms
    )
    if session is not None:
        session.record(
            repository_id=repository_id,
            query_text=normalized,
            intent=contract.intent,
            observed_at=reference_time,
        )
    return QueryUnderstandingResult(UnderstandingStatus.RESOLVED, contract, tuple(candidates))


__all__ = [
    "DETERMINISTIC_THRESHOLD",
    "SLM_DISAMBIGUATION_FLOOR",
    "SLM_ESCALATE_FLOOR",
    "SLM_EXECUTE_THRESHOLD",
    "QueryUnderstandingResult",
    "UnderstandingStatus",
    "understand_query",
]
