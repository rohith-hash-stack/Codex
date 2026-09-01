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
from codex.ontology.entities import BaseEntityType, RepositorySymbol, SourceLocation
from codex.ontology.relationships import RelationshipType
from codex.planner.cache import PlanCache
from codex.planner.models import PlanStatus
from codex.planner.planner import execute_query, plan_query
from codex.planner.retrieval import resolve_targets
from codex.provider.capability import Capability
from codex.query_understanding.models import CompletenessLevel, Intent, QueryContract
from codex.resolution.entity_resolver import resolve_entities
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


# --- Finding 2 (external GitHub real-repository readiness audit): the
# literal target entity can be excluded by canonical-id-only truncation
# under extreme name collision -- exact-bare-name-match entities must sort
# ahead of substring-only matches before `plan_query`'s existing budget
# slice ever runs. -----------------------------------------------------------


def _seed_extreme_name_collision_graph(
    *, decoy_count: int, exact_count: int
) -> InMemoryGraphStore:
    """Reproduces the real audit finding's exact shape: a bare target
    ("add") with a handful of genuinely distinct real entities sharing
    that exact bare name, drowned in a much larger set of entities that
    only substring-match on `qualified_name` (real example: a vendored
    third-party stub tree swept into a SCIP index). Decoy `canonical_id`s
    are deliberately chosen to sort *before* every exact entity's, so the
    pre-refinement canonical-id-only truncation would keep only decoys."""
    store = _store()
    for i in range(decoy_count):
        store.upsert_entity(
            _entity(
                canonical_id=f"aaa_decoy_{i:05d}",
                name=f"AddHelperVariant{i}",
                qualified_name=f"vendor/stubs/pkg{i}/add_something.pyi",
            )
        )
    for i in range(exact_count):
        store.upsert_entity(
            _entity(
                canonical_id=f"zzz_real_add_{i}",
                name="add",
                qualified_name=f"src/real_module_{i}.py::add",
            )
        )
    return store


def test_exact_bare_name_match_survives_extreme_over_budget_collision() -> None:
    """The real audit reproduction: a query target ("add") resolves 7
    exact real entities plus 1,930 further substring-only matches (the
    exact numbers `sourcegraph/scip-python`, an independently selected
    real repository, produced) -- all 1,937 candidates exceed `max_nodes`
    (80, from `token_budget=4000`), and canonical-id-only truncation would
    keep zero of the 7 real `add` entities (every decoy's id sorts first).
    With this refinement, all 7 real exact matches survive the cut."""
    store = _seed_extreme_name_collision_graph(decoy_count=1930, exact_count=7)
    resolved = resolve_targets(store, ["add"])
    assert len(resolved) == 1937
    truncated = resolved[:80]
    exact_survivors = [e for e in truncated if e.name == "add"]
    assert len(exact_survivors) == 7
    assert {e.canonical_id for e in exact_survivors} == {f"zzz_real_add_{i}" for i in range(7)}


def test_exact_bare_name_matches_sort_before_substring_only_matches() -> None:
    """Direct proof of the new ordering rule: every exact-bare-name-match
    entity sorts ahead of every substring-only entity, regardless of
    `canonical_id` -- `canonical_id` remains the tie-break *within* each
    group, unchanged."""
    store = _seed_extreme_name_collision_graph(decoy_count=10, exact_count=3)
    resolved = resolve_targets(store, ["add"])
    assert [e.name for e in resolved[:3]] == ["add", "add", "add"]
    assert [e.canonical_id for e in resolved[:3]] == sorted(
        f"zzz_real_add_{i}" for i in range(3)
    )
    assert [e.canonical_id for e in resolved[3:]] == sorted(f"aaa_decoy_{i:05d}" for i in range(10))


def test_no_unrelated_candidate_incorrectly_promoted() -> None:
    """A decoy whose `name` merely *contains* the target as a substring
    (never an exact match) must never be promoted ahead of the
    canonical-id order it already had -- only a true, case-insensitive
    exact bare-name match earns the new priority."""
    store = _store()
    store.upsert_entity(
        _entity(canonical_id="z_substring_only", name="add_something", qualified_name="pkg/a.py")
    )
    store.upsert_entity(
        _entity(canonical_id="a_exact", name="add", qualified_name="pkg/b.py::add")
    )
    resolved = resolve_targets(store, ["add"])
    # The exact match ("a_exact") sorts first despite its canonical_id
    # also sorting first here -- construct a second case where canonical_id
    # order alone would put the substring-only decoy first, to prove the
    # promotion is driven by the exact-name check, not accidental id order.
    store2 = _store()
    store2.upsert_entity(
        _entity(canonical_id="a_substring_only", name="add_something", qualified_name="pkg/a.py")
    )
    store2.upsert_entity(
        _entity(canonical_id="z_exact", name="add", qualified_name="pkg/b.py::add")
    )
    resolved2 = resolve_targets(store2, ["add"])
    assert [e.canonical_id for e in resolved2] == ["z_exact", "a_substring_only"]
    assert resolved[0].canonical_id == "a_exact"


