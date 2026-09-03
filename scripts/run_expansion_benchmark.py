"""Runs the frozen `validation-expansion-v1` corpus through the real
`OpenAIGateway`, one `run_corpus` call per repository. Mirrors `scripts/
run_canonical_benchmark.py` exactly, extended to 4 repositories.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from benchmark_fixtures import ingest_codex_self  # noqa: E402
from codex.benchmark.canonical_corpus import (  # noqa: E402
    make_click_repository,
    make_flask_repository,
)
from codex.benchmark.expansion_corpus import make_itsdangerous_repository  # noqa: E402
from codex.benchmark.harness import run_corpus, score_run  # noqa: E402
from codex.benchmark.models import DevelopmentCorpus  # noqa: E402
from codex.evaluation.models import BenchmarkCorpus  # noqa: E402
from codex.evidence.store import InMemoryEvidenceStore  # noqa: E402
from codex.ingestion.pipeline import IngestionPipeline  # noqa: E402
from codex.llm.openai_gateway import OpenAIGateway  # noqa: E402
from codex.provider.scip_adapter import SCIPAdapter  # noqa: E402
from codex.registry.registry import CapabilityRegistry  # noqa: E402
from codex.registry.scoring import ProviderScoreProfile  # noqa: E402

FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "benchmark"
FROZEN_FIXTURE = FIXTURES_DIR / "codex_expansion_corpus_v1.json"
DIMENSIONS_PATH = FIXTURES_DIR / "codex_expansion_corpus_v1_dimensions.json"
OUT_DIR = REPO_ROOT / "benchmark_runs"
NOW = datetime(2026, 9, 3, tzinfo=UTC)


def _sub_corpus(corpus: DevelopmentCorpus, repository_id: str) -> DevelopmentCorpus:
    cases = {
        qid: case
        for qid, case in corpus.corpus.cases.items()
        if case.repository_id == repository_id
    }
    labels = {qid: corpus.corpus.labels[qid] for qid in cases}
    categories = {qid: corpus.categories[qid] for qid in cases}
    return DevelopmentCorpus(
        corpus=BenchmarkCorpus(
            corpus_version=corpus.corpus.corpus_version, cases=cases, labels=labels
        ),
        categories=categories,
    )


def _scip_repo(make_repo, index_filename: str):
    repository = make_repo()
    registry = CapabilityRegistry()
    registry.register(
        SCIPAdapter(index_filename=index_filename),
        ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.3),
    )
    evidence = InMemoryEvidenceStore()
    result = IngestionPipeline(registry, evidence).run(repository)
    return repository, result, registry, evidence


def main() -> int:
    corpus = DevelopmentCorpus.model_validate_json(FROZEN_FIXTURE.read_bytes())
    dimensions = json.loads(DIMENSIONS_PATH.read_text())
    print(f"Loaded frozen corpus: version={corpus.corpus.corpus_version} "
          f"cases={len(corpus.corpus.cases)} complete={corpus.is_complete}")

    codex_result, codex_registry, codex_evidence, codex_repo = ingest_codex_self()
    click_repo, click_result, click_registry, click_evidence = _scip_repo(
        make_click_repository, "click_sample.scip"
    )
    flask_repo, flask_result, flask_registry, flask_evidence = _scip_repo(
        make_flask_repository, "flask_sample.scip"
    )
    itsdangerous_repo, itsdangerous_result, itsdangerous_registry, itsdangerous_evidence = (
        _scip_repo(make_itsdangerous_repository, "itsdangerous_sample.scip")
    )

    repos = {
        "codex": (codex_repo, codex_result, codex_registry, codex_evidence),
        "click": (click_repo, click_result, click_registry, click_evidence),
        "flask": (flask_repo, flask_result, flask_registry, flask_evidence),
        "itsdangerous": (
            itsdangerous_repo, itsdangerous_result, itsdangerous_registry, itsdangerous_evidence
        ),
    }
    for repo_id, (_repo, result, _reg, _ev) in repos.items():
        print(f"Ingested {repo_id}: {len(result.graph_store.find_entities())} entities, "
              f"graph_version={result.graph_store.version.version_id}")

    gateway = OpenAIGateway()
    print(f"Gateway: provider={gateway.provider} requested_model={gateway.requested_model}")

    all_records = {}
    all_events = []
    all_traces = {}
    for repo_id, (repository, result, registry, evidence) in repos.items():
        sub_corpus = _sub_corpus(corpus, repo_id)
        if not sub_corpus.corpus.cases:
            continue
        record, events, traces = run_corpus(
            sub_corpus,
            gateway,
            graph=result.graph_store,
            evidence_store=evidence,
            ingestion_result=result,
            registry=registry,
            repository=repository,
            model_id=gateway.requested_model,
            provider=gateway.provider,
            now=NOW,
        )
        all_records[repo_id] = record
        all_events.extend(events)
        all_traces.update(traces)

    report = score_run(all_events, corpus, all_traces, now=NOW)

    OUT_DIR.mkdir(exist_ok=True)
    artifact = {
        "corpus_version": corpus.corpus.corpus_version,
        "dimensions": dimensions,
        "records_by_repository": {
            repo_id: record.model_dump(mode="json") for repo_id, record in all_records.items()
        },
        "evaluation_report": report.model_dump(mode="json"),
    }
    out_path = OUT_DIR / "expansion_v1_openai_run.json"
    out_path.write_text(json.dumps(artifact, indent=2))
    print(f"\nWrote full artifact to {out_path}")

    print("\n=== Per-case results ===")
    for repo_id, record in all_records.items():
        for qid, case_result in record.results.items():
            case = corpus.corpus.cases[qid]
            category = corpus.categories[qid]
            label = corpus.corpus.labels[qid]
            print(f"\n[{repo_id}] {category.value} — {case.query_text!r}  [{dimensions[qid]}]")
            print(f"  generation_status: {case_result.generation_status}  "
                  f"finish_reason: {case_result.finish_reason}")
            print(f"  served_model: {case_result.served_model}  "
                  f"usage_total_tokens: {case_result.usage_total_tokens}")
            print(f"  should_abstain (ground truth): {label.should_abstain}  "
                  f"relevant_count: {len(label.relevant_entity_ids or [])}")
            if case_result.structured_answer is not None:
                print(f"  claims returned: {len(case_result.structured_answer.claims)}")

    print("\n=== Aggregate evaluation report ===")
    print(json.dumps(report.model_dump(mode="json"), indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
