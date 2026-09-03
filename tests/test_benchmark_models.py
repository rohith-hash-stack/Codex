"""Unit tests for `codex.benchmark.models` and `compute_run_id`."""

from __future__ import annotations

from datetime import UTC, datetime

from codex.benchmark.harness import compute_run_id
from codex.benchmark.models import CaseRunResult, DevelopmentCorpus, ModelRunRecord
from codex.evaluation.models import BenchmarkCase, BenchmarkCorpus
from codex.llm.gateway import GenerationStatus
from codex.query_understanding.models import Intent


def _case(query_id: str) -> BenchmarkCase:
    return BenchmarkCase(
        query_id=query_id, repository_id="r", repository_revision="rev", query_text="q"
    )


def test_development_corpus_is_complete_true_when_categories_cover_every_case() -> None:
    corpus = BenchmarkCorpus(corpus_version="v", cases={"q1": _case("q1")})
    dev = DevelopmentCorpus(corpus=corpus, categories={"q1": Intent.FIND_CALLERS})
    assert dev.is_complete is True


def test_development_corpus_is_complete_false_when_a_case_has_no_category() -> None:
    corpus = BenchmarkCorpus(corpus_version="v", cases={"q1": _case("q1"), "q2": _case("q2")})
    dev = DevelopmentCorpus(corpus=corpus, categories={"q1": Intent.FIND_CALLERS})
    assert dev.is_complete is False


def test_compute_run_id_is_deterministic() -> None:
    kwargs = dict(
        corpus_version="v1",
        model_id="m",
        provider="p",
        prompt_template_version="pt1",
        context_construction_version="ct1",
        repository_revision="rev1",
    )
    assert compute_run_id(**kwargs) == compute_run_id(**kwargs)


def test_compute_run_id_changes_with_every_reproducibility_dimension() -> None:
    base = dict(
        corpus_version="v1",
        model_id="m",
        provider="p",
        prompt_template_version="pt1",
        context_construction_version="ct1",
        repository_revision="rev1",
    )
    baseline = compute_run_id(**base)
    for key in base:
        varied = dict(base)
        varied[key] = base[key] + "-different"
        assert compute_run_id(**varied) != baseline, f"run_id insensitive to {key}"


def test_case_run_result_llm_tokens_defaults_to_none_never_fabricated() -> None:
    result = CaseRunResult(
        query_id="q1",
        query_identity="q1",
        generation_status=GenerationStatus.OK,
        retrieval_context_version="gv1",
        token_budget=4000,
        latency_budget_ms=5000,
    )
    assert result.llm_tokens is None
    assert result.latency_ms is None


def test_model_run_record_results_default_to_empty_dict() -> None:
    record = ModelRunRecord(
        run_id="run:x",
        corpus_version="v1",
        repository_id="r",
        repository_revision="rev",
        prompt_template_version="pt1",
        context_construction_version="ct1",
        model_id="m",
        provider="p",
        generated_at=datetime.now(UTC),
    )
    assert record.results == {}
