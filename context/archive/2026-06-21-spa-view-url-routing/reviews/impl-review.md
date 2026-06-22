<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Admin Dashboard — Per-View URLs and Pagination in Browser History

- **Plan**: context/changes/spa-view-url-routing/plan.md
- **Scope**: Full plan (Phases 1–4; deep-dive on Phase 4, since Phases 1–3 each already had their own approved phase review)
- **Date**: 2026-06-22
- **Verdict**: APPROVED (2 minor warnings, both fixed in triage)
- **Findings**: 0 critical, 2 warnings, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | WARNING |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Findings

### F1 — _writeUrl() can re-stamp a stale view URL after logout

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: static/index.html:976 (`_writeUrl`), :1025 / :1074 (call sites in `fetchAnnouncements`/`fetchXPosts`)
- **Detail**: `fetchAnnouncements()`/`fetchXPosts()` call `_writeUrl()` unconditionally after their `await fetch(...)` resolves, with no check that the session is still active. If "Wyloguj" is clicked while a fetch is in flight, `doLogout()` runs first (clears `apiKey`, resets `currentView`, `replaceState('/')`, shows login) — but the in-flight fetch still resolves afterward and calls `_writeUrl()`, overwriting the just-cleared `/` with the stale view's URL while the login screen is showing. Same failure class as the recorded lesson "SPA pagination — out-of-order fetch responses can desync the URL", triggered by logout instead of a double-click.
- **Fix**: Added `if (!apiKey) return;` as the first line of `_writeUrl()` — single shared chokepoint, closes the race for all current/future callers.
- **Decision**: FIXED (commit pending)

### F2 — Two unplanned-but-benign additions, undocumented in plan text

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: static/index.html (`doLogout`), tests/e2e/conftest.py (x-posts fixture padding)
- **Detail**: Phase 4's plan text lists exactly one Changes-Required item (modal-isolation verification, no code change expected). Two more changes landed that aren't written into the plan: the `doLogout()` URL-reset fix (found during this phase's own manual verification) and 20 padding rows added to the x-posts test fixture so pagination has a real page 2. Both well-scoped, low-risk, already covered by their own tests.
- **Fix**: Added an "Addendum (post-implementation)" note under Phase 4's Changes Required section in plan.md documenting both additions and why.
- **Decision**: FIXED (commit pending)

### Observation — No regression test for the F1 race

No test exercises logout-while-fetch-in-flight directly; `test_logout_resets_url_to_root` only logs out once the page is idle. Optional follow-up: a `page.route()`-delayed-response test would close the gap, but F1's guard already fixes the bug at its root (the `_writeUrl` chokepoint), so this is not blocking.
- **Decision**: SKIPPED (guard fix is sufficient; no action taken)
