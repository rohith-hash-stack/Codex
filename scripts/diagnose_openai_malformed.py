"""Phase 1 diagnostic (read-only): reproduces the exact same request
`run_corpus` built for the frozen `codex-self-dev-v0` "build_canonical_id"
case, using the real `OpenAIGateway`'s own `_build_body`/`_post`
internals (no production code modified), and inspects the FULL raw
OpenAI response -- `finish_reason`, `usage`, and exactly where JSON
parsing fails -- rather than assuming a cause.

Not part of the test suite. Prints diagnostic data only; never prints
the API key.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from benchmark_fixtures import BENCHMARK_DEV_NOW, ingest_codex_self  # noqa: E402
from codex.benchmark.harness import _build_request  # noqa: E402
from codex.benchmark.models import DevelopmentCorpus  # noqa: E402
from codex.llm.openai_gateway import OpenAIGateway, _read_api_key  # noqa: E402
from codex.planner.cache import compute_query_identity  # noqa: E402
from codex.planner.planner import execute_query, plan_query  # noqa: E402
from codex.query_understanding.engine import understand_query  # noqa: E402

FROZEN_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "benchmark" / "codex_self_dev_corpus.json"
TARGET_QUERY_TEXT = "What calls build_canonical_id?"


def main() -> int:
    corpus = DevelopmentCorpus.model_validate_json(FROZEN_FIXTURE.read_bytes())
    case = next(c for c in corpus.corpus.cases.values() if c.query_text == TARGET_QUERY_TEXT)
    print(f"Case: {case.query_text!r} (query_id={case.query_id})")
    print(f"Frozen repository_revision: {case.repository_revision}")

    result, registry, evidence_store, repository = ingest_codex_self()
    assert repository.head_revision == case.repository_revision, "revision mismatch vs. frozen case"

    understanding = understand_query(
        case.query_text, repository_id=case.repository_id, now=BENCHMARK_DEV_NOW
    )
    assert understanding.contract is not None
    contract = understanding.contract
    identity = compute_query_identity(contract)
    assert identity == case.query_id, "query identity drift vs. frozen case"

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
    print(f"Retrieval: {len(package.entities)} entities, {len(package.relationships)} "
          f"relationships, {len(package.evidence)} evidence records")
    print(f"retrieval_context_version: {plan.graph_version.version_id}")

    request = _build_request(case.query_text, package, contract)
    print(f"LLMRequest.token_budget={request.token_budget} "
          f"latency_budget_ms={request.latency_budget_ms}")

    gateway = OpenAIGateway()
    body = gateway._build_body(request)  # noqa: SLF001 - diagnostic reuse of real internals
    prompt_chars = sum(len(m["content"]) for m in body["messages"])
    print(f"Request body: model={body['model']} max_tokens={body['max_tokens']} "
          f"prompt_chars={prompt_chars}")

    api_key = _read_api_key()
    raw_bytes = gateway._post(api_key, body)  # noqa: SLF001 - same HTTP path as production
    payload = json.loads(raw_bytes.decode("utf-8"))

    choice = payload["choices"][0]
    finish_reason = choice.get("finish_reason")
    content = choice["message"]["content"]
    usage = payload.get("usage") or {}

    print("\n--- RAW RESPONSE METADATA (full, not the harness's summarized view) ---")
    print(f"served_model: {payload.get('model')}")
    print(f"finish_reason: {finish_reason!r}")
    print(f"usage: {json.dumps(usage)}")
    print(f"content length (chars): {len(content)}")
    print(f"content tail (last 200 chars): {content[-200:]!r}")

    try:
        json.loads(content)
        print("\ncontent IS valid JSON on its own.")
    except json.JSONDecodeError as exc:
        print(f"\ncontent is INVALID JSON: {exc.msg} at line {exc.lineno} col {exc.colno} "
              f"(char {exc.pos} of {len(content)})")

    print("\n--- DIAGNOSIS ---")
    if finish_reason == "length":
        print("CONFIRMED: completion was cut off by the token/length limit "
              "(finish_reason='length').")
    elif finish_reason == "stop":
        print("finish_reason='stop' -- the model completed normally; malformed output (if any) is "
              "NOT a truncation issue. Inspect content above for the actual cause (extra prose, "
              "markdown fences, schema violation, etc.).")
    else:
        print(f"finish_reason={finish_reason!r} -- neither 'length' nor 'stop'; inspect directly.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
