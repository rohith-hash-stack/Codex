"""Codex API local HTTP JSON transport (VS Code + Nervous-System scope
change).

Stdlib `http.server` only -- no web framework dependency, per the "do
not introduce unnecessary frameworks/dependencies" constraint
(`docs/vscode-nervous-system-architecture.md` §9). Binds to
``127.0.0.1`` by default: single local user, matching this cycle's MVP
scope -- auth/multi-user/remote deployment is explicitly deferred
(docs §10).

Every response is either one of `codex.api.contracts`' own JSON shapes
(produced by `CodexAPI`) or a structured ``{"error": "..."}`` body --
never a raw Python traceback reaches the client.
"""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel

from codex.api.service import (
    CodexAPI,
    GitRevisionResolutionError,
    IngestionJobNotFoundError,
    LLMNotConfiguredError,
    RepositoryNotFoundError,
    RepositoryNotReadyError,
)
from codex.llm.openai_gateway import OpenAIAuthenticationError, OpenAIGatewayError
from codex.ontology.relationships import RelationshipType

# `codex.api.service.CodexAPI.ask()` deliberately lets a Gateway
# exception propagate unmodified rather than catching it (see
# `LLMNotConfiguredError`'s own docstring) -- this transport layer is
# where a concrete Gateway's exception types get classified into an
# HTTP status, exactly mirroring `codex.api.__main__`'s own existing
# precedent of being the one place that references a concrete
# provider/adapter implementation directly. `OpenAIGateway` is the
# only concrete `LLMGateway` this project ships today; a future
# Gateway's own exception types would be added here the same way,
# without changing `service.py`'s Protocol-only boundary.


