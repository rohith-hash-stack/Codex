"""Shared fixtures for `codex.llm`/`codex.verification` tests."""

from __future__ import annotations

from codex.graph.version import GraphVersion
from codex.planner.mss import EvidencePackage


def make_graph_version(**overrides: object) -> GraphVersion:
    kwargs: dict[str, object] = {
        "version_id": "v1",
        "repository_id": "repo1",
        "repository_revision": "rev1",
    }
    kwargs.update(overrides)
    return GraphVersion(**kwargs)


def make_evidence_package(**overrides: object) -> EvidencePackage:
    kwargs: dict[str, object] = {
        "graph_version": make_graph_version(),
        "query_identity": "q1",
        "entities": [],
        "relationships": [],
        "evidence": [],
    }
    kwargs.update(overrides)
    return EvidencePackage(**kwargs)
