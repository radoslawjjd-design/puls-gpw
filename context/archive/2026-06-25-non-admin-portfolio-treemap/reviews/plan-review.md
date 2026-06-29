<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Non-admin Portfolio Treemap (PUL-64)

- **Plan**: `context/changes/non-admin-portfolio-treemap/plan.md`
- **Mode**: Deep
- **Date**: 2026-06-28
- **Verdict**: REVISE (was RETHINK; all 3 criticals fixed during triage)
- **Findings**: 3 critical  1 warning  0 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | FAIL → FIXED (F2) |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | FAIL → FIXED (F3, F4) |
| Plan Completeness | FAIL → FIXED (F1) |

## Grounding

7/7 paths ✓ · key symbols ✓ · PUL-65 symbols absent (expected — branch predates merge) · brief↔plan ✓

## Findings

### F1 — Phase 1 success criterion 1.1 can never pass

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 1 change 11 / Progress row 1.1
- **Detail**: Phase 1 change 11 said unit tests are a Phase 1 deliverable but `compute_user_portfolio_treemap_positions()` was implemented in Phase 2. Progress row 1.1 said "pytest passes" — impossible for a nonexistent function. `/10x-implement` would block on 1.1 indefinitely.
- **Fix Applied**: Fix A — moved `compute_user_portfolio_treemap_positions()` to Phase 1 (pure function, zero dependencies). Phase 2 change 1 updated to "no action — import from Phase 1". Phase 2 overview updated. mypy/ruff criteria in 1.2 and 1.3 extended to include `src/portfolio_treemap.py`.
- **Decision**: FIXED via Fix A

### F2 — `TreemapPosition.position_value_pln: float` rejects None at runtime

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: End-State Alignment
- **Location**: Phase 2 step 1 (compute function) vs Phase 2 step 10 (treemap endpoint)
- **Detail**: Existing `TreemapPosition` model at `src/api.py:122` has `position_value_pln: float` (non-nullable). Compute function returns `position_value_pln=None` for no-price positions. Pydantic v2 raises `ValidationError` when `None` is passed to a `float` field — every `GET /api/portfolio/treemap` for a user with an unpriced position returns 500.
- **Fix Applied**: Fix A — Phase 2 step 10 updated with explicit instruction to change `TreemapPosition.position_value_pln` to `float | None` before using it here. Admin endpoint unaffected (always produces float; Pydantic accepts `float` for `float | None`).
- **Decision**: FIXED via Fix A

### F3 — `stopTreemapResizeTracking()` removed but 4 callers not updated

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 3 step 2 — Remove dead treemap globals and functions
- **Detail**: Phase 3 step 2 listed `stopTreemapResizeTracking()` for removal but 4 live callers in non-removed functions were not updated: `doLogout()` line 654, `showAnnouncementsView()` line 1114, `_showXHistoryViewDom()` line 1132, `_showMyWalletViewDom()` line 1210. Would cause `ReferenceError` on any view switch or logout.
- **Fix Applied**: Added explicit sub-bullet to Phase 3 step 2 contract: replace all 4 calls to `stopTreemapResizeTracking()` with `stopPortfolioTreemapResize()` (introduced in step 9) at the listed lines.
- **Decision**: FIXED

### F4 — Phase 1 BQ signature changes break callers; deploy coordination unclear

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Critical Implementation Details / Phase 1 overview
- **Detail**: Phase 1 changes `upsert_user_portfolio_position()` and `delete_user_portfolio_position()` signatures (adding required `portfolio_id`). After master merge, `src/api.py` PUL-65 callers use old signatures. Phase 2 updates callers, but the plan only said "Phase 2 and 3 must ship in the same PR" — Phase 1 wasn't included. Independent Phase 1 CI run would fail with `TypeError`.
- **Fix Applied**: Updated the Critical Implementation Details paragraph to say "All three phases ship in a single PR — Phase 1 changes BQ function signatures, Phase 2 updates the API callers, and Phase 3 updates the frontend; none can land independently without breaking CI."
- **Decision**: FIXED
