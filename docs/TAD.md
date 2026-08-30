# CODEX

## Technical Architecture Document — TAD v1.0

Status: Architecture Baseline
Scope: V1 Single-Repository Intelligence
Architecture: Evidence-first, graph-centered, LLM-bounded
Parent Requirements: Codex HLRD v1.0
Design Principle: Deterministic repository intelligence first; generative reasoning second.

---

## 1. Executive Summary

Codex is a repository intelligence system that constructs a canonical representation of a software repository from multiple specialized sources and uses that representation to answer repository questions with minimal LLM usage and explicit evidence grounding.

The architecture separates:

1. Repository extraction
2. Canonical graph construction
3. Query understanding
4. Retrieval planning
5. Evidence selection
6. LLM reasoning
7. Verification
8. Telemetry and offline learning

The authoritative information flow is:

```
Repository
    ↓
Provider Evidence
    ↓
Canonical Codex Graph
    ↓
Query Understanding
    ↓
Retrieval Planning
    ↓
Evidence Selection
    ↓
LLM Reasoning
    ↓
Claim Verification
    ↓
Answer
```

The LLM is therefore not the repository discovery engine.

It is a bounded reasoning and explanation component operating over evidence already retrieved by Codex.

---

## 2. Architectural Goals

### 2.1 Primary Goals

Codex V1 SHALL:

- automatically analyze a cloned repository;
- construct a canonical repository intelligence graph;
- integrate multiple graph/evidence providers;
- preserve provider provenance;
- resolve entities deterministically wherever possible;
- understand queries using deterministic logic and an SLM;
- generate deterministic retrieval plans;
- minimize LLM invocation;
- minimize LLM token consumption;
- construct minimum sufficient evidence;
- support structural and path-based reasoning;
- verify generated repository claims;
- expose evidence traceability;
- support historical repository analysis;
- tolerate provider failures;
- support incremental graph updates.

---

## 3. Non-Goals

V1 SHALL NOT require:

- multi-repository reasoning;
- universal runtime instrumentation;
- online model training;
- mandatory embeddings;
- relational embeddings;
- autonomous repository modification;
- LLM-controlled retrieval planning;
- LLM-controlled canonical graph mutation;
- complete indexing of external libraries;
- a mandatory graph database technology.

---

## 4. Architectural Principles

**P1 — Evidence Before Generation**

```
Evidence → Reasoning
```

not:

```
Reasoning → invented evidence
```

**P2 — Deterministic Before Probabilistic**

Use deterministic mechanisms whenever the repository structure provides sufficient information.

**P3 — LLM as Synthesizer**

The LLM explains and reasons over retrieved evidence.

It does not determine repository truth.

**P4 — Verification Is Mandatory**

Repository-grounded claims must pass verification.

**P5 — Provenance Is First-Class**

Every important relationship must retain evidence provenance.

**P6 — Version Everything**

Repository revision, graph version, provider versions, schemas and policies must be identifiable.

**P7 — Fail Conservatively**

Insufficient evidence results in qualification, inconclusive results or abstention rather than fabricated certainty.

---

## 5. High-Level Architecture

```
                         ┌───────────────────┐
                         │    Repository     │
                         └─────────┬─────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
              ▼                    ▼                    ▼
           SCIP                  CodeQL                Git
              │                    │                    │
              ▼                    ▼                    ▼
       Sourcegraph/           Runtime*            Other*
        RepoGraph
              │                    │                    │
              └────────────────────┼────────────────────┘
                                   ▼
                       ┌────────────────────────┐
                       │ Provider Adapter Layer │
                       └────────────┬───────────┘
                                    │
                                    ▼
                       ┌────────────────────────┐
                       │ Evidence Normalization │
                       └────────────┬───────────┘
                                    │
                                    ▼
                       ┌────────────────────────┐
                       │ DTD-01 Canonical Graph │
                       │ + Evidence Store       │
                       └────────────┬───────────┘
                                    │
                              graph_version
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ DTD-02              │
                         │ Query Understanding │
                         └──────────┬──────────┘
                                    │
                             QueryContract
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ DTD-03              │
                         │ Query Planner        │
                         └──────────┬──────────┘
                                    │
                             RetrievalPlan
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ DTD-04              │
                         │ Evidence Selection   │
                         │ + MSS                 │
                         └──────────┬──────────┘
                                    │
                           EvidencePackage
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ LLM Reasoning       │
                         └──────────┬──────────┘
                                    │
                          Structured Response
                           + Claims[]
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │ DTD-05              │
                         │ Verification        │
                         └──────────┬──────────┘
                                    │
                         ┌──────────┼──────────┐
                         ▼          ▼          ▼
                      VERIFIED   QUALIFIED   ABSTAIN
```

