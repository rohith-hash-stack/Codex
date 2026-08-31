# Provider Format Research Notes

> Working notes on the real wire formats/reference implementations behind the HLRD Resource Map (§62), gathered before building provider adapters (TAD Phase 2). Feeds ADR-004 (SCIP), ADR-005 (CodeQL), ADR-006 (Sourcegraph/RepoGraph), and touches ADR-001 (graph storage) and ADR-015 (API protocol). Not an ADR itself — just source material.

Researched: 2026-08-30. Some primary docs (`docs.github.com`, `codeql.github.com`, `sourcegraph.com`) were unreachable from this environment (egress-proxy blocked) — see **Gaps** below.

---

## SCIP (`sourcegraph/scip`, `scip.proto`)

Top-level messages, from `scip.proto`:

- **`Index`** — `metadata`, `documents[]` (workspace files), `external_symbols[]` (symbols defined outside the workspace, e.g. in dependencies).
- **`Metadata`** — `version`, `tool_info` (`name`, `version`, `arguments[]`), `project_root` (URI), `text_document_encoding`.
- **`Document`** — `language`, `relative_path`, `occurrences[]`, `symbols[]` (the `SymbolInformation` defined in this file), optional `text`.
- **`Symbol`** — `scheme`, `package` (`manager`, `name`, `version`), `descriptors[]`. Symbols serialize to a string: `<scheme> <package> (<descriptor>)+` or `local <local-id>` for file-local symbols — this string is what `Occurrence.symbol` and `SymbolInformation.symbol` carry.
- **`Descriptor`** — `name`, `disambiguator`, `suffix` (enum: `Namespace, Type, Term, Method, TypeParameter, Parameter, Meta, Local, Macro`).
- **`SymbolInformation`** — `symbol`, `documentation[]` (markdown), `relationships[]`, `kind` (enum, ~86 values: `Class, Function, Method, Variable, Interface, ...`), `display_name`, `signature_documentation`, `enclosing_symbol` (parent, mainly for locals).
- **`Relationship`** — `symbol`, `is_reference`, `is_implementation`, `is_type_definition`, `is_definition` (booleans, not mutually exclusive).
- **`Occurrence`** — `range`/`typed_range` (line/col span), `symbol`, `symbol_roles` (bitset enum `SymbolRole`: `Definition, Import, WriteAccess, ReadAccess, Generated, Test, ForwardDefinition`), `syntax_kind`, `diagnostics[]`.

**Mapping onto `codex.ontology` / `codex.evidence` (already-built Phase 1 model):**

| SCIP | Codex |
|---|---|
| `SymbolInformation.symbol` | `RepositorySymbol.provider_ids["scip"]` |
| `SymbolInformation.kind` | `BaseEntityType` via an explicit kind→base-type table (SCIP's 86 kinds are far finer-grained than our 16 base types — most will collapse, e.g. `AbstractMethod`/`StaticMethod`/`Constructor` → `METHOD`, with the distinction preserved as a role) |
| `Relationship.is_implementation` | `RelationshipType.IMPLEMENTS` edge, subject = the symbol carrying the relationship, object = `Relationship.symbol` |
| `Relationship.is_type_definition` | a `REFERENCES` edge to the type symbol |
| `Occurrence` with `symbol_roles & Definition` | fixes the entity's canonical `source_location` |
| `Occurrence` with `symbol_roles & ReadAccess` / `WriteAccess` | `RelationshipType.READS` / `WRITES` evidence |
| `Occurrence` with `symbol_roles & Import` | `RelationshipType.IMPORTS` evidence |

SCIP does **not** directly give call edges (`A CALLS B`) — that has to be derived by matching an `Occurrence` with a reference role inside a function's enclosing range against the referenced symbol's definition. This is exactly the kind of thing the (not-yet-built) SCIP adapter's `normalize()` has to do — it's real work, not a pass-through.

---

## CodeQL — SARIF v2.1.0 output

**Update 2026-08-30 (D6):** the structure below is now confirmed against the **authoritative OASIS SARIF 2.1.0 JSON schema** (`raw.githubusercontent.com/oasis-tcs/sarif-spec/master/sarif-2.1/schema/sarif-schema-2.1.0.json`, fetched directly) and against **real SARIF files produced by "CodeQL command-line toolchain"** (`github/codeql-action`'s own test fixtures — see `docs/resources.md`'s CodeQL row) — superseding the third-party-mirror caveat below, which no longer applies. Two corrections to the original plan, found during this verification:

1. A result's location can reference `run.artifacts[index]` by index alone with no inline `uri` (confirmed in a real fixture, `fingerprinting.input.sarif`) — a conforming parser must resolve `artifactLocation.index` against `run.artifacts[]`, not assume `uri` is always present.
2. `threadFlowLocation.kinds` — the field that would carry per-step semantics like "read"/"write"/"source"/"sink" — is **optional, freeform, and not populated in any real fixture inspected**. This directly overturns the original plan below ("each `threadFlowLocation` step becomes a `DEPENDS_ON`/`READS`/`WRITES` edge"): there is no deterministic signal to assign those predicates per step, so D6 does not attempt it — see `docs/resources.md` and `docs/architecture-conformance-audit.md`'s D6 entry for the resolved design (source→sink `REFERENCES` only, derived from the codeFlow's own explicit first/last locations).

(Original structure notes, first researched via a third-party mirror since `codeql.github.com` itself was unreachable — retained for history; the shape held up under verification.)

