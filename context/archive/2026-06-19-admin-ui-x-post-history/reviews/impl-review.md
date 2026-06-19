<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Admin UI: X post history view

- **Plan**: context/changes/admin-ui-x-post-history/plan.md
- **Scope**: Full plan (Phases 1-5)
- **Date**: 2026-06-20
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical, 3 warnings, 0 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | WARNING |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | WARNING |

## Findings

### F1 — Cross-cutting E2E locator fix not documented in plan

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: tests/e2e/test_pagination.py, test_profile_menu.py, test_refresh.py, test_idle_timeout.py, test_autocomplete.py (commit f3ad3ea)
- **Detail**: Phase 5's plan.md contract only names conftest.py + the new test file as Phase 5 changes. In practice, Phase 3's eager `#x-history-view` DOM construction created a hidden duplicate "Strona N" text node that broke `get_by_text("Strona N", exact=True)` in 5 pre-existing E2E files (Playwright strict-mode violation). Found and fixed mid-session with explicit user sign-off; required for Phase 5's own "full E2E suite still green" criterion. Both review sub-agents confirmed the fix is applied consistently (zero remaining `get_by_text("Strona")` calls in tests/e2e/).
- **Fix**: Add one line to plan.md's Phase 5 "Changes Required" noting this cross-cutting locator fix, so the plan reflects what actually shipped.
- **Decision**: SKIPPED (already an accepted, in-session decision; documented here for the record)

### F2 — Dropdown placeholder/test-option fix not in plan.md

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: static/index.html (commit d89028e)
- **Detail**: User reported "Okno"/"Status" placeholder options and a stray "test" window option visible in production; fixed directly (relabeled placeholders to "Wszystkie okna"/"Wszystkie statusy", dropped "test" option). Confirmed functionally correct — no other code references `value="test"` for this select; `X_WINDOW_LABELS` still maps `test` for legacy-row table display only, unaffected. Never part of plan.md.
- **Fix**: Add a short addendum note to plan.md or change.md recording this post-epilogue UI polish, so the plan stays the accurate record of what shipped.
- **Decision**: SKIPPED (already an accepted, in-session decision; documented here for the record)

### F3 — Full E2E suite has 2 known-flaky failures (tracked separately)

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: tests/e2e/test_idle_timeout.py
- **Detail**: `uv run pytest tests/e2e/` → 2 failed, 26 passed on this run (`test_stay_logged_in_keeps_session_alive_past_original_deadline`, `test_full_idle_triggers_logout_and_clears_session_storage`). Both are the virtual-clock flake root-caused to Phase 3's eager DOM construction, bisected earlier this session, and filed as PUL-49 per explicit user decision to land Phase 5 now and track the flake separately. Not a new regression — re-confirmed it's still isolated to test_idle_timeout.py and didn't spread.
- **Fix**: No action — already tracked as PUL-49. Recorded here so the review's success-criteria check doesn't silently rubber-stamp a suite that, taken literally, isn't 100% green.
- **Decision**: ACCEPTED (tracked as PUL-49, per user decision earlier this session)

## Sub-agent verification summary

- **Plan Drift Detection**: Phase 5 contract (conftest.py + test_x_post_history.py) MATCH. Phases 1-4 backend/frontend code re-checked for regressions from Phase 5/d89028e — MATCH, no drift. f3ad3ea and d89028e both verdicted EXTRA (legitimate, undocumented) — see F1/F2.
- **Safety, Quality & Pattern Compliance**: No CRITICAL/WARNING findings. `list_x_posts_admin`/`GET /admin/x-posts` parameterize all filters, backtick `` `window` `` everywhere required, true 401/403 gating (no filtered-200 leak). `static/index.html`'s x-post modal/table paths escape all dynamic content via `esc()` before innerHTML insertion. Admin-only DOM is never constructed for non-admin sessions (confirmed via `not_to_be_attached` tests). No `page.waitForTimeout()` anywhere in tests/e2e/. Pattern-mirrors sibling `list_announcements_admin`/`GET /announcements` with no substantive mismatch.

## Automated verification (re-run)

- `uv run pytest tests/ --ignore=tests/e2e -q` → 214 passed
- `uv run pytest tests/e2e/ -q` → 2 failed (tracked, see F3), 26 passed
