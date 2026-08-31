"""Symbol-level D1-D10 integration hardening pass.

Closes ``docs/architecture-truth-report.md`` §12 Finding 1: every
pre-existing D9/D10 test built its graph exclusively on
``tests/planner_fixtures.py``'s FILE-only fixture, so no test had ever
proven the real ingestion -> graph -> QueryContract -> Planner/Retrieval
-> EvidencePackage -> Claims -> Verification -> Final Answer chain for a
symbol-level (function/class/method) query -- even though HLRD/TAD's own
worked examples (HLRD §36) and this project's own prior end-to-end trace
("Which tests call `authenticate`?") are symbol-level, not file-level.

Uses ``tests/symbol_level_fixtures.py``'s mixed FILE/FUNCTION/CLASS/
METHOD graph and the real, unmodified D8 (`codex.query_understanding`),
D9 (`codex.planner`), and D10 (`codex.llm`/`codex.verification`) code
throughout. Only the LLM boundary is faked (`FakeLLMGateway`), matching
every other D10 integration test in this repository.
"""

from __future__ import annotations

from datetime import UTC, datetime

from codex.llm.gateway import LLMRequest
from codex.llm.schema import Claim, ClaimType, StructuredAnswer
from codex.ontology.entities import BaseEntityType
from codex.ontology.relationships import RelationshipType
from codex.planner.planner import execute_query, plan_query
from codex.provider.capability import Capability
from codex.query_understanding.engine import understand_query
from codex.query_understanding.models import CompletenessLevel, Intent, QueryContract
from codex.query_understanding.session import SessionContext
from codex.query_understanding.tier0 import detect
from codex.verification.answer import AnswerDecision, build_final_answer
from codex.verification.resynthesis import run_verification_loop
from codex.verification.state import VerificationStatus
from fake_llm_gateway import FakeLLMGateway, ok_result
from symbol_level_fixtures import (
    AUTH_SERVICE_CLASS,
    AUTHENTICATE_FN,
    AUTHENTICATE_METHOD,
    TEST_INVALID_LOGIN_FN,
    TEST_VALID_LOGIN_FN,
    build_symbol_level_graph,
)

NOW = datetime(2026, 8, 31, tzinfo=UTC)


def test_mixed_graph_contains_file_function_class_and_method_entities() -> None:
    """The fixture itself: one real `IngestionPipeline.run()` produces a
    graph with all four base types and real (non-fabricated)
    relationships -- not four disjoint islands."""
    result, _registry, _evidence_store, _repository = build_symbol_level_graph()
    store = result.graph_store

    by_type: dict[BaseEntityType, list[str]] = {
        base_type: [e.name for e in store.find_entities(base_type=base_type)]
        for base_type in (
            BaseEntityType.FILE,
            BaseEntityType.FUNCTION,
            BaseEntityType.CLASS,
            BaseEntityType.METHOD,
        )
    }

    assert sorted(by_type[BaseEntityType.FILE]) == [
        "auth_service.py",
        "test_auth.py",
        "test_login.py",
    ]
    assert sorted(by_type[BaseEntityType.FUNCTION]) == sorted(
        [AUTHENTICATE_FN, TEST_VALID_LOGIN_FN, TEST_INVALID_LOGIN_FN]
    )
    assert by_type[BaseEntityType.CLASS] == [AUTH_SERVICE_CLASS]
    assert by_type[BaseEntityType.METHOD] == [AUTHENTICATE_METHOD]

    relationships = store.get_relationships()
    calls = [r for r in relationships if r.predicate is RelationshipType.CALLS]
    contains = [r for r in relationships if r.predicate is RelationshipType.CONTAINS]
    assert len(calls) == 2
    assert len(contains) == 1


