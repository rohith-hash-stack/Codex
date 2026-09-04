"""Behavioral tests for the Deterministic Entailment Engine (TAD §47;
directive D10.3), including the OpenAI Claim Grounding Integrity fix's
canonical-identity resolution (`resolve_claim_endpoint`)."""

from __future__ import annotations

from codex.evidence.model import CanonicalRelationship
from codex.llm.schema import Claim, ClaimType
from codex.ontology.entities import BaseEntityType, RepositorySymbol
from codex.ontology.relationships import RelationshipType
from codex.verification.entailment import (
    EntailmentStatus,
    direct_edge_match,
    entail_claim,
    find_path,
    resolve_claim_endpoint,
)
from llm_fixtures import make_evidence_package


def _rel(subject: str, predicate: RelationshipType, object_: str) -> CanonicalRelationship:
    return CanonicalRelationship(subject=subject, predicate=predicate, object=object_)


def _claim(
    subject: str, predicate: object, object_: str, claim_type: ClaimType = ClaimType.FACT
) -> Claim:
    return Claim(subject=subject, predicate=predicate, object=object_, claim_type=claim_type)


def _entity(canonical_id: str, name: str, qualified_name: str) -> RepositorySymbol:
    return RepositorySymbol(
        canonical_id=canonical_id,
        repository_id="repo1",
        repository_revision="abc123",
        name=name,
        qualified_name=qualified_name,
        base_type=BaseEntityType.FUNCTION,
    )


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


# --- OpenAI Claim Grounding Integrity fix -------------------------------------
#
# Reproduces and locks in the fix for the confirmed defect on
# 60e605b2fadd302f4a3a2cd884067dbc66d665f7: deterministic graph evidence
# contained "caller -> CALLS -> plan_query", the model's claim reversed it
# ("plan_query -> CALLS -> caller"), and the system still reported the
# claim as grounded. Root cause: `direct_edge_match` compared
# `claim.subject`/`.object` directly against canonical ids with no
# identity resolution step, so a claim expressed the way an LLM actually
# writes one (by name, not an opaque id) could never match *any* real
# edge, correct or reversed alike -- the reversal itself was never what
# let it through. These tests use real `RepositorySymbol` entities (named
# the way the LLM would refer to them) so the claim's subject/object are
# resolved to canonical ids before matching, exactly as `ask()` now does.

CALLER = _entity("codex:caller", "caller", "app.py::caller")
PLAN_QUERY = _entity("codex:plan_query", "plan_query", "codex.planner.planner::plan_query")
OTHER = _entity("codex:other", "other", "app.py::other")
ENTITIES = [CALLER, PLAN_QUERY, OTHER]


def test_1_exact_valid_calls_claim_is_accepted() -> None:
    """caller -> CALLS -> plan_query (the real, correctly-oriented edge)."""
    package = make_evidence_package(
        entities=ENTITIES,
        relationships=[_rel(CALLER.canonical_id, RelationshipType.CALLS, PLAN_QUERY.canonical_id)],
    )
    claim = _claim("caller", "CALLS", "plan_query")
    result = entail_claim(claim, package)
    assert result.status is EntailmentStatus.SUPPORTED
    assert result.matched_relationship is not None
    assert result.matched_relationship.key == (
        CALLER.canonical_id,
        RelationshipType.CALLS,
        PLAN_QUERY.canonical_id,
    )


def test_2_reversed_calls_claim_is_rejected() -> None:
    """The exact confirmed defect: evidence has caller -> CALLS ->
    plan_query; the claim reverses it to plan_query -> CALLS -> caller.
    Must be UNRESOLVED, never SUPPORTED -- an edge is never undirected."""
    package = make_evidence_package(
        entities=ENTITIES,
        relationships=[_rel(CALLER.canonical_id, RelationshipType.CALLS, PLAN_QUERY.canonical_id)],
    )
    claim = _claim("plan_query", "CALLS", "caller")
    result = entail_claim(claim, package)
    assert result.status is EntailmentStatus.UNRESOLVED
    assert direct_edge_match(claim, package) is None


def test_3_wrong_source_entity_is_rejected() -> None:
    """Real edge: caller -> CALLS -> plan_query. Claim substitutes a
    different, real subject entity ("other") that has no such edge."""
    package = make_evidence_package(
        entities=ENTITIES,
        relationships=[_rel(CALLER.canonical_id, RelationshipType.CALLS, PLAN_QUERY.canonical_id)],
    )
    claim = _claim("other", "CALLS", "plan_query")
    result = entail_claim(claim, package)
    assert result.status is EntailmentStatus.UNRESOLVED


