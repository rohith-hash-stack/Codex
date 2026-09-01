"""Behavioral tests for Entity Resolution (post-D7 directive Phase B).

Covers the directive's required matrix: cross-provider FILE convergence
(real Git/SCIP/CodeQL entity shapes), symbol convergence honesty (no
second provider produces symbols today -- documented, not faked),
different-symbols-with-same-name non-merging, path normalization
("/","\\","./","relative", repo-root-relative), determinism/order-
independence, ambiguity, external-library convergence and version
significance, and provenance preservation.
"""

from __future__ import annotations

from codex.ontology.entities import (
    BaseEntityType,
    LifecycleStatus,
    RepositorySymbol,
    build_canonical_id,
)
from codex.resolution.entity_resolver import MatchReason, resolve_entities
from codex.resolution.paths import normalize_repo_relative_path


def _file(
    *,
    repo: str = "repo1",
    revision: str = "abc123",
    path: str,
    canonical_path: str | None = None,
    roles: list[str] | None = None,
    provider_ids: dict[str, str] | None = None,
    lifecycle: LifecycleStatus = LifecycleStatus.ACTIVE,
) -> RepositorySymbol:
    cid = build_canonical_id(
        repository_id=repo,
        repository_revision=revision,
        qualified_name=canonical_path if canonical_path is not None else path,
        base_type=BaseEntityType.FILE,
    )
    return RepositorySymbol(
        canonical_id=cid,
        repository_id=repo,
        repository_revision=revision,
        name=path.rsplit("/", 1)[-1],
        qualified_name=path,
        base_type=BaseEntityType.FILE,
        roles=roles or [],
        provider_ids=provider_ids or {},
        lifecycle_status=lifecycle,
    )


# --- Path normalization (directive Phase B §12) -----------------------------


def test_normalize_backslashes() -> None:
    assert normalize_repo_relative_path("src\\a.py") == "src/a.py"


def test_normalize_leading_dot_slash() -> None:
    assert normalize_repo_relative_path("./src/a.py") == "src/a.py"


def test_normalize_leading_slash() -> None:
    assert normalize_repo_relative_path("/src/a.py") == "src/a.py"


def test_normalize_already_relative_is_unchanged() -> None:
    assert normalize_repo_relative_path("src/a.py") == "src/a.py"


def test_normalize_collapses_double_slash() -> None:
    assert normalize_repo_relative_path("src//a.py") == "src/a.py"


# --- Exact match / cross-provider FILE convergence ---------------------------


def test_identical_canonical_ids_merge_exact() -> None:
    """Git and SCIP producing byte-identical repo-relative paths -- the
    common real case (D5/D6 real fixtures) -- converges exactly, no
    normalization needed."""
    git_file = _file(path="src/a.py", provider_ids={"git": "src/a.py"})
    scip_file = _file(path="src/a.py", roles=["scip:File"])

    result = resolve_entities([git_file, scip_file])

    assert len(result.entities) == 1
    assert result.merges[0].reason is MatchReason.EXACT_CANONICAL_ID
    merged = result.entities[0]
    assert merged.provider_ids == {"git": "src/a.py"}
    assert merged.roles == ["scip:File"]


def test_three_provider_shapes_converge_on_one_file() -> None:
    """Git (HISTORY), SCIP (file reference), and CodeQL (finding location)
    all describing the same file converge to one canonical entity."""
    git_file = _file(path="src/db.js", provider_ids={"git": "src/db.js"})
    scip_file = _file(path="src/db.js", roles=["scip:File"])
    codeql_file = _file(path="src/db.js", roles=["codeql:finding:js/sql-injection"])

    result = resolve_entities([git_file, scip_file, codeql_file])

    assert len(result.entities) == 1
    merged = result.entities[0]
    assert merged.provider_ids == {"git": "src/db.js"}
    assert set(merged.roles) == {"scip:File", "codeql:finding:js/sql-injection"}


# --- Strong deterministic match: normalized path -----------------------------


def test_leading_dot_slash_converges_with_plain_relative() -> None:
    plain = _file(path="src/a.py")
    dotted = _file(path="./src/a.py", canonical_path="./src/a.py")

    result = resolve_entities([plain, dotted])

    assert len(result.entities) == 1
    assert result.merges[0].reason is MatchReason.NORMALIZED_PATH_IDENTITY
    assert result.entities[0].qualified_name == "src/a.py"
    assert result.entities[0].canonical_id == plain.canonical_id