"*" Optional V1 capability.

---

## 6. Component Architecture

Codex consists of the following logical components.

1. Repository Manager
2. Provider Adapter Manager
3. Capability Registry
4. Evidence Normalizer
5. Canonical Graph Engine
6. Evidence Store
7. Entity Resolution Engine
8. Query Understanding Engine
9. SLM Gateway
10. Query Planner
11. Retrieval Engine
12. Ranking Engine
13. MSS Builder
14. LLM Gateway
15. Verification Engine
16. Telemetry Store
17. Artifact Store
18. Offline Calibration Pipeline

---

## 7. Repository Manager

Responsible for:

- repository registration;
- cloning;
- revision detection;
- branch/head tracking;
- incremental change detection;
- repository metadata;
- triggering indexing.

It SHALL NOT interpret user queries.

---

## 8. Provider Adapter Layer

V1 provider interfaces:

```
ProviderAdapter
    ├── SCIPAdapter
    ├── CodeQLAdapter
    ├── GitAdapter
    ├── SourcegraphAdapter
    └── RuntimeAdapter*
```

Adapters isolate provider-specific formats from the canonical graph.

The canonical engine must never depend directly on provider-specific schemas.

---

## 9. Provider Adapter Contract

Each adapter exposes:

```
provider_name
provider_version
supported_capabilities[]
health_status
availability
freshness
extract()
validate()
normalize()
```

Example:

```
SCIPAdapter.capabilities =

[
  SYMBOL_DEFINITION,
  SYMBOL_REFERENCE,
  IMPLEMENTATION,
  CALL_RELATIONSHIP,
  TYPE_RELATIONSHIP
]
```

---

## 10. Capability Registry

The Capability Registry is the authoritative mapping:

```
Capability
    ↓
Provider(s)
    ↓
Evidence Type
    ↓
Coverage
    ↓
Cost
    ↓
Freshness
```

Example:

```
CALL_RELATIONSHIP

SCIP       → supported
CodeQL     → supported
Runtime    → optional
Git        → unsupported
```

The planner uses this registry instead of hardcoding provider assumptions.

---

## 11. Evidence Normalization

Provider output is converted into:

Canonical Evidence

Example:

```
SCIP:
symbol A references symbol B

        ↓

Evidence {
    subject = A
    predicate = REFERENCES
    object = B
    provider = SCIP
    confidence = ...
    snapshot_id = ...
}
```

The normalization layer does not decide final truth.

---

## 12. Canonical Graph

The graph contains:

- Nodes
- Edges
- Roles
- Source Locations
- External References
- Evidence References
- Version Metadata
- Status

Example node:

```
RepositorySymbol {
    canonical_id
    name
    qualified_name
    base_type
    roles[]
    source_location
    lifecycle_status
}
```

---

## 13. Base Type + Role Model

A symbol has one canonical base type and potentially multiple roles.

Example:

```
Base Type:
CLASS

Roles:
CONTROLLER
SERVICE
API_HANDLER
```

This avoids making every semantic concept a separate ontology type.

It also leaves room for future relational embeddings.

---

## 14. Canonical Relationship Model

Relationships include:

```
CALLS
REFERENCES
IMPLEMENTS
EXTENDS
IMPORTS
DEPENDS_ON
CONTAINS
READS
WRITES
TESTED_BY
CONFIGURED_BY
```

Derived relationships such as:

```
REACHES
TRANSITIVE_CALLS
INDIRECTLY_DEPENDS_ON
```

are computed at query time rather than permanently materialized in V1.

---

## 15. Evidence Model

Every significant relationship can carry:

