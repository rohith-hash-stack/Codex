"""Dependency-boundary tests for Query Understanding (directive D8
Phase 4, 11): no reverse dependency on graph/provider/registry/
ingestion/resolution/reconciliation, and no graph/provider/LLM access
performed by the engine itself."""

from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_MODULES = (
    # Behavioral provider machinery (extraction, selection, registry) --
    # exactly the capabilities directive Phase 4 forbids Query
    # Understanding from exercising.
    "codex.provider.contract",
    "codex.provider.git_adapter",
    "codex.provider.scip_adapter",
    "codex.provider.codeql_adapter",
    "codex.provider.scip",
    "codex.registry",
    "codex.graph",
    "codex.ingestion",
    "codex.resolution",
    "codex.reconciliation",
)
"""Deliberately does **not** include ``codex.provider.capability``:
that module is a pure, stateless vocabulary enum (`Capability`) with no
provider-selection or extraction behavior at all -- structurally the
same kind of shared-ontology-type reuse already established for
`RelationshipType` (`codex.ontology.relationships`) and
`CompletenessLevel` (`codex.coverage.engine`). `QueryContract.
required_evidence: list[Capability]` reuses it rather than inventing a
duplicate parallel enum -- consistent with this project's own "do not
invent fields/types the architecture already defines" discipline. The
actual behavioral boundary (no provider selection, no extraction, no
graph access) is enforced by forbidding every module that *does*
carry behavior."""

QU_PACKAGE_DIR = Path(__file__).parent.parent / "src" / "codex" / "query_understanding"


def _imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_query_understanding_package_exists() -> None:
    assert QU_PACKAGE_DIR.is_dir()


def test_no_module_imports_graph_provider_registry_ingestion_resolution_reconciliation() -> None:
    violations: dict[str, set[str]] = {}
    for py_file in QU_PACKAGE_DIR.glob("*.py"):
        modules = _imported_modules(py_file.read_text())
        hits = {
            m
            for m in modules
            if any(
                m == forbidden or m.startswith(forbidden + ".") for forbidden in FORBIDDEN_MODULES
            )
        }
        if hits:
            violations[py_file.name] = hits
    assert violations == {}, f"Forbidden imports found: {violations}"


def test_no_llm_or_slm_model_dependency_imported() -> None:
    """No real model client library is imported anywhere in the package
    -- directive Phase 7's "do not introduce a real model dependency"."""
    suspicious_substrings = ("openai", "anthropic", "transformers", "torch", "tensorflow")
    for py_file in QU_PACKAGE_DIR.glob("*.py"):
        modules = _imported_modules(py_file.read_text())
        for module in modules:
            lowered = module.lower()
            assert not any(s in lowered for s in suspicious_substrings), (
                f"{py_file.name} imports a real model dependency: {module}"
            )


def test_query_contract_has_no_graph_or_provider_fields() -> None:
    from codex.query_understanding.models import QueryContract

    field_names = set(QueryContract.model_fields)
    assert "graph_version" not in field_names
    assert "graph_store" not in field_names
    assert "provider" not in field_names
