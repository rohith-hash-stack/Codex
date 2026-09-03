"""Tests for `codex.benchmark.canonical_corpus` (`codex-canonical-v1`,
the "Build Canonical LLM Benchmark & Broad Validation" milestone).

Mirrors `test_benchmark_dev_corpus.py`'s own structure: prove
determinism across independent ingestions, prove the checked-in frozen
fixture still matches, and sanity-check category/repository coverage.
`click`/`flask` are ingested from the real, frozen `.scip` fixtures
(`tests/fixtures/benchmark/scip/`) -- no live clone or network access
needed to run these tests.
"""

from __future__ import annotations

from pathlib import Path

from benchmark_fixtures import BENCHMARK_DEV_NOW, ingest_codex_self
from codex.benchmark.canonical_corpus import (
    CANONICAL_CORPUS_VERSION,
    build_canonical_corpus,
    make_click_repository,
    make_flask_repository,
)
from codex.benchmark.models import DevelopmentCorpus
from codex.evidence.store import InMemoryEvidenceStore
from codex.ingestion.pipeline import IngestionPipeline
from codex.provider.scip_adapter import SCIPAdapter
from codex.query_understanding.models import Intent
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile

FROZEN_FIXTURE = (
    Path(__file__).parent / "fixtures" / "benchmark" / "codex_canonical_corpus_v1.json"
)

_SCIP_PROFILE = ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.3)


def _ingest_scip_repo(repository, index_filename: str):
    registry = CapabilityRegistry()
    registry.register(SCIPAdapter(index_filename=index_filename), _SCIP_PROFILE)
    pipeline = IngestionPipeline(registry, InMemoryEvidenceStore())
    result = pipeline.run(repository)
    return result


def _build_live_corpus() -> DevelopmentCorpus:
    codex_result, _reg, _ev, codex_repo = ingest_codex_self()
    click_repo = make_click_repository()
    click_result = _ingest_scip_repo(click_repo, "click_sample.scip")
    flask_repo = make_flask_repository()
    flask_result = _ingest_scip_repo(flask_repo, "flask_sample.scip")

    repository_graphs = {
        "codex": (codex_repo, codex_result.graph_store),
        "click": (click_repo, click_result.graph_store),
        "flask": (flask_repo, flask_result.graph_store),
    }
    return build_canonical_corpus(repository_graphs=repository_graphs, now=BENCHMARK_DEV_NOW)


def test_frozen_fixture_exists_and_parses() -> None:
    corpus = DevelopmentCorpus.model_validate_json(FROZEN_FIXTURE.read_bytes())
    assert corpus.corpus.corpus_version == CANONICAL_CORPUS_VERSION
    assert corpus.is_complete


def test_corpus_spans_all_three_repositories() -> None:
    corpus = _build_live_corpus()
    repo_ids = {case.repository_id for case in corpus.corpus.cases.values()}
    assert repo_ids == {"codex", "click", "flask"}


def test_corpus_covers_seven_real_query_categories() -> None:
    corpus = _build_live_corpus()
    assert set(corpus.categories.values()) == {
        Intent.FIND_CALLERS,
        Intent.FIND_TESTS,
        Intent.FIND_DEPENDENCIES,
        Intent.FIND_IMPLEMENTATIONS,
        Intent.FIND_REFERENCES,
        Intent.ARCHITECTURE_ANALYSIS,
    }


def test_thirteen_cases_no_query_id_collisions() -> None:
    corpus = _build_live_corpus()
    assert len(corpus.corpus.cases) == 13
    assert corpus.is_complete


def test_negative_cases_have_should_abstain_and_empty_ground_truth() -> None:
    corpus = _build_live_corpus()
    negative_cases = [
        case for case in corpus.corpus.cases.values() if "nonexistent" in case.query_text
    ]
    assert len(negative_cases) == 3  # one per repository
    assert {c.repository_id for c in negative_cases} == {"codex", "click", "flask"}
    for case in negative_cases:
        label = corpus.corpus.labels[case.query_id]
        assert label.should_abstain is True
        assert label.relevant_entity_ids == frozenset()


def test_positive_cases_have_nonempty_ground_truth() -> None:
    corpus = _build_live_corpus()
    for case in corpus.corpus.cases.values():
        if "nonexistent" in case.query_text:
            continue
        label = corpus.corpus.labels[case.query_id]
        assert label.should_abstain is False
        assert label.relevant_entity_ids
        assert len(label.relevant_entity_ids) > 0


def test_high_fan_out_cases_have_large_ground_truth_sets() -> None:
    """The two deliberate high-fan-out cases (`plan_query` in codex,
    `ParamType` in click) must have real, large ground-truth sets --
    proving they weren't accidentally narrowed to a single match."""
    corpus = _build_live_corpus()
    by_text = {case.query_text: case.query_id for case in corpus.corpus.cases.values()}

    plan_query_id = by_text["What calls plan_query?"]
    assert len(corpus.corpus.labels[plan_query_id].relevant_entity_ids or []) > 20

    param_type_id = by_text["What implements ParamType?"]
    assert len(corpus.corpus.labels[param_type_id].relevant_entity_ids or []) > 10


def test_corpus_construction_is_deterministic_across_independent_ingestions() -> None:
    corpus1 = _build_live_corpus()
    corpus2 = _build_live_corpus()
    assert corpus1.model_dump() == corpus2.model_dump()


def test_live_corpus_matches_frozen_fixture_content() -> None:
    """Set-level (not raw-JSON-text) equality: `relevant_entity_ids` is
    a `frozenset`, so `.model_dump()` equality is already order-free --
    the checked-in fixture's *byte* stability is a separate freeze-script
    concern (`scripts/build_canonical_corpus.py` sorts before writing),
    not a correctness requirement of the corpus model itself."""
    live = _build_live_corpus()
    frozen = DevelopmentCorpus.model_validate_json(FROZEN_FIXTURE.read_bytes())
    assert live.model_dump() == frozen.model_dump()
