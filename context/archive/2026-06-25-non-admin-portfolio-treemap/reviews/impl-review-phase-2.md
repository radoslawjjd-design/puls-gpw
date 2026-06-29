<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Non-admin Portfolio Treemap (PUL-64)

- **Plan**: context/changes/non-admin-portfolio-treemap/plan.md
- **Scope**: Phase 2 of 6
- **Date**: 2026-06-28
- **Verdict**: APPROVED (all findings fixed)
- **Findings**: 0 critical, 1 warning, 2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | WARNING → FIXED |
| Architecture | PASS |
| Pattern Consistency | WARNING → FIXED |
| Success Criteria | PASS |

## Findings

### F1 — TOCTOU race in POST wallet uniqueness check

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/api.py:484-501 (pre-fix); db/bigquery.py:599-641
- **Detail**: POST fetched existing wallets then called create_user_portfolio in separate BQ round-trips — no atomicity. Concurrent POSTs could both pass the API-layer check before either INSERT landed.
- **Fix**: create_user_portfolio now uses conditional INSERT (SELECT … WHERE NOT EXISTS / WHERE inny_count < 2). 0 affected rows → BigQueryError with constraint message → API maps to 409 as fallback.
- **Decision**: FIXED — commit 2c890ab

### F2 — Missing BQ-error-path tests for POST and DELETE

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: tests/test_api.py
- **Detail**: Watchlist tests have test_watchlist_bq_error_returns_500 but wallet tests had no equivalent for create_user_portfolio / delete_user_portfolio raising BigQueryError.
- **Fix**: Added test_post_wallet_bq_error_returns_500 and test_delete_wallet_bq_error_returns_500.
- **Decision**: FIXED — commit 2c890ab

### F3 — HTTPException raised inside BigQueryError try-block (cosmetic)

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/api.py DELETE endpoint
- **Detail**: 404 HTTPException was raised inside the same try-block catching BigQueryError. Functionally correct but misleading. Fixed by splitting DELETE into separate try-blocks matching POST pattern.
- **Decision**: FIXED (as part of F1 restructuring) — commit 2c890ab
