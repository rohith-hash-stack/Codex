# Python Index Fidelity — Gap Register

**Version:** 1
**Baseline:** `main`@`98fb59ca7d1ddf732523fcadfca15df6b4863b2f`
**Scope:** the SCIP provider's Python-language fidelity (`codex.provider.scip*`), plus its AST-provider counterpart (`codex.provider.ast_calls_adapter`) where they overlap. Indexer: `scip-python@0.6.6` (`docs/resources.md`).
**Method:** raw wire-format inventory against 5 real repositories (django, flask, click, pytest, requests), traced through decode → normalize → identity → graph → candidate → retrieval, per the acceptance-gate discipline in the Phase-2 directive.

This register is the single source of truth for Python-specific fidelity gaps. Every row must reach `FIXED + REGRESSION-LOCKED` or `ACCEPTED — outside contract` with explicit evidence before Python 100% may be declared. No row may remain `UNKNOWN`.

## Register

| GAP-ID | Layer | Evidence | Root Cause | Classification | Fix | Tests | Independent Validation | Status |
|---|---|---|---|---|---|---|---|---|
| GAP-11 | Planner (`planner.py` budget pruning) | **Measured retrieval recall = 0% on confirmed real data.** django `What references QuerySet?`: 38 real `REFERENCES` relationships exist in the graph among the query's own 80 resolved target entities (0 `IMPORTS`), retrieval returns 0. click `What references Command?`: 95 real `REFERENCES` relationships exist among targets, retrieval returns 0. Root-caused: `plan_query`'s truncation step keeps only the alphabetically-first relationship type on overflow. Compounded by this audit's finding that `RelationshipType.IMPORTS` is **never populated** by scip-python (0 of 972,111 real occurrences ever carry the Import role bit — see ACC-2), and `IMPORTS` < `REFERENCES` alphabetically — so the kept type is deterministically always the empty one whenever truncation fires for `FIND_REFERENCES`. | Pre-existing, unrelated to any of GAP-5/7/8/9/10's diffs; confirmed untouched by all of them. | 4 — BUG (retrieval-completeness contract violation, not merely a theoretical edge case) | Not yet designed | — | — | OPEN — confirmed Python-100%-blocking with measured 0% recall on 2 independent real repositories (see §K) |
| GAP-13 | Cross-provider identity resolution (`resolution/entity_resolver.py`'s `_symbol_location_identity_key`) | AST-provided and SCIP-provided entities for the *same real method* fail to converge — two separate canonical IDs exist for one real symbol — whenever the method is declared via the `@typing.overload` idiom (2+ stub signatures plus one real implementation, same name, same class). Root cause: the convergence key requires an *exact* `start_line` match; scip-python's own Definition-role occurrence for the method-level symbol lands on the line of the *first* `@overload` stub, while `AstCallsAdapter` correctly identifies the real, executable implementation's line — a legitimate, principled disagreement about "where this symbol lives" between the two providers, not an arbitrary defect in either one alone. Reproduced independently in 2 repositories: flask's `App.template_test` (SCIP line 713 vs AST line 719, `src/flask/sansio/app.py`) and requests' `iter_slices` (SCIP line 613 vs AST line 620, `src/requests/utils.py`) — both confirmed via direct raw-index inspection, not inference. Sibling methods without `@overload` in the same files/classes (e.g. flask's `App.add_template_test`) converge correctly, isolating `@overload` specifically as the trigger. Aggregate `ast_calls`-only FUNCTION/METHOD counts per repo (django 13, flask 8, click 13, pytest 30, requests 112) are a **mix** of this cause and a separate, non-bug cause (scip-python never indexing a symbol at all, e.g. requests' `test_unicode_is_ascii` — confirmed zero Occurrences/SymbolInformation anywhere in that repo's raw index for it, a genuine upstream gap AST correctly and independently fills) — the two causes were not fully separated per-repo in this audit pass. | The identity-convergence key's exact-line-match requirement has no special case for the `@overload` idiom (multiple textual definitions of the same runtime symbol). | 4 — BUG (identity-completeness contract violation: "identical symbols resolve to the same canonical identity" invariant, Phase 4) for the `@overload` sub-case specifically; the non-`@overload` portion of the divergence is 2 — Not emitted upstream (not a gap). | Not yet designed | — | — | OPEN — newly discovered this audit; needs per-repo cause separation before scoping a fix |
| GAP-12 | SCIP mapping (`infer_base_type`'s descriptor-suffix fallback) | Descriptor paths ending in `:` (scip-python's per-file "module identity" marker, e.g. `` `django.utils.crypto`/__init__: ``, exactly one per document — 3358 total across 5 repos) are never matched by any branch of `infer_base_type`'s fallback (`_SKIP_SUFFIXES`, `"()."`, `"#"`, `"."`), so it falls through to `return None`. Since `SymbolInformation.kind` is always `0` in real scip-python output (244,969/244,969 samples — see §B.1), the kind-based path never rescues this. `_resolve_symbol` treats `None` from `infer_base_type` as "skip, no entity" unconditionally. Real-data measurement: 31,411 non-Definition reference occurrences targeting these symbols are silently dropped (django 27,671; pytest 3,730; requests 10; flask/click 0 — see §C.3). Each is a genuine "module X imports/references module Y" fact scip-python emitted and Codex currently discards without a trace (no partial coverage marker, no error). | `infer_base_type`'s descriptor-suffix table is incomplete — it never enumerated the `:` (module/namespace-identity) shape, only `#`/`.`/`"()."`/`)`. | 4 — BUG (silent evidence loss, no upstream limitation — the data is fully present in the raw index) | Not yet designed | — | — | OPEN — newly discovered this audit, confirmed real, not yet fixed (audit-first scope) |

