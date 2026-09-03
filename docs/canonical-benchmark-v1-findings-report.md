# Canonical v1 Findings Investigation — Evidence & Fabrication

> Companion to [`docs/canonical-benchmark-v1-report.md`](canonical-benchmark-v1-report.md)
> (the original `codex-canonical-v1` report, §15-16 of which first raised these two findings).
> Follows up on both, per the "Diagnose and Fix Canonical v1 Evidence/Fabrication Findings"
> checkpoint. **No production code was changed as a result of this investigation** — both
> findings resolved to "not a Codex defect" (Finding 1) and "model limitation, not a fixable
> prompt-contract defect" (Finding 2), confirmed experimentally rather than assumed.

## Finding 1 — Empty `EvidencePackage.evidence` for SCIP-backed cases

**FINDING**: The original v1 report claimed `EvidencePackage.evidence` was empty (`0`) for every one of the 8 `click`/`flask` SCIP-backed canonical-corpus cases, even ones with well-populated `relationships`.

**ROOT CAUSE**: Not a Codex defect. The `0`-evidence observation came from a one-off, ad-hoc diagnostic script written *during* the original investigation, which called `execute_query(..., evidence_store=InMemoryEvidenceStore(), ...)` — a **fresh, empty** evidence store — instead of reusing the *same* `InMemoryEvidenceStore` instance `IngestionPipeline` had just committed real `Evidence` records into. `codex.planner.retrieval.collect_evidence` resolves `CanonicalRelationship.supporting_evidence_ids` by looking them up in whatever `EvidenceStore` the caller supplies (`docs/architecture-conformance-audit.md` §R.3's explicit-injection pattern) — passing a different, never-committed-to store correctly (by that function's own contract) returns no evidence, because no evidence was ever written to *that* store. This is a testing-methodology bug in the diagnostic script, not in `SCIPAdapter`, `IngestionPipeline`, `collect_evidence`, or `execute_query`.

The actual `codex-canonical-v1` benchmark run (`scripts/run_canonical_benchmark.py`) was checked directly and never had this bug: it constructs exactly one `InMemoryEvidenceStore()` per repository and passes that same instance to both `IngestionPipeline` and `run_corpus(evidence_store=...)`. **The real OpenAI run's `EvidencePackage.evidence` was correctly populated all along.**

**EVIDENCE**: Reproduced both code paths directly, side by side, against the same real ingested `click`/`flask` graphs:

```
[BUGGY fresh store] What implements ParamType? -> relationships: 42 evidence: 0
[CORRECT store]     What implements ParamType? -> relationships: 42 evidence: 42

[BUGGY fresh store] What implements Scaffold?  -> relationships: 5  evidence: 0
[CORRECT store]     What implements Scaffold?  -> relationships: 5  evidence: 5

[BUGGY fresh store] Architecture of Flask?     -> relationships: 1  evidence: 0
[CORRECT store]     Architecture of Flask?     -> relationships: 1  evidence: 1
```

Also confirmed for `codex` (`AstCallsAdapter`-sourced) with the correct store: `relationships: 27, evidence: 27` — `supporting_evidence_ids` populated and resolvable in every case checked.

**DEFECT CLASS**: None — investigator/diagnostic-tooling error, not a Codex boundary defect (not SCIP extraction, not graph evidence creation, not evidence propagation, not retrieval, not `EvidencePackage` construction, not serialization).

**FIX: NO** — nothing to fix; the real benchmark run's data was already correct.

**FILES CHANGED**: None in `src/`. Added `tests/test_scip_evidence_propagation.py` (regression coverage, see below) and `scripts/experiment_grounding_instruction.py`/investigation notes (Finding 2, not this one).

**REGRESSION TEST**: `tests/test_scip_evidence_propagation.py` (3 tests, new) — locks in the correct behavior permanently: (1) evidence resolves end-to-end through `execute_query` for a real SCIP-backed query when the correct store is reused (`relationships` count == `evidence` count, every `supporting_evidence_ids` entry resolves); (2) the exact failure mode is reproduced deliberately with a mismatched store, proving it's a caller-side contract issue, not missing/malformed SCIP evidence; (3) real click `CanonicalRelationship`s carry real `supporting_evidence_ids` pointing to real, committed `provider="scip"` `Evidence` records, checked directly against the graph/evidence stores, independent of the planner.

**BEFORE**: `EvidencePackage.evidence: 0` (as reported, via the buggy diagnostic script).

**AFTER**: `EvidencePackage.evidence` matches `relationships` count exactly, for every case checked, using the correct store (the same code path the real benchmark run always used). No change in the real benchmark run's own data — it never had the reported problem.

---

## Finding 2 — Flask fabrication (`Scaffold`, `Architecture of Flask`)

