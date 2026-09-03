"""Tests for `codex.api.server` (VS Code + Nervous-System scope change,
`docs/vscode-nervous-system-architecture.md` §11).

Runs a real `ThreadingHTTPServer` on an ephemeral loopback port and
exercises it with plain `urllib` requests -- proving the transport adds
nothing and drops nothing relative to `CodexAPI` called directly, and
that malformed/missing input returns a structured 4xx body, never a
raw traceback.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from git import Actor, Repo

from codex.api.server import serve
from codex.api.service import CodexAPI
from codex.evidence.store import InMemoryEvidenceStore
from codex.llm.gateway import LLMGenerationResult, LLMRequest
from codex.llm.openai_gateway import OpenAIAuthenticationError
from codex.llm.schema import StructuredAnswer
from codex.ontology.entities import BaseEntityType
from codex.ontology.relationships import RelationshipType
from codex.provider.capability import Capability
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile
from fake_ingestion_provider import DeterministicFakeAdapter
from fake_llm_gateway import FakeLLMGateway, ok_result

PROFILE = ProviderScoreProfile(evidence_quality=0.8, cost_factor=0.5)


class _RaisingGateway:
    """Test-only `LLMGateway` whose `generate()` always raises -- used
    to prove `codex.api.server` maps a Gateway exception to a
    structured, non-traceback HTTP response (API Integration
    Milestone)."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def generate(self, request: LLMRequest) -> LLMGenerationResult:
        raise self._exc


def _make_git_repo(tmp_path: Path) -> Path:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo = Repo.init(repo_dir)
    (repo_dir / "README.md").write_text("hello\n")
    repo.index.add(["README.md"])
    author = Actor("Test", "test@example.com")
    repo.index.commit("initial", author=author, committer=author)
    return repo_dir


@pytest.fixture
def running_server(tmp_path: Path) -> Iterator[str]:
    registry = CapabilityRegistry()
    evidence_store = InMemoryEvidenceStore()
    adapter = DeterministicFakeAdapter(
        name="fake",
        capabilities=frozenset({Capability.CALL_RELATIONSHIP, Capability.SYMBOL_REFERENCE}),
        entity_paths=("a", "b"),
        relationship_pairs=(("a", "b"),),
        predicate=RelationshipType.CALLS,
        base_type=BaseEntityType.FUNCTION,
    )
    registry.register(adapter, PROFILE)
    api = CodexAPI(registry, evidence_store)
    server = serve(api, port=0)
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base_url
    finally:
        server.shutdown()
        server.server_close()


def _request(
    method: str, url: str, body: dict[str, object] | None = None
) -> tuple[int, dict[str, object]]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload: dict[str, object] = json.loads(resp.read())
            return resp.status, payload
    except urllib.error.HTTPError as err:
        payload = json.loads(err.read())
        return err.code, payload


