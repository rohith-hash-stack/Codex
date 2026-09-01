"""D9 target-resolution refinement regression tests.

Reproduces the real-repository benchmark's four failures (#12/#13/#22/
#24 -- "What does veyra/codex depend on?", "What calls extract?"/"run?")
as synthetic, portable analogs of the same two underlying mechanisms:

1. A short target string that is also a substring of nearly every
   entity's `qualified_name` (a repository's own name, matched against
   every file path under it) exhausts the budget check before any
   traversal happens, even though exactly one entity's `qualified_name`
   is an *exact* match for it.
2. A common/short symbol name shared, as a real substring, by many
   distinct entities (mirroring how every `ProviderAdapter.extract()`
   method, or every SCIP-decorated symbol containing a short word,
   legitimately matches) can also exceed the budget on its own, with no
   exact match to fall back on.

`_resolve_one_target`/`resolve_targets` (`codex.planner.retrieval`) and
`plan_query`'s target-entity budget check (`codex.planner.planner`) are
exercised directly and through the full `plan_query`/`execute_query`
flow, using the same `DeterministicFakeAdapter`/`build_graph` fixture
every other `codex.planner` test suite already uses.
"""

from __future__ import annotations

from codex.graph.memory_store import InMemoryGraphStore
from codex.graph.version import GraphVersion
from codex.ontology.entities import BaseEntityType, RepositorySymbol
from codex.ontology.relationships import RelationshipType
from codex.planner.cache import PlanCache
from codex.planner.models import PlanStatus
from codex.planner.planner import execute_query, plan_query
from codex.planner.retrieval import resolve_targets
from codex.provider.capability import Capability
from codex.query_understanding.models import CompletenessLevel, Intent, QueryContract
from planner_fixtures import build_graph