```
sarifLog
├── version = "2.1.0"
└── runs[]
    ├── tool.driver          # toolComponent: name, organization, version, rules[]
    │   └── rules[]          # reportingDescriptor: id, shortDescription, fullDescription, defaultConfiguration.level
    ├── artifacts[]          # indexed file list: location.uri, index
    └── results[]
        ├── ruleId, ruleIndex
        ├── message
        ├── locations[].physicalLocation.artifactLocation.{uri | index}   # index resolves against run.artifacts[]
        ├── locations[].physicalLocation.region.{startLine,startColumn,endLine,endColumn}  # all optional
        ├── partialFingerprints                # dedup key
        └── codeFlows[].threadFlows[].locations[].location   # only for @kind path-problem queries
```

**Mapping onto Codex (original plan, see the D6 update above for what was actually implemented):**

- Each `result` → one `Evidence` record; `raw_reference` points at the SARIF file + result index (resolvable via the Artifact Store, TAD §52).
- `result.locations[0]` gives `subject`'s source location directly; for a plain `@kind problem` query there is no natural `object` — CodeQL's structural/security findings are frequently *properties of one entity*, not *relationships between two*, which the TAD's `Evidence{subject, predicate, object}` shape doesn't naturally fit. Plan: represent single-entity findings as a self-relationship or a separate `Annotation` evidence subtype rather than forcing them through `CanonicalRelationship` — worth flagging explicitly in ADR-005 rather than deciding now. **Resolved in D6**: plain `problem`-kind results are represented as a role on the file entity, not a fabricated `Evidence` record — see the D6 update above.
- ~~`@kind path-problem` queries (data flow) map naturally: each `threadFlowLocation` step becomes a `DEPENDS_ON`/`READS`/`WRITES` edge between the entities at consecutive steps~~ — **superseded, see the D6 update above**: `threadFlowLocation.kinds` gives no reliable per-step signal in practice.
- `rule.defaultConfiguration.level` (`error`/`warning`/`note`) is a severity, not a probability — it should feed `Evidence.confidence` through an explicit policy mapping, not be used directly as `[0,1]` confidence.
- CodeQL's distinguishing capability per the Capability Registry (TAD §10) is `DATA_FLOW` / security queries, not `CALL_RELATIONSHIP` — SCIP is the cheaper, more complete source for plain call/reference edges; CodeQL should be selected by the planner specifically when a query needs path/data-flow evidence.

---

## RepoGraph reference implementations (HLRD §10)

Both are informative precedent, not adapters we call into — Codex normalizes their *idea*, not their code.

### `chokevin/repograph` (Go, tree-sitter)

- **7 node types**: repository, directory, file, class/struct, function, method, module-scope variable — this is a near-exact subset of our `BaseEntityType`.
- **7 edge types**: containment (`dir→file`), imports, calls, references, inheritance, symbol definition, method-of-class — maps directly onto `RelationshipType.{CONTAINS, IMPORTS, CALLS, REFERENCES, EXTENDS, CONTAINS}`.
- **Architecture**: `Scanner → Parser Orchestrator → Language Plugins (tree-sitter grammars via CGo) → Query Engine → CLI/API`, one plugin interface per language, <2s target on typical repos.
- Confirms tree-sitter (already an HLRD §23 reference) as a credible engine for a from-scratch repository-graph adapter if no external provider like Sourcegraph is available.

### `SillySerpent/Repograph` (Python, Kuzu)

Architecturally the closest existing analog to Codex itself:

- Persists to **Kuzu**, an embedded property-graph database, via a `GraphStore` component.
- Captures **git co-change coupling** as an edge — this already exists in our ontology as `RelationshipType.CO_CHANGED_WITH`, confirming it's a real, implementable signal, not just an HLRD aspiration.
- Also tracks interface→implementation mapping and constructor dependencies — both fit existing `IMPLEMENTS`/`DEPENDS_ON`.
- Pipeline: static analysis first, then an *optional runtime overlay* — the same static/runtime evidence split as HLRD §14/TAD §58 (`Can this relationship exist?` vs `Was this relationship observed?`).
- Exposes itself via CLI, a Python API, **and an MCP server**.

**Implications for open ADRs:**

- **ADR-001 (graph storage)**: add **Kuzu** to the candidate list alongside Neo4j/Dgraph/NetworkX (TAD §48) — it's embedded (no server process to operate), and a reference implementation of this exact problem already uses it successfully.
- **ADR-015 (API protocol)**: MCP is a real, precedented way to expose a repository-graph tool to agents, not just REST/GraphQL/gRPC (TAD §69) — worth weighing given Codex's own stated audience is AI agents/LLMs.

---

## Gaps

`docs.github.com`, `codeql.github.com`, and `sourcegraph.com` are blocked by this environment's outbound egress proxy. Specifically not yet verified against a primary source:

- ~~CodeQL SARIF field names above came from a third-party mirror... not verbatim-confirmed~~ — **closed 2026-08-30 (D6)**: `raw.githubusercontent.com` (unlike `codeql.github.com`) is reachable from this environment. Fetched the authoritative OASIS SARIF 2.1.0 schema directly and validated against real SARIF files produced by "CodeQL command-line toolchain" — see the D6 update above and `docs/resources.md`'s CodeQL row.
- Sourcegraph's Code Navigation GraphQL API (query names, auth, precise-vs-search-based navigation semantics) — not fetched at all. The Sourcegraph Adapter (ADR-006) still needs this before implementation; revisit from an environment that can reach `sourcegraph.com`, or ask the user for internal API docs/an existing client library.
