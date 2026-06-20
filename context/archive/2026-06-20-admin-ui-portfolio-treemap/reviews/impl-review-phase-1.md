<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Admin UI portfolio treemap with daily P&L colouring

- **Plan**: context/changes/admin-ui-portfolio-treemap/plan.md
- **Scope**: Phase 1 of 3
- **Date**: 2026-06-20
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical, 2 warnings, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Findings

### F1 — compute_treemap_positions can still raise on a single malformed position entry

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/portfolio_treemap.py:26-28
- **Detail**: The plan's contract explicitly requires "the function must not crash the endpoint on bad data." The current try/except only guards the top-level `json.loads(...)["positions"]` extraction. The per-item loop (`position["ticker"]`, `position["value"]`) is unguarded — a malformed individual position dict (missing key → KeyError, or not a dict → TypeError) propagates out of `compute_treemap_positions()`, past the endpoint's `except BigQueryError`, and 500s with a raw traceback. Same gap applies to the yesterday-side dict comprehension (line 21) for individual malformed entries. Likelihood is low (write path is controlled by the `/portfolio-xpost` skill) but the plan called this out as an explicit defensive requirement.
- **Fix A ⭐ Recommended**: Guard the per-item access and skip malformed entries individually.
  - Strength: Fully satisfies the plan's stated contract; one bad position degrades gracefully instead of taking down the whole response.
  - Tradeoff: A few more lines in the pure function; needs a test for "one bad item among good ones."
  - Confidence: HIGH — same defensive style already used for the top-level parse in the same function.
  - Blind spot: None significant.
- **Fix B**: Accept current top-level-only guard, document residual risk.
  - Strength: No code change; matches "should not happen" reality.
  - Tradeoff: Plan's explicit contract line stays unmet; a future bad write would 500 the whole admin UI instead of degrading.
  - Confidence: MEDIUM — depends how much you trust the write path to never drift.
  - Blind spot: Haven't audited every code path that writes positions_json.
- **Decision**: FIXED (Fix A) — per-item access in compute_treemap_positions guarded with try/except KeyError/TypeError, malformed entries skipped individually; 2 new tests added (test_malformed_item_in_today_positions_is_skipped_not_raised, test_malformed_item_in_yesterday_positions_is_ignored_not_raised)

### F2 — Endpoint doesn't backstop non-BigQueryError exceptions from the pure function

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/api.py:230-243
- **Detail**: The route's `try/except BigQueryError` doesn't cover non-BigQueryError exceptions raised by `compute_treemap_positions()` or `TreemapPosition(**p)` construction (e.g. the KeyError/TypeError from F1). Mostly resolved once F1 is fixed.
- **Fix**: No separate change needed if F1 is fixed; otherwise broaden the except clause.
- **Decision**: SKIPPED — resolved by F1 fix

### F3 — No SQL injection risk

- **Severity**: 📝 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Safety & Quality
- **Location**: db/bigquery.py:320-326
- **Detail**: `get_latest_snapshot()` interpolates only the internal `_table_ref()` constant via f-string, no externally-controlled value — clean, matches sibling `get_latest_snapshot_before()`.
- **Decision**: ACKNOWLEDGED — no action needed, clean bill