## Accepted / out-of-contract findings (not gaps — documented with evidence, no fix needed)

| ID | Finding | Evidence | Classification | Acceptance rationale |
|---|---|---|---|---|
| ACC-1 | `Index.external_symbols` (wire field 3) is decoded (`ScipIndex.external_symbols`) but never consumed by `SCIPAdapter`. | 208–1444 entries per repo (3359 total). 100% have `kind=0` and zero relationships in all 5 repos — carries no metadata beyond the bare symbol string. 20–127 per repo are unreachable via any Occurrence/Relationship elsewhere (bare whole-module `import os`-style targets scip-python never gives an Occurrence). | 3 — Intentionally unsupported / correctly filtered by contract | `external_symbols` is an `Index`-level field with **no per-Document attribution** in the wire format itself — structurally incapable of producing a correctly-attributed `subject=file` REFERENCES/IMPORTS relationship without violating the no-attribution-contamination requirement. Any package-level signal it might imply is already more authoritatively covered by `PyprojectDependencyAdapter` (declared deps from `pyproject.toml`/requirements). Matches the adapter's own pre-existing documented rationale for leaving `DEPENDENCY`/`DATA_FLOW` unimplemented for SCIP ("SCIP's Import role is a symbol-level fact, not the package-level claim DEPENDS_ON implies"). |
| ACC-2 | `Occurrence.symbol_roles` never carries the Import bit (`0x2`) in real scip-python output. | 0 of 972,111 real occurrences across 5 repos. Only bits `1` (Definition) and `8` (ReadAccess) ever observed — never WriteAccess(`4`), Generated(`16`), Test(`32`), ForwardDefinition(`64`). | 2 — Not emitted upstream | Codex's `_IMPORT_ROLE = 0x2` check is implemented correctly per the real `scip.proto` spec; scip-python 0.6.6 simply never sets this bit. Verified this is the root *amplifier* of GAP-11 (see GAP-11 row) rather than a separate defect — Codex faithfully reports what the indexer emits. |
| ACC-3 | `Relationship.is_reference` / `is_type_definition` / `is_definition` flags are never `True` in real scip-python output — only `is_implementation` ever fires (12,992/12,992 real relationships, 5 repos). | See §C.2. | 2 — Not emitted upstream | The `TYPE_RELATIONSHIP` capability's `is_type_definition`-sourced contribution to `REFERENCES` is real code, correctly implemented, but currently unexercised by this indexer version — not a Codex defect. `REFERENCES` overall is *not* dead (it's also populated from plain non-Definition Occurrences), only this one sourcing path is. |
| ACC-4 | `SymbolInformation.kind` is always `0` (`UnspecifiedKind`) in real scip-python output. | 244,969/244,969 samples, 5 repos, zero exceptions. | 2 — Not emitted upstream | `infer_base_type`'s kind-based table (`_KIND_TO_BASE_TYPE`) is correctly implemented and unit-tested, but is dead code against this indexer version in practice — 100% of real classification happens via the descriptor-suffix fallback. Recorded here because it reframes the fallback from "rare edge case" (as GAP-9/10's own comments characterized it) to "the only path that matters" — relevant context for any future change to that fallback, including GAP-12's fix. |

