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


# --- D9 candidate-prioritization refinement (post-Finding-3 external
# audit's "candidate-generation ambiguity" finding): a real identity match
# for a target must not be diluted by a *buried*, mid-identifier substring
# collision (`"classab"` inside `"SubclassableObject"`) -- while every
# boundary-aligned collision Finding 2 and `_resolve_one_target`'s own
# regression-prevention test rely on (`"add()."`, `"AdapterA#extract()."`,
# `"AddHelperVariant0"`, `"InterfaceAB"`, `"TestClass1"`) keeps being
# discovered, unchanged. -------------------------------------------------


def test_exact_bare_name_beats_boundary_and_buried_substring_candidates() -> None:
    """Requirement 1: with all three tiers present for one target, the
    exact match sorts first, then the boundary-aligned match, then the
    buried match -- a strict generalization of Finding 2's own bool key."""
    store = _store()
    store.upsert_entity(_entity(canonical_id="exact", name="ClassAB", qualified_name="pkg/a.py"))
    store.upsert_entity(
        _entity(canonical_id="boundary", name="ClassABHelper", qualified_name="pkg/b.py")
    )
    store.upsert_entity(
        _entity(canonical_id="buried", name="SubclassableObject", qualified_name="pkg/mro.py")
    )
    resolved = resolve_targets(store, ["ClassAB"])
    assert [e.canonical_id for e in resolved] == ["exact", "boundary"]


def test_classab_not_polluted_by_subclassable_object_when_literal_entity_exists() -> None:
    """Requirement 2, the real repository's own shape: `"SubclassableObject"`
    (real qualified_name embeds the class name too, mirroring the real
    `sourcegraph/scip-python` entity exactly) is dropped once the literal
    `ClassAB` entity exists -- it is a buried match (`"classab"` occurs mid-
    word, preceded by the `b` of `"Subclassable"`), not a boundary one."""
    store = _store()
    store.upsert_entity(
        _entity(canonical_id="classab", name="ClassAB#", qualified_name="pkg/abstractClass2.py")
    )
    store.upsert_entity(
        _entity(
            canonical_id="subclassable",
            name="SubclassableObject#",
            qualified_name="pkg/mro3.py`/SubclassableObject#",
        )
    )
    resolved = resolve_targets(store, ["ClassAB"])
    assert {e.canonical_id for e in resolved} == {"classab"}


def test_foo_prefers_foo_over_foo2_and_fooimpl() -> None:
    """Requirement 3: `"Foo2"`/`"FooImpl"` are boundary-aligned (`"foo"` at
    position 0, a real word-initial collision, same shape as Finding 2's
    own `"AddHelperVariant0"`) -- they are *not* buried, so they remain
    discoverable, but sort strictly after the exact `"foo"` match."""
    store = _store()
    store.upsert_entity(_entity(canonical_id="exact-foo", name="foo", qualified_name="pkg/a.py"))
    store.upsert_entity(_entity(canonical_id="foo2", name="Foo2", qualified_name="pkg/b.py"))
    store.upsert_entity(_entity(canonical_id="fooimpl", name="FooImpl", qualified_name="pkg/c.py"))
    resolved = resolve_targets(store, ["foo"])
    assert {e.canonical_id for e in resolved} == {"exact-foo", "foo2", "fooimpl"}
    assert resolved[0].canonical_id == "exact-foo"


def test_interfacea_prefers_interfacea_over_interfaceab() -> None:
    """Requirement 4: `"InterfaceAB"` is boundary-aligned (position 0), so
    it stays discoverable (unlike the buried `ClassAB`/`SubclassableObject`
    case) but sorts after the exact `"InterfaceA"` match."""
    store = _store()
    store.upsert_entity(
        _entity(canonical_id="exact-ia", name="InterfaceA#", qualified_name="pkg/a.py")
    )
    store.upsert_entity(
        _entity(canonical_id="interfaceab", name="InterfaceAB#", qualified_name="pkg/b.py")
    )
    resolved = resolve_targets(store, ["InterfaceA"])
    assert {e.canonical_id for e in resolved} == {"exact-ia", "interfaceab"}
    assert resolved[0].canonical_id == "exact-ia"


def test_testclass_prefers_testclass_over_testclass1_and_testclass2() -> None:
    """Requirement 5: `"TestClass1"`/`"TestClass2"` are boundary-aligned
    (position 0), kept discoverable, sorted after the exact match."""
    store = _store()
    store.upsert_entity(
        _entity(canonical_id="exact-tc", name="TestClass#", qualified_name="pkg/a.py")
    )
    store.upsert_entity(_entity(canonical_id="tc1", name="TestClass1#", qualified_name="pkg/b.py"))
    store.upsert_entity(_entity(canonical_id="tc2", name="TestClass2#", qualified_name="pkg/c.py"))
    resolved = resolve_targets(store, ["TestClass"])
    assert {e.canonical_id for e in resolved} == {"exact-tc", "tc1", "tc2"}
    assert resolved[0].canonical_id == "exact-tc"


