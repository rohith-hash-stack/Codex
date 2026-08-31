"""The Ingestion Pipeline (TAD §72-73; HLRD §23; Phase D directive D4).

Orchestration only — wires the already-approved D1 (``ProviderAdapter``
contract), D2 (``CapabilityRegistry``), and D3 (``GitAdapter``, and any
future concrete adapter) into one pipeline, matching the directive's
stated shape exactly::

    PROVIDERS -> INGESTION PIPELINE -> EVIDENCE NORMALIZATION -> CANONICAL GRAPH

This module contains **no provider-specific logic** (directive D4 §3):
every provider decision is made by calling the D1 contract's own
methods through the D2 Registry (``registered_providers()``,
``evaluate()``) — never by branching on a provider's name or assuming
a particular one (Git, SCIP, CodeQL, Sourcegraph, ...) exists.

Responsibility map (directive D4 §2) and where each already lives:

- provider discovery          -> ``CapabilityRegistry.registered_providers()`` (D2)
- capability selection        -> ``CapabilityRegistry.evaluate()`` (D2)
- eligibility evaluation      -> ``ProviderAdapter.check_eligibility()`` via ``evaluate()`` (D1/D2)
- extraction orchestration    -> this module: one ``extract()`` call per committed
                                  provider, covering every capability that provider
                                  was found usable for
- failure isolation           -> this module: each provider's extract()/normalize()
                                  is isolated; a ``ProviderExtractionError`` or any
                                  other exception removes only that provider's
                                  contribution for this run (directive D4 §6, §14)
- evidence normalization      -> ``ProviderAdapter.normalize()`` (D1) — never
                                  reimplemented here
- EvidenceCohort construction -> ``ProviderAdapter.extract()`` (D1) — this module
                                  only records the cohort it's handed, never edits
                                  or reinterprets it (an empty successful cohort is
                                  never treated as a failure — directive D4 §6)
- graph update                -> this module: upsert entities, and fold evidence
                                  into ``CanonicalRelationship.supporting_evidence_ids``
                                  (no scoring, no status/contradiction decision —
                                  see "Reconciliation is out of scope" below)
- graph-version creation      -> this module: one new, immutable ``GraphVersion``
                                  per ``run()`` (TAD §19-20)
- provenance preservation     -> ``Evidence``/``EvidenceCohort`` objects are stored
                                  and forwarded unmodified; nothing here strips
                                  provider/version/revision/observed_at/raw_reference
- idempotency                 -> deterministic ``canonical_id``/``evidence_id`` keys
                                  (already true of the D1 ``Evidence`` model and D3's
                                  ``GitAdapter``) mean re-running with equivalent
                                  evidence upserts onto the same keys rather than
                                  duplicating; see ``IngestionPipeline``'s accumulator
- incremental update behavior -> this module never rescans; it accumulates each
                                  run's (already-incremental, per-provider) evidence
                                  on top of everything committed by prior runs,
                                  matching HLRD §23's "avoid rebuilding the entire
                                  graph for small repository changes" requirement

Reconciliation is out of scope
-------------------------------
TAD §73 ("Provider Reconciliation") and TAD component #7 ("Entity
Resolution + Reconciliation Engine") are a distinct, ``NOT_IMPLEMENTED``
component (see ``docs/architecture-conformance-audit.md`` §G) — it
needs ≥2 real providers to be meaningful and none of this pipeline's
logic anticipates its formula. This module performs **no** contradiction
detection and assigns no ``CanonicalRelationship.status`` or
``.confidence``: every ``evidence_id`` observed for a given
``(subject, predicate, object)`` triple is appended to
``supporting_evidence_ids`` without judgment, so no evidence is ever
discarded and no provider is ever treated as "correct" merely because
it ran first or last (directive D4 §11). Status stays
``EvidenceStatus.UNRESOLVED`` and confidence stays at the model default
(``0.0``) until a real Reconciliation Engine exists to compute them —
inventing a merge formula here would be exactly the kind of silent,
unspecified algorithm the directive prohibits (D4 §5, §19-20).

Incremental strategy, not full-graph snapshots
------------------------------------------------
Each ``run()`` targets one repository revision at a time (whatever
``repository.head_revision`` the caller supplies) and relies on each
provider's own extraction being incremental — D3's ``GitAdapter``, for
instance, diffs only the tip commit against its parent, never replaying
full history (TAD §21, §72). This pipeline does not attempt to bridge
multi-commit gaps itself (e.g. catching up after several unseen
commits): doing so would require Git-specific commit-walking logic
outside the adapter boundary, which directive D4 §3 forbids. A caller
that needs to catch up across several commits calls ``run()`` once per
intermediate revision.

``_materialize_store()`` rebuilds a full ``InMemoryGraphStore`` from
the pipeline's in-memory accumulator on every call — O(total
accumulated size), not O(delta). This is an accepted Phase 1
simplification consistent with ``codex.graph``'s own "in-memory
default, storage technology deferred to ADR-001" framing; a real
backend would apply only the incremental delta.

Concurrency and atomicity (directive D4 §14-15)
-------------------------------------------------
``run()`` never mutates a previously-returned ``IngestionResult``'s
``graph_store``/``graph_version`` — each call builds a brand-new,
immutable pair from the accumulator's state *at that moment* and only
returns it once every provider has been attempted and every successful
provider's evidence has been committed to the accumulator. A failure
partway through one provider's processing cannot leave a *published*
version looking complete but actually missing that provider's
evidence, because nothing is published until the whole loop finishes.
Real concurrent-writer detection (TAD §64's ``GRAPH_VERSION_CONFLICT``/
``CONCURRENT_UPDATE_DETECTED``) is not implemented: this pipeline is
synchronous/single-threaded, like the rest of Phase 1's stack (no
async or threading exists anywhere in this codebase yet). Concurrency
safety here is structural (never mutate a returned version) rather
than lock-based; real concurrent-writer detection needs a persistent
storage layer with its own transaction semantics (ADR-002 territory),
out of D4's scope.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import UTC, datetime

from codex.evidence.model import CanonicalRelationship, Evidence, EvidenceCohort
from codex.evidence.store import EvidenceStore
from codex.graph.memory_store import InMemoryGraphStore
from codex.graph.version import GraphVersion
from codex.ingestion.models import IngestionResult, ProviderRunOutcome, ProviderRunStatus
from codex.ontology.entities import RepositorySymbol
from codex.provider.capability import Capability
from codex.provider.contract import ProviderAdapter, ProviderExtractionError
from codex.reconciliation.reconciler import reconcile_relationship
from codex.registry.models import ProviderEvaluationStatus
from codex.registry.registry import CapabilityRegistry
from codex.repository.models import RepositoryMetadata
from codex.resolution.entity_resolver import resolve_entities

_USABLE = frozenset({ProviderEvaluationStatus.AVAILABLE, ProviderEvaluationStatus.PARTIAL})


def _build_version_id(
    repository_id: str, revision: str, provider_versions: dict[str, str]
) -> str:
    """Deterministic version id from TAD §19's actual composite key.

    Not just ``repository_revision`` alone: TAD §19 defines a graph
    version as ``repository_revision + provider_versions + schema_version
    + policy_version`` — if a provider's own version changes between two
    ingestion runs of the *same* revision (e.g. an adapter upgrade), that
    is a materially different graph version per TAD's own definition, so
    it must not collide on the same id.
    """
    fingerprint = ",".join(
        f"{name}={version}" for name, version in sorted(provider_versions.items())
    )
    if fingerprint:
        return f"{repository_id}:{revision}:{fingerprint}"
    return f"{repository_id}:{revision}"


class IngestionPipeline:
    """PROVIDERS -> INGESTION PIPELINE -> EVIDENCE NORMALIZATION -> CANONICAL GRAPH."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        evidence_store: EvidenceStore,
        *,
        provider_authority: Mapping[str, float] | None = None,
    ) -> None:
        """``provider_authority`` feeds TAD §38's Reconciliation formula
        (``codex.reconciliation``, post-D7 directive Phase C) — a
        per-provider trust weight with no defined source anywhere in
        HLRD/TAD (the same kind of gap ADR-018 already resolved for
        ``evidence_quality``/``cost_factor``). Unlike ADR-018's factors,
        a missing entry here defaults to full trust (``1.0``) rather than
        raising: this weight only adjusts *confidence* on an
        already-committed relationship, not provider *selection*, so
        every existing caller that never configures it keeps working
        unchanged."""
        self._registry = registry
        self._evidence_store = evidence_store
        self._provider_authority = provider_authority or {}
        self._entities: dict[str, dict[str, RepositorySymbol]] = {}
        self._relationships: dict[str, dict[tuple[str, str, str], CanonicalRelationship]] = {}

    def run(
        self,
        repository: RepositoryMetadata,
        *,
        capabilities: Collection[Capability] | None = None,
        now: datetime | None = None,
    ) -> IngestionResult:
        """Run one ingestion pass for ``repository`` at its current ``head_revision``.

        ``capabilities`` defaults to every capability any registered
        provider declares; pass an explicit subset to restrict this run
        (e.g. only ``{Capability.HISTORY}``). ``now`` is accepted only
        for deterministic testing of the produced ``GraphVersion``'s
        ``created_at``; real callers should omit it.
        """
        if capabilities is not None:
            requested = frozenset(capabilities)
        else:
            requested = self._all_declared_capabilities()
        to_run, skip_reasons = self._select_providers(requested, repository)
        adapters_by_name = {a.provider_name: a for a in self._registry.registered_providers()}

        outcomes: list[ProviderRunOutcome] = []
        for name in sorted(to_run):
            outcome = self._run_one_provider(adapters_by_name[name], to_run[name], repository)
            outcomes.append(outcome)
        for name in sorted(skip_reasons):
            outcomes.append(
                ProviderRunOutcome(
                    provider_name=name,
                    status=ProviderRunStatus.SKIPPED,
                    detail=skip_reasons[name],
                )
            )

        self._reconcile_relationships(repository.repository_id, now=now)
        graph_version = self._publish(repository, outcomes, now=now)
        graph_store = self._materialize_store(repository.repository_id, graph_version)

        return IngestionResult(
            repository_id=repository.repository_id,
            repository_revision=repository.head_revision,
            graph_version=graph_version,
            graph_store=graph_store,
            provider_outcomes=outcomes,
        )

    def _all_declared_capabilities(self) -> frozenset[Capability]:
        return frozenset(
            capability
            for adapter in self._registry.registered_providers()
            for capability in adapter.supported_capabilities
        )

    def _select_providers(
        self, requested: frozenset[Capability], repository: RepositoryMetadata
    ) -> tuple[dict[str, frozenset[Capability]], dict[str, str]]:
        """Provider discovery + capability selection + eligibility evaluation.

        Delegates every decision to the Capability Registry's
        ``evaluate()`` (D2) — never branches on a provider's name
        (directive D4 §3) and never bypasses the Registry (directive D4
        §4). A provider is selected to run for exactly the capabilities
        the Registry classified AVAILABLE or PARTIAL for it; capabilities
        where it was UNAVAILABLE/INELIGIBLE/FAILED are recorded as a skip
        reason, not a run failure — it was never attempted. A provider
        usable for at least one requested capability is never reported
        as skipped, even if some other requested capability was denied.
        """
        to_run: dict[str, set[Capability]] = {}
        skip_notes: dict[str, list[str]] = {}
        for capability in requested:
            for evaluation in self._registry.evaluate(capability, repository):
                if evaluation.status in _USABLE:
                    to_run.setdefault(evaluation.provider_name, set()).add(capability)
                else:
                    skip_notes.setdefault(evaluation.provider_name, []).append(
                        f"{capability.value}: {evaluation.status.value}"
                    )
        skip_reasons = {
            name: "; ".join(notes) for name, notes in skip_notes.items() if name not in to_run
        }
        return {name: frozenset(caps) for name, caps in to_run.items()}, skip_reasons

    def _run_one_provider(
        self,
        adapter: ProviderAdapter,
        capabilities: frozenset[Capability],
        repository: RepositoryMetadata,
    ) -> ProviderRunOutcome:
        """Extraction orchestration + failure isolation for one provider.

        Any exception here — the contract's own ``ProviderExtractionError``
        included — is caught and turned into a ``FAILED`` outcome for
        *this provider only*; it never aborts the run or discards another
        provider's already-committed contribution (directive D4 §6, §14).
        """
        requested_names = frozenset(c.value for c in capabilities)
        try:
            extraction = adapter.extract(repository, capabilities)
        except ProviderExtractionError as exc:
            return ProviderRunOutcome(
                provider_name=adapter.provider_name,
                status=ProviderRunStatus.FAILED,
                capabilities_requested=requested_names,
                failure_reason=exc.reason,
                detail=exc.detail,
            )
        except Exception as exc:  # noqa: BLE001 - isolate any adapter bug, directive D4 §6/§14
            return ProviderRunOutcome(
                provider_name=adapter.provider_name,
                status=ProviderRunStatus.FAILED,
                capabilities_requested=requested_names,
                detail=f"unexpected error during extract(): {exc}",
            )

        problem = self._validate_extraction(adapter, repository, extraction.cohort)
        if problem is not None:
            return ProviderRunOutcome(
                provider_name=adapter.provider_name,
                status=ProviderRunStatus.FAILED,
                capabilities_requested=requested_names,
                detail=problem,
            )

        try:
            normalized = adapter.normalize(extraction)
        except Exception as exc:  # noqa: BLE001 - isolate any adapter bug, directive D4 §6/§14
            return ProviderRunOutcome(
                provider_name=adapter.provider_name,
                status=ProviderRunStatus.FAILED,
                capabilities_requested=requested_names,
                detail=f"unexpected error during normalize(): {exc}",
            )

        entities_upserted, id_map = self._commit_entities(
            repository.repository_id, normalized.entities
        )
        evidence_upserted = self._commit_evidence(
            repository.repository_id, normalized.evidence, id_map
        )
        self._evidence_store.add_cohort(normalized.cohort)

        return ProviderRunOutcome(
            provider_name=adapter.provider_name,
            status=ProviderRunStatus.COMMITTED,
            capabilities_requested=requested_names,
            cohort=normalized.cohort,
            entities_upserted=entities_upserted,
            evidence_upserted=evidence_upserted,
        )

    @staticmethod
    def _validate_extraction(
        adapter: ProviderAdapter, repository: RepositoryMetadata, cohort: EvidenceCohort
    ) -> str | None:
        """The pipeline's own validation stage (directive D4 §15), distinct from
        ``ProviderAdapter.validate()`` (environment/config, D1). Guards against a
        misbehaving adapter returning a result that doesn't actually correspond
        to what was requested."""
        if cohort.provider != adapter.provider_name:
            return f"cohort.provider {cohort.provider!r} != adapter {adapter.provider_name!r}"
        if cohort.source_revision != repository.head_revision:
            return (
                f"cohort.source_revision {cohort.source_revision!r} != "
                f"requested revision {repository.head_revision!r}"
            )
        return None

    def _commit_entities(
        self, repository_id: str, entities: list[RepositorySymbol]
    ) -> tuple[int, dict[str, str]]:
        """Graph update: upsert new entities via Entity Resolution (post-D7
        directive Phase B), not a raw dict overwrite.

        Re-resolving the *entire* accumulated set (previously committed
        entities plus this batch) on every call, rather than merging the
        new batch in isolation, is what makes this correct regardless of
        call order: a FILE entity Git commits with one raw path string and
        SCIP later commits with a differently-formatted (but equivalent)
        one converge onto one canonical entity either way, and no
        provider's `roles`/`provider_ids` contribution is ever silently
        discarded on a `canonical_id` collision — both real defects in
        the naive overwrite this replaces (see
        ``docs/architecture-conformance-audit.md`` §M). ``resolve_entities``
        is O(N) and re-run per commit is O(N) again, so this is O(N) per
        call (not O(N^2)) — an accepted, documented V1-scale cost per the
        directive's own "do not prematurely optimize without measurements"
        instruction; a real backend would resolve incrementally instead.

        Returns the upserted count plus a raw-id -> resolved-id map
        (identity entries included) covering every entity currently known
        for this repository, so ``_commit_evidence`` can keep
        ``Evidence.subject``/``.object`` pointed at whatever canonical id
        an entity actually resolved to — a normalized-path merge (see
        `codex.resolution.entity_resolver`) can rename a raw provider id,
        and evidence referencing the old id would otherwise dangle.
        """
        existing = list(self._entities.setdefault(repository_id, {}).values())
        result = resolve_entities(existing + entities)
        self._entities[repository_id] = {e.canonical_id: e for e in result.entities}
        id_map = {
            raw_id: merge.canonical_id
            for merge in result.merges
            for raw_id in merge.source_canonical_ids
        }
        return len(entities), id_map

    def _commit_evidence(
        self, repository_id: str, evidence_list: list[Evidence], id_map: dict[str, str]
    ) -> int:
        """Graph update: store every evidence record, and fold it into a
        ``CanonicalRelationship`` keyed by ``(subject, predicate, object)`` —
        never overwriting or excluding another provider's evidence on the
        same key (directive D4 §11; see module docstring's "Reconciliation
        is out of scope"). ``subject``/``object`` are remapped through
        ``id_map`` (Entity Resolution's raw -> resolved id map) before
        keying, so a relationship stays attached to whichever canonical
        entity its endpoints actually resolved to; the stored ``Evidence``
        record itself is never mutated (its own ``subject``/``object``
        fields keep the provider's original ids — only the relationship
        *key* used for reconciliation uses the resolved ids), preserving
        raw provenance intact (directive Phase B §10)."""
        relationships = self._relationships.setdefault(repository_id, {})
        for evidence in evidence_list:
            self._evidence_store.add_evidence(evidence)
            subject = id_map.get(evidence.subject, evidence.subject)
            obj = id_map.get(evidence.object, evidence.object)
            key = (subject, evidence.predicate.value, obj)
            relationship = relationships.get(key)
            if relationship is None:
                relationship = CanonicalRelationship(
                    subject=subject, predicate=evidence.predicate, object=obj
                )
            if evidence.evidence_id not in relationship.supporting_evidence_ids:
                relationship = relationship.model_copy(
                    update={
                        "supporting_evidence_ids": [
                            *relationship.supporting_evidence_ids,
                            evidence.evidence_id,
                        ]
                    }
                )
            relationships[key] = relationship
        return len(evidence_list)

    def _reconcile_relationships(self, repository_id: str, *, now: datetime | None) -> None:
        """Evidence Reconciliation (post-D7 directive Phase C): recompute
        every accumulated relationship's ``status``/``confidence``/
        ``contradiction_score`` from its full ``Evidence`` records, once
        per ``run()`` over the complete current accumulator state — not
        incrementally per evidence commit, so a relationship's status
        always reflects everything known about it at publish time,
        matching D4's existing "each run() builds one new, immutable,
        published GraphVersion" model.

        ``contradicting=()`` always: no current provider (Git/SCIP/
        CodeQL) can assert a negative fact — the ontology has no
        negation mechanism (see `codex.reconciliation.reconciler`'s
        module docstring) — so there is no deterministic signal this
        pipeline could use to populate it without inventing one. This
        is an intentional, documented limitation, not an oversight:
        every relationship reconciled through the real provider set
        today resolves to `SUPPORTED`/`WEAKLY_SUPPORTED`/`UNRESOLVED`
        only; `DISPUTED`/`CONTRADICTED`/`UNSUPPORTED` are proven correct
        against handcrafted evidence in `tests/test_reconciler.py`, not
        reachable end-to-end with the current three providers.
        """
        reference_time = now if now is not None else datetime.now(UTC)
        known_entity_ids = frozenset(self._entities.get(repository_id, {}))
        relationships = self._relationships.get(repository_id, {})
        for key, relationship in relationships.items():
            supporting = [
                evidence
                for evidence_id in relationship.supporting_evidence_ids
                if (evidence := self._evidence_store.get_evidence(evidence_id)) is not None
            ]
            relationships[key] = reconcile_relationship(
                relationship.subject,
                relationship.predicate,
                relationship.object,
                supporting=supporting,
                provider_authority=self._provider_authority,
                known_entity_ids=known_entity_ids,
                now=reference_time,
            )

    def _publish(
        self,
        repository: RepositoryMetadata,
        outcomes: list[ProviderRunOutcome],
        *,
        now: datetime | None,
    ) -> GraphVersion:
        provider_versions = {
            outcome.provider_name: outcome.cohort.provider_version
            for outcome in outcomes
            if outcome.status is ProviderRunStatus.COMMITTED and outcome.cohort is not None
        }
        version_id = _build_version_id(
            repository.repository_id, repository.head_revision, provider_versions
        )
        if now is not None:
            version = GraphVersion(
                version_id=version_id,
                repository_id=repository.repository_id,
                repository_revision=repository.head_revision,
                provider_versions=provider_versions,
                created_at=now,
            )
        else:
            version = GraphVersion(
                version_id=version_id,
                repository_id=repository.repository_id,
                repository_revision=repository.head_revision,
                provider_versions=provider_versions,
            )
        return version.publish()

    def _materialize_store(self, repository_id: str, version: GraphVersion) -> InMemoryGraphStore:
        store = InMemoryGraphStore(version)
        for entity in self._entities.get(repository_id, {}).values():
            store.upsert_entity(entity)
        for relationship in self._relationships.get(repository_id, {}).values():
            store.upsert_relationship(relationship)
        return store