def test_symbol_level_query_end_to_end_via_real_query_understanding() -> None:
    """The full chain, driven by real Tier-0 detection on real query
    text -- not a hand-built `QueryContract` -- for a symbol-level
    ("Which tests call `authenticate`?") query. Mirrors the primary
    trace performed during the architecture closure audit, now as a
    committed, repeatable regression test rather than a one-off script."""
    result, registry, evidence_store, repository = build_symbol_level_graph()

    query_text = "Which tests call authenticate?"
    candidates = detect(query_text)
    assert candidates[0].intent.value == "FIND_TESTS"
    assert candidates[0].targets == (AUTHENTICATE_FN,)

    session = SessionContext(repository_id=repository.repository_id)
    understanding = understand_query(
        query_text, repository_id=repository.repository_id, session=session, now=NOW
    )
    assert understanding.status.value == "RESOLVED"
    contract = understanding.contract
    assert contract.targets == [AUTHENTICATE_FN]

    plan = plan_query(
        query_contract=contract,
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.status.value == "OK"
    assert plan.negative_query_candidate is False

    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    entity_names = {e.name for e in package.entities}
    assert {AUTHENTICATE_FN, TEST_VALID_LOGIN_FN, TEST_INVALID_LOGIN_FN} <= entity_names
    assert len(package.relationships) == 2

    authenticate_id = next(e.canonical_id for e in package.entities if e.name == AUTHENTICATE_FN)
    test1_id = next(e.canonical_id for e in package.entities if e.name == TEST_VALID_LOGIN_FN)
    test2_id = next(e.canonical_id for e in package.entities if e.name == TEST_INVALID_LOGIN_FN)

    explanation = f"{TEST_VALID_LOGIN_FN} and {TEST_INVALID_LOGIN_FN} both call {AUTHENTICATE_FN}."
    answer = StructuredAnswer(
        explanation=explanation,
        claims=[
            Claim(
                subject=test1_id,
                predicate="CALLS",
                object=authenticate_id,
                claim_type=ClaimType.FACT,
            ),
            Claim(
                subject=test2_id,
                predicate="CALLS",
                object=authenticate_id,
                claim_type=ClaimType.FACT,
            ),
        ],
    )
    request = LLMRequest(
        query_text=query_text,
        evidence_package=package,
        response_schema=StructuredAnswer.model_json_schema(),
        token_budget=contract.token_budget,
        latency_budget_ms=contract.latency_budget_ms,
    )
    gateway = FakeLLMGateway([ok_result(answer)])

    loop_result = run_verification_loop(gateway, request, package, registry=registry, now=NOW)
    assert loop_result.resynthesis_used is False
    assert len(loop_result.retained) == 2
    assert loop_result.removed == []

    final = build_final_answer(loop_result, negative_query_result=plan.negative_query_result)
    assert final.decision is AnswerDecision.STRONG_ANSWER
    assert final.verification_status is VerificationStatus.VERIFIED
    assert len(final.supported_claims) == 2


def test_class_contains_method_relationship_resolves_through_real_retrieval() -> None:
    """A second symbol-level shape (CLASS -> CONTAINS -> METHOD),
    proving retrieval is not accidentally specialized to FUNCTION/CALLS
    only. Uses a hand-built `QueryContract` (as the existing
    `test_d1_d10_integration.py` scenarios do) rather than Tier-0 text
    parsing, since Tier-0 has no CLASS-containment pattern -- that is
    an existing, unrelated D8 scope boundary, not something this pass
    changes."""
    result, registry, evidence_store, repository = build_symbol_level_graph()

    contract = QueryContract(
        intent=Intent.FIND_CALLERS,
        targets=[AUTH_SERVICE_CLASS],
        relationship_types=[RelationshipType.CONTAINS],
        complexity=0.2,
        ambiguity=0.1,
        confidence=0.97,
        completeness_requirement=CompletenessLevel.LOW,
        required_evidence=[Capability.TYPE_RELATIONSHIP],
        token_budget=4000,
        latency_budget_ms=5000,
    )
    plan = plan_query(
        query_contract=contract,
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.status.value == "OK"

    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    entity_names = {e.name for e in package.entities}
    assert {AUTH_SERVICE_CLASS, AUTHENTICATE_METHOD} <= entity_names
    assert any(r.predicate is RelationshipType.CONTAINS for r in package.relationships)