def test_qualified_name_exact_match_behavior_unchanged_by_tier_refinement() -> None:
    """Requirement 6: `_resolve_one_target`'s pre-existing exact-
    `qualified_name` narrowing (whole-string equality, unrelated to the new
    tier classification) still reduces a repository-name-shaped collision
    to the one exact match, exactly as before this refinement."""
    store = _store()
    store.upsert_entity(_entity(canonical_id="repo-entity", name="repo1", qualified_name="repo1"))
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


def test_substring_discovery_unchanged_when_no_boundary_or_exact_candidate_exists() -> None:
    """Requirement 7: when *every* candidate for the target is a buried
    match (no tier 0 or tier 1 candidate exists at all to prefer), the
    narrowing never activates -- HLRD §34 discovery is preserved in full,
    exactly as pre-refinement behavior, rather than narrowing a target down
    to nothing."""
    store = _store()
    store.upsert_entity(
        _entity(
            canonical_id="buried-1",
            name="SubclassableObject",
            qualified_name="pkg/a.py",
        )
    )
    store.upsert_entity(
        _entity(
            canonical_id="buried-2",
            name="UnclassAB1e",
            qualified_name="pkg/b.py",
        )
    )
    resolved = resolve_targets(store, ["ClassAB"])
    assert {e.canonical_id for e in resolved} == {"buried-1", "buried-2"}


def test_tier_ordering_deterministic_across_repeated_calls() -> None:
    """Requirement 8."""
    store = _store()
    store.upsert_entity(_entity(canonical_id="exact", name="ClassAB", qualified_name="pkg/a.py"))
    store.upsert_entity(
        _entity(canonical_id="boundary", name="ClassABHelper", qualified_name="pkg/b.py")
    )
    store.upsert_entity(
        _entity(canonical_id="buried", name="SubclassableObject", qualified_name="pkg/c.py")
    )
    runs = [resolve_targets(store, ["ClassAB"]) for _ in range(5)]
    ids = [[e.canonical_id for e in r] for r in runs]
    assert all(run == ids[0] for run in ids[1:])


