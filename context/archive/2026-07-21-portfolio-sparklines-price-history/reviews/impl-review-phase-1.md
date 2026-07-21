<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Portfolio sparklines — price_history

- **Plan**: context/changes/portfolio-sparklines-price-history/plan.md
- **Scope**: Phase 1 of 2
- **Date**: 2026-07-21
- **Verdict**: APPROVED
- **Findings**: 0 critical  1 warning  2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS (1 observation — documented) |
| Safety & Quality | PASS (1 observation) |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | PASS |

## Findings

### F1 — Misleading _WL_ (watchlist) prefix on portfolio constants

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: db/bigquery.py:641-642
- **Detail**: New constants _WL_PRICE_HISTORY_SESSIONS / _WL_PRICE_HISTORY_SCAN_DAYS carried the _WL_ prefix, which in this file means *watchlist* (_WL_SENTIMENT_WINDOW_DAYS:1773, PUL-87). These are portfolio-position constants — the file's portfolio convention uses no WL prefix.
- **Fix**: Rename to _PRICE_HISTORY_SESSIONS / _PRICE_HISTORY_SCAN_DAYS (+ 3 refs in the query body).
- **Decision**: FIXED

### F2 — E2E fake signature change pulled from Phase 2 into Phase 1

- **Severity**: 🔍 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: tests/e2e/conftest.py:357
- **Detail**: Adding include_history=False to the fake was planned as Phase 2's first item, but Phase 1's endpoint change (include_history=True) breaks the e2e harness without it (9 specs fail). Pulling the minimal signature fix forward was necessary to satisfy the commit-on-green invariant. The richer Phase 2 work (fixture data + render assertion) is untouched.
- **Decision**: NOTED — no action; documented in phase summary.

### F3 — Empty-history representation (None vs []) not verified end-to-end

- **Severity**: 🔍 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: db/bigquery.py:685-692
- **Detail**: Decided empty history → None. Real BQ verified for the ARRAY_AGG path (arrays returned) and for the "ticker absent from price_hist" case, but not the full LEFT-JOIN-miss round-trip returning the row — so whether the client yields None or [] for the missing array isn't confirmed. Functionally identical: _sparklineSvg renders "—" for both, and the model field list[float] | None accepts either.
- **Decision**: NOTED — non-blocking; both render identically.
