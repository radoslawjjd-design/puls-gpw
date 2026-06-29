<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Non-admin Portfolio Treemap (PUL-64)

- **Plan**: context/changes/non-admin-portfolio-treemap/plan.md
- **Scope**: All phases (1–6)
- **Date**: 2026-06-28
- **Verdict**: APPROVED (all findings fixed during triage)
- **Findings**: 0 critical  1 warning  2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | WARNING → FIXED |
| Architecture | PASS |
| Pattern Consistency | WARNING → FIXED |
| Success Criteria | WARNING (3.4–3.6 + 6.4 pending manual) |

## Automated Verification

- ✅ 108 unit + integration tests pass
- ✅ 54 E2E tests pass
- ✅ mypy src/api.py src/portfolio_treemap.py db/bigquery.py — clean
- ✅ ruff check src/ db/ — clean

## Manual Verification Status

- ✅ 1.4–1.6 Phase 1 BQ manual checks
- ✅ 2.4–2.7 Phase 2 wallet API manual checks
- ⏳ 3.4 GET /api/portfolio/treemap returns {portfolios, as_of} — PENDING
- ⏳ 3.5 GET /api/portfolio/positions without portfolio_id → 422 — PENDING
- ⏳ 3.6 GET /admin/portfolio/treemap still returns 200 — PENDING
- ✅ 4.3–4.7 Phase 4 wallet UI manual checks
- ✅ 5.4–5.8 Phase 5 treemap + admin cleanup manual checks
- ⏳ 6.4 Full E2E suite against local server — PENDING

## Findings

### F1 — N+1 query pattern in GET /api/portfolio/treemap

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/api.py:566–572 (before fix)
- **Detail**: The endpoint looped over wallets calling list_user_portfolio_positions(client_id, pid)
  once per wallet — up to 7 sequential BQ round-trips. The helper already supported a
  single-call "fetch all" mode (portfolio_id=None), and its own docstring said "used by the
  treemap endpoint" for that mode — but the treemap endpoint never used it.
- **Fix**: Switched to single list_user_portfolio_positions(client_id) call, group rows by
  portfolio_id in Python, compute per-wallet. Reduces BQ round-trips from N to 1.
- **Decision**: FIXED

### F2 — Stale docstring in list_user_portfolio_positions

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: db/bigquery.py:499
- **Detail**: Docstring said "used by the treemap endpoint" for the no-portfolio_id case but the
  treemap endpoint didn't use it that way. Fixed as part of F1 fix — docstring updated to
  describe actual batch-fetch design.
- **Decision**: FIXED (via F1 fix)

### F3 — Dead #treemap-view CSS selector

- **Severity**: 💬 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: static/index.html:90
- **Detail**: CSS rule referenced #treemap-view even though the element was removed in Phase 5.
  Dead rule, no visual impact.
- **Fix**: Removed ", #treemap-view" from the selector.
- **Decision**: FIXED
