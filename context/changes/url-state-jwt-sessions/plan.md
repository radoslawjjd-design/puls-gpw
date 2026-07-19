# URL State for JWT Sessions Implementation Plan

## Overview

Un-gate SPA URL-state (writes + back/forward) for JWT cookie sessions by replacing the two
`!apiKey` guards with a session-presence check (`!role`), consolidate the duplicated HTML
files into a single `static/index.html`, and prove the behavior with e2e tests — including
un-skipping the calendar URL test that waits on this change.

Tracking: Linear PUL-84, GitHub #148.

## Current State Analysis

Verified directly in code (2026-07-19):

- **Restore path already works for JWT.** `showDashboard()` calls `_applyUrlState()`
  unconditionally (`static/index.html:1835`); both auth paths preserve `location.search`
  into the dashboard (`:1388` boot-probe, `:1488` `_enterUserSession`). `_applyUrlState`
  (`:2216-2247`) already has post-PUL-74 branches: `portfolio-positions`/`my-wallet` apply
  for `!apiKey` (JWT), `x-history` for `role === 'admin'`.
- **Write path is dead for JWT.** `_writeUrl` early-returns on `!apiKey` (`:2256`), the
  `popstate` listener early-returns on `!apiKey` (`:1624`). Result: the address bar never
  updates for JWT users, so reload has nothing to restore and back/forward is inert.
- **Existing desync bug (fixed for free by the un-gate).** `_navigateToView` (`:2161`)
  writes `?view=portfolio-positions` via direct `pushState` with no guard — a JWT user gets
  a history entry entering Portfel, but pressing back only changes the URL (popstate
  returns early), leaving the view out of sync with the address bar.
- **The guards have a real purpose that must survive.** Per the comment at `:2252-2254`,
  the `_writeUrl` guard prevents a fetch that was in flight during logout from writing its
  URL after `doLogout()` reset the address bar; the popstate guard keeps URL-state inert on
  the login screen. `doLogout()` nulls both `apiKey` and `role` (`:1253`), so `role` is an
  equivalent post-logout sentinel that covers BOTH auth paths.
- **HTML duplication.** `static/faro-v8.html` is byte-identical to `static/index.html`
  (same git blob `d9d88a2`); `static/index_old.html` is the pre-faro backup. The API
  serves only `static/index.html` (`src/api.py:264`) plus a generic `/static` mount
  (`src/api.py:801`). No code references `faro-v8.html` or `index_old.html` (only a
  prose comment in `tests/e2e/test_autocomplete.py:44`).
- **E2E baseline.** `tests/e2e/test_url_routing.py` covers the API-key path (regression
  suite for this change). `tests/e2e/test_portfolio_calendar.py:110` has
  `test_calendar_url_contains_tab_calendar_after_switch` skipped with reason "PUL-84".
  JWT login helpers exist in conftest (`e2e_login_email`, `e2e_unique_email`, PUL-74).

## Desired End State

- A JWT user (email login, admin or user role) sees the URL update as they navigate and
  filter; reload restores the active view + params; browser back/forward walks the view
  history correctly.
- The API-key session behaves exactly as today (regression-locked by the existing
  `test_url_routing.py`).
- After logout, no in-flight fetch can write a URL, and popstate on the login screen stays
  inert (guard semantics preserved via `role`).
- `static/` contains exactly one dashboard HTML: `index.html` (content identical to
  today's `faro-v8.html` == today's `index.html`). `faro-v8.html` and `index_old.html`
  are gone.
- The calendar URL e2e test runs again (un-skipped) and passes.

### Key Discoveries:

- `role` is set before `showDashboard()` in both auth paths (`:1385`, `:1478`) and nulled
  in `doLogout()` (`:1253`) — a ready-made "session active" sentinel; no new state needed.
- The whole fix for writes + back/forward is two guard lines; everything downstream
  (`_applyUrlState`, param restore helpers) already handles JWT.
- `_navigateToView`'s direct `pushState` calls need no guard — they're click handlers
  reachable only from the visible dashboard.

## What We're NOT Doing

- No auth/role changes (PUL-83 delivered those) and no backend/API changes at all.
- No new views, params, or URL schema changes — JWT gets exactly the URL-state API-key
  sessions have today.
- Not touching the `#/logowanie`/`#/rejestracja` hash routing on the login screen.
- Not restructuring `_applyUrlState`'s per-view role/session branches (PUL-74 semantics
  stand: per-user views apply only for JWT, x-history only for admins).
- Not adding the full view×auth×navigation e2e matrix — coverage is scoped to the flows
  this change alters (plus the API-key regression suite that already exists).
- `index_old.html` removal happens here; the rest of the post-PUL-74 cleanup (DROP
  `watchlist.client_id`, dual-write/backfill, vestigial `X-API-Key` headers — impl-review
  F4) stays in the separate ~2026-07-26 chore.

## Implementation Approach

Smallest possible production diff: swap the two `!apiKey` guards to `!role` and update the
comments that explain them (they currently describe the API-key-only rationale). Delete the
two redundant HTML files in the same phase so the e2e suite proves the single remaining
`index.html` end-to-end. Tests are the bulk of the work: new JWT URL-state tests in
`test_url_routing.py`, a desync regression, and the calendar un-skip.

## Phase 1: URL-state for JWT + HTML consolidation + E2E

### Overview

Make URL writes and popstate work for any authenticated session, collapse the HTML
duplication, and lock the behavior with e2e tests for both auth paths.

### Changes Required:

#### 1. Guard swap in `_writeUrl` and `popstate`

**File**: `static/index.html`

