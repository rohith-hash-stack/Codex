"""Query-Level Cache (TAD §54-55; directive D9 Part 13).

TAD §54's four cache-key dimensions (repository, graph_version,
schema_version, policy_version) plus query identity are already all
present on the existing `GraphVersion` model (`repository_id`,
`version_id`, `schema_version`, `policy_version`) -- no new field was
needed anywhere else. The cache is correct **by construction**: a stale
entry can never be returned because its key would differ (a graph
update publishes a new `GraphVersion.version_id`), matching TAD §55's
"fixed graph_version is mandatory" / "current query continues against
locked version" requirement without any explicit invalidation logic.

**V1 scope limitation, documented not silently claimed solved**: TAD
defines no versioning granularity finer than the whole `GraphVersion`
(TAD §54's "semantic contracts do not automatically invalidate merely
because an unrelated file changed" principle). Inventing a sub-
`GraphVersion` partial-invalidation scheme would be exactly the kind of
unspecified mechanism `docs/policy-external-references.md`'s sibling
"no silent architectural decisions" rule prohibits -- V1's cache
granularity is the full `GraphVersion`, nothing finer.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from codex.graph.version import GraphVersion
from codex.planner.models import RetrievalPlan
from codex.query_understanding.models import QueryContract


def compute_query_identity(contract: QueryContract) -> str:
    """A deterministic content hash of `QueryContract` -- two calls with
    an identical contract produce the same identity (cache hit); any
    field difference produces a different one (cache miss)."""
    payload = contract.model_dump_json()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class CacheKey:
    """TAD §54's exact required dimensions plus query identity."""

    repository_id: str
    graph_version_id: str
    schema_version: str
    policy_version: str
    query_identity: str


def cache_key_for(*, graph_version: GraphVersion, query_identity: str) -> CacheKey:
    return CacheKey(
        repository_id=graph_version.repository_id,
        graph_version_id=graph_version.version_id,
        schema_version=graph_version.schema_version,
        policy_version=graph_version.policy_version,
        query_identity=query_identity,
    )


class PlanCache:
    """In-memory query-execution-scoped cache (TAD §54's "query-execution
    scoped where applicable"). Storage technology remains an ADR (TAD
    §77) -- this in-memory default matches every other store in Codex
    (`InMemoryGraphStore`, `InMemoryEvidenceStore`)."""

    def __init__(self) -> None:
        self._entries: dict[CacheKey, RetrievalPlan] = {}

    def get(self, key: CacheKey) -> RetrievalPlan | None:
        return self._entries.get(key)

    def put(self, key: CacheKey, plan: RetrievalPlan) -> None:
        self._entries[key] = plan


__all__ = ["CacheKey", "PlanCache", "cache_key_for", "compute_query_identity"]
