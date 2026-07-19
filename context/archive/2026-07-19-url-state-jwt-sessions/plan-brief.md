# URL State for JWT Sessions — Plan Brief

> Full plan: `context/changes/url-state-jwt-sessions/plan.md`

## What & Why

Since PUL-74 every regular user is on a JWT cookie session — and for JWT sessions the SPA
never writes the URL and ignores back/forward, so deep links, reload-restore, and browser
navigation are dead for exactly the users we just opened registration to. This change
un-gates URL-state for JWT while keeping the API-key path intact, and consolidates the
duplicated dashboard HTML into a single `index.html`.

## Starting Point

The restore half already works: `_applyUrlState()` runs on every login and has post-PUL-74
per-view branches. Only the write half is dead — `_writeUrl` and the `popstate` listener
both early-return on `!apiKey`. Bonus existing bug: entering Portfel pushes a history
entry even on JWT, but back only changes the URL, desyncing view↔address bar.
`static/faro-v8.html` is byte-identical to `index.html`; `index_old.html` is a stale
backup — nothing in code references either.

## Desired End State

A JWT user navigates, filters, reloads, and uses back/forward like an API-key session
does today; logout still wipes URL-state. `static/` holds exactly one dashboard HTML
(`index.html`); the skipped calendar URL e2e test is back in the suite and green.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) |
| --- | --- | --- |
| Session guard | Reuse `!role` (no new flag) | Set in both auth paths, nulled by doLogout — zero new state, no flag↔session drift. |
| Param scope | Full URL-state for JWT (view + params) | Shared `_writeUrl` path means zero extra code; restore already handles params. |
| E2E breadth | Linear scope + desync regression | Covers all behaviors this change alters, incl. the pre-existing Portfel→back desync. |
| Phasing | Single phase | Production diff is ~3 lines; splitting fix from proof would be artificial. |
| HTML files | One `index.html`; delete `faro-v8.html` + `index_old.html` | Byte-identical duplicate + stale backup only cause naming confusion (user decision). |

## Scope

**In scope:** guard swap in `_writeUrl`/`popstate` + comment updates; `git rm` of
`faro-v8.html` and `index_old.html`; 4 new JWT e2e tests; calendar test un-skip.

**Out of scope:** auth/role changes; backend changes; new views or URL params; login-screen
hash routing; the ~2026-07-26 DROP `client_id` cleanup chore (incl. vestigial `X-API-Key`
headers).

## Architecture / Approach

Two-line production change: `if (!apiKey) return` → `if (!role) return` in `_writeUrl`
(`static/index.html:2256`) and the `popstate` listener (`:1624`). `role` is the ready-made
"session active" sentinel for both auth paths. Everything downstream (restore, param
helpers) already supports JWT. Tests are the bulk of the work.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. URL-state for JWT + HTML consolidation + E2E | Working deep links/back/forward for JWT, single index.html, full e2e proof | Un-skipped calendar test may have stale pre-PUL-74 setup needing adaptation |

**Prerequisites:** none — branch `pul-84-url-state` is open.
**Estimated effort:** ~1 session.

## Open Risks & Assumptions

- `/static/faro-v8.html` will 404 after consolidation — assumed acceptable (any old
  preview bookmark dies).
- Assumes no other dormant `apiKey`-gated URL-state sites exist; grep found only the two
  guards (plus intentionally ungated click handlers).

## Success Criteria (Summary)

- JWT user: URL tracks navigation, reload restores the view, back/forward works, logout
  resets to `/`.
- API-key session behaves exactly as before (existing test_url_routing.py green).
- e2e suite passes with 0 skipped; only `index.html` remains in `static/`.
