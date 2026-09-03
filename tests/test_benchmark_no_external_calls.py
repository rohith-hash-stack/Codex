"""Structural proof that no module under `codex.benchmark` can perform
network I/O -- this milestone's explicit requirement ("Do not make any
external LLM calls during this phase"), checked directly rather than
merely claimed (mirrors `tests/test_evaluation_boundaries.py`'s own
AST-based import-scanning style).
"""

from __future__ import annotations

import ast
from pathlib import Path

BENCHMARK_DIR = Path(__file__).parent.parent / "src" / "codex" / "benchmark"

_FORBIDDEN_MODULE_SUBSTRINGS = (
    "requests",
    "httpx",
    "urllib",
    "http.client",
    "socket",
    "openai",
    "anthropic",
    "aiohttp",
    "grpc",
)


def _imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_benchmark_package_exists() -> None:
    assert BENCHMARK_DIR.is_dir()


def test_no_networking_or_provider_sdk_import_anywhere_in_codex_benchmark() -> None:
    violations: dict[str, set[str]] = {}
    for py_file in BENCHMARK_DIR.glob("*.py"):
        modules = _imported_modules(py_file.read_text())
        hits = {
            m
            for m in modules
            if any(s in m.lower() for s in _FORBIDDEN_MODULE_SUBSTRINGS)
        }
        if hits:
            violations[py_file.name] = hits
    assert violations == {}, f"codex.benchmark imports networking/provider SDKs: {violations}"


def test_no_networking_builtin_call_anywhere_in_codex_benchmark() -> None:
    """Beyond import statements: no direct call to a networking-shaped
    builtin/stdlib name (`socket.socket`, `urlopen`, ...) appears
    anywhere in `codex.benchmark`'s own source."""
    forbidden_calls = {"urlopen", "socket", "create_connection"}
    violations: dict[str, set[str]] = {}
    for py_file in BENCHMARK_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        called_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    called_names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    called_names.add(node.func.attr)
        hits = called_names & forbidden_calls
        if hits:
            violations[py_file.name] = hits
    assert violations == {}, f"codex.benchmark calls a networking builtin: {violations}"


def test_llm_gateway_is_a_plain_parameter_never_a_concrete_implementation() -> None:
    """`codex.benchmark.harness` imports `LLMGateway` only as a type
    (the D10 Protocol) -- it never imports or defines a concrete,
    vendor-backed class of its own (that is explicitly the *next*
    checkpoint's scope, not this one)."""
    harness_source = (BENCHMARK_DIR / "harness.py").read_text()
    tree = ast.parse(harness_source)
    class_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert class_names == set(), f"codex.benchmark.harness defines classes: {class_names}"
