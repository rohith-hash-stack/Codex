# Canonical LLM Benchmark Report — `codex-canonical-v1`

> Companion to [`docs/llm-benchmark-spec.md`](llm-benchmark-spec.md) (development-corpus
> infrastructure and OpenAI Gateway) and [`PROGRESS.md`](../PROGRESS.md).

## 1. Canonical corpus version and exact repositories/SHAs

`corpus_version = "codex-canonical-v1"`, frozen at `tests/fixtures/benchmark/codex_canonical_corpus_v1.json`.

| Repository | Pin | Providers used |
|---|---|---|
| `codex` (this repository, self-hosted) | commit `b01755b1f8bb1f8243360414e1bc736301d399be` | `AstCallsAdapter` (stdlib `ast`), `PyprojectDependencyAdapter` (stdlib `tomllib`) — same pair the development corpus validated |
| `click` (`pallets/click`) | commit `36baa15ff831b939a22bc527cd76ce653ef6f66d` | `SCIPAdapter`, fed from a real, frozen index (`tests/fixtures/benchmark/scip/click_sample.scip`) |
| `flask` (`pallets/flask`) | commit `d318b683471101618febed18996405ad26462110` | `SCIPAdapter`, fed from a real, frozen index (`tests/fixtures/benchmark/scip/flask_sample.scip`) |

Both `.scip` indexes were generated once by actually running `scip-python@0.6.6` (`npx @sourcegraph/scip-python@0.6.6 index .`) against a real shallow clone at the exact pinned commit, then frozen and checked in — the same "generate once, freeze, never regenerate live" precedent `tests/fixtures/scip/codex_resolution_sample.scip` already established in this project. Neither external repository's source tree is vendored into this repository; the frozen `.scip` index is itself hermetic (a self-contained protobuf) and needs no live clone to parse. `codex` remains self-hosted (always present wherever this suite runs, per `tests/test_d7_providers_real_repository.py`'s own precedent).

## 2. Number of queries and category distribution

**13 cases** across 3 repositories, **6 real `Intent` categories**:

| Category | Count | Repositories |
|---|---|---|
| `FIND_CALLERS` | 3 (incl. 1 negative) | codex |
| `FIND_TESTS` | 1 | codex |
| `FIND_DEPENDENCIES` | 1 | codex |
| `FIND_IMPLEMENTATIONS` | 5 (incl. 2 negative) | click, flask |
| `FIND_REFERENCES` | 2 | click, flask |
| `ARCHITECTURE_ANALYSIS` | 1 | flask |

3 of the 13 are deliberate negative/abstention cases (one per repository, a nonexistent symbol). 2 are deliberate high-fan-out/ambiguous cases (`plan_query`, 94 real codex callers; `ParamType`, 24 real click entities substring-matched).

**Explicitly excluded, and why** (pre-existing, already-documented Codex gaps — not routed around):
- `CODE_LOOKUP` — confirmed empirically that plain phrasings ("What is X?") never clear Tier-0's deterministic threshold and no SLM is configured; the same gap `PROGRESS.md` already records.
- `TRACE_EXECUTION` and the `DATA_FLOW`-dependent half of `FIND_IMPACT` — need `Capability.DATA_FLOW` (only `CodeQLAdapter` backs it, which needs a CodeQL CLI/GHAS entitlement unavailable in this environment) — the same gap recorded since the original D7 audit.
- `HISTORY_ANALYSIS`/`CO_CHANGE` for `click`/`flask` — would need `GitAdapter` against live git history this corpus deliberately does not vendor.
- Exhaustive adversarial/near-miss and paraphrase matrices — a small, representative, explicitly bounded first pass, not a claim of completeness.

**A paraphrase finding, not a paraphrase feature**: an initial design tried literal paraphrase pairs ("What calls X?" vs "Who calls X?", "What implements X?" vs "Who implements X?") as separate cases. Every one collided on `query_id` — D8's Tier-0 (`codex.query_understanding.tier0._STRUCTURAL_RULES`) scores every one of these phrasings identically (`_STRUCTURAL_SCORE=0.97`, identical target extraction), producing a byte-identical `QueryContract` regardless of phrasing. This is a genuine, positive finding about D8's determinism (retrieval-plan invariance across superficial rewording), not a gap — documented in `codex.benchmark.canonical_corpus`'s own module docstring rather than forced into artificial duplicate entries.