def test_80_node_budget_behavior_intact_with_buried_match_narrowing() -> None:
    """Requirement 9: full `plan_query` reproduction of the `ClassAB`
    shape -- the buried `SubclassableObject`-style decoy never reaches
    `plan.target_entity_ids` at all now (excluded at candidate-generation,
    before budget truncation), and the plan's own `PlanStatus`/budget
    semantics for the tiny surviving set are otherwise exactly what they
    were before this refinement (`OK`, no pruning needed for 1 entity well
    under `max_nodes`)."""
    result, registry, evidence_store, repository = build_graph(entity_paths=())
    result.graph_store.upsert_entity(
        _entity(canonical_id="classab", name="ClassAB#", qualified_name="pkg/abstractClass2.py")
    )
    result.graph_store.upsert_entity(
        _entity(
            canonical_id="subclassable",
            name="SubclassableObject#",
            qualified_name="pkg/mro3.py`/SubclassableObject#",
        )
    )
    plan = plan_query(
        query_contract=make_contract(
            targets=["ClassAB"],
            intent=Intent.FIND_IMPLEMENTATIONS,
            relationship_types=[RelationshipType.IMPLEMENTS],
            required_evidence=[Capability.IMPLEMENTATION],
        ),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    assert plan.status is PlanStatus.OK
    assert plan.target_entity_ids == ["classab"]


def test_negative_query_unaffected_by_buried_match_narrowing() -> None:
    """Requirement 10: a genuinely nonexistent symbol still resolves to an
    empty target set -- the new narrowing only ever activates when a tier-0
    exact match already exists, so it is a no-op here exactly like every
    earlier refinement in this file."""
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


def test_finding_2_extreme_collision_truncation_still_intact() -> None:
    """Requirement 11: Finding 2's own extreme-collision reproduction is
    unaffected -- every `"AddHelperVariant{i}"` decoy is boundary-aligned
    (`"add"` at position 0 of its own name), not buried, so this
    refinement's narrowing never removes any of them; all 1,937 candidates
    are still returned, and the 7 real exact matches still survive the
    80-node truncation, exactly as Finding 2 established."""
    store = _seed_extreme_name_collision_graph(decoy_count=1930, exact_count=7)
    resolved = resolve_targets(store, ["add"])
    assert len(resolved) == 1937
    truncated = resolved[:80]
    exact_survivors = [e for e in truncated if e.name == "add"]
    assert len(exact_survivors) == 7


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


# --- GAP-1 fix (D13 independent-validation finding): qualified_name's
# file/directory-path segment must never participate in target-string
# matching -- only the symbol-path portion after the last "::" may.
# Reproduced independently on `pytest-dev/pytest` ("approx" -> approx.py,
# "fail" -> failure_demo.py) and `psf/requests` ("models" -> models.py). ----


def test_qualified_name_file_path_does_not_pollute_target_seeds() -> None:
    """Requirement 1: entities that merely live in a file whose path
    contains the target string, but whose own name/symbol-path has no
    real relationship to it, must never be resolved as candidates purely
    from that file-path coincidence (reproduced on `pytest-dev/pytest`'s
    `approx.py`: `_is_bool`/`_recursive_sequence_map` live in the same
    file as the real `approx` function but have nothing to do with it)."""
    store = _store()
    store.upsert_entity(
        _entity(
            canonical_id="real-approx",
            name="approx",
            qualified_name="src/_pytest/approx.py::approx",
        )
    )
    store.upsert_entity(
        _entity(
            canonical_id="unrelated-1",
            name="_is_bool",
            qualified_name="src/_pytest/approx.py::_is_bool",
        )
    )
    store.upsert_entity(
        _entity(
            canonical_id="unrelated-2",
            name="_recursive_sequence_map",
            qualified_name="src/_pytest/approx.py::_recursive_sequence_map",
        )
    )
    resolved = resolve_targets(store, ["approx"])
    assert {e.canonical_id for e in resolved} == {"real-approx"}


def test_no_real_symbol_named_target_and_file_path_only_collision_returns_empty() -> None:
    """Requirement 2, the sharper GAP-1 case: when *no* entity's name
    genuinely matches the target at all, and the only "matches" are
    file-path coincidences (`models.py`), resolution must return nothing
    -- not a confident-looking non-empty candidate set (reproduced on
    `psf/requests`'s `models.py`, where no symbol literally named
    "models" exists anywhere in the repository)."""
    store = _store()
    store.upsert_entity(
        _entity(
            canonical_id="unrelated-1",
            name="prepare_body",
            qualified_name="src/requests/models.py::PreparedRequest.prepare_body",
        )
    )
    store.upsert_entity(
        _entity(
            canonical_id="unrelated-2",
            name="iter_content",
            qualified_name="src/requests/models.py::Response.iter_content",
        )
    )
    resolved = resolve_targets(store, ["models"])
    assert resolved == []


def test_qualified_name_file_path_prefix_word_does_not_pollute_target_seeds() -> None:
    """Requirement 3: a file merely named with the target as a *prefix*
    word (`failure_demo.py` for target `"fail"`) must not sweep in
    unrelated symbols either (reproduced on `pytest-dev/pytest`'s
    `doc/en/example/assertion/failure_demo.py` for target `"fail"`)."""
    store = _store()
    store.upsert_entity(
        _entity(
            canonical_id="real-fail",
            name="fail",
            qualified_name="src/_pytest/outcomes.py::fail",
        )
    )
    store.upsert_entity(
        _entity(
            canonical_id="unrelated",
            name="otherfunc_multi",
            qualified_name="doc/en/example/assertion/failure_demo.py::otherfunc_multi",
        )
    )
    resolved = resolve_targets(store, ["fail"])
    assert {e.canonical_id for e in resolved} == {"real-fail"}


def test_exact_symbol_match_still_resolves_via_qualified_name_symbol_path() -> None:
    """Requirement 4: a genuine exact match on the *symbol* portion of
    `qualified_name` is still tier-0 exact identity after the GAP-1 fix
    -- `_symbol_path` narrows what participates in matching, it does not
    remove genuine matches."""
    store = _store()
    store.upsert_entity(
        _entity(
            canonical_id="exact",
            name="approx",
            qualified_name="src/_pytest/approx.py::approx",
        )
    )
    resolved = resolve_targets(store, ["approx"])
    assert [e.canonical_id for e in resolved] == ["exact"]


def test_boundary_aligned_symbol_name_collision_still_discovered_after_gap1_fix() -> None:
    """Requirement 5: a genuine boundary-aligned identifier collision
    within the symbol-path portion of `qualified_name` (not the file
    path) -- e.g. `check_password_with_timing_attack_mitigation` for
    target `check_password`, the real django/django shape -- remains
    discoverable exactly as D9 intends."""
    store = _store()
    store.upsert_entity(
        _entity(
            canonical_id="exact",
            name="check_password",
            qualified_name="src/pkg/auth.py::check_password",
        )
    )
    store.upsert_entity(
        _entity(
            canonical_id="boundary",
            name="check_password_with_timing_attack_mitigation",
            qualified_name="src/pkg/auth.py::check_password_with_timing_attack_mitigation",
        )
    )
    resolved = resolve_targets(store, ["check_password"])
    assert {e.canonical_id for e in resolved} == {"exact", "boundary"}


def test_buried_substring_in_symbol_path_still_rejected_after_gap1_fix() -> None:
    """Requirement 6: a buried, mid-identifier substring collision within
    the symbol-path portion itself (not the file path) is still
    correctly excluded once a real exact match exists -- the original D9
    `ClassAB`/`SubclassableObject` case, now with a real
    `<file>::<symbol>`-shaped `qualified_name`."""
    store = _store()
    store.upsert_entity(
        _entity(canonical_id="exact", name="ClassAB", qualified_name="pkg/a.py::ClassAB")
    )
    store.upsert_entity(
        _entity(
            canonical_id="buried",
            name="SubclassableObject",
            qualified_name="pkg/mro.py::SubclassableObject",
        )
    )
    resolved = resolve_targets(store, ["ClassAB"])
    assert {e.canonical_id for e in resolved} == {"exact"}


def test_scip_decorated_symbol_path_matching_unaffected_by_gap1_fix() -> None:
    """Requirement 7: SCIP-decorated symbol paths (the `ClassName.method`
    convention living in `qualified_name`'s post-`::` portion) continue
    to match exactly as before -- `_symbol_path` only strips the
    file-path prefix, never the symbol's own decoration."""
    store = _store()
    store.upsert_entity(
        _entity(
            canonical_id="a",
            name="AdapterA#extract().",
            qualified_name="pkg/a.py::AdapterA.extract",
        )
    )
    store.upsert_entity(
        _entity(
            canonical_id="b",
            name="AdapterB#extract().",
            qualified_name="pkg/b.py::AdapterB.extract",
        )
    )
    resolved = resolve_targets(store, ["extract"])
    assert {e.canonical_id for e in resolved} == {"a", "b"}


def test_file_path_separator_never_treated_as_symbol_name_boundary() -> None:
    """Requirement 8: a target string that only occurs immediately after
    a `/` inside the *file-path* portion of `qualified_name` (the exact
    shape that made `"/"` count as a valid boundary for `"approx"` in
    `"src/_pytest/approx.py::_is_bool"` before this fix) must not be
    treated as a boundary-aligned match at all -- the file-path segment
    is excluded from matching entirely, not merely reclassified as
    buried."""
    store = _store()
    store.upsert_entity(
        _entity(canonical_id="exact", name="widget", qualified_name="pkg/widget.py::widget")
    )
    store.upsert_entity(
        _entity(
            canonical_id="filepath-only",
            name="_helper",
            qualified_name="src/widget/module.py::_helper",
        )
    )
    resolved = resolve_targets(store, ["widget"])
    assert {e.canonical_id for e in resolved} == {"exact"}


def test_gap1_fix_deterministic_across_repeated_calls() -> None:
    store = _store()
    store.upsert_entity(
        _entity(
            canonical_id="real-approx",
            name="approx",
            qualified_name="src/_pytest/approx.py::approx",
        )
    )
    for i in range(20):
        store.upsert_entity(
            _entity(
                canonical_id=f"unrelated-{i}",
                name=f"helper_{i}",
                qualified_name=f"src/_pytest/approx.py::helper_{i}",
            )
        )
    runs = [resolve_targets(store, ["approx"]) for _ in range(3)]
    ids = [[e.canonical_id for e in r] for r in runs]
    assert ids[0] == ids[1] == ids[2] == ["real-approx"]


# --- GAP-6 fix (SCIP independent-validation finding): GAP-1's fix
# protected only AstCallsAdapter's `<file>::<symbol>` qualified_name
# shape; SCIPAdapter's qualified_name is SCIP's own raw descriptor path
# (no `"::"` at all), so the original fix's "no separator -> return
# unchanged" fallback left it completely unprotected. Reproduced
# independently on `django/django`'s real SCIP index: "What are the
# implementations of Storage?" resolved 641 candidates (module-path
# collisions via the `` `django.core.files.storage.*`/... `` shape),
# truncating away both real implementations `FileSystemStorage` and
# `InMemoryStorage` entirely; "What are the implementations of Command?"
# resolved 981 candidates for ~57 real implementations, and truncation
# left zero real relationships. Shapes below are the real,
# source-verified SCIP descriptor format (confirmed against the complete
# real django SCIP index, not guessed): a single, optionally
# backtick-quoted module descriptor, `"/"`, then the symbol's own
# `"#"`/`"()."`-suffixed descriptor chain. ----------------------------


def test_scip_module_path_collision_does_not_pollute_target_seeds() -> None:
    """Requirement 1: entities that merely live in a SCIP module whose
    dotted path contains the target string, but whose own symbol
    descriptor has no real relationship to it, must never be resolved as
    candidates purely from that module-path coincidence (reproduced on
    `django/django`'s real `django.core.files.storage.memory` module:
    `TimingMixin` lives there but has nothing to do with `"Storage"`)."""
    store = _store()
    store.upsert_entity(
        _entity(
            canonical_id="real-storage",
            name="Storage",
            qualified_name="`django.core.files.storage.base`/Storage#",
        )
    )
    store.upsert_entity(
        _entity(
            canonical_id="real-filesystemstorage",
            name="FileSystemStorage",
            qualified_name="`django.core.files.storage.filesystem`/FileSystemStorage#",
        )
    )
    store.upsert_entity(
        _entity(
            canonical_id="unrelated-timingmixin",
            name="TimingMixin",
            qualified_name="`django.core.files.storage.memory`/TimingMixin#",
        )
    )
    store.upsert_entity(
        _entity(
            canonical_id="unrelated-dirnode",
            name="InMemoryDirNode",
            qualified_name="`django.core.files.storage.memory`/InMemoryDirNode#",
        )
    )
    resolved = resolve_targets(store, ["Storage"])
    ids = {e.canonical_id for e in resolved}
    assert "unrelated-timingmixin" not in ids
    assert "unrelated-dirnode" not in ids


def test_scip_exact_class_symbol_still_resolves() -> None:
    """Requirement 2: a genuine exact match on a SCIP class descriptor's
    own symbol path is still tier-0 exact identity after the GAP-6 fix."""
    store = _store()
    store.upsert_entity(
        _entity(
            canonical_id="exact",
            name="Storage",
            qualified_name="`django.core.files.storage.base`/Storage#",
        )
    )
    resolved = resolve_targets(store, ["Storage"])
    assert [e.canonical_id for e in resolved] == ["exact"]


def test_scip_method_descriptor_matching_still_resolves() -> None:
    """Requirement 3: a SCIP method descriptor (`Class#method().`) whose
    own symbol path contains the target is still discoverable, exactly
    like `_resolve_one_target`'s pre-existing SCIP-decorated-name test."""
    store = _store()
    store.upsert_entity(
        _entity(
            canonical_id="scalar-eq",
            name="__eq__",
            qualified_name="`src._pytest.approx`/ApproxScalar#__eq__().",
        )
    )
    store.upsert_entity(
        _entity(
            canonical_id="compare-approx",
            name="_compare_approx",
            qualified_name="`src._pytest.approx`/_compare_approx().",
        )
    )
    resolved = resolve_targets(store, ["approx"])
    ids = {e.canonical_id for e in resolved}
    assert "compare-approx" in ids


def test_scip_boundary_aligned_symbol_collision_still_discovered() -> None:
    """Requirement 4: a genuine boundary-aligned identifier collision
    within a SCIP symbol's own descriptor (not its module path) remains
    discoverable exactly as D9 intends -- the real django shape:
    `StorageHandler#`/`StorageSettingsMixin#` both boundary-align on
    `"Storage"` at position 0 of their own class names (this codebase's
    established, character-based boundary rule, unchanged by this fix,
    does *not* treat a camelCase-internal capital as a boundary --
    `FileSystemStorage#`/`InMemoryStorage#` are real, legitimate *buried*
    matches for `"Storage"` under that same pre-existing rule, exactly
    like `SubclassableObject` is buried for `"classab"`; real django
    evidence confirms this doesn't prevent them from being retrieved as
    real `IMPLEMENTS` relationships once the base `Storage#` entity
    itself resolves as a seed -- `bounded_traversal`'s directional
    anchoring collects real edges by their *object*, not by requiring
    every real subclass to also independently resolve as a target)."""
    store = _store()
    store.upsert_entity(
        _entity(
            canonical_id="base",
            name="Storage",
            qualified_name="`django.core.files.storage.base`/Storage#",
        )
    )
    store.upsert_entity(
        _entity(
            canonical_id="handler",
            name="StorageHandler",
            qualified_name="`django.core.files.storage.handler`/StorageHandler#",
        )
    )
    store.upsert_entity(
        _entity(
            canonical_id="mixin",
            name="StorageSettingsMixin",
            qualified_name="`django.core.files.storage.mixins`/StorageSettingsMixin#",
        )
    )
    resolved = resolve_targets(store, ["Storage"])
    assert {e.canonical_id for e in resolved} == {"base", "handler", "mixin"}


def test_scip_buried_symbol_substring_still_rejected() -> None:
    """Requirement 5: a buried, mid-identifier substring collision within
    a SCIP symbol's own descriptor (not its module path) is still
    correctly excluded once a real exact/boundary match exists -- the D9
    `ClassAB`/`SubclassableObject` shape, now with a real SCIP
    `` `module`/Class# `` qualified_name."""
    store = _store()
    store.upsert_entity(
        _entity(
            canonical_id="exact",
            name="ClassAB",
            qualified_name="`pkg.mod`/ClassAB#",
        )
    )
    store.upsert_entity(
        _entity(
            canonical_id="buried",
            name="SubclassableObject",
            qualified_name="`pkg.mro`/SubclassableObject#",
        )
    )
    resolved = resolve_targets(store, ["ClassAB"])
    assert {e.canonical_id for e in resolved} == {"exact"}


def test_astcallsadapter_shape_unaffected_by_gap6_fix() -> None:
    """Requirement 6: `AstCallsAdapter`'s `<file>::<symbol>` shape
    continues to resolve exactly as the original GAP-1 fix left it --
    the GAP-6 generalization only adds a second, SCIP-specific branch,
    it never changes the first (`"::"`-present) branch's behavior."""
    store = _store()
    store.upsert_entity(
        _entity(
            canonical_id="real-approx",
            name="approx",
            qualified_name="src/_pytest/approx.py::approx",
        )
    )
    store.upsert_entity(
        _entity(
            canonical_id="unrelated",
            name="_is_bool",
            qualified_name="src/_pytest/approx.py::_is_bool",
        )
    )
    resolved = resolve_targets(store, ["approx"])
    assert {e.canonical_id for e in resolved} == {"real-approx"}


def test_scip_module_path_only_match_returns_no_candidates() -> None:
    """Requirement 7: a target that matches *only* a SCIP module/path
    segment, with no real symbol of that name anywhere (in either its own
    bare `name` or its symbol-descriptor path), resolves to nothing --
    not a confident-looking non-empty candidate set (the sharper GAP-6
    case, mirroring GAP-1's own `models`/`models.py` finding but for the
    SCIP qualified_name shape). Neither entity's own `name` contains
    `"widgets"` at all -- only the module path does -- so this exercises
    the `qualified_name`-axis filter specifically, not the (always
    unnarrowed) `name` axis."""
    store = _store()
    store.upsert_entity(
        _entity(
            canonical_id="unrelated-1",
            name="Helper",
            qualified_name="`pkg.widgets.internal`/Helper#",
        )
    )
    store.upsert_entity(
        _entity(
            canonical_id="unrelated-2",
            name="Utility",
            qualified_name="`pkg.widgets.internal`/Utility#",
        )
    )
    resolved = resolve_targets(store, ["widgets"])
    assert resolved == []


def test_scip_gap6_fix_deterministic_across_repeated_calls() -> None:
    store = _store()
    store.upsert_entity(
        _entity(
            canonical_id="real-storage",
            name="Storage",
            qualified_name="`django.core.files.storage.base`/Storage#",
        )
    )
    for i in range(20):
        store.upsert_entity(
            _entity(
                canonical_id=f"unrelated-{i}",
                name=f"Helper{i}",
                qualified_name=f"`django.core.files.storage.memory`/Helper{i}#",
            )
        )
    runs = [resolve_targets(store, ["Storage"]) for _ in range(3)]
    ids = [[e.canonical_id for e in r] for r in runs]
    assert ids[0] == ids[1] == ids[2] == ["real-storage"]


def test_symbol_path_scip_shape_splits_on_last_slash() -> None:
    """Direct unit check of `_symbol_path`'s SCIP branch, including the
    nested-class-inside-method shape real django SCIP output produces
    (a local class defined inside a test method) -- only the one
    module-boundary `"/"` is stripped; the full nested descriptor chain
    after it is preserved verbatim."""
    from codex.planner.retrieval import _symbol_path

    assert _symbol_path("`django.core.files.storage.memory`/TimingMixin#") == "TimingMixin#"
    assert (
        _symbol_path(
            "`tests.invalid_models_tests.test_ordinary_fields`/"
            "CharFieldTests#test_choices_named_group_non_pairs().Model#"
        )
        == "CharFieldTests#test_choices_named_group_non_pairs().Model#"
    )
    assert _symbol_path("`django.utils.crypto`/get_random_string().") == "get_random_string()."
    # a FILE entity's own plain path (no "#", no trailing ".") is never split
    assert _symbol_path("django/shortcuts.py") == "django/shortcuts.py"
    assert _symbol_path("django/contrib/gis/db/backends/oracle/operations.py") == (
        "django/contrib/gis/db/backends/oracle/operations.py"
    )


# --- GAP-8 fix: SCIP-aware bare-symbol tier-0 matching ------------------------
#
# First post-freeze improvement cycle against frozen baseline
# 9d62223f5dc645d198fbb22777f6c0da4f5ebc43. GAP-8 (documented in the freeze
# audit, `docs/architecture-conformance-audit.md` §KK.8): a SCIP class
# entity's own `name`/symbol-path always carries SCIP's `"#"` descriptor
# terminator (e.g. `"Storage#"`, never bare `"Storage"`), so it could never
# reach `_match_tier`'s tier-0 exact-identity classification -- only tier-1
# -- which mattered once a query's real candidate set exceeded the fixed
# 80-node truncation budget (`resolve_targets`'s sort key is
# `(tier, canonical_id)`; with every real candidate stuck at tier-1,
# ordering within the truncation cut was effectively arbitrary
# `canonical_id`-based, and real IMPLEMENTS evidence could be lost).


def _seed_scip_over_budget_collision_graph(
    *, decoy_count: int, exact_count: int
) -> InMemoryGraphStore:
    """The real django `"Storage"`/`"Command"` shape: a handful of genuine
    SCIP class entities whose *own* `name` is literally `"Storage#"` (SCIP's
    always-decorated form -- `AstCallsAdapter` never emits class-level
    entities at all, so a class candidate for an IMPLEMENTS query is always
    SCIP-only), drowned in a much larger set of real boundary-aligned but
    non-exact subclasses (`"StorageHandler#"`-shaped -- still real,
    legitimate tier-1 candidates, never buried/tier-2). Decoy `canonical_id`s
    are deliberately chosen to sort *before* every exact entity's, so
    pre-fix (tier-1-for-everyone) canonical-id-only truncation would keep
    only decoys -- exactly `_seed_extreme_name_collision_graph`'s own
    construction, adapted to SCIP's decorated-name shape."""
    store = _store()
    for i in range(decoy_count):
        store.upsert_entity(
            _entity(
                canonical_id=f"aaa_decoy_{i:05d}",
                name=f"StorageVariant{i}#",
                qualified_name=f"`vendor.stubs.pkg{i}`/StorageVariant{i}#",
            )
        )
    for i in range(exact_count):
        store.upsert_entity(
            _entity(
                canonical_id=f"zzz_real_storage_{i}",
                name="Storage#",
                qualified_name=f"`django.contrib.app{i}.storage`/Storage#",
            )
        )
    return store


def test_gap8_bare_scip_symbol_direct_unit_checks() -> None:
    """Direct unit check of `_bare_scip_symbol`, covering all three real
    SCIP descriptor terminators plus the required no-ops: a nested/
    attribute descriptor (only one trailing terminator ever stripped), an
    `AstCallsAdapter` bare identifier (never ends in a SCIP terminator, so
    always a no-op), and a plain string with none of the markers."""
    from codex.planner.retrieval import _bare_scip_symbol

    assert _bare_scip_symbol("storage#") == "storage"
    assert _bare_scip_symbol("get_random_string().") == "get_random_string"
    assert _bare_scip_symbol("command#help.") == "command#help"
    assert _bare_scip_symbol("outer#inner_test().nested#") == "outer#inner_test().nested"
    assert _bare_scip_symbol("approx") == "approx"
    assert _bare_scip_symbol("django/shortcuts.py") == "django/shortcuts.py"


def test_gap8_scip_decorated_class_reaches_tier_zero() -> None:
    """The core hypothesis, direct `_match_tier` proof: a SCIP class
    entity whose own `name` and qualified_name symbol-path are both
    literally `"Storage#"` now classifies as tier-0 exact identity for a
    bare `"Storage"` query target -- before this fix, this was always
    tier-1 (`_has_boundary_aligned_occurrence` only)."""
    from codex.planner.retrieval import _match_tier

    entity = _entity(
        canonical_id="real-storage",
        name="Storage#",
        qualified_name="`django.core.files.storage.base`/Storage#",
    )
    assert _match_tier(entity, {"storage"}) == 0


def test_gap8_nested_or_attribute_descriptor_does_not_reach_tier_zero() -> None:
    """Negative case proving `_bare_scip_symbol` strips *exactly one*
    trailing terminator, never renormalizes a whole descriptor chain: a
    `Command` class's own `help` attribute (`"Command#help."`, real django
    shape) and a nested nested-class descriptor never collapse to a bare
    `"command"` match -- both correctly stay at tier 1 (boundary-aligned,
    since `"command"` still occurs at a boundary), never tier 0."""
    from codex.planner.retrieval import _match_tier

    attribute_entity = _entity(
        canonical_id="command-help-attr",
        name="Command#help.",
        qualified_name="`django.core.management.commands.check`/Command#help.",
    )
    nested_entity = _entity(
        canonical_id="nested-command",
        name="CommandTypes#test_app_command().",
        qualified_name="`tests.admin_scripts.tests`/CommandTypes#test_app_command().",
    )
    assert _match_tier(attribute_entity, {"command"}) == 1
    assert _match_tier(nested_entity, {"command"}) == 1


def test_gap8_scip_decorated_class_promotes_to_tier_zero_via_resolve_targets() -> None:
    """Full `resolve_targets` ordering proof: a SCIP-decorated exact class
    match now sorts ahead of real boundary-aligned (but non-exact)
    subclasses -- exactly `test_exact_bare_name_matches_sort_before_
    substring_only_matches`'s own assertion shape, for SCIP decoration
    instead of a bare `AstCallsAdapter` name."""
    store = _seed_scip_over_budget_collision_graph(decoy_count=10, exact_count=3)
    resolved = resolve_targets(store, ["Storage"])
    assert [e.name for e in resolved[:3]] == ["Storage#", "Storage#", "Storage#"]
    assert [e.canonical_id for e in resolved[:3]] == sorted(
        f"zzz_real_storage_{i}" for i in range(3)
    )
    assert [e.canonical_id for e in resolved[3:]] == sorted(f"aaa_decoy_{i:05d}" for i in range(10))


def test_gap8_multiple_real_classes_sharing_decorated_name_all_reach_tier_zero() -> None:
    """The real django `"Command"` shape: ~60 distinct real classes across
    different management-command modules are all literally named
    `"Command"` (SCIP: `"Command#"`) -- every one of them independently
    reaches tier 0, not just the first one found, since each is its own
    genuine exact match, not a single shared entity."""
    from codex.planner.retrieval import _match_tier

    entities = [
        _entity(
            canonical_id=f"command-{i}",
            name="Command#",
            qualified_name=f"`django.contrib.app{i}.management.commands.foo{i}`/Command#",
        )
        for i in range(5)
    ]
    assert all(_match_tier(e, {"command"}) == 0 for e in entities)


def test_gap8_exact_scip_match_survives_extreme_over_budget_collision() -> None:
    """The real GAP-8 measurement shape reproduced end to end: a bare
    `"Storage"` target resolves a handful of genuine SCIP class exact
    matches plus a much larger set of real boundary-aligned (but
    non-exact) subclasses, all exceeding `max_nodes` (80). Before this
    fix, every candidate was tier-1 and canonical-id-only truncation could
    keep zero of the real exact matches (every decoy's id sorts first, by
    construction); with this fix, all of them survive the cut."""
    store = _seed_scip_over_budget_collision_graph(decoy_count=90, exact_count=5)
    resolved = resolve_targets(store, ["Storage"])
    assert len(resolved) == 95
    truncated = resolved[:80]
    exact_survivors = [e for e in truncated if e.name == "Storage#"]
    assert len(exact_survivors) == 5
    assert {e.canonical_id for e in exact_survivors} == {f"zzz_real_storage_{i}" for i in range(5)}


def test_gap8_astcalls_shape_unaffected() -> None:
    """Regression guard: `_bare_scip_symbol` is a structural no-op for
    every `AstCallsAdapter` bare-identifier shape (no valid Python
    identifier ends in `"#"`/`"()."`/`"."`), so an `AstCallsAdapter`
    entity's tier-0 classification -- already correct before this fix --
    is byte-for-byte unchanged, and `resolve_targets`'s existing
    `"::"`-shape behavior is untouched."""
    from codex.planner.retrieval import _bare_scip_symbol, _match_tier

    entity = _entity(
        canonical_id="real-approx",
        name="approx",
        qualified_name="src/_pytest/approx.py::approx",
    )
    assert _bare_scip_symbol("approx") == "approx"
    assert _match_tier(entity, {"approx"}) == 0

    store = _store()
    store.upsert_entity(entity)
    store.upsert_entity(
        _entity(
            canonical_id="unrelated",
            name="_is_bool",
            qualified_name="src/_pytest/approx.py::_is_bool",
        )
    )
    resolved = resolve_targets(store, ["approx"])
    assert {e.canonical_id for e in resolved} == {"real-approx"}


def test_gap8_negative_query_unaffected() -> None:
    """A target with no real bare or decoration-stripped match anywhere
    still resolves to nothing -- the fix promotes real exact matches, it
    never invents one where none exists."""
    store = _store()
    store.upsert_entity(
        _entity(
            canonical_id="unrelated",
            name="Helper#",
            qualified_name="`pkg.widgets.internal`/Helper#",
        )
    )
    resolved = resolve_targets(store, ["TotallyNonexistentClassXyzzy123"])
    assert resolved == []


def test_gap8_fix_deterministic_across_repeated_calls() -> None:
    store = _seed_scip_over_budget_collision_graph(decoy_count=20, exact_count=3)
    runs = [resolve_targets(store, ["Storage"]) for _ in range(3)]
    ids = [[e.canonical_id for e in r] for r in runs]
    assert ids[0] == ids[1] == ids[2]
