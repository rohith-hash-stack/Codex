# Codex

Codex is an evidence-backed repository intelligence platform that sits between a software repository and AI agents/LLMs. It integrates existing code-intelligence providers (SCIP, CodeQL, Git, Sourcegraph/RepoGraph, optional runtime) behind adapters, normalizes their output into a canonical, versioned repository graph, and exposes it to LLMs through query understanding, evidence-aware retrieval, and a verification layer — so the LLM reasons over repository evidence rather than becoming the repository's source of truth.

## Status

Architecture baseline established:

- [docs/HLRD.md](docs/HLRD.md) — V1 High-Level Requirements Document (scope, architecture, invariants, and success criteria).
- [docs/TAD.md](docs/TAD.md) — V1 Technical Architecture Document (components, data model, DTD pipeline, dependency rules, ADR list).

Next phase: ADRs → Component Design → Implementation.