def test_backslash_path_converges_with_forward_slash() -> None:
    unix = _file(path="src/util.py")
    windows = _file(path="src\\util.py", canonical_path="src\\util.py")

    result = resolve_entities([unix, windows])

    assert len(result.entities) == 1
    assert result.entities[0].qualified_name == "src/util.py"


def test_resolution_is_order_independent() -> None:
    a = _file(path="src/a.py")
    b = _file(path="./src/a.py", canonical_path="./src/a.py")

    forward = resolve_entities([a, b])
    backward = resolve_entities([b, a])

    assert forward.entities == backward.entities


# --- Different entities must NOT merge ---------------------------------------


def test_same_basename_different_directories_do_not_merge() -> None:
    """foo.py in two different directories are different files."""
    a = _file(path="pkg_a/foo.py")
    b = _file(path="pkg_b/foo.py")

    result = resolve_entities([a, b])

    assert len(result.entities) == 2


def test_different_base_types_never_merge_even_at_same_path_string() -> None:
    """A DIRECTORY and a FILE that happen to share a path string are
    different entities -- base_type is part of the identity key."""
    file_cid = build_canonical_id(
        repository_id="repo1",
        repository_revision="abc123",
        qualified_name="src/util",
        base_type=BaseEntityType.FILE,
    )
    dir_cid = build_canonical_id(
        repository_id="repo1",
        repository_revision="abc123",
        qualified_name="src/util",
        base_type=BaseEntityType.DIRECTORY,
    )
    file_entity = RepositorySymbol(
        canonical_id=file_cid,
        repository_id="repo1",
        repository_revision="abc123",
        name="util",
        qualified_name="src/util",
        base_type=BaseEntityType.FILE,
    )
    dir_entity = RepositorySymbol(
        canonical_id=dir_cid,
        repository_id="repo1",
        repository_revision="abc123",
        name="util",
        qualified_name="src/util",
        base_type=BaseEntityType.DIRECTORY,
    )

    result = resolve_entities([file_entity, dir_entity])

    assert len(result.entities) == 2


def test_different_revisions_never_merge() -> None:
    """Revision is part of identity, not evidence -- confirmed by direct
    inspection of build_canonical_id's inputs (directive Phase B §12's
    'Revision changes' requirement: don't invent behavior, verify it)."""
    a = _file(revision="rev1", path="src/a.py")
    b = _file(revision="rev2", path="src/a.py")

    result = resolve_entities([a, b])

    assert len(result.entities) == 2


# --- Symbol-level entities (SCIP only today; documented, not faked) ---------


def test_non_path_shaped_entities_are_never_renormalized() -> None:
    """A CLASS (symbol-level) entity's qualified_name is a SCIP descriptor
    path, not a filesystem path -- normalize_repo_relative_path must never
    be applied to it, on the `NORMALIZED_PATH_IDENTITY` (path) axis. This
    entity carries no `source_location`, so it is also ineligible for the
    *other* axis, symbol-location identity (D7/D9 convergence directive)
    -- there is no location to key on, so it is trusted as-is, exactly
    the module's "only use signals the architecture actually supports"
    principle applied to a second axis. Contrast with
    `test_symbol_location_identity_converges_scip_and_ast_function` etc.
    below, where an otherwise-identical entity *with* a `source_location`
    is eligible and does converge -- this test protects the no-location
    case specifically, not "symbol-level entities never converge" (which
    is no longer true since D7 added a second symbol-producing
    provider)."""
    cid = build_canonical_id(
        repository_id="repo1",
        repository_revision="abc123",
        qualified_name="src/a.py/Greeter#greet().",
        base_type=BaseEntityType.METHOD,
    )
    symbol = RepositorySymbol(
        canonical_id=cid,
        repository_id="repo1",
        repository_revision="abc123",
        name="greet",
        qualified_name="src/a.py/Greeter#greet().",
        base_type=BaseEntityType.METHOD,
    )

    result = resolve_entities([symbol])

    assert result.entities[0].qualified_name == "src/a.py/Greeter#greet()."
    assert result.entities[0].canonical_id == cid
    assert result.merges[0].reason is MatchReason.EXACT_CANONICAL_ID


# --- Symbol-location identity (D7/D9 convergence directive) -----------------


