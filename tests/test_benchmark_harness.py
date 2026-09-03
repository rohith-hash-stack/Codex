"""Tests for `codex.benchmark.harness` (real-LLM benchmark
infrastructure milestone) -- the reproducibility gate this milestone
exists to satisfy: "same corpus + same repository revision + same
prompt version + same retrieval/context version -> directly comparable
model runs with preserved raw outputs and deterministic scoring."

Every test here uses `tests.fake_llm_gateway.FakeLLMGateway`, a
deterministic, in-memory, scripted test double -- never a real network
call (see `test_benchmark_no_external_calls.py` for the structural
proof that no module under `codex.benchmark` can make one).
"""

from __future__ import annotations

import pytest

from benchmark_fixtures import BENCHMARK_DEV_NOW, ingest_codex_self
from codex.benchmark.dev_corpus import build_development_corpus
from codex.benchmark.harness import (
    CONTEXT_CONSTRUCTION_VERSION,
    PROMPT_TEMPLATE_VERSION,
    run_corpus,
    score_run,
)
from codex.evaluation.models import EvaluationMetric
from codex.llm.gateway import GenerationStatus
from codex.llm.schema import Claim, ClaimType, StructuredAnswer
from fake_llm_gateway import FakeLLMGateway, malformed_result, ok_result

_ANSWER = StructuredAnswer(
    explanation="stub explanation",
    claims=[Claim(subject="a", predicate="CALLS", object="b", claim_type=ClaimType.FACT)],
)


def _corpus_and_graph():
    result, registry, evidence_store, repository = ingest_codex_self()
    corpus = build_development_corpus(
        repository=repository, graph=result.graph_store, now=BENCHMARK_DEV_NOW
    )
    return corpus, result, registry, evidence_store, repository


def test_run_corpus_produces_one_result_per_case() -> None:
    corpus, result, registry, evidence_store, repository = _corpus_and_graph()
    gateway = FakeLLMGateway([ok_result(_ANSWER)])

    record, events, traces = run_corpus(
        corpus,
        gateway,
        graph=result.graph_store,
        evidence_store=evidence_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
        model_id="fake-model",
        provider="fake-provider",
        now=BENCHMARK_DEV_NOW,
    )

    assert set(record.results) == set(corpus.corpus.cases)
    assert len(events) == 4
    assert len(traces) == 4
    for qid, case_result in record.results.items():
        assert case_result.query_id == qid
        assert case_result.query_identity == qid
        assert case_result.generation_status is GenerationStatus.OK
        assert case_result.structured_answer == _ANSWER


def test_run_corpus_reports_pinned_reproducibility_dimensions() -> None:
    corpus, result, registry, evidence_store, repository = _corpus_and_graph()
    gateway = FakeLLMGateway([ok_result(_ANSWER)])

    record, _events, _traces = run_corpus(
        corpus,
        gateway,
        graph=result.graph_store,
        evidence_store=evidence_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
        model_id="fake-model",
        provider="fake-provider",
        now=BENCHMARK_DEV_NOW,
    )

    assert record.corpus_version == corpus.corpus.corpus_version
    assert record.repository_id == repository.repository_id
    assert record.repository_revision == repository.head_revision
    assert record.prompt_template_version == PROMPT_TEMPLATE_VERSION
    assert record.context_construction_version == CONTEXT_CONSTRUCTION_VERSION
    assert record.model_id == "fake-model"
    assert record.provider == "fake-provider"
    # Every case ran against the exact same locked graph snapshot.
    context_versions = {r.retrieval_context_version for r in record.results.values()}
    assert len(context_versions) == 1
    assert next(iter(context_versions)) == result.graph_store.version.version_id


def test_run_corpus_is_reproducible_across_two_independent_runs() -> None:
    """The core reproducibility gate: identical corpus + identical
    (already-ingested) graph + identical scripted gateway output ->
    byte-identical `ModelRunRecord`, including `run_id`."""
    corpus, result, registry, evidence_store, repository = _corpus_and_graph()

    record1, _e1, _t1 = run_corpus(
        corpus,
        FakeLLMGateway([ok_result(_ANSWER)]),
        graph=result.graph_store,
        evidence_store=evidence_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
        model_id="fake-model",
        provider="fake-provider",
        now=BENCHMARK_DEV_NOW,
    )
    record2, _e2, _t2 = run_corpus(
        corpus,
        FakeLLMGateway([ok_result(_ANSWER)]),
        graph=result.graph_store,
        evidence_store=evidence_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
        model_id="fake-model",
        provider="fake-provider",
        now=BENCHMARK_DEV_NOW,
    )

    assert record1.run_id == record2.run_id
    assert record1.model_dump() == record2.model_dump()


def test_run_corpus_never_mutates_the_corpus() -> None:
    corpus, result, registry, evidence_store, repository = _corpus_and_graph()
    before = corpus.model_dump()

    run_corpus(
        corpus,
        FakeLLMGateway([ok_result(_ANSWER)]),
        graph=result.graph_store,
        evidence_store=evidence_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
        model_id="fake-model",
        provider="fake-provider",
        now=BENCHMARK_DEV_NOW,
    )

    assert corpus.model_dump() == before


