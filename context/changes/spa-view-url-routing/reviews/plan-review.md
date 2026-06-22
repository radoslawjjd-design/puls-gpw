<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Admin Dashboard — Per-View URLs and Pagination in Browser History

- **Plan**: `context/changes/spa-view-url-routing/plan.md`
- **Mode**: Deep
- **Date**: 2026-06-22
- **Verdict**: REVISE → SOUND (after fixes applied during triage)
- **Findings**: 1 critical, 0 warnings, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | WARNING (fixed) |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | PASS |
| Plan Completeness | WARNING (fixed) |

## Grounding

8/8 paths ✓ (`static/index.html`, `src/api.py:137` `GET /` → `index.html`,
`tests/e2e/{test_pagination,test_refresh,test_portfolio_treemap,
test_x_post_history,conftest}.py`), 10/10 symbols ✓ (`init:548`,
`popstate:602`, `bindDateToggle:613`, `showAnnouncementsView:860`,
`showXHistoryView:869`, `showTreemapView:884`, `fetchAnnouncements:894` +
`pushState:923-926`, `fetchXPosts:935`, `_xHistoryViewBuilt:694` — all
exact), brief↔plan ✓. Also confirmed via grep: no code outside
`static/index.html` touches `location.search`/`history.state`/`popstate` —
the brief's "Open Risk" is verified false (de-risked).

## Findings

### F1 — Phase 3 leaves a leftover double-pushState on x-history nav

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: End-State Alignment
- **Location**: Phase 1 §1 contract vs. Phase 3 §1 vs. Critical Implementation Details
- **Detail**: Critical Implementation Details states the final-state contract: `_navigateToView()` only writes the URL for branches where no fetch is about to fire (announcements, treemap) — not x-history, since `fetchXPosts()` owns that write once it exists. But Phase 1's contract has `_navigateToView` push `?view=x-history` directly as an interim measure "until Phase 3 lands," and Phase 3's three change items never instructed removing that interim push. Once Phase 3 makes `fetchXPosts(push=true)` write the URL on every call — and `showXHistoryView()` always triggers `fetchXPosts()` — a single click on "Historia postów X" would fire two `pushState` calls (the interim sync push, then the async post-fetch push), requiring two back-presses to leave x-history.
- **Decision**: FIXED — added Phase 3 change item #2 ("Drop the Phase 1 interim push from `_navigateToView`'s x-history branch"), renumbered subsequent items.

### F2 — `_writeUrl()` ends up with only one real caller

- **Severity**: 📝 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Implementation Approach vs. Phase 2 §3 vs. Phase 3 §1
- **Detail**: Implementation Approach frames `_writeUrl()` as the single shared function both `fetchAnnouncements()` and `fetchXPosts()` use. Phase 2 §3 kept `fetchAnnouncements()`'s existing inline pushState/replaceState code rather than routing it through `_writeUrl()`, leaving Phase 3 as the only literal caller — not a functional bug, just an inconsistency between the promised shared helper and what the phases actually specify.
- **Decision**: FIXED — Phase 2 §3 now explicitly introduces `_writeUrl()` and has `fetchAnnouncements()` call it; Phase 3 §1 reuses the now-real shared helper.
