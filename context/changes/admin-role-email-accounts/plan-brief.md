# Admin Role for Email Accounts (PUL-83) — Plan Brief

> Full plan: `context/changes/admin-role-email-accounts/plan.md`

## What & Why

Email/password accounts are currently hardwired to role `user` — the owner's own
account can't see the admin surfaces (score, sentiment, CSV, X-post history), which
today exist only behind the API key. This change makes role a real, server-signed
property of an email account, then promotes the owner's account.

## Starting Point

PUL-71/72 shipped Firebase auth + JWT session cookie + landing/login UI, but
`_get_role` returns `"user"` for every valid JWT and the BQ `users` table has no
`role` column. All admin gating (server `_require_admin`, UI `role === 'admin'`)
already exists and works for the API-key path.

## Desired End State

The owner logs in with email and gets the identical dashboard an API-key admin gets;
every other email account (and all new registrations) stays `user`; role changes
apply at the next login. API-key paths untouched.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) |
| --- | --- | --- |
| Role freshness | Read from BQ only at login; claim rides the JWT (refresh carries it) | Zero per-request cost; with one admin, demotion-by-re-login is enough (30d absolute cap backstops) |
| Column semantics | `role` NULLABLE, no backfill — `COALESCE(role,'user')` on read | Zero data migration; only the owner's row ever gets updated |
| UI parity | Full parity with API-key admin | One definition of admin; server-side `_require_admin` keeps enforcing regardless |
| Test scope | Full — unit (claim/gates/SQL regressions) + e2e admin flow | A bug here means score/sentiment leaking to users — exactly the surface worth locking |
| Promotion | One-time human-run BQ UPDATE (SQL provided in plan) | Role management UI is out of scope at this scale |

## Scope

**In scope:** `role` column + `get_user_role`; role claim in session JWT (login/register/refresh); `_get_role` + `/api/auth/me` + login response expose role; UI consumes server-provided role; role-aware e2e mocks + admin-flow tests; owner promotion.

**Out of scope:** role management UI, role re-read on refresh//me, backfill, per-user data isolation (PUL-74), API-key auth changes.

## Architecture / Approach

BQ (`users.role`, NULL=user) → read once in `POST /api/auth/login` → claim in the
HS256 session JWT → `_get_role` maps claim→Role for every endpoint → `/me` and login
responses carry it → UI stores it as a hint and renders the matching view. Trust
lives only in the signed token and server gates.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Backend | Column + claim + gates + unit tests + e2e mocks | MERGE overwriting role on login (locked by SQL regression test) |
| 2. UI + e2e | UI uses server role; admin-flow e2e; owner promotion post-merge | Role leaking from client storage instead of token (kept as UX hint only) |

**Prerequisites:** PUL-71/72 deployed (done); owner account exists in Firebase + BQ (done).
**Estimated effort:** ~1 session, 2 phases.

## Open Risks & Assumptions

- Demotion propagates only at re-login (accepted; 30-day absolute session cap backstops).
- Login gains one BQ query — negligible at current scale; on BQ error login still
  succeeds as `user` (availability over freshness).

## Success Criteria (Summary)

- Owner's email login shows the full admin dashboard on prod; other accounts unchanged.
- 485+ unit and 82+ e2e tests green, including new claim/SQL regressions and admin-flow e2e.
- No admin capability reachable without the signed claim or the admin API key.