## 3. Ground-truth methodology

Every `GroundTruthLabel.relevant_entity_ids` is computed mechanically from the real, committed graph's own relationships (`GraphReader.get_relationships()`/`find_entities()`) — never from D9's ranked retrieval output (which would test D9 against itself) and never from an LLM. `find_entities(name=...)` — the same public, deterministic substring-lookup method D9's own target resolution uses — is reused only to determine which real entities a query's target text names (not a parallel/competing mechanism). See `codex.benchmark.canonical_corpus`'s `_direct_callers`/`_direct_test_callers`/`_repository_dependencies`/`_implementers`/`_referencers`/`_architecture_relevant` functions for the exact real graph fact backing each category. Verified independent of the LLM: ground truth was computed and frozen *before* any OpenAI call was made, and was never adjusted after seeing model output (per this checkpoint's explicit instruction).

## 4. Prompt/context versioning

Reused unchanged from the development-corpus infrastructure: `PROMPT_TEMPLATE_VERSION = "harness-request-v1"`, `CONTEXT_CONSTRUCTION_VERSION = "harness-context-v1"` (`codex.benchmark.harness`). Per-case `retrieval_context_version` is D9's own real `GraphVersion.version_id` — for this run: `codex:b01755b1...:ast_calls=stdlib-ast,pyproject_deps=stdlib-tomllib`, `click:36baa15f...:scip=scip-python@0.6.6`, `flask:d318b683...:scip=scip-python@0.6.6`.

## 5. OpenAI model/provider actually served

