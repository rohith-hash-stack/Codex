"""Regression coverage for the API Hardening & Contract Audit: four
genuine defects found during that audit, each isolated and fixed with
its own test here.

1. `get_repository_status` silently reported `REGISTERED` for the
   entire duration of an active ingestion, disagreeing with
   `get_job_status`'s own correct `INDEXING` for the identical
   repository -- most visibly wrong on `POST /query`'s `409` body.
2. `POST /repositories`/`POST /query` had no upper bound on the
   declared `Content-Length`, an unbounded-memory-allocation risk.
3. A syntactically valid JSON body that was not a JSON object (e.g. a
   bare string), or a required field of the wrong type, reached
   internal code unguarded and surfaced as a raw internal error
   message under `500` instead of a structured `400`.
4. `OpenAIGateway`'s per-call `last_response_metadata` side channel is
   an instance attribute, not a return value -- concurrent `/query`
   requests sharing one `CodexAPI` (and therefore one Gateway
   instance) could read back a *different* concurrent request's
   metadata. Fixed by serializing exactly the `generate()` call and
   its immediate metadata read inside `CodexAPI.ask()`.

Also covers the new `GET /healthz` endpoint (process liveness,
independent of repository/Gateway state).
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from git import Actor, Repo

from codex.api.contracts import AskStatus, RepositoryPhase
from codex.api.server import serve
from codex.api.service import CodexAPI, RepositoryNotReadyError
from codex.evidence.store import InMemoryEvidenceStore
from codex.llm.gateway import GenerationStatus, LLMGenerationResult, LLMRequest
from codex.llm.schema import StructuredAnswer
from codex.ontology.entities import BaseEntityType
from codex.provider.capability import Capability
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile
from fake_ingestion_provider import DeterministicFakeAdapter

PROFILE = ProviderScoreProfile(evidence_quality=0.8, cost_factor=0.5)
DEFAULT_CAPS = frozenset({Capability.CALL_RELATIONSHIP, Capability.SYMBOL_REFERENCE})


def _make_git_repo(tmp_path: Path) -> Path:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    repo = Repo.init(repo_dir)
    (repo_dir / "README.md").write_text("hello\n")
    repo.index.add(["README.md"])
    author = Actor("Test", "test@example.com")
    repo.index.commit("initial", author=author, committer=author)
    return repo_dir


class _GatedAdapter(DeterministicFakeAdapter):
    """Blocks inside `extract()` until `gate` is set -- widens the
    window during which a repository is genuinely `INDEXING`."""

    def __init__(self, *, gate: threading.Event, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._gate = gate

    def extract(self, repository: object, capabilities: object) -> object:  # type: ignore[override]
        self._gate.wait(timeout=5.0)
        return super().extract(repository, capabilities)  # type: ignore[arg-type]


class TestRepositoryStatusDuringIngestion:
    def test_get_repository_status_reports_indexing_not_registered(
        self, tmp_path: Path
    ) -> None:
        gate = threading.Event()
        registry = CapabilityRegistry()
        adapter = _GatedAdapter(
            gate=gate,
            name="gated",
            capabilities=DEFAULT_CAPS,
            entity_paths=("a",),
            base_type=BaseEntityType.FUNCTION,
        )
        registry.register(adapter, PROFILE)
        api = CodexAPI(registry, InMemoryEvidenceStore())
        repo_dir = _make_git_repo(tmp_path)
        api.register_repository("repo1", str(repo_dir))
        handle = api.start_ingestion("repo1")
        try:
            deadline = time.monotonic() + 5.0
            status = api.get_repository_status("repo1")
            while status.phase is RepositoryPhase.REGISTERED and time.monotonic() < deadline:
                # Tiny race at the very start of the thread; retry briefly.
                status = api.get_repository_status("repo1")
            assert status.phase is RepositoryPhase.INDEXING
            # The job-scoped view must agree -- this is the inconsistency
            # the audit found, now resolved on both sides.
            job_status = api.get_job_status(handle.job_id)
            assert job_status.phase is RepositoryPhase.INDEXING
        finally:
            gate.set()

    def test_ask_returns_409_with_indexing_phase_while_ingestion_is_in_flight(
        self, tmp_path: Path
    ) -> None:
        gate = threading.Event()
        registry = CapabilityRegistry()
        adapter = _GatedAdapter(
            gate=gate,
            name="gated",
            capabilities=DEFAULT_CAPS,
            entity_paths=("a",),
            base_type=BaseEntityType.FUNCTION,
        )
        registry.register(adapter, PROFILE)
        gateway = FakeLLMGatewayForHardeningTest()
        api = CodexAPI(registry, InMemoryEvidenceStore(), gateway=gateway)
        repo_dir = _make_git_repo(tmp_path)
        api.register_repository("repo1", str(repo_dir))
        api.start_ingestion("repo1")
        try:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    api.ask("repo1", "What calls a?")
                except RepositoryNotReadyError as exc:
                    assert exc.phase is RepositoryPhase.INDEXING
                    return
                time.sleep(0.005)
            raise AssertionError("never observed RepositoryNotReadyError during ingestion")
        finally:
            gate.set()


@dataclass
class _FakeResponseMetadata:
    served_model: str


class FakeLLMGatewayForHardeningTest:
    """A minimal `LLMGateway` used only in this file -- distinct from
    `tests/fake_llm_gateway.FakeLLMGateway`, since the concurrency test
    below needs to *reproduce* the exact racy side-channel pattern
    `OpenAIGateway` itself uses: `generate()`'s return value
    (`LLMGenerationResult`) is an ordinary local object, safe by
    construction, so asserting on it alone (e.g. `response.answer`)
    would never actually exercise the bug. The real hazard is
    `CodexAPI.ask()` separately reading `self._gateway.
    last_response_metadata` *after* `generate()` returns -- so this
    fake, like the real `OpenAIGateway`, records a per-call marker onto
    that shared instance attribute, and `ask()`'s `_model_metadata()`
    reads it back through `AskResponse.model.served_model`, which is
    what the test below actually asserts on. `_InterleavingGateway`
    (below) deterministically controls *when* each call's `generate()`
    returns, rather than relying on timing to widen the race window."""

    def __init__(self) -> None:
        self.last_response_metadata: _FakeResponseMetadata | None = None
        self.provider = "fake"
        self.requested_model = "fake-model"

    def generate(self, request: LLMRequest) -> LLMGenerationResult:
        self.last_response_metadata = None
        marker = request.query_text
        self.last_response_metadata = _FakeResponseMetadata(served_model=marker)
        result = LLMGenerationResult(
            status=GenerationStatus.OK,
            answer=StructuredAnswer(explanation=marker, claims=[]),
        )
        self._before_return(marker)
        return result

    def _before_return(self, marker: str) -> None:
        """Hook called after `last_response_metadata` is set but before
        `generate()` returns -- a no-op by default, overridden below to
        deterministically hold the "first" call open long enough for a
        second, concurrent call to run to completion and overwrite the
        shared attribute."""


class _InterleavingGateway(FakeLLMGatewayForHardeningTest):
    """Deterministically reproduces the exact interleaving that
    corrupts `last_response_metadata` without `CodexAPI._llm_lock`:
    the *first* `generate()` call is held open (after writing its own
    marker) until a *second*, concurrent call has been given a chance
    to run to completion and overwrite that shared attribute -- then
    the first call is allowed to return. Without serialization, the
    first call's own `ask()` then reads back the *second* call's
    marker. Deterministic by construction (event-coordinated), not
    reliant on GIL/scheduler luck."""

    def __init__(self) -> None:
        super().__init__()
        self._first_call_lock = threading.Lock()
        self._first_call_claimed = False
        self.first_call_wrote_metadata = threading.Event()
        self.let_first_call_return = threading.Event()

    def _before_return(self, marker: str) -> None:
        with self._first_call_lock:
            is_first = not self._first_call_claimed
            self._first_call_claimed = True
        if is_first:
            self.first_call_wrote_metadata.set()
            # A generous safety-net timeout only -- must never be
            # anywhere near the short, deliberate window (below) the
            # test itself uses to confirm the second call is genuinely
            # blocked; the two must not race each other.
            if not self.let_first_call_return.wait(timeout=20.0):
                raise AssertionError("test never released the first generate() call")


class TestConcurrentQuerySafety:
    def test_concurrent_ask_calls_never_cross_contaminate_gateway_metadata(
        self, tmp_path: Path
    ) -> None:
        """Deterministic reproduction of the race
        `CodexAPI._llm_lock` fixes: thread A's `generate()` call
        writes its own marker to the shared `last_response_metadata`,
        then (via `_InterleavingGateway`) is held open until thread
        B's *entire* `ask()` call -- including B's own `generate()`
        and B's own metadata read -- has completed and overwritten
        that same shared attribute. A is then released.

        Without `CodexAPI._llm_lock`: A's `generate()` returns and A's
        `ask()` reads `last_response_metadata` -- but B has already
        overwritten it, so A incorrectly gets B's marker.
        With the lock: B's own `ask()` cannot even acquire
        `self._llm_lock` (held by A for the whole locked block) until
        A's call -- generation *and* metadata read together -- has
        fully completed, so B cannot run early enough to corrupt A's
        read.
        """
        registry = CapabilityRegistry()
        registry.register(
            DeterministicFakeAdapter(
                name="fake",
                capabilities=DEFAULT_CAPS,
                entity_paths=("a", "b"),
                relationship_pairs=(("a", "b"),),
                base_type=BaseEntityType.FUNCTION,
            ),
            PROFILE,
        )
        gateway = _InterleavingGateway()
        api = CodexAPI(registry, InMemoryEvidenceStore(), gateway=gateway)
        repo_dir = _make_git_repo(tmp_path)
        api.register_repository("repo1", str(repo_dir))
        handle = api.start_ingestion("repo1")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if api.get_job_status(handle.job_id).phase is RepositoryPhase.READY:
                break
            time.sleep(0.01)

        results: dict[str, object] = {}
        errors: list[BaseException] = []

        def _run(label: str) -> None:
            try:
                results[label] = api.ask("repo1", f"What calls b? [{label}]")
            except BaseException as exc:  # noqa: BLE001 - captured for the assertion below
                errors.append(exc)

        thread_a = threading.Thread(target=_run, args=("A",))
        thread_a.start()
        assert gateway.first_call_wrote_metadata.wait(timeout=5.0), (
            "thread A's generate() never reached its hold point"
        )

        thread_b = threading.Thread(target=_run, args=("B",))
        thread_b.start()
        # A short, deliberate window -- long enough for B to run to
        # completion *if* nothing is blocking it (the unlocked/buggy
        # case), short enough to stay well clear of `_before_return`'s
        # own 20s safety-net timeout on A's side (the two must never
        # race each other). Whether B is still blocked here is an
        # implementation detail of the fix, not itself asserted on --
        # only the final results, below, are.
        thread_b.join(timeout=0.5)

        gateway.let_first_call_return.set()
        thread_a.join(timeout=10.0)
        thread_b.join(timeout=10.0)
        assert not thread_a.is_alive(), "thread A never returned after being released"
        assert not thread_b.is_alive(), "thread B never completed"

        assert not errors, errors
        assert results["B"].status is AskStatus.OK
        assert results["B"].answer == "What calls b? [B]"
        assert results["B"].model.served_model == "What calls b? [B]"

        # The actual regression: thread A's own response must reflect
        # thread A's own request, never thread B's -- even though B
        # ran (and overwrote the shared side channel) entirely while
        # A's generate() call was held open.
        assert results["A"].status is AskStatus.OK
        assert results["A"].answer == "What calls b? [A]"
        assert results["A"].model.served_model == "What calls b? [A]"


@pytest.fixture
def running_server_no_gateway() -> Iterator[str]:
    registry = CapabilityRegistry()
    api = CodexAPI(registry, InMemoryEvidenceStore())
    server = serve(api, port=0)
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base_url
    finally:
        server.shutdown()
        server.server_close()


class TestHealthz:
    def test_healthz_returns_ok(self, running_server_no_gateway: str) -> None:
        with urllib.request.urlopen(f"{running_server_no_gateway}/healthz", timeout=5) as resp:
            assert resp.status == 200
            assert json.loads(resp.read()) == {"status": "ok"}

    def test_healthz_independent_of_repository_state(
        self, running_server_no_gateway: str
    ) -> None:
        # No repository was ever registered, and no Gateway is
        # configured -- /healthz must still report ok.
        with urllib.request.urlopen(f"{running_server_no_gateway}/healthz", timeout=5) as resp:
            assert resp.status == 200


class TestRequestBodyHardening:
    def test_non_object_json_body_returns_structured_400(
        self, running_server_no_gateway: str
    ) -> None:
        req = urllib.request.Request(
            f"{running_server_no_gateway}/repositories",
            data=b'"just a string"',
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)
        assert exc_info.value.code == 400
        payload = json.loads(exc_info.value.read())
        assert "error" in payload
        assert "Traceback" not in str(payload["error"])

    def test_non_string_required_field_returns_structured_400(
        self, running_server_no_gateway: str
    ) -> None:
        req = urllib.request.Request(
            f"{running_server_no_gateway}/repositories",
            data=json.dumps({"repository_id": 123, "local_path": 456}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)
        assert exc_info.value.code == 400
        payload = json.loads(exc_info.value.read())
        assert "error" in payload
        assert "Traceback" not in str(payload["error"])

    def test_oversized_declared_content_length_returns_413_before_reading_body(
        self, running_server_no_gateway: str
    ) -> None:
        host_port = running_server_no_gateway.removeprefix("http://")
        host, port_str = host_port.split(":")
        sock = socket.create_connection((host, int(port_str)), timeout=5)
        try:
            declared_length = 5_000_000
            partial_body = b'{"repository_id": "x"'  # deliberately never completed
            request_bytes = (
                f"POST /repositories HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {declared_length}\r\n\r\n"
            ).encode() + partial_body
            sock.sendall(request_bytes)
            sock.settimeout(5)
            # Codex validation continuation (test-contract fix, not a
            # production defect): a single `recv()` call is not guaranteed
            # by TCP semantics to return the whole response in one read --
            # under CPU/scheduling load (reproduced directly: this test
            # failed once in a full-suite run, passed reliably five times
            # in isolation and once more in a full clean re-run), the
            # headers and body can arrive in separate reads, truncating
            # this test's own single-`recv()` snapshot before "exceeds the"
            # ever appears. The server already sets `close_connection =
            # True` on this exact path (`_read_json_object_body`) before
            # writing the response, so looping until the peer closes the
            # connection reliably collects the complete response without
            # guessing a byte count or racing a fixed sleep.
            chunks = bytearray()
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.extend(chunk)
            response = chunks.decode(errors="replace")
            assert response.startswith("HTTP/1.1 413")
            assert "exceeds the" in response
        finally:
            sock.close()
