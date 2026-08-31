"""Final Answer / Abstention Policy (HLRD §43, TAD §50; directive D10.8).

**Critical rule (directive D10.8, restating the project's own binding
principle): "NO EVIDENCE -> NO REPOSITORY FACTUAL ASSERTION."** The
system may say it could not establish something; it must never convert
an absence of evidence into an unsupported repository claim.

HLRD §43's four-way policy (`FULLY_VERIFIED -> strong answer`,
`PARTIALLY_VERIFIED -> qualified answer`, `UNVERIFIED -> qualify/
abstain`, `CONTRADICTED -> explain conflict/downgrade/possibly
abstain`) is implemented via TAD §50's own three-bucket routing view
(`to_routing_bucket`, D10.6) -- TAD is the narrower technical
architecture HLRD's qualitative policy operationalizes into (same
precedent as every other HLRD/TAD pairing in this project). `ABSTAIN`
is reachable two ways: TAD's own routing table (only `REJECTED`, used
here for a failed generation), and one explicit override this module
adds -- an answer with **no verified claims and nothing removed
either** (nothing to assert, nothing to explain) always abstains,
since `classify_answer`'s natural INCONCLUSIVE/DISPUTED outcomes both
route to the `QUALIFIED` bucket (TAD §50's own table), which would
otherwise present an empty "qualified answer" asserting nothing.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from codex.coverage.engine import NegativeQueryCoverage
from codex.llm.schema import Claim
from codex.verification.resynthesis import ResynthesisOutcome, ResynthesisResult
from codex.verification.state import (
    VerificationStatus,
    classify_answer,
    classify_claim,
    to_routing_bucket,
)


class AnswerDecision(StrEnum):
    STRONG_ANSWER = "STRONG_ANSWER"
    """TAD §50 routing bucket `VERIFIED` -- HLRD §43's "strong answer"."""

    QUALIFIED_ANSWER = "QUALIFIED_ANSWER"
    """TAD §50 routing bucket `QUALIFIED` -- HLRD §43's "qualified
    answer" / "qualify" / "explain conflict, downgrade confidence"."""

    ABSTAIN = "ABSTAIN"
    """TAD §50 routing bucket `ABSTAIN` (a `REJECTED` verification
    status), or this module's explicit no-evidence override -- HLRD
    §43's "abstain" / "possibly abstain"."""


class FinalAnswer(BaseModel):
    decision: AnswerDecision
    verification_status: VerificationStatus
    text: str
    supported_claims: list[Claim] = Field(default_factory=list)
    """Only claims this answer actually asserts -- `VERIFIED` retained
    claims. A `QUALIFIED_ANSWER`/`ABSTAIN` never lists an unverified or
    removed claim here."""

    limitations: list[str] = Field(default_factory=list)


def _negative_query_text(negative_query_result: NegativeQueryCoverage) -> str:
    if negative_query_result is NegativeQueryCoverage.NO_EVIDENCE_FOUND:
        return (
            "No matching relationship was found. Coverage was complete, "
            "so this absence is a supported conclusion."
        )
    return "Evidence coverage was insufficient to determine whether a matching relationship exists."


def _abstain(
    verification_status: VerificationStatus, text: str, limitations: list[str]
) -> FinalAnswer:
    return FinalAnswer(
        decision=AnswerDecision.ABSTAIN,
        verification_status=verification_status,
        text=text,
        supported_claims=[],
        limitations=limitations,
    )


def build_final_answer(
    result: ResynthesisResult,
    *,
    negative_query_result: NegativeQueryCoverage | None = None,
) -> FinalAnswer:
    """TAD §46's "answer decision" step.

    `negative_query_result` is D9's own already-computed
    `RetrievalPlan.negative_query_result` (TAD §34, `codex.coverage`):
    an absence conclusion is only ever asserted when it is
    `NO_EVIDENCE_FOUND` (coverage proven complete) -- `INCONCLUSIVE`
    coverage never becomes "nothing was found," only "insufficient
    evidence" (directive D10.8 item 3).
    """
    if result.outcome is ResynthesisOutcome.GENERATION_FAILED:
        return _abstain(
            VerificationStatus.REJECTED,
            "Unable to produce a verifiable, schema-valid answer after the available attempts.",
            [result.failure_reason or "generation failed"],
        )

    claim_states = [classify_claim(v) for v in result.retained]
    verified = [
        v
        for v, state in zip(result.retained, claim_states, strict=True)
        if state is VerificationStatus.VERIFIED
    ]
    any_removed = bool(result.removed)

    limitations: list[str] = []
    if result.removed:
        limitations.append(
            f"{len(result.removed)} claim(s) were removed: contradicted by deterministic evidence"
        )
    for verification, state in zip(result.retained, claim_states, strict=True):
        if state is not VerificationStatus.VERIFIED:
            claim = verification.claim
            limitations.append(
                f"Claim '{claim.subject} {claim.predicate} {claim.object}' "
                f"could not be verified ({state.value})"
            )
    if negative_query_result is not None:
        limitations.append(_negative_query_text(negative_query_result))

    # Negative-query safety (directive D10.8 item 3): only NO_EVIDENCE_FOUND
    # (coverage proven complete) may assert an absence conclusion.
    if negative_query_result is NegativeQueryCoverage.NO_EVIDENCE_FOUND and not verified:
        return FinalAnswer(
            decision=AnswerDecision.STRONG_ANSWER,
            verification_status=VerificationStatus.VERIFIED,
            text=_negative_query_text(negative_query_result),
            supported_claims=[],
            limitations=limitations,
        )
    if negative_query_result is NegativeQueryCoverage.INCONCLUSIVE and not verified:
        return _abstain(
            VerificationStatus.INCONCLUSIVE,
            _negative_query_text(negative_query_result),
            limitations,
        )

    # The no-evidence-no-assertion override: nothing verified AND nothing
    # was even removed -- there is genuinely nothing to say.
    if not verified and not any_removed:
        return _abstain(
            classify_answer([], any_removed_for_contradiction=False),
            "No sufficient repository evidence was found to support an answer.",
            limitations,
        )

    status = classify_answer(claim_states, any_removed_for_contradiction=any_removed)
    bucket = to_routing_bucket(status)
    decision = (
        AnswerDecision.STRONG_ANSWER if bucket == "VERIFIED" else AnswerDecision.QUALIFIED_ANSWER
    )
    # `bucket == "ABSTAIN"` is unreachable here: classify_answer() never
    # returns REJECTED (D10.6's own contract) -- every other status maps
    # to VERIFIED or QUALIFIED, never ABSTAIN, in TAD §50's own table.

    explanation = result.final_answer.explanation if result.final_answer is not None else ""
    text = (
        explanation
        if decision is AnswerDecision.STRONG_ANSWER
        else (explanation or "A qualified answer; see limitations.")
    )

    return FinalAnswer(
        decision=decision,
        verification_status=status,
        text=text,
        supported_claims=[v.claim for v in verified],
        limitations=limitations,
    )


__all__ = ["AnswerDecision", "FinalAnswer", "build_final_answer"]
