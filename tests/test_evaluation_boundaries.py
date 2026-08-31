"""Dependency-boundary tests for `codex.evaluation` (directives D13-A,
D13-B): nothing upstream may depend on it, its own dependency surface
is minimal and read-only (D13-B legitimately adds `codex.planner.
{models,ranking,retrieval}`/`codex.graph.store` -- all pure/read-only,
never a mutation surface), and it has no write path into any D1-D12
store, calibration-point constant, or the retrieval decision loop
itself -- the stronger boundary `docs/architecture-conformance-
audit.md` §BB.6/§EE flagged as required for any Offline-Learning- or
Observer-shaped package.
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
these should import `codex.evaluation`. Crucially includes `planner`
itself: D13-B reads from it, but the reverse edge must never exist."""


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


def test_planner_package_specifically_never_imports_evaluation() -> None:
    """D13-B reads *from* `codex.planner` (observer.py) -- the DAG edge
    must be strictly one-directional, the same "Telemetry -> all
    runtime components, never the reverse" shape TAD §75 already
    authorizes for D11, extended here by analogy since D13-B is itself
    an evaluation/observability-shaped reader of D9's real output, not
    a retrieval component. Checked directly, not merely inferred from
    the generic upstream sweep above."""
    planner_dir = SRC_DIR / "planner"
    for py_file in planner_dir.glob("*.py"):
        modules = _imported_modules(py_file.read_text())
        assert not any(
            m == "codex.evaluation" or m.startswith("codex.evaluation.") for m in modules
        ), f"{py_file.name} imports codex.evaluation"


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
    """`codex.evaluation`'s own `codex.*` imports are limited to D11's
    `TelemetryStore`/`QueryTelemetryEvent`, D10's `VerificationStatus`/
    `to_routing_bucket`, and (D13-B, `observer.py` only) D9's own
    already-exported, pure, read-only surface: `RetrievalPlan`/
    `PlanStatus` (data), `GraphReader` (a read-only Protocol), and the
    two pure functions `bounded_traversal`/`rank_entities`. No mutation
    surface (`codex.graph.memory_store`, `codex.evidence.store`,
    `codex.ingestion.pipeline`, `codex.artifact.store`,
    `codex.planner.planner`, `codex.planner.cache`) is ever imported."""
    evaluation_dir = SRC_DIR / "evaluation"
    all_imports: set[str] = set()
    for py_file in evaluation_dir.glob("*.py"):
        all_imports |= _imported_modules(py_file.read_text())
    codex_imports = {m for m in all_imports if m.startswith("codex.")}
    allowed = {
        "codex.telemetry",
        "codex.telemetry.models",
        "codex.telemetry.store",
        "codex.verification.state",
        "codex.evaluation",
        "codex.graph.store",
        "codex.planner.models",
        "codex.planner.ranking",
        "codex.planner.retrieval",
    }
    disallowed = {
        m
        for m in codex_imports
        if not (m in allowed or any(m.startswith(p + ".") for p in allowed))
    }
    assert disallowed == set(), f"codex.evaluation imports beyond its minimal surface: {disallowed}"


def test_evaluation_never_imports_artifact_store() -> None:
    """This slice never touches Artifact Store at all (D13-A/D13-B's
    own approved scope) -- checked directly, not merely implied by the
    minimal-dependency-surface test above."""
    evaluation_dir = SRC_DIR / "evaluation"
    for py_file in evaluation_dir.glob("*.py"):
        modules = _imported_modules(py_file.read_text())
        assert not any(
            m == "codex.artifact" or m.startswith("codex.artifact.") for m in modules
        ), f"{py_file.name} imports codex.artifact"


def test_evaluation_never_imports_a_graph_evidence_or_planner_mutation_api() -> None:
    """Nothing in `codex.evaluation` may import a component capable of
    mutating canonical graph/evidence state, constructing a
    `RetrievalPlan`, or invoking `execute_query` -- the observer reads
    D9's pure functions only, never the retrieval decision loop
    itself."""
    evaluation_dir = SRC_DIR / "evaluation"
    forbidden = (
        "codex.graph.memory_store",
        "codex.evidence.store",
        "codex.ingestion.pipeline",
        "codex.artifact.store",
        "codex.planner.planner",
        "codex.planner.cache",
        "codex.planner.mss",
        "codex.planner.provider_selection",
    )
    violations: dict[str, set[str]] = {}
    for py_file in evaluation_dir.glob("*.py"):
        modules = _imported_modules(py_file.read_text())
        hits = {m for m in modules if any(m == f or m.startswith(f + ".") for f in forbidden)}
        if hits:
            violations[py_file.name] = hits
    assert violations == {}, f"codex.evaluation imports a mutation/decision surface: {violations}"


def test_evaluation_public_surface_has_no_write_shaped_callable() -> None:
    """No `store`/`write`/`record`/`calibrat`/`tune`/`update`/`delete`
    -shaped public name exists anywhere in `codex.evaluation`'s public
    API -- this package is read-only by construction, not merely by
    convention. `observe_ranked_candidates` is deliberately not
    matched by any of these substrings."""
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


def test_observer_never_calls_plan_query_or_execute_query() -> None:
    """Directly proves the observer cannot become part of the retrieval
    decision loop: it never calls `plan_query`/`execute_query`, and
    never constructs a `RetrievalPlan` itself -- it only ever *reads*
    an already-built one."""
    observer_source = (SRC_DIR / "evaluation" / "observer.py").read_text()
    tree = ast.parse(observer_source)
    forbidden_calls = {"plan_query", "execute_query", "RetrievalPlan"}
    called_or_constructed = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert called_or_constructed.isdisjoint(forbidden_calls), called_or_constructed


def test_evaluation_package_never_imports_llm_slm_or_embedding_dependencies() -> None:
    """No LLM/SLM/embedding/external-ML-framework dependency is ever
    introduced anywhere in `codex.evaluation` (directive D13-B's
    explicit constraint) -- checked both by import and by a forbidden-
    module-name substring scan, since none of TAD/HLRD requires one
    here."""
    evaluation_dir = SRC_DIR / "evaluation"
    forbidden_substrings = ("llm", "slm", "embedding", "torch", "tensorflow", "sklearn")
    for py_file in evaluation_dir.glob("*.py"):
        modules = _imported_modules(py_file.read_text())
        for m in modules:
            lowered = m.lower()
            assert not any(s in lowered for s in forbidden_substrings), f"{py_file.name}: {m}"
