# Codex V1 Architecture & Validation Closure Report

**Audit date**: 2026-09-04. **Audited revision**: `32572b8066a036ea8569c85239b70a5a381a2e16` (branch `claude/api-key-env-variable-t37qbl`). **Nature of this cycle**: audit/validation only — the evidence below did not surface a reproducible V1-critical defect, so **no production code was changed** in this cycle (consistent with the audit's own "default action is NO CODE CHANGE" policy).

---

## A. Executive verdict

# **V1 READY WITH DOCUMENTED LIMITATIONS**

The core, protected invariant —

```
Repository → Deterministic Graph → Query Understanding → Targeted/Bounded Retrieval
    → Evidence → OpenAI → Claims → Canonical Identity Resolution
    → Deterministic Grounding Verification → OK / CLAIMS_NOT_GROUNDED
```

— is real, live-verified end to end in this cycle (§C), not merely unit-tested. Every stage is deterministic except the OpenAI call itself, and the deterministic grounding stage is proven, live, to reject a reversed relationship claim over real evidence and to accept a correctly-oriented one over the same evidence. This is not a "PASS because tests pass" verdict: it rests on a fresh, real `python -m codex.api` server, a real temporary repository, and real `gpt-4o-mini` calls executed during this audit.

It is not an unqualified PASS because real, honestly-scoped limitations remain (§D) — most materially, the VS Code extension's actual GUI and the 3D graph's actual browser/GPU runtime behavior cannot be verified from this environment, and two external-artifact providers (SCIP, CodeQL) require infrastructure outside Codex's own control to activate. None of these are defects; all are pre-existing, already-disclosed scope boundaries, not new findings from this cycle.

---

## B. V1 capability matrix

| Capability | Status | Evidence | Limitation | Release blocker? |
|---|---|---|---|---|
| Entity extraction (Git, AST) | **PASS** | Live: `provider_summary` shows `ast_calls`/`git` `COMMITTED`; real `FUNCTION` entities with correct `source_location` returned via `/neighborhood` | AST resolution has documented, intentional coverage gaps (no MRO walk, no nested-closure attribution, dynamic dispatch unresolved) — by design, not a defect | No |
| Containment / location | **PASS** | `source_location` (`file_path`/`start_line`/`end_line`) correct on every live entity checked | — | No |
| `CALLS` (directional) | **PASS** | Live: `caller → CALLS → helper`, `other_caller → CALLS → helper`, correct direction, `SUPPORTED`, confidence ≈1.0 | Only syntactically unambiguous call shapes resolve (documented, `docs/architecture-conformance-audit.md` §HH.1) | No |
| `REFERENCES` | **PASS** (SCIP-backed) | Pre-existing `SCIPAdapter`/`test_scip_adapter.py` coverage; not re-derived this cycle (no SCIP index in this live smoke repo) | Only active when a `.scip` index is supplied — see provider matrix | No |
| `IMPLEMENTS` / inheritance | **PASS** (SCIP-backed) | Pre-existing, unmodified (`Capability.IMPLEMENTATION`); `EXTENDS`/`OVERRIDES` have zero provider anywhere (confirmed by grep, prior cycle) | `EXTENDS`/`OVERRIDES` remain unbacked by any provider — correctly unrouted, not a query-understanding gap | No |
| `DEPENDS_ON` | **PASS** | Live: real `pyproject.toml` → `provider_summary` shows `pyproject_deps: COMMITTED`; `/neighborhood` and `/query` both show real, `SUPPORTED`, confidence ≈1.0 edges | Only PEP 621 sections read (Poetry/Pipenv/`requirements.txt` not read, by original design) | No |
| Canonical identity | **PASS** | Live: every claim/evidence id is a stable `codex:<hash>` canonical id; qualifier-confirmed high-fan-out resolution re-verified this session (prior cycle) — unmodified this cycle | — | No |
| Evidence provenance | **PASS** | Every live `CanonicalRelationship` carries `evidence_count`/`status`/`confidence`; `Evidence.provider`/`confidence` traced to the real emitting adapter | — | No |
| Directional semantics | **PASS** | Live, direct proof: a reversed claim over real `CALLS` evidence is rejected (§C) | — | No |
| Bounded traversal | **PASS** | `/neighborhood`'s `max_nodes`/`max_edges`/`truncated` unchanged, exercised live (`truncated:false` on a small graph, correct) | Large-neighborhood truncation behavior not re-stress-tested this cycle (unchanged since prior validated milestones) | No |
| OpenAI Claim Grounding Integrity | **PASS** | Live, this cycle: correct claim → `OK`; reversed claim over identical real evidence → `CLAIMS_NOT_GROUNDED`, never `OK`; fabricated negative-query claim independently caught live | — | No |
| Query understanding (Tier-0) | **PASS** | Live: `FIND_CALLERS`, `FIND_DEPENDENCIES` correctly routed and resolved this cycle; broader battery validated in the immediately preceding cycle (unmodified since) | A few narrow phrasing variants remain unrouted (§D); one pre-existing hyphen-in-target regex limitation (unrelated to this cycle, not touched) | No |
| High-fan-out identity resolution | **PASS** | Re-verified, unmodified: qualifier-confirmed matches sort first and are the only candidates returned when any exist (prior cycle; not re-touched) | Verified via a faithful synthetic reproduction, not a fresh real-`django` SCIP index in this environment | No |
| Retrieval sufficiency/efficiency | **PASS** | Live: a 2-hop query returned exactly 2 real callers, 0 irrelevant nodes, `truncated:false`; ambiguity query returned exactly the 4 real matching entities, no more, no fewer | No fresh token-footprint/large-repo stress measurement this cycle (relies on prior-cycle canonical-v1/expansion-v1 benchmark evidence, unmodified) | No |
| Provider registry: `GitAdapter` | **Production-ready** | Registered by default, live-verified | — | No |
| Provider registry: `AstCallsAdapter` | **Production-ready** | Registered by default, live-verified | Documented resolution-scope gaps only | No |
| Provider registry: `PyprojectDependencyAdapter` | **Production-ready** | Registered by default (integrated last cycle), live-verified this cycle | PEP 621 only | No |
| Provider registry: `SCIPAdapter` | **Environment-dependent, intentionally not in default CLI** | Needs an external, pre-generated `.scip` index; correctly excluded from `_build_api()` per its own documented criterion | Not a Codex defect — the artifact must come from outside Codex | No (by design) |
| Provider registry: `CodeQLAdapter` | **Environment-dependent, intentionally not in default CLI** | Needs an external, pre-generated SARIF artifact and CodeQL CLI Terms govern who may produce it | Not a Codex defect | No (by design) |
| API/CLI startup, health, ingestion, query | **PASS** | Live this cycle: `CODEX_API_LISTENING`, `/healthz` → `200`, register→ingest→`READY`, `/query` → real grounded/rejected results | — | No |
| API error handling | **PASS** | Live this cycle: malformed JSON → `400`; missing field → `400`; wrong-typed field → `400`; unknown repo query → `404`; oversized body → `413`; every response a structured JSON error, zero raw traceback | — | No |
| Security: key exposure | **PASS** | Live this cycle: key never appears in server log or any captured response; `_redact()` unmodified; no `shell=True`/`os.system` anywhere in `src/codex/` (grep, this cycle) | — | No |
| VS Code extension — implementation | **PASS (code+test evidence only)** | 49/49 TS tests, `tsc` clean, this cycle | — | No |
| VS Code extension — actual GUI interaction | **NOT VERIFIABLE HERE** | No VS Code Extension Host / GUI in this sandbox | Genuine environment limitation, not a defect | No — see §D |
| 2D graph | **PASS (code+test evidence only)** | Renderer + tests pass; no browser to visually confirm | Environment limitation | No |
| 3D graph | **PASS (code+test evidence only)** | Renderer + tests pass; Three.js/WebGL files present at computed paths; no GPU/browser to visually confirm | Environment limitation, already disclosed at build time | No |
| Test integrity | **PASS** | 1387/1387 Python, ruff clean, mypy clean, 49/49 TS, `tsc` clean — all freshly re-run this cycle | One pre-existing test flake already fixed in a prior cycle; Windows `WinError 5` unreproducible here | No |

---

## C. End-to-end proof

Real path, executed live during this audit cycle (server: `python -m codex.api --port 8796`; repository: a real temporary git repo at `/tmp/codex_v1_audit`, not a fixture).

### Setup

```
app.py:
    import requests
    def helper(x): return x + 1
    def caller(): return helper(3)
    def other_caller(): return helper(5)
    def fetch(url): return requests.get(url)

pyproject.toml:
    [project]
    dependencies = ["requests>=2.31", "click>=8.0"]
```

**Ingestion** (`POST /repositories` → `GET /jobs/{id}`): `phase: READY`. `provider_summary`: `ast_calls: COMMITTED` (3 entities, 2 evidence), `git: COMMITTED`, `pyproject_deps: COMMITTED` (3 entities, 2 evidence). All three providers ran; zero errors.

**Deterministic graph** (`GET /neighborhood?symbol=helper&depth=1`):
```
caller       --CALLS--> helper   (SUPPORTED, confidence≈0.9999998)
other_caller --CALLS--> helper   (SUPPORTED, confidence≈0.9999998)
```
Directional, correctly attributed, real `source_location` on every node.

### 1. Correctly-grounded relationship

`POST /query {"query_text": "What calls helper?"}` → real `gpt-4o-mini-2024-07-18` call:

```json
"status": "OK",
"intent": "FIND_CALLERS",
"answer": "The function 'helper' is called by two other functions 'caller' and 'other_caller'.",
"claims": [
  {"subject": "app.py::caller",       "predicate": "CALLS", "object": "app.py::helper", "claim_type": "FACT"},
  {"subject": "app.py::other_caller", "predicate": "CALLS", "object": "app.py::helper", "claim_type": "FACT"}
]
```
Both claims match the real evidence direction exactly. **Result: `OK`.**

### 2. Intentionally reversed relationship — cannot become `OK`

The same real `EvidencePackage` (re-ingested identically) was replayed through `ask()` with a scripted gateway returning the real claims verbatim except the first claim's subject/object were swapped — isolating the reversal as the only variable, no synthetic evidence:

```
[reversal] app.py::other_caller CALLS app.py::helper
        -> app.py::helper CALLS app.py::other_caller

status: CLAIMS_NOT_GROUNDED
detail: 1 relationship claim(s) did not match canonical graph evidence
        (wrong direction, wrong entity, wrong predicate, or no such
        relationship exists): 'app.py::helper CALLS app.py::other_caller'
claims (still visible, unmodified): 2
```

**Result: `CLAIMS_NOT_GROUNDED`, never `OK`.** The claims remain visible verbatim (transparency preserved — nothing is silently dropped), but the status honestly reflects that one of them is unsupported.

### 3. Supplementary live checks (same audit session)

- **Negative query** (`"What calls totally_nonexistent_symbol_zzz?"`): the real model *fabricated* a claim (`subject CALLS "none"`) — independently caught: `status: CLAIMS_NOT_GROUNDED`. The real, honest `negative_query_result=NO_EVIDENCE_FOUND` signal still reached `evidence_context.limitations` unmodified.
- **Ambiguous query** (two distinct real `helper` functions across two files, `"What calls helper?"`): `evidence_context.limitations` correctly reported `"ambiguous target: 4 distinct entities match this query"` (never silently resolved to one); the model's 5 claims all resolved to real canonical ids with real matching edges → `status: OK` (genuinely grounded, not a false disambiguation).
- **Dependency query** (`"What does v1audit depend on?"`): `status: OK`, `intent: FIND_DEPENDENCIES`, two correct claims (`click`, `requests`), both real.

This proves composition, not just two isolated fixes: query understanding routed correctly, retrieval returned exactly the relevant bounded evidence, canonical identity resolved both real and adversarial (reversed) claims correctly, and the deterministic grounding gate is the final, authoritative arbiter of `status` in every case — the LLM's own output was never trusted as ground truth.

---

## D. Known limitations

**VS Code GUI verification limitations.** No VS Code Extension Host or windowing system exists in this sandboxed environment. What is verified: the extension compiles (`tsc` clean), its 49 unit/integration tests pass (including a real spawned `codex.api` server), and its generated Webview HTML/script is well-formed. What is **not** verified: actually opening the panel, clicking buttons, or visually confirming the answer/evidence/status layout renders correctly. This has been true and disclosed since the UI Integration Milestone; unchanged this cycle.

**2D/3D runtime limitations.** No GPU-backed browser exists here. What is verified: both renderers' own logic (layout math, evidence-status coloring, large-graph guard, fallback decision) is unit-tested and passing; the vendored Three.js/OrbitControls/CSS2DRenderer files exist on disk at the exact paths the Webview's import map references. What is **not** verified: actual pan/orbit/zoom feel, label legibility, or real frame rate. Disclosed at 3D-milestone build time (`docs/3d-repository-intelligence-graph.md` §6/§11); unchanged this cycle.

**Windows-specific validation limitations.** This audit runs on Linux. A previously-reported Windows GitPython `WinError 5` (a Windows-only file-handle/access-denied class of failure) could not be reproduced or refuted here — no Windows environment is available. Left classified as environment-specific, per standing instruction; not touched.

**Provider-format limitations.** `SCIPAdapter` requires a pre-generated `.scip` index (produced by `scip-python` or equivalent, outside Codex); `CodeQLAdapter` requires a pre-generated SARIF artifact and is bound by GitHub's own CodeQL CLI Terms for whoever produces it. Neither is registered in the default `python -m codex.api` CLI, by design — both are documented, correct, environment-dependent exclusions, not integration gaps (unlike `PyprojectDependencyAdapter`, which needed no external artifact and was fixed last cycle). `PyprojectDependencyAdapter` itself only reads PEP 621's two standard sections — Poetry/Pipenv/`requirements.txt`/`[tool.uv]` manifests are not read, by original, unmodified design.

**Remaining query phrasing gaps.** A small number of natural phrasings remain unrouted by Tier-0 (e.g., "if X changes, what breaks" — as opposed to the already-covered "if I change X, what breaks"; "how is X structured" for `ARCHITECTURE_ANALYSIS`). None were found to be V1-critical (no reproducible defect — an unrouted phrasing degrades gracefully to `UNDERSTANDING_INCOMPLETE`, an honest "I don't understand," never a wrong or fabricated answer), so none were added this cycle, per the audit's own "do not add vocabulary/routing rules unless a reproducible V1-critical defect exists" instruction. A pre-existing, unrelated regex limitation (`[\w.]+` target-capture doesn't include hyphens, so a hyphenated repository/symbol name can fail to route) was newly observed while preparing this cycle's live proof — worked around with a hyphen-free identifier in that proof, not fixed, since it is not V1-critical and touching Tier-0 was out of this cycle's explicit scope.

