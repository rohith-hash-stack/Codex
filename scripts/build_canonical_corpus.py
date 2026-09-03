"""One-off script: builds and freezes `codex-canonical-v1`
(`codex.benchmark.canonical_corpus`) to `tests/fixtures/benchmark/
codex_canonical_corpus_v1.json`. Not part of the test suite; run once to
(re)generate the frozen fixture, exactly mirroring how `codex_self_dev_
corpus.json` was produced for the development corpus.
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
    build_canonical_corpus,
    make_click_repository,
    make_flask_repository,
)
from codex.evidence.store import InMemoryEvidenceStore  # noqa: E402
from codex.ingestion.pipeline import IngestionPipeline  # noqa: E402
from codex.provider.scip_adapter import SCIPAdapter  # noqa: E402
from codex.registry.registry import CapabilityRegistry  # noqa: E402
from codex.registry.scoring import ProviderScoreProfile  # noqa: E402

NOW = datetime(2026, 9, 3, tzinfo=UTC)
OUT_PATH = REPO_ROOT / "tests" / "fixtures" / "benchmark" / "codex_canonical_corpus_v1.json"


def _ingest_scip_repo(repository, index_filename: str):
    registry = CapabilityRegistry()
    registry.register(
        SCIPAdapter(index_filename=index_filename),
        ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.3),
    )
    pipeline = IngestionPipeline(registry, InMemoryEvidenceStore())
    result = pipeline.run(repository)
    return result, registry


def main() -> int:
    codex_result, codex_registry, codex_evidence, codex_repo = ingest_codex_self()
    print(f"codex: {len(codex_result.graph_store.find_entities())} entities, "
          f"revision={codex_repo.head_revision}")

    click_repo = make_click_repository()
    click_result, click_registry = _ingest_scip_repo(click_repo, "click_sample.scip")
    print(f"click: {len(click_result.graph_store.find_entities())} entities, "
          f"revision={click_repo.head_revision}")

    flask_repo = make_flask_repository()
    flask_result, flask_registry = _ingest_scip_repo(flask_repo, "flask_sample.scip")
    print(f"flask: {len(flask_result.graph_store.find_entities())} entities, "
          f"revision={flask_repo.head_revision}")

    repository_graphs = {
        "codex": (codex_repo, codex_result.graph_store),
        "click": (click_repo, click_result.graph_store),
        "flask": (flask_repo, flask_result.graph_store),
    }
    corpus = build_canonical_corpus(repository_graphs=repository_graphs, now=NOW)
    print(f"\nBuilt corpus: version={corpus.corpus.corpus_version} "
          f"cases={len(corpus.corpus.cases)} complete={corpus.is_complete}")

    for qid, case in corpus.corpus.cases.items():
        label = corpus.corpus.labels[qid]
        print(f"  [{case.repository_id:6s}] {corpus.categories[qid].value:22s} "
              f"{case.query_text!r:60s} relevant={len(label.relevant_entity_ids or [])} "
              f"abstain={label.should_abstain}")

    # `relevant_entity_ids` is a `frozenset[str]` (codex.evaluation.models.
    # GroundTruthLabel) -- semantically order-free, but Python's per-process
    # hash randomization makes its default JSON array order vary run to run
    # even though the *set contents* are always identical (verified: two
    # independent builds produce identical sets, only differently ordered
    # arrays). Sorted here, in this freeze script only, purely so the
    # checked-in fixture is byte-stable across regenerations for clean
    # diffs -- never changes the corpus's own model (a `frozenset` field
    # stays a `frozenset` everywhere else; this is output formatting only).
    payload = corpus.model_dump(mode="json")
    for label in payload["corpus"]["labels"].values():
        if label.get("relevant_entity_ids") is not None:
            label["relevant_entity_ids"] = sorted(label["relevant_entity_ids"])
    OUT_PATH.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"\nWrote {OUT_PATH} ({OUT_PATH.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
