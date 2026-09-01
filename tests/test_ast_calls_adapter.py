"""Behavioral tests for the D7 AST Calls Adapter.

Uses handcrafted, tmp_path-based Python source fixtures for precise
control over resolution edge cases (mirrors `tests/test_scip_adapter.py`'s
own precedent of handcrafted fixtures for exactly this reason).
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from codex.evidence.model import CoverageStatus, EvidenceCohort
from codex.evidence.store import InMemoryEvidenceStore
from codex.ingestion.pipeline import IngestionPipeline
from codex.ontology.entities import BaseEntityType
from codex.ontology.relationships import RelationshipType
from codex.provider import ast_calls_adapter as ast_calls_module
from codex.provider.ast_calls_adapter import AstCallsAdapter
from codex.provider.capability import Capability
from codex.provider.contract import (
    EligibilityStatus,
    ExtractionResult,
    ProviderExtractionError,
    ProviderHealthStatus,
)
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile
from codex.repository.models import RepositoryMetadata

PROFILE = ProviderScoreProfile(evidence_quality=0.85, cost_factor=0.3)


def make_repository(local_path: Path, revision: str = "rev1") -> RepositoryMetadata:
    return RepositoryMetadata(repository_id="repo1", local_path=local_path, head_revision=revision)


def write(tmp_path: Path, relative: str, source: str) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def evidence_pairs(norm) -> set[tuple[str, str]]:  # type: ignore[no-untyped-def]
    by_id = {e.canonical_id: e.qualified_name for e in norm.entities}
    return {(by_id[ev.subject], by_id[ev.object]) for ev in norm.evidence}


def pathological_nested_calls(depth: int) -> str:
    """A function body shaped exactly like the real crash this adapter's
    per-file isolation fix targets (Finding 1 of the external GitHub
    real-repository readiness audit): `sourcegraph/scip-python`'s own
    `maxParseDepth2.py` test fixture (`x[0][0][0]...`, nested ~359 deep,
    itself a pyright parser-stress-test) triggered `RecursionError` inside
    `_CallCollector.visit_Call`'s recursive `ast.NodeVisitor.generic_visit`
    chain -- a deeply nested `Call` expression reproduces the identical
    `visit_Call -> generic_visit -> visit -> visit_Call -> ...` recursion
    shape deterministically and without depending on the external clone."""
    return "def pathological():\n    return " + "f(" * depth + "1" + ")" * depth + "\n"


@contextmanager
def lowered_recursion_limit(limit: int) -> Iterator[None]:
    """Temporarily lowers Python's own recursion limit so a *small*, fast
    nested-`Call` fixture reliably reproduces `RecursionError` -- avoiding
    a dependency on exactly how many real stack frames this adapter's own
    recursive visitors consume per AST level (an implementation detail
    that could shift with any future refactor), matching this project's
    own established `sys.setrecursionlimit`-based technique for testing
    recursion-limit-dependent behavior deterministically."""
    original = sys.getrecursionlimit()
    sys.setrecursionlimit(limit)
    try:
        yield
    finally:
        sys.setrecursionlimit(original)


# --- identity / capabilities -------------------------------------------------


def test_identity_and_capabilities() -> None:
    adapter = AstCallsAdapter()
    assert adapter.provider_name == "ast_calls"
    assert adapter.provider_version == "stdlib-ast"
    assert adapter.supported_capabilities == frozenset({Capability.CALL_RELATIONSHIP})
    assert adapter.health_status is ProviderHealthStatus.HEALTHY
    assert adapter.freshness is None


def test_validate_always_ok() -> None:
    assert AstCallsAdapter().validate().ok is True


def test_check_eligibility_missing_directory(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    result = AstCallsAdapter().check_eligibility(make_repository(missing))
    assert result.status is EligibilityStatus.INELIGIBLE_REPOSITORY
    assert result.eligible is False


def test_check_eligibility_directory_present(tmp_path: Path) -> None:
    result = AstCallsAdapter().check_eligibility(make_repository(tmp_path))
    assert result.eligible is True


def test_availability_zero_for_unsupported_capability(tmp_path: Path) -> None:
    adapter = AstCallsAdapter()
    assert adapter.availability(Capability.DEPENDENCY, make_repository(tmp_path)) == 0.0


def test_availability_full_when_eligible(tmp_path: Path) -> None:
    adapter = AstCallsAdapter()
    assert adapter.availability(Capability.CALL_RELATIONSHIP, make_repository(tmp_path)) == 1.0


def test_availability_zero_when_ineligible() -> None:
    adapter = AstCallsAdapter()
    missing = Path("/nonexistent/path/for/this/test")
    assert adapter.availability(Capability.CALL_RELATIONSHIP, make_repository(missing)) == 0.0


# --- resolution: functions, methods, recursion -------------------------------


def test_module_level_function_call_resolved(tmp_path: Path) -> None:
    write(
        tmp_path,
        "a.py",
        "def helper():\n    return 1\n\n\ndef caller():\n    return helper()\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert evidence_pairs(norm) == {("a.py::caller", "a.py::helper")}
    for ev in norm.evidence:
        assert ev.predicate is RelationshipType.CALLS
        assert ev.confidence == 1.0
        assert ev.provider == "ast_calls"


def test_source_location_is_0_based_matching_established_convention(tmp_path: Path) -> None:
    """`SourceLocation`'s convention (closed 2026-08-31,
    `codex.ontology.entities.SourceLocation`'s own docstring) is 0-based,
    half-open, "matches SCIP's own documented `[start, end)` range
    semantics" -- Python's `ast` module is 1-based for line numbers, so
    this adapter must convert. `def helper` is real (1-based) source
    line 1 -- the corrected `start_line` must be 0, not 1."""
    write(tmp_path, "a.py", "def helper():\n    return 1\n\n\ndef caller():\n    return helper()\n")
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    helper = next(e for e in norm.entities if e.qualified_name == "a.py::helper")
    caller = next(e for e in norm.entities if e.qualified_name == "a.py::caller")
    assert helper.source_location is not None
    assert helper.source_location.start_line == 0  # real line 1, 1-based -> 0-based
    assert helper.source_location.end_line == 1  # real line 2 (the `return 1` line)
    assert caller.source_location is not None
    assert caller.source_location.start_line == 4  # real line 5, 1-based -> 0-based


def test_source_location_multiline_definition_end_line_is_0_based(tmp_path: Path) -> None:
    write(
        tmp_path,
        "a.py",
        "def target():\n    return 1\n\n\n"
        "def multi():\n"
        "    x = 1\n"
        "    y = 2\n"
        "    return target()\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    multi = next(e for e in norm.entities if e.qualified_name == "a.py::multi")
    assert multi.source_location is not None
    # real (1-based) lines 5-8 -> 0-based start_line=4, end_line=7
    assert multi.source_location.start_line == 4
    assert multi.source_location.end_line == 7


def test_source_location_columns_unchanged_already_0_based_half_open(tmp_path: Path) -> None:
    """Only line numbers need `- 1`; `ast`'s own `col_offset`/
    `end_col_offset` are already 0-based and half-open (CPython's own
    documented convention), matching `SourceLocation` exactly -- no
    conversion needed or applied."""
    write(tmp_path, "a.py", "def helper():\n    return 1\n\n\ndef caller():\n    return helper()\n")
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    helper = next(e for e in norm.entities if e.qualified_name == "a.py::helper")
    assert helper.source_location is not None
    assert helper.source_location.start_column == 0
    # end_col_offset spans to the end of the *whole definition* (its
    # last body statement, "    return 1"), not just the `def` line --
    # ast's own multi-line-span behavior, unrelated to this fix.
    assert helper.source_location.end_column == len("    return 1")


def test_self_method_call_resolved(tmp_path: Path) -> None:
    write(
        tmp_path,
        "a.py",
        "class Widget:\n"
        "    def start(self):\n"
        "        return self.stop()\n\n"
        "    def stop(self):\n"
        "        return 1\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert evidence_pairs(norm) == {("a.py::Widget.start", "a.py::Widget.stop")}
    method_entities = [e for e in norm.entities if e.base_type is BaseEntityType.METHOD]
    assert len(method_entities) == 2


def test_classmethod_cls_call_resolved(tmp_path: Path) -> None:
    write(
        tmp_path,
        "a.py",
        "class Factory:\n"
        "    @classmethod\n"
        "    def build(cls):\n"
        "        return cls.helper()\n\n"
        "    @classmethod\n"
        "    def helper(cls):\n"
        "        return 1\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert evidence_pairs(norm) == {("a.py::Factory.build", "a.py::Factory.helper")}


def test_recursive_call_is_self_loop(tmp_path: Path) -> None:
    write(
        tmp_path,
        "a.py",
        "def recurse(x):\n    if x <= 0:\n        return 0\n    return recurse(x - 1)\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert evidence_pairs(norm) == {("a.py::recurse", "a.py::recurse")}
    assert len(norm.evidence) == 1


def test_nested_call_both_captured(tmp_path: Path) -> None:
    write(
        tmp_path,
        "a.py",
        "def f(x):\n    return 1\n\n\ndef g(x):\n    return 1\n\n\ndef h():\n    return f(g(1))\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert evidence_pairs(norm) == {("a.py::h", "a.py::f"), ("a.py::h", "a.py::g")}


def test_async_function_and_await_call_resolved(tmp_path: Path) -> None:
    write(
        tmp_path,
        "a.py",
        "async def helper():\n    return 1\n\n\nasync def caller():\n    return await helper()\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert evidence_pairs(norm) == {("a.py::caller", "a.py::helper")}


# --- cross-file resolution via imports ---------------------------------------


def test_from_import_with_alias_resolved(tmp_path: Path) -> None:
    write(tmp_path, "a.py", "def helper():\n    return 1\n")
    write(
        tmp_path,
        "b.py",
        "from a import helper as h\n\n\ndef caller():\n    return h()\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert evidence_pairs(norm) == {("b.py::caller", "a.py::helper")}


def test_import_module_as_alias_attribute_call_resolved(tmp_path: Path) -> None:
    write(tmp_path, "a.py", "def helper():\n    return 1\n")
    write(
        tmp_path,
        "b.py",
        "import a as amod\n\n\ndef caller():\n    return amod.helper()\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert evidence_pairs(norm) == {("b.py::caller", "a.py::helper")}


def test_from_package_import_submodule_resolved(tmp_path: Path) -> None:
    write(tmp_path, "pkg/__init__.py", "")
    write(tmp_path, "pkg/a.py", "def helper():\n    return 1\n")
    write(
        tmp_path,
        "pkg/b.py",
        "from pkg import a\n\n\ndef caller():\n    return a.helper()\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert evidence_pairs(norm) == {("pkg/b.py::caller", "pkg/a.py::helper")}


def test_relative_import_resolved(tmp_path: Path) -> None:
    write(tmp_path, "pkg/__init__.py", "")
    write(tmp_path, "pkg/a.py", "def helper():\n    return 1\n")
    write(
        tmp_path,
        "pkg/b.py",
        "from .a import helper\n\n\ndef caller():\n    return helper()\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert evidence_pairs(norm) == {("pkg/b.py::caller", "pkg/a.py::helper")}


def test_src_layout_import_resolved(tmp_path: Path) -> None:
    """`src/pkg/a.py` is importable as `pkg.a`, not `src.pkg.a` -- the
    common "src layout" convention (both real repositories this adapter
    was validated against use it)."""
    write(tmp_path, "src/pkg/__init__.py", "")
    write(tmp_path, "src/pkg/a.py", "def helper():\n    return 1\n")
    write(
        tmp_path,
        "src/pkg/b.py",
        "from pkg.a import helper\n\n\ndef caller():\n    return helper()\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert evidence_pairs(norm) == {("src/pkg/b.py::caller", "src/pkg/a.py::helper")}


# --- deliberate abstention (directive: never guess) --------------------------


def test_call_on_unknown_name_unresolved(tmp_path: Path) -> None:
    write(tmp_path, "a.py", "def caller():\n    return some_unknown_thing()\n")
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert norm.evidence == []


def test_class_instantiation_not_treated_as_call(tmp_path: Path) -> None:
    write(
        tmp_path,
        "a.py",
        "class Widget:\n"
        "    def __init__(self):\n"
        "        pass\n\n\n"
        "def caller():\n"
        "    return Widget()\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert norm.evidence == []


def test_call_on_arbitrary_attribute_unresolved(tmp_path: Path) -> None:
    write(
        tmp_path,
        "a.py",
        "def caller(obj):\n    return obj.method()\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert norm.evidence == []


def test_inherited_method_not_resolved_no_mro_walk(tmp_path: Path) -> None:
    write(
        tmp_path,
        "a.py",
        "class Base:\n"
        "    def helper(self):\n"
        "        return 1\n\n\n"
        "class Child(Base):\n"
        "    def start(self):\n"
        "        return self.helper()\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    # `helper` is defined on `Base`, not directly on `Child` -- no MRO walk,
    # so this is left unresolved rather than guessed.
    assert norm.evidence == []


def test_call_via_third_party_import_unresolved(tmp_path: Path) -> None:
    write(
        tmp_path,
        "a.py",
        "import os\n\n\ndef caller():\n    return os.getcwd()\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert norm.evidence == []


def test_call_on_result_of_call_only_inner_call_resolved(tmp_path: Path) -> None:
    write(
        tmp_path,
        "a.py",
        "def make():\n    return object()\n\n\ndef caller():\n    return make()()\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    # `make()` itself is a genuine, resolved call site in `caller`'s body.
    # The *outer* `(...)()` call -- invoking whatever `make()` returns --
    # has a `Call` node as its own `func`, not a `Name`/`Attribute`, so it
    # is correctly left unresolved (no type inference attempted).
    assert evidence_pairs(norm) == {("a.py::caller", "a.py::make")}


def test_nested_closure_calls_not_attributed_to_outer_function(tmp_path: Path) -> None:
    write(
        tmp_path,
        "a.py",
        "def target():\n    return 1\n\n\n"
        "def outer():\n"
        "    def inner():\n"
        "        return target()\n"
        "    return inner\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    # `inner`'s call to `target()` is not attributed to `outer` (would be
    # misattribution) and `inner` itself gets no entity (module docstring:
    # "handle nested calls conservatively").
    assert norm.evidence == []
    qualified_names = {e.qualified_name for e in norm.entities}
    assert "a.py::inner" not in qualified_names
    assert "a.py::outer" not in qualified_names  # outer makes no direct calls of its own


def test_module_level_call_outside_any_function_not_represented(tmp_path: Path) -> None:
    write(tmp_path, "a.py", "def helper():\n    return 1\n\n\nresult = helper()\n")
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert norm.evidence == []
    assert {e.qualified_name for e in norm.entities} == set()


def test_lambda_body_calls_not_attributed(tmp_path: Path) -> None:
    write(
        tmp_path,
        "a.py",
        "def target():\n    return 1\n\n\ndef caller():\n    f = lambda: target()\n    return f\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert norm.evidence == []


def test_syntax_error_file_skipped_not_fatal(tmp_path: Path) -> None:
    write(
        tmp_path,
        "good.py",
        "def helper():\n    return 1\n\n\ndef caller():\n    return helper()\n",
    )
    write(tmp_path, "bad.py", "def broken(:\n    pass\n")
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert evidence_pairs(norm) == {("good.py::caller", "good.py::helper")}


# --- Finding 1 (external GitHub real-repository readiness audit): a single
# pathologically deep file must not abort CALLS extraction for the whole
# repository -- isolation is per-file, not per-capability. ------------------


def test_recursion_error_in_one_file_does_not_abort_other_files(tmp_path: Path) -> None:
    """The exact regression this fix closes: before it, one file raising
    `RecursionError` inside `_extract_calls` propagated all the way up to
    `extract()`'s own `try/except`, marking `CALL_RELATIONSHIP` FAILED and
    discarding every other file's real, already-collected call sites too
    -- confirmed against the real `sourcegraph/scip-python` repository
    (1,075 real files, all zero'd out by one pathological fixture). Here,
    `bad.py` (sorted before `good.py`, so the loop must genuinely continue
    past it, not merely stop before reaching it) reproduces that same
    crash; `good.py` is real, ordinary, resolvable source with nothing
    pathological about it."""
    write(tmp_path, "bad.py", pathological_nested_calls(depth=60))
    write(
        tmp_path,
        "good.py",
        "def helper():\n    return 1\n\n\ndef caller():\n    return helper()\n",
    )
    adapter = AstCallsAdapter()
    with lowered_recursion_limit(80):
        result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)

    # The whole-capability outcome: CALL_RELATIONSHIP now succeeds despite
    # the pathological file, unlike the pre-fix behavior (FAILED/PARTIAL).
    assert result.cohort.successful_capabilities == [Capability.CALL_RELATIONSHIP.value]
    assert result.cohort.failed_capabilities == []
    assert result.cohort.coverage_status is CoverageStatus.FULL

    norm = adapter.normalize(result)
    # good.py's real call is present -- other files are unaffected.
    assert evidence_pairs(norm) == {("good.py::caller", "good.py::helper")}
    # No fabricated evidence: nothing involving `bad.py`/`pathological` at all.
    assert all("bad.py" not in q and "pathological" not in q for q in
               (e.qualified_name for e in norm.entities))


def test_recursion_error_file_contributes_no_partial_call_sites(tmp_path: Path) -> None:
    """A crashing file's contribution is deterministically all-or-nothing:
    an earlier, real, resolvable call in the *same* file as the crash
    (`earlier` calling `helper`, both defined before `pathological` in
    source order, so their call site would already be collected by the
    time the crash happens) must not leak through just because it was
    gathered before the `RecursionError` -- the whole file is discarded
    together, never a partial, order-dependent subset of it."""
    write(
        tmp_path,
        "bad.py",
        "def helper():\n    return 1\n\n\n"
        "def earlier():\n    return helper()\n\n\n"
        + pathological_nested_calls(depth=60),
    )
    adapter = AstCallsAdapter()
    with lowered_recursion_limit(80):
        result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert norm.evidence == []


def test_recursion_error_isolation_is_deterministic_across_repeated_extraction(
    tmp_path: Path,
) -> None:
    """Two independent extraction runs against the same pathological
    repository state produce byte-identical results -- the same
    determinism guarantee this adapter's other extraction paths already
    give (see `test_deterministic_repeated_extraction_same_ids`-style
    coverage in the real-repository integration tests)."""
    write(tmp_path, "bad.py", pathological_nested_calls(depth=60))
    write(
        tmp_path,
        "good.py",
        "def helper():\n    return 1\n\n\ndef caller():\n    return helper()\n",
    )
    adapter1 = AstCallsAdapter()
    adapter2 = AstCallsAdapter()
    with lowered_recursion_limit(80):
        norm1 = adapter1.normalize(
            adapter1.extract(make_repository(tmp_path), adapter1.supported_capabilities)
        )
        norm2 = adapter2.normalize(
            adapter2.extract(make_repository(tmp_path), adapter2.supported_capabilities)
        )
    assert sorted(e.canonical_id for e in norm1.entities) == sorted(
        e.canonical_id for e in norm2.entities
    )
    assert evidence_pairs(norm1) == evidence_pairs(norm2)


def test_virtualenv_directory_excluded_regardless_of_name(tmp_path: Path) -> None:
    """A virtualenv is detected structurally (a `pyvenv.cfg` marker file),
    not just by a fixed set of conventional names -- an arbitrarily named
    virtualenv directory (`.venv-work`, `env39`, ...) must still be
    excluded, matching the real environment this adapter was validated
    against (`.venv-work` specifically)."""
    write(tmp_path, "a.py", "def helper():\n    return 1\n")
    write(
        tmp_path,
        "env39/lib/injected.py",
        "def helper():\n    return 999\n\n\ndef caller():\n    return helper()\n",
    )
    (tmp_path / "env39" / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert norm.entities == []
    assert norm.evidence == []


def test_unreadable_directory_skipped_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path, "a.py", "def helper():\n    return 1\n\n\ndef caller():\n    return helper()\n")
    write(tmp_path, "locked/b.py", "def other():\n    return 1\n")

    real_iterdir = Path.iterdir

    def flaky_iterdir(self: Path):  # type: ignore[no-untyped-def]
        if self.name == "locked":
            raise OSError("simulated permission denied")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", flaky_iterdir)
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert evidence_pairs(norm) == {("a.py::caller", "a.py::helper")}


def test_excluded_directories_skipped(tmp_path: Path) -> None:
    write(tmp_path, "a.py", "def helper():\n    return 1\n")
    write(
        tmp_path,
        ".venv/lib/injected.py",
        "def helper():\n    return 999\n\n\ndef caller():\n    return helper()\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert norm.evidence == []  # the excluded-directory file is never parsed at all
    assert norm.entities == []


# --- coverage/evidence-cohort behavior ---------------------------------------


def test_no_python_files_gives_full_coverage_zero_evidence(tmp_path: Path) -> None:
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    assert result.cohort.coverage_status is CoverageStatus.FULL
    norm = adapter.normalize(result)
    assert norm.entities == []
    assert norm.evidence == []


def test_unrequested_capability_not_extracted(tmp_path: Path) -> None:
    write(tmp_path, "a.py", "def helper():\n    return 1\n\n\ndef caller():\n    return helper()\n")
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), frozenset())
    assert result.cohort.successful_capabilities == []
    assert result.cohort.coverage_status is CoverageStatus.NONE
    norm = adapter.normalize(result)
    assert norm.evidence == []


def test_freshness_set_after_extraction(tmp_path: Path) -> None:
    adapter = AstCallsAdapter()
    assert adapter.freshness is None
    adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    assert adapter.freshness is not None


# --- pipeline integration: real ingestion path -------------------------------


def test_ingestion_pipeline_materializes_calls_edge(tmp_path: Path) -> None:
    write(tmp_path, "a.py", "def helper():\n    return 1\n\n\ndef caller():\n    return helper()\n")
    registry = CapabilityRegistry()
    registry.register(AstCallsAdapter(), PROFILE)
    pipeline = IngestionPipeline(registry, InMemoryEvidenceStore())
    result = pipeline.run(make_repository(tmp_path))

    relationships = result.graph_store.get_relationships()
    assert len(relationships) == 1
    assert relationships[0].predicate is RelationshipType.CALLS
    entities = result.graph_store.find_entities()
    assert {e.name for e in entities} == {"caller", "helper"}


def test_tested_by_relationship_type_never_emitted(tmp_path: Path) -> None:
    """Directive: 'do not create a separate TESTED_BY provider' /
    'verify that REFERENCES alone can never create TESTED_BY'. This
    adapter never emits REFERENCES either -- it only ever emits CALLS --
    so this is a direct, explicit assertion that TESTED_BY never
    appears among this adapter's own evidence, for any input."""
    write(
        tmp_path,
        "test_a.py",
        "from a import target\n\n\ndef test_target():\n    return target()\n",
    )
    write(tmp_path, "a.py", "def target():\n    return 1\n")
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    predicates = {ev.predicate for ev in norm.evidence}
    assert predicates <= {RelationshipType.CALLS}
    assert RelationshipType.TESTED_BY not in predicates


# --- targeted coverage: private helpers and rarer branches -------------------


def test_strip_py_suffix_non_python_path_returned_unchanged() -> None:
    assert ast_calls_module._strip_py_suffix("README.md") == "README.md"


def test_resolve_relative_import_climbs_above_package_root_returns_none() -> None:
    node = ast.parse("from .... import x").body[0]
    assert isinstance(node, ast.ImportFrom)
    result = ast_calls_module._resolve_relative_import(current_module="pkg.mod", node=node)
    assert result is None


def test_resolve_relative_import_with_no_remaining_base_returns_none() -> None:
    node = ast.parse("from . import x").body[0]
    assert isinstance(node, ast.ImportFrom)
    # `current_module` has only one segment (a top-level module) -- its
    # "containing package" is empty, and a bare `from . import x` (no
    # `node.module`) leaves nothing to anchor the import to.
    result = ast_calls_module._resolve_relative_import(current_module="mod", node=node)
    assert result is None


def test_unresolvable_relative_import_at_repo_root_is_skipped(tmp_path: Path) -> None:
    write(tmp_path, "mod.py", "from . import x\n\n\ndef caller():\n    return x()\n")
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert norm.evidence == []


def test_wildcard_import_never_resolved(tmp_path: Path) -> None:
    write(tmp_path, "a.py", "def helper():\n    return 1\n")
    write(tmp_path, "b.py", "from a import *\n\n\ndef caller():\n    return helper()\n")
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert norm.evidence == []


def test_nested_async_function_boundary_not_descended(tmp_path: Path) -> None:
    write(
        tmp_path,
        "a.py",
        "def target():\n    return 1\n\n\n"
        "async def outer():\n"
        "    async def inner():\n"
        "        return target()\n"
        "    return inner\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert norm.evidence == []


def test_nested_class_inside_method_boundary_not_descended(tmp_path: Path) -> None:
    write(
        tmp_path,
        "a.py",
        "def target():\n    return 1\n\n\n"
        "class Outer:\n"
        "    def make(self):\n"
        "        class Inner:\n"
        "            def run(self):\n"
        "                return target()\n"
        "        return Inner\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    # `Inner.run`'s call is not attributed to `Outer.make` (a different,
    # unrepresented nested scope), and no entity exists for `Inner` at all.
    assert norm.evidence == []
    assert "a.py::Outer.make" not in {e.qualified_name for e in norm.entities}


def test_class_body_non_function_statement_skipped(tmp_path: Path) -> None:
    write(
        tmp_path,
        "a.py",
        "def helper():\n    return 1\n\n\n"
        "class Widget:\n"
        "    CONSTANT = 1\n\n"
        "    def start(self):\n"
        "        return helper()\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert evidence_pairs(norm) == {("a.py::Widget.start", "a.py::helper")}


def test_duplicate_call_site_deduplicated_to_one_evidence(tmp_path: Path) -> None:
    write(
        tmp_path,
        "a.py",
        "def helper():\n    return 1\n\n\ndef caller():\n    helper()\n    return helper()\n",
    )
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    assert len(norm.evidence) == 1
    assert evidence_pairs(norm) == {("a.py::caller", "a.py::helper")}


def test_extract_raises_when_repository_directory_disappears(tmp_path: Path) -> None:
    missing = tmp_path / "gone"
    adapter = AstCallsAdapter()
    with pytest.raises(ProviderExtractionError):
        adapter.extract(make_repository(missing), adapter.supported_capabilities)


def test_extract_isolates_unexpected_internal_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(root: Path):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated internal failure")

    monkeypatch.setattr(ast_calls_module, "_extract_calls", boom)
    write(tmp_path, "a.py", "def helper():\n    return 1\n")
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    assert result.cohort.failed_capabilities == [Capability.CALL_RELATIONSHIP.value]
    assert result.cohort.successful_capabilities == []
    assert result.cohort.coverage_status is CoverageStatus.PARTIAL
    norm = adapter.normalize(result)
    assert norm.entities == []
    assert norm.evidence == []


def test_unreadable_file_skipped_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(
        tmp_path,
        "good.py",
        "def helper():\n    return 1\n\n\ndef caller():\n    return helper()\n",
    )
    write(tmp_path, "bad.py", "def other():\n    return 1\n")

    real_read_text = Path.read_text

    def flaky_read_text(self: Path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self.name == "bad.py":
            raise OSError("simulated unreadable file")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)
    adapter = AstCallsAdapter()
    result = adapter.extract(make_repository(tmp_path), adapter.supported_capabilities)
    norm = adapter.normalize(result)
    # `bad.py` is skipped entirely, not treated as a whole-provider
    # failure -- `good.py`'s own, unrelated call still resolves normally.
    assert evidence_pairs(norm) == {("good.py::caller", "good.py::helper")}


def test_normalize_never_fabricates_entity_for_dangling_call_site() -> None:
    """Boundary test on the `ExtractionResult.raw_payload: Any` crossing
    (TAD invariant #2): a payload naming a call site whose caller/callee
    was never actually recorded in `definitions` must never fabricate an
    entity or evidence for it, whatever produced that payload."""
    cohort = EvidenceCohort(
        provider="ast_calls",
        provider_version="stdlib-ast",
        snapshot_id="rev1",
        source_revision="rev1",
        successful_capabilities=[Capability.CALL_RELATIONSHIP.value],
    )
    result = ExtractionResult(
        cohort=cohort,
        raw_payload={
            "repository_id": "repo1",
            "revision": "rev1",
            "definitions": {},
            "call_sites": [
                ast_calls_module._CallSite(caller="a.py::ghost_caller", callee="a.py::ghost_callee")
            ],
        },
    )
    norm = AstCallsAdapter().normalize(result)
    assert norm.entities == []
    assert norm.evidence == []
