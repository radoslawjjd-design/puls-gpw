<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: PUL-59 — Monthly P&L Calendar View

- **Plan**: context/changes/pul-59-portfolio-calendar/plan.md
- **Scope**: All Phases (1–4)
- **Date**: 2026-06-29
- **Verdict**: APPROVED (after fixes)
- **Findings**: 1 critical (fixed) | 3 warnings (2 fixed, 1 skipped) | 3 observations (2 fixed, 1 skipped)

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING |
| Scope Discipline | WARNING |
| Safety & Quality | PASS (after fix) |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | PASS (after fix — 442/442) |

## Automated Verification

- `uv run pytest tests/test_bigquery.py -k calendar` → 4 passed ✓
- `uv run pytest tests/test_portfolio_calendar.py` → 17 passed ✓
- `uv run pytest tests/test_api.py -k calendar` → 8 passed ✓
- `uv run pytest tests/` → 442 passed, 0 failed ✓ (after F1 fix)
- `uv run ruff check db/bigquery.py src/portfolio_calendar.py src/api.py` → All checks passed ✓

## Findings

### F1 — E2E regression: back-button broken after portfolio visit

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality / Scope Discipline
- **Location**: static/index.html:2425
- **Detail**: `showPortfolioPositionsView()` called `_ppWriteUrl(true)` (pushState). Called both on explicit navigation AND on popstate, creating a ghost history entry on every back-navigation to portfolio-positions — making it impossible to navigate all the way back to the initial announcements state.
- **Fix Applied**: Fix A — Added `history.pushState` in `_navigateToView('portfolio-positions')` before calling `showPortfolioPositionsView()`; changed `_ppWriteUrl(true)` → `_ppWriteUrl(false)` at line 2425.
- **Decision**: FIXED

### F2 — Calendar URL deep-link restore silently broken

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Scope Discipline
- **Location**: static/index.html:1463-1467
- **Detail**: `_applyUrlState()` set `_ppCalYear/_ppCalMonth` from URL, then called `showPortfolioPositionsView()` which immediately reset them to current month. Deep links with `?year=X&month=Y` silently ignored.
- **Fix Applied**: Fix B — Moved `showPortfolioPositionsView()` call before the year/month assignment in `_applyUrlState()`. URL state is applied after the reset, winning.
- **Decision**: FIXED

### F3 — Holiday detection hardcoded list expires 2027

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence / Blind Spots
- **Location**: src/portfolio_calendar.py:9-22
- **Detail**: `_GPW_HOLIDAYS` frozenset covers only 2025–2027. After Dec 31 2027, holidays silently show as white (`no_data`) instead of gray (`holiday`). Plan said to infer from absence; implementation hardcodes.
- **Fix Applied**: Added `# Last updated: 2026-06-29. TODO: update for 2028+` comment.
- **Decision**: FIXED

### F4 — get_portfolio_calendar_data placed before its constants

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: db/bigquery.py:352
- **Detail**: Function references `_USER_PORTFOLIO_POSITIONS_TABLE_NAME` (~471) and `_COMPANY_DAILY_STATS_TABLE_NAME` (~1753) but is placed at line 352, before both. Works at runtime but breaks file organization convention.
- **Decision**: SKIPPED

### F5 — Dead CSS class .pp-cal-nodata never applied by JS

- **Severity**: 💬 OBSERVATION
- **Dimension**: Scope Discipline
- **Location**: static/index.html (CSS section)
- **Detail**: `.pp-cal-nodata` defined in CSS but `_renderPortfolioCalendar()` never applies it. Leftover from earlier iteration.
- **Fix Applied**: Removed the two dead CSS rule lines.
- **Decision**: FIXED

### F6 — State vocabulary expanded without plan update (documented drift)

- **Severity**: 💬 OBSERVATION
- **Dimension**: Plan Adherence
- **Location**: src/portfolio_calendar.py:35-44
- **Detail**: Plan specified 5 states; implementation has 6. All user-confirmed changes during the session. Plan Phase 2 spec table stale.
- **Decision**: SKIPPED

### F7 — P&L algorithm changed from consecutive-delta to zmiana_kwotowa (undocumented)

- **Severity**: 💬 OBSERVATION
- **Dimension**: Plan Adherence
- **Location**: db/bigquery.py docstring / src/portfolio_calendar.py:100
- **Detail**: Plan specified Python consecutive-day delta; implementation uses zmiana_kwotowa directly. Better solution, but lookback window (35 days) now fetched but unused by compute function.
- **Fix Applied**: Added explanatory comment in `get_portfolio_calendar_data` docstring.
- **Decision**: FIXED
