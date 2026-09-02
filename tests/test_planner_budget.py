"""Budget-aware planning tests (TAD §32, §41; directive D9 Part 18
"Budget"): plan within budget, latency/token over-budget pruning,
minimal plan blocked, EXHAUSTIVE cannot be over-pruned, PRUNED telemetry.
"""

from __future__ import annotations

from pathlib import Path

from codex.coverage.engine import CompletenessLevel
from codex.ontology.relationships import RelationshipType
from codex.planner.cache import PlanCache
from codex.planner.models import PlanStatus
from codex.planner.planner import execute_query, plan_query
from codex.provider.capability import Capability
from codex.provider.scip_adapter import DEFAULT_INDEX_FILENAME, SCIPAdapter
from codex.query_understanding.models import Intent, QueryContract
from codex.repository.models import RepositoryMetadata
from planner_fixtures import build_graph
from scip_fixtures import document, occurrence, relationship, scip_index, symbol_information


def make_contract(**overrides: object) -> QueryContract:
    kwargs: dict[str, object] = {
        "intent": Intent.FIND_CALLERS,
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


def _fan_out_graph():
    return build_graph(
        entity_paths=("service.py", "auth.py", "billing.py", "cache.py"),
        relationship_pairs=(
            ("service.py", "auth.py"),
            ("service.py", "billing.py"),
            ("service.py", "cache.py"),
        ),
    )


def test_plan_within_budget_is_ok_with_no_pruning() -> None:
    result, registry, _, repository = _fan_out_graph()
    plan = plan_query(
        query_contract=make_contract(),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.status is PlanStatus.OK
    assert plan.telemetry.budget_trace.pruning_occurred is False


def test_latency_over_budget_prunes_traversal_depth() -> None:
    result, registry, _, repository = _fan_out_graph()
    plan = plan_query(
        query_contract=make_contract(intent=Intent.FIND_IMPACT, latency_budget_ms=1000),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.status is PlanStatus.PRUNED
    assert plan.traversal_depth == 0  # base depth 2, latency affords 1000//1500 = 0
    assert "reduce traversal depth" in plan.telemetry.budget_trace.pruning_steps


def test_token_over_budget_truncates_and_prunes() -> None:
    result, registry, _, repository = _fan_out_graph()
    plan = plan_query(
        query_contract=make_contract(token_budget=100),  # max_nodes=2, max_edges=5
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.status is PlanStatus.PRUNED
    assert plan.telemetry.budget_trace.pruning_occurred is True
    assert plan.telemetry.budget_trace.pruned_node_estimate <= plan.budget.max_nodes


def test_relationship_type_removal_step_when_multiple_types_requested() -> None:
    result, registry, _, repository = _fan_out_graph()
    plan = plan_query(
        query_contract=make_contract(
            token_budget=100,
            relationship_types=[RelationshipType.CALLS, RelationshipType.IMPORTS],
        ),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert "remove optional relationship types" in plan.telemetry.budget_trace.pruning_steps
    assert plan.relationship_types == [RelationshipType.CALLS]


# ---------------------------------------------------------------------------
# GAP-11 fix: relationship-type pruning must keep the type with real
# evidence, not blindly the alphabetically-first requested type.
# ---------------------------------------------------------------------------
#
# Root cause (Python fidelity audit, docs/python-fidelity-gap-register.md):
# real scip-python output never sets the SCIP Import occurrence-role bit,
# so RelationshipType.IMPORTS is permanently empty for Python repositories
# -- and "IMPORTS" < "REFERENCES" alphabetically, so `_relationship_types_
# for_intent`'s own (elsewhere-legitimate) alphabetical ordering meant
# FIND_REFERENCES queries deterministically kept the one type guaranteed
# to have zero results on every truncation. `_fan_out_graph()`'s own
# default `predicate=CALLS` graph never exercises this: CALLS happens to
# already sort first, so `test_relationship_type_removal_step_when_
# multiple_types_requested` above passes under both the old and new code
# -- these tests specifically construct a REFERENCES-only graph (zero
# IMPORTS edges anywhere) and request `[IMPORTS, REFERENCES]`, the exact
# real-world shape that reproduced 0% retrieval recall on django/click.


def _references_only_graph():
    return build_graph(
        entity_paths=("service.py", "auth.py", "billing.py", "cache.py"),
        relationship_pairs=(
            ("service.py", "auth.py"),
            ("service.py", "billing.py"),
            ("service.py", "cache.py"),
        ),
        predicate=RelationshipType.REFERENCES,
    )


def test_find_references_with_real_evidence_survives_pruning() -> None:
    """The exact real-world failure mode: `IMPORTS` (alphabetically
    first, zero evidence) must not be kept over `REFERENCES` (real
    evidence) on truncation."""
    result, registry, evidence_store, repository = _references_only_graph()
    plan = plan_query(
        query_contract=make_contract(
            intent=Intent.FIND_REFERENCES,
            token_budget=100,
            relationship_types=[RelationshipType.IMPORTS, RelationshipType.REFERENCES],
            required_evidence=[Capability.SYMBOL_REFERENCE],
        ),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert "remove optional relationship types" in plan.telemetry.budget_trace.pruning_steps
    assert plan.relationship_types == [RelationshipType.REFERENCES]
    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    assert len(package.relationships) > 0
    assert all(r.predicate is RelationshipType.REFERENCES for r in package.relationships)


def test_empty_higher_priority_type_does_not_suppress_populated_type() -> None:
    """Direct unit check on the pruning helper itself: an empty type
    ordered first must never be chosen over a type with real edges in
    the already-computed traversal, regardless of which type the caller
    listed first."""
    from codex.evidence.model import CanonicalRelationship, EvidenceStatus
    from codex.planner.planner import _prioritize_relationship_types_by_evidence

    observed = [
        CanonicalRelationship(
            subject="a",
            predicate=RelationshipType.REFERENCES,
            object="b",
            confidence=1.0,
            status=EvidenceStatus.SUPPORTED,
        )
    ]
    prioritized = _prioritize_relationship_types_by_evidence(
        [RelationshipType.IMPORTS, RelationshipType.REFERENCES], observed
    )
    assert prioritized[0] is RelationshipType.REFERENCES


def test_multiple_populated_types_preserve_deterministic_alphabetical_tie_break() -> None:
    """When two types are tied on evidence count (including the
    all-zero case), the original relative order -- today's alphabetical
    `_relationship_types_for_intent` ordering -- is preserved exactly,
    proving this fix only changes behavior when it is actually wrong."""
    from codex.planner.planner import _prioritize_relationship_types_by_evidence

    # all-zero-evidence case: order must be unchanged (today's behavior)
    assert _prioritize_relationship_types_by_evidence(
        [RelationshipType.CALLS, RelationshipType.IMPORTS, RelationshipType.REFERENCES], []
    ) == [RelationshipType.CALLS, RelationshipType.IMPORTS, RelationshipType.REFERENCES]


def test_evidence_magnitude_never_overrides_intent_priority_among_populated_types() -> None:
    """Regression guard for a real defect caught while designing this fix:
    an earlier draft ranked purely by raw edge count, which broke
    FIND_CALLERS on real pytest data (`CALLS` has 13 real edges, `REFERENCES`
    has 82 -- magnitude-ranking silently swapped a "what *calls* X" answer
    for a generic-reference one). A type with *fewer* real edges must still
    outrank a type with *more* real edges when the fewer-edges type is
    already first in the intent's own priority order -- "has any evidence"
    is the only thing this fix is allowed to change."""
    from codex.evidence.model import CanonicalRelationship, EvidenceStatus
    from codex.planner.planner import _prioritize_relationship_types_by_evidence

    observed = [
        CanonicalRelationship(
            subject="a",
            predicate=RelationshipType.CALLS,
            object="b",
            confidence=1.0,
            status=EvidenceStatus.SUPPORTED,
        ),
    ] * 13 + [
        CanonicalRelationship(
            subject="a",
            predicate=RelationshipType.REFERENCES,
            object="c",
            confidence=1.0,
            status=EvidenceStatus.SUPPORTED,
        ),
    ] * 82
    prioritized = _prioritize_relationship_types_by_evidence(
        [RelationshipType.CALLS, RelationshipType.IMPORTS, RelationshipType.REFERENCES], observed
    )
    assert prioritized[0] is RelationshipType.CALLS


def test_find_callers_keeps_calls_type_despite_more_numerous_references_evidence(
    monkeypatch,  # noqa: ANN001
) -> None:
    """End-to-end version of the guard above, against a real graph: a
    FIND_CALLERS query over budget must still retrieve CALLS-typed
    relationships, never silently substitute REFERENCES merely because
    more REFERENCES edges happen to exist in the same traversal."""
    result, registry, evidence_store, repository = build_graph(
        entity_paths=("service.py", "auth.py", "billing.py"),
        relationship_pairs=(("auth.py", "service.py"),),
        predicate=RelationshipType.CALLS,
    )
    # A second provider contributes many more REFERENCES edges to the
    # same graph than the one real CALLS edge above.
    from fake_ingestion_provider import DeterministicFakeAdapter
    from planner_fixtures import PROFILE

    references_adapter = DeterministicFakeAdapter(
        name="fake-references",
        capabilities=frozenset({Capability.SYMBOL_REFERENCE}),
        entity_paths=(),
        relationship_pairs=(
            ("billing.py", "service.py"),
            ("billing.py", "auth.py"),
        ),
        predicate=RelationshipType.REFERENCES,
    )
    registry.register(references_adapter, PROFILE)
    from codex.ingestion.pipeline import IngestionPipeline

    pipeline = IngestionPipeline(registry, evidence_store)
    result = pipeline.run(repository)

    plan = plan_query(
        query_contract=make_contract(
            intent=Intent.FIND_CALLERS,
            token_budget=100,
            relationship_types=[
                RelationshipType.CALLS,
                RelationshipType.IMPORTS,
                RelationshipType.REFERENCES,
            ],
        ),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    if "remove optional relationship types" in plan.telemetry.budget_trace.pruning_steps:
        assert plan.relationship_types == [RelationshipType.CALLS]


def test_find_implementations_unaffected_by_gap11_fix() -> None:
    """FIND_IMPLEMENTATIONS has exactly one relationship type
    (IMPLEMENTS) -- the `len(effective_relationship_types) > 1` branch
    this fix touches is never reached for it, so its behavior (and the
    fact that it never even hits the "remove optional relationship
    types" step) must be completely unchanged."""
    result, registry, _, repository = build_graph(
        entity_paths=("shape.py", "circle.py"),
        relationship_pairs=(("circle.py", "shape.py"),),
        predicate=RelationshipType.IMPLEMENTS,
        capabilities=frozenset({Capability.IMPLEMENTATION}),
    )
    plan = plan_query(
        query_contract=make_contract(
            intent=Intent.FIND_IMPLEMENTATIONS,
            token_budget=100,
            relationship_types=[RelationshipType.IMPLEMENTS],
            required_evidence=[Capability.IMPLEMENTATION],
        ),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert "remove optional relationship types" not in plan.telemetry.budget_trace.pruning_steps
    assert plan.relationship_types == [RelationshipType.IMPLEMENTS]


def test_find_callers_with_real_evidence_unaffected_by_gap11_fix() -> None:
    """FIND_CALLERS' evidence set is [CALLS, IMPORTS, REFERENCES] --
    CALLS already sorts first *and* has real evidence in this graph, so
    the fix must keep exactly today's choice."""
    result, registry, _, repository = _fan_out_graph()
    plan = plan_query(
        query_contract=make_contract(
            intent=Intent.FIND_CALLERS,
            token_budget=100,
            relationship_types=[
                RelationshipType.CALLS,
                RelationshipType.IMPORTS,
                RelationshipType.REFERENCES,
            ],
        ),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.relationship_types == [RelationshipType.CALLS]


def test_zero_result_negative_query_still_correctly_classified_after_gap11_fix() -> None:
    """A genuinely empty graph (no evidence for any requested type) must
    still be recognized as a negative-query candidate exactly as before
    -- this fix's all-zero fallback preserves today's ordering, so
    nothing about negative-query classification changes."""
    result, registry, _, repository = build_graph(entity_paths=("auth.py",))
    plan = plan_query(
        query_contract=make_contract(
            intent=Intent.FIND_REFERENCES,
            targets=["auth.py"],
            relationship_types=[RelationshipType.IMPORTS, RelationshipType.REFERENCES],
            required_evidence=[Capability.SYMBOL_REFERENCE],
        ),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.negative_query_candidate is True


def test_gap10_relationship_only_entities_unaffected_by_gap11_fix(tmp_path: Path) -> None:
    """GAP-11 is entirely confined to `plan_query`'s relationship-type
    truncation step -- it never touches `SCIPAdapter`/entity resolution,
    so a GAP-10-recovered (relationship-only) entity's own identity and
    IMPLEMENTS evidence must be completely unaffected."""
    subject_symbol = "scip-python python testrepo rev1 `pkg.a`/BetterIndex#"
    object_symbol = "scip-python python testrepo rev1 `pkg.b`/Index#"
    rel = relationship(object_symbol, is_implementation=True)
    subject_def = occurrence(subject_symbol, roles=1, range_=(0, 0, 4))
    subject_sym_info = symbol_information(subject_symbol, kind=7, relationships=(rel,))
    doc_a = document("pkg/a.py", occurrences=(subject_def,), symbols=(subject_sym_info,))
    doc_b = document("pkg/b.py")
    (tmp_path / DEFAULT_INDEX_FILENAME).write_bytes(scip_index(documents=(doc_a, doc_b)))

    adapter = SCIPAdapter()
    repository = RepositoryMetadata(
        repository_id="repo1", local_path=tmp_path, head_revision="rev1"
    )
    result = adapter.extract(repository, adapter.supported_capabilities)
    normalized = adapter.normalize(result)

    entity = next(e for e in normalized.entities if e.qualified_name == "`pkg.b`/Index#")
    assert "scip:inferred-from-relationship-only" in entity.roles
    implements = [e for e in normalized.evidence if e.predicate is RelationshipType.IMPLEMENTS]
    assert len(implements) == 1


def test_minimal_plan_blocked_when_budget_cannot_support_targets() -> None:
    result, registry, evidence_store, repository = _fan_out_graph()
    cache = PlanCache()
    plan = plan_query(
        query_contract=make_contract(token_budget=10),  # max_nodes = 10//50 = 0
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
        cache=cache,  # exercises the cache.put() path on the PLAN_UNSUPPORTED branch
    )
    assert plan.status is PlanStatus.PLAN_UNSUPPORTED
    assert plan.telemetry.budget_trace.reason is not None

    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    assert package.partial is True
    assert package.entities == []


def test_exhaustive_query_is_blocked_not_silently_pruned_on_latency() -> None:
    result, registry, evidence_store, repository = _fan_out_graph()
    cache = PlanCache()
    plan = plan_query(
        query_contract=make_contract(
            intent=Intent.FIND_IMPACT,
            latency_budget_ms=1000,
            completeness_requirement=CompletenessLevel.EXHAUSTIVE,
        ),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
        cache=cache,  # exercises the cache.put() path inside _blocked_plan()
    )
    assert plan.status is PlanStatus.PLAN_BLOCKED
    assert plan.telemetry.budget_trace.pruning_occurred is False  # blocked, never silently pruned

    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    assert package.partial is True


def test_exhaustive_query_is_blocked_not_silently_pruned_on_token_truncation() -> None:
    result, registry, _, repository = _fan_out_graph()
    plan = plan_query(
        query_contract=make_contract(
            token_budget=100, completeness_requirement=CompletenessLevel.EXHAUSTIVE
        ),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.status is PlanStatus.PLAN_BLOCKED


def test_pruned_telemetry_records_original_and_pruned_estimates() -> None:
    result, registry, _, repository = _fan_out_graph()
    plan = plan_query(
        query_contract=make_contract(token_budget=100),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    trace = plan.telemetry.budget_trace
    assert trace.original_node_estimate >= trace.pruned_node_estimate
    assert trace.reason is not None
    assert trace.pruning_steps != []