def test_run_corpus_captures_raw_output_verbatim_even_when_malformed() -> None:
    """A `MALFORMED_OUTPUT` result's `raw_output` is preserved verbatim
    on the `CaseRunResult` -- never discarded, never re-parsed here
    (D10.2's own "never re-parse prose" discipline, extended to
    "capture before parsing")."""
    corpus, result, registry, evidence_store, repository = _corpus_and_graph()
    gateway = FakeLLMGateway([malformed_result(raw_output="not valid json at all")])

    record, _events, _traces = run_corpus(
        corpus,
        gateway,
        graph=result.graph_store,
        evidence_store=evidence_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
        model_id="fake-model",
        provider="fake-provider",
        now=BENCHMARK_DEV_NOW,
    )

    for case_result in record.results.values():
        assert case_result.generation_status is GenerationStatus.MALFORMED_OUTPUT
        assert case_result.raw_model_output == "not valid json at all"
        assert case_result.structured_answer is None


def test_score_run_produces_evaluable_retrieval_metrics_against_real_ground_truth() -> None:
    """`score_run` is a thin call into the real, unmodified
    `codex.evaluation.evaluate.evaluate` -- proving the full diagram's
    last stage ("deterministic scoring") actually runs against this
    milestone's own real corpus/traces, never a parallel formula."""
    corpus, result, registry, evidence_store, repository = _corpus_and_graph()
    gateway = FakeLLMGateway([ok_result(_ANSWER)])

    _record, events, traces = run_corpus(
        corpus,
        gateway,
        graph=result.graph_store,
        evidence_store=evidence_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
        model_id="fake-model",
        provider="fake-provider",
        now=BENCHMARK_DEV_NOW,
    )

    report = score_run(events, corpus, traces, now=BENCHMARK_DEV_NOW)

    by_metric = {r.metric: r for r in report.results}
    assert by_metric[EvaluationMetric.PRECISION_AT_10].evaluable is True
    assert by_metric[EvaluationMetric.RECALL_AT_10].evaluable is True
    assert by_metric[EvaluationMetric.MRR].evaluable is True
    # No claim-verification data exists yet in this phase (no
    # Verification Engine wiring -- correctly out of this milestone's
    # scope) -- honestly NOT_EVALUABLE, never a fabricated score.
    assert by_metric[EvaluationMetric.CLAIM_VERIFICATION_ACCURACY].evaluable is False
    assert by_metric[EvaluationMetric.ABSTENTION_PRECISION].evaluable is False


class _AlwaysRaisesGateway:
    """A minimal `LLMGateway`-shaped stub whose `generate()` always
    raises -- simulates e.g. `codex.llm.openai_gateway.OpenAIGateway`
    hitting an authentication or transport failure, without depending
    on that module or the network at all."""

    def __init__(self, message: str = "simulated gateway failure") -> None:
        self._message = message
        self.calls = 0

    def generate(self, request):  # noqa: ANN001, ANN201 - matches LLMGateway.generate
        self.calls += 1
        raise RuntimeError(self._message)


def test_run_corpus_captures_a_gateway_failure_per_case_without_aborting_the_run() -> None:
    """A gateway that raises for every case (e.g. a real, unreachable
    OpenAI endpoint) must not crash `run_corpus` or silently drop the
    case -- every case still gets a `CaseRunResult`, with `error` set
    and `generation_status` left `None`."""
    corpus, result, registry, evidence_store, repository = _corpus_and_graph()
    gateway = _AlwaysRaisesGateway("boom: simulated auth failure")

    record, events, traces = run_corpus(
        corpus,
        gateway,
        graph=result.graph_store,
        evidence_store=evidence_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
        model_id="fake-model",
        provider="fake-provider",
        now=BENCHMARK_DEV_NOW,
    )

    assert gateway.calls == 4
    assert set(record.results) == set(corpus.corpus.cases)
    for case_result in record.results.values():
        assert case_result.generation_status is None
        assert case_result.error == "boom: simulated auth failure"
        assert case_result.raw_model_output is None
    # Retrieval still happened for every case -- a gateway failure does
    # not discard the retrieval work already done.
    assert len(events) == 4
    assert len(traces) == 4


def test_run_corpus_rejects_a_stale_corpus_whose_query_id_no_longer_matches() -> None:
    """If a case's frozen `query_id` no longer matches what Tier-0/
    `compute_query_identity` produce for its own `query_text` today
    (e.g. the corpus was frozen against different `QueryContract`-
    producing code), `run_corpus` fails loudly rather than silently
    scoring against a mismatched case."""
    corpus, result, registry, evidence_store, repository = _corpus_and_graph()
    real_case = next(iter(corpus.corpus.cases.values()))
    tampered = real_case.model_copy(update={"query_id": "not-the-real-identity"})
    other_cases = {k: v for k, v in corpus.corpus.cases.items() if k != real_case.query_id}

    bad_corpus = corpus.model_copy(
        update={
            "corpus": corpus.corpus.model_copy(
                update={"cases": {**other_cases, "not-the-real-identity": tampered}}
            )
        }
    )

    with pytest.raises(ValueError, match="different query identity"):
        run_corpus(
            bad_corpus,
            FakeLLMGateway([ok_result(_ANSWER)]),
            graph=result.graph_store,
            evidence_store=evidence_store,
            ingestion_result=result,
            registry=registry,
            repository=repository,
            model_id="fake-model",
            provider="fake-provider",
            now=BENCHMARK_DEV_NOW,
        )
