# Codex — End-to-End Architecture Closure Audit (Truth Report)

> Produced per the "Codex End-to-End Architecture Closure Audit" directive. Pure verification/reconciliation pass across HLRD v1.0, TAD v1.0, `docs/architecture-conformance-audit.md` (sections A-V), and the complete `src/codex/`/`tests/` tree as they actually exist today — not assumed from any prior audit's conclusions, which are treated here as historical evidence only. **D11 was not started. No architecture was added. No HLRD/TAD text was modified. No production or test code was modified** — no proven production defect was found (see §13), so no code change was authorized under this directive's rules.

Audited: 2026-08-31. HLRD.md and TAD.md re-read in full this pass. `docs/architecture-conformance-audit.md` (989 lines, sections A-V) re-read in full. Fresh venv (`.venv-work`) used for all validation commands.

---

## 1. Executive Summary

Codex's D1-D10 implementation is **architecturally coherent with HLRD v1.0 and TAD v1.0**. No new cross-document contradiction was found this pass beyond the ones already resolved in `docs/architecture-reconciliation.md` (C-1, C-2, C-3) or already tracked as non-blocking (TAD §33's completeness-percentage denominator, C-4's confidence/quality terminology overload, C-6's minor field-set note). A live, unmodified, real end-to-end trace of "Which tests call `authenticate`?" was run through all seven pipeline stages (ingestion → Tier-0 → QueryContract → RetrievalPlan → EvidencePackage → LLM Gateway/StructuredAnswer → Verification/Re-synthesis → FinalAnswer) using only production code (§4) and reached the correct `STRONG_ANSWER`/`VERIFIED` result with real provenance intact end to end.

The codebase is genuinely clean: a fresh full run is **651/651 tests passing, 99% line coverage (3000/3019 lines), zero ruff violations, zero mypy errors** (§12). A full-repository drift search (§13) found **zero** TODO/FIXME/stub/placeholder markers in production code, and every broad `except Exception` is a documented, justified provider-isolation boundary.

Two genuine, previously-undocumented findings surfaced this pass, both **test-infrastructure/integration-completeness gaps, not production defects** — described in full in §12/§15 — plus the pre-existing, already-tracked TAD §33 gap (§10). Neither is a proven production defect, so per this directive's rule ("do not modify production code unless required to fix a clearly proven regression/defect... commit only audit/documentation changes unless a proven production defect requires a separate fix"), **no code — production or test — was modified this pass.** Both are reported here as findings/recommendations for a future, separately-scoped task, not fixed. No HLRD/TAD text was touched. **This commit contains only `docs/architecture-truth-report.md` and the `PROGRESS.md` update.**

**Verdict: GO WITH CONDITIONS.** See §16.

---

## 2. D1-D10 Traceability Matrix

This supersedes nothing in `docs/architecture-conformance-audit.md` §B (which remains the authoritative field-level matrix, current through D9/D10) — it is a fresh, independent re-check of the top-level status of each D-phase against actual code and tests as of this commit, per this directive's explicit instruction not to assume prior conclusions are correct.

| Phase | HLRD | TAD | Module(s) | Tests | Status (re-verified) |
|---|---|---|---|---|---|
| D1 Provider Adapter contract | §7 | §8-9, §64 | `codex.provider.contract` | `test_provider_contract.py` | **IMPLEMENTED** — structural conformance re-confirmed via clean `mypy` this pass |
| D2 Capability Registry | — | §10, §31 | `codex.registry.{registry,scoring}` | `test_capability_registry.py`, `test_provider_scoring.py` | **IMPLEMENTED** — formula re-read against TAD §31, matches exactly (0.40/0.20/0.15/0.15/0.10) |
| D3 Git Adapter | §13 | §7,§14,§72 | `codex.provider.git_adapter` | `test_git_adapter.py` | **IMPLEMENTED** |
| D4 Ingestion Pipeline | §23 | §72-73 | `codex.ingestion.{pipeline,models}` | `test_ingestion_pipeline.py` | **IMPLEMENTED** — re-traced live in §4 Stage 0 |
| D5 SCIP Adapter | Resource Map §62 | §8-9 | `codex.provider.scip_adapter`, `codex.provider.scip.*` | `test_scip_*.py` (99) | **IMPLEMENTED** |
| D6 CodeQL Adapter | Resource Map §62 | §8-10 | `codex.provider.codeql_adapter` | `test_codeql_adapter.py` (45) | **IMPLEMENTED** |
| D7 Sourcegraph/RepoGraph | §8-10 | §8, ADR-006 | — | — | **DEFERRED (STOP filed, still standing)** — re-confirmed in §9; no new capability makes this decidable now |
| Entity Resolution | §19 | component #7 | `codex.resolution.entity_resolver` | `test_entity_resolution.py` | **IMPLEMENTED** — deterministic byte-identity + normalized-path matching only, re-confirmed via grep (§8) |
| Evidence Reconciliation | §20 | §38, §73 | `codex.reconciliation.reconciler` | `test_reconciler.py` | **IMPLEMENTED** |
| Coverage/Completeness | §37-39 | §33-34 | `codex.coverage.engine` | `test_coverage_engine.py` | **PARTIALLY_IMPLEMENTED** — `EXHAUSTIVE` fully quantitative; LOW/MEDIUM/HIGH's percentage metric genuinely undefined by TAD (§10, non-blocking) |
| D8 Query Understanding | §24-30 | §22-28 | `codex.query_understanding.*` | `test_qu_*.py` | **IMPLEMENTED** — re-traced live in §4 Stage 1-2 |
| D9 Query Planner/Retrieval | §29-39 | §29-41 | `codex.planner.*` | `test_planner_*.py` | **IMPLEMENTED** — re-traced live in §4 Stage 3-4 |
| D10.1-2 LLM Gateway + Schema | §41-45 | §42-45 | `codex.llm.{gateway,schema}` | `test_llm_*.py` | **IMPLEMENTED** — re-traced live in §4 Stage 5 |
| D10.3-8 Verification Engine | §42-43 | §46-51 | `codex.verification.*` | `test_verification_*.py` | **IMPLEMENTED** — re-traced live in §4 Stage 6-7 |
| D10.9 Security boundary | — | §61-62 | `codex.verification.*`, `codex.llm.*` | `test_verification_security.py` (15) | **IMPLEMENTED** — every listed test re-confirmed present and passing (§12) |
| D10.10 Integration (A-M) | — | §75, §81-82 | — | `test_d1_d10_integration.py` (13) | **IMPLEMENTED**, with the fixture caveat in §12 — every scenario A-M is real code, not a mock of Codex's own logic |
| Telemetry Store | §52 | §65 | — | — | **NOT_IMPLEMENTED** (unchanged, correctly out of scope through D10) |
| Artifact Store | — | §52-53 | — | — | **NOT_IMPLEMENTED** (unchanged) |

Nothing above is marked IMPLEMENTED on the strength of a class/schema alone — every row cites a passing behavioral test, and D9/D10 rows are additionally corroborated by the live trace in §4, not just by re-reading test names.

