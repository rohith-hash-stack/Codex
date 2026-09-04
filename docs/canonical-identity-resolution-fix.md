# Canonical Identity Resolution Fix

Fixes the backend identity-resolution defect discovered during the UI Integration Milestone (`docs/ui-integration-milestone.md` §4) and removes the client-side workaround it required.

## 1. Exact root cause

**An exact-match ordering bug in `codex.planner.retrieval._resolve_one_target`.**

The function computes candidates from `GraphReader.find_entities(qualified_name=target)` (a plain substring lookup, `raw_by_qualified_name`) and is documented to "prefer an exact `qualified_name` match over a mere substring one." Before this fix, the code was:

```python
raw_by_qualified_name = graph.find_entities(qualified_name=target)
target_lower = target.lower()
by_qualified_name = {
    e.canonical_id: e
    for e in raw_by_qualified_name
    if target_lower in _symbol_path(e.qualified_name).lower()   # GAP-1/GAP-6 narrowing
}
exact_qualified_name = {
    canonical_id: entity
    for canonical_id, entity in by_qualified_name.items()        # <- computed from the NARROWED set
    if entity.qualified_name.lower() == target.lower()
}
by_name = {e.canonical_id: e for e in graph.find_entities(name=target)}
combined = {**(exact_qualified_name or by_qualified_name), **by_name}
```

`by_qualified_name` narrows `raw_by_qualified_name` to entities where `target` occurs within `_symbol_path(entity.qualified_name)` — the *symbol-only* slice, with the file/module-path prefix stripped (`_symbol_path`'s own docstring: `AstCallsAdapter`'s `"<file>::<symbol>"` splits on the last `"::"`; `SCIPAdapter`'s `` `module`/Class#method(). `` splits on `"/"`). This narrowing exists for a real, independent reason (GAP-1/GAP-6: prevent a target that merely names a *file* — e.g. `"approx"` matching every symbol in `approx.py` — from resolving unrelated entities).

The bug: `exact_qualified_name` was computed from `by_qualified_name` (already narrowed), not from `raw_by_qualified_name` (the raw lookup the exact-match behavior is documented against). When `target` is an entity's own **full** `qualified_name` — e.g. `AstCallsAdapter`'s `"app.py::helper"` — `_symbol_path("app.py::helper")` strips it down to `"helper"`, and `"app.py::helper" in "helper"` is `False`. So the entity never even reached `by_qualified_name`, `exact_qualified_name` was computed over an empty set, and `by_name` (bare-`name`-only lookup) doesn't match a `"::"`-containing string either. Result: `resolve_targets(graph, ["app.py::helper"])` returned `[]` for a real, existing entity whose complete identifier was passed verbatim.

**Classification** (per the investigation's own required categories): this is **an exact-match bug** — specifically an operation-ordering defect where a later-added narrowing step (GAP-1/GAP-6, correct and necessary on its own) was interleaved *before* an earlier, already-documented exact-match preference instead of alongside/after it. It is not a qualified-name normalization mismatch (no case/whitespace/separator handling was wrong), not a provider-specific naming mismatch (reproduced identically for both `AstCallsAdapter`'s and `SCIPAdapter`'s `qualified_name` shapes — see §3), and not a deliberate design choice (the function's own docstring already promises the exact-match behavior the bug prevented).

## 2. Failure path traced

1. **Target extraction**: `QueryContract.targets` (or, for the UI, a `VisualizationNode.qualified_name` the server itself returned) carries the target string unchanged — not implicated.
2. **`resolve_targets`**: calls `_resolve_one_target` per target, deduplicates/sorts the combined result — not implicated; the empty result from `_resolve_one_target` propagates through untouched.
3. **`_resolve_one_target`**: the actual defect, described above.
4. **Canonical entity identity / qualified-name matching**: `RepositorySymbol.canonical_id`/`qualified_name` themselves, and `GraphReader.find_entities`'s own substring lookup, are correct and unmodified — confirmed directly (`graph.find_entities(qualified_name="app.py::helper")` already returned the real entity; only `_resolve_one_target`'s post-processing of that result was wrong).

## 3. The fix (minimal)

Reordered so the exact-match check runs against the **raw** lookup, matching the function's own pre-existing docstring, and short-circuits the GAP-1/GAP-6 narrowing when an exact match exists (that narrowing's purpose — filtering *substring* noise — has nothing left to do once the single most-specific possible signal, a full exact identity match, is already found):

