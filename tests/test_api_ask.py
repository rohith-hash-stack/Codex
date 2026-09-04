"""Tests for `CodexAPI.ask()` (API Integration Milestone): repository ->
query -> intent/evidence requirements -> targeted graph retrieval ->
minimal sufficient grounded context -> LLM -> grounded answer.

Built on the same `DeterministicFakeAdapter`/`FakeLLMGateway` fixtures
every other planner/benchmark test in this project already uses -- no
new fake infrastructure invented. `understand_query`/`plan_query`/
`execute_query` are the real, unmodified D8/D9 pipeline; only the LLM's
own response is scripted, exactly like `tests/test_benchmark_harness.py`
already does for the benchmark harness.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from git import Actor, Repo

from codex.api.contracts import AskStatus, RepositoryPhase
from codex.api.service import (
    CodexAPI,
    LLMNotConfiguredError,
    RepositoryNotFoundError,
    RepositoryNotReadyError,
)
from codex.evidence.store import InMemoryEvidenceStore
from codex.llm.gateway import GenerationStatus, LLMGenerationResult, LLMRequest
from codex.llm.schema import Claim, ClaimType, StructuredAnswer
from codex.ontology.entities import BaseEntityType
from codex.ontology.relationships import RelationshipType
from codex.provider.capability import Capability
from codex.query_understanding.models import Intent
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile
from fake_ingestion_provider import DeterministicFakeAdapter
from fake_llm_gateway import FakeLLMGateway, malformed_result, ok_result

PROFILE = ProviderScoreProfile(evidence_quality=0.8, cost_factor=0.5)
DEFAULT_CAPS = frozenset({Capability.CALL_RELATIONSHIP, Capability.SYMBOL_REFERENCE})


class _RaisingGateway:
    """A minimal `LLMGateway` whose `generate()` always raises -- proves
    `ask()` lets a Gateway exception propagate unmodified rather than
    catching and converting it (see `LLMNotConfiguredError`'s own
    docstring in `codex.api.service`)."""

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


def _fake_adapter(
    *,
    entity_paths: tuple[str, ...],
    relationship_pairs: tuple[tuple[str, str], ...] = (),
    predicate: RelationshipType = RelationshipType.CALLS,
    base_type: BaseEntityType = BaseEntityType.FUNCTION,
) -> DeterministicFakeAdapter:
    return DeterministicFakeAdapter(
        name="fake",
        capabilities=DEFAULT_CAPS,
        entity_paths=entity_paths,
        relationship_pairs=relationship_pairs,
        predicate=predicate,
        base_type=base_type,
    )


def _wait_for_ready(api: CodexAPI, job_id: str, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = api.get_job_status(job_id)
        if status.phase in (RepositoryPhase.READY, RepositoryPhase.FAILED):
            assert status.phase == RepositoryPhase.READY, status.detail
            return
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not reach a terminal phase within {timeout}s")


def _make_ready_api(
    tmp_path: Path,
    *,
    gateway: object | None,
    repository_id: str = "repo1",
    **adapter_kwargs: object,
) -> CodexAPI:
    repo_dir = _make_git_repo(tmp_path)
    registry = CapabilityRegistry()
    registry.register(_fake_adapter(**adapter_kwargs), PROFILE)  # type: ignore[arg-type]
    api = CodexAPI(registry, InMemoryEvidenceStore(), gateway=gateway)  # type: ignore[arg-type]
    api.register_repository(repository_id, str(repo_dir))
    handle = api.start_ingestion(repository_id)
    _wait_for_ready(api, handle.job_id)
    return api


class TestSuccessfulGroundedQuery:
    def test_ok_response_carries_real_claims_and_real_evidence(self, tmp_path: Path) -> None:
        gateway = FakeLLMGateway(
            [
                ok_result(
                    StructuredAnswer(
                        explanation="foo calls bar",
                        claims=[
                            Claim(
                                subject="foo",
                                predicate=RelationshipType.CALLS,
                                object="bar",
                                claim_type=ClaimType.FACT,
                            )
                        ],
                    )
                )
            ]
        )
        api = _make_ready_api(
            tmp_path,
            gateway=gateway,
            entity_paths=("foo", "bar"),
            relationship_pairs=(("foo", "bar"),),
        )
        response = api.ask("repo1", "What calls bar?")

        assert response.status is AskStatus.OK
        assert response.intent is Intent.FIND_CALLERS
        assert response.answer == "foo calls bar"
        assert len(response.claims) == 1
        assert response.claims[0].predicate == RelationshipType.CALLS
        assert response.query_id != ""
        assert response.run_id != ""

        # Real retrieved evidence reached the response, not fabricated by
        # the API layer: the one real `foo CALLS bar` edge, unmodified.
        assert len(response.evidence_context.relationships) == 1
        edge = response.evidence_context.relationships[0]
        assert edge.relationship_type is RelationshipType.CALLS
        assert response.evidence_context.graph_version is not None
        assert len(gateway.requests) == 1
        # The Gateway received the real EvidencePackage, not a summary.
        assert len(gateway.requests[0].evidence_package.relationships) == 1

    def test_query_reaches_llm_exactly_once(self, tmp_path: Path) -> None:
        gateway = FakeLLMGateway(
            [ok_result(StructuredAnswer(explanation="ok", claims=[]))]
        )
        api = _make_ready_api(
            tmp_path,
            gateway=gateway,
            entity_paths=("foo", "bar"),
            relationship_pairs=(("foo", "bar"),),
        )
        api.ask("repo1", "What calls bar?")
        assert len(gateway.requests) == 1


class TestNegativeQuery:
    def test_negative_query_preserves_existing_abstention_behavior(self, tmp_path: Path) -> None:
        """No real relationship exists for the target -- the real
        planner marks this a negative-query candidate (unchanged D9
        behavior) and the API passes the (scripted, but representative
        of a correctly-abstaining model) empty-claims answer straight
        through, never fabricating or suppressing anything itself."""
        gateway = FakeLLMGateway(
            [ok_result(StructuredAnswer(explanation="No callers found.", claims=[]))]
        )
        api = _make_ready_api(
            tmp_path, gateway=gateway, entity_paths=("foo", "bar"), relationship_pairs=()
        )
        response = api.ask("repo1", "What calls bar?")

        assert response.status is AskStatus.OK
        assert response.claims == []
        assert response.evidence_context.relationships == []
        # The real planner's own negative-query signal reached the response.
        assert any("negative_query_result=" in lim for lim in response.evidence_context.limitations)


class TestAmbiguousHighFanOutQuery:
    def test_ambiguous_target_limitation_is_surfaced_not_hidden(self, tmp_path: Path) -> None:
        """Two real, distinct entities share the substring `bar` --
        `resolve_targets` (unmodified) legitimately matches both, and
        `execute_query`'s own existing ambiguity limitation must reach
        the API response verbatim, never resolved or hidden by the API
        layer itself."""
        gateway = FakeLLMGateway(
            [ok_result(StructuredAnswer(explanation="Ambiguous.", claims=[]))]
        )
        api = _make_ready_api(
            tmp_path,
            gateway=gateway,
            entity_paths=("pkg.Foo.bar", "pkg.Baz.bar"),
            base_type=BaseEntityType.METHOD,
        )
        response = api.ask("repo1", "What calls bar?")

        assert response.status is AskStatus.OK
        assert len(response.evidence_context.entities) == 2
        assert any(
            "ambiguous target" in lim for lim in response.evidence_context.limitations
        )


class TestGroundingIntegrity:
    """OpenAI Claim Grounding Integrity fix -- reproduces and locks in
    the fix for the confirmed defect on
    60e605b2fadd302f4a3a2cd884067dbc66d665f7: deterministic graph
    evidence contained "caller -> CALLS -> plan_query", the model's
    claim reversed it ("plan_query -> CALLS -> caller"), and the system
    still reported `status = OK`. `ask()` now runs the real,
    unmodified D10.4/D10.6 Verification Engine
    (`codex.verification.verify_claim`/`classify_claim`) against every
    `FACT`/`DERIVED` claim before it is allowed to report `AskStatus.OK`.
    """

    def test_2_reversed_claim_is_never_reported_as_grounded_ok(self, tmp_path: Path) -> None:
        """The exact reproduction: real evidence is
        caller -> CALLS -> plan_query; the (scripted, representative of
        the real model's actual observed failure mode) LLM response
        reverses it. `status` must not be OK."""
        gateway = FakeLLMGateway(
            [
                ok_result(
                    StructuredAnswer(
                        explanation="plan_query calls caller",
                        claims=[
                            Claim(
                                subject="plan_query",
                                predicate=RelationshipType.CALLS,
                                object="caller",
                                claim_type=ClaimType.FACT,
                            )
                        ],
                    )
                )
            ]
        )
        api = _make_ready_api(
            tmp_path,
            gateway=gateway,
            entity_paths=("caller", "plan_query"),
            relationship_pairs=(("caller", "plan_query"),),
        )
        response = api.ask("repo1", "What calls plan_query?")

        assert response.status is AskStatus.CLAIMS_NOT_GROUNDED
        assert response.status is not AskStatus.OK
        # Transparency: the offending claim is still visible, verbatim,
        # never silently dropped or rewritten.
        assert len(response.claims) == 1
        assert response.claims[0].subject == "plan_query"
        assert response.claims[0].object == "caller"
        assert response.detail is not None
        assert "plan_query" in response.detail and "caller" in response.detail
        # The real, correctly-oriented edge is still visible in evidence
        # -- proving the defect was in claim validation, not in
        # retrieval/evidence construction.
        assert len(response.evidence_context.relationships) == 1
        edge = response.evidence_context.relationships[0]
        assert edge.relationship_type is RelationshipType.CALLS

    def test_correctly_oriented_claim_over_the_same_evidence_passes(self, tmp_path: Path) -> None:
        """The positive control for the test above: identical evidence,
        correctly-oriented claim -> still a real, grounded OK."""
        gateway = FakeLLMGateway(
            [
                ok_result(
                    StructuredAnswer(
                        explanation="caller calls plan_query",
                        claims=[
                            Claim(
                                subject="caller",
                                predicate=RelationshipType.CALLS,
                                object="plan_query",
                                claim_type=ClaimType.FACT,
                            )
                        ],
                    )
                )
            ]
        )
        api = _make_ready_api(
            tmp_path,
            gateway=gateway,
            entity_paths=("caller", "plan_query"),
            relationship_pairs=(("caller", "plan_query"),),
        )
        response = api.ask("repo1", "What calls plan_query?")

        assert response.status is AskStatus.OK
        assert len(response.claims) == 1

    def test_8_mixed_valid_and_invalid_claims_is_not_grounded(self, tmp_path: Path) -> None:
        """One real, correctly-oriented claim plus one reversed claim in
        the same answer -- the whole response must not be OK. A single
        bad relationship claim voids the answer's grounded status, it is
        not averaged away by the good one."""
        gateway = FakeLLMGateway(
            [
                ok_result(
                    StructuredAnswer(
                        explanation="caller calls plan_query; plan_query calls caller",
                        claims=[
                            Claim(
                                subject="caller",
                                predicate=RelationshipType.CALLS,
                                object="plan_query",
                                claim_type=ClaimType.FACT,
                            ),
                            Claim(
                                subject="plan_query",
                                predicate=RelationshipType.CALLS,
                                object="caller",
                                claim_type=ClaimType.FACT,
                            ),
                        ],
                    )
                )
            ]
        )
        api = _make_ready_api(
            tmp_path,
            gateway=gateway,
            entity_paths=("caller", "plan_query"),
            relationship_pairs=(("caller", "plan_query"),),
        )
        response = api.ask("repo1", "What calls plan_query?")

        assert response.status is AskStatus.CLAIMS_NOT_GROUNDED
        assert len(response.claims) == 2  # both still visible, unmodified

    def test_nonexistent_relationship_claim_is_not_grounded(self, tmp_path: Path) -> None:
        """Both entities are real, but the LLM asserts a relationship
        that does not exist in evidence at all (not even reversed)."""
        gateway = FakeLLMGateway(
            [
                ok_result(
                    StructuredAnswer(
                        explanation="foo depends on bar",
                        claims=[
                            Claim(
                                subject="foo",
                                predicate=RelationshipType.DEPENDS_ON,
                                object="bar",
                                claim_type=ClaimType.FACT,
                            )
                        ],
                    )
                )
            ]
        )
        api = _make_ready_api(
            tmp_path,
            gateway=gateway,
            entity_paths=("foo", "bar"),
            relationship_pairs=(("foo", "bar"),),  # real edge is CALLS, not DEPENDS_ON
        )
        response = api.ask("repo1", "What calls bar?")

        assert response.status is AskStatus.CLAIMS_NOT_GROUNDED

    def test_grounding_check_is_not_fooled_by_claim_type_it_checks_every_claim(
        self, tmp_path: Path
    ) -> None:
        """The grounding check is deliberately blind to the model's own
        `claim_type` label -- matching the pre-existing D10.6
        `classify_claim`/`classify_answer`, which never inspected
        `claim_type` either (TAD §47: "The LLM's own claim_type label
        never overrides the deterministic check"). A live E2E check
        (see the fix's own report) found the real model mislabeling a
        fabricated relationship claim as `claim_type=UNKNOWN` rather
        than `FACT` -- a claim_type-based exemption would have let
        exactly that kind of claim through as `AskStatus.OK`. Here, an
        INFERENCE-labeled claim with no matching evidence at all must
        gate the response the same way an unlabeled/FACT one would."""
        gateway = FakeLLMGateway(
            [
                ok_result(
                    StructuredAnswer(
                        explanation="foo calls bar, and appears to implement its interface",
                        claims=[
                            Claim(
                                subject="foo",
                                predicate=RelationshipType.CALLS,
                                object="bar",
                                claim_type=ClaimType.FACT,
                            ),
                            Claim(
                                subject="foo",
                                predicate=RelationshipType.IMPLEMENTS,
                                object="bar",
                                claim_type=ClaimType.INFERENCE,
                            ),
                        ],
                    )
                )
            ]
        )
        api = _make_ready_api(
            tmp_path,
            gateway=gateway,
            entity_paths=("foo", "bar"),
            relationship_pairs=(("foo", "bar"),),  # only CALLS is real; IMPLEMENTS is not
        )
        response = api.ask("repo1", "What calls bar?")

        assert response.status is AskStatus.CLAIMS_NOT_GROUNDED
        assert len(response.claims) == 2  # both still visible, unmodified

    def test_a_grounded_non_fact_claim_still_passes(self, tmp_path: Path) -> None:
        """The flip side of the test above: claim_type-blindness cuts
        both ways -- a real, correctly-oriented, correctly-predicated
        claim is VERIFIED regardless of what `claim_type` the model
        happened to label it with (here, `DERIVED` on a plain direct
        `CALLS` edge -- `predicate` and `claim_type` are independent
        fields; entailment has always been blind to `claim_type`, TAD
        §47), never penalized merely for an unconventional label."""
        gateway = FakeLLMGateway(
            [
                ok_result(
                    StructuredAnswer(
                        explanation="foo calls bar",
                        claims=[
                            Claim(
                                subject="foo",
                                predicate=RelationshipType.CALLS,
                                object="bar",
                                claim_type=ClaimType.DERIVED,
                            )
                        ],
                    )
                )
            ]
        )
        api = _make_ready_api(
            tmp_path,
            gateway=gateway,
            entity_paths=("foo", "bar"),
            relationship_pairs=(("foo", "bar"),),
        )
        response = api.ask("repo1", "What calls bar?")

        assert response.status is AskStatus.OK


class TestRepositoryReadiness:
    def test_unknown_repository_raises_not_found(self) -> None:
        registry = CapabilityRegistry()
        api = CodexAPI(
            registry,
            InMemoryEvidenceStore(),
            gateway=FakeLLMGateway([ok_result(StructuredAnswer(explanation="x", claims=[]))]),
        )
        with pytest.raises(RepositoryNotFoundError):
            api.ask("ghost", "What calls bar?")

    def test_registered_but_not_yet_ingested_raises_not_ready(self, tmp_path: Path) -> None:
        repo_dir = _make_git_repo(tmp_path)
        registry = CapabilityRegistry()
        registry.register(_fake_adapter(entity_paths=("foo",)), PROFILE)
        api = CodexAPI(
            registry,
            InMemoryEvidenceStore(),
            gateway=FakeLLMGateway([ok_result(StructuredAnswer(explanation="x", claims=[]))]),
        )
        api.register_repository("repo1", str(repo_dir))
        # Deliberately does not call start_ingestion.
        with pytest.raises(RepositoryNotReadyError):
            api.ask("repo1", "What calls bar?")


class TestLLMConfiguration:
    def test_no_gateway_configured_raises_before_any_pipeline_stage(self, tmp_path: Path) -> None:
        repo_dir = _make_git_repo(tmp_path)
        registry = CapabilityRegistry()
        registry.register(_fake_adapter(entity_paths=("foo",)), PROFILE)
        api = CodexAPI(registry, InMemoryEvidenceStore())  # no gateway=
        api.register_repository("repo1", str(repo_dir))
        # Not even ingested yet -- LLMNotConfiguredError must win over
        # RepositoryNotReadyError, since it is checked first (a
        # deployment-configuration precondition, not a per-repository one).
        with pytest.raises(LLMNotConfiguredError):
            api.ask("repo1", "What calls bar?")


class TestLLMFailurePropagation:
    def test_gateway_exception_propagates_unmodified(self, tmp_path: Path) -> None:
        api = _make_ready_api(
            tmp_path,
            gateway=_RaisingGateway(RuntimeError("upstream exploded")),
            entity_paths=("foo", "bar"),
            relationship_pairs=(("foo", "bar"),),
        )
        with pytest.raises(RuntimeError, match="upstream exploded"):
            api.ask("repo1", "What calls bar?")

    def test_llm_timeout_is_represented_in_band_not_raised(self, tmp_path: Path) -> None:
        gateway = FakeLLMGateway(
            [LLMGenerationResult(status=GenerationStatus.TIMEOUT, detail="took too long")]
        )
        api = _make_ready_api(
            tmp_path,
            gateway=gateway,
            entity_paths=("foo", "bar"),
            relationship_pairs=(("foo", "bar"),),
        )
        response = api.ask("repo1", "What calls bar?")
        assert response.status is AskStatus.LLM_TIMEOUT
        assert response.detail == "took too long"
        assert response.claims == []
        assert response.answer is None


class TestMalformedOutput:
    def test_malformed_output_is_represented_honestly(self, tmp_path: Path) -> None:
        gateway = FakeLLMGateway(
            [malformed_result(raw_output="not json at all", detail="invalid JSON")]
        )
        api = _make_ready_api(
            tmp_path,
            gateway=gateway,
            entity_paths=("foo", "bar"),
            relationship_pairs=(("foo", "bar"),),
        )
        response = api.ask("repo1", "What calls bar?")
        assert response.status is AskStatus.MALFORMED_OUTPUT
        assert response.answer is None
        assert response.claims == []
        assert response.detail == "invalid JSON"


class TestUnderstandingIncomplete:
    def test_unresolvable_query_never_reaches_the_llm(self, tmp_path: Path) -> None:
        gateway = FakeLLMGateway(
            [ok_result(StructuredAnswer(explanation="should not be called", claims=[]))]
        )
        api = _make_ready_api(
            tmp_path,
            gateway=gateway,
            entity_paths=("foo", "bar"),
            relationship_pairs=(("foo", "bar"),),
        )
        # A bare, structurally-ambiguous mention of "calls" with no
        # surrounding structural pattern -- Tier-0's own category-3
        # score (0.35), below the deterministic bar, with no SLM
        # configured to disambiguate further.
        response = api.ask("repo1", "something about calls")

        assert response.status is AskStatus.UNDERSTANDING_INCOMPLETE
        assert response.query_id == ""
        assert response.run_id == ""
        assert response.claims == []
        assert response.detail is not None
        assert len(gateway.requests) == 0
