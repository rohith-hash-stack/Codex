"""Security/boundary tests for the D7 AST Calls Adapter.

Focus: this adapter parses arbitrary, potentially adversarial Python
source text from a repository it does not control the contents of. It
must never *execute* anything it parses, must never let a crafted
import/call shape escape the repository boundary, and must degrade to
"unresolved" rather than crash or hang on adversarial structure.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from codex.provider import ast_calls_adapter as module
from codex.provider.ast_calls_adapter import AstCallsAdapter
from codex.repository.models import RepositoryMetadata


def make_repository(local_path: Path) -> RepositoryMetadata:
    return RepositoryMetadata(repository_id="repo1", local_path=local_path, head_revision="rev1")


def write(tmp_path: Path, relative: str, source: str) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


# --- never executes parsed source ---------------------------------------------


def test_module_never_calls_eval_exec_or_compile() -> None:
    """AST-level self-check (mirrors `tests/test_planner_boundaries.py`'s
    own import-boundary check pattern): this module's own source must
    never contain a call to `eval`, `exec`, or `compile` -- it only
    ever calls `ast.parse`, which parses without executing."""
    source = inspect.getsource(module)
    tree = ast.parse(source)
    forbidden = {"eval", "exec", "compile"}
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert called_names & forbidden == set()


def test_source_with_dangerous_side_effect_at_import_time_is_never_run(tmp_path: Path) -> None:
    """A module-level statement that would delete files / spawn a process
    if actually executed must have zero effect -- this adapter only
    parses, it never imports or execs the file."""
    marker = tmp_path / "SHOULD_NEVER_EXIST"
    write(
        tmp_path,
        "malicious.py",
        f"import os\nos.system('touch {marker}')\n\n\ndef helper():\n    return 1\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    adapter.normalize(result)
    assert not marker.exists()


# --- import resolution never escapes the repository boundary -----------------


def test_deeply_relative_import_beyond_root_never_resolves(tmp_path: Path) -> None:
    write(tmp_path, "a.py", "def helper():\n    return 1\n")
    write(
        tmp_path,
        "pkg/mod.py",
        "from .......... import something\n\n\ndef caller():\n    return something()\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert norm.evidence == []


def test_resolution_is_pure_dict_lookup_no_filesystem_access_from_import_names(
    tmp_path: Path,
) -> None:
    """A crafted import target naming a real *system* path (e.g.
    `/etc/passwd`-shaped, or absolute-path-looking module segments)
    must never cause a filesystem read outside the discovered file set
    -- resolution is a lookup against `functions_by_module`, built once
    from `_discover_python_files`, never a fresh path access driven by
    import text."""
    write(
        tmp_path,
        "a.py",
        "from etc.passwd import root as helper\n\n\ndef caller():\n    return helper()\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert norm.evidence == []


def test_symlink_style_path_traversal_directory_names_excluded(tmp_path: Path) -> None:
    write(tmp_path, "a.py", "def helper():\n    return 1\n")
    write(
        tmp_path,
        "../outside/evil.py",
        "def helper():\n    return 999\n\n\ndef caller():\n    return helper()\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    # `rglob` never climbs above `root` -- the `../outside` file, if
    # created at all by the test harness, is simply never discovered.
    assert all("outside" not in e.qualified_name for e in norm.entities)


# --- adversarial/malformed source never crashes or hangs ---------------------


def test_extremely_long_identifier_handled(tmp_path: Path) -> None:
    long_name = "x" * 20_000
    write(
        tmp_path,
        "a.py",
        f"def {long_name}():\n    return 1\n\n\ndef caller():\n    return {long_name}()\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert len(norm.evidence) == 1


def test_deeply_nested_expression_does_not_hang(tmp_path: Path) -> None:
    depth = 200
    nested_call = "helper(" * depth + "1" + ")" * depth
    write(
        tmp_path,
        "a.py",
        f"def helper(x):\n    return x\n\n\ndef caller():\n    return {nested_call}\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    # every nested `helper(...)` is a genuine, separately-resolved call
    # site to the same target -- deduplicated to one evidence record.
    assert len(norm.evidence) == 1


def test_null_bytes_in_source_do_not_crash(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_bytes(b"def helper():\n    return 1\x00\n")
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert norm.evidence == []  # a SyntaxError from the embedded NUL -- file skipped, not fatal


def test_binary_garbage_file_with_py_extension_skipped(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_bytes(bytes(range(256)))
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert norm.evidence == []
    assert result.cohort.successful_capabilities != []  # the provider run itself still succeeds


def test_dynamic_call_construction_never_resolved(tmp_path: Path) -> None:
    """`getattr`/dynamic dispatch is exactly the kind of call this adapter
    must not guess at -- confirms it stays unresolved even when the
    dynamic target *happens* to name a real local function."""
    write(
        tmp_path,
        "a.py",
        "def helper():\n    return 1\n\n\n"
        "def caller():\n    return getattr(__import__('sys').modules[__name__], 'helper')()\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    # only the (irrelevant) outer `getattr(...)`/`__import__(...)` calls
    # themselves are candidate call sites, and neither is a `Name`/
    # `self.`/`cls.`/known-module-`Attribute` shape this adapter resolves.
    assert norm.evidence == []


def test_confused_deputy_dependency_name_equal_to_unrelated_symbol_stays_scoped(
    tmp_path: Path,
) -> None:
    """A function named identically to a function in a completely
    unrelated, unimported file must never resolve across that boundary
    -- same-name-different-file is not the same as an explicit import
    binding."""
    write(tmp_path, "a.py", "def helper():\n    return 'real'\n")
    write(
        tmp_path,
        "b.py",
        "def helper():\n    return 'decoy'\n\n\ndef caller():\n    return helper()\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    # `b.py::caller` resolves only to `b.py::helper` (its own module's
    # definition) -- never to `a.py::helper`, despite the identical name.
    by_id = {e.canonical_id: e.qualified_name for e in norm.entities}
    pairs = {(by_id[ev.subject], by_id[ev.object]) for ev in norm.evidence}
    assert pairs == {("b.py::caller", "b.py::helper")}