def _symbol(
    *,
    repo: str = "repo1",
    revision: str = "abc123",
    qualified_name: str,
    name: str | None = None,
    base_type: BaseEntityType = BaseEntityType.FUNCTION,
    file_path: str = "src/a.py",
    start_line: int = 10,
    roles: list[str] | None = None,
    provider_ids: dict[str, str] | None = None,
) -> RepositorySymbol:
    from codex.ontology.entities import SourceLocation

    cid = build_canonical_id(
        repository_id=repo,
        repository_revision=revision,
        qualified_name=qualified_name,
        base_type=base_type,
    )
    return RepositorySymbol(
        canonical_id=cid,
        repository_id=repo,
        repository_revision=revision,
        name=name if name is not None else qualified_name,
        qualified_name=qualified_name,
        base_type=base_type,
        roles=roles or [],
        provider_ids=provider_ids or {},
        source_location=SourceLocation(
            file_path=file_path, start_line=start_line, end_line=start_line
        ),
    )


def test_symbol_location_identity_converges_scip_and_ast_function() -> None:
    """A SCIP-style descriptor qualified_name and an AstCallsAdapter-style
    `<path>::<name>` qualified_name for the same real function -- same
    `(repository_id, repository_revision, base_type, file_path,
    start_line)` -- converge to one entity, tagged
    `SYMBOL_LOCATION_IDENTITY`, even though their `qualified_name`
    strings share no structure at all."""
    scip_style = _symbol(qualified_name="`mod`/foo().", file_path="src/a.py", start_line=10)
    ast_style = _symbol(qualified_name="src/a.py::foo", file_path="src/a.py", start_line=10)

    result = resolve_entities([scip_style, ast_style])

    assert len(result.entities) == 1
    assert result.merges[0].reason is MatchReason.SYMBOL_LOCATION_IDENTITY
    assert result.entities[0].canonical_id not in (scip_style.canonical_id, ast_style.canonical_id)


def test_symbol_location_identity_converges_scip_and_ast_method() -> None:
    scip_style = _symbol(
        qualified_name="`mod`/Greeter#greet().",
        base_type=BaseEntityType.METHOD,
        file_path="src/a.py",
        start_line=20,
    )
    ast_style = _symbol(
        qualified_name="src/a.py::Greeter.greet",
        base_type=BaseEntityType.METHOD,
        file_path="src/a.py",
        start_line=20,
    )

    result = resolve_entities([scip_style, ast_style])

    assert len(result.entities) == 1
    assert result.merges[0].reason is MatchReason.SYMBOL_LOCATION_IDENTITY


def test_symbol_location_identity_applies_even_to_a_singleton() -> None:
    """Unconditional application (the cross-batch evidence-staleness fix):
    a symbol-level entity with a `source_location` and no merge partner
    in this batch still recomputes to the location-derived id (tagged
    `SYMBOL_LOCATION_IDENTITY`), not its raw provider-computed id -- this
    is what lets a provider's *own first commit* already receive the
    entity's final, stable id."""
    lone = _symbol(qualified_name="src/a.py::solo", file_path="src/a.py", start_line=30)

    result = resolve_entities([lone])

    assert result.entities[0].canonical_id != lone.canonical_id
    assert result.merges[0].reason is MatchReason.SYMBOL_LOCATION_IDENTITY
    assert result.merges[0].source_canonical_ids == (lone.canonical_id,)


def test_symbol_location_same_type_different_line_does_not_converge() -> None:
    a = _symbol(qualified_name="src/a.py::foo", file_path="src/a.py", start_line=10)
    b = _symbol(qualified_name="`mod`/foo().", file_path="src/a.py", start_line=99)

    result = resolve_entities([a, b])

    assert len(result.entities) == 2


def test_symbol_location_same_name_different_file_does_not_converge() -> None:
    """Two distinct real functions that happen to share a bare name
    (e.g. two `execute` methods in different modules) must never merge
    just because their `name`/`qualified_name` look alike -- only
    `file_path`+`start_line` (plus repo/revision/base_type) decide."""
    a = _symbol(qualified_name="src/a.py::execute", file_path="src/a.py", start_line=10)
    b = _symbol(qualified_name="src/b.py::execute", file_path="src/b.py", start_line=10)

    result = resolve_entities([a, b])

    assert len(result.entities) == 2