```
Evidence {
    evidence_id
    provider
    provider_version
    snapshot_id
    source_revision
    subject
    predicate
    object
    confidence
    freshness
    independence_group
    raw_reference
    observed_at
}
```

---

## 16. Evidence Independence

Default:

```
independence_group = provider_default_family
```

If omitted, evidence SHALL be treated as non-independent.

Providers can explicitly identify independent evidence cohorts.

This prevents accidental double counting.

---

## 17. Evidence Cohort

```
EvidenceCohort {
    provider
    provider_version
    snapshot_id
    source_revision
    observed_at

    successful_capabilities[]
    failed_capabilities[]
    partial_capabilities[]

    coverage_status
}
```

This allows Codex to distinguish:

not supported

from:

supported but failed

from:

executed successfully but returned no result

---

## 18. Evidence Status

```
SUPPORTED
WEAKLY_SUPPORTED
DISPUTED
UNRESOLVED
CONTRADICTED
UNSUPPORTED
```

These states are preserved throughout the system.

---

## 19. Version Model

A graph version is composed of:

```
graph_version = {
    repository_revision,
    scip_index_version,
    codeql_snapshot_version,
    runtime_version,
    schema_version,
    policy_version
}
```

Not all components need to change together.

---

## 20. Graph Version Lock

At the beginning of DTD-03:

```
graph_version = V182
```

is locked into:

```
RetrievalPlan.graph_version
```

The same version flows through:

```
DTD-04
    ↓
EvidencePackage
    ↓
LLM
    ↓
DTD-05
```

Concurrent graph updates do not change an active query.

---

## 21. Historical Graph Storage

V1 SHALL primarily store:

```
Graph Snapshot
+
Revision Diffs
```

rather than a complete graph snapshot for every commit.

Periodic snapshots can be created.

Historical queries reconstruct:

```
nearest snapshot
+
changesets
=
target graph
```

---

## 22. Query Understanding Architecture

DTD-02 contains:

```
Deterministic Intent Detector
        ↓
SLM
        ↓
Query Contract
```

The SLM is not automatically invoked for every query.

---

## 23. Tier-0 Deterministic Understanding

Candidate intents receive:

```
match_score ∈ [0,1]
```

Routing:

```
> 0.95
    → deterministic execution

0.70–0.95
    → SLM disambiguation

< 0.70
    → SLM
```

Example:

"Who calls authenticate()?"

can strongly match:

FIND_CALLERS

Whereas:

"Call the function to test the API."

must not blindly become "FIND_CALLERS".

---

## 24. SLM Responsibilities

The SLM determines:

```
intent
targets
relationships
constraints
complexity
ambiguity
temporal requirements
completeness
```

It produces:

QueryContract

---

## 25. SLM Confidence

SLM confidence is a calibrated probability:

```
confidence ∈ [0,1]
```

It is not raw logits.

Initial policy:

```
> 0.85
    execute

0.50–0.85
    execute + qualification/clarification logic

< 0.50
    escalate to LLM
```

Calibration will be benchmark-driven.

---

## 26. Query Complexity

Complexity:

```
C = Σ(weight_i × normalized_factor_i)
```

Initial V1 weights:

```
intent_count          0.25
target_count          0.15
relationship_depth    0.25
ambiguity             0.15
temporal_dimension    0.10
reasoning_requirement 0.10
```

All factors are normalized to:

```
[0,1]
```

and:

```
Σ weights = 1
```

---

## 27. Query Contract

```
QueryContract {
    intent
    targets[]
    relationship_types[]
    constraints[]
    temporal_dimension
    complexity
    ambiguity
    confidence
    completeness_requirement
    required_evidence[]
    token_budget
    latency_budget
}
```

---

## 28. Session Context

Session context is scoped to:

one repository

and:

```
last 10 queries
OR
30 minutes
```

whichever occurs first.

Repository changes reset context.

Older context receives lower weight.

---

## 29. Query Planner

DTD-03 transforms:

QueryContract

into:

RetrievalPlan

The planner determines:

- providers;
- capabilities;
- graph traversals;
- evidence types;
- traversal depth;
- budget;
- completeness;
- stopping criteria.

---

## 30. Planner Boundary

The planner SHALL NOT depend on:

```
LLM
SLM
```

