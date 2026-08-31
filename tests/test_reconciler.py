"""Behavioral tests for Evidence Reconciliation (post-D7 directive Phase C).

Uses handcrafted `Evidence` records throughout (never real provider
output) specifically to exercise DISPUTED/CONTRADICTED/UNSUPPORTED --
directive Phase C's own pre-audit finding (see the module docstring of
`codex.reconciliation.reconciler`) is that no real Git/SCIP/CodeQL
evidence combination can produce "contradicting" evidence today, since
the ontology has no negation mechanism. These tests prove the
*algorithm* is correct and TAD §38-faithful; they do not claim any of
this is reachable with the current three real providers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from codex.evidence.model import Evidence, EvidenceStatus
from codex.ontology.relationships import RelationshipType
from codex.reconciliation.reconciler import reconcile_relationship

NOW = datetime(2026, 8, 31, tzinfo=UTC)
SUBJECT = "codex:subject"
OBJECT = "codex:object"
KNOWN = frozenset({SUBJECT, OBJECT})


def make_evidence(
    *,
    evidence_id: str,
    provider: str = "provider_a",
    confidence: float = 0.9,
    independence_group: str | None = None,
    freshness: datetime = NOW,
    predicate: RelationshipType = RelationshipType.CALLS,
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        provider=provider,
        provider_version="1.0.0",
        snapshot_id="rev1",
        source_revision="rev1",
        subject=SUBJECT,
        predicate=predicate,
        object=OBJECT,
        confidence=confidence,
        freshness=freshness,
        independence_group=independence_group,
    )


# --- Missing target -> UNRESOLVED, not contradiction (directive §19-20) -----


def test_missing_subject_entity_is_unresolved() -> None:
    evidence = make_evidence(evidence_id="e1")
    result = reconcile_relationship(
        "codex:missing",
        RelationshipType.CALLS,
        OBJECT,
        supporting=[evidence],
        known_entity_ids=KNOWN,
        now=NOW,
    )
    assert result.status is EvidenceStatus.UNRESOLVED
    assert result.confidence == 0.0


def test_missing_object_entity_is_unresolved() -> None:
    evidence = make_evidence(evidence_id="e1")
    result = reconcile_relationship(
        SUBJECT,
        RelationshipType.CALLS,
        "codex:missing",
        supporting=[evidence],
        known_entity_ids=KNOWN,
        now=NOW,
    )
    assert result.status is EvidenceStatus.UNRESOLVED


def test_no_evidence_at_all_is_unresolved() -> None:
    result = reconcile_relationship(
        SUBJECT,
        RelationshipType.CALLS,
        OBJECT,
        supporting=[],
        known_entity_ids=KNOWN,
        now=NOW,
    )
    assert result.status is EvidenceStatus.UNRESOLVED
    assert result.confidence == 0.0


# --- Single provider support --------------------------------------------


def test_single_high_confidence_provider_is_supported() -> None:
    evidence = make_evidence(evidence_id="e1", confidence=0.95)
    result = reconcile_relationship(
        SUBJECT,
        RelationshipType.CALLS,
        OBJECT,
        supporting=[evidence],
        known_entity_ids=KNOWN,
        now=NOW,
    )
    assert result.status is EvidenceStatus.SUPPORTED
    assert result.confidence > 0.5
    assert result.supporting_evidence_ids == ["e1"]


def test_single_low_confidence_provider_is_weakly_supported() -> None:
    evidence = make_evidence(evidence_id="e1", confidence=0.2, provider="p", independence_group="p")
    result = reconcile_relationship(
        SUBJECT,
        RelationshipType.CALLS,
        OBJECT,
        supporting=[evidence],
        provider_authority={"p": 1.0},
        known_entity_ids=KNOWN,
        now=NOW,
    )
    assert result.status is EvidenceStatus.WEAKLY_SUPPORTED


# --- Multiple independent vs correlated providers (directive §16, §22) ------


def test_multiple_independent_providers_increase_confidence() -> None:
    single = reconcile_relationship(
        SUBJECT,
        RelationshipType.CALLS,
        OBJECT,
        supporting=[
            make_evidence(evidence_id="e1", provider="a", confidence=0.4, independence_group="a")
        ],
        known_entity_ids=KNOWN,
        now=NOW,
    )
    combined = reconcile_relationship(
        SUBJECT,
        RelationshipType.CALLS,
        OBJECT,
        supporting=[
            make_evidence(evidence_id="e1", provider="a", confidence=0.4, independence_group="a"),
            make_evidence(evidence_id="e2", provider="b", confidence=0.4, independence_group="b"),
        ],
        known_entity_ids=KNOWN,
        now=NOW,
    )
    assert combined.confidence > single.confidence


def test_correlated_evidence_same_group_does_not_stack() -> None:
    """Two records in the SAME independence group must not compound --
    confidence should equal the single-highest-weight record's own
    contribution, not the two combined as if independent."""
    same_group_twice = reconcile_relationship(
        SUBJECT,
        RelationshipType.CALLS,
        OBJECT,
        supporting=[
            make_evidence(
                evidence_id="e1", provider="a", confidence=0.4, independence_group="shared"
            ),
            make_evidence(
                evidence_id="e2", provider="a", confidence=0.4, independence_group="shared"
            ),
        ],
        known_entity_ids=KNOWN,
        now=NOW,
    )
    single = reconcile_relationship(
        SUBJECT,
        RelationshipType.CALLS,
        OBJECT,
        supporting=[
            make_evidence(
                evidence_id="e1", provider="a", confidence=0.4, independence_group="shared"
            )
        ],
        known_entity_ids=KNOWN,
        now=NOW,
    )
    assert same_group_twice.confidence == single.confidence


def test_omitted_independence_group_defaults_non_independent_within_provider() -> None:
    """TAD §16: two records from the same provider with no explicit group
    share the provider-family default group -- non-independent."""
    two_from_same_provider = reconcile_relationship(
        SUBJECT,
        RelationshipType.CALLS,
        OBJECT,
        supporting=[
            make_evidence(evidence_id="e1", provider="a", confidence=0.4),
            make_evidence(evidence_id="e2", provider="a", confidence=0.4),
        ],
        known_entity_ids=KNOWN,
        now=NOW,
    )
    one_from_same_provider = reconcile_relationship(
        SUBJECT,
        RelationshipType.CALLS,
        OBJECT,
        supporting=[make_evidence(evidence_id="e1", provider="a", confidence=0.4)],
        known_entity_ids=KNOWN,
        now=NOW,
    )
    assert two_from_same_provider.confidence == one_from_same_provider.confidence


def test_omitted_independence_group_is_independent_across_providers() -> None:
    """Two different providers with no explicit group get DIFFERENT
    provider-family default groups -- independent, per TAD §16's own
    `independence_group = provider_default_family` default (confirmed by
    reading TAD §16 directly, not assumed)."""
    two_providers = reconcile_relationship(
        SUBJECT,
        RelationshipType.CALLS,
        OBJECT,
        supporting=[
            make_evidence(evidence_id="e1", provider="a", confidence=0.4),
            make_evidence(evidence_id="e2", provider="b", confidence=0.4),
        ],
        known_entity_ids=KNOWN,
        now=NOW,
    )
    one_provider = reconcile_relationship(
        SUBJECT,
        RelationshipType.CALLS,
        OBJECT,
        supporting=[make_evidence(evidence_id="e1", provider="a", confidence=0.4)],
        known_entity_ids=KNOWN,
        now=NOW,
    )
    assert two_providers.confidence > one_provider.confidence


# --- Support + contradiction (handcrafted; directive §19, §22) --------------


def test_support_and_contradiction_is_disputed() -> None:
    supporting = [
        make_evidence(evidence_id="e1", provider="a", confidence=0.7, independence_group="a")
    ]
    contradicting = [
        make_evidence(evidence_id="e2", provider="b", confidence=0.3, independence_group="b")
    ]
    result = reconcile_relationship(
        SUBJECT,
        RelationshipType.CALLS,
        OBJECT,
        supporting=supporting,
        contradicting=contradicting,
        known_entity_ids=KNOWN,
        now=NOW,
    )
    assert result.status is EvidenceStatus.DISPUTED
    assert 0.0 < result.contradiction_score < 1.0
    assert result.contradicting_evidence_ids == ["e2"]


def test_dominant_contradiction_is_contradicted() -> None:
    supporting = [
        make_evidence(evidence_id="e1", provider="a", confidence=0.2, independence_group="a")
    ]
    contradicting = [
        make_evidence(evidence_id="e2", provider="b", confidence=0.9, independence_group="b")
    ]
    result = reconcile_relationship(
        SUBJECT,
        RelationshipType.CALLS,
        OBJECT,
        supporting=supporting,
        contradicting=contradicting,
        known_entity_ids=KNOWN,
        now=NOW,
    )
    assert result.status is EvidenceStatus.CONTRADICTED


def test_contradicting_only_is_unsupported() -> None:
    contradicting = [make_evidence(evidence_id="e1", provider="a", confidence=0.8)]
    result = reconcile_relationship(
        SUBJECT,
        RelationshipType.CALLS,
        OBJECT,
        supporting=[],
        contradicting=contradicting,
        known_entity_ids=KNOWN,
        now=NOW,
    )
    assert result.status is EvidenceStatus.UNSUPPORTED


# --- Contradiction score formula (TAD §38, exact) ----------------------------


def test_contradiction_score_matches_tad_38_formula() -> None:
    """contradiction_score = sum(contradict_weight) / (sum(support_weight)
    + sum(contradict_weight)), weight = evidence_confidence x
    provider_authority -- verified against the literal arithmetic, not
    just a qualitative direction."""
    supporting = [
        make_evidence(evidence_id="e1", provider="a", confidence=0.8, independence_group="a")
    ]
    contradicting = [
        make_evidence(evidence_id="e2", provider="b", confidence=0.4, independence_group="b")
    ]
    result = reconcile_relationship(
        SUBJECT,
        RelationshipType.CALLS,
        OBJECT,
        supporting=supporting,
        contradicting=contradicting,
        provider_authority={"a": 1.0, "b": 1.0},
        known_entity_ids=KNOWN,
        now=NOW,
    )
    # freshness is at now(), so staleness == 1.0; weight == confidence x authority
    expected = 0.4 / (0.8 + 0.4)
    assert abs(result.contradiction_score - expected) < 1e-9


# --- Provider failure / staleness / partial coverage (directive §20, §22) --


def test_provider_failure_never_produces_evidence_so_never_contradicts() -> None:
    """A failed provider contributes zero Evidence records (D4's own
    failure isolation) -- reconciling with only the successful provider's
    evidence must never become a contradiction merely because another
    provider failed to run."""
    result = reconcile_relationship(
        SUBJECT,
        RelationshipType.CALLS,
        OBJECT,
        supporting=[make_evidence(evidence_id="e1", confidence=0.9)],
        contradicting=[],
        known_entity_ids=KNOWN,
        now=NOW,
    )
    assert result.status is EvidenceStatus.SUPPORTED
    assert result.contradiction_score == 0.0


def test_stale_evidence_reduces_confidence() -> None:
    fresh = reconcile_relationship(
        SUBJECT,
        RelationshipType.CALLS,
        OBJECT,
        supporting=[make_evidence(evidence_id="e1", confidence=0.9, freshness=NOW)],
        known_entity_ids=KNOWN,
        now=NOW,
    )
    stale = reconcile_relationship(
        SUBJECT,
        RelationshipType.CALLS,
        OBJECT,
        supporting=[
            make_evidence(evidence_id="e1", confidence=0.9, freshness=NOW - timedelta(days=30))
        ],
        known_entity_ids=KNOWN,
        now=NOW,
    )
    assert stale.confidence < fresh.confidence


# --- Provenance: raw evidence never deleted ---------------------------------


def test_supporting_and_contradicting_ids_both_preserved() -> None:
    supporting = [make_evidence(evidence_id="s1"), make_evidence(evidence_id="s2", provider="b")]
    contradicting = [make_evidence(evidence_id="c1", provider="c")]
    result = reconcile_relationship(
        SUBJECT,
        RelationshipType.CALLS,
        OBJECT,
        supporting=supporting,
        contradicting=contradicting,
        known_entity_ids=KNOWN,
        now=NOW,
    )
    assert set(result.supporting_evidence_ids) == {"s1", "s2"}
    assert set(result.contradicting_evidence_ids) == {"c1"}