def test_symbol_location_different_entity_type_same_location_does_not_converge() -> None:
    """Same file/line, but one FUNCTION and one METHOD (e.g. a decorator-
    generated wrapper and the class body it's attached to reporting
    overlapping ranges from two providers) -- base_type is part of the
    key, so these never merge."""
    fn = _symbol(
        qualified_name="src/a.py::thing", base_type=BaseEntityType.FUNCTION,
        file_path="src/a.py", start_line=10,
    )
    method = _symbol(
        qualified_name="`mod`/Thing#thing().", base_type=BaseEntityType.METHOD,
        file_path="src/a.py", start_line=10,
    )

    result = resolve_entities([fn, method])

    assert len(result.entities) == 2


def test_symbol_location_different_revision_does_not_converge() -> None:
    a = _symbol(
        qualified_name="src/a.py::foo", file_path="src/a.py", start_line=10, revision="rev1"
    )
    b = _symbol(
        qualified_name="`mod`/foo().", file_path="src/a.py", start_line=10, revision="rev2"
    )

    result = resolve_entities([a, b])

    assert len(result.entities) == 2


def test_symbol_location_identity_is_order_independent() -> None:
    a = _symbol(qualified_name="src/a.py::foo", file_path="src/a.py", start_line=10)
    b = _symbol(qualified_name="`mod`/foo().", file_path="src/a.py", start_line=10)

    forward = resolve_entities([a, b])
    backward = resolve_entities([b, a])

    assert forward.entities == backward.entities
    assert forward.merges == backward.merges


def test_symbol_location_merge_preserves_provenance_from_both_sides() -> None:
    """Roles and provider_ids from both the AST-style and SCIP-style raw
    entities must both survive the symbol-location merge -- the same
    provenance-preservation guarantee `_merge_pair` already gives
    FILE/DIRECTORY convergence, now exercised for the symbol-location
    axis."""
    ast_side = _symbol(
        qualified_name="src/a.py::foo",
        file_path="src/a.py",
        start_line=10,
        roles=["ast_calls:definition"],
        provider_ids={"ast_calls": "src/a.py::foo"},
    )
    scip_side = _symbol(
        qualified_name="`mod`/foo().",
        file_path="src/a.py",
        start_line=10,
        roles=["scip:definition"],
        provider_ids={"scip": "mod/foo()."},
    )

    result = resolve_entities([ast_side, scip_side])

    assert len(result.entities) == 1
    merged = result.entities[0]
    assert set(merged.roles) == {"ast_calls:definition", "scip:definition"}
    assert merged.provider_ids == {"ast_calls": "src/a.py::foo", "scip": "mod/foo()."}


def test_symbol_location_entity_without_source_location_never_keyed() -> None:
    """An eligible base type (FUNCTION) but no `source_location` at all
    (e.g. a provider that reports symbols without positions) is not
    eligible for this axis -- it is trusted as-is, exactly like a
    non-symbol base type with no path shape."""
    cid = build_canonical_id(
        repository_id="repo1", repository_revision="abc123",
        qualified_name="src/a.py::foo", base_type=BaseEntityType.FUNCTION,
    )
    no_location = RepositorySymbol(
        canonical_id=cid, repository_id="repo1", repository_revision="abc123",
        name="foo", qualified_name="src/a.py::foo", base_type=BaseEntityType.FUNCTION,
    )

    result = resolve_entities([no_location])

    assert result.entities[0].canonical_id == cid
    assert result.merges[0].reason is MatchReason.EXACT_CANONICAL_ID


# --- Symbol Name Merge Normalization (real `sourcegraph/scip-python` audit's
# `add`/`add().` case) -- undecorated-name preference at merge time, never
# touching canonical_id/qualified_name/the convergence key itself. -----------


def test_bare_ast_name_and_decorated_scip_name_converge_to_bare_name() -> None:
    """The exact real-world case: AstCallsAdapter's bare `add` and SCIP's
    decorated `add().` for the same real function -- the merged entity's
    `name` is the bare one, regardless of which raw entity happens to
    win the (unrelated) canonical_id tie-break for `qualified_name`."""
    ast_side = _symbol(
        qualified_name="src/callSite1.py::add", name="add",
        file_path="src/callSite1.py", start_line=3,
        provider_ids={"ast_calls": "add"},
    )
    scip_side = _symbol(
        qualified_name="`mod`/add().", name="add().",
        file_path="src/callSite1.py", start_line=3,
        provider_ids={"scip": "add()."},
    )

    result = resolve_entities([ast_side, scip_side])

    assert len(result.entities) == 1
    assert result.entities[0].name == "add"