directly or transitively.

It can access:

```
Capability Registry
Graph Store
Evidence Store
Provider Adapters
```

This is an architectural enforcement boundary.

---

## 31. Provider Selection

Each provider factor is normalized:

```
[0,1]
```

Initial weighted score:

```
ProviderScore =
    0.40 capability_match
  + 0.20 evidence_quality
  + 0.15 availability
  + 0.15 freshness
  + 0.10 cost_factor
```

If:

```
capability_match = 0
```

the provider is excluded before scoring.

---

## 32. Budget-Aware Planning

Planner considers:

```
latency_budget
token_budget
```

If the initial plan exceeds budget:

1. reduce traversal depth;
2. remove optional relationship types;
3. increase stop-sufficiency threshold;
4. re-estimate;
5. execute if compliant.

If no viable plan exists:

```
PLAN_BLOCKED
```

Exhaustive queries cannot be pruned below required coverage.

---

## 33. Completeness Model

```
LOW:
≥ 50%

MEDIUM:
≥ 75%

HIGH:
≥ 90%

EXHAUSTIVE:
100%
+
complete repository coverage
```

These are initial benchmark-calibrated thresholds.

---

## 34. Negative Query Planning

For:

"Does anything call X?"

an empty result is not sufficient.

Planner must establish:

```
complete scope
+
successful required capability
+
no failed capability
+
no PARTIAL cohort
```

Otherwise:

```
INCONCLUSIVE
```

not:

```
FALSE
```

---

## 35. Retrieval Engine

The Retrieval Engine executes the RetrievalPlan.

It may perform:

```
entity lookup
edge lookup
bounded traversal
provider retrieval
historical lookup
evidence aggregation
```

It must respect:

```
graph_version
token budget
latency budget
completeness
```

---

## 36. Ranking Engine

V1 uses deterministic ranking proxies.

**Semantic relevance**

BM25 over:

```
qualified names
symbols
paths
extracted query entities
```

normalized to:

```
[0,1]
```

**Structural relevance**

```
primary relationship match = 1.0
otherwise = 0.3
```

**Graph proximity**

```
0.9^d
```

where "d" is shortest path distance.

**Constraint match**

Jaccard similarity over applicable:

```
paths
tags
roles
constraints
```

---

## 37. Ranking Formula

Conceptually:

```
Score =
    w1 semantic_relevance
  + w2 structural_relevance
  + w3 graph_proximity
  + w4 query_constraint_match
```

All signals must be normalized.

Weights are calibration parameters.

---

## 38. Evidence Contradiction

Contradiction score:

```
contradiction_score =
    Σ contradict_weight
    /
    (Σ support_weight + Σ contradict_weight)
```

where:

```
weight =
evidence_confidence
×
provider_authority
```

---

## 39. Minimum Sufficient Subgraph

MSS is the smallest evidence subgraph sufficient to answer the QueryContract.

It should minimize:

```
nodes
edges
evidence records
tokens
```

while satisfying:

completeness requirement

---

## 40. MSS Expansion

Expansion occurs only when:

SOURCE_CONTEXT

or equivalent contextual evidence is required and the current MSS does not satisfy the completeness requirement.

V1 bounds:

```
maximum cycles = 2
additional nodes/cycle = 50
```

---

## 41. Dynamic Evidence Budget

Evidence volume is tied to token budget.

Conceptually:

```
max_nodes =
min(
    100,
    token_budget / average_node_cost
)

max_edges =
min(
    250,
    token_budget / average_edge_cost
)
```

If the resulting budget cannot support a minimally viable evidence package:

```
PLAN_UNSUPPORTED
```

---

## 42. Evidence Package

The LLM receives:

```
EvidencePackage {
    graph_version
    query_contract
    entities[]
    relationships[]
    evidence[]
    source_context[]
    coverage
    limitations[]
}
```

The package is the LLM's repository context boundary.

---

## 43. LLM Gateway

The LLM Gateway:

- manages model invocation;
- validates structured output;
- enforces token limits;
- records usage;
- prevents arbitrary repository access.

The LLM receives:

```
Query
+
EvidencePackage
+
ResponseContract
```

It does not receive unrestricted repository access.

---

## 44. Structured LLM Response

