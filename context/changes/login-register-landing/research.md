---
date: 2026-07-18T01:20:01+02:00
researcher: Claude (Fable 5)
git_commit: 8dadf34729f700de382ebef38998ebfeaff1aa86
branch: pul-72-login-register-landing
repository: puls-gpw
topic: "PUL-72: onboarding landing page + email/password login & registration UI over PUL-71 auth foundation"
tags: [research, codebase, auth, landing-page, public-endpoint, faro-v2]
status: complete
last_updated: 2026-07-18
last_updated_by: Claude (Fable 5)
---

# Research: PUL-72 — landing page + login/register UI

**Date**: 2026-07-18T01:20 CET
**Git Commit**: 8dadf34 · **Branch**: pul-72-login-register-landing · **Repository**: puls-gpw

## Research Question

Ground the PUL-72 spec (Linear PUL-72 / GH #128) against: (1) the auth surface actually
delivered by PUL-71, (2) the faro-v2 login/session flow it replaces, (3) the data/caching
building blocks for the new public `GET /api/public/top-announcements` endpoint.

## Summary

The PUL-71 backend is **complete, tested, and entirely unused by the UI** — PUL-72 is its
first consumer. The faro-v2 login screen is a clean greenfield (zero landing/register
remnants). Three findings materially shape the plan:

1. **The JWT session is an HttpOnly cookie** — JS cannot read it. Session state must come
   from `GET /api/auth/me`; the boot gate `if (apiKey && role)` (index.html:1122) must grow
   a cookie-session path. Same-origin fetch sends the cookie by default (fetch
   `credentials: 'same-origin'`), and `_get_role` checks the cookie FIRST, so the existing
   22 fetch call sites work unchanged for JWT users even with `X-API-Key: null` headers.
2. **The public endpoint deliberately breaks the "score is admin-only" convention** —
   `analysis_score` today never reaches non-admin callers anywhere. The ticket explicitly
   wants it on public cards. Needs conscious sign-off; strip `sentiment` as elsewhere.
3. **The "no cookies" GDPR notice becomes false** — `static/index.html:815` says the app
   uses no cookies; after PUL-72 login sets the `session` cookie that's wrong. PUL-71's
   plan explicitly left this update to PUL-72.

## Detailed Findings

### 1. Auth surface delivered by PUL-71 (`src/auth.py`, 396 lines)

Router `prefix="/api/auth"` (src/auth.py:317), mounted at src/api.py:257.

| Route | Success | Errors |
|---|---|---|
| `POST /api/auth/register` (src/auth.py:337) | 200 `{user_id, email}` + `session` cookie | 422 field-level (Pydantic `detail[].loc`), 409 "Email jest już zarejestrowany", 429 (5/min/IP) + Retry-After, 503 "Auth temporarily unavailable" |
| `POST /api/auth/login` (src/auth.py:362) | 200 `{user_id, email}` + cookie | 422, 401 "Nieprawidłowy email lub hasło" (anti-enumeration), 429 Firebase-lockout "Zbyt wiele prób logowania…", 429 local (10/min/IP), 503 |
| `POST /api/auth/logout` (src/auth.py:383) | 204 + cookie deletion | — (stateless; JWT stays valid until exp, only cookie cleared) |
| `GET /api/auth/me` (src/auth.py:388) | 200 `{user_id, email}` from JWT alone (no BQ) | 401 "Brak ważnej sesji" |

- Error messages are **already Polish-localized** — the UI can surface `detail` directly.
- Password rules: 8–128 chars, ≥1 letter + ≥1 digit (validators src/auth.py:33-56).
  **Ticket says only "min 8, max 128"** — the letter+digit rule exists on the backend, so
  client-side validation should mirror it or rely on 422 messages.
- Cookie: name `session`, HttpOnly, SameSite=Lax, Secure only on Cloud Run (`K_SERVICE`),
  TTL 7d, sliding refresh after 24h, absolute cap 30d (src/auth.py:112-163).
- `_get_role` (src/api.py:103-118): **cookie-first**, then X-API-Key. JWT users are always
  role `"user"`. Invalid cookie falls through to header auth — API-key sessions unaffected.
- `_get_client_id` (src/api.py:127-137): JWT users' client_id = **Firebase UID** (PUL-74
  groundwork); header `X-Client-Id` only used when no session.
