<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: URL State for JWT Sessions (PUL-84)

- **Plan**: context/changes/url-state-jwt-sessions/plan.md
- **Scope**: Phase 1 of 1 (full plan)
- **Date**: 2026-07-19
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 1 observation (fixed during triage)

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Review context

Reviewed at branch `pul-84-closeout` after squash `a97e82b` (#157) deployed and
prod-verified. Key confirmations:

- Diff == plan exactly: two guard hunks in `static/index.html`, two HTML files deleted,
  4 new JWT e2e tests, calendar un-skip, 3 justified test adaptations. No unaccounted
  files, no drift, no extras.
- Spoofed client-side `role` exposes no data: x-history branch → backend 403 (no logout
  loop — `doLogout` fires only on 401); all data server-gated. URL-state is cosmetic.
- No races: `role` is assigned before `showDashboard` in all four entry paths;
  `doLogout`'s `role = null` → `replaceState('/')` is a synchronous block, so an
  in-flight fetch resuming later hits the `!role` guard as intended.
- No inconsistent gating remains: auth-hash sites are login-screen-only; raw
  `pushState` in `_navigateToView` is dashboard-only; `!apiKey` conditions inside
  `_applyUrlState` are PUL-74 session-type gates, not auth gates — correct.
- Test patterns compliant: role-based locators, zero `wait_for_timeout`, fresh unique
  email per user test, `E2E_ADMIN_EMAIL` shared per documented convention.
- Success criteria re-run at review time: 509 unit / 90 e2e passed, 0 skipped
  (91 e2e after the F1 fix below). Manual rows 1.5-1.7 confirmed by the user on prod
  (deploy `a97e82b`, `/health` 200, `/static/faro-v8.html` 404).

## Findings

### F1 — Back after logout resurrects the previous session's URL params

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: static/index.html:1627 (popstate), :1486-1488, :1386-1388
- **Detail**: After logout, pressing Back walked history to an entry carrying the
  previous session's `?view=…`/filter params (popstate inert while logged out), and
  both login paths preserve `location.search` — so the next login inherited the
  previous session's view and filters, contradicting `doLogout`'s stated intent.
  Cosmetic only: all data server-gated (my-wallet keyed to the new JWT).
- **Fix**: In the popstate listener's logged-out branch, strip `location.search`
  (preserving the hash) — but ONLY for entries carrying `state.view` (created by
  `_writeUrl`/`_navigateToView`). First attempt stripped unconditionally and broke the
  bookmark deep-link flow, caught by `test_portfolio_positions_url_deeplink`: hash
  navigation (`#/logowanie`) also fires popstate with `state == null`, and a
  pre-login `?view=…` must survive to login. The `state.view` discriminator separates
  session-created entries from hash/boot entries exactly.
- **Decision**: FIXED — popstate `state.view`-guarded strip + e2e regression test
  `test_jwt_back_after_logout_does_not_resurrect_previous_session_url`. Full e2e:
  91 passed, 0 skipped.
