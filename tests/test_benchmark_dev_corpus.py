"""Tests for `codex.benchmark.dev_corpus` (real-LLM benchmark
infrastructure milestone).

Ground truth for all four development-corpus cases is derived
mechanically from the real, live-ingested Codex source graph -- these
tests prove that derivation is deterministic (two independent
ingestions agree) and matches the checked-in frozen fixture
(`tests/fixtures/benchmark/codex_self_dev_corpus.json`), which was
frozen at commit `benchmark_fixtures.FROZEN_REVISION`. This equality is
expected to keep holding indefinitely for this specific corpus: none of
the four cases' ground truth is perturbed by any file this benchmark
milestone itself added (`build_canonical_id`/`compute_query_identity`'s
real caller sets are unaffected -- verified directly, see this module's
own test below) -- but a *future*, unrelated change that adds a new
`tests/`-path caller of `compute_query_identity`, or any repo-wide
caller of `build_canonical_id`, would legitimately need this fixture
re-frozen. That is an expected property of "ground truth pinned to a
real repository snapshot," not a defect.
"""

from __future__ import annotations

from pathlib import Path

from benchmark_fixtures import BENCHMARK_DEV_NOW, ingest_codex_self
from codex.benchmark.dev_corpus import CORPUS_VERSION, build_development_corpus
from codex.benchmark.models import DevelopmentCorpus
from codex.query_understanding.models import Intent

FROZEN_FIXTURE = (
    Path(__file__).parent / "fixtures" / "benchmark" / "codex_self_dev_corpus.json"
)


def test_frozen_fixture_exists_and_parses() -> None:
    corpus = DevelopmentCorpus.model_validate_json(FROZEN_FIXTURE.read_text())
    assert corpus.corpus.corpus_version == CORPUS_VERSION
    assert corpus.is_complete


def test_build_development_corpus_has_four_cases_across_three_categories() -> None:
    result, registry, _evidence_store, repository = ingest_codex_self()
    corpus = build_development_corpus(
        repository=repository, graph=result.graph_store, now=BENCHMARK_DEV_NOW
    )
    assert len(corpus.corpus.cases) == 4
    assert corpus.is_complete
    assert set(corpus.categories.values()) == {
        Intent.FIND_CALLERS,
        Intent.FIND_TESTS,
        Intent.FIND_DEPENDENCIES,
    }


def test_negative_case_has_should_abstain_and_empty_ground_truth() -> None:
    result, registry, _evidence_store, repository = ingest_codex_self()
    corpus = build_development_corpus(
        repository=repository, graph=result.graph_store, now=BENCHMARK_DEV_NOW
    )
    negative = next(
        (qid, case)
        for qid, case in corpus.corpus.cases.items()
        if "does_not_exist" in case.query_text
    )
    qid, _case = negative
    label = corpus.corpus.labels[qid]
    assert label.should_abstain is True
    assert label.relevant_entity_ids == frozenset()


def test_positive_cases_have_nonempty_ground_truth_and_no_abstention() -> None:
    result, registry, _evidence_store, repository = ingest_codex_self()
    corpus = build_development_corpus(
        repository=repository, graph=result.graph_store, now=BENCHMARK_DEV_NOW
    )
    for qid, case in corpus.corpus.cases.items():
        if "does_not_exist" in case.query_text:
            continue
        label = corpus.corpus.labels[qid]
        assert label.should_abstain is False
        assert label.relevant_entity_ids
        assert len(label.relevant_entity_ids) > 0


def test_corpus_construction_is_deterministic_across_independent_ingestions() -> None:
    """Two entirely independent `ingest_codex_self()` calls (fresh
    `IngestionPipeline`, fresh adapters, fresh evidence store each time)
    produce byte-identical corpora -- extends `AstCallsAdapter`'s own
    `test_deterministic_repeated_extraction_same_ids` proof up through
    ground-truth derivation and corpus assembly."""
    result1, _r1, _e1, repo1 = ingest_codex_self()
    corpus1 = build_development_corpus(
        repository=repo1, graph=result1.graph_store, now=BENCHMARK_DEV_NOW
    )

    result2, _r2, _e2, repo2 = ingest_codex_self()
    corpus2 = build_development_corpus(
        repository=repo2, graph=result2.graph_store, now=BENCHMARK_DEV_NOW
    )

    assert corpus1.model_dump() == corpus2.model_dump()


def test_live_ingestion_matches_frozen_fixture() -> None:
    """The current working tree's real ground truth (for these four
    specific cases) still matches what was frozen at
    `benchmark_fixtures.FROZEN_REVISION` -- see this module's own
    docstring for why that equality is expected to hold for this
    specific corpus and what would legitimately break it."""
    result, _registry, _evidence_store, repository = ingest_codex_self()
    live = build_development_corpus(
        repository=repository, graph=result.graph_store, now=BENCHMARK_DEV_NOW
    )
    frozen = DevelopmentCorpus.model_validate_json(FROZEN_FIXTURE.read_text())
    assert live.model_dump() == frozen.model_dump()