- Firebase: register via Admin SDK (`FIREBASE_SERVICE_ACCOUNT_JSON`), login via Identity
  Toolkit REST (`FIREBASE_WEB_API_KEY`). `JWT_SECRET` signs sessions.
- BQ `users` table: user_id, email, created_at, last_login_at (db/bigquery.py:851-856);
  insert/upsert failures are logged, non-fatal (src/auth.py:355-357, 376-378).

### 2. faro-v2 login/session flow (`static/index.html`)

- Login DOM: `#login-screen` (lines 792-807) — API-key input (`#api-key-input`), button
  `#login-btn` "Zaloguj się", error `#login-error`. CSS lines 56-96 + dark overrides
  (737-763). Login and app are sibling sections toggled by `style.display`
  (`#dashboard-screen` lines 809-876) — the landing page replaces/augments `#login-screen`.
- Handler (1127-1140): validates key via `GET /auth/role` with `X-API-Key`; stores
  `apiKey`+`role` in **sessionStorage**; `clientId` is a separate throwaway UUID in
  **localStorage** `watchlist_client_id` (1045-1051), surviving logout.
- Boot: `init()` (1119-1124, invoked at 3265) → `if (apiKey && role) showDashboard(role)
  else showLogin()`. **No cookie-session check exists** — needs `GET /api/auth/me` probe.
- `showDashboard(r)` (1252-1263): renderHeaders → injectAdminOnlyChrome → loadAutocomplete
  → `_applyUrlState()` → startIdleTracking.
- Fetch pattern: **no central wrapper** — 22 call sites attach `X-API-Key` inline,
  13 add `X-Client-Id`; the inline `if (r.status === 401) { doLogout(); return; }` pattern
  appears 15×. For JWT users these all keep working via cookie-first `_get_role`
  (same-origin fetch sends cookies by default).
- `doLogout()` (996-1014, incl. PUL-82 watchlist reset): purely client-side today —
  **must additionally call `POST /api/auth/logout`** to clear the HttpOnly cookie
  (ticket requirement).
- Landing/register remnants: **none** (only hit for "password" is the autocomplete attr).
- `static/index_old.html`: same login flow minus the PUL-82 logout reset — not a factor.

### 3. Public top-announcements endpoint — building blocks

- **No existing query fits.** The only score-sorted function is `fetch_top_n_for_window`
  (db/bigquery.py:1271-1334, `ORDER BY analysis_score DESC, published_at DESC`, approved +
  `event_type != 'inne'` + min-score + window) — designed for X-posts (dedup per ticker).
  New function needed, e.g. `list_top_announcements_public(limit=3)`:
  `analysis_approved = TRUE AND analysis_score IS NOT NULL`, same ORDER BY, LIMIT 3.
  Consider reusing the 90-day default window from `_build_filter_clauses`
  (db/bigquery.py:1360-1363) so "most recent with highest score" stays fresh.
- Announcements schema (db/bigquery.py:48-67): safe public card fields = `company`,
  `ticker`, `title`, `event_type`, `published_at`, `analysis_score` (deliberate new
  exposure), summary from `structured_analysis.summary_pl`. Do NOT expose
  `parsed_content`, `analysis_approved`, `analysis_reject_reason`, raw JSON, or `sentiment`.
- **Cache**: `_perf_get`/`_perf_set` with explicit TTL — exact precedent
  `_perf_get("admin:treemap", ttl=60)` (src/api.py:472/505). Key `"public:top-announcements"`
  (no per-client part — response identical for everyone). Per-instance only (2 Cloud Run
  instances) — acceptable for 60s. `_ac_*` unsuitable (hardcoded 300s, list-only).
- Public route = simply omit `Depends(_get_role)` (raises 401 itself). Precedents:
  `/health` (src/api.py:280), `/` (284), `/static` mount (758). Cloud Run is already
  `--allow-unauthenticated`; no CORS in the app (same-origin only) — none needed.
- Rate limiting: `rate_limit(...)` dependency factory exists (src/auth.py) if desired;
  the 60s cache already bounds BQ load.

### 4. Test infrastructure

