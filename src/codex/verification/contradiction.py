"""Contradiction Handling (TAD §49; directive D10.5).

TAD §49: "A contradicted claim SHALL NOT be rewritten through
speculative reasoning. Instead: CONTRADICTED -> REMOVE CLAIM ->
RE-SYNTHESIZE." Mechanically separate from normal support (TAD §46
keeps "entailment" and "contradiction detection" as distinct pipeline
stages, D10.3/D10.4): this module only ever *removes* claims and
generates *deterministic* feedback text -- it never asks the LLM to
justify, explain away, or preserve a contradicted claim (directive
D10.5 item 4). The LLM is never authoritative over contradiction.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel

from codex.verification.engine import ClaimVerification, is_significantly_contradicted

_FEEDBACK_HEADER: Final[str] = (
    "The following claims are contradicted by deterministic evidence and "
    "must be removed. Do not justify, rewrite, or explain away the "
    "contradiction -- omit these claims entirely and, if needed, adjust "
    "the explanation to no longer depend on them."
)


class ContradictionHandlingResult(BaseModel):
    """`retained`/`removed` partition `verifications` exactly -- every
    input `ClaimVerification` appears in exactly one list."""

    retained: list[ClaimVerification]
    removed: list[ClaimVerification]
    feedback: str | None = None
    """Deterministic re-synthesis instruction text (directive D10.5
    item 2-4). `None` when nothing was removed -- no re-synthesis
    request is needed."""

    @property
    def has_contradictions(self) -> bool:
        return bool(self.removed)


def _describe_claim(verification: ClaimVerification) -> str:
    claim = verification.claim
    return f"- REMOVE: {claim.subject} {claim.predicate} {claim.object} (contradicted by evidence)"


def _build_feedback(removed: list[ClaimVerification]) -> str:
    lines = [_FEEDBACK_HEADER, *(_describe_claim(v) for v in removed)]
    return "\n".join(lines)


def handle_contradictions(verifications: list[ClaimVerification]) -> ContradictionHandlingResult:
    """TAD §49's mechanical process, step 1-4: partition into
    retained/removed by `is_significantly_contradicted` (D10.4's own
    guard, driven entirely by D10 Decision 1's thresholds -- no new
    contradiction rule invented here), and build deterministic
    re-synthesis feedback for whatever was removed.

    Consuming the shared re-synthesis budget and re-verifying the
    result (TAD §49 step 5-6) is the Re-synthesis Controller's job
    (D10.7), which calls this function as one step of its loop.
    """
    removed = [v for v in verifications if is_significantly_contradicted(v)]
    retained = [v for v in verifications if not is_significantly_contradicted(v)]
    feedback = _build_feedback(removed) if removed else None
    return ContradictionHandlingResult(retained=retained, removed=removed, feedback=feedback)


__all__ = ["ContradictionHandlingResult", "handle_contradictions"]
