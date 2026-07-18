# Onboarding Landing + Login/Register (PUL-72) — Implementation Plan

## Overview

Replace the bare API-key login screen with an onboarding landing page: hero + 3 top-score
announcement cards (new public endpoint `GET /api/public/top-announcements`, 60s cache) +
email/password register & login forms wired to the PUL-71 auth endpoints. "Mam klucz API"
stays as a secondary path behind a link. First UI consumer of the PUL-71 auth foundation.

Tracking: Linear PUL-72, GitHub #128.

## Current State Analysis

From `research.md` (commit cbe93d8, verified with grep counts):

- PUL-71 auth backend (`src/auth.py`, 396 lines) is complete, tested, deployed — and
  **entirely unused by the UI**. Routes: register/login/logout/me under `/api/auth`,
  Polish error messages, field-level 422s.
- Session = HttpOnly cookie `session` (SameSite=Lax, TTL 7d, sliding refresh) — JS cannot
  read it; only `GET /api/auth/me` can answer "am I logged in?".
- `_get_role` (src/api.py:103-118) is **cookie-first** — all 22 inline `X-API-Key` fetch
  call sites work unchanged for JWT users; integration cost is concentrated in boot,
  forms, and logout.
- Login screen `#login-screen` (static/index.html:792-807) is a clean greenfield: API-key
  input `#api-key-input` labeled "Klucz API", handler at :1127-1140, boot gate
  `if (apiKey && role)` at :1119-1124, `doLogout()` at :996-1014 (purely client-side).
- No score-sorted public query exists; the only score-ordered template is
  `fetch_top_n_for_window` (db/bigquery.py:1271-1334).
- `_perf_get/_perf_set` cache with per-call TTL exists; 60s precedent `"admin:treemap"`
  (src/api.py:472/505).
- E2E: every BQ function used by an endpoint must be patched in
  `tests/e2e/conftest.py:343-433` `_patches`; auth is already fully mocked
  (JWT_SECRET, firebase create_user, verify_password_rest). ~10 e2e files log in via
  `get_by_label("Klucz API")` — the input must stay reachable.
- GDPR notice at static/index.html:815 claims "no cookies" — becomes false with PUL-72
  (update explicitly delegated by the PUL-71 plan).

## Desired End State

- Anonymous visitor sees a landing page: hero, 3 announcement cards (company, ticker,
  title, event type, date, PL summary — **no score, no sentiment**), auth panel with tabs
  "Zaloguj się" / "Załóż konto", and a "Mam klucz API" link revealing the API-key form.
- Register → immediately in the dashboard (role `user`), no second login.
- Login/logout round-trips work; refresh keeps a logged-in JWT user in the dashboard
  without a landing-page flash; expired session falls back to landing cleanly.
- API-key path (admin and user) works exactly as before, one click deeper.
- Full e2e suite green, including a new landing/auth test file.

### Key Discoveries:

- Backend already sets the session cookie on `POST /api/auth/register` — auto-login after
  registration is free (src/auth.py:337-360).
- Password rule is min 8–128 **plus ≥1 letter and ≥1 digit** (src/auth.py:33-56) — ticket
  only said "min 8"; the form hint must state the real rule.
- Public route = omit `Depends(_get_role)`; precedent `/health` (src/api.py:280). No CORS
  needed (same-origin only).
- `_clear_caches` autouse fixture (tests/test_api.py:18-25) wipes `_PERF_CACHE` between
  unit tests — the 60s cache won't leak across tests.

## What We're NOT Doing

- **No `analysis_score` on public cards or in the public endpoint response** (user
  decision 2026-07-18: admin-only convention stands; score is used server-side for
  ordering only).
- No sentiment exposure anywhere public (strip as everywhere else).
- No guest mode (PUL-73), no per-user data isolation (PUL-74).
- No email verification, password reset, or profile page — auth surface stays as PUL-71
  shipped it.
- No central fetch wrapper refactor for the 22 X-API-Key call sites (works as-is via
  cookie-first `_get_role`).
- No e2e login-helper centralization into conftest (mechanical per-file update only —
  scope decision 2026-07-18).
- No rate-limit changes; 60s cache bounds BQ load on the public endpoint.

## Implementation Approach

Three phases, each independently green and committable:

1. **Backend** — new BQ query + public endpoint + cache + unit tests + e2e mock.
2. **Landing UI** — rebuild `#login-screen` into the landing (cards wired to the new
   endpoint, tabs present, API-key behind a click) and update all e2e login helpers in
   the same phase so the suite stays green. Forms render but are wired in Phase 3.
3. **Auth wiring** — form submits, inline validation, error rendering, `hasSession`
   boot probe, logout call, plus the new e2e auth test file.

