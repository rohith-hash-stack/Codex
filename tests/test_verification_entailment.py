"""Behavioral tests for the Deterministic Entailment Engine (TAD §47;
directive D10.3)."""

from __future__ import annotations

from codex.evidence.model import CanonicalRelationship
from codex.llm.schema import Claim, ClaimType
from codex.ontology.relationships import RelationshipType
from codex.verification.entailment import (
    EntailmentStatus,
    direct_edge_match,
    entail_claim,
    find_path,
)
from llm_fixtures import make_evidence_package


def _rel(subject: str, predicate: RelationshipType, object_: str) -> CanonicalRelationship:
    return CanonicalRelationship(subject=subject, predicate=predicate, object=object_)


def _claim(
    subject: str, predicate: object, object_: str, claim_type: ClaimType = ClaimType.FACT
) -> Claim:
    return Claim(subject=subject, predicate=predicate, object=object_, claim_type=claim_type)


# --- direct edge matching ----------------------------------------------------


def test_direct_edge_match_exact_calls() -> None:
    package = make_evidence_package(relationships=[_rel("A", RelationshipType.CALLS, "B")])
    claim = _claim("A", "CALLS", "B")
    result = entail_claim(claim, package)
    assert result.status is EntailmentStatus.SUPPORTED
    assert result.matched_relationship is not None
    assert result.matched_relationship.key == ("A", RelationshipType.CALLS, "B")


def test_direct_edge_match_wrong_predicate_is_unresolved() -> None:
    package = make_evidence_package(relationships=[_rel("A", RelationshipType.IMPORTS, "B")])
    claim = _claim("A", "CALLS", "B")
    result = entail_claim(claim, package)
    assert result.status is EntailmentStatus.UNRESOLVED


def test_direct_edge_match_wrong_direction_is_unresolved() -> None:
    package = make_evidence_package(relationships=[_rel("B", RelationshipType.CALLS, "A")])
    claim = _claim("A", "CALLS", "B")
    result = entail_claim(claim, package)
    assert result.status is EntailmentStatus.UNRESOLVED


def test_set_membership_via_contains_is_a_direct_edge_match() -> None:
    package = make_evidence_package(
        relationships=[_rel("namespace_b", RelationshipType.CONTAINS, "A")]
    )
    claim = _claim("namespace_b", "CONTAINS", "A")
    result = entail_claim(claim, package)
    assert result.status is EntailmentStatus.SUPPORTED
    assert result.method.value == "DIRECT_EDGE"


def test_type_hierarchy_via_implements_is_a_direct_edge_match() -> None:
    package = make_evidence_package(relationships=[_rel("A", RelationshipType.IMPLEMENTS, "IFoo")])
    claim = _claim("A", "IMPLEMENTS", "IFoo")
    result = entail_claim(claim, package)
    assert result.status is EntailmentStatus.SUPPORTED


# --- bounded path existence --------------------------------------------------


def test_reaches_via_multi_hop_calls_chain() -> None:
    package = make_evidence_package(
        relationships=[
            _rel("A", RelationshipType.CALLS, "B"),
            _rel("B", RelationshipType.CALLS, "C"),
        ]
    )
    claim = _claim("A", "REACHES", "C", claim_type=ClaimType.DERIVED)
    result = entail_claim(claim, package)
    assert result.status is EntailmentStatus.SUPPORTED
    assert result.method.value == "PATH_EXISTENCE"
    assert [r.key for r in result.matched_path] == [
        ("A", RelationshipType.CALLS, "B"),
        ("B", RelationshipType.CALLS, "C"),
    ]


def test_reaches_with_no_path_is_unresolved() -> None:
    package = make_evidence_package(relationships=[_rel("A", RelationshipType.CALLS, "B")])
    claim = _claim("A", "REACHES", "Z", claim_type=ClaimType.DERIVED)
    result = entail_claim(claim, package)
    assert result.status is EntailmentStatus.UNRESOLVED


def test_indirectly_depends_on_uses_depends_on_chain() -> None:
    package = make_evidence_package(
        relationships=[
            _rel("A", RelationshipType.DEPENDS_ON, "B"),
            _rel("B", RelationshipType.DEPENDS_ON, "C"),
        ]
    )
    claim = _claim("A", "INDIRECTLY_DEPENDS_ON", "C", claim_type=ClaimType.DERIVED)
    result = entail_claim(claim, package)
    assert result.status is EntailmentStatus.SUPPORTED


def test_find_path_never_loops_on_a_cycle() -> None:
    package = make_evidence_package(
        relationships=[
            _rel("A", RelationshipType.CALLS, "B"),
            _rel("B", RelationshipType.CALLS, "A"),  # cycle back to A
        ]
    )
    claim = _claim("A", "REACHES", "Z", claim_type=ClaimType.DERIVED)
    path = find_path(claim, package)
    assert path == []  # terminates, doesn't hang or crash


def test_path_existence_only_follows_the_relevant_base_predicate() -> None:
    """A DEPENDS_ON edge must not satisfy a REACHES (CALLS-based) claim."""
    package = make_evidence_package(relationships=[_rel("A", RelationshipType.DEPENDS_ON, "B")])
    claim = _claim("A", "REACHES", "B", claim_type=ClaimType.DERIVED)
    result = entail_claim(claim, package)
    assert result.status is EntailmentStatus.UNRESOLVED


# --- unsupported semantic claims ---------------------------------------------


def test_inference_claim_with_no_matching_edge_is_unresolved_never_verified() -> None:
    """directive D10.3: unsupported semantic predicates must produce
    UNRESOLVED rather than fabricated support -- even an INFERENCE-typed
    claim gets the same uniform deterministic check, never a free pass."""
    package = make_evidence_package(relationships=[])
    claim = _claim("A", "CALLS", "B", claim_type=ClaimType.INFERENCE)
    result = entail_claim(claim, package)
    assert result.status is EntailmentStatus.UNRESOLVED


def test_inference_claim_can_still_be_supported_if_deterministic_evidence_exists() -> None:
    """The LLM's own claim_type label never overrides the deterministic
    check in either direction -- entailment is blind to it."""
    package = make_evidence_package(relationships=[_rel("A", RelationshipType.CALLS, "B")])
    claim = _claim("A", "CALLS", "B", claim_type=ClaimType.INFERENCE)
    result = entail_claim(claim, package)
    assert result.status is EntailmentStatus.SUPPORTED


def test_direct_edge_match_never_used_for_derived_predicates() -> None:
    package = make_evidence_package(relationships=[_rel("A", RelationshipType.CALLS, "B")])
    claim = _claim("A", "REACHES", "B", claim_type=ClaimType.DERIVED)
    assert direct_edge_match(claim, package) is None


def test_empty_package_never_supports_any_claim() -> None:
    package = make_evidence_package(relationships=[])
    claim = _claim("A", "CALLS", "B")
    result = entail_claim(claim, package)
    assert result.status is EntailmentStatus.UNRESOLVED
    assert result.detail is not None
