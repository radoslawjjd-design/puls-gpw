<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Admin Dashboard — Per-View URLs and Pagination in Browser History

- **Plan**: context/changes/spa-view-url-routing/plan.md
- **Scope**: Phase 1 of 4
- **Date**: 2026-06-22
- **Verdict**: REJECTED → fixed during triage, all green after fix
- **Findings**: 1 critical

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | FAIL (pre-fix) → fixed |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Findings

### F1 — _applyUrlState() crashes for non-admin users on admin-only view URLs

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: static/index.html:910-923
- **Detail**: `injectAdminOnlyChrome(r)` only builds `#treemap-view`/`#x-history-view`/`#treemap-btn`/`#x-history-btn` for `r === 'admin'`. Before Phase 1, `showTreemapView()`/`showXHistoryView()` were only reachable via those admin-only buttons, so a non-admin session could never call them. `_applyUrlState()` calls them unconditionally based on the URL's `view=` param, and is invoked from both `showDashboard()` (any role, on login/refresh) and the `popstate` listener (any role, on back/forward). A non-admin user loading `?view=treemap` or `?view=x-history` (bookmark, shared link, or guess) hits a null DOM lookup (`$('treemap-view').style.display = ''` where `$()` returns `null`) → `TypeError`, aborting `showDashboard()` and breaking the whole dashboard render.
- **Fix**: Guard the treemap/x-history branches in `_applyUrlState()` with `role === 'admin'`, falling through to the announcements default otherwise. `role` is already the correct global by the time `_applyUrlState()` runs.
- **Decision**: FIXED — guard added at static/index.html:912,915. Full suite re-run green (276 passed).
