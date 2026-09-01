"""Security/boundary tests for the D7 pyproject.toml Dependency Adapter.

Focus: this adapter parses arbitrary, potentially adversarial TOML from
a repository it does not control. It must never execute anything it
parses, must never let a crafted dependency name collide with or
overwrite the REPOSITORY entity's own identity, and must degrade to
zero-dependencies rather than crash or hang on adversarial structure.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from codex.provider import pyproject_dependency_adapter as module
from codex.provider.pyproject_dependency_adapter import (
    DEFAULT_MANIFEST_FILENAME,
    PyprojectDependencyAdapter,
)
from codex.repository.models import RepositoryMetadata


def make_repository(local_path: Path) -> RepositoryMetadata:
    return RepositoryMetadata(repository_id="repo1", local_path=local_path, head_revision="rev1")


def write_manifest(tmp_path: Path, content: str) -> None:
    (tmp_path / DEFAULT_MANIFEST_FILENAME).write_text(content, encoding="utf-8")


# --- never executes parsed content --------------------------------------------


def test_module_never_calls_eval_exec_or_compile() -> None:
    source = inspect.getsource(module)
    tree = ast.parse(source)
    forbidden = {"eval", "exec", "compile"}
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert called_names & forbidden == set()


def test_toml_content_is_data_only_never_interpreted(tmp_path: Path) -> None:
    """`tomllib` is a pure data parser; a dependency string that *looks*
    like a shell command or Python expression must be treated as inert
    text, never interpreted."""
    write_manifest(
        tmp_path,
        "[project]\nname = 'x'\n"
        "dependencies = [\"$(rm -rf /); os.system('evil')\"]\n",
    )
    adapter = PyprojectDependencyAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    # the malicious string doesn't even parse as a well-formed PEP 508
    # name token (it starts with `$`, not a letter/digit), and regardless,
    # nothing here ever shells out or evaluates it -- no dependency entity
    # is extracted from it at all.
    dependency_names = {e.name for e in norm.entities if e.name != "repo1"}
    assert dependency_names == set()


# --- adversarial / malformed TOML never crashes or hangs ---------------------


def test_deeply_nested_table_does_not_hang(tmp_path: Path) -> None:
    depth = 200
    nested = "".join(f"[a{i}]\n" for i in range(depth))
    write_manifest(tmp_path, f"[project]\nname = 'x'\ndependencies = []\n{nested}")
    adapter = PyprojectDependencyAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert norm.evidence == []


def test_extremely_long_dependency_string_handled(tmp_path: Path) -> None:
    long_name = "x" * 20_000
    write_manifest(tmp_path, f"[project]\nname = 'x'\ndependencies = ['{long_name}']\n")
    adapter = PyprojectDependencyAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    names = {e.name for e in norm.entities if e.name != "repo1"}
    assert names == {long_name}


def test_huge_number_of_dependencies_handled(tmp_path: Path) -> None:
    entries = ", ".join(f"'pkg{i}>=1.0'" for i in range(2000))
    write_manifest(tmp_path, f"[project]\nname = 'x'\ndependencies = [{entries}]\n")
    adapter = PyprojectDependencyAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert len(norm.evidence) == 2000


def test_binary_garbage_manifest_raises_cleanly(tmp_path: Path) -> None:
    from codex.provider.contract import ProviderExtractionError

    (tmp_path / DEFAULT_MANIFEST_FILENAME).write_bytes(bytes(range(256)))
    adapter = PyprojectDependencyAdapter()
    try:
        adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    except ProviderExtractionError:
        pass
    else:
        raise AssertionError("expected ProviderExtractionError for binary-garbage manifest")


# --- namespace confusion: a dependency cannot masquerade as the repo ---------


def test_dependency_named_after_repository_id_does_not_collide(tmp_path: Path) -> None:
    """`repository_id`'s canonical id uses `BaseEntityType.REPOSITORY`;
    a dependency of the same literal name uses `BaseEntityType.
    EXTERNAL_LIBRARY` and the fixed "external" revision sentinel --
    `build_canonical_id` folds `base_type` into the hash, so these can
    never collide regardless of what an attacker names a dependency."""
    write_manifest(tmp_path, "[project]\nname = 'x'\ndependencies = ['repo1']\n")
    adapter = PyprojectDependencyAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert len(norm.entities) == 2  # the REPOSITORY entity and the EXTERNAL_LIBRARY, distinct
    ids = {e.canonical_id for e in norm.entities}
    assert len(ids) == 2


def test_dependency_name_cannot_forge_pypi_scheme_prefix(tmp_path: Path) -> None:
    """A crafted dependency string that itself contains `pypi:` (this
    adapter's own qualified-name scheme prefix) must still resolve to a
    single, ordinary distribution-name token -- `_distribution_name`'s
    PEP 508 grammar stops at the first `:`-illegal character, so the
    literal scheme prefix can never be smuggled through as part of a
    name that would look like a *different* package's identity."""
    write_manifest(tmp_path, "[project]\nname = 'x'\ndependencies = ['pypi:networkx>=1.0']\n")
    adapter = PyprojectDependencyAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    names = {e.name for e in norm.entities if e.name != "repo1"}
    # `:` is not a valid PEP 508 name character -- the regex stops at
    # "pypi", the token before it; this is not "networkx" and is
    # visibly distinguishable from a real `networkx` dependency.
    assert names == {"pypi"}
