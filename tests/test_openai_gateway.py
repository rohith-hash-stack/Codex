"""Tests for `codex.llm.openai_gateway.OpenAIGateway`.

The network is always mocked (`unittest.mock.patch` on
`urllib.request.urlopen`) -- no real API key is used, and no real
network call is ever attempted by this test module.
"""

from __future__ import annotations

import io
import json
import urllib.error
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from codex.graph.version import GraphVersion
from codex.llm.gateway import GenerationStatus, LLMRequest
from codex.llm.openai_gateway import (
    API_KEY_ENV_VAR,
    CHAT_COMPLETIONS_URL,
    DEFAULT_MAX_COMPLETION_TOKENS,
    DEFAULT_MODEL,
    OpenAIAuthenticationError,
    OpenAIGateway,
    OpenAIGatewayError,
)
from codex.llm.schema import StructuredAnswer
from codex.planner.mss import EvidencePackage

FAKE_KEY = "sk-testonly-0123456789abcdefghijklmno"


def _request() -> LLMRequest:
    graph_version = GraphVersion(
        version_id="gv1",
        repository_id="r",
        repository_revision="rev",
        created_at=datetime.now(UTC),
    )
    package = EvidencePackage(
        graph_version=graph_version,
        query_identity="q1",
        entities=[],
        relationships=[],
        evidence=[],
    )
    return LLMRequest(
        query_text="What calls foo?",
        evidence_package=package,
        response_schema=StructuredAnswer.model_json_schema(),
        token_budget=4000,
        latency_budget_ms=5000,
    )


class _FakeHTTPResponse:
    def __init__(self, body: bytes) -> None:
        self._buffer = io.BytesIO(body)

    def read(self) -> bytes:
        return self._buffer.read()

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


def _chat_completion_payload(
    *,
    content: str,
    model: str = "gpt-4o-mini-2024-07-18",
    finish_reason: str = "stop",
    completion_tokens: int = 30,
) -> bytes:
    return json.dumps(
        {
            "id": "chatcmpl-test",
            "model": model,
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": completion_tokens,
                "total_tokens": 120 + completion_tokens,
            },
        }
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# Missing API key
# ---------------------------------------------------------------------------


def test_missing_api_key_raises_authentication_error_without_any_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    gateway = OpenAIGateway()
    with patch("codex.llm.openai_gateway.urllib.request.urlopen") as mock_urlopen:
        with pytest.raises(OpenAIAuthenticationError, match=API_KEY_ENV_VAR):
            gateway.generate(_request())
        mock_urlopen.assert_not_called()


def test_empty_api_key_also_raises_authentication_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV_VAR, "")
    gateway = OpenAIGateway()
    with pytest.raises(OpenAIAuthenticationError):
        gateway.generate(_request())


# ---------------------------------------------------------------------------
# Successful request
# ---------------------------------------------------------------------------


