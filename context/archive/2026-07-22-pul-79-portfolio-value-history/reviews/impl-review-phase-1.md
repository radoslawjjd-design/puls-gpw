<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Portfolio Value-History Endpoint (PUL-79 / FARO-5)

- **Plan**: context/changes/pul-79-portfolio-value-history/plan.md
- **Scope**: Phase 1 of 2 (BQ layer — get_portfolio_history)
- **Date**: 2026-07-22
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Success Criteria (re-run fresh)

- 1.1 `uv run pytest tests/test_bigquery.py -k history` → 5 passed
- 1.2 full unit suite → 573 passed
- 1.3 `ruff check db/bigquery.py tests/test_bigquery.py` → All checks passed
- 1.4 (manual) live bq query over owner portfolio → smooth curve, no spurious step at the 9/12→12/12 boundary, ETF included ✓

Diff scope = exactly the planned files (db/bigquery.py, tests/test_bigquery.py) + the seeded change folder. No unplanned changes.

## Findings

### F1 — LOCF can carry a stale price for a delisted/halted holding

- **Severity**: 🔵 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: db/bigquery.py — get_portfolio_history (filled CTE)
- **Detail**: The 400-day LOCF window carries a stopped ticker's last close forward for up to 400 days. Correct for active holdings; a delisted holding would show flat value instead of dropping out. Not a concern for current (liquid) portfolios, and the full-coverage gate keeps the series internally consistent.
- **Fix**: None needed now. If delisted holdings ever appear, cap carry-forward age (only fill from a close within N trading days).
- **Decision**: SKIPPED — recorded as a known edge case; revisit if/when delisted holdings surface.

## Note

change.md status intentionally left at `implementing` (not `impl_reviewed`): this is a phase-scoped review with Phase 2 (API layer) still pending; the TDD flow owns the status lifecycle through to `implemented`.