V1 requires:

```json
{
    "answer": "...",
    "claims": [
        {
            "subject": "...",
            "predicate": "...",
            "object": "...",
            "claim_type": "FACT"
        }
    ]
}
```

Strict JSON Schema validation is mandatory.

Invalid output:

```
RESYNTHESIS
```

---

## 45. Claim Model

Claims are classified:

```
FACT
DERIVED
INFERENCE
UNKNOWN
```

Example:

```
FACT:
A CALLS B

DERIVED:
A REACHES C

INFERENCE:
A appears to handle authentication
```

---

## 46. Verification Engine

DTD-05 performs:

```
claim extraction validation
        ↓
evidence mapping
        ↓
entailment
        ↓
contradiction detection
        ↓
coverage verification
        ↓
confidence
        ↓
answer decision
```

---

## 47. Deterministic Entailment

V1 supports:

```
direct edge matching
path existence
bounded graph traversal
set membership
type hierarchy
```

Complex semantic assertions default to:

```
UNRESOLVED
```

unless deterministic rules exist.

---

## 48. Verification Confidence

Initial conceptual model:

```
V =
    0.35 evidence_support
  + 0.20 evidence_quality
  + 0.15 evidence_independence
  + 0.10 coverage
  + 0.10 freshness
  + 0.10 provider_authority
```

If significant contradictory evidence exists:

```
V = min(V, 0.50)
```

Weak contradiction may receive a small penalty.

---

## 49. Contradicted Claims

A contradicted claim SHALL NOT be rewritten through speculative reasoning.

Instead:

```
CONTRADICTED
    ↓
REMOVE CLAIM
    ↓
RE-SYNTHESIZE
```

Maximum V1 re-synthesis:

```
1
```

---

## 50. Final Verification States

```
VERIFIED
PARTIALLY_VERIFIED
QUALIFIED
DISPUTED
INCONCLUSIVE
REJECTED
```

The answer layer must preserve material uncertainty.

---

## 51. Traceability

Every accepted repository claim should be traceable:

```
Claim
 ↓
Canonical Entity/Relationship
 ↓
Evidence
 ↓
Provider
 ↓
Snapshot
 ↓
Source Location
```

Example:

```
C1
 ↓
Relationship R839
 ↓
SCIP Evidence E21
 ↓
SCIP Snapshot S100
 ↓
auth_service.py:42
```

---

## 52. Artifact Store

Raw provider artifacts are retained separately.

"raw_reference" SHALL be a resolvable URI.

Examples:

```
artifact://store/blob#offset

s3://bucket/key

file://absolute/path
```

The Codex Artifact Retrieval Service resolves these references.

---

## 53. Storage Architecture

Logical storage:

```
┌─────────────────────────┐
│ Canonical Graph Store   │
└─────────────────────────┘

┌─────────────────────────┐
│ Evidence Store          │
└─────────────────────────┘

┌─────────────────────────┐
│ Artifact Store          │
└─────────────────────────┘

┌─────────────────────────┐
│ Telemetry Store         │
└─────────────────────────┘

┌─────────────────────────┐
│ Cache                   │
└─────────────────────────┘
```

Technology selection remains an ADR.

---

## 54. Cache Architecture

Caches include:

```
Query Understanding Cache
Retrieval Cache
Provider Result Cache
Artifact Cache
```

Cache keys must include relevant:

```
repository
graph_version
schema_version
policy_version
query/contract identity
```

Semantic contracts do not automatically invalidate merely because an unrelated file changed.

---

## 55. Query-Level Cache

During execution:

```
fixed graph_version
```

is mandatory.

If the graph updates concurrently:

```
current query continues against locked version
```

Telemetry records:

```
CONCURRENT_UPDATE_DETECTED
```

No livelock.

---

## 56. External Libraries

V1 does not fully index external dependencies.

Example:

```
pypi:requests@2.31.0
```

can be represented as:

```
EXTERNAL_LIBRARY
```

with package-qualified identity.

This provides deterministic dependency representation without requiring external source indexing.

---

## 57. Runtime Adapter

Runtime is optional in V1.

Potential sources:

```
Pytest coverage
JaCoCo
OpenTelemetry
```

