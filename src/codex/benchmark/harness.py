"""Benchmark execution harness.

    frozen corpus
        -> real repository
        -> real Codex retrieval/planner (D8 `understand_query`, D9
           `plan_query`/`execute_query`, all real and unmodified)
        -> LLMGateway
        -> raw model output
        -> deterministic scoring (`codex.evaluation.evaluate.evaluate`,
           unmodified -- never re-implemented here)
        -> reproducible `ModelRunRecord`

`run_corpus` takes an `LLMGateway` (D10's own Protocol, TAD §43) as a
plain parameter -- it never constructs, imports, or references a
concrete provider-backed implementation, and never performs network I/O
itself. This milestone's own tests only ever pass `tests.
fake_llm_gateway.FakeLLMGateway`, a deterministic, in-memory, scripted
test double -- see `tests/test_benchmark_no_external_calls.py` for a
structural (not merely conventional) proof that no module under
`codex.benchmark` imports any networking capability.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from codex.benchmark.models import CaseRunResult, DevelopmentCorpus, ModelRunRecord
from codex.evaluation.evaluate import evaluate
from codex.evaluation.models import EvaluationReport, EvaluationTrace
from codex.evaluation.observer import observe_ranked_candidates
from codex.evidence.store import EvidenceStore
from codex.graph.store import GraphReader
from codex.ingestion.models import IngestionResult
from codex.llm.gateway import LLMGateway, LLMRequest
from codex.llm.schema import StructuredAnswer
from codex.planner.cache import compute_query_identity
from codex.planner.mss import EvidencePackage
from codex.planner.planner import execute_query, plan_query
from codex.query_understanding.engine import UnderstandingStatus, understand_query
from codex.query_understanding.models import QueryContract
from codex.registry.registry import CapabilityRegistry
from codex.repository.models import RepositoryMetadata
from codex.telemetry.models import QueryTelemetryEvent

PROMPT_TEMPLATE_VERSION = "harness-request-v1"
"""Versions exactly the deterministic `LLMRequest` construction recipe
below (`query_text` + the real `EvidencePackage` + `StructuredAnswer.
model_json_schema()` + the query's own contract-derived `token_budget`/
`latency_budget_ms`) -- the same recipe `tests/test_d1_d10_integration.
py`'s own `_request()` helper already established as canonical for this
project. This is deliberately **not** a wire-level prompt string: no
concrete `LLMGateway` implementation exists yet to define one (this
milestone's own explicit scope: infrastructure only, no OpenAI
integration). Once a real Gateway exists, its own prompt-construction
version must be captured alongside this one -- a documented, deferred
gap, not invented further here."""

CONTEXT_CONSTRUCTION_VERSION = "harness-context-v1"
"""Versions the D8->D9 call *procedure* used to build each case's
`EvidencePackage` (`understand_query` -> `plan_query` -> `execute_query`,
all real and unmodified) -- changes only if this harness's own call
sequence changes. The actual *data* snapshot is separately, more
precisely pinned per case by `CaseRunResult.retrieval_context_version`
(D9's own real `GraphVersion.version_id`, TAD §19) -- this constant
versions the recipe, not the data."""


def compute_run_id(
    *,
    corpus_version: str,
    model_id: str,
    provider: str,
    prompt_template_version: str,
    context_construction_version: str,
    repository_revision: str,
) -> str:
    """Deterministic identity for one `ModelRunRecord`: two runs sharing
    every reproducibility dimension get the identical id (mirrors
    `codex.planner.cache.compute_query_identity`'s own SHA-256 hash
    pattern, never a random UUID)."""
    payload = "\x1f".join(
        [
            corpus_version,
            model_id,
            provider,
            prompt_template_version,
            context_construction_version,
            repository_revision,
        ]
    )
    return "run:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _build_request(
    query_text: str, package: EvidencePackage, contract: QueryContract
) -> LLMRequest:
    return LLMRequest(
        query_text=query_text,
        evidence_package=package,
        response_schema=StructuredAnswer.model_json_schema(),
        token_budget=contract.token_budget,
        latency_budget_ms=contract.latency_budget_ms,
    )


def run_corpus(
    corpus: DevelopmentCorpus,
    gateway: LLMGateway,
    *,
    graph: GraphReader,
    evidence_store: EvidenceStore,
    ingestion_result: IngestionResult,
    registry: CapabilityRegistry,
    repository: RepositoryMetadata,
    model_id: str,
    provider: str,
    now: datetime | None = None,
) -> tuple[ModelRunRecord, list[QueryTelemetryEvent], dict[str, EvaluationTrace]]:
    """Run every case in `corpus` through the real, unmodified D8->D9
    pipeline and `gateway`, returning a reproducible `ModelRunRecord`
    plus the telemetry events / D13-B traces needed to score it via
    `codex.evaluation.evaluate.evaluate` (never re-implemented here).

    Never mutates `corpus` -- only ever reads `corpus.corpus.cases`.
    Raises `ValueError` if a case's own query text no longer resolves
    deterministically via Tier-0 to `RESOLVED` (a corpus-authoring
    defect: every case in this milestone's development corpus is
    required to resolve deterministically, so a resolution failure is a
    genuine error, never silently skipped or given a fabricated
    `CaseRunResult`).
    """
    now = now or datetime.now(UTC)
    results: dict[str, CaseRunResult] = {}
    events: list[QueryTelemetryEvent] = []
    traces: dict[str, EvaluationTrace] = {}

    for query_id, case in corpus.corpus.cases.items():
        understanding = understand_query(
            case.query_text, repository_id=case.repository_id, now=now
        )
        resolved = understanding.status is UnderstandingStatus.RESOLVED
        if not resolved or understanding.contract is None:
            raise ValueError(
                f"benchmark case {query_id!r} ({case.query_text!r}) no longer resolves "
                f"deterministically via Tier-0 (status={understanding.status.value}) -- "
                "a corpus-authoring defect, not a runtime condition to paper over"
            )
        contract = understanding.contract
        identity = compute_query_identity(contract)
        if identity != query_id:
            raise ValueError(
                f"benchmark case {query_id!r} ({case.query_text!r}) now resolves to a "
                f"different query identity {identity!r} -- the frozen corpus is stale "
                "against the current QueryContract-producing code and must be re-frozen"
            )

        plan = plan_query(
            query_contract=contract,
            graph=graph,
            ingestion_result=ingestion_result,
            registry=registry,
            repository=repository,
        )
        package = execute_query(
            plan, graph=graph, evidence_store=evidence_store, ingestion_result=ingestion_result
        )
        traces[query_id] = observe_ranked_candidates(plan, graph)

        request = _build_request(case.query_text, package, contract)
        try:
            generation = gateway.generate(request)
        except Exception as exc:  # noqa: BLE001 - isolate one case's gateway
            # failure from the rest of the corpus run (D5 §14 precedent:
            # a single provider/case failure must never abort the whole
            # run) -- never silently discarded, captured in `error`
            # instead. The Gateway implementation itself is responsible
            # for never leaking a secret into its own exception message
            # (see e.g. `codex.llm.openai_gateway._redact`, applied on
            # every one of its own error paths before raising); this
            # harness does not attempt a second redaction pass.
            results[query_id] = CaseRunResult(
                query_id=query_id,
                query_identity=identity,
                error=str(exc),
                retrieval_context_version=plan.graph_version.version_id,
                token_budget=contract.token_budget,
                latency_budget_ms=contract.latency_budget_ms,
            )
            events.append(
                QueryTelemetryEvent.build(
                    query_id=identity,
                    graph_version=plan.graph_version,
                    query_contract=contract,
                    retrieval_plan=plan,
                    candidate_count=len(package.entities),
                    mss_size=len(package.entities),
                    llm_calls=1,
                    now=now,
                )
            )
            continue

        metadata = getattr(gateway, "last_response_metadata", None)
        results[query_id] = CaseRunResult(
            query_id=query_id,
            query_identity=identity,
            generation_status=generation.status,
            raw_model_output=generation.raw_output,
            structured_answer=generation.answer,
            detail=generation.detail,
            retrieval_context_version=plan.graph_version.version_id,
            token_budget=contract.token_budget,
            latency_budget_ms=contract.latency_budget_ms,
            served_model=getattr(metadata, "served_model", None),
            usage_prompt_tokens=getattr(metadata, "usage_prompt_tokens", None),
            usage_completion_tokens=getattr(metadata, "usage_completion_tokens", None),
            usage_total_tokens=getattr(metadata, "usage_total_tokens", None),
            llm_tokens=getattr(metadata, "usage_total_tokens", None),
        )
        events.append(
            QueryTelemetryEvent.build(
                query_id=identity,
                graph_version=plan.graph_version,
                query_contract=contract,
                retrieval_plan=plan,
                candidate_count=len(package.entities),
                mss_size=len(package.entities),
                llm_calls=1,
                now=now,
            )
        )

    run_id = compute_run_id(
        corpus_version=corpus.corpus.corpus_version,
        model_id=model_id,
        provider=provider,
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
        context_construction_version=CONTEXT_CONSTRUCTION_VERSION,
        repository_revision=repository.head_revision,
    )
    record = ModelRunRecord(
        run_id=run_id,
        corpus_version=corpus.corpus.corpus_version,
        repository_id=repository.repository_id,
        repository_revision=repository.head_revision,
        prompt_template_version=PROMPT_TEMPLATE_VERSION,
        context_construction_version=CONTEXT_CONSTRUCTION_VERSION,
        model_id=model_id,
        provider=provider,
        generated_at=now,
        results=results,
    )
    return record, events, traces


def score_run(
    events: list[QueryTelemetryEvent],
    corpus: DevelopmentCorpus,
    traces: dict[str, EvaluationTrace],
    *,
    now: datetime | None = None,
) -> EvaluationReport:
    """Thin, direct call into `codex.evaluation.evaluate.evaluate` --
    never a parallel scoring implementation (this milestone's explicit
    instruction: "do not create parallel evaluation models or duplicate
    scoring logic")."""
    return evaluate(events, corpus.corpus, traces=traces, now=now)


__all__ = [
    "CONTEXT_CONSTRUCTION_VERSION",
    "PROMPT_TEMPLATE_VERSION",
    "compute_run_id",
    "run_corpus",
    "score_run",
]
