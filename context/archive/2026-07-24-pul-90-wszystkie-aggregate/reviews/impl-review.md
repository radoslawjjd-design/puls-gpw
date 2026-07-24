<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: "Wszystkie" Aggregate View in Mój portfel

- **Plan**: context/changes/pul-90-wszystkie-aggregate/plan.md
- **Scope**: Full plan (Phases 1-3)
- **Date**: 2026-07-24
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Findings

### F1 — Merge assumes identical market data per ticker across wallets

- **Severity**: 🔎 OBSERVATION
- **Impact**: 🏃 LOW — documented, verified assumption
- **Dimension**: Safety & Quality
- **Location**: src/api.py `_merge_positions_by_ticker`
- **Detail**: The merge originally carried the first non-null market-data fields per ticker. Correct today (per-ticker DB price scan → identical across wallets), but latent risk if a source ever diverged.
- **Fix**: Applied — carry the market-data bundle from the row with the **freshest `price_as_of`** instead of first-seen; company_name still first-non-null. Added `test_merge_positions_carries_freshest_price_not_first_or_last`.
- **Decision**: FIXED

### F2 — Phase 3 scope reduction (no 2nd fixture portfolio)

- **Severity**: 🔎 OBSERVATION
- **Impact**: 🏃 LOW — approved deviation, equivalent coverage
- **Dimension**: Plan Adherence
- **Location**: tests/e2e/conftest.py / plan Progress 3.3
- **Detail**: Plan Phase 3 specified a 2nd fixture portfolio + shared ticker to exercise the merge in e2e. Dropped (user-approved) because a 2nd shared-ticker portfolio destabilises the treemap/calendar/scoping suite via Playwright strict-mode duplicate matches. Cross-wallet merge is covered 3× instead: unit tests, real-BQ curl, headed-browser on real 2 wallets. E2e still covers the "Wszystkie" default/read-only/scope-back contract.
- **Fix**: None — accepted; documented in Progress 3.3 + Phase 3 commit.
- **Decision**: SKIPPED (accepted)

## Cross-phase notes

- Sentinel `"all"` consistent backend↔frontend (`src/api.py:_ALL_PORTFOLIOS` / `static/index.html:_ALL_PORTFOLIOS`).
- Read-only defended at two layers: frontend guards + hidden controls, and backend POST/DELETE validate portfolio_id against the user's wallets → the sentinel is rejected 404.
- No SQL injection (static filter literal); no div-by-zero in merge (guarded `avg_buy_price`).

## Success Criteria Verification

- Automated: `uv run pytest tests/test_api.py` → 152 passed; full suite → 705 passed (593 unit + 112 e2e); ruff clean.
- Manual: 1.6/1.7 verified on real BigQuery; 2.2–2.5 verified via headed browser on real 2-wallet data (14 merged rows, combined calendar+chart, no console errors).