Requested `model="gpt-4o-mini"` (`OpenAIGateway`'s default). **Served model: `gpt-4o-mini-2024-07-18`** for all 13 cases (recorded from each response, never assumed).

## 6. Overall metrics

**Retrieval (`codex.evaluation.evaluate`, real, deterministic, unmodified):**

| Metric | Value | Sample size |
|---|---|---|
| `PRECISION_AT_10` | 0.385 | 13 |
| `RECALL_AT_10` | 0.559 | 10 (3 negative cases excluded — undefined denominator) |
| `MRR` | 0.391 | 13 |
| `CLAIM_VERIFICATION_ACCURACY` / `ABSTENTION_PRECISION` | `NOT_EVALUABLE` | No D10 Verification Engine wiring in the harness (unchanged, documented gap) |

**Generation:** 13/13 `generation_status: OK` (zero `MALFORMED_OUTPUT` — the previous checkpoint's `max_tokens` fix held across all 13 cases, including a 73,999-token case). No gateway errors, no timeouts, no authentication failures. `finish_reason: "stop"` for every case.

**Claim grounding** (manual verification pass, `scripts/analyze_canonical_run.py` — see §10): **60/69 real claims (87%) grounded** to a genuine graph entity; **56/69 (81%) matched a real graph edge exactly**; **6/69 (9%) fabricated** — all isolated to 2 of 13 cases (see §10, §15).

## 7. Per-category metrics (claim grounding)

| Category | Grounded | Fabricated | Real edge | Total claims |
|---|---|---|---|---|
| `FIND_CALLERS` | 22 | 0 | 22 | 23 |
| `FIND_TESTS` | 8 | 0 | 8 | 8 |
| `FIND_DEPENDENCIES` | 7 | 0 | 7 | 7 |
| `FIND_IMPLEMENTATIONS` | 12 | 5 | 11 | 19 |
| `FIND_REFERENCES` | 11 | 0 | 8 | 11 |
| `ARCHITECTURE_ANALYSIS` | 0 | 1 | 0 | 1 |

`FIND_CALLERS`/`FIND_TESTS`/`FIND_DEPENDENCIES` (all `codex`, `AstCallsAdapter`/`PyprojectDependencyAdapter`-backed): **perfect grounding, zero fabrication**. `FIND_IMPLEMENTATIONS`/`ARCHITECTURE_ANALYSIS` (all fabrication is concentrated here — see §15).

## 8. Per-repository metrics

| Repository | Cases | Claims grounded | Claims fabricated |
|---|---|---|---|
| `codex` | 5 | 37/37 (excl. 1 abstention placeholder) | 0 |
| `click` | 4 | 20/20 (excl. 1 abstention placeholder) | 0 |
| `flask` | 4 | 3/9 (excl. 1 abstention placeholder) | 6 |

**All 6 fabricated claims are in `flask`.** `codex` and `click` — both cases where `EvidencePackage.relationships` was reasonably dense relative to the candidate-entity count — show zero fabrication. `flask`'s two failing cases both had very sparse real relationships (5 and 1 edges) against very large candidate-entity sets (52 and 80) — see §15 for the root-cause hypothesis.

## 9. Token usage

| Case | Tokens |
|---|---|
| codex: build_canonical_id | 18,163 |
| codex: plan_query | 53,629 |
| codex: compute_query_identity tests | 7,556 |
| codex: dependencies | 6,372 |
| codex: negative | 975 |
| click: ParamType | 29,740 |
| click: UsageError | 6,878 |
| click: BadParameter references | 6,075 |
| click: negative | 951 |
| flask: Scaffold | 13,352 |
| flask: Architecture | 15,988 |
| flask: Blueprint references | **73,999** |
| flask: negative | 961 |

**Total: 234,639 tokens** across 13 real calls. High-fan-out cases (`plan_query`, `ParamType`, `Blueprint` references — the latter hit D9's own budget-pruning, 139→80 and 148→80 target truncation) dominate cost; negative cases are cheap (~1,000 tokens).

## 10. Unsupported/fabricated claim analysis

D10's Verification Engine is not wired into the harness (unchanged, documented gap — `docs/llm-benchmark-spec.md` §5), so `UNSUPPORTED_CLAIM_RATE`/`CLAIM_VERIFICATION_ACCURACY` are not automatically computable via `codex.evaluation.evaluate`. `scripts/analyze_canonical_run.py` performs the equivalent check by hand, read-only, against the real ingested graphs: for every claim, resolve `subject`/`object` to a real entity (by canonical_id — the expected format — or by exact `qualified_name`, a format the model sometimes substituted), then check whether the claimed `(subject, predicate, object)` triple is a real graph edge.

**Result: 6 fabricated claims, all in 2 of 13 cases** (`flask`: "What implements Scaffold?" — 5 claims; "Architecture of Flask?" — 1 claim). Every fabricated claim used a qualified-name-shaped string with a stray `codex:` prefix glued on (e.g. `codex:Scaffold#add_url_rule()`) rather than the real canonical_id the entity actually had (`codex:58cf9a1effcf7c56d612cc6ecd2b1df3`) — the model invented an identifier shape, not just a wrong fact. See §15 for root-cause.

The 3 negative-case placeholder claims (e.g. `zzz_nonexistent_codex_symbol_xyz CALLS None`) are **not** counted as fabrication: they are the model's schema-compliant way of representing "no evidence found" and are addressed separately in §11.

## 11. Abstention analysis

**3/3 negative cases correctly abstained.** Every one produced an explanation stating no evidence was found (e.g. *"No evidence was found for any calls to 'zzz_nonexistent_codex_symbol_xyz'"*), with a single placeholder claim (subject = the query's own nonexistent symbol name, object = the literal string `"None"` or JSON `null`) rather than a fabricated relationship. `ABSTENTION_PRECISION` is `NOT_EVALUABLE` via the automated metric (same Verification Engine gap as §10), but this manual read is unambiguous: real abstention behavior, not fabrication, across every negative case in this corpus.

## 12. Multihop/impact/ambiguity/high-fan-out results

- **High-fan-out, `codex`** (`plan_query`, 94 real callers, `FIND_CALLERS`): 5/5 claims grounded, 5/5 matched real edges. The model surfaced a small, correct sample of the 94 real callers (never claimed to enumerate all 94) — appropriate behavior given the token budget, no fabrication.
- **High-fan-out/ambiguous, `click`** (`ParamType`, 24 real substring-matched targets, `FIND_IMPLEMENTATIONS`): 5/5 grounded, 4/5 matched a real edge exactly (one claim used a real entity pair not connected by a literal `IMPLEMENTS` edge in the graph — a near-miss, not a fabrication of a nonexistent entity).
- **High-fan-out + sparse evidence, `flask`** (`Scaffold`, 47 targets/52 entities but only 5 real relationships; `Flask` architecture, 80 targets/80 entities but only 1 real relationship): the two cases where fabrication occurred — see §15.
- **`FIND_IMPACT`/`TRACE_EXECUTION` (multihop/behavioral)**: not included — `DATA_FLOW`-dependent, structurally unsupported in this environment (§2).

No genuine multihop (depth > 1) traversal case was included — every included intent uses `_BASE_DEPTH_BY_INTENT`'s shallow default depth. Flagged as a gap for a future corpus revision, not attempted here.

## 13. Conceptual-query results

`ARCHITECTURE_ANALYSIS` ("Architecture of Flask?") is the one conceptual/higher-level case in this corpus. It is also the case with the fewest real relationships (1) relative to candidate entities (80) and the one 100%-fabricated case (1/1 claims). No conclusion about conceptual-query quality in general can be drawn from a single case — flagged as needing more coverage in a future revision, not claimed as representative here.

## 14. Paraphrase/adversarial results

See §2's paraphrase finding: D8's Tier-0 treats "who calls X"/"what calls X" (and the `IMPLEMENTS` equivalents) as byte-identical `QueryContract`s, so a literal paraphrase A/B test at the retrieval-plan level is moot for these phrasings — real paraphrase diversity in this corpus instead comes from testing the same two intent families (`FIND_CALLERS`, `FIND_IMPLEMENTATIONS`) against different real targets/repositories. No dedicated adversarial/near-miss cases were included in this pass (§2) — an explicit, bounded scope decision, not an oversight.

## 15. Failure taxonomy

**One genuine, reproducible finding, two related symptoms:**

1. **Evidence defect (systemic, all SCIP-backed queries)**: `EvidencePackage.evidence` is `0` for every one of the 8 `click`/`flask` cases in this run, including cases that were otherwise perfectly grounded (`ParamType`: 42 real `relationships` but `0 evidence`). `EvidencePackage.relationships` *is* correctly populated from `SCIPAdapter`'s real `IMPLEMENTS`/`REFERENCES` data. This means the model is grounding almost entirely from `relationships`/`entities`, not from the raw `evidence` provenance records D10 was designed around (TAD §42/§44's "Evidence" concept) — worth a focused investigation of D9's evidence-selection step for SCIP-sourced relationships, **not investigated further or fixed in this checkpoint** (would require reading `codex.planner.mss`/`execute_query` in depth — out of this milestone's "diagnose, classify, stop" scope). **Classification: evidence defect** (candidate) — not confirmed root-caused, flagged for a dedicated follow-up.

2. **Fabrication under sparse-relationship, high-candidate-count conditions** (`flask` `Scaffold`/`Architecture` cases only): when the `EvidencePackage` contains a large candidate-entity list (52-80) but very few real relationships among them (5, then 1), the model did not reliably ground its claims in the few real edges present — instead inventing plausible-sounding `IMPLEMENTS` relationships between entity names it saw, using a malformed identifier shape (`codex:Scaffold#add_url_rule()`, not a real canonical_id) rather than any of the 52-80 real canonical_ids actually supplied. **Classification: prompt/context defect, bordering on a model limitation** — the current prompt does not clearly signal "most of these entities have no known relationship to each other; only these N edges are real," inviting confabulation when that ratio is very low. **Not fixed in this checkpoint** (explicit instruction: document, classify, stop — do not modify gateway/prompt behavior to chase a benchmark score). A concrete, minimal reproduction: `scripts/run_canonical_benchmark.py`'s flask cases + `scripts/analyze_canonical_run.py`'s cross-reference, both checked in and rerunnable.

**Not a defect — a real strength, confirmed**: zero fabrication anywhere `EvidencePackage.relationships` density was reasonable (`codex`, `click`), and clean, appropriate abstention on all 3 negative cases.

## 16. Discovered Codex defects

One candidate defect surfaced (§15 item 1: `EvidencePackage.evidence` empty for all SCIP-sourced queries) — reported here per this checkpoint's explicit instruction to stop and report rather than silently patch. **No fix proposed or applied.** This needs a human decision on priority/scope before any code changes: is `evidence`'s emptiness itself the defect (D9 not carrying SCIP's real Evidence records through to the MSS), or is `relationships`-only grounding an acceptable, already-intended fallback the prompt should simply lean on more explicitly? Both are legitimate framings; this report does not resolve which.

## 17. Regression results

Full test suite before this checkpoint: 1297/1297. After (13 new development-corpus-mirroring tests for `codex.benchmark.canonical_corpus`, zero changes to any existing production module): **1306/1306 passing.** The `codex-self-dev-v0` corpus (`tests/fixtures/benchmark/codex_self_dev_corpus.json`) is confirmed byte-untouched (`git diff` empty). No graph, ingestion, identity resolution, retrieval algorithm, query-understanding rule, planner semantics, or gateway behavior was modified anywhere in this checkpoint.

## 18. Tests/ruff/mypy results

- **pytest**: 1306/1306 passing (13 new: `tests/test_benchmark_canonical_corpus.py`).
- **ruff** (`ruff check src tests scripts`): all checks passed.
- **mypy** (`mypy src`, matching CI's own gate): no issues found, 90 source files.

## 19. Exact reproducibility artifacts

- Corpus: `tests/fixtures/benchmark/codex_canonical_corpus_v1.json` (`corpus_version="codex-canonical-v1"`, byte-stable across regenerations — verified).
- SCIP fixtures: `tests/fixtures/benchmark/scip/{click,flask}_sample.scip`.
- Corpus builder: `src/codex/benchmark/canonical_corpus.py`; freeze script: `scripts/build_canonical_corpus.py`.
- Run script: `scripts/run_canonical_benchmark.py`; analysis script: `scripts/analyze_canonical_run.py`.
- Full run artifact (every `CaseRunResult`, raw output included, no secrets): `benchmark_runs/canonical_v1_openai_run.json`.
- `run_id`s (one per repository, since `repository_revision` differs): computed from `(corpus_version="codex-canonical-v1", model_id="gpt-4o-mini", provider="openai", prompt_template_version="harness-request-v1", context_construction_version="harness-context-v1", repository_revision=<per-repo pin>)` — see the artifact's `records_by_repository[*].run_id`.

## 20. Final gate

```
CANONICAL CORPUS: PASS — 13 cases, 3 real repositories, 6 real query categories,
  deterministic construction verified (byte-identical across independent
  ingestions and against the frozen fixture).
GROUND TRUTH: PASS — every label mechanically derived from real graph facts,
  verified independent of the LLM, never adjusted after seeing model output.
BENCHMARK REPRODUCIBILITY: PASS — corpus/repository/prompt/context/provider/
  served-model/run-id/raw-output/token-usage all pinned and recorded;
  frozen corpus confirmed byte-stable across rebuilds.
OPENAI MODEL VALIDATION: PASS — 13/13 real calls completed (generation_status
  OK, finish_reason "stop"), no gateway errors, no fallback, served model
  gpt-4o-mini-2024-07-18 recorded for every case.
CODEX LLM GROUNDING: PARTIAL — 87% of real claims grounded to genuine graph
  entities, 81% matched a real graph edge exactly, zero fabrication across
  8 of 13 cases (all codex/click cases); 6 fabricated claims concentrated in
  2 of 13 cases (flask, both under sparse-relationship/high-candidate-count
  conditions) with a documented, unconfirmed candidate evidence defect and
  a documented prompt/context weakness — neither investigated to root cause
  nor fixed in this checkpoint.
CANONICAL LLM BENCHMARK: ESTABLISHED (v1, first pass) — explicitly not
  claimed complete: query-category coverage, repository diversity, and
  claim-verification automation all have documented, bounded gaps (§2, §10,
  §16) that a future revision should close before this corpus is treated as
  exhaustive.
```

Historical note (per this checkpoint's explicit instruction): the previously-discussed "50 queries × 5 repositories, Claude Sonnet" result remains historical conversation-level evidence only — it is not, and is not treated here as, a reproducible baseline this corpus's results are compared against. No model-parity claim is made against it.