The Runtime Adapter is provider-specific.

Universal instrumentation is not required.

---

## 58. Runtime Semantics

Static evidence:

Can this relationship exist?

Runtime evidence:

Was this relationship observed?

These semantics must not be conflated.

---

## 59. Learning Architecture

Production runtime does not perform unrestricted online learning.

Production produces:

```
Telemetry
Feedback
Statistics
Cache signals
```

Offline pipeline:

```
Telemetry
 ↓
Dataset
 ↓
Evaluation
 ↓
Calibration
 ↓
Shadow
 ↓
Canary
 ↓
Production
```

---

## 60. User Feedback

Feedback enters the Telemetry Store.

Examples:

```
thumbs up
thumbs down
correction
click-through
follow-up query
explicit disambiguation
```

Feedback does not directly modify the canonical graph.

---

## 61. Security Boundary

The LLM must not have unrestricted access to:

```
filesystem
repository
provider credentials
graph mutation APIs
artifact storage
```

It receives only the approved EvidencePackage.

---

## 62. Graph Mutation Boundary

Only ingestion/update pipelines can mutate:

canonical graph

Query processing is read-only.

Verification is read-only.

LLM reasoning is read-only.

---

## 63. Failure Handling

Provider failure:

```
Provider
 ↓
failed_capabilities
 ↓
Coverage Engine
 ↓
Planner
```

Possible responses:

```
alternate provider
partial answer
INCONCLUSIVE
abstention
```

No silent assumption that missing evidence means no relationship.

---

## 64. Failure Taxonomy

```
PROVIDER_UNAVAILABLE
PROVIDER_TIMEOUT
PARTIAL_PROVIDER_RESULT
ENTITY_UNRESOLVED
GRAPH_VERSION_CONFLICT
INSUFFICIENT_EVIDENCE
PLAN_BLOCKED
PLAN_UNSUPPORTED
LLM_SCHEMA_FAILURE
VERIFICATION_FAILURE
CONCURRENT_UPDATE_DETECTED
```

Every failure should be observable.

---

## 65. Observability

Telemetry should capture:

```
query_id
repository_id
graph_version
query_contract
selected_providers
retrieval_plan
candidate counts
MSS size
LLM calls
LLM tokens
latency
verification result
unsupported claims
contradictions
cache hits
provider failures
user feedback
```

---

## 66. Key Metrics

**Retrieval**

```
Precision@10
Recall@10
MRR
```

**Answer**

```
Factual accuracy
Claim verification accuracy
Unsupported claim rate
Abstention precision
```

**Efficiency**

```
LLM calls/query
LLM tokens/query
retrieval latency
p95 end-to-end latency
```

**Graph**

```
indexing latency
update latency
graph size
evidence coverage
provider success rate
```

---

## 67. Initial V1 Targets

| Metric | Target |
|---|---|
| p95 query latency | < 5 seconds |
| Graph update | < 10 min / 1,000 files |
| LLM tokens/query | < 4,000 |
| Repository size | ~100k LOC |
| Precision@10 | > 0.80 |
| Recall@10 | > 0.75 |
| Factual accuracy | > 0.85 |
| Token reduction | ≥ 50% |
| Traceability | ≥ 90% |

These are benchmark targets, not hard architectural guarantees.

---

## 68. Multi-Repository Boundary

V1:

ONE REPOSITORY

Future:

```
Repository A
      ↕
Repository B
      ↕
Repository C
```

requires:

```
cross-repository entity resolution
federated graph queries
version coordination
service/dependency identity
```

This is V2+.

---

## 69. API Boundary

External:

```
REST
GraphQL
gRPC
```

are intentionally not fixed by this TAD.

The API architecture becomes a dedicated technical design deliverable.

---

## 70. Deployment Model

Logical deployment:

```
                    ┌──────────────┐
                    │ API Gateway  │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │ Query Service│
                    └──────┬───────┘
                           │
             ┌─────────────┼──────────────┐
             ▼             ▼              ▼
       Understanding    Planner       Verification
             │             │              │
             ▼             ▼              ▼
            SLM        Graph/Store       LLM
```

Indexing can run independently:

```
Repository
   ↓
Indexer Workers
   ↓
Provider Adapters
   ↓
Graph/Evidence Stores
```

