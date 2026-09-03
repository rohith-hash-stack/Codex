"""Model-run / development-corpus data model for the real-LLM benchmark
infrastructure milestone ("Establish Reproducible LLM Benchmark Before
OpenAI Integration").

Deliberately a **new, separate** module from `codex.evaluation.models`,
not an extension of it. `codex.evaluation`'s own boundary tests
(`tests/test_evaluation_boundaries.py::test_evaluation_package_has_
minimal_dependencies` and `::test_evaluation_package_never_imports_
llm_slm_or_embedding_dependencies`) explicitly close that package's
dependency surface against `codex.llm` and `codex.query_understanding`
(D13-A/B's approved "read-only reporting layer" scope) -- so a type that
carries an `LLMGenerationResult`/`StructuredAnswer`, or a `BenchmarkCase`
extended with its query's `Intent` category, cannot live there without
reopening a deliberately-closed boundary. `codex.benchmark` sits
downstream of both `codex.evaluation` and `codex.llm`/
`codex.query_understanding`/`codex.planner`, and reuses `codex.
evaluation`'s `BenchmarkCorpus`/`BenchmarkCase`/`GroundTruthLabel`
verbatim (imported, never re-declared) rather than duplicating them --
only wrapping what they cannot themselves carry.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from codex.evaluation.models import BenchmarkCorpus
from codex.llm.gateway import GenerationStatus
from codex.llm.schema import StructuredAnswer
from codex.query_understanding.models import Intent


class DevelopmentCorpus(BaseModel):
    """A real `BenchmarkCorpus` (reused verbatim, D13-C) plus the one
    piece of per-case metadata it cannot itself carry without widening
    `codex.evaluation`'s closed dependency surface: each case's query
    `Intent` category (HLRD §57's "query category" corpus dimension).

    Deliberately named "Development", never "corpus_v1"/"canonical" --
    per this milestone's explicit instruction not to claim canonical
    status the evidence doesn't yet support. See `docs/llm-benchmark-
    spec.md` for what promotion to a canonical corpus would require.
    """

    corpus: BenchmarkCorpus
    categories: dict[str, Intent] = Field(default_factory=dict)
    """`query_id -> Intent`, covering exactly the same key set as
    `corpus.cases` -- checked by `is_complete`, never assumed."""

    @property
    def is_complete(self) -> bool:
        """`True` iff every case has a recorded category and vice
        versa -- a malformed/partial corpus is detectable, not silently
        tolerated."""
        return set(self.categories) == set(self.corpus.cases)


class CaseRunResult(BaseModel):
    """One `BenchmarkCase`'s real, captured result from one model run.

    `raw_model_output` is captured verbatim, before any parsing (D10.2's
    own "the verifier must never parse claims back out of free-form
    prose" discipline, extended here to "always keep the pre-parse
    original for reproducibility"). `retrieval_context_version` is D9's
    own real `GraphVersion.version_id` (TAD §19's composite key) -- not
    invented -- pinning exactly which graph snapshot and provider-version
    set produced this case's `EvidencePackage`.
    """

    query_id: str
    query_identity: str
    """`codex.planner.cache.compute_query_identity(contract)`, recomputed
    from the real `QueryContract` Tier-0 produced for this case's query
    text at run time -- must equal `query_id` for the case to be
    genuinely reproducible; the harness asserts this itself rather than
    trusting it silently (mirrors `codex.evaluation.benchmark.
    verify_case_execution`'s own consistency-check discipline)."""

    generation_status: GenerationStatus | None = None
    """`None` only when `error` is set -- the gateway itself raised
    before producing any `LLMGenerationResult` at all (e.g. a missing
    API key or a transport/auth failure), so there is no model
    disposition to report. Never both `None` and `error is None`
    simultaneously in practice: the harness sets exactly one of the two
    per case."""
    raw_model_output: str | None = None
    structured_answer: StructuredAnswer | None = None
    detail: str | None = None

    error: str | None = None
    """Set when the `LLMGateway.generate()` call itself raised (not a
    `GenerationStatus`-representable model disposition) -- e.g.
    `codex.llm.openai_gateway.OpenAIAuthenticationError`/
    `OpenAIGatewayError`. The harness never lets one case's gateway
    failure abort the whole corpus run, and never silently drops it
    either -- it is captured here instead. Always the gateway's own
    already-redacted message (see `codex.llm.openai_gateway._redact`);
    this field is never populated from an un-redacted exception."""

    retrieval_context_version: str
    """D9's real `RetrievalPlan.graph_version.version_id` -- the
    deterministic composite identity (repository_id + revision +
    provider_versions + schema/policy version, TAD §19) of the exact
    graph snapshot this case's evidence was retrieved from."""

    token_budget: int = Field(gt=0)
    latency_budget_ms: int = Field(gt=0)

    served_model: str | None = None
    """The exact model identifier the provider's own response reported
    (e.g. `codex.llm.openai_gateway.ResponseMetadata.served_model`) --
    never assumed from the run's configured `ModelRunRecord.model_id`.
    `None` when the gateway does not expose this (e.g. `FakeLLMGateway`,
    or a case that errored before any response was received)."""

    llm_tokens: int | None = Field(default=None, ge=0)
    """`None` until a concrete `LLMGateway` implementation reports real
    usage -- `LLMGenerationResult` (D10, unmodified by this milestone)
    carries no token/latency field today (matching `QueryTelemetryEvent.
    llm_tokens`'s own identical, already-documented gap); never
    fabricated here. Populated from `usage_total_tokens` below when a
    gateway reports one."""
    usage_prompt_tokens: int | None = Field(default=None, ge=0)
    usage_completion_tokens: int | None = Field(default=None, ge=0)
    usage_total_tokens: int | None = Field(default=None, ge=0)
    latency_ms: float | None = Field(default=None, ge=0.0)

    finish_reason: str | None = None
    """The provider's own completion-stop reason (e.g. `codex.llm.
    openai_gateway.ResponseMetadata.finish_reason`) -- `"length"` means
    the completion was cut off by the gateway's max-completion-tokens
    cap, `"stop"` means it completed normally. Recorded so a
    `MALFORMED_OUTPUT` case is triageable straight from the run record
    (was this a truncation, or a genuinely malformed generation?)
    without a one-off diagnostic script -- the exact question the
    "Diagnose & Fix OpenAI Malformed Output" checkpoint had to answer by
    hand for the `build_canonical_id` case. `None` for gateways that
    don't expose it."""


class ModelRunRecord(BaseModel):
    """One reproducible run of one model/provider against one
    `DevelopmentCorpus`/`BenchmarkCorpus` snapshot -- the "capture raw
    output, pin every reproducibility dimension" record this milestone
    exists to establish.

    Immutable once built (a plain value, like every other D13
    evaluation type): a second run against the same corpus produces a
    second, independent `ModelRunRecord`, never an in-place update to
    this one -- so two runs sharing a `corpus_version` are always
    independently diffable, never one overwriting the other.
    """

    run_id: str
    """`codex.benchmark.harness.compute_run_id(...)` -- a deterministic
    hash of every reproducibility dimension below, so two runs with
    identical dimensions always get the identical id (mirrors
    `compute_query_identity`'s own hash pattern)."""

    corpus_version: str
    repository_id: str
    repository_revision: str
    prompt_template_version: str
    context_construction_version: str
    model_id: str
    provider: str
    generated_at: datetime
    results: dict[str, CaseRunResult] = Field(default_factory=dict)


__all__ = ["CaseRunResult", "DevelopmentCorpus", "ModelRunRecord"]
