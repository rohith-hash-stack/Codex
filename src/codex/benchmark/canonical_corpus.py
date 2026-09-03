"""The canonical LLM benchmark corpus (`codex-canonical-v1`) — the
"Build Canonical LLM Benchmark & Broad Validation" milestone.

**Repository diversity, three real, pinned Python repositories:**

1. **`codex`** (this repository, self-hosted — same technique
   `tests/test_d7_providers_real_repository.py` established) — ingested
   via the same two dependency-free, network-free providers the
   development corpus (`codex-self-dev-v0`) already validated:
   `AstCallsAdapter` (`CALL_RELATIONSHIP`) and
   `PyprojectDependencyAdapter` (`DEPENDENCY`). Backs `FIND_CALLERS`/
   `FIND_TESTS`/`FIND_DEPENDENCIES`.
2. **`click`** (`pallets/click`, pinned at commit
   `36baa15ff831b939a22bc527cd76ce653ef6f66d`) — a real, moderately
   large (79 files) CLI framework: decorator-heavy, real class
   hierarchies (`ParamType` subclasses, `Group`/`CommandCollection`).
3. **`flask`** (`pallets/flask`, pinned at commit
   `d318b683471101618febed18996405ad26462110`) — a real web framework
   (83 files): richer inheritance (`Scaffold` -> `App`, `ProxyMixin`),
   blueprints, real external dependencies (Jinja2).

`click`/`flask` are ingested via `SCIPAdapter` alone, fed from a real,
frozen `.scip` index generated once by actually running
`scip-python@0.6.6` against each pinned commit (`tests/fixtures/
benchmark/scip/{click,flask}_sample.scip` — same "generate once, freeze,
never regenerate live" precedent `tests/fixtures/scip/
codex_resolution_sample.scip` already established for this project).
Deliberately **not** vendoring either repository's full source tree
into this repository (that would mean checking in ~160 external files
for two dependency's worth of source) — the frozen `.scip` index is
itself hermetic and needs no live clone to parse deterministically;
`AstCallsAdapter`/`GitAdapter` are **not** used for `click`/`flask`
specifically because both need live source/git access this corpus does
not vendor, an honest, documented scope boundary (see module docstring
below for the categories this excludes).

**Query category coverage, and what is honestly excluded:**

Covered with real, mechanically-derived ground truth: `FIND_CALLERS`,
`FIND_TESTS`, `FIND_DEPENDENCIES` (`codex`, including one real
high-fan-out case — `plan_query`, 94 real callers); `FIND_IMPLEMENTATIONS`
(including one deliberately ambiguous/high-fan-out case — "ParamType"
substring-matches 24 real entities), `FIND_REFERENCES`,
`ARCHITECTURE_ANALYSIS` (`click`/`flask`); negative/abstention cases in
every repository.

**A paraphrase finding, not a paraphrase feature**: an initial design
tried literal paraphrase pairs ("What calls X?" vs "Who calls X?",
"What implements X?" vs "Who implements X?") as separate cases. Every
one collided on `query_id` -- D8's Tier-0 (`codex.query_understanding.
tier0._STRUCTURAL_RULES`) scores every one of these phrasings at the
identical `_STRUCTURAL_SCORE=0.97` with identical target extraction,
producing a byte-identical `QueryContract` regardless of which phrasing
is used. This is a genuine, positive finding about D8's determinism
(retrieval-plan invariance across superficial rewordings of the same
structural request) rather than a gap to route around -- documented
here instead of forced into artificial separate corpus entries;
paraphrase robustness at the *model* level is instead exercised by the
two intent families (`FIND_CALLERS`/`FIND_IMPLEMENTATIONS`) each
appearing multiple times across different repositories/targets.

**Excluded, and why** (pre-existing, already-documented gaps — not
routed around):

- `CODE_LOOKUP` — confirmed empirically that plain phrasings ("What is
  X?") do not clear Tier-0's deterministic threshold and no SLM is
  configured (D8's own directive: "no real SLM ships with D8"); this is
  the same gap `PROGRESS.md` already records ("CODE_LOOKUP's low Tier-0
  confidence with no SLM configured").
- `TRACE_EXECUTION` and the `DATA_FLOW`-dependent half of `FIND_IMPACT`
  — need `Capability.DATA_FLOW`, which only `CodeQLAdapter` backs, which
  needs a CodeQL CLI/GHAS entitlement this environment does not have —
  the same gap `PROGRESS.md` has recorded since the original D7 audit.
- `HISTORY_ANALYSIS`/`CO_CHANGE` for `click`/`flask` — would need
  `GitAdapter` against a live git history this corpus deliberately does
  not vendor (see above); `codex` itself could back this but is excluded
  from this pass's scope to keep the corpus's provider mix per
  repository simple and easy to reason about.
- Exhaustive adversarial/near-miss and paraphrase matrices — a small,
  representative sample is included (not an exhaustive combinatorial
  sweep across every category and repository), an explicit, bounded
  first pass, not a claim of completeness.

Ground truth is always derived mechanically from the real, committed
graph's own relationships (`GraphReader.get_relationships()`/
`find_entities()`) — never from D9's ranked retrieval output and never
from an LLM.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from codex.benchmark.models import DevelopmentCorpus
from codex.evaluation.models import BenchmarkCase, BenchmarkCorpus, GroundTruthLabel
from codex.graph.store import GraphReader
from codex.ontology.entities import BaseEntityType
from codex.ontology.relationships import RelationshipType
from codex.planner.cache import compute_query_identity
from codex.query_understanding.engine import UnderstandingStatus, understand_query
from codex.query_understanding.models import Intent
from codex.repository.models import RepositoryMetadata

_FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "benchmark" / "scip"

CANONICAL_CORPUS_VERSION = "codex-canonical-v1"

CLICK_REVISION = "36baa15ff831b939a22bc527cd76ce653ef6f66d"
FLASK_REVISION = "d318b683471101618febed18996405ad26462110"
CLICK_SCIP_FIXTURE = "click_sample.scip"
FLASK_SCIP_FIXTURE = "flask_sample.scip"


def make_click_repository() -> RepositoryMetadata:
    return RepositoryMetadata(
        repository_id="click", local_path=_FIXTURES_DIR, head_revision=CLICK_REVISION
    )


def make_flask_repository() -> RepositoryMetadata:
    return RepositoryMetadata(
        repository_id="flask", local_path=_FIXTURES_DIR, head_revision=FLASK_REVISION
    )


# ---------------------------------------------------------------------------
# Mechanical ground-truth derivation -- always real graph facts, never D9's
# ranked output, never an LLM. `find_entities(name=...)` is the same public,
# deterministic substring-lookup Protocol method D9's own target resolution
# uses (not a parallel/competing mechanism) -- reused here only to determine
# which real entities a query's target text names, exactly matching how a
# benchmark author is expected to determine ground truth per `BenchmarkCase`'s
# own docstring.
# ---------------------------------------------------------------------------


def _resolved_targets(graph: GraphReader, needle: str) -> list[str]:
    return [e.canonical_id for e in graph.find_entities(name=needle)]


def _direct_callers(graph: GraphReader, *, qualified_name_suffix: str) -> frozenset[str]:
    target = next(
        e for e in graph.find_entities() if e.qualified_name.endswith("::" + qualified_name_suffix)
    )
    rels = graph.get_relationships(predicate=RelationshipType.CALLS, object_id=target.canonical_id)
    return frozenset(r.subject for r in rels)


def _direct_test_callers(graph: GraphReader, *, qualified_name_suffix: str) -> frozenset[str]:
    all_callers = _direct_callers(graph, qualified_name_suffix=qualified_name_suffix)
    by_id = {e.canonical_id: e for e in graph.find_entities()}
    return frozenset(cid for cid in all_callers if by_id[cid].qualified_name.startswith("tests/"))


def _repository_dependencies(graph: GraphReader, *, repository_id: str) -> frozenset[str]:
    repo_entity = next(
        e
        for e in graph.find_entities(base_type=BaseEntityType.REPOSITORY)
        if e.repository_id == repository_id
    )
    rels = graph.get_relationships(
        predicate=RelationshipType.DEPENDS_ON, subject=repo_entity.canonical_id
    )
    return frozenset(r.object for r in rels)


def _implementers(graph: GraphReader, *, needle: str) -> frozenset[str]:
    """Real, direct `IMPLEMENTS` subjects (the "implementers") of every
    entity `find_entities(name=needle)` resolves -- deliberately over
    the *whole* resolved target set, including a needle (like
    "ParamType") that substring-matches many real entities, so ground
    truth honestly reflects the same ambiguity a real query would face."""
    targets = _resolved_targets(graph, needle)
    result: set[str] = set()
    for target_id in targets:
        rels = graph.get_relationships(predicate=RelationshipType.IMPLEMENTS, object_id=target_id)
        result.update(r.subject for r in rels)
    return frozenset(result)


def _referencers(graph: GraphReader, *, needle: str) -> frozenset[str]:
    """Real, direct `REFERENCES` subjects of every entity
    `find_entities(name=needle)` resolves."""
    targets = _resolved_targets(graph, needle)
    result: set[str] = set()
    for target_id in targets:
        rels = graph.get_relationships(predicate=RelationshipType.REFERENCES, object_id=target_id)
        result.update(r.subject for r in rels)
    return frozenset(result)


def _architecture_relevant(graph: GraphReader, *, needle: str) -> frozenset[str]:
    """Real `IMPLEMENTS`/`REFERENCES` neighbors (either direction) of
    every entity `find_entities(name=needle)` resolves -- the real graph
    facts an `ARCHITECTURE_ANALYSIS` query's required evidence
    (`SYMBOL_DEFINITION`+`IMPLEMENTATION`+`TYPE_RELATIONSHIP` ->
    `IMPLEMENTS`/`REFERENCES`) is actually grounded in."""
    targets = _resolved_targets(graph, needle)
    result: set[str] = set()
    for target_id in targets:
        for predicate in (RelationshipType.IMPLEMENTS, RelationshipType.REFERENCES):
            incoming = graph.get_relationships(predicate=predicate, object_id=target_id)
            outgoing = graph.get_relationships(predicate=predicate, subject=target_id)
            result.update(r.subject for r in incoming)
            result.update(r.object for r in outgoing)
    return frozenset(result)


def _no_evidence(graph: GraphReader) -> frozenset[str]:
    return frozenset()


@dataclass(frozen=True)
class _CaseSpec:
    repository_id: str
    query_text: str
    expected_intent: Intent
    ground_truth: Callable[[GraphReader], frozenset[str]]
    should_abstain: bool


def _click_cases() -> tuple[_CaseSpec, ...]:
    return (
        _CaseSpec(
            repository_id="click",
            query_text="What implements ParamType?",
            expected_intent=Intent.FIND_IMPLEMENTATIONS,
            ground_truth=lambda g: _implementers(g, needle="ParamType"),
            should_abstain=False,
        ),
        _CaseSpec(
            repository_id="click",
            query_text="What implements UsageError?",
            expected_intent=Intent.FIND_IMPLEMENTATIONS,
            ground_truth=lambda g: _implementers(g, needle="UsageError"),
            should_abstain=False,
        ),
        _CaseSpec(
            repository_id="click",
            query_text="What references BadParameter?",
            expected_intent=Intent.FIND_REFERENCES,
            ground_truth=lambda g: _referencers(g, needle="BadParameter"),
            should_abstain=False,
        ),
        _CaseSpec(
            repository_id="click",
            query_text="What implements zzz_nonexistent_click_symbol_xyz?",
            expected_intent=Intent.FIND_IMPLEMENTATIONS,
            ground_truth=_no_evidence,
            should_abstain=True,
        ),
    )


def _flask_cases() -> tuple[_CaseSpec, ...]:
    return (
        _CaseSpec(
            repository_id="flask",
            query_text="What implements Scaffold?",
            expected_intent=Intent.FIND_IMPLEMENTATIONS,
            ground_truth=lambda g: _implementers(g, needle="Scaffold"),
            should_abstain=False,
        ),
        _CaseSpec(
            repository_id="flask",
            query_text="Architecture of Flask?",
            expected_intent=Intent.ARCHITECTURE_ANALYSIS,
            ground_truth=lambda g: _architecture_relevant(g, needle="Flask"),
            should_abstain=False,
        ),
        _CaseSpec(
            repository_id="flask",
            query_text="What references Blueprint?",
            expected_intent=Intent.FIND_REFERENCES,
            ground_truth=lambda g: _referencers(g, needle="Blueprint"),
            should_abstain=False,
        ),
        _CaseSpec(
            repository_id="flask",
            query_text="What implements zzz_nonexistent_flask_symbol_xyz?",
            expected_intent=Intent.FIND_IMPLEMENTATIONS,
            ground_truth=_no_evidence,
            should_abstain=True,
        ),
    )


def _codex_cases() -> tuple[_CaseSpec, ...]:
    return (
        _CaseSpec(
            repository_id="codex",
            query_text="What calls build_canonical_id?",
            expected_intent=Intent.FIND_CALLERS,
            ground_truth=lambda g: _direct_callers(g, qualified_name_suffix="build_canonical_id"),
            should_abstain=False,
        ),
        _CaseSpec(
            repository_id="codex",
            query_text="What calls plan_query?",
            expected_intent=Intent.FIND_CALLERS,
            ground_truth=lambda g: _direct_callers(g, qualified_name_suffix="plan_query"),
            should_abstain=False,
        ),
        _CaseSpec(
            repository_id="codex",
            query_text="Which tests call compute_query_identity?",
            expected_intent=Intent.FIND_TESTS,
            ground_truth=lambda g: _direct_test_callers(
                g, qualified_name_suffix="compute_query_identity"
            ),
            should_abstain=False,
        ),
        _CaseSpec(
            repository_id="codex",
            query_text="What does codex depend on?",
            expected_intent=Intent.FIND_DEPENDENCIES,
            ground_truth=lambda g: _repository_dependencies(g, repository_id="codex"),
            should_abstain=False,
        ),
        _CaseSpec(
            repository_id="codex",
            query_text="What calls zzz_nonexistent_codex_symbol_xyz?",
            expected_intent=Intent.FIND_CALLERS,
            ground_truth=_no_evidence,
            should_abstain=True,
        ),
    )


def all_case_specs() -> tuple[_CaseSpec, ...]:
    return _codex_cases() + _click_cases() + _flask_cases()


def build_canonical_corpus(
    *, repository_graphs: dict[str, tuple[RepositoryMetadata, GraphReader]], now: datetime
) -> DevelopmentCorpus:
    """Deterministically build `codex-canonical-v1` against
    already-ingested graphs for every repository the case specs
    reference (`repository_graphs`: `repository_id -> (RepositoryMetadata,
    GraphReader)`) -- performs no ingestion itself, matching `codex.
    benchmark.dev_corpus.build_development_corpus`'s own "given a graph,
    not how to build one" pattern. Raises `ValueError` on any case spec
    that no longer resolves via Tier-0 to its declared `expected_intent`."""
    cases: dict[str, BenchmarkCase] = {}
    labels: dict[str, GroundTruthLabel] = {}
    categories: dict[str, Intent] = {}

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
                f"already used by {cases[query_id].query_text!r} "
                f"({cases[query_id].repository_id})"
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

    corpus = BenchmarkCorpus(corpus_version=CANONICAL_CORPUS_VERSION, cases=cases, labels=labels)
    return DevelopmentCorpus(corpus=corpus, categories=categories)


__all__ = [
    "CANONICAL_CORPUS_VERSION",
    "CLICK_REVISION",
    "CLICK_SCIP_FIXTURE",
    "FLASK_REVISION",
    "FLASK_SCIP_FIXTURE",
    "build_canonical_corpus",
    "make_click_repository",
    "make_flask_repository",
]
