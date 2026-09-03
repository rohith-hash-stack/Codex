"""One-off script: builds and freezes `validation-expansion-v1`
(`codex.benchmark.expansion_corpus`) to `tests/fixtures/benchmark/
codex_expansion_corpus_v1.json`. Mirrors `scripts/build_canonical_
corpus.py` exactly, including its `relevant_entity_ids` sort-before-
write fix for byte-stable regeneration.
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
from codex.benchmark.expansion_corpus import (  # noqa: E402
    build_expansion_corpus,
    make_itsdangerous_repository,
)
from codex.evidence.store import InMemoryEvidenceStore  # noqa: E402
from codex.ingestion.pipeline import IngestionPipeline  # noqa: E402
from codex.provider.scip_adapter import SCIPAdapter  # noqa: E402
from codex.registry.registry import CapabilityRegistry  # noqa: E402
from codex.registry.scoring import ProviderScoreProfile  # noqa: E402

NOW = datetime(2026, 9, 3, tzinfo=UTC)
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "benchmark"
OUT_PATH = FIXTURES_DIR / "codex_expansion_corpus_v1.json"
DIMENSIONS_PATH = FIXTURES_DIR / "codex_expansion_corpus_v1_dimensions.json"


def _ingest_scip_repo(repository, index_filename: str):
    registry = CapabilityRegistry()
    registry.register(
        SCIPAdapter(index_filename=index_filename),
        ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.3),
    )
    result = IngestionPipeline(registry, InMemoryEvidenceStore()).run(repository)
    return result


def main() -> int:
    codex_result, _r, _e, codex_repo = ingest_codex_self()
    click_repo = make_click_repository()
    click_result = _ingest_scip_repo(click_repo, "click_sample.scip")
    flask_repo = make_flask_repository()
    flask_result = _ingest_scip_repo(flask_repo, "flask_sample.scip")
    itsdangerous_repo = make_itsdangerous_repository()
    itsdangerous_result = _ingest_scip_repo(itsdangerous_repo, "itsdangerous_sample.scip")

    repository_graphs = {
        "codex": (codex_repo, codex_result.graph_store),
        "click": (click_repo, click_result.graph_store),
        "flask": (flask_repo, flask_result.graph_store),
        "itsdangerous": (itsdangerous_repo, itsdangerous_result.graph_store),
    }
    corpus, dimensions = build_expansion_corpus(repository_graphs=repository_graphs, now=NOW)
    print(f"Built corpus: version={corpus.corpus.corpus_version} "
          f"cases={len(corpus.corpus.cases)} complete={corpus.is_complete}")
    for qid, case in corpus.corpus.cases.items():
        label = corpus.corpus.labels[qid]
        print(f"  [{case.repository_id:12s}] {corpus.categories[qid].value:22s} "
              f"{case.query_text!r:45s} relevant={len(label.relevant_entity_ids or [])} "
              f"abstain={label.should_abstain}  [{dimensions[qid]}]")

    payload = corpus.model_dump(mode="json")
    for label in payload["corpus"]["labels"].values():
        if label.get("relevant_entity_ids") is not None:
            label["relevant_entity_ids"] = sorted(label["relevant_entity_ids"])
    OUT_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    DIMENSIONS_PATH.write_text(json.dumps(dimensions, indent=2, sort_keys=True) + "\n")
    print(f"\nWrote {OUT_PATH} ({OUT_PATH.stat().st_size} bytes)")
    print(f"Wrote {DIMENSIONS_PATH} ({DIMENSIONS_PATH.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
