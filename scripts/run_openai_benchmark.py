"""One-off real-OpenAI benchmark run against the frozen
`codex-self-dev-v0` development corpus.

Not part of the test suite and not imported by any production code --
a diagnostic script for this checkpoint's own "first real OpenAI
development baseline" run. Loads the checked-in frozen corpus JSON
verbatim (never rebuilds it), ingests the live repository purely to
give the real D8/D9 pipeline something to retrieve against, and drives
`codex.llm.openai_gateway.OpenAIGateway` through
`codex.benchmark.harness.run_corpus`.

Prints a JSON report to stdout. The `Codex_open_API_key` value itself
is never printed, logged, or included in the report -- only whatever
`OpenAIGateway`/`run_corpus` already redact-and-record.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from benchmark_fixtures import BENCHMARK_DEV_NOW, ingest_codex_self  # noqa: E402
from codex.benchmark.harness import run_corpus, score_run  # noqa: E402
from codex.benchmark.models import DevelopmentCorpus  # noqa: E402
from codex.llm.openai_gateway import (  # noqa: E402
    OpenAIAuthenticationError,
    OpenAIGateway,
    OpenAIGatewayError,
)

FROZEN_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "benchmark" / "codex_self_dev_corpus.json"


def main() -> int:
    frozen_bytes = FROZEN_FIXTURE.read_bytes()
    corpus = DevelopmentCorpus.model_validate_json(frozen_bytes)
    print(f"Loaded frozen corpus: version={corpus.corpus.corpus_version} "
          f"cases={len(corpus.corpus.cases)} complete={corpus.is_complete}")

    result, registry, evidence_store, repository = ingest_codex_self()
    print(f"Ingested live repository: repository_id={repository.repository_id} "
          f"pinned_revision={repository.head_revision} "
          f"graph_version_id={result.graph_store.version.version_id}")

    gateway = OpenAIGateway()
    print(f"Gateway: provider={gateway.provider} requested_model={gateway.requested_model}")

    try:
        record, events, traces = run_corpus(
            corpus,
            gateway,
            graph=result.graph_store,
            evidence_store=evidence_store,
            ingestion_result=result,
            registry=registry,
            repository=repository,
            model_id=gateway.requested_model,
            provider=gateway.provider,
            now=BENCHMARK_DEV_NOW,
        )
    except OpenAIAuthenticationError as exc:
        print(f"AUTHENTICATION FAILURE (no fallback attempted): {exc}")
        return 2
    except OpenAIGatewayError as exc:
        print(f"GATEWAY FAILURE before any per-case run could start: {exc}")
        return 3

    report = score_run(events, corpus, traces, now=BENCHMARK_DEV_NOW)

    print("\n--- run_id / reproducibility dimensions ---")
    print(json.dumps(record.model_dump(mode="json", exclude={"results"}), indent=2))

    print("\n--- per-case results ---")
    for query_id, case_result in record.results.items():
        case = corpus.corpus.cases[query_id]
        print(f"\ncase {query_id[:12]}... ({case.query_text!r})")
        print(f"  generation_status: {case_result.generation_status}")
        print(f"  error: {case_result.error}")
        print(f"  served_model: {case_result.served_model}")
        print(f"  usage_total_tokens: {case_result.usage_total_tokens}")
        print(f"  retrieval_context_version: {case_result.retrieval_context_version}")

    print("\n--- aggregate evaluation report ---")
    print(json.dumps(report.model_dump(mode="json"), indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