def _wait_ready(base_url: str, job_id: str, timeout: float = 5.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, payload = _request("GET", f"{base_url}/jobs/{job_id}")
        assert status == 200
        if payload["phase"] in ("READY", "FAILED"):
            return payload
        time.sleep(0.01)
    raise AssertionError("job did not reach a terminal phase in time")


def test_full_flow_register_index_lookup_neighborhood(
    running_server: str, tmp_path: Path
) -> None:
    repo_dir = _make_git_repo(tmp_path)

    status, payload = _request(
        "POST",
        f"{running_server}/repositories",
        {"repository_id": "repo1", "local_path": str(repo_dir)},
    )
    assert status == 202
    job_id = payload["job_id"]
    assert isinstance(job_id, str)

    final = _wait_ready(running_server, job_id)
    assert final["phase"] == "READY"
    result = final["result"]
    assert isinstance(result, dict)
    provider_summary = result["provider_summary"]
    assert isinstance(provider_summary, list)
    assert provider_summary[0]["provider_name"] == "fake"

    status, payload = _request("GET", f"{running_server}/repositories/repo1/status")
    assert status == 200
    assert payload["phase"] == "READY"

    status, payload = _request("GET", f"{running_server}/symbols?repository_id=repo1&query=a")
    assert status == 200
    nodes = payload["nodes"]
    assert isinstance(nodes, list)
    assert {node["qualified_name"] for node in nodes} == {"a"}
    assert payload["edges"] == []

    status, payload = _request(
        "GET", f"{running_server}/neighborhood?repository_id=repo1&symbol=a&depth=1"
    )
    assert status == 200
    nodes = payload["nodes"]
    edges = payload["edges"]
    assert isinstance(nodes, list)
    assert isinstance(edges, list)
    assert {node["qualified_name"] for node in nodes} == {"a", "b"}
    assert len(edges) == 1
    assert edges[0]["relationship_type"] == "CALLS"
    assert payload["truncated"] is False


def test_missing_query_parameter_returns_structured_400(running_server: str) -> None:
    status, payload = _request("GET", f"{running_server}/symbols?repository_id=repo1")
    assert status == 400
    assert "error" in payload


def test_unknown_route_returns_404(running_server: str) -> None:
    status, payload = _request("GET", f"{running_server}/nonexistent")
    assert status == 404
    assert "error" in payload


def test_neighborhood_unregistered_repository_returns_404_not_traceback(
    running_server: str,
) -> None:
    status, payload = _request(
        "GET", f"{running_server}/neighborhood?repository_id=ghost&symbol=a"
    )
    assert status == 404
    assert "error" in payload
    assert "Traceback" not in str(payload["error"])

    status, payload = _request("GET", f"{running_server}/jobs/no-such-job")
    assert status == 404
    assert "error" in payload


def test_malformed_json_body_returns_structured_400(running_server: str) -> None:
    req = urllib.request.Request(
        f"{running_server}/repositories",
        data=b"not json",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=5)
    assert exc_info.value.code == 400
    payload = json.loads(exc_info.value.read())
    assert "error" in payload


def test_repositories_missing_fields_returns_structured_400(running_server: str) -> None:
    status, payload = _request("POST", f"{running_server}/repositories", {"repository_id": "x"})
    assert status == 400
    assert "error" in payload


def test_neighborhood_bad_depth_returns_structured_400(running_server: str) -> None:
    status, payload = _request(
        "GET", f"{running_server}/neighborhood?repository_id=repo1&symbol=a&depth=notanumber"
    )
    assert status == 400
    assert "error" in payload


# -- POST /query (API Integration Milestone) ---------------------------------


def _wait_ready_api(api: CodexAPI, job_id: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = api.get_job_status(job_id)
        if status.phase.value in ("READY", "FAILED"):
            assert status.phase.value == "READY", status.detail
            return
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach a terminal phase within {timeout}s")


@pytest.fixture
def make_server(tmp_path: Path) -> Iterator[Callable[..., str]]:
    """Factory fixture: `make_server(gateway, ingest=True)` builds a
    fresh `CodexAPI` (one real `foo CALLS bar` fake-provider edge,
    registered as `repo1`, ingested unless `ingest=False`), wraps it in
    a real server, and returns its base URL. Every server it starts is
    shut down at test teardown."""
    servers: list[object] = []

    def _make(gateway: object | None, *, ingest: bool = True) -> str:
        repo_dir = _make_git_repo(tmp_path)
        registry = CapabilityRegistry()
        adapter = DeterministicFakeAdapter(
            name="fake",
            capabilities=frozenset({Capability.CALL_RELATIONSHIP, Capability.SYMBOL_REFERENCE}),
            entity_paths=("foo", "bar"),
            relationship_pairs=(("foo", "bar"),),
            predicate=RelationshipType.CALLS,
            base_type=BaseEntityType.FUNCTION,
        )
        registry.register(adapter, PROFILE)
        api = CodexAPI(registry, InMemoryEvidenceStore(), gateway=gateway)  # type: ignore[arg-type]
        api.register_repository("repo1", str(repo_dir))
        if ingest:
            handle = api.start_ingestion("repo1")
            _wait_ready_api(api, handle.job_id)
        server = serve(api, port=0)
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield _make
    for server in servers:
        server.shutdown()  # type: ignore[attr-defined]
        server.server_close()  # type: ignore[attr-defined]


def test_query_endpoint_returns_grounded_answer(make_server: Callable[..., str]) -> None:
    gateway = FakeLLMGateway([ok_result(StructuredAnswer(explanation="foo calls bar", claims=[]))])
    base_url = make_server(gateway)
    status, payload = _request(
        "POST", f"{base_url}/query", {"repository_id": "repo1", "query_text": "What calls bar?"}
    )
    assert status == 200
    assert payload["status"] == "OK"
    assert payload["answer"] == "foo calls bar"
    assert payload["intent"] == "FIND_CALLERS"
    evidence_context = payload["evidence_context"]
    assert isinstance(evidence_context, dict)
    assert len(evidence_context["relationships"]) == 1


def test_query_missing_fields_returns_structured_400(make_server: Callable[..., str]) -> None:
    gateway = FakeLLMGateway([ok_result(StructuredAnswer(explanation="x", claims=[]))])
    base_url = make_server(gateway)
    status, payload = _request("POST", f"{base_url}/query", {"repository_id": "repo1"})
    assert status == 400
    assert "error" in payload


def test_query_repository_not_ready_returns_409(make_server: Callable[..., str]) -> None:
    gateway = FakeLLMGateway([ok_result(StructuredAnswer(explanation="x", claims=[]))])
    base_url = make_server(gateway, ingest=False)
    status, payload = _request(
        "POST", f"{base_url}/query", {"repository_id": "repo1", "query_text": "What calls bar?"}
    )
    assert status == 409
    assert "error" in payload


def test_query_llm_not_configured_returns_503(make_server: Callable[..., str]) -> None:
    base_url = make_server(None)
    status, payload = _request(
        "POST", f"{base_url}/query", {"repository_id": "repo1", "query_text": "What calls bar?"}
    )
    assert status == 503
    assert "error" in payload


def test_query_llm_auth_failure_returns_502_not_traceback(
    make_server: Callable[..., str],
) -> None:
    gateway = _RaisingGateway(OpenAIAuthenticationError("Codex_open_API_key is not set"))
    base_url = make_server(gateway)
    status, payload = _request(
        "POST", f"{base_url}/query", {"repository_id": "repo1", "query_text": "What calls bar?"}
    )
    assert status == 502
    assert "error" in payload
    assert "Traceback" not in str(payload["error"])


def test_query_unknown_repository_returns_404(make_server: Callable[..., str]) -> None:
    gateway = FakeLLMGateway([ok_result(StructuredAnswer(explanation="x", claims=[]))])
    base_url = make_server(gateway)
    status, payload = _request(
        "POST", f"{base_url}/query", {"repository_id": "ghost", "query_text": "What calls bar?"}
    )
    assert status == 404
    assert "error" in payload
