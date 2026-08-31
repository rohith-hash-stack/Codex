"""Dependency-boundary tests for `codex.evaluation` (directive D13-A):
nothing upstream may depend on it, its own dependency surface is
minimal (only `codex.telemetry`/`codex.verification.state`), and it
has no write path into any D1-D12 store or calibration-point constant
-- the stronger boundary `docs/architecture-conformance-audit.md` §BB.6
flagged as required for any Offline-Learning-shaped package, beyond
what D11/D12's own boundary tests needed to prove.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_DIR = Path(__file__).parent.parent / "src" / "codex"

UPSTREAM_PACKAGES = (
    "ontology",
    "repository",
    "evidence",
    "graph",
    "provider",
    "resolution",
    "registry",
    "reconciliation",
    "ingestion",
    "coverage",
    "query_understanding",
    "planner",
    "llm",
    "verification",
    "telemetry",
    "artifact",
)
"""Every existing top-level `codex` package as of D1-D12 -- none of
these should import `codex.evaluation`."""


def _imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_evaluation_package_exists() -> None:
    assert (SRC_DIR / "evaluation").is_dir()


def test_no_upstream_package_imports_evaluation() -> None:
    violations: dict[str, set[str]] = {}
    for package in UPSTREAM_PACKAGES:
        package_dir = SRC_DIR / package
        if not package_dir.is_dir():
            continue
        for py_file in package_dir.rglob("*.py"):
            modules = _imported_modules(py_file.read_text())
            hits = {
                m
                for m in modules
                if m == "codex.evaluation" or m.startswith("codex.evaluation.")
            }
            if hits:
                violations[f"{package}/{py_file.name}"] = hits
    assert violations == {}, f"Upstream code depends on codex.evaluation: {violations}"


def test_llm_package_never_imports_evaluation() -> None:
    """Extends TAD §61's "the LLM receives only the approved
    EvidencePackage" boundary by the same reasoning already applied to
    `codex.artifact` (D12) -- checked directly, not merely inferred
    from the generic upstream sweep."""
    llm_dir = SRC_DIR / "llm"
    for py_file in llm_dir.glob("*.py"):
        modules = _imported_modules(py_file.read_text())
        assert not any(
            m == "codex.evaluation" or m.startswith("codex.evaluation.") for m in modules
        ), f"{py_file.name} imports codex.evaluation"


def test_evaluation_package_has_minimal_dependencies() -> None:
    """`codex.evaluation` only needs D11's `TelemetryStore`/
    `QueryTelemetryEvent` (read-only) and D10's `VerificationStatus`/
    `to_routing_bucket` -- no graph, evidence, artifact, planner, or
    registry type is needed to compute the two implemented metrics."""
    evaluation_dir = SRC_DIR / "evaluation"
    all_imports: set[str] = set()
    for py_file in evaluation_dir.glob("*.py"):
        all_imports |= _imported_modules(py_file.read_text())
    codex_imports = {m for m in all_imports if m.startswith("codex.")}
    allowed_prefixes = ("codex.telemetry", "codex.verification.state", "codex.evaluation")
    disallowed = {
        m for m in codex_imports if not any(m.startswith(p) for p in allowed_prefixes)
    }
    assert disallowed == set(), f"codex.evaluation imports beyond its minimal surface: {disallowed}"


def test_evaluation_never_imports_artifact_store() -> None:
    """This slice never touches Artifact Store at all (D13-A's own
    approved scope) -- checked directly, not merely implied by the
    minimal-dependency-surface test above."""
    evaluation_dir = SRC_DIR / "evaluation"
    for py_file in evaluation_dir.glob("*.py"):
        modules = _imported_modules(py_file.read_text())
        assert not any(
            m == "codex.artifact" or m.startswith("codex.artifact.") for m in modules
        ), f"{py_file.name} imports codex.artifact"


def test_evaluation_never_imports_a_graph_or_evidence_mutation_api() -> None:
    evaluation_dir = SRC_DIR / "evaluation"
    forbidden = (
        "codex.graph.memory_store",
        "codex.evidence.store",
        "codex.ingestion.pipeline",
        "codex.artifact.store",
    )
    violations: dict[str, set[str]] = {}
    for py_file in evaluation_dir.glob("*.py"):
        modules = _imported_modules(py_file.read_text())
        hits = {m for m in modules if any(m == f or m.startswith(f + ".") for f in forbidden)}
        if hits:
            violations[py_file.name] = hits
    assert violations == {}, f"codex.evaluation imports a mutation surface: {violations}"


def test_evaluation_public_surface_has_no_write_shaped_callable() -> None:
    """No `store`/`write`/`record`/`calibrate`/`tune`/`update`/`delete`
    -shaped public name exists anywhere in `codex.evaluation`'s public
    API -- this package is read-only by construction, not merely by
    convention."""
    import codex.evaluation as evaluation_pkg

    forbidden_substrings = ("store", "write", "record", "calibrat", "tune", "update", "delete")
    for name in evaluation_pkg.__all__:
        lowered = name.lower()
        assert not any(s in lowered for s in forbidden_substrings), name


def test_evaluation_never_calls_a_telemetry_store_write_method() -> None:
    """Source-level check that `codex.evaluation` never invokes
    `record_query_event`/`record_failure_event` -- it may only call
    `TelemetryStore.query_events` (read-only), matching `select_dataset`'s
    own documented behavior."""
    evaluation_dir = SRC_DIR / "evaluation"
    forbidden_calls = ("record_query_event", "record_failure_event")
    violations: dict[str, list[str]] = {}
    for py_file in evaluation_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        hits = [
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in forbidden_calls
        ]
        if hits:
            violations[py_file.name] = hits
    assert violations == {}, f"codex.evaluation calls a telemetry write method: {violations}"
