# Codex

Codex is an evidence-backed repository intelligence platform that sits between a software repository and AI agents/LLMs. It integrates existing code-intelligence providers (SCIP, CodeQL, Git, Sourcegraph/RepoGraph, optional runtime) behind adapters, normalizes their output into a canonical, versioned repository graph, and exposes it to LLMs through query understanding, evidence-aware retrieval, and a verification layer — so the LLM reasons over repository evidence rather than becoming the repository's source of truth.

## Status

Architecture baseline established, Phase 1 (Foundation) scaffolding underway. See [PROGRESS.md](PROGRESS.md) for the dated status log and open items.

- [docs/HLRD.md](docs/HLRD.md) — V1 High-Level Requirements Document (scope, architecture, invariants, and success criteria).
- [docs/TAD.md](docs/TAD.md) — V1 Technical Architecture Document (components, data model, DTD pipeline, dependency rules, ADR list).

## Project layout

```
src/codex/
    ontology/     # base entity types, roles, relationship types (HLRD §16-18, TAD §12-14)
    evidence/      # Evidence, EvidenceCohort, CanonicalRelationship + in-memory store (TAD §15-18)
    graph/         # GraphStore interface + in-memory (NetworkX) implementation, GraphVersion (TAD §12,19-20,53)
    repository/    # Repository Manager: registration, cloning, revision + change detection (TAD §7)
tests/            # pytest suite for the above
```

Storage/provider technology choices are intentionally deferred to ADRs (TAD §77); the in-memory implementations here are Phase 1 defaults behind stable interfaces so the rest of the system can be built without waiting on them.

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

ruff check src tests   # lint
mypy src                # type-check
pytest --cov=codex      # tests
```
