"""Integration tests for `codex.telemetry` (directive D11): a real
D8->D9->D10 pipeline result recorded as telemetry, the real
`GraphVersionMismatchError` mapped to a `CONCURRENT_UPDATE_DETECTED`
telemetry event, and confirmation that recording telemetry never
alters the pipeline's own outcome.

Uses the real, unmodified D8/D9/D10 pipeline throughout (the same
`build_graph`/`plan_query`/`execute_query`/`run_verification_loop`
chain every other D9/D10 integration test in this repository already
exercises) -- only the LLM boundary is faked, matching every other
D10 integration test.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from codex.coverage.engine import CompletenessLevel
from codex.ingestion.pipeline import IngestionPipeline
from codex.llm.gateway import LLMRequest
from codex.llm.schema import Claim, ClaimType, StructuredAnswer
from codex.ontology.relationships import RelationshipType
from codex.planner.planner import GraphVersionMismatchError, execute_query, plan_query
from codex.provider.capability import Capability
from codex.query_understanding.models import Intent, QueryContract
from codex.telemetry.mapping import failure_event_from_graph_version_mismatch
from codex.telemetry.models import FailureCode, QueryTelemetryEvent
from codex.telemetry.store import InMemoryTelemetryStore
from codex.verification.answer import build_final_answer
from codex.verification.resynthesis import run_verification_loop
from codex.verification.state import VerificationStatus
from fake_llm_gateway import FakeLLMGateway, ok_result
from planner_fixtures import build_graph

NOW = datetime(2026, 8, 31, tzinfo=UTC)


def _contract(**overrides: object) -> QueryContract:
    kwargs: dict[str, object] = {
        "intent": Intent.FIND_CALLERS,
        "targets": ["auth.py"],
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


# --- real pipeline -> QueryTelemetryEvent ----------------------------------


def test_real_pipeline_result_recorded_as_query_telemetry_event() -> None:
    """A real D9/D10 run's output, recorded verbatim as telemetry --
    proves the schema's fields are actually populatable from real
    pipeline objects, not just synthetic fixtures."""
    result, registry, evidence_store, repository = build_graph(
        entity_paths=("service.py", "auth.py"), relationship_pairs=(("service.py", "auth.py"),)
    )
    contract = _contract()
    plan = plan_query(
        query_contract=contract,
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    subject_id = next(e.canonical_id for e in package.entities if e.name == "service.py")
    object_id = next(e.canonical_id for e in package.entities if e.name == "auth.py")
    answer = StructuredAnswer(
        explanation="service.py calls auth.py.",
        claims=[
            Claim(
                subject=subject_id,
                predicate="CALLS",
                object=object_id,
                claim_type=ClaimType.FACT,
            )
        ],
    )
    request = LLMRequest(
        query_text="Who calls auth.py?",
        evidence_package=package,
        response_schema=StructuredAnswer.model_json_schema(),
        token_budget=4000,
        latency_budget_ms=5000,
    )
    gateway = FakeLLMGateway([ok_result(answer)])
    loop_result = run_verification_loop(gateway, request, package, registry=registry, now=NOW)
    final = build_final_answer(loop_result, negative_query_result=plan.negative_query_result)

    unsupported = sum(
        1
        for cv in (*loop_result.retained, *loop_result.removed)
        if cv.entailment.status.value == "UNRESOLVED"
    )
    contradictions = sum(
        1
        for cv in (*loop_result.retained, *loop_result.removed)
        if cv.contradiction_level.value != "NONE"
    )

    event = QueryTelemetryEvent.build(
        query_id="q-real-1",
        graph_version=plan.graph_version,
        query_contract=contract,
        retrieval_plan=plan,
        candidate_count=len(package.entities) + len(package.relationships),
        mss_size=len(package.entities) + len(package.relationships),
        llm_calls=loop_result.attempts,
        verification_result=final.verification_status,
        unsupported_claim_count=unsupported,
        contradiction_count=contradictions,
        now=NOW,
    )

    store = InMemoryTelemetryStore()
    store.record_query_event(event)

    recorded = store.query_events(repository_id=repository.repository_id)
    assert len(recorded) == 1
    assert recorded[0].graph_version_id == plan.graph_version.version_id
    assert recorded[0].verification_result is VerificationStatus.VERIFIED
    assert recorded[0].llm_calls == 1
    assert recorded[0].unsupported_claim_count == 0
    assert recorded[0].contradiction_count == 0


def test_partial_provider_result_recorded_as_failure_event() -> None:
    """A real ingestion outcome from a run with a partially-failing
    capability, recorded generically via `FailureCode.
    PARTIAL_PROVIDER_RESULT` -- proves the generic failure-recording
    mechanism works beyond the one TAD-§55-named
    CONCURRENT_UPDATE_DETECTED case."""
    from codex.telemetry.models import FailureTelemetryEvent

    result, registry, evidence_store, repository = build_graph(
        entity_paths=("service.py", "auth.py"),
        relationship_pairs=(("service.py", "auth.py"),),
        fail_capabilities=frozenset({Capability.SYMBOL_REFERENCE}),
    )
    assert any(
        o.cohort is not None and o.cohort.coverage_status.value == "PARTIAL"
        for o in result.provider_outcomes
    )

    store = InMemoryTelemetryStore()
    event = FailureTelemetryEvent.build(
        code=FailureCode.PARTIAL_PROVIDER_RESULT,
        repository_id=repository.repository_id,
        graph_version_id=result.graph_version.version_id,
        detail="fake provider's SYMBOL_REFERENCE capability failed",
        now=NOW,
    )
    store.record_failure_event(event)

    recorded = store.failure_events(code=FailureCode.PARTIAL_PROVIDER_RESULT)
    assert len(recorded) == 1
    assert recorded[0].repository_id == repository.repository_id
    assert recorded[0].graph_version_id == result.graph_version.version_id


# --- GraphVersionMismatchError telemetry mapping ---------------------------


def test_graph_version_mismatch_error_maps_to_concurrent_update_detected_event() -> None:
    """The real D9 detection mechanism (`GraphVersionMismatchError`,
    unmodified) raises; this test proves the mapping helper correctly
    translates that real, live exception into a storable telemetry
    event -- not a synthetic/hand-constructed exception."""
    result, registry, evidence_store, repository = build_graph(
        entity_paths=("service.py", "auth.py"), relationship_pairs=(("service.py", "auth.py"),)
    )
    plan = plan_query(
        query_contract=_contract(),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )

    pipeline = IngestionPipeline(registry, evidence_store)
    moved_repository = repository.model_copy(update={"head_revision": "rev2"})
    new_result = pipeline.run(moved_repository)

    store = InMemoryTelemetryStore()
    with pytest.raises(GraphVersionMismatchError) as exc_info:
        execute_query(
            plan,
            graph=new_result.graph_store,
            evidence_store=evidence_store,
            ingestion_result=new_result,
        )

    event = failure_event_from_graph_version_mismatch(
        exc_info.value, plan=plan, query_id="q-mismatch-1", now=NOW
    )
    store.record_failure_event(event)

    recorded = store.failure_events(code=FailureCode.CONCURRENT_UPDATE_DETECTED)
    assert len(recorded) == 1
    assert recorded[0].repository_id == repository.repository_id
    assert recorded[0].graph_version_id == plan.graph_version.version_id
    assert recorded[0].query_id == "q-mismatch-1"
    assert "CONCURRENT_UPDATE_DETECTED" in recorded[0].detail


def test_graph_version_mismatch_still_raises_after_telemetry_mapping_is_available() -> None:
    """D9's own refusal-to-proceed behavior is completely unchanged by
    telemetry existing -- the exception still propagates; recording it
    is something the caller does *in addition to*, never *instead of*,
    letting the error surface."""
    result, registry, evidence_store, repository = build_graph(
        entity_paths=("service.py", "auth.py"), relationship_pairs=(("service.py", "auth.py"),)
    )
    plan = plan_query(
        query_contract=_contract(),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    pipeline = IngestionPipeline(registry, evidence_store)
    moved_repository = repository.model_copy(update={"head_revision": "rev2"})
    new_result = pipeline.run(moved_repository)

    with pytest.raises(GraphVersionMismatchError):
        execute_query(
            plan,
            graph=new_result.graph_store,
            evidence_store=evidence_store,
            ingestion_result=new_result,
        )


# --- no correctness dependency on telemetry --------------------------------


def test_recording_telemetry_does_not_change_the_already_computed_final_answer() -> None:
    """Recording an event into `InMemoryTelemetryStore` after the fact
    cannot retroactively alter a `FinalAnswer`/`ClaimVerification`
    already returned -- telemetry is a pure sink, never a mutator of
    the objects it observes."""
    result, registry, evidence_store, repository = build_graph(
        entity_paths=("service.py", "auth.py"), relationship_pairs=(("service.py", "auth.py"),)
    )
    contract = _contract()
    plan = plan_query(
        query_contract=contract,
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    subject_id = next(e.canonical_id for e in package.entities if e.name == "service.py")
    object_id = next(e.canonical_id for e in package.entities if e.name == "auth.py")
    answer = StructuredAnswer(
        explanation="service.py calls auth.py.",
        claims=[
            Claim(
                subject=subject_id,
                predicate="CALLS",
                object=object_id,
                claim_type=ClaimType.FACT,
            )
        ],
    )
    request = LLMRequest(
        query_text="Who calls auth.py?",
        evidence_package=package,
        response_schema=StructuredAnswer.model_json_schema(),
        token_budget=4000,
        latency_budget_ms=5000,
    )
    gateway = FakeLLMGateway([ok_result(answer)])
    loop_result = run_verification_loop(gateway, request, package, now=NOW)
    final_before = build_final_answer(loop_result, negative_query_result=plan.negative_query_result)

    store = InMemoryTelemetryStore()
    event = QueryTelemetryEvent.build(
        query_id="q1",
        graph_version=plan.graph_version,
        query_contract=contract,
        retrieval_plan=plan,
        candidate_count=len(package.entities),
        mss_size=len(package.entities),
        llm_calls=loop_result.attempts,
        verification_result=final_before.verification_status,
        now=NOW,
    )
    store.record_query_event(event)

    final_after = build_final_answer(loop_result, negative_query_result=plan.negative_query_result)
    assert final_after == final_before
    assert final_after.decision == final_before.decision
    assert final_after.verification_status == final_before.verification_status