## Summary counts (raw inventory, 5 repositories: django, flask, click, pytest, requests)

| Metric | Total |
|---|---|
| Documents | 3358 |
| Occurrences | 972,111 |
| SymbolInformation entries | 244,969 |
| Relationships | 12,992 (100% `is_implementation`) |
| External symbols (Index-level) | 3359 |
| Local (function-scoped) symbols | 100,156 |
| Distinct `SymbolInformation.kind` values observed | 1 (`{0}`) |
| Distinct `Occurrence.symbol_roles` values observed | 2 (`{1, 8}`) |
| Distinct descriptor-path suffix shapes observed | 4 (`` ` # . ) : ` ``) |

Full raw counters: `/tmp/claude-0/-home-user/3b795297-2a2e-5a02-ae81-b4114757a603/scratchpad/python100/raw_inventory.json` (per-repo breakdown).

## End-to-end fidelity matrix

| Category | Raw emitted | Decoded | Normalized | Graph | Candidate | Retrieved | Loss / Classification |
|---|---|---|---|---|---|---|---|
| Definition occurrences (`#`/`.`/`"()."` suffix shapes) | Yes | Yes | Yes | Yes | Yes | Yes | None found |
| Definition occurrences (`:` suffix — module identity) | Yes | Yes | **No — `infer_base_type` returns `None`** | **No entity** | **No** | **No** | **GAP-12 — BUG, 31,411 real reference occurrences lost across 5 repos** |
| Non-Definition reference occurrences (ReadAccess, bit `8`) | Yes | Yes | Yes | Yes | Yes | Yes (when not over budget) | None at graph layer; retrieval-layer loss is GAP-11 |
| Import-role occurrences (bit `2`) | **Never emitted by scip-python** | n/a | n/a | n/a | n/a | n/a | ACC-2 — not emitted upstream |
| `is_implementation` relationships | Yes | Yes | Yes | Yes | Yes | Yes (when not over budget) | None found — 100% endpoint resolution measured |
| `is_type_definition` / `is_reference` / `is_definition` relationships | **Never emitted by scip-python** | n/a | n/a | n/a | n/a | n/a | ACC-3 — not emitted upstream |
| Relationship-only symbols (no Definition, no SymbolInformation) | Yes | Yes | Yes (GAP-10 fix) | Yes | Yes (measured: 3/175 for flask `Index`) | Yes | Fixed, merged, regression-locked (GAP-10) |
| `SymbolInformation.kind` | **Always `0`** | Yes | Falls back to descriptor-suffix inference | — | — | — | ACC-4 — not emitted upstream; descriptor-suffix fallback is the *only* live path |
| `Index.external_symbols` | Yes (208–1444/repo) | Yes | Not consumed | — | — | — | ACC-1 — intentionally unsupported, no per-file attribution possible |
| Cross-provider identity (AST FUNCTION/METHOD vs SCIP FUNCTION/METHOD, same real symbol) | Yes (both sides) | Yes | **Fails to merge for `@overload`-declared methods** | Two entities instead of one | Both independently candidates | Evidence split across two nodes | **GAP-13 — BUG, confirmed in 2 repos (flask, requests)** |
| FIND_REFERENCES / multi-relationship-type retrieval under budget truncation | Yes (real, in graph) | Yes | Yes | Yes | Yes | **No — pruning keeps the empty type** | **GAP-11 — BUG, measured 0% recall (django `QuerySet`: 38→0; click `Command`: 95→0)** |
| Local (function-scoped) symbols | Yes (100,156 total) | Yes | Correctly filtered (`is_local_symbol`) | No entity (by design) | n/a | n/a | Category 1 — correctly filtered by contract |
| Parameter descriptors (`)` suffix) | Yes (65,259 total) | Yes | Correctly filtered (`_SKIP_SUFFIXES`) | No entity (by design) | n/a | n/a | Category 1 — correctly filtered by contract |

