from codex.ontology import (
    BaseEntityType,
    CommonRole,
    RepositorySymbol,
    SourceLocation,
    build_canonical_id,
)


def test_build_canonical_id_is_deterministic() -> None:
    id1 = build_canonical_id(
        repository_id="repo1",
        repository_revision="abc123",
        qualified_name="pkg.module.ClassName",
        base_type=BaseEntityType.CLASS,
        language="python",
    )
    id2 = build_canonical_id(
        repository_id="repo1",
        repository_revision="abc123",
        qualified_name="pkg.module.ClassName",
        base_type=BaseEntityType.CLASS,
        language="python",
    )
    assert id1 == id2
    assert id1.startswith("codex:")


def test_build_canonical_id_distinguishes_different_entities() -> None:
    common = {
        "repository_id": "repo1",
        "repository_revision": "abc123",
        "base_type": BaseEntityType.FUNCTION,
        "language": "python",
    }
    id_a = build_canonical_id(qualified_name="pkg.module.func_a", **common)
    id_b = build_canonical_id(qualified_name="pkg.module.func_b", **common)
    assert id_a != id_b


def test_repository_symbol_roles() -> None:
    symbol = RepositorySymbol(
        canonical_id="codex:abc",
        repository_id="repo1",
        repository_revision="abc123",
        name="LoginController",
        qualified_name="app.controllers.LoginController",
        base_type=BaseEntityType.CLASS,
        roles=[CommonRole.CONTROLLER],
        source_location=SourceLocation(
            file_path="app/controllers.py", start_line=10, end_line=40
        ),
    )
    assert symbol.has_role(CommonRole.CONTROLLER)
    assert not symbol.has_role(CommonRole.SERVICE)
