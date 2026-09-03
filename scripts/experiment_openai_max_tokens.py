"""Phase 2 controlled experiment (read-only, no production code
change): re-runs the exact same "build_canonical_id" request as
`diagnose_openai_malformed.py`, with only `max_completion_tokens`
raised, to prove (or disprove) that a larger completion budget resolves
the truncation confirmed in Phase 1. Everything else -- corpus,
repository revision, query, retrieval, prompt template, model -- stays
identical.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from benchmark_fixtures import BENCHMARK_DEV_NOW, ingest_codex_self  # noqa: E402
from codex.benchmark.harness import _build_request  # noqa: E402
from codex.benchmark.models import DevelopmentCorpus  # noqa: E402
from codex.llm.gateway import GenerationStatus  # noqa: E402
from codex.llm.openai_gateway import OpenAIGateway  # noqa: E402
from codex.planner.cache import compute_query_identity  # noqa: E402
from codex.planner.planner import execute_query, plan_query  # noqa: E402
from codex.query_understanding.engine import understand_query  # noqa: E402

FROZEN_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "benchmark" / "codex_self_dev_corpus.json"
TARGET_QUERY_TEXT = "What calls build_canonical_id?"
EXPERIMENT_MAX_COMPLETION_TOKENS = 4096


def main() -> int:
    corpus = DevelopmentCorpus.model_validate_json(FROZEN_FIXTURE.read_bytes())
    case = next(c for c in corpus.corpus.cases.values() if c.query_text == TARGET_QUERY_TEXT)

    result, registry, evidence_store, repository = ingest_codex_self()
    understanding = understand_query(
        case.query_text, repository_id=case.repository_id, now=BENCHMARK_DEV_NOW
    )
    contract = understanding.contract
    assert contract is not None
    assert compute_query_identity(contract) == case.query_id

    plan = plan_query(
        query_contract=contract,
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    package = execute_query(
        plan, graph=result.graph_store, evidence_store=evidence_store, ingestion_result=result
    )
    request = _build_request(case.query_text, package, contract)

    print(f"Experiment: max_completion_tokens={EXPERIMENT_MAX_COMPLETION_TOKENS} "
          f"(vs. default {1024}), everything else identical to Phase 1")

    gateway = OpenAIGateway(max_completion_tokens=EXPERIMENT_MAX_COMPLETION_TOKENS)
    generation = gateway.generate(request)
    metadata = gateway.last_response_metadata

    print(f"\ngeneration_status: {generation.status}")
    print(f"served_model: {getattr(metadata, 'served_model', None)}")
    print(f"usage: prompt={getattr(metadata, 'usage_prompt_tokens', None)} "
          f"completion={getattr(metadata, 'usage_completion_tokens', None)} "
          f"total={getattr(metadata, 'usage_total_tokens', None)}")
    print(f"raw_output length (chars): "
          f"{len(generation.raw_output) if generation.raw_output else 0}")

    if generation.status is GenerationStatus.OK:
        assert generation.answer is not None
        print(f"claims parsed: {len(generation.answer.claims)}")
        print(f"explanation (first 200 chars): {generation.answer.explanation[:200]!r}")
    else:
        print(f"detail: {generation.detail}")
        if generation.raw_output:
            print(f"raw_output tail (last 200 chars): {generation.raw_output[-200:]!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
