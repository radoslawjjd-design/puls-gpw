<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Non-admin Portfolio Treemap (PUL-64)

- **Plan**: context/changes/non-admin-portfolio-treemap/plan.md
- **Scope**: Phase 1 of 6
- **Date**: 2026-06-28
- **Verdict**: APPROVED (after fixes)
- **Findings**: 0 critical, 2 warnings, 3 observations

## Verdicts

| Dimension | Verdict |
|---|---|
| Plan Adherence | PASS |
| Scope Discipline | WARNING |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | WARNING |

## Findings

### F1 — ZeroDivisionError when daily_change_pct == -100.0

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/portfolio_treemap.py:35
- **Detail**: `daily_change_pln = position_value_pln * d_pct / 100 / (1 + d_pct / 100)` raised ZeroDivisionError when `d_pct == -100.0`. Sibling function `compute_treemap_positions` in the same file already guards this at lines 119-122. The guard was not carried over to the new function.
- **Fix**: Added `_denom = 1 + d_pct / 100` guard; returns None when `denom == 0`. Added unit test `test_user_compute_daily_change_pct_minus_100_does_not_raise`.
- **Decision**: FIXED

### F2 — mypy success criterion 1.2 unverifiable

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: N/A (toolchain gap)
- **Detail**: mypy was not in pyproject.toml; `uv run mypy` failed with "program not found". Progress checkbox [x] in d288aea was marked without evidence the command ran.
- **Fix**: Ran `uv add --dev mypy`; `uv run mypy db/bigquery.py src/portfolio_treemap.py` now passes clean.
- **Decision**: FIXED

### F3 — Unplanned file scripts/test_bq_user_portfolios.py

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: scripts/test_bq_user_portfolios.py
- **Detail**: File not in the Phase 1 plan but was created and committed. Follows the exact same convention as scripts/test_bq.py, test_bq_company_stats_merge.py, etc. Not collected by pytest, no CI impact.
- **Fix**: Accepted as-is (follows project convention).
- **Decision**: SKIPPED

### F4 — Inconsistent dict access in total_value sum

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/portfolio_treemap.py:17
- **Detail**: `row["shares"]` (direct, KeyError risk) in `total_value` sum vs `row.get("shares") or 0.0` in the per-row loop below. Not reachable via real pipeline but inconsistent.
- **Fix**: Changed to `row.get("shares", 0.0)` for symmetry.
- **Decision**: FIXED

### F5 — Ruff E402 errors are pre-existing, not introduced by Phase 1

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: db/bigquery.py:36-40
- **Detail**: 4 E402 errors existed identically on master before Phase 1. The load_dotenv() before GCP imports is intentional (documented in lessons.md and db-bigquery.md rule).
- **Fix**: Added `# noqa: E402` to the 4 established import lines so criterion 1.3 now passes clean.
- **Decision**: FIXED