- **E2E**: `tests/e2e/conftest.py:343-433` — every BQ function used by an endpoint must be
  patched as `patch("src.api.<fn>")` in `_patches`, or e2e hits real BQ. Auth already
  mocked: `JWT_SECRET="e2e-jwt-secret"`, `src.auth._get_firebase_app`,
  `firebase_auth.create_user` → uid "e2e-firebase-uid", `verify_password_rest` →
  `("e2e-firebase-uid", "e2e@example.com")`. The new BQ function MUST join `_patches`.
- **Unit**: `api_client` fixture (tests/test_api.py:28-30); `_env` sets API keys but NOT
  `JWT_SECRET` (set per-test-group at :1236). Public endpoint tests: no headers, model
  after `test_health_no_auth_returns_200` (:33-36). `_clear_caches` autouse (:18-25) wipes
  `_PERF_CACHE` between tests — the 60s cache won't leak.
- **e2e login tests to update**: `tests/e2e/test_login_ux.py` tests the current API-key
  screen (`.login-banner`, "Klucz API" label) — landing page will change these selectors.
  Every e2e `_login` helper types into `get_by_label("Klucz API")` — **the API-key path
  must stay reachable for the whole e2e suite** (or all suites' login helpers change).

## Code References

- `src/auth.py:317-395` — auth router (register/login/logout/me)
- `src/auth.py:33-56` — password/email validators (letter+digit rule beyond ticket)
- `src/auth.py:112-163` — cookie set/clear/refresh mechanics
- `src/api.py:103-137` — `_get_role` cookie-first + `_get_client_id` UID mapping
- `src/api.py:59-95` — the two cache helpers; 60s precedent at :472/:505
- `db/bigquery.py:1271-1334` — score-ordered query template
- `db/bigquery.py:48-67` — announcements schema
- `static/index.html:792-807` — login screen to replace; `:1119-1124` boot gate;
  `:996-1014` doLogout; `:815` GDPR "no cookies" notice (now false)
- `tests/e2e/conftest.py:343-433` — live-server patch list + auth mocks

## Architecture Insights

- Auth is app-layer per-endpoint `Depends`, no middleware; public = omit the dependency.
- The UI is a single-file vanilla-JS SPA with display-toggled sections; new landing page
  is a third sibling section (or an extension of `#login-screen`), not a separate route.
- Cookie-first `_get_role` means the JWT path needs zero changes at data-fetch call
  sites — the integration cost is concentrated in boot, login/register forms, and logout.
- Score exposure on public cards is the first non-admin exposure of `analysis_score` —
  a product decision, not an accident of implementation.

## Historical Context (from prior changes)

- `context/archive/2026-07-17-pul-71-auth-foundation/plan.md` — cookie flags, rate-limit
  choices, the "each new BQ fn joins the 30-mock e2e list" rule (plan.md:214-216), GDPR
  notice handed to PUL-72 (plan.md:45), and a **scope note**: that plan assigned
  `/api/public/*` to PUL-73, while the PUL-72 ticket now includes top-announcements —
  ticket is newer and authoritative, discrepancy surfaced to the owner.
- `context/archive/2026-06-11-auth-public-url/` — Cloud Run `--allow-unauthenticated` +
  app-layer auth decision.
- `context/foundation/roadmap.md:151-166` — provenance of `analysis_score`
  (tier + event_type + priority; wyniki=100 … inne=20) — the number shown on public cards.
- `context/foundation/prd.md` / `roadmap.md` — predate the PUL-70 epic; registration is
  specced only in Linear (PUL-70/71/72) and the PUL-71 archive folder.

## Related Research

- `context/archive/2026-07-17-pul-71-auth-foundation/research.md` — auth landscape pre-PUL-71.

## Open Questions

1. **Score on public cards** — deliberate break of the admin-only convention; needs
   explicit sign-off in planning (recommend: yes per ticket, strip sentiment).
2. **Password hint copy** — backend enforces letter+digit beyond the ticket's "min 8";
   mirror client-side or let 422 carry it?
3. **Boot-order cost** — `GET /api/auth/me` probe on every unauthenticated page load adds
   one request before the landing renders; acceptable, or render landing immediately and
   probe in parallel?
4. **e2e `_login` helpers** — landing page must keep `get_by_label("Klucz API")` reachable
   (after clicking "Mam klucz API") or ~10 e2e files need their login helper updated.