def test_exact_bare_name_preference_is_case_insensitive_matching_existing_convention() -> None:
    """Matches `_resolve_one_target`'s own established case-insensitive
    exact-match convention for the `qualified_name` axis -- the new
    `name`-axis preference uses the same `.lower()` comparison, not a
    second, inconsistent case-sensitivity rule."""
    store = _store()
    store.upsert_entity(
        _entity(canonical_id="aaa_decoy", name="AddSomething", qualified_name="pkg/a.py")
    )
    store.upsert_entity(_entity(canonical_id="zzz_exact", name="Add", qualified_name="pkg/b.py"))
    resolved = resolve_targets(store, ["add"])
    assert resolved[0].canonical_id == "zzz_exact"


def test_qualified_name_exact_match_preference_still_applies_first() -> None:
    """Tier 1 (`_resolve_one_target`'s own exact-`qualified_name`
    narrowing, per target, unchanged) still runs before this refinement's
    combined-set bare-name ordering -- for a target whose `qualified_name`
    exact match already narrows things to one entity, the new bare-name
    tier has nothing left to reorder."""
    store = _store()
    exact_qn = _entity(canonical_id="repo-entity", name="repo1", qualified_name="repo1")
    store.upsert_entity(exact_qn)
    for i in range(50):
        store.upsert_entity(
            _entity(
                canonical_id=f"decoy{i}",
                name=f"thing{i}",
                qualified_name=f"src/repo1/module{i}.py",
            )
        )
    resolved = resolve_targets(store, ["repo1"])
    assert [e.canonical_id for e in resolved] == ["repo-entity"]


def test_exact_bare_name_preference_deterministic_across_repeated_calls() -> None:
    store = _seed_extreme_name_collision_graph(decoy_count=200, exact_count=7)
    runs = [resolve_targets(store, ["add"]) for _ in range(3)]
    ids = [[e.canonical_id for e in r] for r in runs]
    assert ids[0] == ids[1] == ids[2]


def test_negative_query_unaffected_by_exact_bare_name_preference() -> None:
    """An empty result set (a genuinely nonexistent symbol) is unaffected
    by the new ordering -- it is a no-op on an already-empty list, exactly
    like the existing qualified-name exact-match preference already is."""
    store = _seed_extreme_name_collision_graph(decoy_count=20, exact_count=2)
    resolved = resolve_targets(store, ["totallyNonexistentSymbolXyzzy"])
    assert resolved == []


