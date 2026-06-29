<!-- PLAN-REVIEW-REPORT -->
# Plan Review: PUL-59 — P&L Calendar Monthly Portfolio View

- **Plan**: `context/changes/pul-59-portfolio-calendar/plan.md`
- **Mode**: Deep
- **Date**: 2026-06-29
- **Verdict**: SOUND (after fixes applied during triage)
- **Findings**: 1 critical, 3 warnings, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | WARNING → PASS (F1 fixed) |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | WARNING → PASS (F2, F5 resolved) |
| Plan Completeness | WARNING → PASS (F3, F4 fixed) |

## Grounding

6/6 paths ✓ (db/bigquery.py, src/api.py, static/index.html, tests/test_api.py [1019L],
tests/test_bigquery.py [1149L], tests/e2e/), 5/5 symbols ✓ (_get_role, _get_client_id,
_activePortfolioId, _ppTreemapData, list_user_portfolios), brief↔plan ✓

## Findings

### F1 — Wallet tabs hidden in calendar mode

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: End-State Alignment
- **Location**: Phase 4, Change #7
- **Detail**: index.html:2172 uses `mode === 'table' ? '' : 'none'` — hides wallet tabs for ALL non-table modes including 'calendar'. Plan's change #7 added calendar show/hide but didn't update this condition. User would be unable to switch wallets while in calendar mode.
- **Fix Applied**: Updated Change #7 contract to also update the tabs-wrap condition to `(mode === 'table' || mode === 'calendar') ? '' : 'none'`.
- **Decision**: FIXED (via Fix in plan)

### F2 — fetchPortfolioCalendar() missing null guard for _activePortfolioId

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 4, Change #4
- **Detail**: `_activePortfolioId` starts as null (line 1761). `fetchPortfolioPositions()` guards at line 1702. The plan's `fetchPortfolioCalendar()` spec had no guard — null portfolio_id → API 422.
- **Fix Applied**: Added null guard to Change #4 spec mirroring the pattern at line 1702: early return showing 'Wybierz portfel powyżej.'
- **Decision**: FIXED (via Fix in plan)

### F3 — Wrong location reference for _ppCalData=null wallet-switch reset

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 4, Change #8
- **Detail**: Plan referenced "alongside `_ppTreemapData = null`" but `_ppTreemapData = null` is in portfolio DELETE handler (line 2029), not wallet click handler. Additionally, plan didn't trigger refetch when switching wallets while in calendar mode.
- **Fix Applied**: Rewrote Change #8 with correct location (wallet click handler, line ~1948) and added conditional `fetchPortfolioCalendar()` call when calendar mode is active.
- **Decision**: FIXED (via Fix in plan)

### F4 — Phase 4 automated check requires non-existent E2E tests

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 4 — Success Criteria — Automated Verification
- **Detail**: `uv run pytest tests/e2e/ -k calendar` was the only Phase 4 automated gate. Tests don't exist yet (created by `/10x-e2e` after implementation). pytest exit 5 = failure. Reference to non-existent "Phase 5" was confusing.
- **Fix Applied**: Removed E2E gate from Phase 4 automated. Added note directing to `/10x-e2e pul-59-portfolio-calendar` after manual verification. Updated Progress 4.1 to `pytest tests/test_api.py` regression check.
- **Decision**: FIXED (via Fix in plan)

### F5 — BQ query: total_positions scalar subquery per row

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 1 — BQ query description
- **Detail**: `(SELECT cnt FROM total_pos) AS total_positions` inside GROUP BY — scalar subquery evaluated per-group. Correct but slightly unusual. Python `len()` alternative is simpler.
- **Decision**: ACCEPTED (implementer chooses approach)
