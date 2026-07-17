# Onboarding Landing + Login/Register (PUL-72) — Plan Brief

> Full plan: `context/changes/login-register-landing/plan.md`
> Research: `context/changes/login-register-landing/research.md`

## What & Why

Replace the bare API-key login screen with an onboarding landing page — hero, 3
top-score announcement cards from a new public endpoint, and email/password
register/login forms — making PUL-72 the first UI consumer of the PUL-71 auth
foundation. This closes the loop on auth and unblocks notifications (PUL-81).

## Starting Point

PUL-71 shipped a complete, deployed auth backend (`/api/auth/register|login|logout|me`,
HttpOnly JWT cookie) that no UI code calls. The faro-v2 login screen is a single API-key
input; `_get_role` is cookie-first, so the app's 22 fetch call sites already work for
JWT users unchanged.

## Desired End State

An anonymous visitor lands on a marketing page with live announcement cards and can
register or log in with email/password; registration drops them straight into the
dashboard. Refresh keeps a JWT session alive without flashing the landing. The API-key
path survives one click deeper behind "Mam klucz API".

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Score on public cards | **No** — score orders server-side, never returned | Admin-only convention stands; containment enforced at the DB layer | Plan (user) |
| Sentiment on cards | Stripped server-side (only `summary_pl` returned) | Same convention; raw JSON never reaches the client | Research |
| Password UX | Hint + inline validation mirroring backend rule (min 8, letter+digit) | Users learn the real rule before submitting; backend stays source of truth | Plan (user) |
| Boot/session detection | `localStorage.hasSession` flag + conditional `GET /api/auth/me` probe | Anonymous boots make zero auth requests; logged-in users get no landing flash | Plan (user) |
| API-key path | Behind "Mam klucz API" link; ~10 e2e helpers get one extra click | Clean landing per ticket; mechanical, uniform test change | Plan (user) |
| Landing layout | Single section: hero + cards + tabbed forms (Zaloguj / Załóż konto) | Simplest state in the display-toggle SPA; no extra views | Plan (user) |
| Post-register | Straight to dashboard (auto-login) | Backend already sets the session cookie on register — friction-free | Plan (user) |
| E2E scope | Full: cards, register, login, wrong password, logout, API-key regression | First auth consumer — flows protect the PUL-71 foundation | Plan (user) |
| Public endpoint cache | `_perf` in-memory, key `public:top-announcements`, ttl=60 | Exact `admin:treemap` precedent; bounds BQ load | Research |
| GDPR notice | Reword to "one essential session cookie"; no consent banner | Essential cookies need no consent; "no cookies" claim becomes false | Research |

## Scope

**In scope:** new BQ query + `GET /api/public/top-announcements` (60s cache, no auth);
landing page rebuild of `#login-screen`; register/login/logout wiring + boot probe;
GDPR notice; e2e helper updates + new `test_landing_auth.py`.

**Out of scope:** guest mode (PUL-73), data isolation (PUL-74), email
verification/password reset, fetch-wrapper refactor, e2e helper centralization,
rate-limit changes.

## Architecture / Approach

Three layers, thinnest possible touch: one new BQ function (modeled on
`fetch_top_n_for_window`), one public FastAPI route with the existing `_perf` cache, and
all UI work inside the single-file SPA's `#login-screen` section using the established
display-toggle pattern. Cookie-first `_get_role` means zero changes at data-fetch call
sites.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Backend endpoint | Public top-announcements route + cache + tests + e2e mock | Hand-written SQL — mitigated by real-BQ round-trip + query-string regression |
| 2. Landing UI | New landing, cards live, API-key behind click, all e2e helpers updated | Suite breaks if any `_login` helper is missed — full e2e run gates the phase |
| 3. Auth wiring | Forms live, boot probe, logout, new e2e auth file | `hasSession` flag desync with cookie — 401 probe must clear the flag |

**Prerequisites:** PUL-71 deployed (done); Firebase env vars in local `.env` for Phase 3
manual verification.
**Estimated effort:** ~2-3 sessions across 3 phases.

## Open Risks & Assumptions

- Cards depend on fresh approved announcements in the last 90 days — if the window is
  empty, the landing hides the strip gracefully (designed for).
- Per-instance 60s cache across 2 Cloud Run instances can serve briefly different card
  sets — accepted.
- Prod Firebase register flow verified only post-merge (CI deploys on master).

## Success Criteria (Summary)

- Anonymous visitor can register and is in the dashboard in one step; login/logout/
  refresh round-trips behave.
- Public cards never contain `analysis_score` or `sentiment` — locked by unit + e2e
  assertions.
- Entire e2e suite green with the landing in place; API-key path regression-proof.