The e2e helper update cannot be deferred to a later "test phase": hiding the API-key
input behind a click breaks every suite's `_login` helper the moment Phase 2 lands.

## Critical Implementation Details

**Score containment**: the no-score decision is enforced at the DB layer — the new BQ
function does not SELECT `analysis_score` (ORDER BY may reference a non-selected column).
The endpoint parses `structured_analysis` server-side and returns only `summary_pl` as
`summary`; the raw JSON (which contains `sentiment`) never reaches the client.

**Boot ordering & flag desync**: `init()` checks sessionStorage (API-key path,
synchronous) first; only when absent and `localStorage.hasSession` is set does it probe
`GET /api/auth/me` behind a loader. A 401 from the probe MUST clear `hasSession` before
showing the landing — otherwise an expired session loops the loader on every boot.
Anonymous visitors (no flag) render the landing immediately with zero auth requests.

**Double-submit guard**: disable the submit button synchronously in the submit handler
before the fetch (lessons.md SPA out-of-order rule — the disable must precede the
request, not live inside the async body after state is read).

**JWT session has no apiKey**: after login/register, sessionStorage gets `role: "user"`
but no `apiKey`. Existing call sites send `X-API-Key: null` headers — harmless, because
`_get_role` reads the cookie first. `doLogout()` must call `POST /api/auth/logout`
(fire-and-forget with `keepalive` is fine) AND clear `hasSession` in addition to its
existing PUL-82 resets.

## Phase 1: Backend — public top-announcements endpoint

### Overview

Ship `GET /api/public/top-announcements`: 3 highest-score approved announcements from the
last 90 days, no auth, 60s in-memory cache, no score/sentiment in the response.

### Changes Required:

#### 1. BQ query

**File**: `db/bigquery.py`

**Intent**: New function `list_top_announcements_public(limit: int = 3)` returning the
top-score approved announcements for the landing cards. Modeled on
`fetch_top_n_for_window` (db/bigquery.py:1271-1334) but without ticker dedup/min-score.

**Contract**: SELECT `company, ticker, title, event_type, published_at,
structured_analysis` (NO `analysis_score` in the select list — ordering only). WHERE
`analysis_approved = TRUE AND analysis_score IS NOT NULL AND event_type != 'inne'` and
`published_at` within the last 90 days (freshness window, matching
`_build_filter_clauses` default at db/bigquery.py:1360-1363). ORDER BY
`analysis_score DESC, published_at DESC` LIMIT `@limit`. Parameterized query, docstring
states the score-containment rationale.

#### 2. Public endpoint + response model

**File**: `src/api.py`

**Intent**: `GET /api/public/top-announcements` with NO auth dependency, serving the
landing cards from a 60s `_perf` cache.

**Contract**: New Pydantic model (e.g. `PublicAnnouncement`) with exactly:
`company, ticker, title, event_type, published_at, summary` — `summary` extracted from
`structured_analysis.summary_pl` via the existing parse helper; everything else from the
raw JSON is dropped server-side. Cache: `_perf_get("public:top-announcements", ttl=60)` /
`_perf_set` (precedent src/api.py:472/505; no per-client key — response is identical for
everyone). Route defined WITHOUT `Depends(_get_role)` (precedent `/health`,
src/api.py:280).

#### 3. Unit tests

**File**: `tests/test_api.py`

**Intent**: Lock the public contract and the SQL string.

**Contract**: With `list_top_announcements_public` patched:
- request without any auth headers → 200 (model: `test_health_no_auth_returns_200`);
- response items contain exactly the public field set — assert `analysis_score`,
  `sentiment`, and raw `structured_analysis` are absent;
- second call within TTL does not re-call the patched BQ function (cache hit);
- query-string regression (lessons.md): `analysis_score` appears in ORDER BY and NOT in
  the SELECT list; `analysis_approved` in WHERE.

#### 4. E2E mock registration

**File**: `tests/e2e/conftest.py`

**Intent**: The new BQ function MUST join `_patches` (conftest rule, lines 343-433) —
from Phase 2 every e2e page load hits this endpoint, and an unmocked function would hit
real BigQuery.

**Contract**: `patch("src.api.list_top_announcements_public")` returning 3 fake rows
with realistic fields (dynamic `published_at` within the window — lessons from PUL-82:
never hardcode dates that age out; `structured_analysis` JSON containing `summary_pl`
AND a `sentiment` key, so e2e can prove sentiment never renders).

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/ --ignore=tests/e2e -q`
- Public-contract tests green (no-auth 200, field set without score/sentiment, cache hit)
- Query-string regression assert green (score in ORDER BY only, approved filter present)
- Full e2e suite still green: `uv run pytest tests/e2e -q`

#### Manual Verification:

- BQ round-trip (lessons.md — mocked tests don't validate SQL): call the endpoint locally
  against real BigQuery and confirm 3 rows with non-empty summaries, no score/sentiment
  keys in the JSON

**Implementation Note**: After this phase and all automated verification passes, pause
for manual confirmation before proceeding to Phase 2.

---

## Phase 2: Landing page UI + e2e suite kept green

### Overview

Rebuild `#login-screen` into the landing page (hero, cards, tabbed auth panel, API-key
form behind "Mam klucz API") and update every e2e login helper in the same phase.
Login/register forms render but submit is wired in Phase 3.