def make_contract(**overrides: object) -> QueryContract:
    kwargs: dict[str, object] = {
        "intent": Intent.FIND_CALLERS,
        "targets": ["repo1"],
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


def _entity(*, canonical_id: str, name: str, qualified_name: str) -> RepositorySymbol:
    return RepositorySymbol(
        canonical_id=canonical_id,
        repository_id="repo1",
        repository_revision="rev1",
        name=name,
        qualified_name=qualified_name,
        base_type=BaseEntityType.FUNCTION,
    )


def _store() -> InMemoryGraphStore:
    version = GraphVersion(version_id="v1", repository_id="repo1", repository_revision="rev1")
    return InMemoryGraphStore(version)


# --- unit tests: _resolve_one_target / resolve_targets -----------------------


def test_repository_name_collision_narrows_to_exact_qualified_name_match() -> None:
    """Reproduces benchmark #12/#13: a short target string ("repo1")
    substring-matches nearly every entity's `qualified_name` (a real file
    path under the repo), but exactly one entity's `qualified_name` is
    an exact match for it -- that one entity, and only it, is returned.
    """
    store = _store()
    exact = _entity(canonical_id="repo-entity", name="repo1", qualified_name="repo1")
    store.upsert_entity(exact)
    for i in range(200):
        decoy = _entity(
            canonical_id=f"decoy{i}",
            name=f"thing{i}",
            qualified_name=f"src/repo1/module{i}.py",
        )
        store.upsert_entity(decoy)

    resolved = resolve_targets(store, ["repo1"])
    assert [e.canonical_id for e in resolved] == ["repo-entity"]


def test_short_symbol_name_keeps_full_name_axis_substring_recall() -> None:
    """The `name`-axis substring set is never narrowed by exact-match
    preference (only the `qualified_name` axis is) -- entities whose
    bare name only *contains* the target (mirroring SCIP's own
    `ClassName#method().`-decorated naming, which never produces a bare
    exact match for a callable) must all still be found, exactly as
    before this refinement.
    """
    store = _store()
    decorated_a = _entity(
        canonical_id="a", name="AdapterA#extract().", qualified_name="pkg/a.py::AdapterA.extract"
    )
    decorated_b = _entity(
        canonical_id="b", name="AdapterB#extract().", qualified_name="pkg/b.py::AdapterB.extract"
    )
    plain = _entity(canonical_id="c", name="extract", qualified_name="pkg/c.py::extract")
    store.upsert_entity(decorated_a)
    store.upsert_entity(decorated_b)
    store.upsert_entity(plain)

    resolved = resolve_targets(store, ["extract"])
    assert {e.canonical_id for e in resolved} == {"a", "b", "c"}


def test_ambiguous_target_returns_every_distinct_real_candidate() -> None:
    """Multiple genuinely different entities that exactly share the same
    bare name (a real, common case -- several classes each defining a
    method called `execute`) are all returned, deterministically sorted
    -- none arbitrarily dropped."""
    store = _store()
    for i in range(5):
        store.upsert_entity(
            _entity(
                canonical_id=f"exec{i}",
                name="execute",
                qualified_name=f"pkg/mod{i}.py::execute",
            )
        )

    resolved = resolve_targets(store, ["execute"])
    assert [e.canonical_id for e in resolved] == sorted(f"exec{i}" for i in range(5))
    # deterministic across repeated calls
    assert resolve_targets(store, ["execute"]) == resolved


def test_no_exact_qualified_name_match_keeps_full_substring_set() -> None:
    """When no entity's `qualified_name` is an exact match, the full
    substring set is returned exactly as it always was -- this is a
    narrowing only, never a new way to lose recall a target with no
    exact hit already had."""
    store = _store()
    for i in range(3):
        store.upsert_entity(
            _entity(
                canonical_id=f"partial{i}",
                name=f"partial{i}",
                qualified_name=f"src/pkg/partial_widget_{i}.py",
            )
        )
    resolved = resolve_targets(store, ["partial_widget"])
    assert {e.canonical_id for e in resolved} == {"partial0", "partial1", "partial2"}


def test_exact_match_on_name_axis_alone_does_not_narrow_qualified_name_axis() -> None:
    """An exact `name` match does not, by itself, narrow the
    `qualified_name` axis -- only an exact `qualified_name` match does.
    This is deliberately asymmetric (see `_resolve_one_target`'s own
    docstring): narrowing the `name` axis was tried and reverted after
    it silently dropped real, differently-decorated entities."""
    store = _store()
    store.upsert_entity(
        _entity(canonical_id="exact-name", name="widget", qualified_name="pkg/w.py")
    )
    store.upsert_entity(
        _entity(canonical_id="qn-substr", name="other", qualified_name="pkg/widget_helper.py")
    )
    resolved = resolve_targets(store, ["widget"])
    assert {e.canonical_id for e in resolved} == {"exact-name", "qn-substr"}


# --- integration tests: through plan_query / execute_query -------------------


def _seed_repository_name_collision_graph() -> tuple:
    """Builds the real production shape `DeterministicFakeAdapter` can't:
    `name` and `qualified_name` genuinely differ (every real provider --
    Git/SCIP/AstCallsAdapter -- gives a decoy entity a short bare `name`,
    never its full path). `build_graph(entity_paths=())` supplies valid
    pipeline scaffolding (an `IngestionResult`/registry/repository), and
    the entities that matter for this test are then added directly.
    """
    result, registry, evidence_store, repository = build_graph(entity_paths=())
    exact = _entity(canonical_id="repo-entity", name="repo1", qualified_name="repo1")
    result.graph_store.upsert_entity(exact)
    # 85 decoys alone already exceed max_nodes=80 (token_budget=4000) --
    # if exact-match narrowing did *not* apply, the plan would still see
    # 86 target entities and need Tier-2 truncation (PRUNED). If it does
    # apply, the plan proceeds from the single exact match alone (OK).
    for i in range(85):
        decoy = _entity(
            canonical_id=f"decoy{i}",
            name=f"thing{i}",
            qualified_name=f"src/repo1/module{i}.py",
        )
        result.graph_store.upsert_entity(decoy)
    return result, registry, evidence_store, repository


def test_repository_name_collision_no_longer_plan_unsupported() -> None:
    """Full `plan_query` reproduction of benchmark #12/#13: a repository-
    name-shaped target that used to blow the budget before any
    traversal now produces a real, budget-compliant plan -- and, because
    the exact `qualified_name` match narrows the target set to just one
    entity (85 unrelated decoys never enter it), no Tier-2 truncation is
    even needed (`status is OK`, not `PRUNED`)."""
    result, registry, evidence_store, repository = _seed_repository_name_collision_graph()
    plan = plan_query(
        query_contract=make_contract(targets=["repo1"], token_budget=4000),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.status is PlanStatus.OK
    assert plan.target_entity_ids == ["repo-entity"]
    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    assert package.entities == [result.graph_store.get_entity("repo-entity")]


def test_common_short_name_over_budget_prunes_instead_of_blocking() -> None:
    """Full `plan_query` reproduction of benchmark #22/#24: a common
    short target name with no exact match, whose substring set alone
    exceeds `max_nodes`, is deterministically truncated to the budget
    (a new, explicit pruning step) rather than declared PLAN_UNSUPPORTED
    -- real evidence is still retrieved."""
    # token_budget=4000 -> max_nodes=80 (min(100, 4000//50)); 90 distinct
    # "extract"-substring entities, none an exact match for "extract".
    entity_paths = tuple(f"AdapterX{i}#extract()." for i in range(90))
    result, registry, evidence_store, repository = build_graph(
        entity_paths=entity_paths,
        relationship_pairs=((entity_paths[0], entity_paths[1]),),
    )
    plan = plan_query(
        query_contract=make_contract(targets=["extract"], token_budget=4000),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.status is PlanStatus.PRUNED
    assert len(plan.target_entity_ids) == 80
    assert any(
        step.startswith("reduce target-entity set to budget")
        for step in plan.telemetry.budget_trace.pruning_steps
    )
    assert (
        plan.telemetry.budget_trace.pruning_steps[0]
        == "reduce target-entity set to budget (90 -> 80)"
    )
    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    assert package.entities != []
    assert package.relationships != []


def test_truncation_is_deterministic_across_repeated_calls() -> None:
    entity_paths = tuple(f"Thing{i}#extract()." for i in range(90))
    result, registry, evidence_store, repository = build_graph(entity_paths=entity_paths)
    contract = make_contract(targets=["extract"], token_budget=4000)
    plans = [
        plan_query(
            query_contract=contract,
            graph=result.graph_store,
            ingestion_result=result,
            registry=registry,
            repository=repository,
        )
        for _ in range(3)
    ]
    ids = [p.target_entity_ids for p in plans]
    assert ids[0] == ids[1] == ids[2]


# --- EXHAUSTIVE preservation --------------------------------------------------


def test_exhaustive_query_still_plan_unsupported_when_target_set_exceeds_budget() -> None:
    """TAD §32: 'Exhaustive queries cannot be pruned below required
    coverage.' An EXHAUSTIVE query over an oversized target-entity set
    must get exactly today's unconditional PLAN_UNSUPPORTED -- the new
    deterministic-truncation path must never apply to it."""
    entity_paths = tuple(f"Thing{i}#extract()." for i in range(90))
    result, registry, evidence_store, repository = build_graph(entity_paths=entity_paths)
    plan = plan_query(
        query_contract=make_contract(
            targets=["extract"],
            token_budget=4000,
            completeness_requirement=CompletenessLevel.EXHAUSTIVE,
        ),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.status is PlanStatus.PLAN_UNSUPPORTED
    assert plan.telemetry.budget_trace.pruning_occurred is False
    assert plan.telemetry.budget_trace.reason == (
        "token_budget cannot support even the target entities themselves"
    )


def test_exhaustive_query_also_benefits_from_clean_exact_match_narrowing() -> None:
    """`_resolve_one_target`'s exact-`qualified_name` narrowing (Tier 1)
    runs unconditionally, before `plan_query` ever branches on
    `is_exhaustive` -- an EXHAUSTIVE query with a clean exact match never
    even reaches the over-budget check, because the narrowed target set
    (1 entity) was never over budget to begin with. This is correct, not
    a TAD §32 violation: narrowing to the one entity that *is* the exact
    target discards nothing an EXHAUSTIVE query needed -- the 85 decoys
    were never legitimately part of "repo1"'s required coverage. EXHAUSTIVE
    protection (never truncating an over-budget set) is a Tier-2-only
    concern, covered separately by
    `test_exhaustive_query_still_plan_unsupported_when_target_set_exceeds_budget`,
    whose fixture has no exact match to narrow to."""
    result, registry, evidence_store, repository = _seed_repository_name_collision_graph()
    plan = plan_query(
        query_contract=make_contract(
            targets=["repo1"],
            token_budget=4000,
            completeness_requirement=CompletenessLevel.EXHAUSTIVE,
        ),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.status is PlanStatus.OK
    assert plan.target_entity_ids == ["repo-entity"]


def test_zero_node_budget_still_plan_unsupported_not_truncated_to_empty() -> None:
    """`max_nodes == 0` must never be treated as 'truncate to zero and
    continue' -- an empty seed set is not a smaller-but-viable plan, it
    is exactly TAD §41's 'budget cannot support even a minimally viable
    evidence package.'"""
    result, registry, evidence_store, repository = build_graph(entity_paths=("service.py",))
    cache = PlanCache()
    plan = plan_query(
        query_contract=make_contract(targets=["service.py"], token_budget=10),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
        cache=cache,
    )
    assert plan.status is PlanStatus.PLAN_UNSUPPORTED
    assert plan.telemetry.budget_trace.pruning_occurred is False


# --- negative-query safety ----------------------------------------------------


def test_negative_query_unaffected_by_target_resolution_refinement() -> None:
    """A genuinely nonexistent symbol resolves to an empty target set
    either way -- exact-match narrowing and budget truncation are both
    no-ops on an already-empty set, so negative-query detection is
    unaffected."""
    result, registry, evidence_store, repository = build_graph(entity_paths=("service.py",))
    plan = plan_query(
        query_contract=make_contract(targets=["totallyNonexistentSymbolXyzzy"]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.target_entity_ids == []
    assert plan.negative_query_candidate is True
    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    assert package.entities == []


def test_negative_query_with_repository_name_shaped_target_still_empty_when_absent() -> None:
    """A repository-name-shaped target that genuinely does not exist in
    the graph at all (no entity's `name`/`qualified_name` contains it)
    still resolves to nothing -- exact-match preference only narrows an
    already-non-empty match set, it never fabricates one."""
    result, registry, evidence_store, repository = build_graph(entity_paths=("service.py",))
    resolved = resolve_targets(result.graph_store, ["nonexistent_repo_xyz"])
    assert resolved == []
