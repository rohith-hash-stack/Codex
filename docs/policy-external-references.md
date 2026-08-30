# Codex — External Reference / Clean-Room Implementation Policy

Status: Adopted, binding on all provider adapter work (Phase 2 onward and beyond)
Applies to: every external open-source project, repository, specification, protocol, or documentation source referenced by HLRD, TAD, `docs/research/provider-formats.md`, or `docs/resources.md`

---

## 1. The rule

Codex SHALL use external projects, specifications, protocols, and documentation as research/reference material.

Codex SHALL NOT copy, fork, translate, adapt line-by-line, or otherwise incorporate third-party source code into the Codex implementation unless an explicit license review and architectural approval has been completed first.

The default strategy is:

```
Reference → Understand → Specify → Independently Implement → Test
```

not:

```
Reference → Copy → Modify → Integrate
```

## 2. Per-resource process

For every external reference Codex draws on:

1. Study the public architecture, protocol, schema, documented behavior, and publicly described algorithms.
2. Record the resource and relevant URL in [`docs/resources.md`](resources.md).
3. Identify the specific principle or capability Codex intends to adopt.
4. Design the Codex interface independently — the adapter's shape is Codex's own, not a mirror of the external project's API.
5. Implement the functionality independently.
6. Do not copy third-party tests or implementation code.
7. Preserve the Canonical Codex Graph as Codex-owned and provider-neutral (TAD invariant #1-2) — an adapter's *behavior* may be compatible with an external provider; its *code* is always Codex's own.
8. If a capability genuinely can't be independently implemented and incorporating third-party code seems necessary, **STOP and request an explicit license decision** before writing or importing anything. Do not proceed on the assumption that "it's probably fine."

A restrictive license does **not** block researching a project's publicly available concepts — it only gates the moment any code or dependency would actually be incorporated. Licenses are an implementation constraint to plan around, not a reason to skip research.

## 3. Worked examples (from `docs/resources.md`)

| External project | Study | Codex builds |
|---|---|---|
| SCIP | The `scip.proto` specification (message shapes, symbol string scheme, roles) — already done, `docs/research/provider-formats.md` | An independently-designed `SCIPAdapter` implementing Codex's own `ProviderAdapter` contract, translating `scip.proto` concepts into `Evidence`/`RepositorySymbol` — never importing or embedding SCIP's protobuf-generated code as Codex's internal model |
| CodeQL | The documented SARIF v2.1.0 output shape — already done | An independently-designed `CodeQLAdapter` that parses SARIF as *external data*, not CodeQL's own libraries; integration only through CodeQL's documented, licensed CLI/output interface — never copying CodeQL's query engine or analysis code. **License gate already identified** (`docs/resources.md`): free tier excludes private-repo analysis, so the adapter's availability must be conditioned on license status, not assumed |
| RepoGraph (`chokevin/repograph`, `SillySerpent/Repograph`) | Public architecture/ontology only — already done | An independently-implemented Codex repository-graph capability (if ever needed as a fallback) informed by their node/edge design and storage choices (Kuzu), never their source. **License gate already identified**: `SillySerpent/Repograph` is AGPL-3.0 — wrapping or vendoring it would obligate Codex to AGPL-3.0 terms, so this is a hard "no" without a separate license decision, not just a style preference |
| Sourcegraph | Publicly documented code-navigation/GraphQL concepts (partially done — API schema still blocked from this environment) | An independently-implemented `SourcegraphAdapter` calling Sourcegraph's own documented external API, never embedding Sourcegraph's implementation |
| Git | Documented Git CLI/protocol semantics, consumed via the mature `GitPython` binding (already a declared dependency, not vendored) | `RepositoryManager` (built) and the future `GitAdapter`, both Codex-owned code calling out to Git's own CLI/library rather than reimplementing or embedding Git's internals |

## 4. Current compliance state (as of 2026-08-30)

- No provider adapter code exists yet (Phase 2 hasn't started — see `docs/architecture-conformance-audit.md`), so there is nothing to audit for a violation today.
- All Phase 1 code (`codex.ontology`, `codex.evidence`, `codex.graph`, `codex.repository`) was authored directly from HLRD/TAD specification text, not derived from or copied out of any external repository.
- `docs/resources.md` already records, per external resource, what Codex adopts vs. does not — this policy formalizes the process that produced that table and makes it binding for every future adapter (SCIP, CodeQL, Git, Sourcegraph/RepoGraph, and any Runtime Adapter).
- Two license gates are already on record and must be honored when their adapters are built: CodeQL's private-repo restriction (ADR-005) and `SillySerpent/Repograph`'s AGPL-3.0 status (ADR-006 — reference only, no code adoption).

This policy does not require any code change today. It is binding on the next implementation phase (`ProviderAdapter` contract → Git Adapter → SCIP Adapter → ...).