### Changes Required:

#### 1. Landing markup + styles

**File**: `static/index.html`

**Intent**: Replace the contents of `#login-screen` (lines 792-807) with the landing
layout, single-section with display-toggles (SPA convention): hero (app name, tagline,
value proposition), cards strip, auth panel with tabs "Zaloguj się" / "Załóż konto"
(email + password fields, password hint "min. 8 znaków, w tym litera i cyfra"), and a
"Mam klucz API" link that toggles the existing API-key form (input keeps its
"Klucz API" label — e2e contract). Light + dark theme styles (existing dark overrides
pattern at :737-763).

**Contract**: `#login-screen` remains the single sibling section toggled against
`#dashboard-screen`; `#api-key-input`, `#login-btn`, `#login-error` and the "Klucz API"
label survive (hidden until the link is clicked). Tab switching is pure display-toggle,
no URL state.

#### 2. Cards fetch

**File**: `static/index.html`

**Intent**: On `showLogin()`, fetch `GET /api/public/top-announcements` (no headers) and
render the 3 cards; on error/empty, hide the cards strip gracefully (landing must render
fine without them).

**Contract**: Card shows company, ticker, event-type badge (existing badge classes),
date (existing PL date formatting), title, summary. No score, no sentiment anywhere in
the DOM.

#### 3. GDPR notice update

**File**: `static/index.html`

**Intent**: The ":815 no-cookies" claim is false once login sets the session cookie.
Reword: the app uses a single essential session cookie for login, no tracking/analytics
cookies.

**Contract**: Notice text only — no consent banner (essential cookies don't require
consent under GDPR/ePrivacy).

#### 4. E2E login helpers + login UX tests

**Files**: `tests/e2e/test_login_ux.py` + every e2e file with a `_login`-style helper
(~10 files, e.g. `tests/e2e/test_x_post_history.py:5-13`)

**Intent**: Each helper gains one click on "Mam klucz API" before filling
`get_by_label("Klucz API")` — mechanical, identical change per file.
`test_login_ux.py` assertions move to the new landing selectors (role/label/text
locators, per /10x-e2e rules).

**Contract**: `uv run pytest tests/e2e -q` fully green at the end of this phase.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/ --ignore=tests/e2e -q`
- Full e2e suite green after helper updates: `uv run pytest tests/e2e -q`

#### Manual Verification:

- Local visual check (port-8123 pattern): landing renders in light + dark, mobile
  viewport sane, cards populated, tabs switch, "Mam klucz API" reveals the form and
  admin API-key login reaches the dashboard

**Implementation Note**: After this phase and all automated verification passes, pause
for manual confirmation before proceeding to Phase 3.

---

## Phase 3: Auth wiring + boot probe + e2e auth coverage

### Overview

Wire the forms to `POST /api/auth/register|login`, add the `hasSession` boot probe and
logout call, render backend errors, and cover the flows in a new e2e file.

### Changes Required:

#### 1. Register/login submit handlers

**File**: `static/index.html`

**Intent**: Register: inline validation first (email format; password min 8 + ≥1 letter
+ ≥1 digit — mirrors src/auth.py:33-56), then `POST /api/auth/register`; on 200 set
`localStorage.hasSession = "1"`, `sessionStorage.role = "user"`, and go straight to
`showDashboard('user')` (backend already set the cookie). Login: same flow via
`POST /api/auth/login`. Disable the submit button synchronously before the fetch.

**Contract**: Error rendering per status: 422 → field-level messages from
`detail[].loc`/`msg` under the matching inputs; 401/409/429/503 → the backend's Polish
`detail` string in the form's error area. Buttons re-enable on any failure.

#### 2. Boot probe

**File**: `static/index.html`

**Intent**: Extend `init()` (:1119-1124): API-key sessionStorage path unchanged and
first; else if `localStorage.hasSession` → show a minimal loader, probe
`GET /api/auth/me`; 200 → `showDashboard('user')`, anything else → remove `hasSession`,
`showLogin()`. No flag → `showLogin()` immediately (zero auth requests for anonymous
visitors).

**Contract**: A logged-in JWT user refreshing the page lands in the dashboard without a
landing flash; an expired session shows the landing (not a loader loop).

#### 3. Logout integration

**File**: `static/index.html`

**Intent**: `doLogout()` (:996-1014) additionally calls `POST /api/auth/logout`
(clears the HttpOnly cookie) and removes `localStorage.hasSession`, keeping all existing
resets (PUL-82 watchlist state included).

**Contract**: After logout, a page refresh shows the landing; the session cookie is
gone from the browser.

#### 4. E2E auth tests

**File**: `tests/e2e/test_landing_auth.py` (new)

**Intent**: Full-scope coverage (user decision 2026-07-18) against the conftest auth
mocks (firebase create_user / verify_password_rest already stubbed):
- landing shows 3 cards from the mock; no sentiment/score text in the cards' DOM;
- register → dashboard (user role) without re-login;
- login → dashboard; wrong password → Polish error visible, still on landing;
- logout → landing; reload stays on landing (hasSession cleared);
- API-key path regression: link → form → admin login works.

**Contract**: /10x-e2e hard rules — role/label/text locators, no `waitForTimeout`,
independent setup/cleanup, unique ids where state is created.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/ --ignore=tests/e2e -q`
- Full e2e suite green incl. new file: `uv run pytest tests/e2e -q`

