"""Dependency-boundary tests for `codex.artifact` (directive D12):
nothing upstream may depend on it, and `codex.llm` specifically must
never import it (TAD §61: "The LLM must not have unrestricted access
to... artifact storage").
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
)
"""Every existing top-level `codex` package as of D1-D11 -- none of
these should import `codex.artifact`."""


def _imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_artifact_package_exists() -> None:
    assert (SRC_DIR / "artifact").is_dir()


def test_no_upstream_package_imports_artifact() -> None:
    violations: dict[str, set[str]] = {}
    for package in UPSTREAM_PACKAGES:
        package_dir = SRC_DIR / package
        if not package_dir.is_dir():
            continue
        for py_file in package_dir.rglob("*.py"):
            modules = _imported_modules(py_file.read_text())
            hits = {
                m for m in modules if m == "codex.artifact" or m.startswith("codex.artifact.")
            }
            if hits:
                violations[f"{package}/{py_file.name}"] = hits
    assert violations == {}, f"Upstream code depends on codex.artifact: {violations}"


def test_llm_package_never_imports_artifact_storage() -> None:
    """TAD §61, verbatim: "The LLM must not have unrestricted access
    to... artifact storage. It receives only the approved
    EvidencePackage." Checked directly (not merely implied by the
    generic upstream check above) since this is the one TAD-named
    security invariant D12 exists to uphold."""
    llm_dir = SRC_DIR / "llm"
    for py_file in llm_dir.glob("*.py"):
        modules = _imported_modules(py_file.read_text())
        assert not any(
            m == "codex.artifact" or m.startswith("codex.artifact.") for m in modules
        ), f"{py_file.name} imports codex.artifact"


def test_artifact_package_has_minimal_dependencies() -> None:
    """`codex.artifact` only needs D1's existing scheme-validation
    helper -- it is a generic byte store, decoupled from every typed
    pipeline object (`Evidence`, `RetrievalPlan`, etc.), unlike
    `codex.telemetry` which legitimately needs to import pipeline
    types for its own field typing."""
    artifact_dir = SRC_DIR / "artifact"
    all_imports: set[str] = set()
    for py_file in artifact_dir.glob("*.py"):
        all_imports |= _imported_modules(py_file.read_text())
    codex_imports = {m for m in all_imports if m.startswith("codex.")}
    assert codex_imports <= {"codex.artifact.store", "codex.evidence.model"}


def test_artifact_store_has_no_write_path_into_the_graph_or_evidence_store() -> None:
    from codex.artifact.store import InMemoryArtifactStore

    public_methods = {name for name in dir(InMemoryArtifactStore) if not name.startswith("_")}
    for forbidden_name in ("upsert_entity", "upsert_relationship", "add_evidence", "publish"):
        assert forbidden_name not in public_methods