**Intent**: Replace the session check `!apiKey` with `!role` in `_writeUrl` (`:2256`) and
the `popstate` listener (`:1624`) so both auth paths get URL-state, while the post-logout
protection keeps working (`doLogout` nulls `role`). Update the two adjacent comments —
they currently justify the guard in API-key terms; they must now say "any authenticated
session (role is set in both auth paths, nulled on logout)".

**Contract**: `_writeUrl(view, params, push)` is a no-op iff `role == null` (logged out /
login screen). `popstate` → `_applyUrlState()` iff `role != null`. No other behavior
change; `_applyUrlState` branches stay as they are.

#### 2. HTML consolidation — one `index.html`

**File**: `static/faro-v8.html`, `static/index_old.html` (delete)

**Intent**: `faro-v8.html` is byte-identical to `index.html` and `index_old.html` is a
stale pre-faro backup; both exist only as historical artifacts and cause naming confusion.
Remove them with `git rm` (history preserved). The final state is a single
`static/index.html`. Note: the preview URL `/static/faro-v8.html` will 404 from now on —
intended.

**Contract**: `ls static/*.html` → exactly `index.html`. `src/api.py` needs no change
(it reads `static/index.html` and mounts `/static` generically).

#### 3. E2E — JWT URL-state + desync regression

**File**: `tests/e2e/test_url_routing.py`

**Intent**: Extend the existing URL-routing suite with the JWT flows this change enables.
Follow `/10x-e2e` hard rules (role/label/text locators, no `waitForTimeout`, independent
setup per test) and the conftest JWT helpers (`e2e_login_email` + `e2e_unique_email`,
fresh uid per user-role test). New tests:

- (a) JWT user: navigate to Obserwowane → URL gains `?view=my-wallet`; reload → view and
  params restored (not dumped to Ogłoszenia).
- (b) JWT user: Portfel (`?view=portfolio-positions`) → back → view returns to Ogłoszenia
  AND URL is `/` (regression for today's URL↔view desync).
- (c) JWT admin: X-historia deep link with a param (e.g. `page=2`) → reload restores view
  + param; back/forward walks between x-history and announcements.
- (d) JWT user: after logout, address bar is `/` and re-login lands on Ogłoszenia (no
  stale view resurrection — guards + doLogout's replaceState still cooperate).

**Contract**: Existing API-key tests in this file stay untouched and green (they are the
API-key regression required by the Linear scope). Assertions on `page.url` via
`expect(page).to_have_url(...)` / `wait_for_url` — no timeouts.

#### 4. Un-skip the calendar URL test

**File**: `tests/e2e/test_portfolio_calendar.py`

**Intent**: Remove the `@pytest.mark.skip(reason="PUL-84: ...")` decorator (`:110-113`)
from `test_calendar_url_contains_tab_calendar_after_switch`; the behavior it asserts
(calendar tab writes `tab=calendar` into the URL on a JWT session) starts working with
change #1. Adjust the test body only if its pre-PUL-74 login/setup is stale — verify,
don't assume.

**Contract**: Test runs (no skip marker) and passes in the full e2e suite.

### Success Criteria:

#### Automated Verification:

- Unit suite passes: `uv run pytest tests/ --ignore=tests/e2e -q`
- Full e2e suite passes with 0 skipped: `uv run pytest tests/e2e -q`
- New JWT URL-state tests green (deep-link restore, back/forward, desync regression,
  post-logout inertness)
- `ls static/*.html` shows only `index.html`; `git grep -l "faro-v8.html\|index_old"`
  finds no code references (comment in test_autocomplete.py may stay)

#### Manual Verification:

- On prod after deploy (JWT email account): navigate views + filters → URL updates;
  reload restores the view; back/forward works; logout resets to `/`
- API-key admin session: spot-check that URL-state behaves as before
- `/static/faro-v8.html` returns 404 (expected post-consolidation)

**Implementation Note**: After completing this phase and all automated verification
passes, pause for manual confirmation before closing out.

---

## Testing Strategy

### Unit Tests:

- No backend change — existing unit suite runs as a no-regression gate only.

### Integration Tests:

- E2E: four new JWT URL-state tests (above) + un-skipped calendar test + existing
  API-key `test_url_routing.py` suite as regression.

### Manual Testing Steps:

1. Prod, email login (user role): Obserwowane → reload → still Obserwowane; back →
   Ogłoszenia (view AND URL).
2. Prod, email login (admin): X-historia page 2 → reload → restored; back/forward walks
   history.
3. Prod, API-key login: unchanged behavior sanity check.
4. Logout in each case → address bar `/`, login screen, no stale view after re-login.

## References

- Change notes / origin: `context/changes/url-state-jwt-sessions/change.md`
- Linear PUL-84 (scope), impl-review F3 of PUL-83 (origin)
- Guard comments being replaced: `static/index.html:2252-2254`, `:1623-1626`
- Session sentinel evidence: `static/index.html:1253` (doLogout), `:1385`, `:1478`
- Lesson checked: "SPA pagination — out-of-order fetch responses can desync the URL"
  (`context/foundation/lessons.md`) — no new fetch-driven URL writes are added here, the
  existing sequencing rules are untouched.

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: URL-state for JWT + HTML consolidation + E2E

#### Automated

- [x] 1.1 Unit suite passes: `uv run pytest tests/ --ignore=tests/e2e -q`
- [x] 1.2 Full e2e suite passes with 0 skipped: `uv run pytest tests/e2e -q`
- [x] 1.3 New JWT URL-state tests green (restore, back/forward, desync, post-logout)
- [x] 1.4 `static/*.html` == `index.html` only; no code references to removed files

#### Manual

- [ ] 1.5 Prod JWT session: URL updates, reload restores, back/forward works, logout resets
- [ ] 1.6 Prod API-key session: URL-state unchanged (regression spot-check)
- [ ] 1.7 `/static/faro-v8.html` → 404 (expected)