#### Manual Verification:

- Local end-to-end with real Firebase (env from `.env`, port-8123 pattern): register a
  fresh email → dashboard; logout; login again; refresh keeps the session; wrong
  password shows the backend message
- Post-merge prod check (CI deploys on master merge; verify `/health` first): register +
  login + logout on production, cards visible on the landing

---

## Testing Strategy

### Unit Tests:

- Public endpoint: no-auth 200, exact field set (no score/sentiment/raw JSON), cache
  hit, query-string regression (ORDER BY score, SELECT without score)

### Integration Tests:

- E2E: full suite green after helper updates (Phase 2); new `test_landing_auth.py`
  covering cards, register, login, wrong password, logout, API-key path (Phase 3)

### Manual Testing Steps:

1. Phase 1: local BQ round-trip of the endpoint (3 rows, clean field set)
2. Phase 2: visual landing check light/dark/mobile + admin API-key login
3. Phase 3: local Firebase register/login/logout round-trip; prod verification
   post-merge

## Performance Considerations

- 60s `_perf` cache caps BQ load from the public endpoint regardless of landing traffic;
  per-instance cache across 2 Cloud Run instances is acceptable at this TTL.
- Anonymous boot issues zero auth requests (flag-gated probe) — preserves the PUL-60
  sub-second perceived-load work.

## Migration Notes

- No schema changes, no data migration. New endpoint is additive; the API-key flow is
  untouched server-side.
- Rollback = revert the PR; no state to clean up (hasSession flag is inert without the
  probe code).

## References

- Ticket: Linear PUL-72 / GitHub #128
- Research: `context/changes/login-register-landing/research.md`
- Auth foundation: `context/archive/2026-07-17-pul-71-auth-foundation/plan.md`
- Score-query template: `db/bigquery.py:1271-1334`
- Cache precedent: `src/api.py:472/505`
- Lessons applied: BQ round-trip + query-string regression; synchronous control disable
  (`context/foundation/lessons.md`)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Backend — public top-announcements endpoint

#### Automated

- [x] 1.1 Unit tests pass: `uv run pytest tests/ --ignore=tests/e2e -q` — 74361de
- [x] 1.2 Public-contract tests green (no-auth 200, field set without score/sentiment, cache hit) — 74361de
- [x] 1.3 Query-string regression assert green (score in ORDER BY only, approved filter present) — 74361de
- [x] 1.4 Full e2e suite still green: `uv run pytest tests/e2e -q` — 74361de

#### Manual

- [x] 1.5 BQ round-trip: endpoint returns 3 rows locally, no score/sentiment keys — 74361de

### Phase 2: Landing page UI + e2e suite kept green

#### Automated

- [ ] 2.1 Unit tests pass: `uv run pytest tests/ --ignore=tests/e2e -q`
- [ ] 2.2 Full e2e suite green after helper updates: `uv run pytest tests/e2e -q`

#### Manual

- [ ] 2.3 Local visual check: landing light/dark/mobile, cards, tabs, API-key path to dashboard

### Phase 3: Auth wiring + boot probe + e2e auth coverage

#### Automated

- [ ] 3.1 Unit tests pass: `uv run pytest tests/ --ignore=tests/e2e -q`
- [ ] 3.2 Full e2e suite green incl. `test_landing_auth.py`: `uv run pytest tests/e2e -q`

#### Manual

- [ ] 3.3 Local Firebase round-trip: register → dashboard, logout, login, refresh keeps session, wrong-password message
- [ ] 3.4 Post-merge prod check: register + login + logout on prod, cards visible
