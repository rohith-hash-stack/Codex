"""The Codex API facade (VS Code + Nervous-System scope change).

`CodexAPI` is the one object `codex.api.server` (and, in-process, any
other future client) talks to. It is a thin facade over already-
existing, unmodified components -- `RepositoryManager`, `CapabilityRegistry`,
`IngestionPipeline`, `GraphReader`, and the Planner's own exported
`resolve_targets`/`bounded_traversal` retrieval functions
(`codex.planner.retrieval`) -- and makes no retrieval, ranking, or
identity decision of its own (`docs/vscode-nervous-system-architecture.md`
§2). No file under `src/codex/{provider,resolution,reconciliation,
query_understanding,planner,ingestion,ontology,evidence,graph}` is
imported for its *implementation* here, only for its already-published
public interface.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass

from codex.api.contracts import (
    GraphVersionRef,
    IngestionJobHandle,
    IngestionJobStatus,
    ProviderSummary,
    RepositoryPhase,
    RepositoryStatus,
    VisualizationEdge,
    VisualizationGraph,
    VisualizationNode,
)
from codex.evidence.model import CanonicalRelationship
from codex.evidence.store import EvidenceStore
from codex.graph.store import GraphReader
from codex.ingestion.models import IngestionResult
from codex.ingestion.pipeline import IngestionPipeline
from codex.ontology.entities import RepositorySymbol
from codex.ontology.relationships import RelationshipType
from codex.planner.retrieval import bounded_traversal, resolve_targets
from codex.registry.registry import CapabilityRegistry
from codex.repository.manager import RepositoryManager
from codex.repository.models import RepositoryMetadata

DEFAULT_MAX_NODES = 50
DEFAULT_MAX_EDGES = 100
DEFAULT_LOOKUP_LIMIT = 25


class RepositoryNotFoundError(KeyError):
    """Raised for any repository-scoped operation before that
    repository has completed at least one ingestion run -- there is no
    graph to query yet (mirrors `RepositoryManager.get_metadata`'s own
    `KeyError`-on-unregistered behavior, extended to "registered but
    not yet ingested")."""


class IngestionJobNotFoundError(KeyError):
    """Raised by `get_job_status` for an unknown `job_id`."""


class GitRevisionResolutionError(RuntimeError):
    """R2 fix: raised by `start_ingestion` when the repository's current
    git HEAD cannot be read (deleted/corrupted working tree, no
    commits, etc.) -- surfaced synchronously to the caller rather than
    silently reusing whatever revision happened to be stored from an
    earlier `register_repository`/`clone` call."""

    def __init__(self, repository_id: str, detail: str) -> None:
        self.repository_id = repository_id
        self.detail = detail
        super().__init__(
            f"could not resolve current HEAD revision for repository {repository_id!r}: {detail}"
        )


@dataclass
class _JobState:
    job_id: str
    repository_id: str
    phase: RepositoryPhase
    detail: str | None = None
    result: RepositoryStatus | None = None


class CodexAPI:
    """Repository-lifecycle and repository-intelligence facade.

    One `CodexAPI` instance owns one `RepositoryManager` and one
    `IngestionPipeline` (itself built from the `CapabilityRegistry`/
    `EvidenceStore` the caller supplies -- provider registration stays
    the caller's responsibility, exactly as it already is for every
    existing `IngestionPipeline` caller in this codebase). It tracks,
    in process memory, the most recent `IngestionResult` per
    `repository_id` and every ingestion job it has started -- there is
    no persistence layer here, matching this cycle's single-local-user
    MVP scope (`docs/vscode-nervous-system-architecture.md` §10).
    """

    def __init__(
        self,
        registry: CapabilityRegistry,
        evidence_store: EvidenceStore,
        *,
        provider_authority: dict[str, float] | None = None,
    ) -> None:
        self._registry = registry
        self._pipeline = IngestionPipeline(
            registry, evidence_store, provider_authority=provider_authority
        )
        self._repo_manager = RepositoryManager()
        self._lock = threading.Lock()
        self._results: dict[str, IngestionResult] = {}
        self._jobs: dict[str, _JobState] = {}
        self._active_jobs: dict[str, str] = {}
        """R1 fix: `repository_id -> job_id` of the ingestion currently
        mutating that repository's state, if any. This map *is* the
        mutual-exclusion mechanism (a "singleflight" per repository,
        checked-and-set atomically under `self._lock`) -- there is no
        separate `threading.Lock` per repository, so unrelated
        repositories never contend on anything beyond the brief,
        O(1) critical section that reads/writes this dict."""

    # -- Repository lifecycle -------------------------------------------------

    def register_repository(
        self,
        repository_id: str,
        local_path: str,
        *,
        remote_url: str | None = None,
        revision: str | None = None,
    ) -> RepositoryStatus:
        """Register an already-cloned local repository, or clone one
        first when `remote_url` is given (docs §6). Does not start
        ingestion -- call `start_ingestion` separately, so a caller can
        register many repositories cheaply before choosing which to
        index."""
        if remote_url is not None:
            metadata = self._repo_manager.clone(
                repository_id, remote_url, local_path, revision=revision
            )
        else:
            metadata = self._repo_manager.register(repository_id, local_path)
        return RepositoryStatus(
            repository_id=repository_id,
            phase=RepositoryPhase.REGISTERED,
            head_revision=metadata.head_revision,
        )

    def start_ingestion(self, repository_id: str) -> IngestionJobHandle:
        """Start `IngestionPipeline.run()` on a background thread and
        return immediately (docs §6: "genuinely non-blocking, not
        merely documented as a future concern").

        **R2 fix (stale HEAD):** always re-resolves the repository's
        *current* git HEAD before starting ingestion -- never reuses
        whatever revision happened to be stored from an earlier
        `register_repository`/`clone` call. Raises `KeyError` if
        `repository_id` was never registered (the same error
        `RepositoryManager.get_metadata` already raises, not wrapped or
        hidden), or `GitRevisionResolutionError` if the repository's
        git state cannot be read right now.

        **R1 fix (concurrent same-repository ingestion):** if an
        ingestion for `repository_id` is already in flight, this
        returns *that job's existing handle* rather than starting a
        second one -- a per-repository "singleflight": at most one
        `IngestionPipeline.run()` call may be mutating a given
        repository's accumulator state at a time, while unrelated
        repositories remain fully independent (the check-and-set below
        is keyed by `repository_id` and holds `self._lock` only for
        the O(1) dict operation itself, never across the actual
        ingestion work). Never silently starts a second mutation.
        """
        metadata = self._resolve_fresh_metadata(repository_id)

        with self._lock:
            existing_job_id = self._active_jobs.get(repository_id)
            if existing_job_id is not None:
                return IngestionJobHandle(job_id=existing_job_id, repository_id=repository_id)
            job_id = f"job-{repository_id}-{uuid.uuid4().hex[:8]}"
            self._jobs[job_id] = _JobState(
                job_id=job_id, repository_id=repository_id, phase=RepositoryPhase.INDEXING
            )
            self._active_jobs[repository_id] = job_id

        thread = threading.Thread(
            target=self._run_ingestion, args=(job_id, metadata), daemon=True
        )
        thread.start()
        return IngestionJobHandle(job_id=job_id, repository_id=repository_id)

    def _resolve_fresh_metadata(self, repository_id: str) -> RepositoryMetadata:
        """R2 fix: re-read the repository's current git HEAD (never a
        cached value) immediately before it is used to determine the
        ingestion target, and return the now-refreshed metadata.

        `RepositoryManager.get_head_revision` both resolves the live
        HEAD *and* persists it back onto the stored `RepositoryMetadata`
        (`repository/manager.py`, unmodified) -- calling it here means
        every `start_ingestion` call is guaranteed to target whatever
        commit is current *at the moment ingestion is requested*,
        exactly like a build tool re-running `git rev-parse HEAD`
        rather than trusting an earlier snapshot.
        """
        try:
            self._repo_manager.get_head_revision(repository_id)
        except KeyError:
            raise
        except Exception as exc:  # noqa: BLE001 -- any git/filesystem
            # failure while resolving HEAD is reported as a distinct,
            # named error (never silently swallowed into "use whatever
            # revision we already had").
            raise GitRevisionResolutionError(repository_id, str(exc)) from exc
        return self._repo_manager.get_metadata(repository_id)

    def _run_ingestion(self, job_id: str, metadata: RepositoryMetadata) -> None:
        try:
            try:
                result = self._pipeline.run(metadata)
            except Exception as exc:  # noqa: BLE001 -- isolate the
                # background thread; the failure is reported through
                # job status, never raised into an unrelated caller's
                # stack (mirrors `IngestionPipeline`'s own per-provider
                # isolation discipline, applied here at the job level).
                with self._lock:
                    self._jobs[job_id].phase = RepositoryPhase.FAILED
                    self._jobs[job_id].detail = str(exc)
                return

            status = self._status_from_result(metadata.repository_id, result)
            with self._lock:
                self._results[metadata.repository_id] = result
                self._jobs[job_id].phase = RepositoryPhase.READY
                self._jobs[job_id].result = status
        finally:
            # R1 fix: release this repository's ingestion slot on
            # *every* exit path (success or failure) -- never leaves a
            # repository permanently "busy". Only clears the entry if
            # it still points at this job (defensive; by construction
            # at most one job can ever be active per repository, so
            # this can never actually mismatch, but costs nothing to
            # guard).
            with self._lock:
                if self._active_jobs.get(metadata.repository_id) == job_id:
                    del self._active_jobs[metadata.repository_id]

    def get_job_status(self, job_id: str) -> IngestionJobStatus:
        with self._lock:
            state = self._jobs.get(job_id)
        if state is None:
            raise IngestionJobNotFoundError(job_id)
        return IngestionJobStatus(
            job_id=state.job_id,
            repository_id=state.repository_id,
            phase=state.phase,
            detail=state.detail,
            result=state.result,
        )

    def get_repository_status(self, repository_id: str) -> RepositoryStatus:
        with self._lock:
            result = self._results.get(repository_id)
        if result is not None:
            return self._status_from_result(repository_id, result)
        try:
            metadata = self._repo_manager.get_metadata(repository_id)
        except KeyError:
            return RepositoryStatus(
                repository_id=repository_id, phase=RepositoryPhase.NOT_REGISTERED
            )
        return RepositoryStatus(
            repository_id=repository_id,
            phase=RepositoryPhase.REGISTERED,
            head_revision=metadata.head_revision,
        )

    @staticmethod
    def _status_from_result(repository_id: str, result: IngestionResult) -> RepositoryStatus:
        providers = [
            ProviderSummary(
                provider_name=outcome.provider_name,
                status=outcome.status.value,
                entities_upserted=outcome.entities_upserted,
                evidence_upserted=outcome.evidence_upserted,
                detail=outcome.detail,
            )
            for outcome in result.provider_outcomes
        ]
        return RepositoryStatus(
            repository_id=repository_id,
            phase=RepositoryPhase.READY,
            head_revision=result.repository_revision,
            graph_version_id=result.graph_version.version_id,
            provider_summary=providers,
        )

    # -- Repository intelligence -----------------------------------------------

    def lookup_symbols(
        self, repository_id: str, query: str, *, limit: int = DEFAULT_LOOKUP_LIMIT
    ) -> VisualizationGraph:
        """Symbol/file lookup (docs §7). Reuses `resolve_targets` --
        the same deterministic, boundary-aligned candidate-resolution
        function every NL query already uses -- never a separate
        matching rule invented for the API layer. Zero edges, depth 0:
        this is a lookup, not a traversal."""
        graph = self._graph_for(repository_id)
        matches = resolve_targets(graph, [query]) if query else []
        nodes = [self._to_node(entity, 0) for entity in matches[:limit]]
        return VisualizationGraph(
            center=query,
            nodes=nodes,
            graph_version=self._version_ref(graph),
            requested_depth=0,
        )

    def get_neighborhood(
        self,
        repository_id: str,
        symbol: str,
        *,
        depth: int = 1,
        max_nodes: int = DEFAULT_MAX_NODES,
        max_edges: int = DEFAULT_MAX_EDGES,
        relationship_types: list[RelationshipType] | None = None,
    ) -> VisualizationGraph:
        """Bounded graph-neighborhood retrieval (docs §8): "give me the
        relevant neighborhood around symbol X," never the whole graph.
        Reuses `resolve_targets` for seed resolution and
        `bounded_traversal` for the traversal itself -- the identical
        pure, exported retrieval-engine function
        `codex.evaluation.observer.observe_ranked_candidates` already
        replays from outside the main query pipeline. No traversal
        logic is duplicated or forked here."""
        if depth < 0:
            raise ValueError(f"depth must be >= 0, got {depth}")
        graph = self._graph_for(repository_id)
        version_ref = self._version_ref(graph)
        seeds = resolve_targets(graph, [symbol])
        if not seeds:
            return VisualizationGraph(
                center=symbol, graph_version=version_ref, requested_depth=depth
            )

        traversal = bounded_traversal(
            graph, seeds, list(relationship_types or []), depth, max_nodes, max_edges
        )
        nodes = [
            self._to_node(entity, traversal.distances.get(entity.canonical_id, 0))
            for entity in traversal.entities
        ]
        edges = [self._to_edge(relationship) for relationship in traversal.relationships]
        return VisualizationGraph(
            center=symbol,
            nodes=nodes,
            edges=edges,
            graph_version=version_ref,
            requested_depth=depth,
            truncated=traversal.truncated,
        )

    def _graph_for(self, repository_id: str) -> GraphReader:
        with self._lock:
            result = self._results.get(repository_id)
        if result is None:
            raise RepositoryNotFoundError(repository_id)
        return result.graph_store

    @staticmethod
    def _version_ref(graph: GraphReader) -> GraphVersionRef:
        version = graph.version
        return GraphVersionRef(
            version_id=version.version_id,
            repository_id=version.repository_id,
            repository_revision=version.repository_revision,
        )

    @staticmethod
    def _to_node(entity: RepositorySymbol, distance: int) -> VisualizationNode:
        return VisualizationNode(
            id=entity.canonical_id,
            name=entity.name,
            qualified_name=entity.qualified_name,
            node_type=entity.base_type,
            roles=list(entity.roles),
            language=entity.language,
            source_location=entity.source_location,
            distance=distance,
        )

    @staticmethod
    def _to_edge(relationship: CanonicalRelationship) -> VisualizationEdge:
        return VisualizationEdge(
            id=f"{relationship.subject}|{relationship.predicate.value}|{relationship.object}",
            source=relationship.subject,
            target=relationship.object,
            relationship_type=relationship.predicate,
            status=relationship.status,
            confidence=relationship.confidence,
            evidence_count=len(relationship.supporting_evidence_ids)
            + len(relationship.contradicting_evidence_ids),
        )


__all__ = [
    "DEFAULT_LOOKUP_LIMIT",
    "DEFAULT_MAX_EDGES",
    "DEFAULT_MAX_NODES",
    "CodexAPI",
    "GitRevisionResolutionError",
    "IngestionJobNotFoundError",
    "RepositoryNotFoundError",
]
