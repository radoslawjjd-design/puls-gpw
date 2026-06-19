<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Admin UI: X post history view

- **Plan**: `context/changes/admin-ui-x-post-history/plan.md`
- **Mode**: Deep
- **Date**: 2026-06-19
- **Verdict**: SOUND (after fixes; was REVISE)
- **Findings**: 2 critical, 1 warning, 0 observations — all fixed during triage

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | FAIL (pre-fix) → PASS (post-fix) |
| Plan Completeness | PASS |

## Grounding

6/6 paths ✓ (`db/bigquery.py`, `src/api.py`, `static/index.html`,
`tests/e2e/conftest.py`, `tests/e2e/test_profile_menu.py`,
`context/foundation/lessons.md`), 8/8 symbols ✓ (`list_announcements_admin`,
`_build_filter_clauses`, `_require_admin`, `showDashboard`, `renderHeaders`,
`fetchAnnouncements`, `openModal`, `popstate` handler), brief↔plan ✓

## Findings

### F1 — Reserved-keyword `window` column unbacked in new SQL

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 1, Changes Required #1 (BigQuery query function)
- **Detail**: The contract wrote the filter clause as `window = @window` with no mention of backticking. `window` is a BigQuery reserved keyword, and this exact bug already happened once on this table — `context/foundation/lessons.md` documents a PUL-29 incident (`x_posts.window` in an unbacked INSERT → `400 Syntax error: Unexpected keyword WINDOW`). The codebase's existing queries on `x_posts` already backtick it correctly (`db/bigquery.py:695,805`); the new plan's contract text didn't carry that detail forward.
- **Fix**: Updated Phase 1's contract to read `` `window` = @window `` in the WHERE clause, noted the SELECT list must also backtick `window`, and added a unit-test note (Testing Strategy) asserting the backtick is present in the emitted SQL, mirroring the lesson's recommended regression test.
- **Decision**: FIXED

### F2 — New view's interactive elements have no specified IDs; `$()` is `getElementById`, so reusing existing IDs breaks both views

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 3, Changes Required #2 (filter form + table + pagination markup)
- **Detail**: `static/index.html:332` defines `const $ = id => document.getElementById(id)` — the only DOM-lookup mechanism in the file. `getElementById` silently resolves to the *first* matching element when IDs collide. Phase 3 described the x-history filter/table/pagination markup only by class and said the page-size select should match "the existing one," never assigning distinct IDs. If implemented literally with reused IDs (`filter-form`, `f-page-size`, `btn-prev`, `btn-next`, `page-label`, `table-head`, `table-body`), every `$()` call inside the new `fetchXPosts()` would silently resolve to the announcements view's elements instead (first in DOM order, per Phase 2's own contract that `#x-history-view` is appended after `#announcements-view`).
- **Fix**: Added an explicit unique-ID requirement to Phase 3's contract — every injected interactive element now gets an `xp-`-prefixed ID (`xp-filter-form`, `xp-f-page-size`, `xp-btn-prev`, `xp-btn-next`, `xp-page-label`, `xp-table-head`, `xp-table-body`), with `fetchXPosts()`/its handlers explicitly required to reference only those, plus a callout explaining the `getElementById` collision risk.
- **Decision**: FIXED

### F3 — Existing global `popstate` handler doesn't know about the new view

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 2 / Phase 3 (view-toggle functions, no-pushState decision)
- **Detail**: The plan correctly decided the new view itself won't call `history.pushState`. But it didn't account for the *existing* global `popstate` listener (`static/index.html:548-556`), which fires on any browser back/forward and unconditionally calls `fetchAnnouncements(false)` with no check on which view is currently visible. If an admin paginates the announcements table (pushing history state), opens "Historia postów X," then presses the browser Back button, `popstate` refetches/re-renders the hidden announcements table while `#x-history-view` stays visible — the screen doesn't visibly change, leaving a stale/confusing state.
- **Fix**: Added a `showAnnouncementsView()` call into the `popstate` handler (alongside the existing `fetchAnnouncements(false)`) so any back/forward navigation always lands back on the announcements view, plus a new manual-verification step (2.6) covering this scenario.
  - Strength: One-line addition to an existing handler; no new state tracking needed since the announcements view is the only view that ever participates in history.
  - Tradeoff: None significant.
  - Confidence: HIGH — grounded directly in the existing handler code and Phase 2's own toggle functions.
  - Blind spot: Not yet manually verified in a live browser — covered by the new manual-verification step 2.6, to be confirmed during implementation.
- **Decision**: FIXED