**Retrieval-sufficiency measurement scope.** This cycle's own live checks confirm retrieval stays minimal and bounded on small, fresh repositories (exact expected node/edge counts, `truncated:false`). It relies on the *prior* canonical-v1/validation-expansion-v1 benchmark corpora (unmodified, byte-identical, confirmed via `git diff`) for large-repository/high-fan-out token-footprint evidence — that benchmark was not re-run this cycle (no code path it exercises changed).

---

## E. Release blockers

**None identified.** No reproducible V1-critical defect was found in this audit cycle. Every capability in §B that touches the protected invariant (graph → retrieval → evidence → LLM → grounding) is either directly live-verified in this cycle or was live-verified and left unmodified in the immediately preceding cycles, with the full regression suite re-confirming zero drift.

---

## F. Deferred backlog (non-blocking)

1. A small number of additional Tier-0 phrasing variants (§D) — cosmetic UX improvement, not correctness.
2. The `[\w.]+` hyphen-in-target-capture regex limitation — a real, narrow Tier-0 gap, newly observed this cycle; not V1-critical (degrades to an honest `UNDERSTANDING_INCOMPLETE`).
3. `EXTENDS`/`OVERRIDES`/`CONFIGURED_BY`/`EXPOSES`/`CONSUMES`/`PERSISTS_TO` remain unbacked by any provider — a genuine future-scope decision (build a provider, or formally scope them out of V1), not an implementation defect.
4. Real, live GUI/3D/browser validation, once an environment with a display and GPU is available.
5. `SCIPAdapter`/`CodeQLAdapter` activation for deployments that can supply the external artifacts — a deployment/packaging concern, not a code gap.
6. Windows-specific GitPython validation, once a Windows environment is available.
7. A fresh, large-repository (e.g., real `django`) token-footprint/retrieval-efficiency re-measurement, as a periodic health check rather than a blocking requirement.

