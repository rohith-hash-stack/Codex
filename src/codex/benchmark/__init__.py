"""Reproducible real-LLM benchmark infrastructure -- the milestone
scoped by "Establish Reproducible LLM Benchmark Before OpenAI
Integration" / "Build Reproducible LLM Benchmark Infrastructure".

**What this package is:** the plumbing needed to turn a frozen,
versioned `DevelopmentCorpus` plus any `codex.llm.gateway.LLMGateway`
implementation into a reproducible `ModelRunRecord` -- real D8/D9
retrieval (unmodified), a real `LLMGateway.generate()` call, raw output
captured verbatim, and deterministic scoring via `codex.evaluation.
evaluate.evaluate` (reused, never duplicated). It exists downstream of
`codex.evaluation` specifically because that package's own boundary
tests forbid it from ever importing `codex.llm`/`codex.query_
understanding`/`codex.planner.planner` -- see `codex.benchmark.models`'s
module docstring for the full reasoning.

**What this package is explicitly NOT:**

- Not an LLM Gateway implementation. `run_corpus` takes an `LLMGateway`
  as a plain parameter; nothing here constructs, imports, or calls out
  to OpenAI, Anthropic, or any other provider's API, and nothing here
  performs network I/O of any kind (structurally checked, not merely
  claimed -- `tests/test_benchmark_no_external_calls.py`).
- Not a canonical benchmark. `codex.benchmark.dev_corpus.
  build_development_corpus` produces a small, honestly-scoped
  *development* corpus (three real `Intent` categories the two real,
  dependency-free D7 providers it uses can actually back, plus one
  negative-query/abstention case) -- see `docs/llm-benchmark-spec.md`
  for what promotion to a canonical corpus requires.
- Not a scoring engine. `score_run` is a thin, direct call into
  `codex.evaluation.evaluate.evaluate` -- no parallel metric formula is
  implemented here.
- Not a modification of graph ontology, ingestion, SCIP/AST identity
  merging, query understanding, query-shaped traversal, or production
  retrieval behavior -- every D1-D10 component this package calls
  (`understand_query`, `plan_query`, `execute_query`, `evaluate`,
  `observe_ranked_candidates`) is used exactly as D1-D13 already ship
  it, with zero changes.
"""

from codex.benchmark.dev_corpus import CORPUS_VERSION, build_development_corpus
from codex.benchmark.harness import (
    CONTEXT_CONSTRUCTION_VERSION,
    PROMPT_TEMPLATE_VERSION,
    compute_run_id,
    run_corpus,
    score_run,
)
from codex.benchmark.models import CaseRunResult, DevelopmentCorpus, ModelRunRecord

__all__ = [
    "CONTEXT_CONSTRUCTION_VERSION",
    "CORPUS_VERSION",
    "PROMPT_TEMPLATE_VERSION",
    "CaseRunResult",
    "DevelopmentCorpus",
    "ModelRunRecord",
    "build_development_corpus",
    "compute_run_id",
    "run_corpus",
    "score_run",
]