def test_extreme_collision_full_plan_query_pipeline_keeps_exact_matches() -> None:
    """Full `plan_query`/`execute_query` reproduction: the same extreme
    collision shape as the real audit ("add", 1,937 raw candidates,
    `max_nodes=80`), through the complete, unmodified planning/execution
    pipeline -- the plan's own `target_entity_ids` (what `execute_query`
    actually seeds traversal from) contains real `add`-named entities,
    not only decoys, and the existing `BudgetTrace`/pruning-step/
    `PlanStatus.PRUNED` semantics are completely unchanged."""
    entity_paths = tuple(f"AddHelperVariant{i}" for i in range(90))
    result, registry, evidence_store, repository = build_graph(entity_paths=entity_paths)
    for i in range(7):
        result.graph_store.upsert_entity(
            _entity(
                canonical_id=f"zzz_real_add_{i}",
                name="add",
                qualified_name=f"src/real_module_{i}.py::add",
            )
        )
    plan = plan_query(
        query_contract=make_contract(targets=["add"], token_budget=4000),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.status is PlanStatus.PRUNED
    assert len(plan.target_entity_ids) == 80
    assert plan.telemetry.budget_trace.pruning_steps[0] == (
        "reduce target-entity set to budget (97 -> 80)"
    )
    surviving_names = {
        result.graph_store.get_entity(cid).name for cid in plan.target_entity_ids
    }
    assert "add" in surviving_names
    surviving_exact_ids = {cid for cid in plan.target_entity_ids if cid.startswith("zzz_real_add_")}
    assert len(surviving_exact_ids) == 7
    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    assert package.entities != []


# --- Symbol Name Merge Normalization + Finding 2, composed: the exact real
# `callSite1.py::add()` scenario end to end -----------------------------------


def test_name_normalized_add_entity_survives_finding_2_budget_truncation() -> None:
    """The real `sourcegraph/scip-python` audit's exact scenario, chained
    through *both* fixes: `AstCallsAdapter`'s bare `add` and `SCIPAdapter`'s
    decorated `add().` raw entities are first converged through the real,
    unmodified `codex.resolution.entity_resolver.resolve_entities` (giving
    the merged entity `name == "add"`, per this directive's fix), then
    seeded into a graph alongside ~1,930 unrelated substring-colliding
    decoys and 6 further same-file-shaped-but-different-location `add`-
    named entities (mirroring the real audit's 7-exact-entity count),
    and finally resolved through `plan_query`'s real budget truncation
    (Finding 2, unmodified). Before this directive's fix, the merged
    entity's `name` would have been `add().` (or `add`, arbitrarily,
    depending on canonical-id hash order) -- an entity whose bare `name`
    is deterministically `add` is what Finding 2's exact-match preference
    needs to keep it inside the 80-node budget, and this proves the whole
    real chain now delivers that."""
    ast_add = _entity(
        canonical_id="raw-ast-add",
        name="add",
        qualified_name="src/callSite1.py::add",
    )
    ast_add = ast_add.model_copy(
        update={
            "source_location": SourceLocation(
                file_path="src/callSite1.py", start_line=3, end_line=3
            ),
            "provider_ids": {"ast_calls": "add"},
        }
    )
    scip_add = _entity(
        canonical_id="raw-scip-add",
        name="add().",
        qualified_name="`mod`/add().",
    )
    scip_add = scip_add.model_copy(
        update={
            "source_location": SourceLocation(
                file_path="src/callSite1.py", start_line=3, end_line=3
            ),
            "provider_ids": {"scip": "add()."},
        }
    )
    resolved = resolve_entities([ast_add, scip_add])
    assert len(resolved.entities) == 1
    merged_add = resolved.entities[0]
    assert merged_add.name == "add"  # this directive's fix

    store = _store()
    store.upsert_entity(merged_add)
    # 6 further real, distinct "add" functions elsewhere (matching the
    # real audit's other 6 exact matches: 2 other real fixture functions,
    # 4 vendored typeshed stubs) -- all bare-named, all genuinely distinct.
    for i in range(6):
        store.upsert_entity(
            _entity(
                canonical_id=f"other-real-add-{i}",
                name="add",
                qualified_name=f"src/other_{i}.py::add",
            )
        )
    # ~1,930 unrelated substring-colliding decoys (the real vendored-stub-
    # tree scale), canonical_ids chosen to sort ahead of the real entries.
    for i in range(1930):
        store.upsert_entity(
            _entity(
                canonical_id=f"aaa_decoy_{i:05d}",
                name=f"AddHelperVariant{i}",
                qualified_name=f"vendor/stubs/pkg{i}/add_something.pyi",
            )
        )

    resolved_targets = resolve_targets(store, ["add"])
    assert len(resolved_targets) == 1937
    truncated = resolved_targets[:80]
    assert merged_add.canonical_id in {e.canonical_id for e in truncated}


def test_name_normalized_add_entity_survives_full_plan_query_pipeline() -> None:
    """Same scenario as above, through the real, unmodified
    `plan_query`/`execute_query` pipeline (not just `resolve_targets`
    directly) -- the converged, name-normalized `add` entity's
    `canonical_id` is present in `plan.target_entity_ids` after budget
    truncation, `PlanStatus` is `PRUNED` (not `PLAN_UNSUPPORTED`), and
    `execute_query` returns real, non-empty results built from it."""
    ast_add = _entity(
        canonical_id="raw-ast-add", name="add", qualified_name="src/callSite1.py::add"
    )
    ast_add = ast_add.model_copy(
        update={
            "source_location": SourceLocation(
                file_path="src/callSite1.py", start_line=3, end_line=3
            ),
            "provider_ids": {"ast_calls": "add"},
        }
    )
    scip_add = _entity(canonical_id="raw-scip-add", name="add().", qualified_name="`mod`/add().")
    scip_add = scip_add.model_copy(
        update={
            "source_location": SourceLocation(
                file_path="src/callSite1.py", start_line=3, end_line=3
            ),
            "provider_ids": {"scip": "add()."},
        }
    )
    resolved = resolve_entities([ast_add, scip_add])
    merged_add = resolved.entities[0]
    assert merged_add.name == "add"

    result, registry, evidence_store, repository = build_graph(entity_paths=())
    result.graph_store.upsert_entity(merged_add)
    for i in range(6):
        result.graph_store.upsert_entity(
            _entity(
                canonical_id=f"other-real-add-{i}",
                name="add",
                qualified_name=f"src/other_{i}.py::add",
            )
        )
    for i in range(1930):
        result.graph_store.upsert_entity(
            _entity(
                canonical_id=f"aaa_decoy_{i:05d}",
                name=f"AddHelperVariant{i}",
                qualified_name=f"vendor/stubs/pkg{i}/add_something.pyi",
            )
        )

    plan = plan_query(
        query_contract=make_contract(targets=["add"], token_budget=4000),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.status is PlanStatus.PRUNED
    assert merged_add.canonical_id in plan.target_entity_ids
    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    assert package.entities != []