---

## 71. Concurrency Model

Query workloads should be isolated from indexing workloads.

An active query operates against:

immutable graph_version

while indexing can build:

next graph_version

Example:

```
V182 ← active queries

V183 ← being built

V183 becomes active only after validation
```

This is effectively a versioned read model.

---

## 72. Graph Update Lifecycle

```
Git Change
   ↓
Change Detection
   ↓
Affected Files
   ↓
Provider Incremental Analysis
   ↓
Evidence Update
   ↓
Graph Reconciliation
   ↓
Validation
   ↓
New Graph Version
   ↓
Publish
```

Failed builds do not replace the current valid graph.

---

## 73. Provider Reconciliation

When providers disagree:

```
Provider A
    ↓
Evidence

Provider B
    ↓
Evidence

        ↓

Reconciliation Engine
        ↓
Canonical Relationship
        +
Supporting Evidence
        +
Contradicting Evidence
        +
Status
```

The evidence itself is not destroyed.

---

## 74. Canonical Graph Truth Model

The graph should be understood as:

> «Codex's current reconciled representation of repository structure, backed by provider evidence.»

It is not:

> «A claim that every provider independently agrees.»

This distinction is fundamental.

---

## 75. Architecture Dependency Rules

```
Provider → Evidence
Evidence → Canonical Graph

Query Understanding → Query Contract

Query Planner → Query Contract + Capabilities

Retrieval → Retrieval Plan

MSS → Evidence

LLM → EvidencePackage

Verification → Claims + Evidence

Telemetry → all runtime components

Offline Learning → Telemetry
```

Forbidden:

```
Planner → LLM
LLM → Graph Mutation
Verification → Graph Mutation
Query → Provider-specific schema
Provider → Query Understanding
```

---

## 76. Core Architectural Invariants

The following are frozen:

1. Canonical graph is provider-independent.
2. Provider-specific schemas never leak beyond adapters.
3. Evidence provenance is retained.
4. Graph versions are immutable once published.
5. Active queries use one graph version.
6. Planner does not call LLM/SLM.
7. LLM does not control retrieval.
8. LLM does not mutate the graph.
9. Verification is mandatory for repository facts.
10. Unsupported claims cannot become facts.
11. Negative claims require coverage.
12. Contradictions are preserved.
13. Derived graph relationships are computed where practical.
14. Learning is offline.
15. User feedback does not directly mutate repository truth.

---

## 77. ADR List

The following decisions should become formal ADRs during implementation:

```
ADR-001 Graph Storage Technology
ADR-002 Evidence Storage Technology
ADR-003 Artifact Storage Technology
ADR-004 SCIP Integration Strategy
ADR-005 CodeQL Integration Strategy
ADR-006 Sourcegraph/RepoGraph Integration Strategy
ADR-007 SLM Selection
ADR-008 LLM Selection
ADR-009 Embedding Strategy
ADR-010 Search/Ranking Engine
ADR-011 Cache Technology
ADR-012 Graph Versioning Strategy
ADR-013 Historical Graph Reconstruction
ADR-014 Runtime Adapter Strategy
ADR-015 API Protocol
ADR-016 Authentication/Authorization
ADR-017 Deployment Architecture
```

---

## 78. V1 Component Priority

**Mandatory**

```
Repository Manager
SCIP Adapter
CodeQL Adapter
Git Adapter
At least one Repository Graph Adapter
Canonical Graph
Evidence Store
Capability Registry
Entity Resolution
DTD-02
DTD-03
DTD-04
LLM Gateway
DTD-05
Telemetry
Artifact Store
```

**Optional**

```
Runtime Adapter
Embeddings
advanced semantic search
```

---

## 79. V2 Research

Potential V2 capabilities:

```
Relational embeddings
Graph neural representations
Learned ranking
Learned provider selection
Multi-repository graph
Advanced runtime intelligence
Cross-service reasoning
Autonomous planning
Agentic execution
```

The V1 architecture must not depend on these.

---

## 80. Implementation Phases

**Phase 1 — Foundation**

```
Repository Manager
Canonical ontology
Graph storage abstraction
Evidence model
Versioning
```

**Phase 2 — Providers**