def test_4_wrong_target_entity_is_rejected() -> None:
    """Same real edge; claim substitutes a different, real object entity."""
    package = make_evidence_package(
        entities=ENTITIES,
        relationships=[_rel(CALLER.canonical_id, RelationshipType.CALLS, PLAN_QUERY.canonical_id)],
    )
    claim = _claim("caller", "CALLS", "other")
    result = entail_claim(claim, package)
    assert result.status is EntailmentStatus.UNRESOLVED


def test_5_wrong_predicate_is_rejected() -> None:
    """Correct, correctly-oriented endpoints; wrong relationship type."""
    package = make_evidence_package(
        entities=ENTITIES,
        relationships=[_rel(CALLER.canonical_id, RelationshipType.CALLS, PLAN_QUERY.canonical_id)],
    )
    claim = _claim("caller", "IMPORTS", "plan_query")
    result = entail_claim(claim, package)
    assert result.status is EntailmentStatus.UNRESOLVED


def test_6_valid_references_direction_is_accepted() -> None:
    package = make_evidence_package(
        entities=ENTITIES,
        relationships=[_rel(CALLER.canonical_id, RelationshipType.REFERENCES, OTHER.canonical_id)],
    )
    claim = _claim("caller", "REFERENCES", "other")
    result = entail_claim(claim, package)
    assert result.status is EntailmentStatus.SUPPORTED


def test_7_reversed_references_is_rejected() -> None:
    """Conservative REFERENCES semantics preserved: direction still
    matters exactly as much for REFERENCES as for CALLS -- it is never
    treated as a symmetric/undirected relationship."""
    package = make_evidence_package(
        entities=ENTITIES,
        relationships=[_rel(CALLER.canonical_id, RelationshipType.REFERENCES, OTHER.canonical_id)],
    )
    claim = _claim("other", "REFERENCES", "caller")
    result = entail_claim(claim, package)
    assert result.status is EntailmentStatus.UNRESOLVED


def test_9_nonexistent_relationship_is_rejected() -> None:
    """Both entities are real and present in evidence, but no
    relationship at all exists between them -- not even reversed."""
    package = make_evidence_package(entities=ENTITIES, relationships=[])
    claim = _claim("caller", "CALLS", "plan_query")
    result = entail_claim(claim, package)
    assert result.status is EntailmentStatus.UNRESOLVED


def test_10_ambiguous_entity_name_cannot_be_validated_by_matching_names_alone() -> None:
    """Two real, distinct entities both named "helper" (different
    modules). A claim referring to the bare name "helper" must not
    resolve to either one merely by name -- resolving it would be a
    guess, and the directive is explicit: no claim may be validated
    merely by matching names when that match is ambiguous."""
    helper_a = _entity("codex:helper_a", "helper", "pkg_a/mod.py::helper")
    helper_b = _entity("codex:helper_b", "helper", "pkg_b/mod.py::helper")
    package = make_evidence_package(
        entities=[helper_a, helper_b, OTHER],
        relationships=[_rel(helper_a.canonical_id, RelationshipType.CALLS, OTHER.canonical_id)],
    )
    # Resolution itself must refuse to pick one.
    assert resolve_claim_endpoint("helper", package) is None
    # And therefore entailment must not accidentally validate the claim
    # against whichever of the two the real edge happens to belong to.
    claim = _claim("helper", "CALLS", "other")
    result = entail_claim(claim, package)
    assert result.status is EntailmentStatus.UNRESOLVED


def test_resolve_claim_endpoint_prefers_exact_canonical_id_over_name() -> None:
    package = make_evidence_package(entities=ENTITIES, relationships=[])
    assert resolve_claim_endpoint(CALLER.canonical_id, package) == CALLER.canonical_id


def test_resolve_claim_endpoint_resolves_by_exact_qualified_name() -> None:
    package = make_evidence_package(entities=ENTITIES, relationships=[])
    assert resolve_claim_endpoint("app.py::caller", package) == CALLER.canonical_id


def test_resolve_claim_endpoint_never_uses_substring_or_similarity_matching() -> None:
    """"call" is a substring of "caller" and semantically related to
    "CALLS" -- neither is a legitimate basis for resolution."""
    package = make_evidence_package(entities=ENTITIES, relationships=[])
    assert resolve_claim_endpoint("call", package) is None
    assert resolve_claim_endpoint("Caller", package) is None  # case must match exactly too


def test_resolve_claim_endpoint_finds_a_relationship_only_endpoint_not_in_entities() -> None:
    """A canonical id already known to the package via a relationship's
    own subject/object (but not separately listed in `entities`) still
    resolves -- ids are unique by construction, so this axis is never
    ambiguous regardless of which list it was found in."""
    package = make_evidence_package(
        entities=[], relationships=[_rel("codex:x", RelationshipType.CALLS, "codex:y")]
    )
    assert resolve_claim_endpoint("codex:x", package) == "codex:x"
    assert resolve_claim_endpoint("codex:y", package) == "codex:y"
    assert resolve_claim_endpoint("codex:z", package) is None