```python
raw_by_qualified_name = graph.find_entities(qualified_name=target)
target_lower = target.lower()
exact_qualified_name = {
    e.canonical_id: e for e in raw_by_qualified_name if e.qualified_name.lower() == target_lower
}
by_qualified_name = exact_qualified_name or {
    e.canonical_id: e
    for e in raw_by_qualified_name
    if target_lower in _symbol_path(e.qualified_name).lower()
}
by_name = {e.canonical_id: e for e in graph.find_entities(name=target)}
combined = {**by_qualified_name, **by_name}
```

**Lines changed: one function, `src/codex/planner/retrieval.py`.** No other file touched. The GAP-1/GAP-6 narrowing filter itself, `_symbol_path`, `_qualifier_confirmation_tier`, `_match_tier`, and every other identity/disambiguation mechanism are byte-identical — this fix only changes *which input* the exact-match check is computed from, not what "exact match" means, not the narrowing's own logic, not any downstream ranking/traversal/evidence code.

## 4. Before / after behavior

| Query | Before | After |
|---|---|---|
| `resolve_targets(graph, ["app.py::helper"])` (entity's own full `qualified_name`) | `[]` | `["helper"]` |
| `resolve_targets(graph, ["approx"])` where `approx.py` also contains unrelated symbols (GAP-1 case) | correctly excludes unrelated symbols | unchanged — still excludes them |
| `resolve_targets(graph, ["Storage"])` where 20+ SCIP entities share the bare symbol (GAP-8/high-fan-out case) | correctly tier-ranked/disambiguated | unchanged |
| `resolve_targets(graph, ["execute"])` where 5 entities share a bare name (genuine ambiguity) | all 5 returned | unchanged — all 5 still returned |
| `resolve_targets(graph, ["pkg/mod2.py::execute"])` (one of those 5 entities' own full `qualified_name`) | `[]` (same defect) | `["exec2"]` — now correctly disambiguates by full identity |
| `GET /neighborhood?symbol=app.py::helper` (the UI's exact evidence-to-graph navigation call) | `0` nodes | `2` nodes, `1` edge — correct |

## 5. Tests added

`tests/test_planner_target_resolution.py` (+10 tests, appended in a new, clearly labeled section):

1. `test_exact_full_qualified_name_resolves_to_its_own_entity` — the exact defect, AST shape.
2. `test_exact_full_qualified_name_unaffected_by_unrelated_file_path_decoys` — GAP-1 protection preserved.
3. `test_exact_full_qualified_name_scip_shape_resolves_to_its_own_entity` — SCIP shape (requirement 2).
4. `test_exact_full_qualified_name_ast_shape_still_matches_case_insensitively` — case-insensitivity preserved.
5. `test_full_qualified_name_disambiguates_high_fan_out_bare_symbol` — high-fan-out disambiguation (requirement 3), plus confirms the bare, unqualified symbol name is still genuinely ambiguous across all candidates.
6. `test_bare_name_target_behavior_unchanged_by_full_qualified_name_fix` — bare-name behavior unchanged (requirement 4).
7. `test_ambiguous_target_still_explicitly_ambiguous_after_fix` — ambiguity handling unchanged (requirement 5).
8. `test_full_qualified_name_fix_deterministic_across_repeated_calls` — determinism.
9. `test_full_qualified_name_negative_query_still_returns_empty` — no fabricated matches.
10. `test_full_qualified_name_through_full_plan_query_execute_query_pipeline` — end-to-end through the real `plan_query`/`execute_query` pipeline, the exact shape of query the UI's evidence-to-graph navigation issues (requirement 6, backend side).

`vscode-extension/src/integration.test.ts` — the pre-existing discovered-gap regression test (added during the UI Integration Milestone) rewritten to assert the **fixed** behavior: an entity's own full `qualified_name` now resolves directly via a real `/neighborhood` call, with no fallback (requirement 6, UI side).

**Before the fix**: all 10 new Python tests would fail (as manually confirmed for test 1 by reverting the fix locally and re-running); the existing TS integration test failed exactly as expected (`2 !== 0`) once the backend was fixed but the test itself hadn't been updated yet — confirming the test suite genuinely exercises the real behavior change, not a tautology.

**After the fix**: all pass (see §6).

## 6. Complete validation results

- **Full Python suite**: **1351/1351 passing** (was 1341; +10 new).
- **`tests/test_planner_target_resolution.py`** (the identity/retrieval-resolution suite): **79/79 passing** (was 69; +10 new).
- **API tests** (`test_api_contracts.py`, `test_api_server.py`, `test_api_service.py`, `test_api_r1_r2_regression.py`, `test_api_ask.py`, `test_api_hardening_audit.py`): **66/66 passing**, unaffected.
- **Real-repository/SCIP integration tests** (`test_scip_adapter.py`, `test_scip_evidence_propagation.py`): **135/135 passing**, unaffected.
- **`ruff check src tests scripts`**: clean.
- **`mypy src`**: clean, 91 source files.
- **`codex-canonical-v1` / `validation-expansion-v1` regression** (`test_benchmark_canonical_corpus.py`, `test_benchmark_expansion_corpus.py`, `test_benchmark_dev_corpus.py`): **22/22 passing** — both frozen corpora still reconstruct byte-identically from real ingestion.
- **Frozen benchmark artifacts**: confirmed byte-unchanged (`git status`/`git diff --stat` on `tests/fixtures/benchmark/`, `benchmark_runs/` both empty).
- **UI/TypeScript tests** (`npm test`, Node's built-in test runner): **30/30 passing**, including the rewritten discovered-gap-turned-confirmation test.
- **No new benchmark run, no re-run against a real LLM was needed or performed**: this fix touches only deterministic target resolution (D9's own retrieval-engine layer), never the LLM Gateway, prompt construction, or evidence generation — the already-frozen `benchmark_runs/*.json` artifacts remain the valid, current record of validated LLM behavior, unaffected by this change (confirmed empty diff, not merely assumed).

## 7. UI workaround: removed

The `AskPanel.runExpand` bare-name fallback (added during the UI Integration Milestone) is **removed** — the corrected backend contract makes it unnecessary:

- `vscode-extension/src/askPanel.ts`: `runExpand` no longer takes or uses a `fallback` parameter; the `InboundMessage` `"expand"` variant no longer carries `fallback`.
- `vscode-extension/src/askPanelView.ts`: `exploreSymbol` no longer takes or sends a `name` alongside `qualifiedName`; the search-result chip markup no longer carries a `data-name` attribute; the graph's `onNodeClick` handler posts only `symbol`, no `fallback`.
- `vscode-extension/src/integration.test.ts`: the discovered-gap regression test rewritten to confirm the *fixed* behavior (§5) instead of the workaround's effect.

Verified live: `GET /neighborhood?symbol=app.py::helper` (the exact call the removed fallback used to retry) now returns the correct `2` nodes / `1` edge directly, with no second request.

## 8. Confirmations

- **No benchmark corpus/ground-truth/artifact changes**: confirmed (§6).
- **No unrelated deterministic-layer behavior changed**: only `_resolve_one_target`'s exact-match computation order changed; `resolve_targets`, `bounded_traversal`, `rank_entities`, `plan_query`, `execute_query`, evidence collection, and every other identity/disambiguation mechanism (`_symbol_path`, `_qualifier_confirmation_tier`, `_match_tier`, `_has_boundary_aligned_occurrence`, `_bare_scip_symbol`) are unmodified, confirmed by the full existing `test_planner_target_resolution.py`/`test_planner_retrieval.py`/`test_planner_boundaries.py` suites passing unchanged.
- **No fabricated relationships introduced**: the fix only changes which *real* entities a target string resolves to (strictly a precision improvement — an entity's own real, complete identifier now finds itself); it introduces no new graph facts, no new relationship types, no fuzzy/approximate matching.
- **No retrieval regression**: full suite green, high-fan-out/ambiguity/GAP-1/GAP-6/GAP-8 test families all still pass.
- **No change to validated LLM behavior**: no file under `src/codex/llm/`, `src/codex/query_understanding/`, or `src/codex/benchmark/` was touched; frozen benchmark run artifacts byte-unchanged.

## 9. Commit

Committed separately from any UI/3D work, isolated to this fix: `src/codex/planner/retrieval.py` (the fix), `tests/test_planner_target_resolution.py` (backend regression coverage), `vscode-extension/src/askPanel.ts`/`askPanelView.ts`/`integration.test.ts` (workaround removal + updated regression coverage), `docs/canonical-identity-resolution-fix.md` (this report), `docs/ui-integration-milestone.md` (a one-line "resolved" pointer, historical claims otherwise untouched), `PROGRESS.md`.
