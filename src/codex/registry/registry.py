"""The Capability Registry (TAD §10, §31; Phase D directive D2; ADR-018).

The central deterministic mechanism for registering
``ProviderAdapter``s, discovering which ones declare support for a
``Capability`` (TAD §10's ``Capability -> Provider(s)`` mapping), and
evaluating — per repository — which of those are actually usable
right now and how they rank against each other (TAD §31).

This module contains **no provider-specific logic** (directive point
2): every decision is made by calling the ``ProviderAdapter``
contract's own methods/properties (``provider_name``,
``provider_version``, ``supported_capabilities``, ``health_status``,
``availability()``, ``freshness``, ``validate()``,
``check_eligibility()``) — never by branching on a provider's name or
assuming a particular one (SCIP, CodeQL, Git, Sourcegraph, ...)
exists. Nothing here implements Git/SCIP/CodeQL/Sourcegraph/Runtime
behavior, and nothing hard-codes a named-provider field anywhere.

Two distinct queries, deliberately kept separate:

- ``providers_for()`` — static declared-support lookup ("SUPPORTED").
- ``evaluate()`` / ``rank()`` — live, per-repository evaluation,
  producing one of ``ProviderEvaluationStatus``'s five values.

**ADR-018 (resolved 2026-08-30):** ``rank()`` takes no caller-supplied
scoring values. ``evidence_quality``/``cost_factor`` come from a
``ProviderScoreProfile`` attached once at ``register()`` time (not
per query, so ranking for a given capability/repository is identical
regardless of which caller invokes ``rank()``); ``freshness`` is
derived from the adapter's own ``freshness`` timestamp via a single
generic default decay function. See ``codex.registry.scoring`` for
the full sourcing rationale and ``docs/architecture-conformance-
audit.md`` §I for the decision record.
"""

from __future__ import annotations

from datetime import UTC, datetime

from codex.provider.capability import Capability
from codex.provider.contract import ProviderAdapter, ProviderHealthStatus
from codex.registry.models import ProviderEvaluation, ProviderEvaluationStatus
from codex.registry.scoring import (
    ProviderScoreInputs,
    ProviderScoreProfile,
    default_freshness_score,
    provider_score,
)
from codex.repository.models import RepositoryMetadata


