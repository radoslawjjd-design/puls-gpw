<!-- PLAN-REVIEW-REPORT -->
# Plan Review: PUL-65 — User Portfolio Positions CRUD

- **Plan**: `context/changes/pul-65/plan.md`
- **Mode**: Deep
- **Date**: 2026-06-27
- **Verdict**: SOUND (after fixes)
- **Findings**: 1 critical | 2 warnings | 2 observations — all fixed

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | WARNING |
| Plan Completeness | FAIL → PASS (after fix) |

## Grounding

6/6 paths ✓, 7/7 symbols ✓, brief↔plan ✓

## Findings

### F1 — Phase 4 Progress section missing #### Manual subsection

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: ## Progress → Phase 4: E2E Tests
- **Detail**: Phase 4 Manual Verification bullet missing from Progress section. `/10x-implement` parses Progress mechanically and would silently miss this step.
- **Fix**: Added `#### Manual` + `- [ ] 4.3 All four E2E test scenarios confirmed passing in CI output` to Phase 4 in Progress.
- **Decision**: FIXED

### F2 — E2E store isolation uses wrong mechanism

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 4 → Change 2 (conftest fake store)
- **Detail**: `live_server_url` is `scope="session"` (`tests/e2e/conftest.py:202`). A `.clear()` call inside the fixture runs once at session start, not per test. Existing isolation is per-test client_id (fresh browser context → new UUID → unique store key), identical to `_watchlist_store` pattern.
- **Fix**: Removed `.clear()` instruction. Updated contract to explain per-test client_id isolation and reference the watchlist precedent.
- **Decision**: FIXED via Recommended Fix

### F3 — `test_positions_show_dashes_when_no_price_data` has no viable mechanism

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 4 → Change 3 (test_portfolio_positions.py)
- **Detail**: `_fake_upsert_user_portfolio_position` always stores `current_price=52.0`. Re-patching `_fake_list_user_portfolio_positions` from within a test is not straightforward with a session-scoped fixture.
- **Fix**: Updated test contract to use direct store injection: `from tests.e2e.conftest import _portfolio_positions_store`, read client_id via `page.evaluate()`, inject a null-price row, then re-navigate to trigger fresh fetch.
- **Decision**: FIXED via Recommended Fix

### F4 — Phase 3 item 7 doesn't describe `showPortfolioPositionsView()` helper

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 3 → Change 7 (navigation binding)
- **Detail**: `_navigateToView` is an if-else chain delegating to per-view functions. Plan only described the click listener, not the required `showPortfolioPositionsView()` helper function.
- **Fix**: Added to Phase 3 item 7 contract: description of `showPortfolioPositionsView()` function and the `else if (view === 'portfolio-positions')` branch in `_navigateToView`.
- **Decision**: FIXED

### F5 — `_applyUrlState` not mentioned; URL persistence scope unclear

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Architectural Fitness
- **Location**: Phase 3 (navigation)
- **Detail**: `_navigateToView` has sibling `_applyUrlState` at line 1299. Consistent with "my-wallet" which also doesn't use URL state, but should be explicit.
- **Fix**: Added to "What We're NOT Doing": URL state persistence for portfolio-positions, noted as consistent with my-wallet.
- **Decision**: FIXED