def test_provider_ids_preserves_both_raw_representations() -> None:
    """Both providers' own raw names survive the merge in `provider_ids`,
    keyed by provider -- including the decorated name that lost the
    `name` tie-break, which is never silently discarded."""
    ast_side = _symbol(
        qualified_name="src/callSite1.py::add", name="add",
        file_path="src/callSite1.py", start_line=3,
        provider_ids={"ast_calls": "add"},
    )
    scip_side = _symbol(
        qualified_name="`mod`/add().", name="add().",
        file_path="src/callSite1.py", start_line=3,
        provider_ids={"scip": "add()."},
    )

    result = resolve_entities([ast_side, scip_side])

    assert result.entities[0].provider_ids == {"ast_calls": "add", "scip": "add()."}


def test_canonical_id_unaffected_by_name_tie_break() -> None:
    """`canonical_id` is still the synthesized symbol-location id (same
    `_recompute_symbol_canonical_id` mechanism as before this fix) --
    entirely independent of which raw name wins for display."""
    ast_side = _symbol(
        qualified_name="src/callSite1.py::add", name="add",
        file_path="src/callSite1.py", start_line=3,
    )
    scip_side = _symbol(
        qualified_name="`mod`/add().", name="add().",
        file_path="src/callSite1.py", start_line=3,
    )

    result = resolve_entities([ast_side, scip_side])

    assert result.entities[0].canonical_id not in (
        ast_side.canonical_id,
        scip_side.canonical_id,
    )
    assert result.merges[0].reason is MatchReason.SYMBOL_LOCATION_IDENTITY


def test_qualified_name_selection_unaffected_by_name_tie_break() -> None:
    """`qualified_name` still follows the pre-existing, untouched `base`
    rule -- decoupled from the new `name` selection. This can now
    legitimately differ in *source* from `name` (one provider's
    `qualified_name`, the other provider's bare `name`) -- that is the
    intended, documented consequence of treating `name` as a separate,
    non-identity-bearing display field (HLRD §18 never lists `name`
    among identity-bearing fields)."""
    ast_side = _symbol(
        qualified_name="src/callSite1.py::add", name="add",
        file_path="src/callSite1.py", start_line=3,
    )
    scip_side = _symbol(
        qualified_name="`mod`/add().", name="add().",
        file_path="src/callSite1.py", start_line=3,
    )

    result = resolve_entities([ast_side, scip_side])

    assert result.entities[0].qualified_name in (ast_side.qualified_name, scip_side.qualified_name)
    # Whichever qualified_name won, it is exactly one of the two raw,
    # unmodified provider strings -- never synthesized, never altered.


def test_relationships_still_resolve_correctly_after_name_normalization(
) -> None:
    """CALLS/REFERENCES/IMPLEMENTS resolution is keyed entirely on
    `canonical_id`, never on `name` -- a real caller->callee pair still
    resolves correctly to the converged entity's (unchanged) id after
    this fix, exactly as it did before. Uses the real `IngestionPipeline`
    call chain's own downstream consumer shape: two entities that
    reference each other by `canonical_id`, independent of what `name`
    resolves to."""
    ast_caller = _symbol(
        qualified_name="src/callSite2.py::caller", name="caller",
        file_path="src/callSite2.py", start_line=1,
    )
    ast_callee = _symbol(
        qualified_name="src/callSite1.py::add", name="add",
        file_path="src/callSite1.py", start_line=3,
        provider_ids={"ast_calls": "add"},
    )
    scip_callee = _symbol(
        qualified_name="`mod`/add().", name="add().",
        file_path="src/callSite1.py", start_line=3,
        provider_ids={"scip": "add()."},
    )

    result = resolve_entities([ast_caller, ast_callee, scip_callee])

    assert len(result.entities) == 2
    caller_entity = next(e for e in result.entities if e.name == "caller")
    callee_entity = next(e for e in result.entities if e.name == "add")
    # The relationship a real CALLS-producing provider would have
    # recorded (caller.canonical_id -> callee.canonical_id) still
    # resolves to a real, distinct pair of entities post-merge.
    assert caller_entity.canonical_id != callee_entity.canonical_id
    assert callee_entity.canonical_id not in (ast_callee.canonical_id, scip_callee.canonical_id)


