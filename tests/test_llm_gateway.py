"""Behavioral tests for the LLM Gateway contract (TAD §43; directive
D10.1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codex.llm.gateway import GenerationStatus, LLMRequest
from codex.llm.schema import Claim, ClaimType, StructuredAnswer
from codex.ontology.relationships import RelationshipType
from fake_llm_gateway import FakeLLMGateway, malformed_result, ok_result
from llm_fixtures import make_evidence_package


def _request(**overrides: object) -> LLMRequest:
    kwargs: dict[str, object] = {
        "query_text": "Who calls authenticate?",
        "evidence_package": make_evidence_package(),
        "response_schema": StructuredAnswer.model_json_schema(),
        "token_budget": 4000,
        "latency_budget_ms": 5000,
    }
    kwargs.update(overrides)
    return LLMRequest(**kwargs)


def test_generate_returns_ok_status_with_structured_answer() -> None:
    answer = StructuredAnswer(
        explanation="X calls Y.",
        claims=[
            Claim(
                subject="X", predicate=RelationshipType.CALLS, object="Y", claim_type=ClaimType.FACT
            )
        ],
    )
    gateway = FakeLLMGateway([ok_result(answer)])
    result = gateway.generate(_request())
    assert result.status is GenerationStatus.OK
    assert result.answer == answer


def test_generate_returns_malformed_status_never_a_fabricated_answer() -> None:
    gateway = FakeLLMGateway([malformed_result(raw_output="{not valid json")])
    result = gateway.generate(_request())
    assert result.status is GenerationStatus.MALFORMED_OUTPUT
    assert result.answer is None
    assert result.raw_output == "{not valid json"


def test_request_carries_no_graph_provider_or_evidence_store_reference() -> None:
    """Structural proof: LLMRequest's field types contain no store/
    registry-shaped dependency at all."""
    field_names = set(LLMRequest.model_fields)
    for forbidden in ("graph", "graph_store", "registry", "evidence_store", "repository"):
        assert forbidden not in field_names


def test_request_requires_positive_budgets() -> None:
    with pytest.raises(ValidationError):
        _request(token_budget=0)
    with pytest.raises(ValidationError):
        _request(latency_budget_ms=0)


def test_fake_gateway_records_every_request_for_inspection() -> None:
    gateway = FakeLLMGateway([ok_result(StructuredAnswer(explanation="ok", claims=[]))])
    request = _request()
    gateway.generate(request)
    assert gateway.requests == [request]


def test_fake_gateway_scripted_sequence_drives_resynthesis_style_flows() -> None:
    """A gateway can be scripted to fail then succeed -- exactly the
    shape a re-synthesis controller (D10.7) needs to test against."""
    answer = StructuredAnswer(explanation="ok", claims=[])
    gateway = FakeLLMGateway([malformed_result(), ok_result(answer)])
    first = gateway.generate(_request())
    second = gateway.generate(_request())
    assert first.status is GenerationStatus.MALFORMED_OUTPUT
    assert second.status is GenerationStatus.OK
    assert second.answer == answer


def test_feedback_field_is_none_by_default_and_set_only_on_resynthesis() -> None:
    plain = _request()
    assert plain.feedback is None
    retry = _request(feedback="remove claim X: contradicted by evidence")
    assert retry.feedback == "remove claim X: contradicted by evidence"
