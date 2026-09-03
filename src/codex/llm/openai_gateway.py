"""Concrete, OpenAI-backed `LLMGateway` implementation (D10's own
Protocol, TAD §43) -- the first real, vendor-backed Gateway this project
ships.

**Isolation behind the existing boundary.** Nothing outside this module
knows or needs to know that OpenAI specifically backs it:
`OpenAIGateway.generate(request) -> LLMGenerationResult` satisfies
`codex.llm.gateway.LLMGateway` exactly, so any caller written against
the Protocol (`codex.benchmark.harness.run_corpus` included) can use it
unchanged. `codex.llm.gateway`/`codex.llm.schema` (D10's finished
contract) are not modified.

**Provider isolation / no fallback, by construction.** This module talks
to exactly one, hardcoded endpoint (`CHAT_COMPLETIONS_URL`, naming
`api.openai.com` -- never assembled from caller-supplied
host/base-url configuration) and imports nothing from any other
provider's SDK. There is no second code path for `generate()` to
silently switch to. An authentication failure (`Codex_open_API_key`
unset, or a 401/403 HTTP response) raises `OpenAIAuthenticationError`
rather than returning a degraded-but-quiet result or trying anything
else -- "fail loudly, never switch providers," verbatim.

**Authentication.** The API key is read from the `Codex_open_API_key`
process environment variable inside `generate()`, on every call --
never cached on `self`, never logged, never printed, never embedded in
a persisted artifact. Every error path in this module routes its
message through `_redact` before it is ever stored or raised, so even a
message that happened to echo request/response text cannot carry the
key or an `Authorization` header value.

**Dependency-free by design**, matching this project's own D5/D7
precedent (`SCIPAdapter`'s dependency-free protobuf decoder,
`AstCallsAdapter`/`PyprojectDependencyAdapter`'s stdlib-only
extraction): uses only `urllib.request`/`json` from the standard
library. No `openai` or `requests` package is added to
`pyproject.toml`.

**Metadata beyond `LLMGenerationResult`.** D10's closed
`LLMGenerationResult` (TAD §43-44) carries no token-usage or
served-model field -- not modified here to add one, since that contract
is D10's finished surface, not this checkpoint's to reopen. Instead,
each `generate()` call records a `ResponseMetadata` (exact served
model, per-call token usage) on `self.last_response_metadata` -- an
explicit, documented, opt-in side channel a caller MAY read
(`getattr(gateway, "last_response_metadata", None)`) without it being
part of the `LLMGateway` Protocol itself. Reset to `None` at the start
of every `generate()` call so a stale value is never mistaken for the
current one.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from codex.llm.gateway import GenerationStatus, LLMGenerationResult, LLMRequest
from codex.llm.schema import StructuredAnswer

API_KEY_ENV_VAR = "Codex_open_API_key"
"""The one and only place this module reads credentials from -- never a
`.env` file, config file, or hardcoded value."""

CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
"""The one and only endpoint this module ever contacts -- a fixed
module-level constant, never assembled from a caller-supplied
host/base-url, so there is no configuration surface that could redirect
a request anywhere else (in particular: never Anthropic's API)."""

DEFAULT_MODEL = "gpt-4o-mini"
"""The *requested* model when a caller does not override it. The
*served* model -- what OpenAI's own response reports -- is always what
gets recorded as authoritative (`ResponseMetadata.served_model`), never
assumed to equal this constant."""

DEFAULT_MAX_COMPLETION_TOKENS = 1024
"""A conservative, fixed cap on the completion -- not derived from
`LLMRequest.token_budget` (TAD/HLRD never define a Codex `token_budget`
-> OpenAI `max_tokens` mapping, and inventing one here would be the same
"undefined formula" pattern `codex.evaluation.evaluate` already
documents refusing to do elsewhere in this project). `StructuredAnswer`
JSON is short; this is comfortably sufficient for this milestone's
development-corpus cases."""

_DEFAULT_TIMEOUT_SECONDS = 60.0

_KEY_LIKE_RE = re.compile(r"sk-[A-Za-z0-9_-]{10,}")
_BEARER_RE = re.compile(r"Bearer\s+\S+")


def _redact(text: str) -> str:
    """Defense in depth: strip anything shaped like an OpenAI API key or
    a `Bearer` header value from `text` before it is ever stored or
    raised. Applied on every error path in this module, even ones that
    should never structurally contain the key -- redaction is never
    conditional on "should be safe already."""
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _KEY_LIKE_RE.sub("[REDACTED]", text)
    return text


class OpenAIGatewayError(RuntimeError):
    """Raised when `OpenAIGateway.generate()` cannot obtain a usable
    response from OpenAI at all -- missing credentials, a transport
    failure, or a non-2xx HTTP response.

    Deliberately **not** folded into `GenerationStatus` (D10's closed,
    four-value enum, TAD §43-44): those values describe a *model's*
    disposition once its response pipeline was actually reached
    (`OK`/`MALFORMED_OUTPUT`) or a budget condition
    (`TIMEOUT`/`BUDGET_EXCEEDED`) -- none of them honestly describes
    "never reached the model at all," and stretching one to cover it
    would misrepresent what happened (the same "clearly represent the
    state" discipline `codex.query_understanding.engine.
    UnderstandingStatus` already documents). The message is always
    passed through `_redact` first."""


class OpenAIAuthenticationError(OpenAIGatewayError):
    """Missing `Codex_open_API_key`, or a 401/403 response from OpenAI
    itself. "Authentication failures fail loudly rather than switching
    providers" (this checkpoint's own explicit requirement) -- raised,
    never silently swallowed into a returned result. There is
    structurally no other provider this Gateway could switch to: it
    imports nothing from any other provider's SDK and contacts exactly
    one hardcoded endpoint."""


@dataclass(frozen=True)
class ResponseMetadata:
    """One `generate()` call's provider-reported metadata beyond what
    `LLMGenerationResult` itself carries -- read via
    `OpenAIGateway.last_response_metadata`, never part of the
    `LLMGateway` Protocol itself."""

    served_model: str | None
    usage_prompt_tokens: int | None
    usage_completion_tokens: int | None
    usage_total_tokens: int | None


def _read_api_key() -> str:
    key = os.environ.get(API_KEY_ENV_VAR)
    if not key:
        raise OpenAIAuthenticationError(
            f"{API_KEY_ENV_VAR} is not set in the process environment -- "
            "OpenAIGateway reads credentials only from this variable, never "
            "a .env file, config file, or hardcoded value"
        )
    return key


class OpenAIGateway:
    """`LLMGateway` (D10's Protocol) implemented against OpenAI's Chat
    Completions API. `model` is the *requested* model identifier;
    `provider`/`requested_model` are exposed so a caller (e.g. the
    benchmark harness) can record what was configured without
    duplicating the string."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS,
    ) -> None:
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_completion_tokens = max_completion_tokens
        self.last_response_metadata: ResponseMetadata | None = None

    @property
    def requested_model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "openai"

    def generate(self, request: LLMRequest) -> LLMGenerationResult:
        self.last_response_metadata = None
        api_key = _read_api_key()
        body = self._build_body(request)
        try:
            raw_body = self._post(api_key, body)
        except _TimeoutSignal as exc:
            return LLMGenerationResult(
                status=GenerationStatus.TIMEOUT,
                detail=_redact(
                    f"OpenAI request exceeded {self._timeout_seconds:.1f}s: {exc}"
                ),
            )
        payload = json.loads(raw_body.decode("utf-8"))
        return self._parse(payload)

    def _build_body(self, request: LLMRequest) -> dict[str, Any]:
        instructions = (
            "You are Codex's structured-answer generator. Respond with a "
            "single JSON object matching exactly this JSON Schema, and "
            "nothing else (no markdown fences, no prose outside the JSON "
            f"object):\n{json.dumps(request.response_schema)}"
        )
        user_content = json.dumps(
            {
                "query": request.query_text,
                "evidence_package": request.evidence_package.model_dump(mode="json"),
            }
        )
        return {
            "model": self._model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": self._max_completion_tokens,
        }

    def _post(self, api_key: str, body: dict[str, Any]) -> bytes:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            CHAT_COMPLETIONS_URL,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_seconds) as resp:
                return resp.read()
        except TimeoutError as exc:
            raise _TimeoutSignal(str(exc)) from exc
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            message = _redact(f"OpenAI returned HTTP {exc.code}: {raw}")
            if exc.code in (401, 403):
                raise OpenAIAuthenticationError(message) from exc
            raise OpenAIGatewayError(message) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise _TimeoutSignal(str(exc.reason)) from exc
            raise OpenAIGatewayError(
                _redact(f"transport failure contacting OpenAI: {exc}")
            ) from exc

    def _parse(self, payload: dict[str, Any]) -> LLMGenerationResult:
        usage = payload.get("usage") or {}
        self.last_response_metadata = ResponseMetadata(
            served_model=payload.get("model"),
            usage_prompt_tokens=usage.get("prompt_tokens"),
            usage_completion_tokens=usage.get("completion_tokens"),
            usage_total_tokens=usage.get("total_tokens"),
        )
        content = payload["choices"][0]["message"]["content"]
        try:
            answer = StructuredAnswer.model_validate_json(content)
        except (ValidationError, json.JSONDecodeError) as exc:
            return LLMGenerationResult(
                status=GenerationStatus.MALFORMED_OUTPUT,
                raw_output=content,
                detail=_redact(str(exc)),
            )
        return LLMGenerationResult(
            status=GenerationStatus.OK, answer=answer, raw_output=content
        )


class _TimeoutSignal(Exception):
    """Internal-only: lets `_post` unwind out of two different except
    clauses (a direct `TimeoutError` and a `URLError` wrapping one) to
    one place, where `generate()` turns it into a proper
    `LLMGenerationResult(status=GenerationStatus.TIMEOUT)` -- a
    legitimate, honestly-representable closed-enum value (unlike an
    auth/transport failure, which raises `OpenAIGatewayError` instead;
    see this module's own docstring). Never escapes `generate()`."""


__all__ = [
    "API_KEY_ENV_VAR",
    "CHAT_COMPLETIONS_URL",
    "DEFAULT_MAX_COMPLETION_TOKENS",
    "DEFAULT_MODEL",
    "OpenAIAuthenticationError",
    "OpenAIGateway",
    "OpenAIGatewayError",
    "ResponseMetadata",
]