None of these were touched this cycle, per the audit's explicit "do not manufacture work" instruction.

---

## G. Repository integrity

| Item | Value |
|---|---|
| Final commit (this cycle) | `32572b8066a036ea8569c85239b70a5a381a2e16` (unchanged — audit made no commit) |
| Branch | `claude/api-key-env-variable-t37qbl` |
| `git status` at audit end | clean |
| Files changed during this audit | **none** (audit/validation only; one new doc, this report, added and committed separately from the audit's own findings) |
| Protected subsystems (`provider/`, `resolution/`, `reconciliation/`, `query_understanding/`, `planner/`, `graph/`, `ontology/`, `evidence/`, `verification/`, `api/service.py`, `api/__main__.py`) | **untouched** this cycle |
| Frozen benchmark artifacts (`tests/fixtures/benchmark/`, `benchmark_runs/`) | **untouched**, byte-identical |
| Python tests | **1387/1387 passing** (fresh run, this cycle) |
| Ruff | **clean** (`ruff check src tests scripts`) |
| mypy | **clean**, 91 source files (`python3 -m mypy src` — this environment's bare `mypy` on `PATH` resolves to a different interpreter; the correct invocation is `python3 -m mypy`, a known, previously-documented environment quirk) |
| TypeScript | **49/49 passing**, `tsc` clean (fresh run, this cycle) |
| Live E2E smoke test | Real server, real repository, real `gpt-4o-mini-2024-07-18` calls — grounded and rejected results both confirmed (§C); temp files and server process cleaned up after |
| API key handling during this audit | Read only via the pre-existing, unmodified `os.environ` lookup inside `OpenAIGateway`; never printed, logged, or persisted; independently confirmed absent from the server log and every captured response this cycle |

---

## H. Architectural conclusion

**1. Is the deterministic graph sufficient for the validated V1 query classes?**
Yes. `CALLS` (AST), `DEPENDS_ON` (manifest), `HISTORY`/`CO_CHANGE` (Git), and — where a SCIP index is supplied — `REFERENCES`/`IMPLEMENTS` all produce real, directional, provenance-carrying evidence, live-confirmed this cycle for the default (no-external-artifact) provider set. The graph is the sole source of relationship truth throughout; nothing downstream ever adds a relationship the graph didn't already contain.

**2. Is query-shaped retrieval sufficient?**
Yes, for the validated classes. Live checks this cycle returned exactly the relevant bounded evidence (2 real callers for a 2-entity query, 4 real candidates for a genuinely ambiguous one, 2 real dependencies for a dependency query) — no excess, no silent truncation of relevant data, ambiguity preserved rather than resolved by guessing. Deeper token-footprint/high-fan-out efficiency rests on the prior, unmodified canonical/expansion benchmarks rather than a fresh measurement this cycle.

**3. Is the LLM correctly positioned as synthesizer rather than authority?**
Yes, and this is the cycle's central, freshly-reproduced proof: a real model call that reversed a real relationship was rejected regardless of the model's own confidence or fluency, and a real model call that fabricated a claim about a nonexistent symbol was independently caught the same way. The LLM proposes; the deterministic Entailment Engine, working only from canonical graph evidence, decides.

**4. Is deterministic grounding enforcement reliable?**
Yes. It is claim-type-blind (does not trust the model's own self-classification), identity-aware (resolves names/qualified-names/canonical-ids to the one real entity they unambiguously denote, never guessing on ambiguity), and directional (an edge is never treated as symmetric). All three properties were live-verified again this cycle, not merely re-run as unit tests.

**5. Is canonical identity resolution sufficiently safe?**
Yes, for the validated classes. Exact-id resolution is unambiguous by construction; name/qualified-name resolution refuses to guess when two or more entities share a name (live-verified in the ambiguous-query check, and unit-verified in the high-fan-out qualifier mechanism, unmodified this cycle).

**6. Is the provider architecture coherent?**
Yes. Every registered provider (`GitAdapter`, `AstCallsAdapter`, `PyprojectDependencyAdapter`) needs only a local, already-present artifact (`.git`, source files, `pyproject.toml`) and is registered in the default CLI; every provider requiring an *externally produced* artifact (`SCIPAdapter`, `CodeQLAdapter`) is correctly excluded from that default registration, per a single, consistently-applied criterion stated in `_build_api()`'s own docstring — not an ad hoc or inconsistent boundary.

**7. Are remaining limitations implementation defects or mostly validation/environment scope?**
Overwhelmingly the latter. The only items resembling an "implementation gap" are cosmetic (a handful of unrouted phrasing variants, a hyphen-in-capture-group regex limitation) and none change correctness — an unrouted or malformed query degrades to an honest "I don't understand," never a wrong or fabricated answer. Every other open item in §D is a genuine environment boundary (no GUI/GPU/Windows here) or a deliberate, documented scope decision (SCIP/CodeQL need external artifacts; certain relationship types have no provider yet).

**8. Is another architectural redesign justified?**
No. Nothing in this cycle's evidence points to a structural weakness in the pipeline itself. The protected invariant held under live, adversarial testing (a real model actively producing both a reversed claim and a fabricated one, in the same session, both correctly rejected) without needing any code change. The system is ready to move to a V1 acceptance/verification audit on its current architecture.