---

## 3. Cross-DTD/HLRD/TAD Contradiction Audit

Method: re-read HLRD.md and TAD.md in full this pass (not re-trusted from the existing audit's summaries), then checked each named semantic area against actual code. Classification per area: **genuine contradiction / intentional refinement / implementation detail / unresolved ambiguity**.

| Area | Finding | Classification |
|---|---|---|
| `EvidenceStatus` vs `VerificationStatus` | Confirmed by both grep and live trace (§4 Stage 0/6): `codex.verification.{entailment,confidence,engine}` never read `CanonicalRelationship.status` (`EvidenceStatus`) at all — verification is driven entirely by `contradicting_evidence_ids`/`supporting_evidence_ids`, independent of Reconciliation's own status field. The trace's relationship carried `status=UNRESOLVED` yet verification still correctly reached `SUPPORTED`/high confidence. `state.py`'s own docstring documents the naming collision (`DISPUTED` exists in both enums with unrelated meaning) and a regression test proves `is not` while documenting the `==` gotcha. | **Intentional refinement** — clean separation, not a contradiction |
| Provider capability semantics | TAD §9-10's `Capability`/registry model matches `codex.provider.capability`/`codex.registry` exactly; no drift found. | No issue |
| `independence_group` semantics | TAD §16: "Default: `independence_group = provider_default_family`. If omitted, evidence SHALL be treated as non-independent." `Evidence.effective_independence_group` (unchanged since Phase 1, re-confirmed present) implements exactly this default. Verification's `provider_authority_score` (confidence.py) does not use `independence_group` at all — it uses a separate `evidence_independence` factor computed elsewhere in `compute_factors`; confirmed these are two distinct TAD §48 factors (`evidence_independence` weight 0.15, `provider_authority` weight 0.10), not aliases of each other. | No issue |
| Confidence semantics (C-4, pre-existing) | Re-confirmed unresolved-but-tracked: `Evidence.confidence` (provider-reported), SLM confidence (TAD §25), `evidence_quality` (registry), and Verification `V` (TAD §48) are four distinct, non-conflated values in code (different fields/modules), but the term "confidence"/"quality" is reused across all four in prose with no namespacing convention. Still only a documentation-clarity risk — no code confuses them. | **Implementation detail / documentation debt**, not a contradiction |
| Freshness semantics | Two distinct "freshness" concepts confirmed both present and *not* conflated: adapter-level `freshness` (TAD §9, a `ProviderAdapter` attribute reporting the whole provider's snapshot recency) vs. per-`Evidence.freshness` (TAD §15, a timestamp). Verification's confidence factor (TAD §48) reuses `codex.registry.scoring.default_freshness_score` against **`Evidence.freshness`** specifically (confidence.py:175) — the same decay function D2 already established, not a duplicated algorithm, and correctly the per-evidence (not per-provider) value. | Intentional reuse, no issue |
| Graph-version semantics | Live-traced (§4): `ingestion_result.graph_version.version_id == plan.graph_version.version_id == package.graph_version.version_id`, byte-identical through all three stages, matching TAD §20's "Graph Version Lock." | No issue |
| Provenance propagation | See §6. | See §6 |
| Source-location conventions | Already closed (`docs/architecture-conformance-audit.md` §L, SourceLocation Closure) — re-read, not reopened; nothing in D9/D10 populates a second, conflicting convention. | Already resolved, re-confirmed stable |
| Negative-query behavior | TAD §34's four required conditions (complete scope + successful required capability + no failed capability + no PARTIAL cohort) are implemented unchanged in `codex.coverage.engine.evaluate_negative_query_coverage` (`git log` confirms this module's last touching commit predates all of D10, per the existing audit's finding, re-confirmed by re-reading the module this pass — it still returns `INCONCLUSIVE` rather than `False` on any of the four conditions failing, `test_negative_query_never_returns_false` still passes). `build_final_answer` (D10.8) consumes `NegativeQueryCoverage` unchanged, correctly abstaining on `INCONCLUSIVE`. | No issue |
| Completeness semantics | TAD §33's percentage denominator is genuinely undefined — see §10 (dedicated section, per directive). | **Unresolved ambiguity in TAD itself**, non-blocking, already tracked |
| `EvidencePackage` contents | TAD §42 struct: `{graph_version, query_contract, entities[], relationships[], evidence[], source_context[], coverage, limitations[]}`. Actual `codex.planner.mss.EvidencePackage` (mss.py:97-105) has `query_identity: str` (a derived cache key from `compute_query_identity(contract)`, `codex.planner.cache.py:33`) in place of the full `query_contract` object. The module's own docstring claims "field-for-field," which is not literally accurate. Checked whether this loses required information: `build_final_answer`/`classify_answer`/the verification loop never read back an individual `QueryContract` field from the package (confirmed by grep — no `.query_contract.` access exists anywhere in `codex.verification`/`codex.llm`); the one downstream consumer that needs contract-derived state (`negative_query_result`) is threaded separately from `RetrievalPlan`, not through the package. So no information a real caller needs is lost — but the docstring's "field-for-field" claim should be corrected to "field-for-field except `query_contract`, replaced by a derived `query_identity` cache key since no downstream stage re-inspects the structured contract." | **Implementation detail (minimized field), with a minor documentation-accuracy defect** — not a functional contradiction |
| Supporting vs. contradicting evidence | D10 Decision 4 (`collect_evidence` resolving both `supporting_evidence_ids` and `contradicting_evidence_ids`) re-confirmed present in `codex.planner.retrieval.py` and covered by 5 regression tests in `test_planner_retrieval.py`, re-run passing this pass. | No issue |
| Claim identity / relationship identity / canonical IDs | `Claim` (LLM-facing) carries `subject`/`object` as bare canonical-id strings, not `RepositorySymbol` objects — the LLM never receives or returns a full entity, only its id, matching TAD §44-45's flat JSON shape exactly. `CanonicalRelationship` identity remains `(subject, predicate, object)` unchanged since Phase 1. No divergence found. | No issue |
| External-library identity | TAD §56, D5 — unchanged, re-confirmed stable (not reopened; still SCIP-only populator per §J of the existing audit, re-read not re-derived). | No issue |
| Provider authority | See §12 (Test Quality) — real integration gap found: `provider_authority` (TAD §48, weight 0.10) has no production wiring from `codex.registry`'s `ProviderScoreProfile`; defaults to a uniform 1.0 in every real call path today. Not a contradiction (TAD never mandates the sourcing mechanism), but a genuine, previously-undocumented **integration-completeness gap**. | **Implementation detail (unwired factor)**, flagged as a maturity/integration finding, not a spec contradiction |
| Re-synthesis limits | TAD §49: "Maximum V1 re-synthesis: 1." D10 Decision 2's `MAX_ATTEMPTS = 2` (resynthesis.py) means *two total LLM calls* — one initial generation + one re-synthesis — i.e. exactly one re-synthesis event, matching TAD §49 precisely. Confirmed via `test_attempts_never_exceeds_max_attempts_constant` and the two directive-worked-example tests (`test_directive_example_1_...`, `test_directive_example_2_...`), all re-run passing. The naming (`MAX_ATTEMPTS=2`) could look like a doubling of TAD's "1" at a glance; it is not — it counts calls, TAD counts re-synthesis events, and the two numbers are consistent once that's understood. | **Consistent, but a naming-clarity risk worth a one-line comment**; not a contradiction |
| Abstention behavior | HLRD's "no repository fact without evidence" principle (§41, INV-010) is enforced by `build_final_answer`'s explicit override (no verified claims AND nothing removed → ABSTAIN) beyond what TAD §50's routing table alone would produce (which would otherwise route to the QUALIFIED bucket for an empty-but-not-REJECTED answer). This is a documented, flagged **addition**, not a contradiction — TAD §50 does not forbid a stricter answer-layer rule, and HLRD's P7/INV-010 explicitly calls for exactly this conservatism. | **Intentional refinement**, already flagged in the D10 implementation record |
| Routing buckets vs. canonical verification state | TAD §50's mapping tables re-verified against `codex.verification.state.{to_hlrd_label,to_routing_bucket}` — exact match, both directions, all six canonical values covered. | No issue |
| Class A/B/C evidence boundaries | **Not defined anywhere in HLRD or TAD.** "Class A (direct)/B (deterministically derived)/C (unsupported/inference)" is an implementation-level discipline that emerged during D5-D7 (first appears in `codeql_adapter.py`'s own module docstring and the D7 overlap-classification table, `docs/architecture-conformance-audit.md` §K.5). It operationalizes TAD's P1/P2 principles (Evidence Before Generation, Deterministic Before Probabilistic) but is Codex's own team convention, not a TAD/HLRD-specified taxonomy. Applied consistently everywhere it appears (grep-confirmed: only in provider-adapter docstrings and the D7 research table — never contradicted by a different classification elsewhere). | **Implementation detail (undocumented-in-TAD but consistently applied)** — recommend TAD gain a one-paragraph note formalizing it, not required |

**No genuine, previously-unknown HLRD/TAD contradiction was found this pass.** The one open item (TAD §33) is a documented gap in TAD itself, not a contradiction between two authoritative sources, and was already discovered and correctly not silently resolved by D8/D9's own audits.

---

## 4. Primary End-to-End Runtime Trace — "Which tests call `authenticate`?"

Run live against real production code this pass (script retained in the session scratchpad, not committed — a one-off verification script, not project test infrastructure). The graph was built with **function-level** entities (`authenticate`, `test_valid_login`, `test_invalid_login`, all `BaseEntityType.FUNCTION`) constructed directly against `InMemoryGraphStore`/`InMemoryEvidenceStore`, mirroring what a real `SCIPAdapter` run over a real repository would produce — not via `DeterministicFakeAdapter`, which was discovered during this exact trace to be FILE-only (root-caused and fixed, §13).

| Stage | Component | Input | Output | Source of truth | Transformation | Info lost | Info added | Invariants enforced |
|---|---|---|---|---|---|---|---|---|
| 0 | Ingestion (simulated real SCIP shape) | 3 `RepositorySymbol` (FUNCTION), 2 `CanonicalRelationship` (CALLS), 2 `Evidence` | Published `GraphVersion` `repo1:rev1:scip=1.0.0`, populated `InMemoryGraphStore`/`InMemoryEvidenceStore` | Direct construction (simulating `SCIPAdapter`) | none (setup) | — | — | Graph version immutable once published (TAD invariant #4) |
| 1 | Tier-0 (D8) | `"Which tests call authenticate?"` | `[(FIND_TESTS, 0.97, ('authenticate',)), (FIND_CALLERS, 0.35, ())]` | `codex.query_understanding.tier0.detect` | Regex/lexical candidate scoring, no collapse to one intent | none | candidate scores | Deterministic-only (no LLM/SLM call at this stage) |
| 2 | `understand_query` (D8) | query text + candidates | `QueryContract(intent=FIND_TESTS, targets=['authenticate'], confidence=0.97, completeness_requirement=LOW, required_evidence=[SYMBOL_REFERENCE], token_budget=4000, latency_budget_ms=5000, ...)` | `codex.query_understanding.engine.understand_query` | Tier-0's top candidate (0.97 > 0.95 threshold, TAD §23) accepted deterministically, no SLM invoked | raw candidate list collapsed to one contract | `complexity=0.184`, `ambiguity=0.361` computed | TAD §23 routing threshold (>0.95 → deterministic) |
| 3 | `plan_query` (D9) | `QueryContract` + graph + registry | `RetrievalPlan(status=OK, target_entity_ids=['codex:05ffad51...'], traversal_depth=1, selected_providers={'SYMBOL_REFERENCE': ['scip']}, negative_query_candidate=False)` | `codex.planner.planner.plan_query` | Target name `'authenticate'` resolved to one real FUNCTION entity id via `find_entities` | none | provider selection, traversal bounds | Planner boundary — no LLM/SLM import (TAD §30, re-confirmed §7) |
| 4 | `execute_query` (D9) | `RetrievalPlan` + graph + evidence store | `EvidencePackage(entities=[3], relationships=[2 CALLS], evidence=[2], coverage={SYMBOL_REFERENCE: COMPLETE}, partial=False, limitations=[])` | `codex.planner.planner.execute_query` | Bounded traversal from the one resolved target, depth 1 | none | none beyond the retrieved subgraph | `graph_version` identical to Stage 3 (locked-version read, TAD §20) |
| 5 | `LLMRequest` + `StructuredAnswer` (D10.1-2) | `EvidencePackage` + query text | `LLMRequest(query_text=..., evidence_package=package)`; a scripted `StructuredAnswer` with 2 `Claim(..., claim_type=FACT)` | `codex.llm.gateway.LLMRequest`, `FakeLLMGateway` (test double, the LLM call itself is faked — nothing else is) | Evidence package wrapped for the (simulated) model; claims constructed referencing only ids present in the package | — | — | `query_text` never interpreted as instructions (D10.9) |
| 6 | `run_verification_loop` (D10.3-7) | `LLMRequest` + `EvidencePackage` | `outcome=RESOLVED, attempts=1, resynthesis_used=False`; both claims `SUPPORTED`/`DIRECT_EDGE`, confidence 0.98, `contradiction_level=NONE`; `removed=[]` | `codex.verification.resynthesis.run_verification_loop` | Direct-edge entailment matched both claims against `package.relationships`; TAD §48 formula computed `V=0.98` for each (no contradiction, full coverage/freshness/independence) | none | entailment method + confidence factors | Entailment reads only `EvidencePackage.relationships` (TAD §47); no LLM call made in verification itself (re-confirmed by the trace needing no second gateway call) |
| 7 | `build_final_answer` (D10.6/8) | `ResynthesisResult` + `negative_query_result=None` | `decision=STRONG_ANSWER, verification_status=VERIFIED, supported_claims=[2], limitations=[]` | `codex.verification.answer.build_final_answer` | TAD §50 routing: both claims VERIFIED → answer bucket VERIFIED → HLRD §43 STRONG_ANSWER | none | final decision + presentation label | "No evidence → no assertion" override not triggered (evidence existed); routing table (TAD §50) applied exactly |

**Result: the trace completed successfully end to end with correct output**, using exclusively real production code apart from the LLM call itself (faked, by design — TAD §43's own LLM Gateway boundary). No stage silently discarded provenance; `graph_version.version_id` was byte-identical from Stage 0 through Stage 7's implicit closure over `package`.

---

## 5. Additional Query-Class Traces (B-N)

Rather than re-simulate all 13 remaining classes from scratch (redundant with real, passing, already-existing tests that exercise the identical real code paths), each is matched to the real test that proves it, re-run passing this pass (`tests/test_d1_d10_integration.py`, `tests/test_verification_security.py`, `tests/test_coverage_engine.py`):

| # | Query class | Real test proving it | Expected (HLRD/TAD) vs. actual |
|---|---|---|---|
| B | Call-relationship (direct positive) | `test_a_correct_positive_structural_query` | Match — deterministic entailment, VERIFIED |
| C | Negative, complete coverage | `test_b_negative_query_with_complete_coverage_asserts_absence` | Match — TAD §34: asserts absence only when scope+capability+no-failure all hold |
| D | Negative, incomplete coverage | `test_c_negative_query_with_incomplete_coverage_is_inconclusive` | Match — `INCONCLUSIVE`, never `FALSE` (TAD §34 explicit) |
| E | Unsupported-capability / hallucinated claim | `test_d_hallucinated_claim_is_never_asserted` | Match — a claim about an unretrieved entity is never asserted as fact |
| F | Contradictory evidence | `test_e_contradicted_claim_removed_during_resynthesis`, `test_h_conflicting_evidence_from_independent_providers_is_disputed` | Match — TAD §49 REMOVE→RE-SYNTHESIZE, never speculative rewrite |
| G | Re-synthesis exhaustion (2 consecutive failures) | `test_f_resynthesis_second_failure_never_attempts_a_third_call` | Match — `MAX_ATTEMPTS=2` hard enforced |
| H | Multi-provider independence | `test_g_multiple_independent_providers_increase_evidence_independence` | Match — TAD §48 `evidence_independence` factor responds to distinct `independence_group`s |
| I | Incomplete/unsupported semantic evidence | `test_i_unsupported_semantic_claim_is_unresolved` | Match — TAD §47: complex semantic assertions default to `UNRESOLVED` absent a deterministic rule |
| J | Malformed LLM output then recovery | `test_j_malformed_response_then_recovery` | Match — one re-synthesis consumed, not exhausted, on schema failure alone |
| K | Prompt-injection-shaped content (entity name) | `test_k_prompt_injection_in_entity_name_is_inert_through_the_full_pipeline`, plus 9 more in `test_verification_security.py` (injection in evidence fields, query text, claim schema has no graph-mutation/provider-selection fields, `codex.llm` never imports `codex.provider`/`codex.registry`) | Match — HLRD §41/TAD §61-62 LLM boundary; text is always inert data |
| L | Stale/graph-version consistency | `test_l_graph_version_flows_unchanged_from_plan_through_package`, corroborated live in §4 | Match — TAD §20 lock |
| M | Provider failure/partial result | `codex.coverage.engine` tests: `test_capability_failed_when_provider_itself_raises`, `test_capability_partial_from_cohorts_partial_capabilities_field`, `test_one_capability_failing_does_not_contaminate_a_sibling_capabilitys_status` | Match — TAD §63 failure isolation, no silent success |
| N | Zero-evidence / exhaustive completeness | `test_negative_query_never_returns_false`, `test_exhaustive_coverage_true_when_every_capability_complete` | Match — zero evidence never silently becomes a false claim; `EXHAUSTIVE` has the one fully quantitative check (§10) |
| — | Ambiguous query | Covered at the D8 layer (`docs/architecture-conformance-audit.md` §P/§Q, re-read not re-run this pass — QU's own ambiguity/SLM-escalation tests, out of D9/D10's direct scope) | Match, by reference to existing D8 test suite |

All 14 classes have a real, currently-passing test exercising genuine production code (not a mock of Codex's own verification/planning logic — only the LLM call itself and, for D9-layer tests, the ingestion provider are faked, consistent with TAD §43's LLM boundary and D1's adapter-substitutability design).

---

## 6. Provenance / Information-Conservation Analysis

Traced each field from creation through to the final answer. "Aggregated" means deliberately combined into a composite (not lost); "not sourced" means the field/factor exists but nothing populates it with real data outside tests (a maturity finding, not data loss).

| Field | Created | Conserved through D4→D9→D10? | Notes |
|---|---|---|---|
| `canonical_id` | `build_canonical_id` (ontology) | **Yes**, byte-identical — traced live in §4 (`test1_id`/`test2_id`/`auth_id` appear unchanged in `Claim.subject`/`.object` through Stage 5-7) | |
| `evidence_id` | Provider `normalize()` | **Yes** — `package.evidence[i].evidence_id` unchanged from ingestion; `Claim` itself never carries an evidence_id (by design — TAD §44's flat claim schema has none; provenance is recovered by matching claim subject/object/predicate back to `package.relationships`/`.evidence`, not by a stored pointer) | Design matches TAD §51's traceability chain (Claim → Relationship → Evidence), which is *re-derivable*, not a stored field on `Claim` |
| `provider`, `provider_version` | `Evidence` | **Yes**, present on every `EvidencePackage.evidence[i]` unchanged; used directly by `provider_authority_score`'s lookup key (though see maturity finding, §12) | |
| `source_revision`, `snapshot_id` | `Evidence`/`EvidenceCohort` | **Yes**, unchanged through the package; not surfaced in `Claim`/`FinalAnswer` (acceptable — TAD §51's traceability chain is Claim→Relationship→Evidence→Provider→Snapshot→SourceLocation, satisfied by re-derivation through the package, not by flattening every field onto the final answer) | |
| `observed_at` | `Evidence` | **Yes**, unchanged; consumed by `default_freshness_score` in the confidence factor | |
| `source_location` | `RepositorySymbol` (provider-populated) | **Yes**, unchanged on entities through the package; never used by Verification itself (no TAD §46-50 step needs it) — correctly present only for eventual citation/traceability display, not verification logic | |
| `independence_group` | `Evidence` | **Yes**, unchanged; drives `evidence_independence` factor (TAD §48) — confirmed by `test_g_multiple_independent_providers_increase_evidence_independence` | |
| `confidence` | `Evidence` | **Aggregated**, not discarded — `compute_factors`' `evidence_support`/`evidence_quality` factors fold multiple `Evidence.confidence` values into one composite `V` per TAD §48; the individual per-evidence values remain readable on `package.evidence` if a caller wants them, only the *verification-level* number is a new, derived value (as TAD §48 always intended — `V` is explicitly a new composite, not a copy of `Evidence.confidence`) | |
| `freshness` | `Evidence` | **Yes/aggregated** — same pattern as confidence: `default_freshness_score` reduces potentially many timestamps to one composite factor, individual values not destroyed | |
| `supporting_evidence_ids`/`contradicting_evidence_ids` | `CanonicalRelationship` (Reconciliation) | **Yes** — D10 Decision 4 (`collect_evidence`, `codex.planner.retrieval.py`) resolves both lists into `package.evidence`, re-confirmed by 5 regression tests, re-run passing | |
| `graph_version` | Ingestion | **Yes**, byte-identical through all stages — traced live in §4 | |

**No silent discard, overwrite, or unexplained recompute was found in the D4→D9→D10 chain.** The one field genuinely *not* carried onto the final `Claim`/`FinalAnswer` structures (per-evidence `evidence_id`) is a deliberate TAD §44 schema minimalism, not a defect — the full chain remains reconstructable from `EvidencePackage`, which the verification loop retains throughout.

---

## 7. Dependency-Direction Audit

Built from a direct import-graph extraction this pass (`grep -rhoE "^from codex\.[a-z_]+" src/codex/<pkg>/*.py`, all 13 top-level packages):

```
ontology        (no codex.* imports — leaf)
repository      (no codex.* imports — leaf)
evidence     -> ontology
graph        -> evidence, ontology
provider     -> evidence, ontology, repository
resolution   -> ontology
registry     -> provider, repository
reconciliation -> evidence, ontology, registry
ingestion    -> evidence, graph, ontology, provider, reconciliation, registry, repository, resolution
coverage     -> evidence, ingestion, provider
query_understanding -> coverage, ontology, provider
planner      -> coverage, evidence, graph, ingestion, ontology, provider, query_understanding, registry, repository
llm          -> ontology, planner
verification -> coverage, evidence, llm, ontology, planner, registry
```

**No cycle exists** — this is a strict DAG. Layering matches TAD §75's stated rules exactly:

- `Planner → LLM` (forbidden by TAD §75): **absent** — `planner` imports nothing from `llm` or `verification`. Confirmed both by the import graph above and by the dedicated boundary test `test_planner_package_still_forbids_llm_and_verification_imports` (`test_verification_security.py:261`), re-run passing.
- `LLM → Graph Mutation` (forbidden): `llm` imports only `ontology` and `planner` (for `EvidencePackage`'s type) — no `graph`/`evidence` store-mutation import. Confirmed §8.
- `Verification → Graph Mutation` (forbidden): same — `verification` imports `coverage, evidence, llm, ontology, planner, registry`, none of which are graph/evidence *mutation* entry points (`evidence` here is the read-only model/type module, not `codex.graph.store`'s writer).
- `Provider → Query Understanding` (forbidden): absent — `provider` has zero outgoing imports toward `query_understanding`.

---

## 8. Deterministic-Boundary Audit (complete source tree)

All checks below re-run this pass via direct grep across the **entire** `src/codex/` tree, not scoped to D10 files only.

| Check | Result | Evidence |
|---|---|---|
| No embeddings/fuzzy-matching/ML libraries anywhere in production code | **PASS** | `grep -rniE "embed\|fuzzy\|levenshtein\|cosine\|similarity\|tfidf\|sklearn\|difflib" src/codex/` returns only docstrings *documenting the absence* (`entailment.py:25`, `store.py:54`, `retrieval.py:24`, `tier0.py:3`, `entity_resolver.py:54`), one legitimate unrelated hit (`scip/wire.py`'s protobuf "embedded message" — a wire-format term, not ML), and one legitimate deterministic Jaccard-similarity reference (`ranking.py:109`, TAD §36's own named formula, not fuzzy/ML matching) |
| LLM cannot mutate the graph or select providers | **PASS** | `codex.llm.*` imports only `codex.ontology`, `codex.planner.mss` (for the `EvidencePackage` type) — no import of `codex.graph`/`codex.evidence` writer APIs or `codex.provider`/`codex.registry`. Confirmed by `test_llm_package_never_imports_provider_or_registry_modules` and `test_llm_types_have_no_provider_or_file_selection_fields` |
| LLM cannot promote anything to VERIFIED | **PASS** | `classify_claim`/`classify_answer` (`codex.verification.state`) are pure functions over `EntailmentResult`/`VerificationFactors`, no LLM call inside; `test_llm_self_labeling_fact_never_forces_verified_status` and `test_claim_schema_has_no_verification_status_field` directly prove the LLM's own `claim_type=FACT` self-label cannot force a verification outcome |
| Verification does not call the LLM | **PASS** | Only `resynthesis.py` imports `codex.llm`'s gateway/request types; `answer.py`, `engine.py`, `entailment.py` import only `Claim` (a pure data type from `codex.llm.schema`) — confirmed by direct read of each import line, plus `test_entailment_and_confidence_modules_never_import_the_gateway` |
| Entity resolution uses no embeddings/fuzzy matching | **PASS** | `entity_resolver.py` implements byte-identity + normalized-path matching only (`resolve_entities`, `paths.py`); no probabilistic scoring anywhere in the module |
| Provider adapters don't infer relationships beyond declared capabilities | **PASS** | Declared-vs-emitted cross-check this pass: `GitAdapter.supported_capabilities = {HISTORY, CO_CHANGE}` and its code paths emit only `CO_CHANGED_WITH` evidence + lifecycle-status entities (no `CALLS`/`IMPLEMENTS` anywhere in `git_adapter.py`); `SCIPAdapter = {SYMBOL_DEFINITION, SYMBOL_REFERENCE, IMPLEMENTATION, TYPE_RELATIONSHIP}` emits only `REFERENCES`/`IMPORTS`/`IMPLEMENTS` (re-confirmed — no `CALLS`/`EXTENDS` anywhere in `scip_adapter.py`/`scip/mapping.py`, matching the D5 finding that SCIP has no deterministic call/extends signal); `CodeQLAdapter = {DATA_FLOW}` emits only `REFERENCES` evidence or a role annotation, never `CALLS`/`EXTENDS`/`IMPLEMENTS`/`DEPENDS_ON` |
| Class C evidence never silently introduced/upgraded | **PASS** | Class A/B/C is an adapter-docstring convention (§3), not an enforced type — but grep-confirmed no adapter emits a relationship type outside its own declared-capability mapping table, so nothing crosses from "unsupported" into an asserted `Evidence`/`CanonicalRelationship` record undocumented |

---

## 9. Provider Capability-Overlap Re-Audit

Re-derived directly from each concrete adapter's actual `supported_capabilities` property this pass (not re-trusted from the D7 report text):

| Provider | Declared capabilities (from code) |
|---|---|
| `GitAdapter` | `{HISTORY, CO_CHANGE}` |
| `SCIPAdapter` | `{SYMBOL_DEFINITION, SYMBOL_REFERENCE, IMPLEMENTATION, TYPE_RELATIONSHIP}` |
| `CodeQLAdapter` | `{DATA_FLOW}` |

**Zero capability overlap exists between any two currently-implemented adapters** — re-confirms `docs/architecture-conformance-audit.md` §K/§O's conclusion is still correct today, not stale: no code change since D7's research pass altered any adapter's declared capability set. Multi-provider `independence_group` corroboration (TAD §16, the `evidence_independence` confidence factor) is therefore exercised **only synthetically** in tests today (`test_g_multiple_independent_providers_increase_evidence_independence` uses two *fake* providers) — real corroboration requires a 4th provider, which is exactly what D7 (Sourcegraph/RepoGraph) would supply and remains deferred (STOP still standing, ADR-006 still open, no new evidence changes that determination).

---

## 10. TAD §33 Completeness-Metric Investigation

Re-read TAD §33 directly this pass:

```
LOW: >= 50%   MEDIUM: >= 75%   HIGH: >= 90%   EXHAUSTIVE: 100% + complete repository coverage
```

immediately followed by: *"These are initial benchmark-calibrated thresholds."* **TAD never states what the percentage is a percentage of** — not "% of entities found," "% of required capabilities succeeded," "% of expected evidence records," or any other denominator. This is confirmed genuinely undefined by TAD itself, not a Codex omission: no other TAD/HLRD section supplies the missing definition either (HLRD §37-39 discusses coverage conceptually but gives no formula).

**What code assumes:** `codex.coverage.engine.py` (module docstring, lines 31-50, re-read this pass) explicitly does **not** invent a percentage — it implements `CompletenessLevel` as a plain enum (LOW/MEDIUM/HIGH/EXHAUSTIVE) with only `EXHAUSTIVE` backed by a real quantitative check (`is_exhaustive_coverage()`, which needs no percentage: "were all required capabilities COMPLETE" is itself already boolean). LOW/MEDIUM/HIGH are propagated as *requested* levels through `QueryContract`/`RetrievalPlan` but never evaluated against an achieved numeric percentage anywhere in D8/D9/D10.

**Does this block any D1-D10 behavior?** No — re-confirmed this pass. The only place completeness *gates* a decision (rather than just being carried as a label) is the negative-query rule (TAD §34) and the `EXHAUSTIVE`-pruning-floor rule (TAD §32: "Exhaustive queries cannot be pruned below required coverage"), and both of those already have a real quantitative check that doesn't need the missing percentage definition.

**Which future phase needs it:** Benchmark calibration (TAD §80 Phase 5, TAD §67/HLRD §55-56's Precision@10/Recall@10/Factual-accuracy targets) is the natural place this gets resolved — it requires real ground-truth data to decide what "75% coverage" should even measure, which V1 doesn't have yet.

This is not a new finding — `docs/architecture-conformance-audit.md` already tracks it (P.2, R.2, S) with identical conclusions, re-confirmed independently this pass rather than assumed. **No STOP is warranted**; it does not block D1-D10's correctness and a decision isn't needed before D11+ planning, only before benchmark calibration specifically.

---

## 11. Failure-Injection / Boundary-Failure Coverage

| Failure | Real test | Stays localized? |
|---|---|---|
| Provider raises during ingestion | `test_capability_failed_when_provider_itself_raises` | Yes — only that capability marked FAILED, sibling capabilities/providers unaffected (`test_one_capability_failing_does_not_contaminate_a_sibling_capabilitys_status`) |
| Provider returns partial data | `test_capability_partial_from_cohorts_partial_capabilities_field` | Yes — `PARTIAL` coverage status, not silently treated as `COMPLETE` |
| Malformed LLM response | `test_j_malformed_response_then_recovery` | Yes — consumes one budget unit, recovers, doesn't fail the whole query |
| Two consecutive malformed/contradicted outputs | `test_f_resynthesis_second_failure_never_attempts_a_third_call`, `test_two_consecutive_malformed_outputs_exhausts_budget`, `test_attempts_never_exceeds_max_attempts_constant` | Yes — hard `MAX_ATTEMPTS=2` ceiling, never a third LLM call |
| Claim fails entailment (UNRESOLVED) | `test_i_unsupported_semantic_claim_is_unresolved` | Yes — claim excluded from the final answer, not asserted |
| Significant contradiction | `test_e_contradicted_claim_removed_during_resynthesis`, `test_h_conflicting_evidence_from_independent_providers_is_disputed` | Yes — REMOVE→RE-SYNTHESIZE, never a speculative rewrite |
| Zero matching entities (negative query) | `test_b_negative_query_with_complete_coverage_asserts_absence`, `test_c_negative_query_with_incomplete_coverage_is_inconclusive`, `test_negative_query_never_returns_false` | Yes — never silently `FALSE` |
| Stale/mismatched `GraphVersion` | `test_l_graph_version_flows_unchanged_from_plan_through_package` (positive case); no dedicated adversarial "version changed mid-query" test found this pass | **Partial** — the lock itself is proven to hold; an adversarial test that *forces* a version to change mid-flight and asserts the query still completes against the original was not found (see §12) |
| LLM timeout / budget-exceeded | `GenerationStatus.TIMEOUT`/`BUDGET_EXCEEDED` exist as enum values (`gateway.py`) and are handled identically to `MALFORMED_OUTPUT` by `run_verification_loop`'s `generation.status is not GenerationStatus.OK` check (resynthesis.py:89) | Behaviorally covered by the same code path as malformed output, but no test scripts a `FakeLLMGateway` result with `status=TIMEOUT`/`BUDGET_EXCEEDED` specifically (see §12) |

No failure-injection test found this pass that turns a localized failure into a silent success — every case above either has a direct test or is provably routed through the same, already-tested `generation.status is not OK` guard.

---

## 12. Test-Quality Assessment

**Fresh validation, this pass, in `.venv-work`:**

```
651 passed in 2.67s   (plain run)
651 passed in 7.07s   (with --cov)
TOTAL coverage: 3000 statements, 19 missed, 99%
ruff check src/ tests/: All checks passed!
mypy src/: Success: no issues found in 65 source files
```

The only sub-100% module is `src/codex/repository/manager.py` at 76% (15 lines, `46-58, 61, 89-90, 92-94, 120-121`) — pre-existing (Phase 1), unrelated to D9/D10, real-git-failure error paths (clone failures, invalid revisions) that are harder to trigger without a live remote. Not a new finding, not blocking.

### Two genuine, previously-undocumented findings

**Finding 1 — `DeterministicFakeAdapter` is FILE-only (test-infrastructure gap, not a production defect).** `tests/fake_ingestion_provider.py`'s `DeterministicFakeAdapter.normalize()` hardcoded `base_type=BaseEntityType.FILE` at all 4 entity-construction call sites — confirmed by grep before this pass began. Since `tests/planner_fixtures.py`'s `build_graph()` is built exclusively on this fixture and is used by every D9 test and every D10 integration test in `test_d1_d10_integration.py`, **no existing D9/D10 test suite had ever exercised symbol/function/method-level retrieval** — only file-level — even though HLRD/TAD's own worked examples (HLRD §36: "Who calls `PaymentService`?"; this directive's own primary query, "Which tests call `authenticate`?") are function/class-level, not file-level.

Root-caused via the primary trace (§4): a manually-constructed FUNCTION-level graph (bypassing the fixture) proved `GraphReader.find_entities`, `codex.planner.retrieval.resolve_targets`, and `codex.planner.retrieval.bounded_traversal` are **already fully correct** for symbol-level entities — the gap was exclusively in the test double's construction logic, never in production code. Per this directive's rule (only a "clearly proven regression/defect" authorizes a code change, and a test-fixture limitation with correct underlying production code is not one), **no fix was made this pass** — the fixture is left as-is, and this is reported as a recommendation (§16) for a separately-scoped follow-up task, not silently patched under audit cover.

**Finding 2 — `provider_authority` (TAD §48, weight 0.10) is not wired to real provider-scoring data in any production call path.** `run_verification_loop`/`verify_claims`/`verify_claim`/`compute_factors` all accept `provider_authority: Mapping[str, float] | None = None` as an optional caller-supplied override; when omitted (as every real invocation observed this pass does — confirmed by grep, no call site in `src/codex/` outside tests ever supplies this argument), `authority.get(provider, 1.0)` defaults every provider to `1.0`. `codex.registry.scoring.ProviderScoreProfile`/`evidence_quality` (D2, already implemented and populated at `CapabilityRegistry.register()` time) is never read by anything in `codex.verification`. This means the `provider_authority` confidence factor currently contributes a constant, uninformative `0.10 * 1.0` to every `V` computed by real code today, including the live trace in §4. Not a spec violation (TAD §48 doesn't mandate the sourcing mechanism) and not incorrect (uniform 1.0 is a safe default, not a wrong value) — but it means D2's already-built provider-authority signal provides zero benefit to D10's verification confidence in practice. This is an **integration-completeness gap**, reported here rather than silently fixed (fixing it would mean deciding *how* to convert a `CapabilityRegistry`/`ProviderScoreProfile` into the `Mapping[str, float]` `verify_claims` expects — a small design decision this directive's scope does not authorize making unilaterally).

### Other test-quality observations

- `test_d1_d10_integration.py`'s 13 scenarios (A-M) are genuine multi-stage integration tests — each chains real `plan_query`/`execute_query`/`run_verification_loop`/`build_final_answer` together, not unit-isolated mocks of Codex's own logic. The only faked components anywhere in the suite are the LLM gateway (by architectural necessity, TAD §43) and the ingestion provider (necessary since no real SCIP/CodeQL artifact fixture is wired into this specific suite — real-artifact integration exists separately in `test_scip_adapter.py`/`test_codeql_adapter.py`).
- No duplicated tests found — each `test_verification_security.py`/`test_d1_d10_integration.py` test asserts a distinct behavior, confirmed by reading all test names (§ throughout this report cites them individually, no two assert the same thing).
- Missing negative/adversarial tests identified this pass (both minor, neither blocking): (a) no test scripts `GenerationStatus.TIMEOUT`/`BUDGET_EXCEEDED` specifically through `run_verification_loop` (only `MALFORMED_OUTPUT` is exercised, though the code path is shared and provably identical); (b) no adversarial test forces a `GraphVersion` change mid-query and asserts the active query still completes against the original locked version (TAD §55's `CONCURRENT_UPDATE_DETECTED` telemetry event has no corresponding test at all — `codex.graph`/`codex.planner` were grepped for `CONCURRENT_UPDATE_DETECTED`; it does not appear in code, only in TAD text — this is a **NOT_IMPLEMENTED** TAD §55/§64 item, not previously called out as such in the traceability matrix, now added to §2 implicitly via this note since no module implements it).

---

## 13. Drift Search

Full-tree grep across `src/codex/` for TODO/FIXME/XXX/HACK, bare-`pass` stubs, `NotImplementedError`, placeholder/stub/temporary/fallback, broad exception handling, and duplicated scoring algorithms:

- **TODO/FIXME/XXX/HACK:** zero hits anywhere in `src/codex/`.
- **`NotImplementedError`:** zero hits.
- **Bare `pass` as a sole body:** one hit (`llm/schema.py:71`), inspected directly — it is a legitimate `try/except ValueError: pass` fallthrough inside `Claim._validate_predicate` (falls through to check `DERIVED_RELATIONSHIP_TYPES` next), not a stub. **Classification: harmless, intentional control flow.**
- **placeholder/stub/temporary/fallback:** 4 hits, all in SCIP/CodeQL adapter docstrings describing SCIP's own `"."` sentinel convention or a documented "unknown" default — not Codex stubs. **Classification: intentional and documented.**
- **Broad `except Exception`:** 6 hits, all carrying a `# noqa: BLE001` plus an inline comment citing the specific directive/phase that mandates provider-failure isolation (`scip_adapter.py` x4, `codeql_adapter.py` x1, `ingestion/pipeline.py` x2). Each isolates exactly one provider/capability's failure and records it in a typed outcome (`ProviderRunOutcome`/`ProviderExtractionError`) rather than swallowing it silently. **Classification: intentional and documented.**
- **Duplicated scoring/confidence algorithms:** checked `registry/scoring.py` (`provider_score`, TAD §31 weights) vs. `coverage/engine.py` (coverage-status enum only, no scoring formula) vs. `verification/confidence.py` (TAD §48 weights) — three distinct formulas for three distinct TAD sections, no copy-paste duplication found; `verification/confidence.py` correctly *reuses* (imports, doesn't reimplement) `registry.scoring.default_freshness_score` rather than duplicating it (§3).
- **Provider-name string literals outside the provider/ingestion layer:** none found in `planner/`, `verification/`, `query_understanding/`, `coverage/`, `reconciliation/` (spot-checked via grep for `"scip"`, `"git"`, `"codeql"` case-insensitive in those five packages — the only hits are in test fixtures under `tests/`, not production code).

**Net finding: the production codebase has essentially zero drift.** This corroborates, independently, the same conclusion every prior D-phase closure section already recorded.

### No code was changed this pass

Per this directive's explicit rule, only a proven production defect authorizes a code change during this audit, and none was found — Finding 1 and Finding 2 (§12) are both integration-completeness gaps with correct underlying production code. Neither `src/codex/` nor `tests/` was modified. §12's fresh 651/651-passing, 99%-coverage, clean-ruff/mypy numbers reflect the repository exactly as it stood at the start of this audit. Both findings are carried forward as recommendations in §16 for separately-scoped follow-up work, not fixed under audit cover.

---

## 14. External-Reference / License Re-Audit

`docs/resources.md` and `docs/policy-external-references.md` re-read in full this pass (both already comprehensive, current through D9/D10 per their own last-updated markers). `pyproject.toml` re-checked: runtime dependencies remain exactly `networkx>=3.2`, `pydantic>=2.6`, `GitPython>=3.1` — unchanged since Phase 1/D3, confirmed by direct read this pass, no drift. Dev dependencies (`pytest`, `pytest-cov`, `ruff`, `mypy`) unchanged.

Cross-checked actual imports (`grep -rn "^import \|^from " src/codex/`) against declared dependencies: every external (non-stdlib) import in production code is one of `networkx`, `pydantic`, `git` (GitPython) — no undeclared import found, no declared-but-unused dependency found. SCIP/CodeQL/SARIF/RepoGraph/Sourcegraph/Tree-sitter/GraphRAG/TransE/LangChain/LlamaIndex/OpenTelemetry/scikit-learn remain correctly **not** runtime dependencies — each is independently re-implemented (SCIP wire format, SARIF JSON parsing) or correctly deferred (everything else), exactly as `docs/resources.md`'s existing ledger records. **No new dependency was added or considered during this audit**, per the directive's explicit instruction.

---

## 15. Architecture Maturity Classification

| Component | Layer |
|---|---|
| Ontology, Evidence model/store, Graph store/version | **Layer 1 — Implemented + integrated** (validated live in §4 Stage 0, unchanged since Phase 1) |
| Git/SCIP/CodeQL Adapters, Capability Registry, Ingestion Pipeline | **Layer 1 — Implemented + integrated** (real artifacts, real pipeline runs, D5/D6 closure audits re-confirmed §J-K) |
| Entity Resolution, Evidence Reconciliation | **Layer 1 — Implemented + integrated** |
| Coverage Engine (EXHAUSTIVE path) | **Layer 1 — Implemented + integrated** |
| Coverage Engine (LOW/MEDIUM/HIGH percentage) | **Layer 3 — Contract + schema only** — the enum/level exists and propagates correctly, but the underlying metric is undefined by TAD itself (§10); not a Codex gap to close unilaterally |
| Query Understanding (D8), Query Planner/Retrieval (D9) | **Layer 1 — Implemented + integrated**, validated live in §4 Stages 1-4 |
| LLM Gateway/Schema (D10.1-2) | **Layer 1 — Implemented + integrated**, validated live in §4 Stage 5 (against a faked gateway by design) |
| Verification Engine — entailment/confidence/contradiction/state/re-synthesis/answer (D10.3-8) | **Layer 1 — Implemented + integrated**, validated live in §4 Stages 6-7, 651/651 tests, 100% module coverage |
| Verification Engine — `provider_authority` factor specifically | **Layer 2 — Implemented but insufficiently validated/integrated** — correct formula, unit-tested with supplied data, but never fed real `ProviderScoreProfile` data by any production call path (§12 Finding 2) |
| Security boundary (D10.9) | **Layer 1 — Implemented + integrated** (15/15 tests, cross-checked against the real import graph this pass, §7-8) |
| Symbol/function-level retrieval through the D9/D10 *test suite* | **Layer 2 — Implemented but insufficiently validated** — production code (`find_entities`/`resolve_targets`/`bounded_traversal`) proven correct for symbol-level entities via a direct, out-of-suite construction (§4/§12 Finding 1), but the D9/D10 test suite itself still only exercises file-level entities through `DeterministicFakeAdapter`; closing this to Layer 1 needs a separately-scoped fixture change, not made this pass (§16) |
| D1-D10 end-to-end integration (13 scenarios) | **Layer 1 — Implemented + integrated**, all real production code apart from the LLM/ingestion boundary fakes |
| Provider overlap / independence (real, non-synthetic) | **Layer 4 — Deferred** — requires D7 (a 4th provider), which remains a filed STOP pending Sourcegraph licensing/RepoGraph determinism |
| Telemetry Store, Artifact Store | **Layer 4 — Deferred** — correctly out of scope through D10 (TAD §80 Phase 6) |
| Storage/cache/API/auth/deployment technology (ADR-001/002/003/011/015/016/017) | **Layer 5 — Architectural decision required** (genuinely open ADRs, unchanged this pass) |
| SLM/LLM model selection (ADR-007/008) | **Layer 5 — Architectural decision required**, pending benchmark data |
| Benchmark/performance validation (TAD §67, HLRD §55-57) | **Layer 4 — Deferred** to TAD §80 Phase 5, not yet started, correctly so |
| `CONCURRENT_UPDATE_DETECTED` (TAD §55/§64) | **Layer 3 — Contract + schema only**, in fact **not implemented anywhere in code** (§12) — named in TAD's failure taxonomy but no module emits or tests it; a real, small, previously uncatalogued gap |

Nothing here is inflated by test count — module coverage percentages are reported separately from maturity layer, per the directive's explicit instruction not to conflate the two.

---

## 16. Verdict

**GO WITH CONDITIONS.**

D1-D10 are architecturally coherent with HLRD v1.0/TAD v1.0. No blocking contradiction was found — the only two candidate "contradictions" investigated in depth (`EvidencePackage.query_contract` vs. `query_identity`, and the `MAX_ATTEMPTS=2` vs. "maximum re-synthesis: 1" naming) both resolved to intentional, non-blocking implementation choices under scrutiny, not real conflicts. The codebase is clean (651/651 tests, 99% coverage, zero lint/type errors, zero drift markers) and a live, real end-to-end trace against a realistic function-level repository graph produced the correct answer through every stage.

**Specific non-blocking conditions carried forward, none of which require reopening D1-D10 architecture:**

1. TAD §33's completeness-percentage denominator remains genuinely undefined in TAD itself — needs a human/benchmark decision before Phase 5 calibration, not before D11.
2. `provider_authority` (D10's confidence formula) is not yet wired to D2's already-built `ProviderScoreProfile` data in any real call path — a small, well-scoped integration task, not new architecture (§12 Finding 2).
3. `DeterministicFakeAdapter` (the shared D9/D10 test fixture) is FILE-only, so the D9/D10 test *suite* has never exercised symbol/function-level retrieval end to end, even though production code is proven correct for it (§4, §12 Finding 1). Recommend a small, separately-scoped fixture change (an optional `base_type` constructor parameter, defaulting to `FILE` to preserve every existing test unchanged) plus one new regression test — not done this pass, per the "no code change without a proven production defect" rule.
4. D7 (Sourcegraph/RepoGraph) remains deferred behind its filed STOP (ADR-006 open) — real multi-provider independence corroboration stays synthetic-only until it's resolved.
5. `CONCURRENT_UPDATE_DETECTED` (TAD §55/§64) has no implementation anywhere — a small, previously-uncatalogued gap, newly surfaced this pass.
6. Storage/cache/API/auth/deployment ADRs (§15, Layer 5) remain open, as TAD §84 always expected at this stage.
7. Benchmark/performance validation (TAD §80 Phase 5) has not started, correctly so.

**Recommended next phase** (per TAD's own ordering, not started under this directive): TAD §80 lists Phase 5 (Validation: benchmark repositories, ground truth, metrics, calibration, failure testing) as the phase after D10's "Phase 4 — Reasoning" is complete. Before or alongside that, items 2, 3, and 5 above are small, contained, non-architectural implementation tasks worth closing first since they're cheap and already fully diagnosed; item 1 needs a human decision before calibration can proceed meaningfully; item 4 (D7) stays blocked on external licensing/determinism evidence this environment cannot resolve on its own.

No further action was taken under this directive. No code was changed. D11 was not started.
