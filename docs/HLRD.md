# Codex — High-Level Requirements Document

Document: Codex High-Level Requirements Document
Version: 1.0
Date: August 30, 2026
Status: Architecture Baseline — FROZEN
Scope: V1
Next Phase: Technical Architecture → ADRs → Component Design → Implementation

**Amendment log (Architecture Reconciliation, 2026-08-30):** two clauses below were reconciled against the TAD via `docs/architecture-reconciliation.md` — §16's `TESTS` relationship (superseded by `TESTED_BY`, matching TAD §14) and §42-43's verification-state list (now a presentation-layer mapping of TAD §50's canonical internal taxonomy, not a competing definition). Both are marked inline below. The rest of this document is unchanged and remains frozen.

---

## 1. Executive Summary

Codex is an evidence-backed repository intelligence platform designed to sit between a software repository and AI agents/LLMs.

Rather than rebuilding existing code-intelligence technologies, Codex shall integrate existing capabilities through adapters, normalize their outputs, and construct a Canonical Codex Graph.

The primary intelligence flow is:

```
Repository
    ↓
Provider / Adapter Layer
    ↓
Evidence Normalization
    ↓
Entity Resolution
    ↓
Relationship Reconciliation
    ↓
Evidence Fusion
    ↓
Canonical Codex Graph
    ↓
Codex Intelligence Engine
    ↓
Query Understanding
    ↓
Query Planning
    ↓
Evidence Retrieval
    ↓
Ranking
    ↓
Minimum Sufficient Subgraph
    ↓
Coverage Assessment
    ↓
Context Builder
    ↓
LLM / Agent
    ↓
Verification
    ↓
Answer
    ↓
Telemetry
    ↓
Offline Learning / Calibration
```

The fundamental architectural principle is:

> The LLM reasons over repository evidence; it does not become the repository's source of truth.

---

## 2. Problem Statement

Modern software repositories contain multiple forms of knowledge:

- source structure
- symbols
- imports
- calls
- inheritance
- implementations
- dependencies
- APIs
- configuration
- tests
- historical changes
- runtime observations
- static-analysis findings
- semantic relationships

Existing technologies can extract significant portions of this information.

However, these technologies generally operate independently.

Codex addresses the integration problem by providing:

1. a common evidence model,
2. a canonical repository graph,
3. provider-independent entity and relationship representation,
4. query understanding,
5. query planning,
6. evidence-aware retrieval,
7. graph and semantic ranking,
8. evidence coverage assessment,
9. controlled LLM context construction,
10. verification,
11. telemetry and offline learning.

---

## 3. Vision

Codex shall become a repository intelligence substrate for AI agents.

Instead of repeatedly asking an LLM to rediscover:

- "What files exist?"
- "What calls what?"
- "Where is this class implemented?"
- "Which tests cover this method?"
- "Where does this API eventually write?"

Codex should deterministically retrieve and organize that information.

The LLM should primarily perform tasks where semantic reasoning is genuinely necessary.

---

## 4. Core Architectural Principle

Codex shall follow:

```
Deterministic Evidence
        ↓
Structured Intelligence
        ↓
Targeted Retrieval
        ↓
Minimum Sufficient Context
        ↓
LLM Reasoning
        ↓
Verification
```

It shall avoid:

```
Repository
    ↓
Huge code dump
    ↓
LLM
    ↓
Guess
```

---

## 5. Architectural Scope

V1 shall focus on single-repository intelligence.

V1 shall support:

- repository ingestion
- graph construction
- incremental updates
- provider integration
- entity resolution
- relationship reconciliation
- provenance
- query understanding
- query planning
- retrieval
- ranking
- subgraph selection
- evidence coverage
- LLM integration
- verification
- telemetry
- offline learning/calibration

V1 shall not require:

- multi-repository intelligence
- universal runtime instrumentation
- training a foundation model
- autonomous graph self-modification
- custom graph neural networks
- reinforcement learning
- complete semantic representation of every programming language feature.

---

## 6. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         CODEX                                │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │                  PROVIDER LAYER                       │  │
│  │                                                       │  │
│  │ Sourcegraph │ RepoGraph │ SCIP │ CodeQL │ Git │      │  │
│  │ Runtime                                                   │
│  └───────────────────────────┬───────────────────────────┘  │
│                              ↓                              │
│                  Evidence Normalization                     │
│                              ↓                              │
│                    Entity Resolution                        │
│                              ↓                              │
│               Relationship Reconciliation                   │
│                              ↓                              │
│                       Evidence Fusion                       │
│                              ↓                              │
│                 ┌────────────────────────┐                  │
│                 │ CANONICAL CODEX GRAPH │                  │
│                 └────────────┬───────────┘                  │
│                              ↓                              │
│                 CODEX INTELLIGENCE ENGINE                   │
│                              ↓                              │
│                   Query Understanding                       │
│                              ↓                              │
│                    Query Complexity                         │
│                              ↓                              │
│                     Query Contract                          │
│                              ↓                              │
│                      Query Planner                           │
│                              ↓                              │
│                  Capability Resolution                       │
│                              ↓                              │
│                    Retrieval Policy                          │
│                              ↓                              │
│                   Candidate Generation                        │
│                              ↓                              │
│                  Evidence-aware Ranking                       │
│                              ↓                              │
│              Minimum Sufficient Subgraph                      │
│                              ↓                              │
│                   Coverage Assessment                         │
│                              ↓                              │
│                     Context Builder                           │
│                              ↓                              │
│                        LLM / Agent                            │
│                              ↓                              │
│                       Verification                            │
│                              ↓                              │
│                     Answer / Action                           │
│                              ↓                              │
│                      Telemetry Store                          │
│                              ↓                              │
│                  Offline Learning / Calibration               │
└─────────────────────────────────────────────────────────────┘
```

---

## 7. Provider / Adapter Architecture

Codex shall not recreate capabilities that existing technologies already provide where suitable integrations exist.

The provider layer shall expose adapters such as:

```
Sourcegraph Adapter
RepoGraph Adapter
CodeQL Adapter
SCIP Adapter
Git Adapter
Runtime Adapter
        ↓
Canonical Codex Graph Model
        ↓
Codex Intelligence Engine
```

The adapters shall isolate provider-specific schemas, APIs, versions, and limitations from the canonical graph.

---

## 8. Minimum V1 Provider Set

V1 shall require:

- SCIP
- CodeQL
- Git
- + at least one qualified repository intelligence/search provider

The repository intelligence provider may be:

- Sourcegraph
- a qualified open-source repository graph implementation
- another provider satisfying Codex capability requirements.

Runtime is optional in V1.

The requirement is therefore based on capability, not a permanent dependency on a particular vendor.

---

## 9. Sourcegraph Adapter

The Sourcegraph adapter shall consume available repository intelligence capabilities such as:

- code search
- precise navigation
- symbol information
- cross-reference information
- repository navigation.

Reference:

- [Sourcegraph Documentation](https://sourcegraph.com/docs/)
- [Sourcegraph Code Navigation](https://sourcegraph.com/docs/code-navigation)

Sourcegraph functionality shall remain behind an adapter boundary.

---

## 10. RepoGraph Adapter

Codex may integrate existing repository graph implementations.

These implementations are:

> Reference implementations, not mandatory production dependencies.

Examples for research/reference:

- [RepoGraph reference implementation](https://github.com/chokevin/repograph/)
- [RepoGraph reference implementation](https://github.com/SillySerpent/Repograph/)

The adapter shall translate provider-specific graph representations into the canonical Codex model.

---

## 11. SCIP Adapter

SCIP shall provide precise source-code indexing information where available.

Potential information includes:

- symbols
- definitions
- references
- relationships
- documentation
- source locations.

Resources:

- [SCIP repository](https://github.com/sourcegraph/scip/)
- [Sourcegraph Indexer Documentation](https://sourcegraph.com/docs/code-navigation/writing-an-indexer)

---

## 12. CodeQL Adapter

CodeQL shall provide static-analysis evidence.

Potential capabilities include:

- structural queries
- data-flow analysis
- security analysis
- dependency analysis
- path queries
- semantic relationships.

Resource:

- [CodeQL Documentation](https://codeql.github.com/docs/contents/)
- [CodeQL Language Reference](https://codeql.github.com/docs/ql-language-reference/)

---

## 13. Git Adapter

Git shall provide historical evidence including:

- commits
- revisions
- file changes
- renames
- deletions
- introductions
- co-change relationships
- historical repository state
- temporal relationships.

Git history shall be treated as evidence rather than automatically treated as semantic truth.

---

## 14. Runtime Adapter

Runtime integration shall be provider-specific in V1.

Potential sources include:

- Pytest coverage
- JaCoCo
- OpenTelemetry
- language-specific runtime instrumentation
- application tracing.

Universal runtime instrumentation is explicitly out of V1 scope.

Reference:

- [OpenTelemetry Documentation](https://opentelemetry.io/docs/)

---

## 15. Evidence Normalization

All providers SHALL be converted into a common evidence representation.

The normalization layer shall preserve:

- provider
- provider version
- repository revision
- snapshot
- extraction mechanism
- source location
- confidence
- timestamp
- evidence type.

Provider-specific data shall not directly determine the canonical graph schema.

---

## 16. Canonical Codex Graph

The canonical graph is the central repository knowledge structure.

It shall represent:

### Entities

Examples:

```
Repository
Directory
File
Module
Namespace
Class
Interface
Function
Method
Variable
Test
Configuration
ExternalLibrary
API
Database
RuntimeComponent
```

### Relationships

Examples:

```
CONTAINS
IMPORTS
DEPENDS_ON
CALLS
REFERENCES
IMPLEMENTS
EXTENDS
OVERRIDES
TESTED_BY
CONFIGURED_BY
EXPOSES
CONSUMES
PERSISTS_TO
CO_CHANGED_WITH
OBSERVED_CALL
```

> **Reconciled 2026-08-30 (C-1, see `docs/architecture-reconciliation.md` §2):** this list originally read `TESTS`. TAD §14 uses the opposite direction, `TESTED_BY` (`production_code --TESTED_BY--> test`); that direction is now canonical. There is no separate `TESTS` relationship — the inverse question ("what does this test test?") is answered by traversing `TESTED_BY` edges by object instead of subject at query time, not by storing a second edge type.

The ontology shall remain extensible.

---

## 17. Base Types vs Roles

Codex SHALL separate base entity types from roles.

For example:

```
Function
 ├── API
 ├── EntryPoint
 ├── HTTPHandler
 └── TestTarget
```

rather than treating every concept as an independent base type.

This prevents ontology explosion.

The V1 ontology targets broad coverage of common programming constructs rather than perfect representation of every language feature.

Provider-specific extension nodes/roles may represent:

- advanced generics
- traits
- mixins
- macros
- language-specific constructs.

---

## 18. Canonical Identity

Each graph entity SHALL have a stable canonical identity.

Identity may incorporate:

- repository
- repository revision
- source location
- qualified name
- provider identifiers
- language
- entity type.

Multiple providers may refer to the same canonical entity.

---

## 19. Entity Resolution

Codex SHALL resolve equivalent provider entities into canonical entities.

Example:

```
SCIP Symbol
      \
CodeQL Entity ----→ Canonical Function
      /
Sourcegraph Symbol
```

Entity resolution shall preserve all provider references rather than deleting them.

---

## 20. Relationship Reconciliation

Multiple providers may produce contradictory evidence.

Example:

```
SCIP:
A CALLS B

CodeQL:
A CALLS B

Runtime:
A CALLS B

Provider X:
A DOES_NOT_CALL B
```

Codex SHALL NOT silently overwrite contradictory evidence.

The graph should retain:

- supporting evidence
- contradicting evidence
- provider
- provider reliability
- recency
- confidence

A relationship may therefore have:

```
confidence = 0.94

support:
  SCIP
  CodeQL
  Runtime

contradiction:
  Provider X
```

---

## 21. Evidence Provenance

Every significant graph assertion SHALL be traceable to its evidence.

Example:

```
Relationship:
A CALLS B

Evidence:
 ├── SCIP index
 ├── CodeQL query
 ├── Runtime trace
 └── source location
```

This allows Codex to answer:

> "Why does Codex believe A calls B?"

with actual evidence.

---

## 22. Evidence Cohorts and Versioning

Providers update at different frequencies.

Codex shall therefore use an Evidence Cohort concept.

Example:

```
Evidence Cohort
 ├── repository_revision
 ├── provider
 ├── provider_version
 ├── provider_snapshot
 ├── generated_at
 └── validity_window
```

A graph version may reference multiple evidence cohorts.

Example:

```
Graph Version 104

Repository:
 commit abc123

SCIP:
 index 77

CodeQL:
 snapshot 31

Runtime:
 09:00–09:30 observation window
```

---

## 23. Incremental Graph Updates

Codex SHALL support incremental updates where practical.

Preferred flow:

```
Git Change
    ↓
Affected Files
    ↓
Affected Symbols
    ↓
Affected Relationships
    ↓
Incremental Reconciliation
    ↓
Updated Graph
```

The system should avoid rebuilding the entire graph for small repository changes when reliable incremental information is available.

Reference:

- [Tree-sitter Documentation](https://tree-sitter.github.io/tree-sitter/)

---

## 24. Query Understanding

Query understanding shall be progressive.

Preferred routing:

```
User Query
    ↓
Deterministic Interpretation
    ↓
SLM
    ↓
LLM fallback
```

The system shall consider:

- query confidence
- query complexity
- ambiguity
- repository scope
- available graph evidence.

---

## 25. Query Complexity

Query complexity is an independent dimension from confidence.

Example:

"What class contains LoginController?"

Low complexity.

"Trace authentication from HTTP request through middleware, service, database, and tests."

High complexity.

Complexity may influence:

- retrieval strategy
- number of providers
- planning depth
- required verification
- model selection.

---

## 26. SLM / LLM Routing

The architecture shall minimize expensive LLM invocation.

Initial routing may use configurable thresholds:

```
Confidence > 0.85
    → execute

0.50–0.85
    → execute + possibly request clarification

< 0.50
    → larger LLM fallback
```

These values are initial configuration values, not immutable scientific constants.

They SHALL be calibrated against validation data.

---

## 27. Query Ambiguity Resolution

For ambiguous queries, Codex shall support:

- ranked alternatives
- evidence-based disambiguation
- session context
- clarification requests.

Example:

"Where is the login handler?"

```
1. AuthController.login()
   confidence 0.91

2. LoginHandler.handle()
   confidence 0.76

3. UserService.login()
   confidence 0.61
```

The LLM should not be invoked merely because a deterministic ambiguity exists.

---

## 28. Query Intent

Codex shall classify requests into logical intents.

Examples:

```
CODE_LOOKUP
CALL_GRAPH
EXECUTION_FLOW
IMPACT_ANALYSIS
ARCHITECTURE
DEPENDENCY
TEST_COVERAGE
HISTORY
DEBUGGING
SECURITY
REFACTORING
CONCEPTUAL
```

Intent determines retrieval strategy.

---

## 29. Query Contract

Every sufficiently complex query shall produce a Query Contract.

Conceptually:

```
Query Contract
 ├── Intent
 ├── Scope
 ├── Required Entities
 ├── Required Relationships
 ├── Required Evidence
 ├── Completeness Criteria
 └── Answer Constraints
```

The contract defines what Codex must retrieve before considering an answer sufficiently grounded.

---

## 30. Query Planner

The planner shall convert user intent into logical operations.

Examples:

```
FIND_CALLERS
TRACE_EXECUTION
FIND_IMPLEMENTATIONS
FIND_TESTS
FIND_IMPACT
FIND_DEPENDENCIES
HISTORY_ANALYSIS
ARCHITECTURE_ANALYSIS
```

The planner shall remain provider-independent.

---

## 31. Provider Capability Registry

Codex shall maintain a capability registry.

Example:

```
Operation: FIND_CALLERS

SCIP        ✓
CodeQL      ✓
Sourcegraph ✓
Runtime     ✓
Git         ✗
```

The planner selects providers according to:

- capability
- availability
- repository scope
- evidence quality
- cost
- latency.

---

## 32. Retrieval Policy

Retrieval SHALL be intent-specific.

Possible retrieval modes:

```
Graph traversal
Lexical retrieval
Semantic retrieval
Provider query
Runtime retrieval
Historical retrieval
Hybrid retrieval
```

No single retrieval mechanism shall be considered universally optimal.

---

## 33. Candidate Generation

Codex SHALL generate candidate evidence using multiple signals.

Possible sources:

```
Graph
Search
Embeddings
Provider results
Runtime
Git history
```

The candidate generation stage should favor high recall.

Ranking and verification subsequently reduce irrelevant or weak candidates.

---

## 34. Semantic Representations

Embeddings are optional.

Possible representations include:

```
Node embeddings
Edge embeddings
Path embeddings
Subgraph embeddings
```

These are optimizations and SHALL NOT become prerequisites for the canonical graph.

The canonical graph remains the mandatory representation.

Reference:

- [TransE / Knowledge Graph Embeddings research](https://arxiv.org/abs/1301.3485)

---

## 35. Evidence-aware Ranking

Ranking SHALL combine normalized signals.

Potential signals:

```
Semantic relevance
Structural relevance
Relationship relevance
Evidence quality
Intent relevance
Path relevance
Runtime relevance
Temporal relevance
Provider agreement
Redundancy
```

Signals used in additive ranking SHALL be normalized to comparable ranges.

Conceptually:

```
R =
w1 × semantic
+
w2 × structural
+
w3 × relationship
+
w4 × evidence
+
...
```

Weights may be learned offline.

---

## 36. Minimum Sufficient Subgraph

Codex SHALL retrieve the smallest subgraph sufficient to satisfy the Query Contract.

Example:

Query: "Who calls PaymentService?"

Instead of:

```
Entire repository
        ↓
LLM
```

Codex should retrieve:

```
PaymentService
   ↑
   ├── OrderService
   ├── BillingService
   └── CheckoutController
```

plus required evidence.

The optimizer SHALL NOT remove evidence required to satisfy completeness merely because it has a lower relevance score.

---

## 37. Evidence Coverage

Confidence and coverage are independent.

**Confidence** — How strongly does available evidence support an assertion?

**Coverage** — How much of the evidence required by the query has actually been obtained?

Example:

```
Confidence = 0.94
Coverage   = 0.42
```

means:

> Evidence found so far is highly trustworthy, but the answer is incomplete.

Coverage shall be query-intent dependent.

---

## 38. Completeness Contract

Different queries require different definitions of completeness.

Example:

"Where is LoginController?"

Coverage target: 100% lookup confidence

Whereas:

"Explain application architecture."

may require:

```
major subsystem coverage
+
entry points
+
important dependencies
+
key relationships
```

It does not require every graph node.

Therefore completeness SHALL be defined by the Query Contract.

---

## 39. Retrieval Stopping Condition

Retrieval should continue while:

```
coverage < required level
AND
additional retrieval has meaningful expected value
```

Retrieval may stop when:

- coverage requirement satisfied

or:

- additional retrieval is unlikely to improve coverage

or:

- provider capabilities are exhausted

This prevents arbitrary fixed top-K retrieval from becoming the primary stopping mechanism.

---

## 40. Context Builder

The Context Builder shall convert selected evidence into a controlled context package.

The package may include:

- Entities
- Relationships
- Paths
- Source snippets
- Evidence
- Provenance
- Confidence
- Coverage
- Uncertainty
- Repository revision
- Constraints

The context should be minimal while remaining sufficient.

---

## 41. LLM Boundary

This is a non-negotiable architectural invariant.

> The LLM SHALL NOT be treated as an authoritative source of repository truth.

The LLM receives evidence from Codex.

```
Canonical Evidence
       ↓
      LLM
       ↓
Verification
```

The Verification Layer is the enforcement mechanism against hallucination and unsupported conclusions.

This boundary SHALL NOT be violated in V1.

---

## 42. Verification Continuum

> **Reconciled 2026-08-30 (C-3, see `docs/architecture-reconciliation.md` §4):** the four labels below are a **presentation-layer mapping**, not the canonical internal verification model. The canonical internal model is TAD §50's six-value taxonomy (`VERIFIED, PARTIALLY_VERIFIED, QUALIFIED, DISPUTED, INCONCLUSIVE, REJECTED`); the Verification Engine, LLM Gateway response contract, telemetry, and answer contracts all use that enum. This section's four labels are how a canonical state is reported at the HLRD/presentation level, per the mapping table in TAD §50.

Verification reporting SHALL support these presentation labels:

```
FULLY_VERIFIED
PARTIALLY_VERIFIED
UNVERIFIED
CONTRADICTED
```

**Fully verified** — Evidence supports the assertion. (Maps from canonical `VERIFIED`.)

**Partially verified** — Some assertions are supported while others lack sufficient evidence. (Maps from canonical `PARTIALLY_VERIFIED` or `QUALIFIED`.)

**Unverified** — Adequate evidence could not be found. (Maps from canonical `INCONCLUSIVE`.)

**Contradicted** — Evidence conflicts with the conclusion. (Maps from canonical `DISPUTED` or `REJECTED`.)

---

## 43. Answer Policy

Verification status shall influence the answer. The policy below is expressed in this document's presentation labels (see the §42 mapping note); TAD §50 gives the canonical internal semantics each label is derived from.

```
FULLY_VERIFIED
    → strong answer

PARTIALLY_VERIFIED
    → qualified answer

UNVERIFIED
    → qualify / abstain

CONTRADICTED
    → explain conflict / downgrade confidence / possibly abstain
```

---

## 44. Learning Architecture

Codex shall separate:

- Repository Truth

from:

- Learned Intelligence

Learning may improve:

- query classification
- complexity prediction
- provider selection
- ranking
- retrieval policy
- confidence calibration
- subgraph selection
- cache behavior.

Learned models SHALL NOT directly modify canonical repository truth.

---

## 45. Learning Deployment Lifecycle

The production learning pipeline shall follow:

```
Production Telemetry
        ↓
Training Dataset
        ↓
Offline Evaluation
        ↓
Shadow Mode
        ↓
Canary
        ↓
Production
        ↓
Monitoring
        ↓
Rollback
```

Online learning shall be restricted to:

- statistics
- cache tuning
- non-authoritative operational optimization.

---

## 46. Feedback Integration

Codex SHALL capture:

### Explicit feedback

- Thumbs up
- Thumbs down
- User correction

### Implicit feedback

- Click-through
- Follow-up query
- Repeated retrieval
- Query abandonment

Feedback shall flow into the Telemetry Store.

Important:

> Feedback is a learning signal, not automatically ground truth.

---

## 47. Multi-Repository Boundary

V1 is explicitly:

> Single repository.

Future multi-repository intelligence may require:

- Cross-repository entity resolution
- Federated graph
- Cross-service dependency resolution
- Authorization boundaries
- Distributed graph queries

This is outside V1.

---

## 48. Graph Storage Decision

The HLRD shall remain implementation-neutral.

Technical Architecture shall evaluate storage technologies based on:

1. graph traversal performance
2. path queries
3. subgraph retrieval
4. node/edge scale
5. update frequency
6. incremental update support
7. versioning
8. query language
9. operational complexity
10. ecosystem
11. team expertise
12. cost.

Potential candidates may include:

- Neo4j
- Dgraph
- NetworkX
- relational/property-graph alternatives.

No graph database is selected by this HLRD.

---

## 49. Caching

Codex should support caching for:

- provider results
- entity resolution
- query understanding
- query plans
- embeddings
- graph traversals
- ranking
- context packages.

Cache keys SHALL account for relevant:

- repository revision
- provider version
- model version
- graph version.

---

## 50. Security and Trust

Codex SHALL enforce:

- repository isolation
- provider authorization
- credential isolation
- context authorization
- sensitive-data controls
- auditability
- repository scope restrictions.

The LLM shall receive only information authorized for the relevant repository and query.

---

## 51. Graceful Degradation

Provider failure SHALL reduce evidence availability rather than create fabricated certainty.

Example:

```
CodeQL unavailable
     ↓
SCIP + Sourcegraph available
     ↓
Reduced evidence
     ↓
Lower coverage/confidence
     ↓
Qualified answer
```

Not:

```
CodeQL unavailable
     ↓
LLM guesses
     ↓
Answer presented as fact
```

---

## 52. Observability

Codex SHALL provide traceability across the complete query lifecycle.

Telemetry should include:

```
Query ID
Repository
Revision
Query intent
Complexity
Query plan
Providers used
Provider failures
Retrieval operations
Candidate counts
Ranking signals
Selected subgraph
Coverage
Model calls
Token usage
Latency
Verification status
Answer outcome
User feedback
```

This enables both operational debugging and offline learning.

---

## 53. Data Retention

Retention shall be configurable.

- Graph versions: N revisions / N days
- Telemetry: configured retention
- Learning datasets: versioned + governed retention

Sensitive repository metadata shall be minimized and anonymized where feasible.

Telemetry SHALL NOT become an uncontrolled shadow copy of repository source code.

---

## 54. External API Boundary

External API protocol selection is deliberately outside this HLRD.

Potential future interfaces include:

- REST
- GraphQL
- gRPC
- MCP
- CLI
- SDK

The final choice belongs to Technical Architecture and ADRs.

---

## 55. Quantitative V1 Performance Targets

Initial engineering targets:

| Metric | Target |
|---|---|
| Query latency | < 5 sec p95 |
| Graph update | < 10 min / 1,000 files |
| LLM tokens | < 4,000/query |
| Baseline repository size | 100K LOC |

These are provisional targets.

They SHALL be validated and potentially adjusted using benchmark results.

---

## 56. Quantitative V1 Success Criteria

Initial targets:

| Criterion | Target |
|---|---|
| Precision@10 | > 0.80 |
| Recall@10 | > 0.75 |
| Token efficiency | ≥ 50% reduction vs naïve retrieval |
| Factual accuracy | > 0.85 |
| Assertion traceability | ≥ 90% |

Evaluation SHALL use a versioned benchmark corpus with validated ground truth.

---

## 57. Benchmark Requirements

Codex SHALL eventually establish benchmark repositories representing:

- small repositories
- medium repositories
- large repositories
- multiple programming constructs
- complex call graphs
- inheritance
- dependency relationships
- tests
- configuration
- historical changes
- ambiguous queries
- negative queries
- incomplete evidence
- contradictory evidence.

The benchmark shall contain ground truth sufficient to measure:

```
Retrieval
Ranking
Coverage
Accuracy
Verification
Token efficiency
Latency
```

---

## 58. Architectural Decision Records

Significant technology choices SHALL be recorded as ADRs.

Expected ADR categories:

```
ADR-001 Graph Storage
ADR-002 Provider Architecture
ADR-003 SCIP Integration
ADR-004 CodeQL Integration
ADR-005 Sourcegraph / Repository Graph Integration
ADR-006 Embedding Strategy
ADR-007 SLM Selection
ADR-008 LLM Selection
ADR-009 Retrieval Architecture
ADR-010 Ranking Strategy
ADR-011 Versioning
ADR-012 Runtime Integration
ADR-013 External API
```

---

## 59. V1 Explicitly Out of Scope

The following are not required for V1:

```
Foundation-model training
Universal compiler implementation
Universal language semantic coverage
Replacing Sourcegraph
Replacing CodeQL
Replacing SCIP
Universal runtime instrumentation
Multi-repository federation
Autonomous graph self-modification
Reinforcement learning
Custom GNN architecture
Full autonomous software-development agent
```

---

## 60. Future Architecture

Once the repository graph is stable, Codex may support additional specialized graphs.

Potential future architecture:

```
Canonical Repository Graph
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
    Planning Graph        QA Graph          Architecture Graph
          │                   │                   │
          ▼                   ▼                   ▼
    Planning Agent        QA Agent          Architecture Agent
          │                   │                   │
          └───────────────────┼───────────────────┘
                              ▼
                       Coordinator Agent
```

This is future scope, not a V1 requirement.

---

## 61. Architectural Invariants

These SHALL be treated as Codex's fundamental architectural rules.

- **INV-001 — Canonical Truth**: The canonical graph is the authoritative representation of repository knowledge.
- **INV-002 — Evidence Provenance**: Significant graph assertions SHALL retain provenance.
- **INV-003 — LLM Boundary**: The LLM is not repository truth.
- **INV-004 — Verification**: Model conclusions SHALL be verified where deterministic verification is possible.
- **INV-005 — Confidence ≠ Coverage**: Confidence and coverage SHALL be represented independently.
- **INV-006 — Completeness**: Query completeness SHALL be defined through a query-specific completeness contract.
- **INV-007 — Provider Independence**: The canonical graph SHALL remain independent of individual provider implementations.
- **INV-008 — Learning Isolation**: Learned intelligence SHALL NOT directly mutate canonical repository truth.
- **INV-009 — Progressive Model Usage**: Codex SHALL minimize expensive LLM usage through deterministic and SLM processing wherever practical.
- **INV-010 — Graceful Degradation**: Provider failure SHALL reduce evidence rather than create fabricated certainty.
- **INV-011 — Version Awareness**: Evidence SHALL remain traceable to repository and provider versions/snapshots.
- **INV-012 — Minimum Sufficient Context**: Codex SHALL attempt to provide the LLM with the minimum context necessary to satisfy the Query Contract.

---

## 62. Resource Map

These resources are references for principles and technologies incorporated into Codex.

**Repository Intelligence**
- [Sourcegraph Documentation](https://sourcegraph.com/docs/)
- [Sourcegraph Code Navigation](https://sourcegraph.com/docs/code-navigation)

**SCIP**
- [SCIP GitHub Repository](https://github.com/sourcegraph/scip/)

**CodeQL**
- [CodeQL Documentation](https://codeql.github.com/docs/contents/)
- [CodeQL Language Reference](https://codeql.github.com/docs/ql-language-reference/)

**Repository Graph Research**
- [RepoGraph reference implementation](https://github.com/chokevin/repograph/)
- [RepoGraph reference implementation](https://github.com/SillySerpent/Repograph/)

**Incremental Parsing**
- [Tree-sitter Documentation](https://tree-sitter.github.io/tree-sitter/)

**GraphRAG**
- [Microsoft GraphRAG](https://www.microsoft.com/en-us/research/project/graphrag/)
- [Microsoft GraphRAG GitHub](https://github.com/microsoft/graphrag/)

**Knowledge Graph Embeddings**
- [TransE Research Paper](https://arxiv.org/abs/1301.3485)

**LLM Orchestration**
- [LangChain Documentation](https://docs.langchain.com/)
- [LlamaIndex Documentation](https://docs.llamaindex.ai/)

**Observability**
- [OpenTelemetry Documentation](https://opentelemetry.io/docs/)

**Model Calibration**
- [Scikit-learn Probability Calibration](https://scikit-learn.org/stable/modules/calibration.html)

---

## 63. Final Codex Architecture Statement

Codex is an evidence-backed repository intelligence platform that integrates heterogeneous static, semantic, historical, and runtime code-intelligence sources into a canonical, versioned repository graph.

Its Intelligence Engine converts user queries into evidence requirements, determines query complexity and intent, selects appropriate providers, retrieves and ranks relevant graph structures, constructs a minimum sufficient subgraph, evaluates evidence coverage, builds a controlled context contract, and invokes language models only where semantic reasoning is necessary.

The resulting model answer is passed through a verification layer that distinguishes fully verified, partially verified, unverified, and contradicted conclusions.

Codex separates:

- Repository Truth

from:

- Learned Intelligence

allowing retrieval, planning, ranking, and routing to improve over time without allowing model-generated assumptions to become repository truth.

---

## 64. Final Status

| Area | Status |
|---|---|
| Research questions | CLOSED |
| Provider strategy | CLOSED |
| Canonical graph | CLOSED |
| Ontology | CLOSED |
| Entity resolution | CLOSED |
| Relationship reconciliation | CLOSED |
| Evidence/provenance | CLOSED |
| Versioning | CLOSED |
| Query understanding | CLOSED |
| Query planning | CLOSED |
| Retrieval | CLOSED |
| Ranking | CLOSED |
| Minimum sufficient subgraph | CLOSED |
| Evidence coverage | CLOSED |
| Completeness contract | CLOSED |
| LLM boundary | CLOSED |
| Verification | CLOSED |
| Learning | CLOSED |
| Feedback | CLOSED |
| Security | CLOSED |
| Observability | CLOSED |
| V1 scope | CLOSED |
| Performance targets | PROVISIONAL / BENCHMARK VALIDATION REQUIRED |
| Success metrics | PROVISIONAL / BENCHMARK VALIDATION REQUIRED |
| Graph technology | DEFERRED TO ADR |
| Embedding technology | DEFERRED TO ADR |
| LLM/SLM selection | DEFERRED TO ADR |
| External API | DEFERRED TO TECHNICAL ARCHITECTURE |

**FINAL BASELINE**

Codex HLRD v1.0 — ARCHITECTURE BASELINE FROZEN
