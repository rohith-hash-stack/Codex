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
    be applied to it. No second provider produces symbol-level entities
    today (D5 closure audit, §J), so this proves the *absence* of
    accidental cross-provider symbol merging, not a positive convergence
    case that doesn't yet exist."""
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
    assert result.merges[0].reason is MatchReason.EXACT_CANONICAL_ID


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
