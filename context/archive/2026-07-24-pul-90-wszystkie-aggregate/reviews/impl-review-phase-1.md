<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: "Wszystkie" Aggregate View in Mój portfel

- **Plan**: context/changes/pul-90-wszystkie-aggregate/plan.md
- **Scope**: Phase 1 of 3 (Backend)
- **Date**: 2026-07-24
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

## Findings

### F1 — Merge assumes identical market data per ticker across wallets

- **Severity**: 🔎 OBSERVATION
- **Impact**: 🏃 LOW — documented, verified assumption
- **Dimension**: Safety & Quality
- **Location**: src/api.py `_merge_positions_by_ticker` (carry loop)
- **Detail**: The merge carries the first non-null current_price / daily_change_pct / price_history / price_as_of per ticker. Correct because the DB price scan is per-ticker (same close for both wallets) — verified on real data (TOA/XTB/KRU/ETFBS80TR merged cleanly, shares sums exact). Documented in the helper docstring and plan Critical Implementation Details. Latent risk only if the price join ever became portfolio-scoped.
- **Fix**: None — documented and verified. Left as an observation.
- **Decision**: ACCEPTED (no action)

## Success Criteria Verification

- Automated: `uv run pytest tests/test_api.py` → 151 passed; full non-e2e suite → 593 passed; ruff clean.
- Manual (1.6/1.7): verified on real BigQuery for user with 2 wallets (główny+IKZE): positions?portfolio_id=all → 12 merged rows (11+7, shares sums exact); calendar/history all-mode → 200 (no 403), combined series.
