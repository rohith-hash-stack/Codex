"""Dependency-boundary tests for `codex.telemetry` (directive D11):
telemetry may depend on the entire existing pipeline (TAD §75:
"Telemetry -> all runtime components"), but nothing upstream may
depend on telemetry for correctness -- confirming the DAG-preserving
design reconstructed in `docs/architecture-conformance-audit.md` §X.7.
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
)
"""Every existing top-level `codex` package as of D1-D10 -- none of
these should import `codex.telemetry` (D11 is a strict DAG successor,
never a dependency of anything that came before it)."""


def _imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_telemetry_package_exists() -> None:
    assert (SRC_DIR / "telemetry").is_dir()


def test_no_upstream_package_imports_telemetry() -> None:
    violations: dict[str, set[str]] = {}
    for package in UPSTREAM_PACKAGES:
        package_dir = SRC_DIR / package
        if not package_dir.is_dir():
            continue
        for py_file in package_dir.rglob("*.py"):
            modules = _imported_modules(py_file.read_text())
            hits = {
                m for m in modules if m == "codex.telemetry" or m.startswith("codex.telemetry.")
            }
            if hits:
                violations[f"{package}/{py_file.name}"] = hits
    assert violations == {}, f"Upstream code depends on telemetry: {violations}"


def test_telemetry_may_depend_on_the_rest_of_the_pipeline() -> None:
    """The reverse direction is explicitly authorized (TAD §75) --
    confirming `codex.telemetry` actually does import from the
    existing pipeline (not that it's forbidden to), so the "no
    correctness dependency on telemetry" property is a real, exercised
    asymmetry, not a coincidence of an empty package."""
    telemetry_dir = SRC_DIR / "telemetry"
    all_imports: set[str] = set()
    for py_file in telemetry_dir.glob("*.py"):
        all_imports |= _imported_modules(py_file.read_text())

    expected_touched = {"codex.planner", "codex.query_understanding", "codex.verification"}
    touched = {
        prefix
        for prefix in expected_touched
        if any(m == prefix or m.startswith(prefix + ".") for m in all_imports)
    }
    assert touched == expected_touched


def test_telemetry_never_imports_a_graph_or_evidence_mutation_api() -> None:
    """Telemetry is read-only with respect to canonical repository
    truth (TAD §62, extended the same way D10.9 already extended it to
    the LLM/Verification boundary) -- it may import types (`GraphVersion`,
    `RetrievalPlan`, ...) but never `codex.graph.store`/`codex.evidence.
    store`'s mutating surface."""
    telemetry_dir = SRC_DIR / "telemetry"
    forbidden = ("codex.graph.memory_store", "codex.evidence.store", "codex.ingestion.pipeline")
    violations: dict[str, set[str]] = {}
    for py_file in telemetry_dir.glob("*.py"):
        modules = _imported_modules(py_file.read_text())
        hits = {m for m in modules if any(m == f or m.startswith(f + ".") for f in forbidden)}
        if hits:
            violations[py_file.name] = hits
    assert violations == {}, f"Telemetry imports a mutation surface: {violations}"


def test_telemetry_store_has_no_write_path_back_into_the_graph() -> None:
    from codex.telemetry.store import InMemoryTelemetryStore

    public_methods = {
        name for name in dir(InMemoryTelemetryStore) if not name.startswith("_")
    }
    for forbidden_name in ("upsert_entity", "upsert_relationship", "add_evidence", "publish"):
        assert forbidden_name not in public_methods