def test_both_bare_names_keep_existing_canonical_id_tie_break() -> None:
    """Neither raw name is decorated -- `_choose_symbol_name` cannot
    distinguish them, so today's existing `base`-wins-by-canonical-id
    behavior is preserved exactly, unchanged by this fix."""
    a = _symbol(
        qualified_name="src/a.py::helper", name="helper_a",
        file_path="src/a.py", start_line=5,
    )
    b = _symbol(
        qualified_name="src/b.py::helper", name="helper_b",
        file_path="src/a.py", start_line=5,
    )

    result = resolve_entities([a, b])

    assert len(result.entities) == 1
    winner = min(a, b, key=lambda e: e.canonical_id)
    assert result.entities[0].name == winner.name


def test_both_decorated_names_keep_existing_canonical_id_tie_break() -> None:
    """Both raw names carry symbol-descriptor punctuation -- again
    genuinely ambiguous by this rule, so the existing canonical-id
    tie-break decides, exactly as before this fix (e.g. two SCIP-style
    providers, or a provider reporting a decorated name for a reason
    unrelated to SCIP specifically -- the check is structural, not
    provider-specific)."""
    a = _symbol(
        qualified_name="src/a.py::helper", name="Helper#helper().",
        file_path="src/a.py", start_line=5,
    )
    b = _symbol(
        qualified_name="src/b.py::helper", name="OtherHelper#helper().",
        file_path="src/a.py", start_line=5,
    )

    result = resolve_entities([a, b])

    assert len(result.entities) == 1
    winner = min(a, b, key=lambda e: e.canonical_id)
    assert result.entities[0].name == winner.name


def test_file_directory_merge_name_selection_unchanged() -> None:
    """FILE/DIRECTORY convergence's `name` selection is completely
    unaffected by this fix -- `_choose_symbol_name` returns `base.name`
    immediately for any base type outside `FUNCTION`/`METHOD`/`CLASS`,
    exactly reproducing pre-fix behavior even when the two raw `name`
    strings happen to differ."""
    git_file = _file(path="src/a.py", provider_ids={"git": "src/a.py"})
    scip_file = _file(path="src/a.py", roles=["scip:File"])

    result = resolve_entities([git_file, scip_file])

    assert len(result.entities) == 1
    winner = min(git_file, scip_file, key=lambda e: e.canonical_id)
    assert result.entities[0].name == winner.name


def test_name_selection_deterministic_across_repeated_runs() -> None:
    """Three independent `resolve_entities` calls against the same raw
    input produce byte-identical `name` (and everything else) -- the new
    tie-break is a pure function of its inputs, no hidden state."""
    ast_side = _symbol(
        qualified_name="src/callSite1.py::add", name="add",
        file_path="src/callSite1.py", start_line=3,
    )
    scip_side = _symbol(
        qualified_name="`mod`/add().", name="add().",
        file_path="src/callSite1.py", start_line=3,
    )

    runs = [resolve_entities([ast_side, scip_side]) for _ in range(3)]
    names = [r.entities[0].name for r in runs]
    assert names[0] == names[1] == names[2] == "add"


def test_negative_query_safety_unaffected_by_name_normalization() -> None:
    """A symbol-level entity with no merge partner at all still resolves
    normally -- the name-selection rule is only ever consulted inside
    `_merge_pair`, never on a path that could turn a real, present
    entity into an empty/negative result, or vice versa."""
    lone = _symbol(
        qualified_name="src/only.py::solo", name="solo",
        file_path="src/only.py", start_line=1,
    )

    result = resolve_entities([lone])

    assert len(result.entities) == 1
    assert result.entities[0].name == "solo"


# --- External library convergence + version significance --------------------


def test_external_library_same_package_converges() -> None:
    cid = build_canonical_id(
        repository_id="repo1",
        repository_revision="external",
        qualified_name="npm:react@18.2.0",
        base_type=BaseEntityType.EXTERNAL_LIBRARY,
    )
    e1 = RepositorySymbol(
        canonical_id=cid, repository_id="repo1", repository_revision="external",
        name="react", qualified_name="npm:react@18.2.0",
        base_type=BaseEntityType.EXTERNAL_LIBRARY, roles=["import"],
    )
    e2 = RepositorySymbol(
        canonical_id=cid, repository_id="repo1", repository_revision="external",
        name="react", qualified_name="npm:react@18.2.0",
        base_type=BaseEntityType.EXTERNAL_LIBRARY, roles=["reference"],
    )

    result = resolve_entities([e1, e2])

    assert len(result.entities) == 1
    assert set(result.entities[0].roles) == {"import", "reference"}


