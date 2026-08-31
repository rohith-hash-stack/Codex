"""A deterministic, test-only `LLMGateway` -- no real model dependency
(matches `tests/fake_slm_adapter.py`'s precedent)."""

from __future__ import annotations

from codex.llm.gateway import GenerationStatus, LLMGateway, LLMGenerationResult, LLMRequest
from codex.llm.schema import StructuredAnswer


class FakeLLMGateway:
    """Returns a scripted sequence of `LLMGenerationResult`s, one per
    call, so re-synthesis flows can be tested deterministically. If the
    script is exhausted, repeats the last configured result."""

    def __init__(self, results: list[LLMGenerationResult]) -> None:
        if not results:
            raise ValueError("FakeLLMGateway requires at least one scripted result")
        self._results = results
        self.requests: list[LLMRequest] = []

    def generate(self, request: LLMRequest) -> LLMGenerationResult:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self._results) - 1)
        return self._results[index]


def ok_result(answer: StructuredAnswer, *, detail: str | None = None) -> LLMGenerationResult:
    return LLMGenerationResult(status=GenerationStatus.OK, answer=answer, detail=detail)


def malformed_result(
    *, raw_output: str = "not json", detail: str | None = None
) -> LLMGenerationResult:
    return LLMGenerationResult(
        status=GenerationStatus.MALFORMED_OUTPUT, raw_output=raw_output, detail=detail
    )


__all__ = ["FakeLLMGateway", "LLMGateway", "malformed_result", "ok_result"]
