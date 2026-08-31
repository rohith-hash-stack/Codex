"""Ranking Engine tests (TAD §36-37; directive D9 Part 18 "Ranking"):
deterministic ranking, each signal in isolation, normalization,
tie-breaking, graph distance.
"""

from __future__ import annotations

from codex.coverage.engine import CompletenessLevel
from codex.evidence.model import CanonicalRelationship
from codex.ontology.entities import BaseEntityType, RepositorySymbol
from codex.ontology.relationships import RelationshipType
from codex.planner.planner import execute_query, plan_query
from codex.planner.ranking import (
    RankingSignals,
    bm25_scores,
    candidate_tags,
    graph_proximity,
    query_constraint_match,
    rank_entities,
    score,
    structural_relevance,
)
from codex.provider.capability import Capability
from codex.query_understanding.models import Intent, QueryContract
from planner_fixtures import build_graph


def _entity(name: str, canonical_id: str) -> RepositorySymbol:
    return RepositorySymbol(
        canonical_id=canonical_id,
        repository_id="repo1",
        repository_revision="rev1",
        name=name,
        qualified_name=name,
        base_type=BaseEntityType.FILE,
    )


# --- individual signals ------------------------------------------------------


def test_bm25_scores_are_normalized_to_zero_one() -> None:
    docs = [["auth", "service"], ["billing", "cache"], ["auth", "handler"]]
    scores = bm25_scores(docs, ["auth"])
    assert max(scores) == 1.0
    assert all(0.0 <= s <= 1.0 for s in scores)
    assert scores[1] == 0.0  # no query-term match


def test_bm25_scores_empty_query_is_all_zero() -> None:
    docs = [["auth"], ["billing"]]
    assert bm25_scores(docs, []) == [0.0, 0.0]


def test_bm25_scores_no_document_matches_query_terms_is_all_zero() -> None:
    docs = [["auth"], ["billing"]]
    assert bm25_scores(docs, ["nonexistent"]) == [0.0, 0.0]


def test_structural_relevance_primary_match_vs_mismatch() -> None:
    rel = CanonicalRelationship(subject="a", predicate=RelationshipType.CALLS, object="b")
    assert structural_relevance(rel, RelationshipType.CALLS) == 1.0
    assert structural_relevance(rel, RelationshipType.IMPORTS) == 0.3
    assert structural_relevance(None, RelationshipType.CALLS) == 0.3


def test_graph_proximity_decay() -> None:
    assert graph_proximity(0) == 1.0
    assert graph_proximity(1) == 0.9
    assert abs(graph_proximity(2) - 0.81) < 1e-9
    assert graph_proximity(None) == 0.0


def test_query_constraint_match_jaccard() -> None:
    assert query_constraint_match({"api", "handler"}, {"api"}) == 0.5
    assert query_constraint_match({"api"}, set()) == 1.0  # vacuous: nothing to violate
    assert query_constraint_match(set(), {"api"}) == 0.0


def test_candidate_tags_includes_roles_base_type_and_path_segments() -> None:
    entity = RepositorySymbol(
        canonical_id="codex:x",
        repository_id="repo1",
        repository_revision="rev1",
        name="auth.py",
        qualified_name="src/auth.py",
        base_type=BaseEntityType.FILE,
        roles=["SERVICE"],
    )
    tags = candidate_tags(entity)
    assert "SERVICE" in tags
    assert "FILE" in tags
    assert "src" in tags
    assert "auth.py" in tags


def test_score_is_weighted_sum_of_four_signals() -> None:
    signals = RankingSignals(
        semantic_relevance=1.0,
        structural_relevance=0.0,
        graph_proximity=0.0,
        query_constraint_match=0.0,
    )
    assert score(signals) == 0.25  # equal 0.25 weighting


# --- rank_entities: determinism, tie-breaking, distance ----------------------


def test_rank_entities_orders_by_score_descending() -> None:
    close = _entity("auth", "codex:a")
    far = _entity("cache", "codex:b")
    ranked = rank_entities(
        entities=[far, close],
        relationships=[],
        distances={"codex:a": 1, "codex:b": 3},
        query_targets=[],
        query_constraints=[],
        primary_relationship_type=None,
    )
    assert [e.canonical_id for e, _ in ranked] == ["codex:a", "codex:b"]


def test_rank_entities_ties_broken_by_canonical_id() -> None:
    a = _entity("x", "codex:a")
    b = _entity("y", "codex:b")
    ranked = rank_entities(
        entities=[b, a],
        relationships=[],
        distances={},
        query_targets=[],
        query_constraints=[],
        primary_relationship_type=None,
    )
    assert [e.canonical_id for e, _ in ranked] == ["codex:a", "codex:b"]


def test_rank_entities_is_deterministic_across_calls() -> None:
    entities = [_entity("auth", "codex:a"), _entity("billing", "codex:b")]
    kwargs = dict(
        entities=entities,
        relationships=[],
        distances={"codex:a": 1, "codex:b": 2},
        query_targets=["auth"],
        query_constraints=[],
        primary_relationship_type=None,
    )
    first = rank_entities(**kwargs)
    second = rank_entities(**kwargs)
    assert [(e.canonical_id, s) for e, s in first] == [(e.canonical_id, s) for e, s in second]


# --- integration: ranking flows through execute_query ------------------------


def _contract(**overrides: object) -> QueryContract:
    kwargs: dict[str, object] = {
        "intent": Intent.FIND_IMPACT,
        "targets": ["service.py"],
        "relationship_types": [RelationshipType.CALLS],
        "complexity": 0.3,
        "ambiguity": 0.1,
        "confidence": 0.97,
        "completeness_requirement": CompletenessLevel.LOW,
        "required_evidence": [Capability.CALL_RELATIONSHIP],
        "token_budget": 4000,
        "latency_budget_ms": 5000,
    }
    kwargs.update(overrides)
    return QueryContract(**kwargs)


def test_execute_query_ranks_closer_entities_first() -> None:
    result, registry, evidence_store, repository = build_graph(
        entity_paths=("service.py", "auth.py", "billing.py"),
        relationship_pairs=(("service.py", "auth.py"), ("auth.py", "billing.py")),
    )
    plan = plan_query(
        query_contract=_contract(),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    names = [e.name for e in package.entities]
    assert names.index("service.py") < names.index("billing.py")
