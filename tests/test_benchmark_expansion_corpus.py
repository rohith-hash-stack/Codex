"""Tests for `codex.benchmark.expansion_corpus` (`validation-expansion-v1`,
the "Broad LLM Grounding Validation" milestone). Mirrors `test_benchmark_
canonical_corpus.py`'s structure. `click`/`flask`/`itsdangerous` are all
ingested from real, frozen `.scip` fixtures -- no live clone or network
access needed to run these tests.
"""

from __future__ import annotations

from pathlib import Path

from benchmark_fixtures import BENCHMARK_DEV_NOW, ingest_codex_self
from codex.benchmark.canonical_corpus import make_click_repository, make_flask_repository
from codex.benchmark.expansion_corpus import (
    EXPANSION_CORPUS_VERSION,
    build_expansion_corpus,
    make_itsdangerous_repository,
)
from codex.benchmark.models import DevelopmentCorpus
from codex.evidence.store import InMemoryEvidenceStore
from codex.ingestion.pipeline import IngestionPipeline
from codex.provider.scip_adapter import SCIPAdapter
from codex.query_understanding.models import Intent
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile

FROZEN_FIXTURE = (
    Path(__file__).parent / "fixtures" / "benchmark" / "codex_expansion_corpus_v1.json"
)

_SCIP_PROFILE = ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.3)


def _ingest_scip_repo(repository, index_filename: str):
    registry = CapabilityRegistry()
    registry.register(SCIPAdapter(index_filename=index_filename), _SCIP_PROFILE)
    return IngestionPipeline(registry, InMemoryEvidenceStore()).run(repository)


def _build_live_corpus() -> DevelopmentCorpus:
    codex_result, _reg, _ev, codex_repo = ingest_codex_self()
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
    corpus, _dimensions = build_expansion_corpus(
        repository_graphs=repository_graphs, now=BENCHMARK_DEV_NOW
    )
    return corpus


def test_frozen_fixture_exists_and_parses() -> None:
    corpus = DevelopmentCorpus.model_validate_json(FROZEN_FIXTURE.read_bytes())
    assert corpus.corpus.corpus_version == EXPANSION_CORPUS_VERSION
    assert corpus.is_complete
    assert len(corpus.corpus.cases) == 14


def test_corpus_spans_four_repositories_including_the_new_one() -> None:
    corpus = _build_live_corpus()
    repo_ids = {case.repository_id for case in corpus.corpus.cases.values()}
    assert repo_ids == {"codex", "click", "flask", "itsdangerous"}


def test_ground_truth_consistency_should_abstain_matches_empty_relevant_set() -> None:
    """Every case's `should_abstain` flag must agree with whether its
    mechanically-derived `relevant_entity_ids` is actually empty --
    this milestone's own investigation found and fixed two cases where
    that had drifted (`__repr__`, the qualified `Blueprint.add_url_rule`
    case) before freezing; this test guards against it recurring."""
    corpus = _build_live_corpus()
    for case in corpus.corpus.cases.values():
        label = corpus.corpus.labels[case.query_id]
        is_empty = not label.relevant_entity_ids
        assert label.should_abstain == is_empty, (
            f"{case.query_text!r}: should_abstain={label.should_abstain} but "
            f"relevant_entity_ids empty={is_empty}"
        )


def test_six_negative_cases_have_should_abstain_true() -> None:
    """buld_canonical_id (typo), __repr__ (no real REFERENCES edge),
    Blueprint.add_url_rule (qualifier narrows to zero real edges),
    ConfigAttribute (plausible-but-false), itsdangerous-depends-on
    (unsupported relationship type), NoneAlgorithm (real entity, no
    real relationship)."""
    corpus = _build_live_corpus()
    abstain_cases = [
        c
        for c in corpus.corpus.cases.values()
        if corpus.corpus.labels[c.query_id].should_abstain
    ]
    assert len(abstain_cases) == 6


def test_qualified_vs_unqualified_pair_present() -> None:
    corpus = _build_live_corpus()
    texts = {c.query_text for c in corpus.corpus.cases.values()}
    assert "What references add_url_rule?" in texts
    assert "What references Blueprint.add_url_rule?" in texts


def test_covers_architecture_analysis_at_two_fan_out_levels() -> None:
    """The 2-hop `ARCHITECTURE_ANALYSIS` intent appears for both a
    low-fan-out (`itsdangerous`) and comparison target, contrasting with
    `codex-canonical-v1`'s own high-fan-out `Architecture of Flask?`."""
    corpus = _build_live_corpus()
    architecture_cases = [
        c
        for c in corpus.corpus.cases.values()
        if corpus.categories[c.query_id] is Intent.ARCHITECTURE_ANALYSIS
    ]
    assert len(architecture_cases) == 2


def test_corpus_construction_is_deterministic_across_independent_ingestions() -> None:
    corpus1 = _build_live_corpus()
    corpus2 = _build_live_corpus()
    assert corpus1.model_dump() == corpus2.model_dump()
