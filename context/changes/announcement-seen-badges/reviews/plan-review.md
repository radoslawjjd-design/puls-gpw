<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Per-item "new" badge clearing (PUL-94)

- **Plan**: context/changes/announcement-seen-badges/plan.md
- **Mode**: Deep
- **Date**: 2026-07-24
- **Verdict**: REVISE → **SOUND after fixes** (all 5 findings fixed in plan)
- **Findings**: 2 critical, 2 warnings, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | WARNING (F1 — fixed) |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | FAIL (F2 — fixed) |
| Plan Completeness | WARNING (F3, F4 — fixed) |

## Grounding

5/5 paths ✓ (static/index.html, tests/e2e/conftest.py, db/bigquery.py, src/api.py, lessons.md), 7/7 symbols ✓ (`_navigateToView` 2543, `_applyUrlState` 2608, `doLogout` 1352, `renderTable` 4344, `openModal` 4430, `modalAttrs` 4377, `currentView` 1293), `test_new_badges.py` correctly absent, `contract-surfaces.md` absent (check skipped), brief↔plan ✓.

Deep verification (1 sub-agent, 5 targeted questions) — key evidence:
- `showAnnouncementsView` (2304) display-toggles only; comment 2540-2542: "x-history always fetches on show, announcements doesn't"; Obserwowane fetch gated by `_watchlistFetched` (2416-2423) → **no re-render on re-entry to either view**.
- `currentView` has 9 writers incl. `.pp-ticker-link` (3259-3268) bypassing the router; fresh deep-link `?view=my-wallet` reaches `_applyUrlState` with `currentView='announcements'` never rendered → false-leave risk real.
- Conftest mock is `return_value=` (ignores paging; 20 rows load-bearing for "Strona 2" tests); `test_refresh` asserts `tr` count == 20; nothing sorts; no test asserts dates/order → bump-one-date safe, append breaks.
- `ticker`/`published_at` present in all row shapes but `| None` in models; `data-seen-key` arrives as `d.seenKey` (DOMStringMap camelCase); `d.date` is a formatted string, not ISO.
- Session-active idiom is the global `role` (`if (!role) return`, popstate 1764-1781); `hasSession` ≠ "dashboard shown".

## Findings

### F1 — View re-entry does not re-render: e2e scenario 3 would fail; stale badges linger in DOM

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: End-State Alignment
- **Location**: Phase 1 change #5 + Phase 2 scenario 3
- **Detail**: Plan assumed "back to Ogłoszenia (refetch + re-render)"; neither view re-fetches nor re-renders on re-entry, so navigate-away clearing would never be visible in-session and the e2e as written fails.
- **Fix A ⭐ Recommended**: Re-render from cache on view re-entry (`renderTable(_annData/_wlData)`, no fetch — watchlist-guard test safe).
  - Strength: Satisfies AC visually and e2e #3 unchanged; zero extra requests.
  - Tradeoff: Touches show*View; needs empty-cache guard.
  - Confidence: HIGH — same pattern as the sort re-render (2186).
  - Blind spot: Confirm `_wlData` holds the last my-wallet page.
- **Fix B**: No re-render; e2e #3 verifies via reload.
  - Strength: Smaller diff. Tradeoff: stale in-session badges, weaker UX. Confidence: HIGH. Blind spot: none.
- **Decision**: FIXED via Fix A — new Phase 1 change #7 + Critical Implementation Details bullet + scenario 3 wording + "What We're NOT Doing" clarification.

### F2 — False "leave" marks a never-rendered view as seen (deep-link, popstate, doLogout)

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 1 changes #5-#6
- **Detail**: `currentView` initializes to `'announcements'` (1293); fresh deep-link to my-wallet, every popstate, and the doLogout reset (1362) would trigger "leave announcements" though the table never rendered — silently clearing badges the user never saw. 9 `currentView` writers, incl. `.pp-ticker-link` bypassing the router.
- **Fix**: Per-view rendered-since-last-mark flag — `renderTable` sets `_viewRendered[key]=true`; `_markViewSeen` no-ops without it and clears it. Hooks can fire liberally.
  - Strength: One mechanism closes the whole class (deep-link, popstate repeats, logout ordering, login screen, all 9 writers).
  - Tradeoff: One more piece of in-memory state (2 booleans).
  - Confidence: HIGH — renderTable is the single source of truth for "badges were on screen".
  - Blind spot: None significant.
- **Decision**: FIXED — flag wired into changes #1 (consume+clear), #3 (set), #5 (spurious calls harmless), #6 (second line of defense) + Critical Implementation Details bullet.

### F3 — Listener guard underspecified; codebase idiom is the `role` global

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 1 change #6
- **Detail**: Plan said "active session" without a mechanism; `hasSession` is true on landing during boot probe. Popstate precedent (1764-1781) uses `if (!role) return`.
- **Fix**: Specify `if (!role) return` guard (popstate idiom) in change #6.
- **Decision**: FIXED.

### F4 — Fixture "append one row" option would break existing tests

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 2 change #1
- **Detail**: `test_refresh` asserts `#table-body tr` count == 20; mock `return_value=` ignores paging so exactly 20 rows is load-bearing for "Strona 2" tests. Bumping one existing row's date verified safe (no sorting, no date/order assertions).
- **Fix**: Remove the append option; unambiguously bump one existing `_FAKE_ADMIN_ROWS` date.
- **Decision**: FIXED.

### F5 — `ticker`/`published_at` nullable — seen-key needs a presence guard

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 1 changes #3-#4
- **Detail**: Both fields are `| None` in the API models; renderTable already tolerates nulls. A row without `published_at` never badges.
- **Fix**: Emit `data-seen-key` only when both `ticker` and `published_at` are present.
- **Decision**: FIXED.