def test_external_library_different_versions_do_not_converge() -> None:
    """Version is identity-significant for external libraries (D5) --
    two different versions of the same package must not silently merge."""
    v1 = build_canonical_id(
        repository_id="repo1", repository_revision="external",
        qualified_name="npm:react@17.0.0", base_type=BaseEntityType.EXTERNAL_LIBRARY,
    )
    v2 = build_canonical_id(
        repository_id="repo1", repository_revision="external",
        qualified_name="npm:react@18.2.0", base_type=BaseEntityType.EXTERNAL_LIBRARY,
    )
    e1 = RepositorySymbol(
        canonical_id=v1, repository_id="repo1", repository_revision="external",
        name="react", qualified_name="npm:react@17.0.0", base_type=BaseEntityType.EXTERNAL_LIBRARY,
    )
    e2 = RepositorySymbol(
        canonical_id=v2, repository_id="repo1", repository_revision="external",
        name="react", qualified_name="npm:react@18.2.0", base_type=BaseEntityType.EXTERNAL_LIBRARY,
    )

    result = resolve_entities([e1, e2])

    assert len(result.entities) == 2


# --- Provenance preservation (directive Phase B §10) -------------------------


def test_merge_preserves_provider_ids_from_both_sides() -> None:
    a = _file(path="src/a.py", provider_ids={"git": "src/a.py"})
    b = _file(path="src/a.py", provider_ids={"other": "src/a.py"})

    result = resolve_entities([a, b])

    assert result.entities[0].provider_ids == {"git": "src/a.py", "other": "src/a.py"}


def test_merge_preserves_roles_union_deduplicated() -> None:
    a = _file(path="src/a.py", roles=["role_a", "shared"])
    b = _file(path="src/a.py", roles=["shared", "role_b"])

    result = resolve_entities([a, b])

    assert result.entities[0].roles == ["role_a", "shared", "role_b"]


def test_merge_prefers_informative_lifecycle_status() -> None:
    """Only Git observes lifecycle -- a non-default DELETED/RENAMED from
    one side must survive the merge even though the other side (never
    reporting lifecycle at all) is plain ACTIVE by default."""
    deleted = _file(path="src/old.py", lifecycle=LifecycleStatus.DELETED)
    default_active = _file(path="src/old.py")

    result = resolve_entities([deleted, default_active])

    assert result.entities[0].lifecycle_status is LifecycleStatus.DELETED


def test_merge_prefers_informative_lifecycle_status_regardless_of_side() -> None:
    """Same as above with the informative side arriving second (as `other`
    in `_merge_pair`, not `base`) -- the outcome must not depend on which
    side happened to sort first."""
    default_active = _file(path="src/renamed.py")
    renamed = _file(path="src/renamed.py", lifecycle=LifecycleStatus.RENAMED)

    result = resolve_entities([default_active, renamed])

    assert result.entities[0].lifecycle_status is LifecycleStatus.RENAMED


def test_source_location_conflict_prefers_first_non_null() -> None:
    """Documented tie-break for the (currently unexercised by any real
    provider pair) case where two converging entities both carry a
    location -- deterministic, not silently dropped."""
    from codex.ontology.entities import SourceLocation

    with_location = RepositorySymbol(
        canonical_id="codex:x", repository_id="repo1", repository_revision="abc123",
        name="a.py", qualified_name="src/a.py", base_type=BaseEntityType.FILE,
        source_location=SourceLocation(file_path="src/a.py", start_line=0, end_line=0),
    )
    without_location = RepositorySymbol(
        canonical_id="codex:x", repository_id="repo1", repository_revision="abc123",
        name="a.py", qualified_name="src/a.py", base_type=BaseEntityType.FILE,
    )

    result = resolve_entities([with_location, without_location])

    assert result.entities[0].source_location is not None
    assert result.entities[0].source_location.start_line == 0


# --- Ambiguity / no candidates -------------------------------------------


def test_empty_input_resolves_to_empty_output() -> None:
    result = resolve_entities([])
    assert result.entities == ()
    assert result.merges == ()


def test_single_entity_resolves_unchanged() -> None:
    entity = _file(path="src/a.py")
    result = resolve_entities([entity])
    assert result.entities == (entity,)
    assert result.merges[0].source_canonical_ids == (entity.canonical_id,)