class _RequestError(Exception):
    """A client-facing error with an explicit HTTP status -- distinct
    from the exceptions `CodexAPI` itself raises, which the dispatcher
    also maps to a status code (see `_dispatch`)."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _write_json(
    handler: BaseHTTPRequestHandler, status: HTTPStatus, payload: BaseModel | dict[str, str]
) -> None:
    body = (
        payload.model_dump_json() if isinstance(payload, BaseModel) else json.dumps(payload)
    ).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _require(params: dict[str, list[str]], name: str) -> str:
    values = params.get(name)
    if not values or not values[0]:
        raise _RequestError(HTTPStatus.BAD_REQUEST, f"missing required query parameter {name!r}")
    return values[0]


def _optional_int(params: dict[str, list[str]], name: str, default: int) -> int:
    values = params.get(name)
    if not values or not values[0]:
        return default
    try:
        return int(values[0])
    except ValueError as exc:
        raise _RequestError(
            HTTPStatus.BAD_REQUEST, f"query parameter {name!r} must be an integer"
        ) from exc


def _optional_positive_body_int(body: dict[str, object], name: str) -> int | None:
    value = body.get(name)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise _RequestError(HTTPStatus.BAD_REQUEST, f"{name!r} must be a positive integer")
    return value


def _optional_relationship_types(
    params: dict[str, list[str]]
) -> list[RelationshipType] | None:
    values = params.get("relationship_type")
    if not values:
        return None
    try:
        return [RelationshipType(value) for value in values]
    except ValueError as exc:
        raise _RequestError(HTTPStatus.BAD_REQUEST, f"invalid relationship_type: {exc}") from exc


def make_handler(api: CodexAPI) -> type[BaseHTTPRequestHandler]:
    """Build a `BaseHTTPRequestHandler` subclass closed over `api`.

    Endpoints (docs §9): ``POST /repositories``, ``GET /jobs/{job_id}``,
    ``GET /repositories/{id}/status``, ``GET /symbols``,
    ``GET /neighborhood``, ``POST /query`` (API Integration Milestone,
    `docs/api-query-integration.md`).
    """

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            return  # quiet by default; a real deployment would route
            # this through codex.telemetry instead of stderr.

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            try:
                if method == "POST" and parsed.path == "/repositories":
                    self._post_repositories()
                elif method == "POST" and parsed.path == "/query":
                    self._post_query()
                elif method == "GET" and parsed.path.startswith("/jobs/"):
                    self._get_job(parsed.path[len("/jobs/") :])
                elif (
                    method == "GET"
                    and parsed.path.startswith("/repositories/")
                    and parsed.path.endswith("/status")
                ):
                    repository_id = parsed.path[len("/repositories/") : -len("/status")]
                    self._get_status(repository_id)
                elif method == "GET" and parsed.path == "/symbols":
                    self._get_symbols(params)
                elif method == "GET" and parsed.path == "/neighborhood":
                    self._get_neighborhood(params)
                else:
                    raise _RequestError(
                        HTTPStatus.NOT_FOUND, f"no route for {method} {parsed.path}"
                    )
            except _RequestError as err:
                _write_json(self, err.status, {"error": err.message})
            except RepositoryNotFoundError as exc:
                _write_json(
                    self, HTTPStatus.NOT_FOUND, {"error": f"repository not ingested: {exc}"}
                )
            except RepositoryNotReadyError as exc:
                _write_json(self, HTTPStatus.CONFLICT, {"error": str(exc)})
            except IngestionJobNotFoundError as exc:
                _write_json(self, HTTPStatus.NOT_FOUND, {"error": f"unknown job: {exc}"})
            except GitRevisionResolutionError as exc:
                _write_json(self, HTTPStatus.CONFLICT, {"error": str(exc)})
            except LLMNotConfiguredError as exc:
                _write_json(self, HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)})
            except OpenAIAuthenticationError as exc:
                # Already redacted by `openai_gateway._redact` before being
                # raised -- no second redaction pass needed here (mirrors
                # `codex.benchmark.harness.run_corpus`'s own documented
                # reasoning for the identical situation).
                _write_json(
                    self, HTTPStatus.BAD_GATEWAY, {"error": f"LLM authentication failed: {exc}"}
                )
            except OpenAIGatewayError as exc:
                _write_json(self, HTTPStatus.BAD_GATEWAY, {"error": f"LLM request failed: {exc}"})
            except KeyError as exc:
                _write_json(
                    self, HTTPStatus.NOT_FOUND, {"error": f"repository not registered: {exc}"}
                )
            except ValueError as exc:
                _write_json(self, HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except Exception as exc:  # last resort: never leak a traceback to the client
                _write_json(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

        def _post_repositories(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError as exc:
                raise _RequestError(HTTPStatus.BAD_REQUEST, f"invalid JSON body: {exc}") from exc
            repository_id = body.get("repository_id")
            local_path = body.get("local_path")
            if not repository_id or not local_path:
                raise _RequestError(
                    HTTPStatus.BAD_REQUEST, "repository_id and local_path are required"
                )
            api.register_repository(
                repository_id,
                local_path,
                remote_url=body.get("remote_url"),
                revision=body.get("revision"),
            )
            handle = api.start_ingestion(repository_id)
            _write_json(self, HTTPStatus.ACCEPTED, handle)

        def _post_query(self) -> None:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError as exc:
                raise _RequestError(HTTPStatus.BAD_REQUEST, f"invalid JSON body: {exc}") from exc
            repository_id = body.get("repository_id")
            query_text = body.get("query_text")
            if not repository_id or not query_text:
                raise _RequestError(
                    HTTPStatus.BAD_REQUEST, "repository_id and query_text are required"
                )
            token_budget = _optional_positive_body_int(body, "token_budget")
            latency_budget_ms = _optional_positive_body_int(body, "latency_budget_ms")
            response = api.ask(
                repository_id,
                query_text,
                token_budget=token_budget,
                latency_budget_ms=latency_budget_ms,
            )
            _write_json(self, HTTPStatus.OK, response)

        def _get_job(self, job_id: str) -> None:
            _write_json(self, HTTPStatus.OK, api.get_job_status(job_id))

        def _get_status(self, repository_id: str) -> None:
            _write_json(self, HTTPStatus.OK, api.get_repository_status(repository_id))

        def _get_symbols(self, params: dict[str, list[str]]) -> None:
            repository_id = _require(params, "repository_id")
            query = _require(params, "query")
            limit = _optional_int(params, "limit", 25)
            _write_json(
                self, HTTPStatus.OK, api.lookup_symbols(repository_id, query, limit=limit)
            )

        def _get_neighborhood(self, params: dict[str, list[str]]) -> None:
            repository_id = _require(params, "repository_id")
            symbol = _require(params, "symbol")
            depth = _optional_int(params, "depth", 1)
            max_nodes = _optional_int(params, "max_nodes", 50)
            max_edges = _optional_int(params, "max_edges", 100)
            relationship_types = _optional_relationship_types(params)
            _write_json(
                self,
                HTTPStatus.OK,
                api.get_neighborhood(
                    repository_id,
                    symbol,
                    depth=depth,
                    max_nodes=max_nodes,
                    max_edges=max_edges,
                    relationship_types=relationship_types,
                ),
            )

    return Handler


def serve(api: CodexAPI, *, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Build and start a server bound to ``host``:``port`` (``port=0``
    picks a free ephemeral port, readable back via
    ``server.server_address[1]``). Runs the accept loop on a daemon
    thread and returns immediately; call ``server.shutdown()`` to stop
    it."""
    handler_cls = make_handler(api)
    server = ThreadingHTTPServer((host, port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


__all__ = ["make_handler", "serve"]
