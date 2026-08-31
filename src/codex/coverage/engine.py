"""The Coverage / Completeness Engine (TAD §33-35; HLRD; gap-closure directive Gap B).

Distinguishes "no evidence found" from "insufficient evidence to
establish absence" — the correctness boundary the gap-closure directive
calls out most explicitly (its Phase 11 duplicates TAD §34 almost
verbatim). Built entirely as a pure, deterministic function of
`codex.ingestion.models.IngestionResult` — no new signal is invented;
every distinction below already exists on `ProviderRunOutcome`/
`EvidenceCohort` (D1-D4), this module only classifies it.

**What this module implements, and why (gap-closure directive Gap B):**

TAD §34 ("Negative Query Planning") gives a fully deterministic,
already-implementable rule: a negative-query result may only be treated
as `NO_EVIDENCE_FOUND` when "complete scope + successful required
capability + no failed capability + no PARTIAL cohort" all hold;
otherwise it is `INCONCLUSIVE`, never `FALSE`. This is a boolean AND-gate
over signals `EvidenceCohort`/`ProviderRunOutcome` already carry — no
missing formula, fully implemented here as `evaluate_negative_query_
coverage()`.

The six capability/provider distinctions the directive's Gap B
explicitly names (capability unavailable / failed / partially completed
/ completed successfully with zero results / provider coverage
incomplete / provider coverage complete) map directly onto the same
existing fields — implemented as `classify_capability_coverage()` and
`is_provider_coverage_complete()`.

**What this module deliberately does NOT implement, and why:**

TAD §33 ("Completeness Model") states `LOW >= 50%`, `MEDIUM >= 75%`,
`HIGH >= 90%`, `EXHAUSTIVE == 100% + complete repository coverage` --
but **never defines the coverage metric itself**: 50%/75%/90% of *what*
quantity (requested capabilities that succeeded? repository files
touched? relevant entities discovered? something else)? No formula,
worked example, or unit is given anywhere in HLRD or TAD. Per the
gap-closure directive's own explicit instruction ("if a required
formula is genuinely missing, STOP and document the exact missing
semantic rather than inventing one") and Rule 0.2 ("no silent
architectural decisions"), this module does **not** compute a LOW/
MEDIUM/HIGH percentage — doing so would require inventing a metric TAD
never specifies. `CompletenessLevel` below is still defined (the four
names TAD §33 uses, so downstream code has a shared vocabulary to refer
to), but only `EXHAUSTIVE` has an implemented, deterministic check here
(`is_exhaustive_coverage()`) -- **it alone** needs no percentage metric:
"100% + complete repository coverage" reduces to the same "every
requested capability, on every provider that declares it, completed
fully" AND-gate `evaluate_negative_query_coverage()` already
implements, just applied without needing a specific missing target.
LOW/MEDIUM/HIGH remain an explicit, documented, open gap -- see
`docs/architecture-conformance-audit.md`'s gap table.
"""

from __future__ import annotations

from enum import StrEnum

from codex.evidence.model import CoverageStatus
from codex.ingestion.models import IngestionResult, ProviderRunOutcome, ProviderRunStatus
from codex.provider.capability import Capability


