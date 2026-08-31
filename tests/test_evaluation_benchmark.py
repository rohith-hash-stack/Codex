"""Tests for `BenchmarkCase`/`BenchmarkCorpus.cases` (models.py) and
`codex.evaluation.benchmark.verify_case_execution` (directive D13-C):
malformed/duplicate-case rejection, deterministic ids, deterministic
ordering, repository/revision mismatch detection.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from codex.evaluation.benchmark import verify_case_execution
from codex.evaluation.models import BenchmarkCase, BenchmarkCorpus
from codex.telemetry.models import QueryTelemetryEvent
from telemetry_fixtures import make_contract, make_graph_version, make_plan

NOW = datetime(2026, 8, 31, tzinfo=UTC)


def make_case(
    *,
    query_id: str = "q1",
    repository_id: str = "repo1",
    repository_revision: str = "rev1",
    query_text: str = "Who calls authenticate?",
) -> BenchmarkCase:
    return BenchmarkCase(
        query_id=query_id,
        repository_id=repository_id,
        repository_revision=repository_revision,
        query_text=query_text,
    )


def make_event(
    *, query_id: str = "q1", repository_id: str = "repo1", repository_revision: str = "rev1"
) -> QueryTelemetryEvent:
    gv = make_graph_version().model_copy(
        update={"repository_id": repository_id, "repository_revision": repository_revision}
    )
    return QueryTelemetryEvent.build(
        query_id=query_id,
        graph_version=gv,
        query_contract=make_contract(),
        retrieval_plan=make_plan(gv),
        candidate_count=1,
        mss_size=1,
        llm_calls=1,
        now=NOW,
    )


# --- BenchmarkCase: malformed rejection --------------------------------------


def test_benchmark_case_rejects_empty_query_id() -> None:
    with pytest.raises(ValidationError):
        BenchmarkCase(query_id="", repository_id="r", repository_revision="v", query_text="q")


def test_benchmark_case_rejects_empty_repository_id() -> None:
    with pytest.raises(ValidationError):
        BenchmarkCase(query_id="q", repository_id="", repository_revision="v", query_text="q")


def test_benchmark_case_rejects_empty_repository_revision() -> None:
    with pytest.raises(ValidationError):
        BenchmarkCase(query_id="q", repository_id="r", repository_revision="", query_text="q")


def test_benchmark_case_rejects_empty_query_text() -> None:
    with pytest.raises(ValidationError):
        BenchmarkCase(query_id="q", repository_id="r", repository_revision="v", query_text="")


# --- BenchmarkCorpus.cases: key/id consistency, duplicate detection ---------


def test_benchmark_corpus_accepts_a_consistent_case() -> None:
    corpus = BenchmarkCorpus(corpus_version="v1", cases={"q1": make_case(query_id="q1")})
    assert corpus.cases["q1"].query_id == "q1"


def test_benchmark_corpus_rejects_a_case_whose_query_id_does_not_match_its_key() -> None:
    with pytest.raises(ValidationError, match="mismatched"):
        BenchmarkCorpus(corpus_version="v1", cases={"q1": make_case(query_id="q2")})


def test_benchmark_corpus_rejects_two_different_keys_claiming_the_same_query_id() -> None:
    """The realistic "duplicate case" scenario: two dict entries whose
    embedded `query_id` both claim to be the same case. Python dicts
    cannot literally have two identical keys, so this is the only way
    a duplicate can be expressed -- and it is structurally impossible
    for both to pass the key/id-consistency check."""
    with pytest.raises(ValidationError, match="mismatched"):
        BenchmarkCorpus(
            corpus_version="v1",
            cases={
                "case-a": make_case(query_id="same-id"),
                "case-b": make_case(query_id="same-id"),
            },
        )


def test_benchmark_corpus_cases_default_to_empty() -> None:
    corpus = BenchmarkCorpus(corpus_version="v1")
    assert corpus.cases == {}


# --- deterministic ids / ordering ---------------------------------------------


def test_benchmark_case_construction_is_deterministic() -> None:
    a = make_case(query_id="q1")
    b = make_case(query_id="q1")
    assert a == b


def test_benchmark_corpus_cases_preserve_insertion_order() -> None:
    corpus = BenchmarkCorpus(
        corpus_version="v1",
        cases={
            "q3": make_case(query_id="q3"),
            "q1": make_case(query_id="q1"),
            "q2": make_case(query_id="q2"),
        },
    )
    assert list(corpus.cases.keys()) == ["q3", "q1", "q2"]


# --- verify_case_execution: repository/revision mismatch handling -----------


def test_verify_case_execution_true_for_matching_run() -> None:
    case = make_case(query_id="q1", repository_id="repo1", repository_revision="rev1")
    event = make_event(query_id="q1", repository_id="repo1", repository_revision="rev1")
    assert verify_case_execution(case, event) is True


def test_verify_case_execution_false_on_repository_mismatch() -> None:
    case = make_case(query_id="q1", repository_id="repo1", repository_revision="rev1")
    event = make_event(query_id="q1", repository_id="repo-DIFFERENT", repository_revision="rev1")
    assert verify_case_execution(case, event) is False


def test_verify_case_execution_false_on_revision_mismatch() -> None:
    case = make_case(query_id="q1", repository_id="repo1", repository_revision="rev1")
    event = make_event(query_id="q1", repository_id="repo1", repository_revision="rev-DIFFERENT")
    assert verify_case_execution(case, event) is False


def test_verify_case_execution_false_on_query_id_mismatch() -> None:
    case = make_case(query_id="q1", repository_id="repo1", repository_revision="rev1")
    event = make_event(query_id="q-DIFFERENT", repository_id="repo1", repository_revision="rev1")
    assert verify_case_execution(case, event) is False


def test_verify_case_execution_never_mutates_its_arguments() -> None:
    case = make_case()
    event = make_event()
    case_before, event_before = case.model_copy(deep=True), event.model_copy(deep=True)
    verify_case_execution(case, event)
    assert case == case_before
    assert event == event_before
