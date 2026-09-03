"""The initial, real-repository **development** corpus: Codex's own live
source tree, self-hosted -- the same "self-hosting" technique `tests/
test_d7_providers_real_repository.py` already established ("unlike the
`veyra` repository used for manual validation during the D7 audit... not
part of this repo and not guaranteed present wherever this test suite
runs... Codex's own checked-out source is always present"). Ingestion
uses only `AstCallsAdapter` (stdlib `ast`) and `PyprojectDependencyAdapter`
(stdlib `tomllib`) -- both dependency-free, network-free, real D7
providers -- never SCIP/CodeQL/Git (avoiding any external-tool/network
dependency for this milestone, and avoiding `GitAdapter`'s CO_CHANGE/
HISTORY capabilities entirely, which are not needed by any case below).

Explicitly a **development/smoke** corpus, not a canonical benchmark
(`docs/llm-benchmark-spec.md` documents promotion criteria, not met
yet): four cases across three real `Intent` categories (FIND_CALLERS,
FIND_TESTS, FIND_DEPENDENCIES) plus one deliberate negative/no-evidence
case -- enough to exercise the harness's full machinery honestly, given
what these two real providers can actually back, without claiming
broad query/repository coverage this milestone never attempted.

**Ground truth is derived mechanically from the real, committed graph's
own relationships** (`GraphReader.get_relationships()`), never from D9's
ranked retrieval output (which would test D9 against itself) and never
from an LLM -- each `_CaseSpec.ground_truth` closure below states
exactly which real graph fact it reads.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from codex.benchmark.models import DevelopmentCorpus
from codex.evaluation.models import BenchmarkCase, BenchmarkCorpus, GroundTruthLabel
from codex.graph.store import GraphReader
from codex.ontology.entities import BaseEntityType
from codex.ontology.relationships import RelationshipType
from codex.planner.cache import compute_query_identity
from codex.query_understanding.engine import UnderstandingStatus, understand_query
from codex.query_understanding.models import Intent
from codex.repository.models import RepositoryMetadata

CORPUS_VERSION = "codex-self-dev-v0"
"""Deliberately not "v1"/"canonical" -- see this module's own docstring
and `docs/llm-benchmark-spec.md`'s promotion criteria."""


def _direct_callers(graph: GraphReader, *, qualified_name_suffix: str) -> frozenset[str]:
    """Real, direct `CALLS` predecessors of the one entity whose
    `qualified_name` ends with `::{qualified_name_suffix}` -- read
    straight from the committed graph's own relationships, never from a
    traversal/ranking pass."""
    target = next(
        e for e in graph.find_entities() if e.qualified_name.endswith("::" + qualified_name_suffix)
    )
    rels = graph.get_relationships(predicate=RelationshipType.CALLS, object_id=target.canonical_id)
    return frozenset(r.subject for r in rels)


def _direct_test_callers(graph: GraphReader, *, qualified_name_suffix: str) -> frozenset[str]:
    """Same as `_direct_callers`, narrowed to callers whose own
    `qualified_name` lives under `tests/` -- the real graph fact backing
    a "which tests call X" query, matching this project's own canonical
    symbol-level worked example (`tests/symbol_level_fixtures.py`)."""
    all_callers = _direct_callers(graph, qualified_name_suffix=qualified_name_suffix)
    by_id = {e.canonical_id: e for e in graph.find_entities()}
    return frozenset(cid for cid in all_callers if by_id[cid].qualified_name.startswith("tests/"))


def _repository_dependencies(graph: GraphReader, *, repository_id: str) -> frozenset[str]:
    """Real `DEPENDS_ON` successors of the repository's own `REPOSITORY`
    entity -- exactly `PyprojectDependencyAdapter`'s own committed
    evidence, read from the graph, never re-parsed from `pyproject.toml`
    a second time here."""
    repo_entity = next(
        e
        for e in graph.find_entities(base_type=BaseEntityType.REPOSITORY)
        if e.repository_id == repository_id
    )
    rels = graph.get_relationships(
        predicate=RelationshipType.DEPENDS_ON, subject=repo_entity.canonical_id
    )
    return frozenset(r.object for r in rels)


def _no_evidence(graph: GraphReader) -> frozenset[str]:
    return frozenset()


@dataclass(frozen=True)
class _CaseSpec:
    query_text: str
    expected_intent: Intent
    ground_truth: Callable[[GraphReader], frozenset[str]]
    should_abstain: bool


_CASE_SPECS: tuple[_CaseSpec, ...] = (
    _CaseSpec(
        query_text="What calls build_canonical_id?",
        expected_intent=Intent.FIND_CALLERS,
        ground_truth=lambda g: _direct_callers(g, qualified_name_suffix="build_canonical_id"),
        should_abstain=False,
    ),
    _CaseSpec(
        query_text="Which tests call compute_query_identity?",
        expected_intent=Intent.FIND_TESTS,
        ground_truth=lambda g: _direct_test_callers(
            g, qualified_name_suffix="compute_query_identity"
        ),
        should_abstain=False,
    ),
    _CaseSpec(
        query_text="What does codex depend on?",
        expected_intent=Intent.FIND_DEPENDENCIES,
        ground_truth=lambda g: _repository_dependencies(g, repository_id="codex"),
        should_abstain=False,
    ),
    _CaseSpec(
        # A deliberate negative-query case (TAD §34's own negative-query
        # safety rule; D10's "no evidence -> no repository factual
        # assertion" Final Answer/Abstention Policy): no entity named
        # this exists anywhere in the real graph, by construction.
        query_text="What calls this_function_does_not_exist_anywhere_xyz?",
        expected_intent=Intent.FIND_CALLERS,
        ground_truth=_no_evidence,
        should_abstain=True,
    ),
)


def build_development_corpus(
    *, repository: RepositoryMetadata, graph: GraphReader, now: datetime
) -> DevelopmentCorpus:
    """Deterministically build the development corpus against an
    already-ingested `graph` (this function performs no ingestion
    itself -- separation of concerns matching D9's own "given a graph,
    not how to build one" pattern). Raises `ValueError` if any
    `_CaseSpec`'s query text no longer resolves via Tier-0 to its
    declared `expected_intent` -- a corpus-authoring defect, never
    silently tolerated.
    """
    cases: dict[str, BenchmarkCase] = {}
    labels: dict[str, GroundTruthLabel] = {}
    categories: dict[str, Intent] = {}

    for spec in _CASE_SPECS:
        understanding = understand_query(
            spec.query_text, repository_id=repository.repository_id, now=now
        )
        resolved = understanding.status is UnderstandingStatus.RESOLVED
        if not resolved or understanding.contract is None:
            raise ValueError(
                f"case spec {spec.query_text!r} did not resolve via Tier-0 "
                f"(status={understanding.status.value})"
            )
        contract = understanding.contract
        if contract.intent is not spec.expected_intent:
            raise ValueError(
                f"case spec {spec.query_text!r} resolved to {contract.intent.value}, "
                f"expected {spec.expected_intent.value}"
            )

        query_id = compute_query_identity(contract)
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

    corpus = BenchmarkCorpus(corpus_version=CORPUS_VERSION, cases=cases, labels=labels)
    return DevelopmentCorpus(corpus=corpus, categories=categories)


__all__ = ["CORPUS_VERSION", "build_development_corpus"]
