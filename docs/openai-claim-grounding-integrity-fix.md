# OpenAI Claim Grounding Integrity Fix

Fixes a confirmed grounding-integrity defect reproduced on commit
`60e605b2fadd302f4a3a2cd884067dbc66d665f7`: deterministic graph evidence
contained `caller -> CALLS -> plan_query`, the OpenAI response claimed
`plan_query -> CALLS -> caller` (the reverse), and `/query` still returned
`status = OK`.

## 1. Root cause

**Not primarily a direction bug — the Verification Engine was never called
from `/query` at all.** `codex.verification` (D10.3-D10.8: Entailment
Engine, Confidence, Contradiction Handling, Re-synthesis, Answer/
Abstention Policy, State mapping) has existed, fully built and unit-tested,
since the D10 milestone. But `codex.api.service.CodexAPI.ask()` set
`AskResponse.status` purely from `GenerationStatus`:

```python
status=_ASK_STATUS_BY_GENERATION_STATUS[generation.status]
```

`AskStatus.OK` therefore meant only "the LLM returned schema-valid JSON" —
never "the claims are grounded." Confirmed directly: grepping the entire
`codex.api` package for any reference to `codex.verification` returned
nothing before this fix. `codex.benchmark`'s own prior milestone reports
already documented this same gap for the *offline* harness ("since D10's
Verification Engine still isn't wired into the harness, performed a manual
claim-grounding cross-reference") — this fix closes the identical gap on
the live `/query` path.

**A secondary, real defect also existed in the Entailment Engine itself**:
`direct_edge_match`/`find_path` compared `Claim.subject`/`.object` directly
against `CanonicalRelationship.subject`/`.object` (both always canonical
ids) with no identity-resolution step. This happened to *fail closed* (a
name never equals a canonical id, so an unresolved comparison always fell
through to `UNRESOLVED` rather than a false match) — but it also meant a
claim expressed the way an LLM actually writes one (by name or qualified
name, not an opaque id string) could never entail-match *at all*, correct
orientation included. Wiring the Verification Engine into `ask()` without
first fixing this would have made every real answer register as
"not grounded," not just reversed ones.

## 2. Investigation, before any code changed

Per the directive's explicit requirement, the following was inspected and
confirmed before writing a line of fix code:

- **Where graph evidence is serialized for OpenAI**: `codex.llm.
  openai_gateway.OpenAIGateway._build_body` — `request.evidence_package.
  model_dump(mode="json")`, the full, real `EvidencePackage` (entities,
  relationships, evidence, coverage, limitations), sent verbatim as the
  user message. **No instruction tells the model which field (`id` vs.
  `qualified_name` vs. `name`) to echo back** in `claims[].subject`/
  `.object` — the system prompt only supplies the JSON Schema for
  `StructuredAnswer`.
- **Response/claim schema**: `codex.llm.schema.Claim` — strict
  `subject: str`, `predicate: RelationshipType | str`, `object: str`,
  `claim_type: ClaimType`. `predicate` is schema-validated against the
  closed ontology (`RelationshipType` or `DERIVED_RELATIONSHIP_TYPES`)
  regardless of `claim_type` — there is no claim shape a `claim_type`
  label exempts from being a real, checkable relationship assertion.
- **Claim extraction**: `codex.llm.openai_gateway.OpenAIGateway._parse` —
  `StructuredAnswer.model_validate_json(content)`, a direct Pydantic parse
  of the model's raw JSON; no post-processing.
- **Grounding/validation logic**: `codex.verification.entailment.
  entail_claim`/`direct_edge_match`/`find_path` (D10.4) and `codex.
  verification.state.classify_claim` (D10.6) — fully built, fully tested
  in isolation, never invoked from `codex.api`.
- **Canonical entity IDs**: `codex.ontology.entities.RepositorySymbol.
  canonical_id` — globally unique by construction (identity resolution,
  D7/D9, untouched by this fix). `CanonicalRelationship.subject`/`.object`
  are always canonical ids.
- **Relationship predicate and direction handling**: `CanonicalRelationship`
  stores `subject`/`predicate`/`object` as one direction only — the graph
  layer has never had an "undirected edge" concept; direction was always
  well-defined, it just was never checked at the claim-verification layer
  at all.
- **Why the inverse edge passed validation**: because *no validation ran*.
  `status=OK` was set unconditionally whenever `GenerationStatus.OK`
  (i.e., "parsed successfully"), independent of what the claims asserted.

**Confirmed not involved, therefore not modified**: graph extraction,
identity resolution (`codex.resolution`), traversal
(`codex.planner.retrieval`), query-shaped retrieval (`codex.planner.
planner`), the graph store, or the ontology. `git diff --stat` against
this commit shows zero changes under any of `provider/`, `resolution/`,
`reconciliation/`, `query_understanding/`, `planner/`, `graph/`,
`ontology/`, `ingestion/`, `benchmark/`.

## 3. The fix

**Two files in `src/codex/`, both additive/extending existing contracts,
no redesign:**

### `src/codex/verification/entailment.py`

Added `resolve_claim_endpoint(value: str, package: EvidencePackage) ->
str | None`: resolves a claim's raw subject/object string to the one
canonical id it unambiguously names, checked strictly in this order:

1. `value` is already a real canonical id known to `package` (checked
   against both `entities` and every relationship endpoint — a canonical
   id is globally unique, so this axis is never ambiguous).
2. `value` exactly equals exactly one entity's `qualified_name`.
3. `value` exactly equals exactly one entity's bare `name`.

Any zero-match or multi-match at a given axis returns `None` — **never a
guess**. `direct_edge_match` and `find_path` now resolve both endpoints
before comparing, and compare `==` against the real relationship's own
`subject`/`object` — an edge is never treated as undirected, so
`(B, predicate, A) != (A, predicate, B)` unless `A == B`.

### `src/codex/api/service.py`

`ask()` now calls the real, unmodified `verify_claim`/`classify_claim`
(D10.4/D10.6) on every claim the model returns, in one read-only pass over
the already-retrieved `EvidencePackage` — no second LLM call, no
re-synthesis retry:

```python
if status is AskStatus.OK and claims:
    ungrounded = _ungrounded_relationship_claims(claims, package)
    if ungrounded:
        status = AskStatus.CLAIMS_NOT_GROUNDED
        detail = _grounding_failure_detail(ungrounded)
```

`AskResponse.claims` is left exactly as the LLM produced it either way —
never rewritten, never dropped — so the caller can see precisely which
claim failed.

### `src/codex/api/contracts.py`

One new, additive `AskStatus` member: `CLAIMS_NOT_GROUNDED`. The existing
five values (`OK`, `UNDERSTANDING_INCOMPLETE`, `MALFORMED_OUTPUT`,
`LLM_TIMEOUT`, `LLM_BUDGET_EXCEEDED`) are unchanged.

### A scope correction found by live E2E testing

The first draft of `_ungrounded_relationship_claims` filtered to
`claim_type in (FACT, DERIVED)` only, reasoning that `INFERENCE`/`UNKNOWN`
claims are "semantic/uncertain by definition" (TAD §47) and gating on them
would make any answer with one interpretive remark register as
"not grounded." **A live E2E check against the real model disproved this
premise**: asked about a nonexistent symbol, the real `gpt-4o-mini`
fabricated a claim (`subject CALLS "Unknown Caller"`/`"NONE"`) and
self-labeled it `claim_type=UNKNOWN`, not `FACT`; asked the ambiguous bare
name `main`, it hallucinated an entire fictitious 11-function call chain
among unrelated `main()` functions in different files, all labeled
`UNKNOWN` or `FACT` depending on the run. A `claim_type`-based exemption is
trivially defeated by the model's own unreliable self-labeling.

Corrected: gate on **every** claim regardless of `claim_type`. This is
also the more architecturally consistent choice — `classify_claim`/
`classify_answer` (the pre-existing D10.6 machinery this fix reuses) were
already claim-type-blind before this fix (TAD §47: "The LLM's own
claim_type label never overrides the deterministic check"); the original
`FACT`/`DERIVED` filter was a new exemption this fix would have invented,
not something already in the contract.

### What was deliberately *not* done

- **No re-synthesis/self-correction loop invoked.** `codex.verification.
  resynthesis.run_verification_loop` exists and could ask the model to
  retry after a contradiction — this fix does not call it. A single,
  read-only verification pass is authoritative; asking the LLM to fix
  itself is never the mechanism that decides `status`, per the directive's
  explicit "do NOT ask the LLM to self-correct as the primary fix."
- **No full `build_final_answer`/`AnswerDecision` wiring.** That pipeline's
  own "no verified claims and nothing removed -> ABSTAIN" override would
  have reclassified the *existing, already-validated* empty-claims
  ambiguous/negative-query responses (currently `AskStatus.OK` with
  `claims: []`, by design) as an abstention — a real behavior change
  outside this defect's scope and explicitly protected by the directive's
  "preserve ambiguity handling"/"preserve negative-query handling"
  regression requirements. This fix reuses only the lower-level D10.4/
  D10.6 primitives (`verify_claim`, `classify_claim`), not the full D10.5/
  D10.7/D10.8 pipeline.
- **No prompt change.** The system prompt still doesn't tell the model
  which evidence field to echo back — a legitimate, separate improvement
  candidate, not requested and not needed for the fix (identity resolution
  on the *validation* side handles it regardless of which field the model
  chooses).

## 4. Before / after: the reproduced inverse-edge case

**Before** (this exact confirmed defect): evidence `caller -> CALLS ->
plan_query`; claim `plan_query -> CALLS -> caller`; `AskStatus.OK`.

**After**, reproduced live against real evidence (§6): evidence `caller ->
CALLS -> plan_query` (25 real edges, self-hosted repository); claim
reversed (subject/object swapped on one of the model's own real claims);
`AskStatus.CLAIMS_NOT_GROUNDED`, `detail` names the exact reversed triple,
`claims` still lists all 25 claims verbatim. A correctly-oriented claim
over the identical real evidence: `AskStatus.OK`.

## 5. New regression tests (19 total)

`tests/test_verification_entailment.py` (+15, numbered to match the
directive's own required matrix):

1. exact valid CALLS claim accepted
2. reversed CALLS claim rejected (the exact confirmed defect, reproduced
   with real-shaped named entities: `caller`/`plan_query`)
3. wrong source entity rejected
4. wrong target entity rejected
5. wrong predicate rejected
6. valid REFERENCES direction accepted
7. reversed REFERENCES rejected (conservative REFERENCES semantics
   preserved — direction matters exactly as much as for CALLS)
9. nonexistent relationship rejected (real entities, no edge at all)
10. ambiguous same-name entities refuse to resolve rather than guess

Plus direct `resolve_claim_endpoint` unit tests: canonical-id preference
over name, exact qualified_name resolution, no substring/case-insensitive
matching, and resolution via a relationship-only (not separately listed in
`entities`) canonical id.

`tests/test_api_ask.py`, new `TestGroundingIntegrity` class (+4): the exact
reversed-claim reproduction at the `AskResponse` level; its
correctly-oriented positive control; mixed valid+invalid claims not
grounded (test #8); a nonexistent-relationship claim at the API level; and
claim-type-blindness proven in both directions (an unverifiable
non-`FACT` claim gates the response; a verified non-`FACT` claim does
not).

## 6. E2E grounding result (real `Codex_open_API_key`, never printed/logged/persisted)

Registered and ingested this repository itself (Git + AST providers only,
no SCIP needed) against a live `python -m codex.api` server.

**Pass 1** — real query "What calls plan_query?" against real
`gpt-4o-mini-2024-07-18`: 25 real claims returned, every single one
cross-checked programmatically against `evidence_context.relationships`
and confirmed a genuine `(source, CALLS, target)` edge. `status: OK`.

**Pass 2** — the identical real, live-retrieved `EvidencePackage` replayed
through `ask()` with the model's own first real claim's subject/object
swapped (isolating the reversal as the only variable — no synthetic
evidence, no second nondeterministic live call needed for the negative
case): `status: CLAIMS_NOT_GROUNDED`, `detail` named the exact reversed
triple, all 25 claims still present in the response.

**Two additional, unscripted live checks** (not required by the directive
but run anyway, per "run the existing direct, ambiguous, negative, and
relationship cases"):

- **Negative query** ("What calls totally_nonexistent_function_xyz_zzz?"):
  the real model fabricated a claim rather than returning `claims: []` as
  scripted fixtures assume — this fix correctly caught it
  (`CLAIMS_NOT_GROUNDED`), while `negative_query_result=NO_EVIDENCE_FOUND`
  still reached `evidence_context.limitations` unmodified (negative-query
  *signal* handling unregressed; the response's overall grounded-status
  claim is now honest where it previously would not have been).
- **Ambiguous query** ("What calls main?"): `evidence_context.limitations`
  still correctly reported `"ambiguous target: 11 distinct entities match
  this query"` (ambiguity *signal* handling unregressed). The real model
  hallucinated an entire fictitious call chain among the 11 candidates;
  this fix correctly caught it (`CLAIMS_NOT_GROUNDED`).

Both are additional real evidence the fix works as intended, not a second
defect requiring separate remediation — not investigated further or
fixed, per "do not fix unrelated issues found during validation."

## 7. Full test / Ruff / mypy results

- **Python**: 1370/1370 passing (was 1351, +19), `ruff check src tests
  scripts` clean, `mypy src` clean (91 source files, via `python3 -m
  mypy` — this environment's bare `mypy` on `PATH` resolves to a
  different, pydantic-less interpreter, a pre-existing environment quirk
  documented in `docs/local-manual-verification.md`, not a code defect).
- **TypeScript**: 49/49 passing, `tsc` clean. `AskStatus`'s TS mirror
  (`vscode-extension/src/codexClient.ts`) gained `"CLAIMS_NOT_GROUNDED"`;
  `askPanelView.ts`'s status-explanation switch gained a matching case
  (the pre-existing badge-class/explanation fallbacks were already safe
  defaults, but explicit beats implicit — the new status now renders a
  red "error" badge with a real explanation rather than a blank one).
- **Frozen artifacts**: `git diff --stat` confirms zero changes under
  `tests/fixtures/benchmark/`, `benchmark_runs/`, or any of `provider/`,
  `resolution/`, `reconciliation/`, `query_understanding/`, `planner/`,
  `graph/`, `ontology/`, `ingestion/`, `benchmark/`.

## 8. Remaining limitations

- **The system prompt still doesn't tell the model which evidence field
  to echo back** in `claims[].subject`/`.object`. This fix makes
  validation robust to whichever the model chooses (canonical id,
  qualified name, or bare name — resolved deterministically), but a
  prompt-side nudge toward canonical ids specifically could reduce
  ambiguous-resolution failures further. Not implemented — out of this
  fix's scope (prompt engineering was not requested, and the directive's
  "do NOT ask the LLM to self-correct as the primary fix" argues against
  leaning on prompt tuning as the fix mechanism).
- **`CLAIMS_NOT_GROUNDED` is a single status covering a range of
  severities** — one ungrounded claim among many, or every claim
  ungrounded, both produce the same status value (though `detail` always
  names exactly which claim(s) failed). The fuller D10.5-D10.8 pipeline
  (`handle_contradictions`/`build_final_answer`, TAD §49-50's
  `PARTIALLY_VERIFIED`/`QUALIFIED`/`DISPUTED` granularity) would give a
  finer-grained answer here, at the cost of also changing empty-claims
  ambiguous/negative-query behavior (§3, "what was deliberately not
  done") — a legitimate candidate for a future, dedicated milestone, not
  bundled into this defect fix.
- **No re-synthesis attempt on an ungrounded claim.** The existing
  `run_verification_loop` could ask the model to retry once when a claim
  is significantly contradicted — not wired in here, per the directive's
  explicit instruction not to make self-correction the primary fix
  mechanism. A future milestone could add this as a genuine UX
  improvement (retry once, but still gate on the deterministic result
  either way) without weakening this fix's core guarantee.
- **Live E2E model behavior is nondeterministic by nature** — the exact
  claims/hallucinations shown in §6 will not reproduce byte-identically
  on a re-run; the mechanism (identity-aware, directional entailment
  gating every claim) is what's being verified, not a specific model
  output.
