"""Re-synthesis Controller (TAD §44, §49; directive D10.7, D10 Decision 2).

**One shared re-synthesis budget per query, maximum 1 total** (D10
Decision 2, explicitly approved): malformed/invalid structured output
(TAD §44) and a contradicted claim (TAD §49) consume the *same*
counter, never two independent budgets. Once spent, the loop returns
whatever it has -- no further LLM generation, no recursive/unbounded
retry, no third attempt.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Final

from pydantic import BaseModel, Field

from codex.llm.gateway import GenerationStatus, LLMGateway, LLMRequest
from codex.llm.schema import StructuredAnswer
from codex.planner.mss import EvidencePackage
from codex.verification.contradiction import handle_contradictions
from codex.verification.engine import ClaimVerification, verify_claims

_MALFORMED_FEEDBACK: Final[str] = (
    "Your previous response was not valid JSON matching the required "
    "schema (an object with an 'explanation' string and a 'claims' "
    "array of {subject, predicate, object, claim_type} objects). "
    "Produce a corrected response in exactly that schema."
)

MAX_ATTEMPTS: Final[int] = 2
"""TAD §49's "Maximum V1 re-synthesis: 1" -- one initial generation
plus at most one re-synthesis, never more."""


class ResynthesisOutcome(StrEnum):
    RESOLVED = "RESOLVED"
    """A schema-valid `StructuredAnswer` was obtained (with or without
    contradicted claims having been removed along the way)."""

    GENERATION_FAILED = "GENERATION_FAILED"
    """No schema-valid output was ever obtained, even after the shared
    retry -- there are no claims to verify at all."""


class ResynthesisResult(BaseModel):
    outcome: ResynthesisOutcome
    final_answer: StructuredAnswer | None = None
    """The last schema-valid `StructuredAnswer` obtained, if any --
    `retained`/`removed` describe how its `claims[]` were adjudicated,
    not a re-filtered copy of it (directive D10.8 builds the
    user-facing answer from `retained`, never from this field's raw
    `claims[]` directly)."""

    retained: list[ClaimVerification] = Field(default_factory=list)
    removed: list[ClaimVerification] = Field(default_factory=list)
    """Accumulated across every attempt -- a claim removed on the
    first attempt (if the model repeats it) stays recorded even though
    the second attempt's output superseded the first."""

    resynthesis_used: bool = False
    attempts: int
    failure_reason: str | None = None


def run_verification_loop(
    gateway: LLMGateway,
    request: LLMRequest,
    package: EvidencePackage,
    *,
    provider_authority: Mapping[str, float] | None = None,
    now: datetime | None = None,
) -> ResynthesisResult:
    """TAD §46's full per-query loop: generate -> validate/verify ->
    (if correctable and budget remains) one re-synthesis -> final
    state. Never recurses past `MAX_ATTEMPTS` (directive D10.7: "No
    recursive/unbounded retry loop")."""
    resynthesis_available = True
    current_request = request
    all_removed: list[ClaimVerification] = []
    attempts = 0

    while True:
        attempts += 1
        generation = gateway.generate(current_request)

        if generation.status is not GenerationStatus.OK or generation.answer is None:
            if resynthesis_available:
                resynthesis_available = False
                current_request = current_request.model_copy(
                    update={"feedback": _MALFORMED_FEEDBACK}
                )
                continue
            return ResynthesisResult(
                outcome=ResynthesisOutcome.GENERATION_FAILED,
                final_answer=None,
                retained=[],
                removed=all_removed,
                resynthesis_used=attempts > 1,
                attempts=attempts,
                failure_reason=f"generation_status={generation.status.value}",
            )

        verifications = verify_claims(
            generation.answer.claims, package, provider_authority=provider_authority, now=now
        )
        handling = handle_contradictions(verifications)
        all_removed.extend(handling.removed)

        if handling.has_contradictions and resynthesis_available:
            resynthesis_available = False
            current_request = current_request.model_copy(update={"feedback": handling.feedback})
            continue

        return ResynthesisResult(
            outcome=ResynthesisOutcome.RESOLVED,
            final_answer=generation.answer,
            retained=handling.retained,
            removed=all_removed,
            resynthesis_used=attempts > 1,
            attempts=attempts,
            failure_reason=None,
        )


__all__ = [
    "MAX_ATTEMPTS",
    "ResynthesisOutcome",
    "ResynthesisResult",
    "run_verification_loop",
]
