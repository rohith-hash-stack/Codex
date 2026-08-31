"""Session context (TAD §28; HLRD §27; directive D8 Phase 8).

Repository-scoped, deterministic query history used to bias
disambiguation (HLRD §27: "session context" as one of the supported
ambiguity-resolution mechanisms). TAD §28's rules, implemented exactly:

- scoped to **one repository** — a session tracks exactly one
  `repository_id`; there is no cross-repository sharing (a query
  against a different repository starts a fresh session, never reads
  or writes another repository's history);
- **last 10 queries OR 30 minutes, whichever occurs first** — both
  bounds are enforced, not just one;
- **repository changes reset context** — recording a query for a
  different `repository_id` than the session's own raises, rather than
  silently mixing histories (the caller is responsible for starting a
  new `SessionContext` per repository, exactly mirroring how
  `IngestionPipeline` scopes its accumulator per `repository_id`);
- **older context receives lower weight** — a documented, deterministic
  decay function (TAD names the *rule*, "older = lower weight," but no
  formula; this is a calibration point, the same kind ADR-018 already
  established the project's precedent for).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final

from codex.query_understanding.models import Intent

MAX_QUERIES: Final = 10
MAX_WINDOW = timedelta(minutes=30)
DECAY_HALF_LIFE_QUERIES: Final = 5.0
"""Calibration point (TAD §28 names "older = lower weight" as a rule,
not a formula): weight halves every this-many-queries-old, an
exponential-by-recency-rank decay -- deterministic, monotonic, and
bounded, the same style of documented default already established for
`codex.registry.scoring.DEFAULT_FRESHNESS_HALF_LIFE`."""


@dataclass(frozen=True)
class SessionEntry:
    query_text: str
    intent: Intent
    observed_at: datetime
    is_clarification: bool = False
    """TAD §28/HLRD §27: "explicit clarification actions persist within
    that scope" -- flagged so a clarification response can be weighted
    or retained differently from an ordinary query if a future SLM
    adapter needs to (D8 itself does not discriminate on this flag)."""


class RepositoryMismatchError(ValueError):
    """Raised when a query for a different repository is recorded
    against an existing session (directive Phase 8: "repository changes
    reset context" -- enforced as a hard boundary, never a silent mix)."""


@dataclass
class SessionContext:
    """One repository's sliding-window query history."""

    repository_id: str
    _entries: list[SessionEntry] = field(default_factory=list)

    def record(
        self,
        *,
        repository_id: str,
        query_text: str,
        intent: Intent,
        observed_at: datetime,
        is_clarification: bool = False,
    ) -> None:
        if repository_id != self.repository_id:
            raise RepositoryMismatchError(
                f"session is scoped to repository {self.repository_id!r}, "
                f"got {repository_id!r} -- start a new SessionContext instead"
            )
        self._entries.append(SessionEntry(query_text, intent, observed_at, is_clarification))

    def active_entries(self, *, now: datetime) -> list[SessionEntry]:
        """Entries still inside the sliding window at ``now`` -- both the
        10-query cap and the 30-minute cap applied, most recent last."""
        within_time = [e for e in self._entries if now - e.observed_at <= MAX_WINDOW]
        return within_time[-MAX_QUERIES:]

    def weighted_entries(self, *, now: datetime) -> list[tuple[SessionEntry, float]]:
        """Active entries paired with their recency-decayed weight
        (``1.0`` for the most recent, decaying by ``DECAY_HALF_LIFE_
        QUERIES`` per position further back), most recent first."""
        active = list(reversed(self.active_entries(now=now)))
        return [
            (entry, 0.5 ** (rank / DECAY_HALF_LIFE_QUERIES)) for rank, entry in enumerate(active)
        ]


__all__ = ["MAX_QUERIES", "MAX_WINDOW", "RepositoryMismatchError", "SessionContext", "SessionEntry"]