class CapabilityRegistry:
    """Registers providers and evaluates/ranks them per capability and repository."""

    def __init__(self) -> None:
        self._providers: dict[str, ProviderAdapter] = {}
        self._profiles: dict[str, ProviderScoreProfile] = {}

    def register(
        self, adapter: ProviderAdapter, profile: ProviderScoreProfile | None = None
    ) -> None:
        """Register a provider, optionally attaching its scoring profile.

        Re-registering the same ``provider_name`` replaces the
        previous adapter (last write wins) — a routine registry-
        management choice, not an architectural one; nothing in
        HLRD/TAD specifies duplicate-registration behavior. Omitting
        ``profile`` on a re-registration leaves any previously-set
        profile untouched (it's provider-level canonical metadata,
        not tied to a particular adapter object); passing a new one
        replaces it. A provider with no profile can still be
        registered, discovered, and evaluated — a profile is only
        required at ``rank()`` time.
        """
        self._providers[adapter.provider_name] = adapter
        if profile is not None:
            self._profiles[adapter.provider_name] = profile

    def unregister(self, provider_name: str) -> None:
        """Remove a provider and its scoring profile. A no-op if never registered."""
        self._providers.pop(provider_name, None)
        self._profiles.pop(provider_name, None)

    def registered_providers(self) -> list[ProviderAdapter]:
        return list(self._providers.values())

    def providers_for(self, capability: Capability) -> list[ProviderAdapter]:
        """Providers that declare support for ``capability`` (TAD §10).

        A static claim only — "SUPPORTED" in the Phase D directive's
        vocabulary. Returns ``[]`` if no registered provider declares
        it; never raises for an unknown/unsupported capability.
        """
        return [p for p in self._providers.values() if capability in p.supported_capabilities]

    def evaluate(
        self, capability: Capability, repository: RepositoryMetadata
    ) -> list[ProviderEvaluation]:
        """Live, per-repository evaluation of every provider declaring ``capability``.

        Every result already passed ``capability_match`` (TAD §31) —
        a provider that doesn't declare the capability never appears
        here at all, so exclusion needs no separate step.
        """
        return [
            self._evaluate_one(adapter, capability, repository)
            for adapter in self.providers_for(capability)
        ]

    def rank(
        self,
        capability: Capability,
        repository: RepositoryMetadata,
        *,
        now: datetime | None = None,
    ) -> list[ProviderEvaluation]:
        """Usable candidates (``AVAILABLE``/``PARTIAL``), scored via TAD §31's
        formula and sorted highest score first, ties broken by
        ``provider_name`` for a fully deterministic order.

        Takes no caller-supplied scoring values (ADR-018): every
        input is either derived by the Registry itself
        (``capability_match``, ``availability``, ``freshness``) or
        comes from the ``ProviderScoreProfile`` attached at
        ``register()`` time (``evidence_quality``, ``cost_factor``) —
        so two different callers ranking the same capability/
        repository at the same moment always get the same answer.
        ``now`` is accepted only for deterministic testing of
        freshness decay; real callers should omit it.

        Raises ``ValueError`` naming any usable candidate that was
        never given a ``ProviderScoreProfile`` — a provider excluded
        from the candidate set (unsupported/ineligible/failed/
        unavailable) never requires one.
        """
        usable = (ProviderEvaluationStatus.AVAILABLE, ProviderEvaluationStatus.PARTIAL)
        candidates = [
            evaluation
            for evaluation in self.evaluate(capability, repository)
            if evaluation.status in usable
        ]
        reference_time = now if now is not None else datetime.now(UTC)

        scored: list[tuple[float, ProviderEvaluation]] = []
        for evaluation in candidates:
            name = evaluation.provider_name
            profile = self._profiles.get(name)
            if profile is None:
                raise ValueError(
                    f"rank(): no ProviderScoreProfile registered for provider {name!r}"
                )

            freshness = default_freshness_score(
                self._providers[name].freshness, now=reference_time
            )
            score = provider_score(
                ProviderScoreInputs(
                    capability_match=1.0,
                    evidence_quality=profile.evidence_quality,
                    availability=evaluation.availability,
                    freshness=freshness,
                    cost_factor=profile.cost_factor,
                )
            )
            scored.append((score, evaluation.model_copy(update={"score": score})))

        scored.sort(key=lambda pair: (-pair[0], pair[1].provider_name))
        return [evaluation for _score, evaluation in scored]

    @staticmethod
    def _evaluate_one(
        adapter: ProviderAdapter, capability: Capability, repository: RepositoryMetadata
    ) -> ProviderEvaluation:
        validation = adapter.validate()
        eligibility = adapter.check_eligibility(repository)
        health = adapter.health_status
        availability = adapter.availability(capability, repository)

        if not validation.ok:
            status = ProviderEvaluationStatus.FAILED
        elif not eligibility.eligible:
            status = ProviderEvaluationStatus.INELIGIBLE
        elif health is ProviderHealthStatus.UNHEALTHY or availability == 0.0:
            status = ProviderEvaluationStatus.UNAVAILABLE
        elif availability == 1.0 and health is ProviderHealthStatus.HEALTHY:
            status = ProviderEvaluationStatus.AVAILABLE
        else:
            status = ProviderEvaluationStatus.PARTIAL

        return ProviderEvaluation(
            provider_name=adapter.provider_name,
            provider_version=adapter.provider_version,
            capability=capability,
            status=status,
            availability=availability,
            health_status=health,
            eligibility=eligibility,
            validation=validation,
        )
