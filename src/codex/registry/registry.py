"""The Capability Registry (TAD §10, §31; Phase D directive D2).

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

See ``codex.registry.scoring`` for why ``rank()`` requires
``evidence_quality``/``freshness_score``/``cost_factor`` as explicit
caller-supplied inputs rather than computing or defaulting them.
"""

from __future__ import annotations

from collections.abc import Mapping

from codex.provider.capability import Capability
from codex.provider.contract import ProviderAdapter, ProviderHealthStatus
from codex.registry.models import ProviderEvaluation, ProviderEvaluationStatus
from codex.registry.scoring import ProviderScoreInputs, provider_score
from codex.repository.models import RepositoryMetadata


class CapabilityRegistry:
    """Registers providers and evaluates/ranks them per capability and repository."""

    def __init__(self) -> None:
        self._providers: dict[str, ProviderAdapter] = {}

    def register(self, adapter: ProviderAdapter) -> None:
        """Register a provider.

        Re-registering the same ``provider_name`` replaces the
        previous registration (last write wins) — a routine
        registry-management choice, not an architectural one; nothing
        in HLRD/TAD specifies duplicate-registration behavior.
        """
        self._providers[adapter.provider_name] = adapter

    def unregister(self, provider_name: str) -> None:
        """Remove a provider. A no-op if ``provider_name`` was never registered."""
        self._providers.pop(provider_name, None)

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

        Every result already passed ``capability_match`` (directive
        point 4) — a provider that doesn't declare the capability
        never appears here at all, so exclusion needs no separate step.
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
        evidence_quality: Mapping[str, float],
        freshness_score: Mapping[str, float],
        cost_factor: Mapping[str, float],
    ) -> list[ProviderEvaluation]:
        """Usable candidates (``AVAILABLE``/``PARTIAL``), scored via TAD §31's
        formula and sorted highest score first, ties broken by
        ``provider_name`` for a fully deterministic order.

        ``evidence_quality``/``freshness_score``/``cost_factor`` are
        required, per-provider-name inputs with no default — see this
        module's and ``codex.registry.scoring``'s docstrings for why.
        Raises ``ValueError`` naming any usable candidate missing from
        one of the three mappings; a provider excluded from the
        candidate set (unsupported/ineligible/failed/unavailable)
        never requires an entry.
        """
        usable = (ProviderEvaluationStatus.AVAILABLE, ProviderEvaluationStatus.PARTIAL)
        candidates = [
            evaluation
            for evaluation in self.evaluate(capability, repository)
            if evaluation.status in usable
        ]

        scored: list[tuple[float, ProviderEvaluation]] = []
        for evaluation in candidates:
            name = evaluation.provider_name
            missing = [
                label
                for label, mapping in (
                    ("evidence_quality", evidence_quality),
                    ("freshness_score", freshness_score),
                    ("cost_factor", cost_factor),
                )
                if name not in mapping
            ]
            if missing:
                raise ValueError(f"rank(): missing {missing} for provider {name!r}")

            score = provider_score(
                ProviderScoreInputs(
                    capability_match=1.0,
                    evidence_quality=evidence_quality[name],
                    availability=evaluation.availability,
                    freshness=freshness_score[name],
                    cost_factor=cost_factor[name],
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