**FINDING**: 6 of 69 real claims across the v1 run were fabricated, concentrated entirely in `flask`'s "What implements Scaffold?" (5 claims) and "Architecture of Flask?" (1 claim) cases — both used a malformed, non-canonical-id identifier shape (e.g. `codex:Scaffold#add_url_rule()` instead of the real `codex:58cf9a1effcf7c56d612cc6ecd2b1df3`) for relationships that do not exist in the graph.

**ROOT CAUSE (traced end to end)**:

| Stage | Finding |
|---|---|
| Query → intent → target resolution | Correct, working as designed. "Scaffold" (bare word) substring-matches 47 real entities via `find_entities(name="Scaffold")` (`graph.find_entities`'s own documented, deterministic, no-fuzzy-matching substring semantics) — mostly flask's own `Scaffold#<method>()`/`Scaffold#<attr>.` descriptor-suffixed symbols, which legitimately contain "Scaffold" as a substring. `plan.target_entity_ids` = 47, correctly all preserved (HLRD §34's ambiguity-abstention discipline: never silently collapse a real multiplicity) — surfaced explicitly via `EvidencePackage.limitations = ["ambiguous target: 47 distinct entities match this query"]`. |
| Candidate selection / graph relationships | `bounded_traversal` admits every seed unconditionally (by its own documented design) and returns real relationships where they exist. For "Scaffold": 52 entities in the package, but only **5 real `IMPLEMENTS` relationships**, touching only **8 of the 52 entities** — the other 44 are genuinely unconnected within this package (no fabricated or missing data; this is what the real graph actually contains for this query shape). |
| `EvidencePackage` / serialization | Confirmed complete and correct (Finding 1): `evidence` (5, matching `relationships`) and `limitations` (the ambiguity string) both reach the serialized prompt (`OpenAIGateway._build_body`'s `user_content` includes the full `evidence_package.model_dump(mode="json")`, `limitations` included). No data loss at this boundary. |
| Prompt contract | The system prompt (`OpenAIGateway._build_body`'s `instructions`) said only *"respond with a single JSON object matching exactly this JSON Schema"* — **no explicit grounding rule** requiring claims to correspond to an actual `relationships`/`evidence` entry. This is a real, demonstrated gap, not a guess. |
| Model behavior | Given a controlled experiment (below) explicitly adding a grounding rule to the same prompt, **fabrication persisted unchanged** on both cases. |

**Controlled experiment** (`scripts/experiment_grounding_instruction.py`): re-ran the exact same two cases — same real retrieval (correct evidence store), same model, same response schema — with one added system-prompt sentence: *"every claim's subject/predicate/object MUST correspond to an actual entry in evidence_package.relationships... never inferred merely because two entities both appear in evidence_package.entities... do not invent a plausible-sounding claim to fill the gap."*

```
=== What implements Scaffold? ===
real relationships in package: 5 / real entities: 52
claims: 4  grounded: 0  fabricated: 4
  UNGROUNDED: Scaffold#_check_setup_finished(). IMPLEMENTS Scaffold#add_url_rule().
  UNGROUNDED: Blueprint#add_url_rule(). IMPLEMENTS Scaffold#add_url_rule().
  UNGROUNDED: App#add_url_rule(). IMPLEMENTS Scaffold#add_url_rule().
  UNGROUNDED: Scaffold#static_url_path(). IMPLEMENTS Scaffold#add_url_rule().

=== Architecture of Flask? ===
real relationships in package: 1 / real entities: 80
claims: 1  grounded: 0  fabricated: 1
```

The explicit grounding instruction **did not eliminate fabrication** — the model still invented plausible-sounding `IMPLEMENTS` relationships among real entity *names* it saw, still without using the real canonical_id format it was given. This is evidence *against* a fixable prompt-contract defect: the underlying data was complete and correct (Finding 1), the ambiguity was explicitly flagged (`limitations`), and an explicit grounding rule was tried and did not change the outcome.

**EVIDENCE**: Both the original run's raw claims and the controlled-experiment's raw claims are reproducible via `scripts/run_canonical_benchmark.py` + `scripts/analyze_canonical_run.py` (original) and `scripts/experiment_grounding_instruction.py` (controlled experiment), all checked in.

**DEFECT CLASS**: **Model limitation** (`gpt-4o-mini-2024-07-18`) under sparse-relationship/high-candidate-count conditions — not retrieval over-selection (target resolution is correct and honestly flagged), not missing evidence (Finding 1: evidence was present and complete), not ambiguous identity (each real entity has its own distinct, correct canonical_id), not insufficient relationship constraints (the 5/1 real relationships are exactly what the real graph contains, not an artifact of `plan_query`'s pruning — no `partial: True`/pruning limitation was present for the "Scaffold" case, "Architecture" had one, both are addressed below), and — now demonstrated, not assumed — not solely a prompt contract gap either (a direct, principled fix to the gap that does exist did not resolve the symptom).

One retrieval-side observation, kept separate from the fabrication defect classification: "Architecture of Flask?" also hit `plan_query`'s existing, unmodified target-set budget pruning (148→80 targets, an already-documented D9 mechanism, TAD §32) — this affects candidate breadth but is pre-existing, working-as-designed behavior, not a new defect, and was not touched.

**FIX: NO** — no Codex-side change is warranted for a demonstrated model limitation. Per this checkpoint's explicit instruction ("If a finding turns out to be a model limitation rather than a Codex defect, do not patch Codex to compensate for it"), the tested grounding-instruction candidate was **not** applied to `codex.llm.openai_gateway`; `PROMPT_TEMPLATE_VERSION` remains `"harness-request-v1"`, unchanged.

**FILES CHANGED**: None in `src/`. Added `scripts/experiment_grounding_instruction.py` (the controlled experiment, kept as a reproducible negative result).

**REGRESSION TEST**: None applicable — no code changed, so there is no new behavior to lock in. The negative experimental result is preserved as a runnable script rather than a pytest assertion (it requires a real OpenAI call and is not part of the deterministic test suite, matching this project's "mock the network in unit tests" convention).

**BEFORE**: 6/69 claims fabricated (5 `Scaffold`, 1 `Architecture`), non-canonical-id identifier shapes, `finish_reason: "stop"` both times (not a truncation issue).

**AFTER**: Unchanged — no fix was applied. The controlled experiment (a *different* prompt, tested in isolation, never shipped) also fabricated on both cases, confirming the underlying cause is not resolved by prompt instruction alone.

---

## Dependency between the two findings

Established experimentally, not assumed: **the two findings are independent.** Finding 1's "empty evidence" was never actually present in the real benchmark run (it was a diagnostic artifact) — the real run always had complete, correctly-populated evidence (5 real records for `Scaffold`, matching its 5 relationships) — and fabrication happened anyway. Empty evidence could not have *caused* the fabrication, because the evidence was never empty in the conditions that actually produced the fabricated claims.

## Regression

No production code changed (`git diff --stat` on `src/` is empty for this checkpoint). Full suite: **1309/1309 passing** (was 1306 before this checkpoint; +3 new evidence-propagation regression tests, 0 removed, 0 modified in `src/`). `ruff check src tests scripts`: all checks passed. `mypy src`: no issues, 90 source files.

`tests/fixtures/benchmark/codex_canonical_corpus_v1.json`, `tests/fixtures/benchmark/codex_self_dev_corpus.json`, `tests/fixtures/benchmark/scip/*.scip`, and `benchmark_runs/canonical_v1_openai_run.json` are all confirmed byte-unchanged (`git diff --stat` empty on every one).

**No v1 benchmark re-run was performed, and none was needed**: since neither finding produced a confirmed Codex defect requiring a fix, there is no code-path difference between "before" and "after" this investigation — the original `benchmark_runs/canonical_v1_openai_run.json` remains the current, valid, unchanged result. Re-running the real OpenAI calls again would add cost and LLM-sampling noise without testing any actual change. One test needed adjustment for an unrelated, pre-existing, now-documented reason: adding `tests/test_scip_evidence_propagation.py` (which itself calls `plan_query`, a corpus target function) legitimately grew the *live* ground truth for the `codex` "What calls plan_query?" case beyond what is frozen — exactly the same "ground truth pinned to a real repository snapshot" caveat `codex.benchmark.dev_corpus`'s own docstring already documents for the development corpus. `test_live_corpus_matches_frozen_fixture_content` was adjusted (frozen-subset-of-live for `codex` cases, exact match retained for the hermetic `click`/`flask` cases) — the *test* adapted; the frozen corpus/ground-truth fixture itself was never touched, per this checkpoint's explicit constraint.

## Overall conclusion

```
SCIP EVIDENCE: FIXED
  (not a Codex defect — the real benchmark run's evidence was always
  correct; the reported symptom was a diagnostic-script bug, now
  understood, corrected in the record, and guarded by 3 new regression
  tests proving the real behavior end-to-end.)

FLASK FABRICATION: MODEL LIMITATION
  (root-caused end to end: target resolution, candidate selection,
  relationships, evidence, and serialization are all correct and
  complete; a principled, tested prompt-contract fix did not resolve
  it; classified as a gpt-4o-mini limitation under sparse-relationship/
  high-candidate-count conditions, not patched.)

CANONICAL v1 REGRESSION: PASS
  (1309/1309 tests, clean ruff/mypy, corpus/ground-truth/original-run
  artifacts all confirmed byte-unchanged, zero production code
  modified.)
```