def test_successful_request_returns_ok_with_parsed_structured_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    answer_json = json.dumps({"explanation": "x calls foo", "claims": []})
    response_bytes = _chat_completion_payload(content=answer_json)

    with patch("codex.llm.openai_gateway.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _FakeHTTPResponse(response_bytes)
        gateway = OpenAIGateway()
        result = gateway.generate(_request())

    assert result.status is GenerationStatus.OK
    assert result.answer == StructuredAnswer(explanation="x calls foo", claims=[])
    assert result.raw_output == answer_json


def test_successful_request_posts_to_the_hardcoded_chat_completions_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    response_bytes = _chat_completion_payload(
        content=json.dumps({"explanation": "x", "claims": []})
    )

    with patch("codex.llm.openai_gateway.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _FakeHTTPResponse(response_bytes)
        OpenAIGateway().generate(_request())

        sent_request = mock_urlopen.call_args.args[0]
        assert sent_request.full_url == CHAT_COMPLETIONS_URL
        assert sent_request.get_header("Authorization") == f"Bearer {FAKE_KEY}"
        assert sent_request.get_method() == "POST"


# ---------------------------------------------------------------------------
# Malformed / error response
# ---------------------------------------------------------------------------


def test_malformed_json_content_returns_malformed_output_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    response_bytes = _chat_completion_payload(content="not valid json at all")

    with patch("codex.llm.openai_gateway.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _FakeHTTPResponse(response_bytes)
        result = OpenAIGateway().generate(_request())

    assert result.status is GenerationStatus.MALFORMED_OUTPUT
    assert result.raw_output == "not valid json at all"
    assert result.answer is None


def test_schema_invalid_json_content_returns_malformed_output() -> None:
    """Valid JSON, but missing `StructuredAnswer`'s required `explanation`
    field -- still `MALFORMED_OUTPUT`, never coerced into a fabricated
    answer (D10.2's own "reject malformed claims" discipline)."""
    with patch.dict("os.environ", {API_KEY_ENV_VAR: FAKE_KEY}):
        content = json.dumps({"claims": []})  # no "explanation"
        response_bytes = _chat_completion_payload(content=content)
        with patch("codex.llm.openai_gateway.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _FakeHTTPResponse(response_bytes)
            result = OpenAIGateway().generate(_request())

    assert result.status is GenerationStatus.MALFORMED_OUTPUT
    assert result.raw_output == content


def test_http_error_response_raises_openai_gateway_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    http_error = urllib.error.HTTPError(
        CHAT_COMPLETIONS_URL,
        500,
        "Internal Server Error",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b'{"error": "boom"}'),
    )

    with patch("codex.llm.openai_gateway.urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(OpenAIGatewayError, match="HTTP 500"):
            OpenAIGateway().generate(_request())


def test_timeout_returns_timeout_status_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    with patch(
        "codex.llm.openai_gateway.urllib.request.urlopen",
        side_effect=TimeoutError("timed out"),
    ):
        result = OpenAIGateway().generate(_request())

    assert result.status is GenerationStatus.TIMEOUT
    assert result.detail is not None


# ---------------------------------------------------------------------------
# Regression: "Diagnose & Fix OpenAI Malformed Output" checkpoint.
#
# Root cause (confirmed against real gpt-4o-mini-2024-07-18 via
# scripts/diagnose_openai_malformed.py): the codex-self-dev-v0
# "build_canonical_id" case's real response came back with
# finish_reason="length" and completion_tokens exactly equal to the old
# DEFAULT_MAX_COMPLETION_TOKENS=1024 cap, content ending mid-string
# ("Unterminated string... at char 2632 of 2633") -- a textbook
# completion-budget truncation, not a model-quality, prompt-size, or
# gateway-parsing issue. scripts/experiment_openai_max_tokens.py proved
# raising the cap to 4096 resolves it (finish_reason="stop", 27/27
# claims parsed, only 2149 of 4096 tokens actually used) -- now the
# default.
# ---------------------------------------------------------------------------


def test_default_max_completion_tokens_is_4096_not_the_original_undersized_1024() -> None:
    """Regression guard against ever silently reverting the fix: the
    default gateway must request `max_tokens=4096`, not the original
    `1024` that truncated the real `build_canonical_id` response."""
    gateway = OpenAIGateway()
    body = gateway._build_body(_request())  # noqa: SLF001 - asserting the actual wire value
    assert body["max_tokens"] == DEFAULT_MAX_COMPLETION_TOKENS
    assert body["max_tokens"] == 4096
    assert body["max_tokens"] != 1024


def test_finish_reason_length_with_truncated_json_is_malformed_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduces the exact real failure signature found during
    diagnosis: `finish_reason="length"` and content that ends mid-string
    (not a complete JSON object) -- must be classified
    `MALFORMED_OUTPUT`, with `finish_reason` recorded so a future case
    like this is triageable without a one-off diagnostic script."""
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    truncated_content = (
        '{"explanation": "x calls many things", "claims": '
        '[{"subject": "a", "predicate": "CALLS", "object": "b", '
        '"claim_type": "FACT"}, {"subject": "c'
    )  # deliberately cut off mid-string, exactly like the real response
    response_bytes = _chat_completion_payload(
        content=truncated_content, finish_reason="length", completion_tokens=1024
    )

    with patch("codex.llm.openai_gateway.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _FakeHTTPResponse(response_bytes)
        gateway = OpenAIGateway()
        result = gateway.generate(_request())

    assert result.status is GenerationStatus.MALFORMED_OUTPUT
    assert result.raw_output == truncated_content
    assert gateway.last_response_metadata is not None
    assert gateway.last_response_metadata.finish_reason == "length"
    assert gateway.last_response_metadata.usage_completion_tokens == 1024


def test_finish_reason_stop_with_complete_json_is_ok_and_finish_reason_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same shape of case, but completing normally under the fixed
    cap: `finish_reason="stop"`, valid JSON -> `OK`, with `finish_reason`
    recorded (not just inferred from success)."""
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    complete_content = json.dumps(
        {
            "explanation": "x calls many things",
            "claims": [
                {"subject": "a", "predicate": "CALLS", "object": "b", "claim_type": "FACT"}
            ],
        }
    )
    response_bytes = _chat_completion_payload(
        content=complete_content, finish_reason="stop", completion_tokens=2149
    )

    with patch("codex.llm.openai_gateway.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _FakeHTTPResponse(response_bytes)
        gateway = OpenAIGateway()
        result = gateway.generate(_request())

    assert result.status is GenerationStatus.OK
    assert gateway.last_response_metadata is not None
    assert gateway.last_response_metadata.finish_reason == "stop"


# ---------------------------------------------------------------------------
# 401/403 -> authentication error, never a fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_shaped_http_status_raises_authentication_error(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    http_error = urllib.error.HTTPError(
        CHAT_COMPLETIONS_URL,
        status_code,
        "Unauthorized",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b'{"error": "invalid_api_key"}'),
    )
    with patch("codex.llm.openai_gateway.urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(OpenAIAuthenticationError):
            OpenAIGateway().generate(_request())


# ---------------------------------------------------------------------------
# Provider / model metadata
# ---------------------------------------------------------------------------


def test_requested_and_served_model_are_recorded_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    response_bytes = _chat_completion_payload(
        content=json.dumps({"explanation": "x", "claims": []}),
        model="gpt-4o-mini-2024-07-18",  # a dated snapshot, not the bare requested name
    )
    with patch("codex.llm.openai_gateway.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _FakeHTTPResponse(response_bytes)
        gateway = OpenAIGateway(model=DEFAULT_MODEL)
        gateway.generate(_request())

    assert gateway.requested_model == DEFAULT_MODEL
    assert gateway.provider == "openai"
    assert gateway.last_response_metadata is not None
    assert gateway.last_response_metadata.served_model == "gpt-4o-mini-2024-07-18"
    assert gateway.last_response_metadata.served_model != DEFAULT_MODEL
    assert gateway.last_response_metadata.usage_total_tokens == 150
    assert gateway.last_response_metadata.usage_prompt_tokens == 120
    assert gateway.last_response_metadata.usage_completion_tokens == 30


def test_last_response_metadata_resets_to_none_on_a_failed_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    response_bytes = _chat_completion_payload(
        content=json.dumps({"explanation": "x", "claims": []})
    )
    gateway = OpenAIGateway()
    with patch("codex.llm.openai_gateway.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _FakeHTTPResponse(response_bytes)
        gateway.generate(_request())
    assert gateway.last_response_metadata is not None

    with patch(
        "codex.llm.openai_gateway.urllib.request.urlopen",
        side_effect=TimeoutError("timed out"),
    ):
        gateway.generate(_request())
    assert gateway.last_response_metadata is None


# ---------------------------------------------------------------------------
# No fallback / single endpoint
# ---------------------------------------------------------------------------


def test_gateway_never_imports_anthropic_and_the_only_url_literal_is_openai() -> None:
    """Structural proof this module contacts only OpenAI: no import
    statement names an Anthropic (or any other vendor) SDK, and the
    only `https://` URL literal anywhere in the source is OpenAI's own
    Chat Completions endpoint (module docstrings are free to *mention*
    "never Anthropic" in prose -- this checks code, not comments)."""
    import ast
    import pathlib
    import re

    source_path = pathlib.Path("src/codex/llm/openai_gateway.py")
    source = source_path.read_text()

    tree = ast.parse(source)
    imported_modules = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any("anthropic" in m.lower() for m in imported_modules)

    url_literals = set(re.findall(r'https?://[^\s"\']+', source))
    assert url_literals == {CHAT_COMPLETIONS_URL}


def test_only_one_url_constant_exists_and_it_is_openai() -> None:
    assert CHAT_COMPLETIONS_URL == "https://api.openai.com/v1/chat/completions"


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------


def test_http_error_message_never_contains_the_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    # A pathological error body that echoes back a Bearer header and a
    # key-shaped string, as a hostile/misbehaving server might.
    leaking_body = f'{{"error": "Bearer {FAKE_KEY} rejected, key={FAKE_KEY}"}}'.encode()
    http_error = urllib.error.HTTPError(
        CHAT_COMPLETIONS_URL,
        400,
        "Bad Request",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(leaking_body),
    )
    with patch("codex.llm.openai_gateway.urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(OpenAIGatewayError) as excinfo:
            OpenAIGateway().generate(_request())

    assert FAKE_KEY not in str(excinfo.value)
    assert "REDACTED" in str(excinfo.value)


def test_missing_key_error_message_never_contains_a_real_key_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    try:
        OpenAIGateway().generate(_request())
    except OpenAIAuthenticationError as exc:
        assert FAKE_KEY not in str(exc)
        assert "sk-" not in str(exc)


# ---------------------------------------------------------------------------
# Raw-output preservation
# ---------------------------------------------------------------------------


def test_raw_output_preserved_verbatim_including_whitespace_quirks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(API_KEY_ENV_VAR, FAKE_KEY)
    odd_but_valid = '  {"explanation": "x", "claims": []}  '
    response_bytes = _chat_completion_payload(content=odd_but_valid)
    with patch("codex.llm.openai_gateway.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _FakeHTTPResponse(response_bytes)
        result = OpenAIGateway().generate(_request())

    assert result.raw_output == odd_but_valid