class CompletenessLevel(StrEnum):
    """TAD §33's four named levels. Only `EXHAUSTIVE` has an implemented
    quantitative check here (`is_exhaustive_coverage()`) -- LOW/MEDIUM/
    HIGH's underlying coverage metric is not defined anywhere in HLRD/TAD
    (see this module's docstring) and is not computed by this engine."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    EXHAUSTIVE = "EXHAUSTIVE"


class CapabilityCoverage(StrEnum):
    """Per-capability classification (gap-closure directive Gap B's six
    distinctions, items 1-4; items 5-6 are `is_provider_coverage_complete`)."""

    NOT_SUPPORTED = "NOT_SUPPORTED"
    """No registered provider declares this capability at all."""

    UNAVAILABLE = "UNAVAILABLE"
    """Every provider declaring it was never attempted this run (SKIPPED --
    the Capability Registry classified it UNAVAILABLE/INELIGIBLE/FAILED)."""

    FAILED = "FAILED"
    """At least one provider attempted it and it ended up in that
    provider's `EvidenceCohort.failed_capabilities`, or the provider
    itself FAILED outright, and no other provider fully completed it."""

    PARTIAL = "PARTIAL"
    """At least one provider attempted it and its cohort's
    `coverage_status` was `PARTIAL` for this capability (or it appears
    in `partial_capabilities`), and no provider fully completed it."""

    EMPTY_SUCCESS = "EMPTY_SUCCESS"
    """At least one provider completed it successfully (`coverage_status
    FULL`, in `successful_capabilities`) but zero entities/evidence were
    committed by any such provider run -- "ran to completion and found
    nothing," distinct from `FAILED`/`PARTIAL`/`UNAVAILABLE` (TAD §17's
    own "executed successfully but returned no result" distinction)."""

    COMPLETE = "COMPLETE"
    """At least one provider completed it successfully and committed at
    least one entity or evidence record."""


_PRIORITY: dict[CapabilityCoverage, int] = {
    CapabilityCoverage.COMPLETE: 5,
    CapabilityCoverage.EMPTY_SUCCESS: 4,
    CapabilityCoverage.PARTIAL: 3,
    CapabilityCoverage.FAILED: 2,
    CapabilityCoverage.UNAVAILABLE: 1,
    CapabilityCoverage.NOT_SUPPORTED: 0,
}
"""Best-evidence-wins ordering when more than one provider declares the
same capability (none of Git/SCIP/CodeQL overlap today, but the engine
must not assume exactly one provider per capability -- D7, if ever
un-deferred, could add a second)."""


def _outcome_capability_status(
    outcome: ProviderRunOutcome, capability: Capability
) -> CapabilityCoverage | None:
    """This one provider's contribution to ``capability``'s classification,
    or ``None`` if this provider was never asked about it at all."""
    if capability.value not in outcome.capabilities_requested:
        return None
    if outcome.status is ProviderRunStatus.SKIPPED:
        return CapabilityCoverage.UNAVAILABLE
    if outcome.status is ProviderRunStatus.FAILED:
        return CapabilityCoverage.FAILED

    cohort = outcome.cohort
    if cohort is None:  # defensive: COMMITTED always carries a cohort (D4 invariant)
        return CapabilityCoverage.FAILED
    if capability.value in cohort.failed_capabilities:
        return CapabilityCoverage.FAILED
    if capability.value in cohort.partial_capabilities:
        return CapabilityCoverage.PARTIAL
    if capability.value not in cohort.successful_capabilities:
        return CapabilityCoverage.UNAVAILABLE
    if outcome.entities_upserted == 0 and outcome.evidence_upserted == 0:
        return CapabilityCoverage.EMPTY_SUCCESS
    return CapabilityCoverage.COMPLETE


def classify_capability_coverage(
    result: IngestionResult, capability: Capability
) -> CapabilityCoverage:
    """Best (highest-priority) classification for ``capability`` across
    every provider outcome in ``result`` -- deterministic, no new signal
    invented, a pure read of what D1-D4 already recorded."""
    statuses = [
        status
        for outcome in result.provider_outcomes
        if (status := _outcome_capability_status(outcome, capability)) is not None
    ]
    if not statuses:
        return CapabilityCoverage.NOT_SUPPORTED
    return max(statuses, key=lambda s: _PRIORITY[s])


def is_provider_coverage_complete(outcome: ProviderRunOutcome) -> bool:
    """Gap-closure directive Gap B, item 6 ("provider coverage complete")
    vs. item 5 ("provider coverage incomplete"): ``True`` only for a
    COMMITTED provider whose cohort reports `coverage_status == FULL` --
    no capability it was asked about failed or partially completed."""
    return (
        outcome.status is ProviderRunStatus.COMMITTED
        and outcome.cohort is not None
        and outcome.cohort.coverage_status is CoverageStatus.FULL
    )


class NegativeQueryCoverage(StrEnum):
    """TAD §34 / gap-closure directive Phase 11's two allowed outcomes for
    a negative query. `FALSE` is never a member -- it is not a valid
    negative-query conclusion in this engine (per TAD §34's explicit
    "not: FALSE")."""

    NO_EVIDENCE_FOUND = "NO_EVIDENCE_FOUND"
    INCONCLUSIVE = "INCONCLUSIVE"


def evaluate_negative_query_coverage(
    result: IngestionResult, required_capability: Capability
) -> NegativeQueryCoverage:
    """TAD §34's exact rule: only when the required capability's coverage
    is `COMPLETE` or `EMPTY_SUCCESS` (i.e. it ran to completion --
    "successful required capability + no failed capability + no PARTIAL
    cohort") may an absence of evidence be reported as
    `NO_EVIDENCE_FOUND`. Every other classification (`PARTIAL`, `FAILED`,
    `UNAVAILABLE`, `NOT_SUPPORTED`) means the search itself did not fully
    run, so absence of evidence proves nothing -- `INCONCLUSIVE`.

    Callers are responsible for having already confirmed no evidence for
    the specific relationship in question was found; this function only
    answers whether that absence is *trustworthy*, not whether evidence
    exists (that is Reconciliation's/the caller's job, not Coverage's).
    """
    coverage = classify_capability_coverage(result, required_capability)
    if coverage in (CapabilityCoverage.COMPLETE, CapabilityCoverage.EMPTY_SUCCESS):
        return NegativeQueryCoverage.NO_EVIDENCE_FOUND
    return NegativeQueryCoverage.INCONCLUSIVE


def is_exhaustive_coverage(result: IngestionResult, capabilities: frozenset[Capability]) -> bool:
    """TAD §33's `EXHAUSTIVE` level: "100% + complete repository
    coverage" -- the one `CompletenessLevel` this engine can check without
    an undefined percentage metric (see module docstring), because it
    reduces to "every one of ``capabilities`` is COMPLETE or
    EMPTY_SUCCESS, with no PARTIAL/FAILED/UNAVAILABLE/NOT_SUPPORTED
    among them" -- the same AND-gate `evaluate_negative_query_coverage`
    already implements, generalized across a capability set instead of
    one required capability."""
    return all(
        classify_capability_coverage(result, capability)
        in (CapabilityCoverage.COMPLETE, CapabilityCoverage.EMPTY_SUCCESS)
        for capability in capabilities
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