## Completeness counters (measured, 5 repositories)

| Repo | Raw occ. | Raw def. occ. | Raw distinct symbols | Raw relationships | Graph entities | Graph relationships | Endpoints resolved | Identity collisions | Colon-suffix entities (expect 0) | AST/SCIP converged | AST-only entities |
|---|---|---|---|---|---|---|---|---|---|---|---|
| django | 772,518 | 115,005 | 116,216 | 12,295 | 65,504 | 69,988 | 100.00% | 0 | 0 | 10,313 | 13 |
| flask | 20,313 | 3,991 | 4,393 | 105 | 2,055 | 1,471 | 100.00% | 0 | 0 | 194 | 8 |
| click | 33,627 | 5,493 | 5,904 | 174 | 2,520 | 2,352 | 100.00% | 0 | 0 | 358 | 13 |
| pytest | 137,429 | 19,407 | 20,395 | 354 | 8,401 | 9,837 | 100.00% | 0 | 0 | 2,013 | 30 |
| requests | 8,224 | 1,071 | 1,368 | 64 | 943 | 635 | 100.00% | 0 | 0 | 153 | 112 |

Raw JSON: `identity_fidelity.json`, `completeness_counters.json` in the scratchpad `python100/` directory (see report for full path). `AST-only entities` is a **mix** of GAP-13 (`@overload` split, confirmed present) and legitimate scip-python indexing gaps AST independently fills (not a bug) — not yet separated per-repo; `requests`' notably higher ratio (112/153) needs that separation before scoping GAP-13's fix.

## Scope and depth disclosure (honest accounting, not overclaiming)

- **Fully measured with real data**: upstream symbol/occurrence/relationship-flag inventory (Phase 2), identity collision counts (Phase 4), relationship endpoint resolution (Phase 5), GAP-11's retrieval recall (Phase 5/7), candidate generation for GAP-10-recovered entities (Phase 6), AST/SCIP convergence rates (Phase 8).
- **Partially measured**: candidate-generation completeness for large pools (cites pre-existing GAP-8 measurements, not independently re-measured this pass); external/unresolved semantics (cites GAP-9/10's own 15-test regression suite plus this audit's own false-positive re-verification, not new dedicated tests written this pass).
- **Not done this pass**: dedicated mutation testing (Phase 11's "deliberately alter descriptor decorations/remove SymbolInformation/etc." battery), a repository outside the existing 5 (django/flask/click/pytest/requests — all already used during prior gap-fix cycles, so the "at least one independent repository" requirement for *future fixes* is not yet satisfied and must be honored when GAP-11/12/13 are fixed), per-repo separation of GAP-13's two contributing causes.
- **Rationale for stopping here**: three confirmed, real Python-specific bugs (GAP-11, GAP-12, GAP-13) already make the Python 100% gate unreachable regardless of how much additional Phase 9-12 depth is added. Further exhaustive measurement would not change the READY/NOT READY verdict — it would only pad evidence for gaps already conclusively proven. Effort was directed at breadth-first correctness (real data, multiple repos, root-caused not just observed) over exhaustively completing every sub-bullet of every phase.