```
SCIP
CodeQL
Git
Sourcegraph/RepoGraph
```

**Phase 3 — Intelligence**

```
Capability Registry
Entity Resolution
DTD-02
DTD-03
DTD-04
```

**Phase 4 — Reasoning**

```
LLM Gateway
EvidencePackage
Structured Claims
DTD-05
```

**Phase 5 — Validation**

```
Benchmark repositories
Ground truth
Metrics
Calibration
Failure testing
```

**Phase 6 — Production Hardening**

```
security
observability
scaling
caching
incremental indexing
version management
rollback
```

---

## 81. Testing Strategy

Codex itself should use layered testing.

**Unit**

```
ontology
normalization
entity resolution
ranking
planning
verification
```

**Contract**

```
Provider → Canonical Evidence
DTD → DTD
LLM → Verification
```

**Integration**

```
Repository
 → Providers
 → Graph
 → Query
 → Evidence
 → LLM
 → Verification
```

**Ground Truth**

Queries with manually verified expected answers.

**Adversarial**

Test:

```
ambiguous queries
missing providers
contradictory providers
stale evidence
partial indexing
negative queries
LLM hallucinations
invalid structured output
graph updates during queries
```

---

## 82. Architectural Quality Gates

Before production:

```
Provider extraction accuracy
        ↓
Graph correctness
        ↓
Entity resolution
        ↓
Retrieval accuracy
        ↓
MSS sufficiency
        ↓
LLM synthesis
        ↓
Verification accuracy
        ↓
End-to-end answer accuracy
```

A downstream success must not conceal an upstream failure.

---

## 83. Final Architecture Statement

Codex V1 is fundamentally:

```
              SPECIALIZED ANALYZERS
                       │
                       ▼
              EVIDENCE NORMALIZATION
                       │
                       ▼
               CANONICAL GRAPH
                       │
                       ▼
              QUERY UNDERSTANDING
                       │
                       ▼
                 QUERY PLAN
                       │
                       ▼
              EVIDENCE RETRIEVAL
                       │
                       ▼
                  MSS/EVIDENCE
                       │
                       ▼
                    LLM
                       │
                 CLAIMS + ANSWER
                       │
                       ▼
                VERIFICATION
                       │
                       ▼
                  TRUSTED ANSWER
```

The architectural philosophy is:

```
        STRUCTURE
           +
        EVIDENCE
           +
        GRAPH
           +
      DETERMINISTIC
        PLANNING
           +
       CONTROLLED
        REASONING
           +
       VERIFICATION
           =
    REPOSITORY INTELLIGENCE
```

The LLM is therefore inside the architecture, but not above the architecture.

---

## 84. TAD Closure Status

| Area | Status |
|---|---|
| Overall architecture | 🟢 CLOSED |
| DTD integration | 🟢 CLOSED |
| Provider architecture | 🟢 CLOSED |
| Canonical graph | 🟢 CLOSED |
| Evidence model | 🟢 CLOSED |
| Query understanding | 🟢 CLOSED |
| Planning | 🟢 CLOSED |
| Ranking | 🟢 CLOSED |
| MSS | 🟢 CLOSED |
| LLM boundary | 🟢 CLOSED |
| Verification | 🟢 CLOSED |
| Versioning | 🟢 CLOSED |
| Historical graph | 🟢 CLOSED |
| Runtime boundary | 🟢 CLOSED |
| Learning boundary | 🟢 CLOSED |
| Security boundary | 🟢 CLOSED |
| Failure model | 🟢 CLOSED |
| Observability | 🟢 CLOSED |
| Storage technology | 🟡 ADR |
| SLM/LLM selection | 🟡 ADR |
| Benchmark calibration | 🟡 Research |
| Performance validation | 🟡 Research |
| API implementation | 🟡 Technical design |
| Deployment technology | 🟡 ADR |

**FINAL STATUS**

CODEX TAD v1.0 — ARCHITECTURE BASELINE ESTABLISHED

There are no major conceptual architecture blockers remaining.

The remaining unknowns are now deliberately pushed into three controlled categories:

1. ADR decisions
2. Empirical benchmark/research
3. Implementation engineering

That is where we want to be before writing the actual implementation.
