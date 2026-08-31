"""Dependency-boundary tests for `codex.planner` (directive D9 Part 15,
Part 18 "Boundaries"): no LLM/SLM/verification dependency, no
provider-specific logic, no duplicate provider-selection algorithm.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PLANNER_PACKAGE_DIR = Path(__file__).parent.parent / "src" / "codex" / "planner"

FORBIDDEN_MODULES = (
    # No LLM/SLM/verification component exists yet -- these names are
    # forward-looking guards, not modules that currently exist.
    "codex.llm",
    "codex.slm",
    "codex.verification",
    "codex.query_understanding.slm",
    # Behavioral provider machinery -- provider selection must go through
    # codex.registry.registry.CapabilityRegistry only (D9 directive Part 5).
    "codex.provider.contract",
    "codex.provider.git_adapter",
    "codex.provider.scip_adapter",
    "codex.provider.codeql_adapter",
    "codex.provider.scip",
)
"""Deliberately does **not** include ``codex.provider.capability``
(stateless vocabulary enum) or ``codex.registry.registry`` (the one
provider-selection mechanism D9 is required to reuse, not avoid)."""


def _imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_planner_package_exists() -> None:
    assert PLANNER_PACKAGE_DIR.is_dir()


def test_no_module_imports_forbidden_dependencies() -> None:
    violations: dict[str, set[str]] = {}
    for py_file in PLANNER_PACKAGE_DIR.glob("*.py"):
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


def test_no_llm_or_model_dependency_imported() -> None:
    suspicious_substrings = ("openai", "anthropic", "transformers", "torch", "tensorflow")
    for py_file in PLANNER_PACKAGE_DIR.glob("*.py"):
        modules = _imported_modules(py_file.read_text())
        for module in modules:
            lowered = module.lower()
            assert not any(s in lowered for s in suspicious_substrings), (
                f"{py_file.name} imports a real model dependency: {module}"
            )


def test_no_provider_specific_branching_by_name() -> None:
    """No module in `codex.planner` should branch on a hard-coded
    provider name (e.g. `if provider_name == "scip"`) -- selection must
    flow entirely through `CapabilityRegistry`."""
    known_provider_names = ("scip", "codeql", "git", "sourcegraph")
    pattern = re.compile(r'==\s*["\'](' + "|".join(known_provider_names) + r')["\']', re.IGNORECASE)
    violations: dict[str, list[str]] = {}
    for py_file in PLANNER_PACKAGE_DIR.glob("*.py"):
        text = py_file.read_text()
        hits = pattern.findall(text)
        if hits:
            violations[py_file.name] = hits
    assert violations == {}, f"Provider-name branching found: {violations}"


def test_provider_selection_module_only_calls_registry_rank_or_providers_for() -> None:
    """`select_providers` must not define its own scoring function --
    proven structurally: the module defines exactly one public function
    and it is a thin pass-through to `CapabilityRegistry`."""
    from codex.planner import provider_selection

    assert provider_selection.__all__ == ["select_providers"]
    source = (PLANNER_PACKAGE_DIR / "provider_selection.py").read_text()
    assert "def rank(" not in source
    assert "def score(" not in source
    assert "registry.rank(" in source


def test_retrieval_plan_has_no_llm_answer_or_verification_fields() -> None:
    from codex.planner.models import RetrievalPlan

    field_names = set(RetrievalPlan.model_fields)
    for forbidden in ("answer", "verification_status", "claims", "llm_response"):
        assert forbidden not in field_names
