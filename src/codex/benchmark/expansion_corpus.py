"""The validation-expansion corpus (`validation-expansion-v1`) — the
"Broad LLM Grounding Validation" milestone.

**Purpose**: characterize Codex + `gpt-4o-mini` grounding robustness
across dimensions `codex-canonical-v1` didn't specifically target —
fan-out spectrum, inheritance chains, same-name ambiguity, qualified
vs. unqualified target resolution, negative-query subtypes beyond
"nonexistent symbol", and adversarial near-misses. **Not** a claim of
exhaustive coverage of every dimension × every repository combination —
an explicitly bounded, labeled first characterization pass, matching
this project's own established "narrow, honest scope; document what's
excluded" discipline.

**`codex-canonical-v1` is untouched** — this is a wholly separate,
additively-versioned corpus (`EXPANSION_CORPUS_VERSION =
"validation-expansion-v1"`), reusing `codex.evaluation`'s `BenchmarkCorpus`/
`BenchmarkCase`/`GroundTruthLabel` and `codex.benchmark.canonical_corpus`'s
own mechanical ground-truth derivation functions (`_implementers`,
`_referencers`, `_architecture_relevant`, `_direct_callers`,
`_repository_dependencies`, `_no_evidence`) verbatim — never duplicated.

**Repositories**: `codex` (self-hosted, `AstCallsAdapter`+
`PyprojectDependencyAdapter`, same as v1) and `click`/`flask` (SCIP,
same frozen indexes as v1), plus one **new** repository for genuine
size/structure contrast:

- **`itsdangerous`** (`pallets/itsdangerous`, commit
  `672971d66a2ef9f85151e53283113f33d642dabd`) — a genuinely small (15
  files total, 8 source) cryptographic-signing library with a clean,
  shallow inheritance chain (`BadData` <- `BadSignature` <- `BadHeader`/
  `BadTimeSignature` <- `SignatureExpired`) and several near-single-match
  ("low fan-out") symbol names — the size/density contrast `click`
  (79 files)/`flask` (83 files) don't provide. Ingested the same way as
  `click`/`flask`: `SCIPAdapter` alone, fed from a real, frozen index
  (`tests/fixtures/benchmark/scip/itsdangerous_sample.scip`, generated
  once via `scip-python@0.6.6` against the pinned commit, never
  vendoring the source tree — the same precedent `codex-canonical-v1`
  already established).

**Multihop honesty note**: TAD's own depth model
(`codex.planner.planner._BASE_DEPTH_BY_INTENT`) caps at depth 2 for the
two intents this environment's providers can back at that depth
(`ARCHITECTURE_ANALYSIS`, `FIND_IMPACT` -- the latter only partially,
since its full evidence set needs `DATA_FLOW`). Genuine depth-3
traversal is gated on `TRACE_EXECUTION`'s multi-hop capability, which
also needs `DATA_FLOW` (`CodeQLAdapter`, unavailable in this
environment — the same pre-existing, already-documented gap
`codex-canonical-v1`'s own report records). This corpus tests 1-hop
(`FIND_CALLERS`/`FIND_IMPLEMENTATIONS`/`FIND_REFERENCES`/
`FIND_DEPENDENCIES`) and 2-hop (`ARCHITECTURE_ANALYSIS`) honestly; it
does **not** fabricate a 3-hop case with capabilities that don't exist.

**Fan-out reference conditions**: rather than re-spending real API
budget re-querying cases `codex-canonical-v1` already ran, this corpus's
own report cites those results directly as controlled reference points
across the fan-out spectrum: `UsageError` (7 targets, well-grounded),
`ParamType` (24, well-grounded), `plan_query` (94, well-grounded),
`Scaffold` (47 targets but only 5 real relationships — the known
fabrication case). New cases here fill in the low end of that spectrum
(`itsdangerous`'s `BadData`/`BadSignature`/`NoneAlgorithm`, 2-4 targets
each) and add inheritance-chain, ambiguity, and adversarial dimensions
`v1` didn't specifically target.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from codex.benchmark.canonical_corpus import (
    _architecture_relevant,
    _direct_callers,
    _implementers,
    _no_evidence,
    _referencers,
)
from codex.benchmark.models import DevelopmentCorpus
from codex.evaluation.models import BenchmarkCase, BenchmarkCorpus, GroundTruthLabel
from codex.graph.store import GraphReader
from codex.planner.cache import compute_query_identity
from codex.query_understanding.engine import UnderstandingStatus, understand_query
from codex.query_understanding.models import Intent
from codex.repository.models import RepositoryMetadata

EXPANSION_CORPUS_VERSION = "validation-expansion-v1"

ITSDANGEROUS_REVISION = "672971d66a2ef9f85151e53283113f33d642dabd"
ITSDANGEROUS_SCIP_FIXTURE = "itsdangerous_sample.scip"

_FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "benchmark" / "scip"


def make_itsdangerous_repository() -> RepositoryMetadata:
    return RepositoryMetadata(
        repository_id="itsdangerous",
        local_path=_FIXTURES_DIR,
        head_revision=ITSDANGEROUS_REVISION,
    )


@dataclass(frozen=True)
class _CaseSpec:
    repository_id: str
    query_text: str
    expected_intent: Intent
    ground_truth: Callable[[GraphReader], frozenset[str]]
    should_abstain: bool
    dimension: str
    """Which validation dimension(s) this case targets -- carried
    through to the report, never used in scoring itself."""


def _itsdangerous_cases() -> tuple[_CaseSpec, ...]:
    return (
        _CaseSpec(
            repository_id="itsdangerous",
            query_text="What implements BadData?",
            expected_intent=Intent.FIND_IMPLEMENTATIONS,
            ground_truth=lambda g: _implementers(g, needle="BadData"),
            should_abstain=False,
            dimension="fan-out:low",
        ),
        _CaseSpec(
            repository_id="itsdangerous",
            query_text="What implements BadSignature?",
            expected_intent=Intent.FIND_IMPLEMENTATIONS,
            ground_truth=lambda g: _implementers(g, needle="BadSignature"),
            should_abstain=False,
            dimension=(
                "inheritance-chain (2-level: BadData<-BadSignature<-"
                "{BadHeader,BadTimeSignature})"
            ),
        ),
        _CaseSpec(
            repository_id="itsdangerous",
            query_text="What implements Serializer?",
            expected_intent=Intent.FIND_IMPLEMENTATIONS,
            ground_truth=lambda g: _implementers(g, needle="Serializer"),
            should_abstain=False,
            dimension="fan-out:high (73 substring-matched targets in a small repo)",
        ),
        _CaseSpec(
            repository_id="itsdangerous",
            query_text="What does itsdangerous depend on?",
            expected_intent=Intent.FIND_DEPENDENCIES,
            ground_truth=_no_evidence,
            should_abstain=True,
            dimension=(
                "negative:relationship-type-unsupported (SCIP-only repo, "
                "no DEPENDENCY capability)"
            ),
        ),
        _CaseSpec(
            repository_id="itsdangerous",
            query_text="What implements NoneAlgorithm?",
            expected_intent=Intent.FIND_IMPLEMENTATIONS,
            ground_truth=_no_evidence,
            should_abstain=True,
            dimension="negative:real-entity-no-real-relationship",
        ),
        _CaseSpec(
            repository_id="itsdangerous",
            query_text="Architecture of SigningAlgorithm?",
            expected_intent=Intent.ARCHITECTURE_ANALYSIS,
            ground_truth=lambda g: _architecture_relevant(g, needle="SigningAlgorithm"),
            should_abstain=False,
            dimension="2-hop, low-fan-out (contrast to Flask's high-fan-out 2-hop case)",
        ),
    )


def _click_cases() -> tuple[_CaseSpec, ...]:
    return (
        _CaseSpec(
            repository_id="click",
            query_text="What references __repr__?",
            expected_intent=Intent.FIND_REFERENCES,
            ground_truth=lambda g: _referencers(g, needle="__repr__"),
            should_abstain=True,
            dimension=(
                "ambiguity:same-method-name-across-classes "
                "(StringParamType/BoolParamType/FloatParamType...); ground truth is "
                "genuinely empty -- SCIP never captures an implicit repr()-triggered "
                "dunder call as a REFERENCES occurrence, so correct behavior here is "
                "abstention despite several real __repr__ methods existing"
            ),
        ),
        _CaseSpec(
            repository_id="click",
            query_text="What implements Param?",
            expected_intent=Intent.FIND_IMPLEMENTATIONS,
            ground_truth=lambda g: _implementers(g, needle="Param"),
            should_abstain=False,
            dimension=(
                "adversarial:truncated-prefix (real substring, triggers a "
                "much broader match than intended)"
            ),
        ),
    )


def _flask_cases() -> tuple[_CaseSpec, ...]:
    return (
        _CaseSpec(
            repository_id="flask",
            query_text="What references add_url_rule?",
            expected_intent=Intent.FIND_REFERENCES,
            ground_truth=lambda g: _referencers(g, needle="add_url_rule"),
            should_abstain=False,
            dimension="ambiguity:unqualified (4 real distinct methods share this bare name)",
        ),
        _CaseSpec(
            repository_id="flask",
            query_text="What references Blueprint.add_url_rule?",
            expected_intent=Intent.FIND_REFERENCES,
            ground_truth=lambda g: _referencers(g, needle="Blueprint.add_url_rule"),
            should_abstain=True,
            dimension=(
                "ambiguity:qualified (paired with the unqualified case above; "
                "qualifier narrows 4->1); ground truth is genuinely empty -- of the "
                "unqualified case's 3 real referencers, none happen to target "
                "specifically Blueprint's own add_url_rule (they reference sibling "
                "Scaffold/App variants instead), so correct behavior is abstention "
                "even though the qualifier itself resolved correctly to exactly 1 entity"
            ),
        ),
        _CaseSpec(
            repository_id="flask",
            query_text="What implements ConfigAttribute?",
            expected_intent=Intent.FIND_IMPLEMENTATIONS,
            ground_truth=_no_evidence,
            should_abstain=True,
            dimension=(
                "negative:plausible-but-false (a real class, involved in "
                "IMPLEMENTS as subject not object)"
            ),
        ),
        _CaseSpec(
            repository_id="flask",
            query_text="Architecture of Blueprint?",
            expected_intent=Intent.ARCHITECTURE_ANALYSIS,
            ground_truth=lambda g: _architecture_relevant(g, needle="Blueprint"),
            should_abstain=False,
            dimension=(
                "conceptual/high-level (distinct target from v1's own "
                "Architecture-of-Flask case)"
            ),
        ),
    )


def _codex_cases() -> tuple[_CaseSpec, ...]:
    return (
        _CaseSpec(
            repository_id="codex",
            query_text="What calls resolve_targets?",
            expected_intent=Intent.FIND_CALLERS,
            ground_truth=lambda g: _direct_callers(g, qualified_name_suffix="resolve_targets"),
            should_abstain=False,
            dimension=(
                "fan-out:medium (fresh reference point, distinct from v1's "
                "build_canonical_id/plan_query)"
            ),
        ),
        _CaseSpec(
            repository_id="codex",
            query_text="What calls buld_canonical_id?",
            expected_intent=Intent.FIND_CALLERS,
            ground_truth=_no_evidence,
            should_abstain=True,
            dimension="adversarial:typo (one letter short of a real, high-fan-out real symbol)",
        ),
    )


def all_case_specs() -> tuple[_CaseSpec, ...]:
    return _codex_cases() + _click_cases() + _flask_cases() + _itsdangerous_cases()


def build_expansion_corpus(
    *, repository_graphs: dict[str, tuple[RepositoryMetadata, GraphReader]], now: datetime
) -> tuple[DevelopmentCorpus, dict[str, str]]:
    """Mirrors `codex.benchmark.canonical_corpus.build_canonical_corpus`
    exactly, plus returns a `query_id -> dimension` map (not part of
    `DevelopmentCorpus` itself -- purely a report-labeling aid, never
    used in scoring)."""
    cases: dict[str, BenchmarkCase] = {}
    labels: dict[str, GroundTruthLabel] = {}
    categories: dict[str, Intent] = {}
    dimensions: dict[str, str] = {}

    for spec in all_case_specs():
        repository, graph = repository_graphs[spec.repository_id]
        understanding = understand_query(
            spec.query_text, repository_id=repository.repository_id, now=now
        )
        resolved = understanding.status is UnderstandingStatus.RESOLVED
        if not resolved or understanding.contract is None:
            raise ValueError(
                f"case spec {spec.query_text!r} ({spec.repository_id}) did not resolve via "
                f"Tier-0 (status={understanding.status.value})"
            )
        contract = understanding.contract
        if contract.intent is not spec.expected_intent:
            raise ValueError(
                f"case spec {spec.query_text!r} ({spec.repository_id}) resolved to "
                f"{contract.intent.value}, expected {spec.expected_intent.value}"
            )

        query_id = compute_query_identity(contract)
        if query_id in cases:
            raise ValueError(
                f"query_id collision for {spec.query_text!r} ({spec.repository_id}) -- "
                f"already used by {cases[query_id].query_text!r}"
            )
        relevant_ids = spec.ground_truth(graph)

        cases[query_id] = BenchmarkCase(
            query_id=query_id,
            repository_id=repository.repository_id,
            repository_revision=repository.head_revision,
            query_text=spec.query_text,
        )
        labels[query_id] = GroundTruthLabel(
            query_id=query_id,
            relevant_entity_ids=relevant_ids,
            should_abstain=spec.should_abstain,
        )
        categories[query_id] = contract.intent
        dimensions[query_id] = spec.dimension

    corpus = BenchmarkCorpus(corpus_version=EXPANSION_CORPUS_VERSION, cases=cases, labels=labels)
    return DevelopmentCorpus(corpus=corpus, categories=categories), dimensions


__all__ = [
    "EXPANSION_CORPUS_VERSION",
    "ITSDANGEROUS_REVISION",
    "ITSDANGEROUS_SCIP_FIXTURE",
    "build_expansion_corpus",
    "make_itsdangerous_repository",
]
