"""Security tests for `codex.evaluation` (directive D13-B): repository/
query text can never inject or modify `EvaluationTrace` fields, and the
observer contains no dangerous dynamic-execution surface at all.
"""

from __future__ import annotations

import ast
from pathlib import Path

from codex.evaluation.observer import observe_ranked_candidates
from codex.ontology.relationships import RelationshipType
from codex.planner.planner import plan_query
from codex.provider.capability import Capability
from codex.query_understanding.models import CompletenessLevel, Intent, QueryContract
from planner_fixtures import build_graph

SRC_DIR = Path(__file__).parent.parent / "src" / "codex" / "evaluation"

MALICIOUS_STRINGS = [
    "'; DROP TABLE evaluation_trace; --",
    "__import__('os').system('echo pwned')",
    "<script>alert(1)</script>",
    "{{7*7}}",
    "$(rm -rf /)",
    "../../etc/passwd",
]


def make_contract(**overrides: object) -> QueryContract:
    kwargs: dict[str, object] = {
        "intent": Intent.FIND_CALLERS,
        "targets": ["auth.py"],
        "relationship_types": [RelationshipType.CALLS],
        "complexity": 0.3,
        "ambiguity": 0.1,
        "confidence": 0.97,
        "completeness_requirement": CompletenessLevel.LOW,
        "required_evidence": [Capability.CALL_RELATIONSHIP],
        "token_budget": 4000,
        "latency_budget_ms": 5000,
    }
    kwargs.update(overrides)
    return QueryContract(**kwargs)


def test_malicious_entity_path_never_escapes_the_canonical_id_field() -> None:
    """A repository whose real file paths are shell/script-injection-
    shaped strings still produces a trace whose `entity_id`s are plain,
    inert canonical-id strings -- never interpreted, executed, or
    allowed to alter the trace's shape."""
    for malicious in MALICIOUS_STRINGS:
        result, registry, _evidence_store, repository = build_graph(
            entity_paths=(malicious, "caller.py"),
            relationship_pairs=(("caller.py", malicious),),
        )
        plan = plan_query(
            query_contract=make_contract(targets=[malicious]),
            graph=result.graph_store,
            ingestion_result=result,
            registry=registry,
            repository=repository,
        )
        trace = observe_ranked_candidates(plan, result.graph_store)

        assert trace.ordered_candidates, f"expected candidates for {malicious!r}"
        for candidate in trace.ordered_candidates:
            assert isinstance(candidate.entity_id, str)
            assert isinstance(candidate.score, float)
            assert isinstance(candidate.rank, int)
        # The trace's own schema fields are exactly the declared ones --
        # a malicious path cannot inject an extra field.
        assert set(trace.model_dump().keys()) == {
            "query_identity",
            "repository_id",
            "graph_version_id",
            "ordered_candidates",
        }


def test_malicious_query_constraints_do_not_alter_trace_structure() -> None:
    """`plan.constraints` flows into `rank_entities`'s `query_constraint_
    match` signal purely as token/tag data (regex tokenization,
    `_TOKEN_PATTERN = [a-zA-Z0-9]+`) -- never as code or a format
    string."""
    result, registry, _evidence_store, repository = build_graph(
        entity_paths=("auth.py", "caller.py"), relationship_pairs=(("caller.py", "auth.py"),)
    )
    contract = make_contract(targets=["auth.py"], constraints=list(MALICIOUS_STRINGS))
    plan = plan_query(
        query_contract=contract,
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    trace = observe_ranked_candidates(plan, result.graph_store)
    assert trace.ordered_candidates
    for candidate in trace.ordered_candidates:
        assert 0.0 <= candidate.score <= 1.0


def test_evaluation_package_never_calls_eval_exec_or_a_subprocess() -> None:
    """AST-level proof, not just a code-review claim: no `eval`,
    `exec`, `subprocess`, `os.system`, or dynamic `__import__` call
    exists anywhere in `codex.evaluation`."""
    forbidden_names = {"eval", "exec", "__import__"}
    forbidden_modules = {"subprocess", "os"}
    violations: dict[str, list[str]] = {}
    for py_file in SRC_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        hits: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in forbidden_names:
                    hits.append(node.func.id)
            if isinstance(node, ast.Import):
                hits.extend(a.name for a in node.names if a.name in forbidden_modules)
            if isinstance(node, ast.ImportFrom) and node.module in forbidden_modules:
                hits.append(node.module)
        if hits:
            violations[py_file.name] = hits
    assert violations == {}, f"Dangerous dynamic-execution surface found: {violations}"


def test_evaluation_trace_round_trips_through_serialization_unchanged() -> None:
    """A malicious-looking field value survives model_dump/model_
    validate byte-for-byte -- pydantic validation, not string
    interpolation, governs every field."""
    result, registry, _evidence_store, repository = build_graph(
        entity_paths=(MALICIOUS_STRINGS[0], "caller.py"),
        relationship_pairs=(("caller.py", MALICIOUS_STRINGS[0]),),
    )
    plan = plan_query(
        query_contract=make_contract(targets=[MALICIOUS_STRINGS[0]]),
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    trace = observe_ranked_candidates(plan, result.graph_store)
    from codex.evaluation.models import EvaluationTrace

    round_tripped = EvaluationTrace.model_validate(trace.model_dump())
    assert round_tripped == trace
