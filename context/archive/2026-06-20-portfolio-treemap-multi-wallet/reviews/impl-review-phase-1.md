<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Portfolio treemap — main + IKZE side-by-side with portfolio-share %

- **Plan**: context/changes/portfolio-treemap-multi-wallet/plan.md
- **Scope**: Phase 1 of 3 (Backend — per-wallet query, share computation, endpoint reshape)
- **Date**: 2026-06-20
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Test results

- `uv run pytest tests/test_portfolio_treemap.py -q` — 10/10 passed
- `uv run pytest tests/test_bigquery.py -q -k get_latest_snapshot` — 4/4 passed
- `uv run pytest tests/test_api.py -q -k treemap` — 9/9 passed (after F2 fix; 8/8 pre-fix)
- `uv run pytest --tb=short` (full suite) — 263 passed, 2 failed (pre-existing flaky `tests/e2e/test_idle_timeout.py`, tracked separately as PUL-49, unrelated to this change), 1 xfailed (treemap e2e render test, deliberately xfail pending Phase 2/3 frontend wiring)

## Findings

### F1 — Partial-result discard on second-wallet failure

- **Severity**: OBSERVATION
- **Impact**: LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/api.py:236-257
- **Detail**: If `main` succeeds and `ikze` then raises `BigQueryError`, the whole handler 500s and `main`'s already-computed data is discarded. Matches the plan's explicit "500 for either, regardless of which wallet triggered it" instruction — intended behavior, not drift.
- **Fix**: None needed — matches plan intent.
- **Decision**: ACKNOWLEDGED, no action

### F2 — No test for "wallet 1 succeeds, wallet 2 raises" ordering

- **Severity**: OBSERVATION
- **Impact**: LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: tests/test_api.py:367-371
- **Detail**: Existing 500-on-error test made `get_latest_snapshot_for_wallet` always raise, firing on the first iteration (`main`). No test proved the main-succeeds-then-ikze-raises discard scenario from F1.
- **Fix**: Add a side_effect-based test case (main returns data, ikze raises).
- **Decision**: FIXED — added `test_admin_treemap_first_wallet_succeeds_second_raises_returns_500` to tests/test_api.py

### F3 — Extra `except TypeError` not in plan contract

- **Severity**: OBSERVATION
- **Impact**: LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: src/portfolio_treemap.py (portfolio_share_pct block)
- **Detail**: Plan specified only `total_value == 0 → None`. Code adds a `try/except TypeError` around the division, swallowing the case where `value` isn't numeric. Harmless — only fires on already-malformed position data the function tolerates elsewhere.
- **Fix**: None needed — minor defensive addition consistent with the function's existing tolerate-malformed-input philosophy.
- **Decision**: ACKNOWLEDGED, no action
