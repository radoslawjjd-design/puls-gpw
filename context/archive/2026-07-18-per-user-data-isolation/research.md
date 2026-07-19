---
date: 2026-07-18T16:00:01+02:00
researcher: Claude (Fable 5)
git_commit: d5c213bf29a6e970986552be62224f05de731f19
branch: master
repository: puls-gpw
topic: "PUL-74: per-user data isolation — watchlist, portfolio, treemap, calendar scoped to JWT user_id; API-key admin keeps global view"
tags: [research, codebase, auth, client-id, user-id, watchlist, portfolio, treemap, calendar, bigquery]
status: complete
last_updated: 2026-07-18
last_updated_by: Claude (Fable 5)
last_updated_note: "Added follow-up: user decisions resolving all open questions (PUL-73 dropped, JWT-only endpoints, re-key owner data, column rename)"
---

# Research: PUL-74 — Per-user data isolation

**Date**: 2026-07-18T16:00:01+02:00
**Researcher**: Claude (Fable 5)
**Git Commit**: d5c213bf29a6e970986552be62224f05de731f19
**Branch**: master
**Repository**: puls-gpw

## Research Question

Scope watchlist, portfolio, treemap, calendar (and transitively MTD) to the JWT `user_id` instead of the shared browser `client_id`; API-key admin keeps the global view. Prereqs PUL-71/72/83 done. What exists today, what is the real remaining work, and where are the gaps?

## Summary

**The single most important finding: the Linear ticket description (written 2026-07-16) is largely stale — most of its "What to build" is already built or mismatched with the actual schema.** PUL-71 already shipped the identity unification seam: `_get_client_id` (src/api.py:130-140) returns the signed JWT `user_id` (Firebase UID) when a session cookie is present, falling back to the `X-Client-Id` header otherwise. Because every one of the 12 data endpoints resolves identity through this one dependency, **JWT users are already functionally isolated from each other** on all four surfaces — user A's Firebase UID scopes every watchlist/portfolio/treemap/calendar query.

What the ticket assumed vs. reality:

| Ticket assumption | Reality |
|---|---|
| Add `user_id STRING` column to `watchlist`, `portfolio_snapshots`, treemap/calendar tables | Portfolio tables (`user_portfolios`, `user_portfolio_positions`) **already have `user_id`**; only `watchlist` uses the column name `client_id`. `portfolio_snapshots` is **admin/global XTB data** — not used by per-user treemap/calendar at all |
| `WHERE client_id = ? AND user_id IS NULL` dual-predicate migration pattern | Not applicable — there is **one identity column per table**, holding either a browser UUID (API-key path) or a Firebase UID (JWT path) as the value. No dual columns, no NULL semantics needed |
| Logic lives in "the auth middleware introduced in PUL-71" | There is **no auth middleware** — identity is per-endpoint FastAPI `Depends` (`_get_role` ×18, `_get_client_id` ×12, `_require_admin` ×3; ast-grep verified) |
| MTD needs `portfolio_snapshots` scoping | MTD (`mtd_diff`) is a pure-Python computed field of the calendar response (src/portfolio_calendar.py:113-124) — already scoped transitively |

**The real remaining work** (candidate scope for the plan):

1. **Close the actual isolation hole: `X-Client-Id` is unverified.** On the API-key path any string is accepted (src/api.py:130-140) — anyone holding the shared `USER_API_KEY` can set `X-Client-Id` to a victim's Firebase UID and read/modify their watchlist and portfolio. This is the only genuine cross-user leakage vector today.
2. **Two SQL predicates missing the identity scope** (defense-in-depth relies on API-layer ownership checks only): the positions-upsert MERGE key `ON T.portfolio_id = S.portfolio_id AND T.ticker = S.ticker` (db/bigquery.py:545, no `user_id`) and the cascade positions DELETE `WHERE portfolio_id = @portfolio_id` (db/bigquery.py:802-805, no `user_id`).
3. **Isolation tests** — the ticket's success criterion "user A cannot retrieve user B's rows" has no test today (tests cover missing-header 400s and JWT-precedence, not cross-user denial).
4. **Data-continuity decision** — the owner's historical watchlist/portfolio rows are keyed by the browser UUID from the API-key era; after email login their identity is the Firebase UID, so those rows are invisible on the JWT path (no migration was done — explicitly deferred: "ujednolicenie = PUL-74").
5. **Naming debt (optional)** — `watchlist.client_id` vs `user_id` everywhere else; documented as PUL-74 debt.
6. **Frontend cleanup (optional)** — stale `localStorage.watchlist_client_id` survives logout; SPA always sends `X-Client-Id` even when the backend ignores it on the JWT path.

**Admin global view is already safe**: the global treemap is a separate endpoint `GET /admin/portfolio/treemap` reading admin XTB `portfolio_snapshots`, gated by `_require_admin` (src/api.py:518-519), and PUL-83 gave email-admins full parity with API-key admins. Per-user surfaces never mix with it.

## Detailed Findings

### Identity resolution today (the PUL-71 seam)

- `_CLIENT_ID_HEADER = APIKeyHeader(name="X-Client-Id", auto_error=False)` — src/api.py:100
- `_get_client_id(request, client_id=Security(_CLIENT_ID_HEADER)) -> str` — src/api.py:130-140:
  - session cookie present → returns `payload["user_id"]` (Firebase UID, signed; comment: "groundwork for PUL-74")
  - no cookie → returns raw `X-Client-Id` header (400 if missing). **The header is not verified in any way.**
- `_get_role` — src/api.py:104-121: JWT cookie first (role claim), else `ADMIN_API_KEY`/`USER_API_KEY` header match, else 401. API key yields only a role, **no identity**. `_get_client_id` on the API-key path does not itself check the API key — it relies on `_get_role` being co-declared on the same endpoint.
- `_require_admin` — src/api.py:124-127 (403 unless admin).
- No auth middleware exists; the only middleware is the `X-Process-Time` timer (src/api.py:274-280).

Call-site counts (ast-grep, verified 2026-07-18): `Depends(_get_client_id)` ×12, `Depends(_get_role)` ×18, `Depends(_require_admin)` ×3 — all in src/api.py.

### JWT session (PUL-71 + PUL-83)

- `create_session_token` — src/auth.py:68-97, HS256, secret `JWT_SECRET`. Claims: `user_id` (Firebase UID), `email`, `auth_type`, `iat`, `exp` (7d), `login_at` (30-day absolute cap), `role` (PUL-83, read from BQ once at login).
- `decode_session_token` — src/auth.py:100-118: returns `None` on any failure; requires `exp`/`iat` and non-empty `user_id`/`email`.
- Cookie `session`: HttpOnly, SameSite=Lax, Secure on Cloud Run (`K_SERVICE`), max_age 7d (src/auth.py:29-30, 121-131).
- Sliding refresh >24h, carries `role` forward — src/auth.py:154-174; invoked inside `_get_role` (src/api.py:113).
- `GET /api/auth/me` returns `{user_id, email, role}` from JWT only — src/auth.py:409-422.
- `user_id` origin: Firebase UID (`user.uid` at register src/auth.py:356; `localId` from Identity Toolkit at login src/auth.py:308).

### Users table & role (PUL-83)

- BQ `users` (db/bigquery.py:849-859): `user_id` REQUIRED (Firebase UID), `email` REQUIRED, `created_at` REQUIRED, `last_login_at` NULLABLE, `role` NULLABLE (NULL ≡ "user", every reader `COALESCE(role,'user')`).
- `get_user_role` (db/bigquery.py:943-969) called only at login (src/auth.py:397); role rides the JWT afterwards. Promotion to admin = human-only BQ UPDATE (no code path writes `role='admin'`).
- Admin-only endpoints: `GET /admin/x-posts` (src/api.py:496-505), `GET /admin/portfolio/treemap` (src/api.py:518-519), `DELETE /announcements/{id}` (src/api.py:562-563).

### The naming split — one runtime value, two column names

The identity string returned by `_get_client_id` is passed positionally into DB functions; it binds to a `client_id` SQL param for watchlist and a `user_id` SQL param for portfolio tables. Same value, two names.

**Only one table has a literal `client_id` column** — `watchlist` (db/bigquery.py:460-466): `client_id` REQUIRED, `ticker` REQUIRED, `added_at` REQUIRED. Its 4 SQL sites all predicate on `client_id = @client_id` (grep-verified): INSERT-if-not-exists db/bigquery.py:979-985, DELETE :1009-1012, SELECT :1036-1039, watchlist-join subquery in `list_announcements_for_watchlist` :1644-1652.

**Portfolio tables already use `user_id`**:
- `user_portfolios` (db/bigquery.py:669-678): `user_id`, `portfolio_id` (server UUID), `portfolio_type` (glowny|ikze|ike|ppk|ppe|inny), `portfolio_name`, `display_order`, `created_at`. All queries `WHERE user_id = @user_id` (:712, :740-780, :807-808).
- `user_portfolio_positions` (db/bigquery.py:491-502): `user_id`, `ticker`, `company_name`, `shares`, `avg_buy_price`, `created_at`, `updated_at`, `portfolio_id` NULLABLE. Scoped queries at :585, :652, :831-833, :400-404 (calendar CTE).
- `users` (db/bigquery.py:849-859) — see above.

**Global (unscoped, correct as-is)**: `announcements`, `x_posts`, `portfolio_snapshots` (admin XTB uploads), `companies`, `company_daily_stats`, `etf_instruments`, `etf_quotes`.

### Two SQL sites missing the identity predicate (verified)

1. **Positions upsert MERGE** — db/bigquery.py:539-555, key `ON T.portfolio_id = S.portfolio_id AND T.ticker = S.ticker` (line 545) — **no `user_id`**. Cross-user safety depends solely on the API-layer ownership check (`portfolio_id` ∈ `list_user_portfolios(client_id)`, src/api.py:618).
2. **Cascade positions DELETE** in `delete_user_portfolio` — db/bigquery.py:802-805, `WHERE portfolio_id = @portfolio_id` only (the wallet DELETE at :806-809 IS user-scoped). Same reliance on the caller's prior ownership check + UUID unguessability.

### Endpoint map (all 12 per-user data endpoints)

All use `Depends(_get_role)` + `Depends(_get_client_id)` (src/api.py, inside `create_app`):

| Endpoint | dep line | DB calls |
|---|---|---|
| GET /watchlist | :426 | `list_watchlist_tickers(client_id)` :429 |
| POST /watchlist/{ticker} | :439 | `add_watchlist_ticker(client_id, ticker)` :445 |
| DELETE /watchlist/{ticker} | :455 | `remove_watchlist_ticker(client_id, ticker)` :458 |
| GET /announcements/my-wallet | :470 | `list_announcements_for_watchlist(client_id, …)` :473-475 |
| GET /api/portfolio/positions | :576 | `list_user_portfolios` :583 + `list_user_portfolio_positions` :590 |
| POST /api/portfolio/positions | :613 | ownership :618 + `upsert_user_portfolio_position` :628-630 |
| DELETE /api/portfolio/positions/{ticker} | :642 | ownership :645 + `delete_user_portfolio_position` :652 |
| GET /api/portfolio/wallets | :661 | `list_user_portfolios` :664 |
| POST /api/portfolio/wallets | :673 | :676, `create_user_portfolio` :687, `assign_orphan_positions_to_portfolio` :689 |
| DELETE /api/portfolio/wallets/{portfolio_id} | :704 | ownership :707 + `delete_user_portfolio` :714 |
| GET /api/portfolio/treemap | :722 | `list_user_portfolios` :729 + `list_user_portfolio_positions(client_id)` :736 |
| GET /api/portfolio/calendar | :778 | ownership :790 + `get_portfolio_calendar_data(portfolio_id, client_id, y, m)` :797 |

In-process cache keys are already namespaced by the **resolved** identity (so they re-key automatically for JWT users): `positions:{client_id}:{portfolio_id}` (src/api.py:578), `treemap:{client_id}` (:724), `calendar:{client_id}:{portfolio_id}:{year}:{month}` (:785); invalidation `_perf_invalidate_portfolio` (src/api.py:91-94).

### Treemap / calendar / MTD data flow

- **Per-user treemap** GET /api/portfolio/treemap: user's positions (`WHERE p.user_id = @user_id`, db/bigquery.py:652) LEFT-JOINed to **global** price tables `company_daily_stats` + `etf_quotes` via CTEs (db/bigquery.py:618-651); aggregation in pure Python `compute_user_portfolio_treemap_positions` (src/api.py:751). Does **not** read `portfolio_snapshots`.
- **Admin/global treemap** GET /admin/portfolio/treemap (src/api.py:518-519): `_require_admin`, reads admin XTB `portfolio_snapshots` — this is the "global view" that must stay intact. Already separate; no work needed beyond not breaking it.
- **Calendar** GET /api/portfolio/calendar: ownership check → `get_portfolio_calendar_data` (db/bigquery.py:362-457); positions CTE user+portfolio scoped (:400-404), prices global.
- **MTD**: no dedicated endpoint — `mtd_diff` computed in Python from calendar rows (src/portfolio_calendar.py:66-124; response field src/api.py:209). Scoped transitively; ticket's "portfolio_snapshots → MTD" claim is wrong for the per-user path.

### Frontend

- `clientId` generated `crypto.randomUUID()`, persisted in `localStorage.watchlist_client_id` (`initClientId`, static/index.html:1298-1304); sent as `X-Client-Id` header on 13 call sites (grep count; e.g. :2405, :2553, :2905).
- JWT session rides the `session` cookie automatically; after email login `apiKey` is null and the backend ignores the headers (cookie wins in both dependencies).
- API-key path: `sessionStorage.apiKey` + manual `X-API-Key` header; role via `GET /auth/role` (:1418-1424). Role cached in `sessionStorage.role` as UI hint only.
- **Gaps**: `watchlist_client_id` survives logout (flagged in login-register-landing research); the SPA never uses `user_id` from `/api/auth/me` as data identity — it always sends the random UUID and relies on the backend override.

### Tests (current coverage)

- tests/test_api.py: eight missing-`X-Client-Id` → 400 tests (:433, :447, :486, :509, :749, :825, :874, :1202); JWT-precedence test `test_watchlist_with_cookie_uses_jwt_user_id` (:1353-1359).
- tests/test_bigquery.py: watchlist schema + `client_id = @client_id` predicate assertions (:743-745, :761-807); users MERGE on `user_id` (:841-920); calendar param binding (:1307-1331).
- tests/e2e/conftest.py: in-memory watchlist fakes keyed by `client_id` (:172-221) — **reminder (lesson feedback-e2e-conftest-bq-mocking): any new BQ function must be mocked in ALL e2e conftest fakes**.
- **Missing**: no cross-user isolation test (user A ↛ user B) — a ticket success criterion.

## Code References

- `src/api.py:100` — `X-Client-Id` header definition
- `src/api.py:104-121` — `_get_role` (JWT → role claim; else API keys)
- `src/api.py:124-127` — `_require_admin`
- `src/api.py:130-140` — `_get_client_id` (JWT `user_id` wins; unverified header fallback) — **the seam**
- `src/api.py:518-519` — admin global treemap (`_require_admin`, `portfolio_snapshots`)
- `src/auth.py:68-97` / `:100-118` — JWT create/decode; claims incl. `role`
- `db/bigquery.py:460-466` — `watchlist` schema (only `client_id` column in the system)
- `db/bigquery.py:539-555` — positions MERGE, key without `user_id` (line 545)
- `db/bigquery.py:796-817` — `delete_user_portfolio`; positions DELETE without `user_id` (:802-805)
- `db/bigquery.py:652` — positions SELECT `WHERE p.user_id = @user_id`
- `src/portfolio_calendar.py:113-124` — MTD computed from calendar rows
- `static/index.html:1298-1304` — `initClientId` (localStorage UUID)

## Architecture Insights

- **Identity is a single string with two provenances**: signed Firebase UID (JWT) or unauthenticated browser UUID (API-key path). Isolation strength is asymmetric: cryptographic on the JWT path, honor-system on the API-key path.
- **Per-endpoint DI, not middleware** — the ticket's "middleware" is in reality extending `_get_client_id`/`_get_role`; PUL-71 confirmed no signature change is needed across the ~30 call sites.
- **Ownership checks live at the API layer** (portfolio_id membership via `list_user_portfolios`), with two SQL statements not independently scoped — any plan should decide whether to add `user_id` predicates as defense-in-depth (cheap) rather than rely on layered checks alone.
- **Admin global vs per-user surfaces are physically separate** (different endpoints, different tables) — "API-key admin keeps global view" is already structurally guaranteed; the risk is only regression.
- **Caches already key on resolved identity** — no re-keying work needed (PUL-60's cache-key concern is resolved by the PUL-71 seam).

## Historical Context (from prior changes)

- `context/archive/2026-06-22-my-wallet-watchlist/plan.md:5,87,340-348` — client_id born as "throwaway, registration-free per-browser UUID" (PUL-28); real registration explicitly out of scope.
- `context/archive/2026-06-27-pul-65/research.md:59,339` — "No users table, ever… name it `user_id` for clarity but the value is exactly the `client_id` from the header" — origin of the naming split.
- `context/archive/2026-07-17-pul-71-auth-foundation/plan.md:192,259` — `_get_client_id` JWT override = "grunt pod PUL-74"; "ujednolicenie = PUL-74" (no migration done).
- `context/archive/2026-07-17-pul-71-auth-foundation/plan-brief.md:62` — `client_id`/`user_id` naming collision = debt for PUL-74.
- `context/archive/2026-07-17-pul-71-auth-foundation/reviews/impl-review.md` F1 — per-user token revocation (denylist) known gap, mitigated by 30-day cap.
- `context/archive/2026-07-18-admin-role-email-accounts/plan.md:8,61-62` — full admin parity (email admin ≡ API-key admin); per-user isolation explicitly deferred to PUL-74.
- `context/archive/2026-07-18-login-register-landing/research.md:78-79` — stale `localStorage.watchlist_client_id` survives logout (frontend cleanup for PUL-74).
- `context/archive/2026-06-30-pul-60/plan.md:169-193` — cache keys per client_id; admin treemap explicitly global.
- `context/archive/2026-06-22-my-wallet-watchlist/reviews/plan-review.md:62` — watchlist INSERT check-then-insert race (possible duplicate rows on double-click) — pre-existing debt, adjacent to any watchlist SQL work.
- `context/archive/2026-06-25-non-admin-portfolio-treemap/research.md:405-423` — wallet registry model, per-user wallet-count constraints enforced at API layer.

## Related Research

- `context/archive/2026-07-17-pul-71-auth-foundation/research.md` — auth seam analysis (the direct predecessor of this document)
- `context/archive/2026-06-29-pul-59-portfolio-calendar/research.md` — calendar data model options (portfolio_snapshots rejected for per-user path)
- `context/archive/2026-06-30-pul-60/research.md` — cache architecture keyed by client_id

## Open Questions

1. **PUL-73 (guest mode) is NOT implemented** — Linear lists it as a prerequisite of PUL-74 ("so we know which endpoints need user_id vs none"), but the change notes say prereqs are PUL-71/72/83. Confirm PUL-74 proceeds without guest mode (likely yes — the JWT/API-key split already defines which endpoints carry identity).
2. **Data continuity for the owner**: historical watchlist/portfolio rows are keyed by the browser UUID; after email login the owner's JWT identity is the Firebase UID → old rows invisible on the JWT path. Options: one-time re-key (UPDATE rows SET identity = <UID> WHERE identity = <browser-uuid>), a merge-on-login mechanism, or accept the split (API-key session still sees old data). Human decision needed — which UUID(s) belong to the owner is not derivable from code.
3. **Should the `X-Client-Id` spoofing hole be closed in PUL-74?** Options: leave as legacy (shared `USER_API_KEY` implies mutual trust), bind API-key sessions to a fixed identity, or drop the anonymous path for authenticated features entirely. The ticket's "Never allow one user's user_id to reach another user's rows" arguably requires closing it.
4. **Rename `watchlist.client_id` → `user_id`?** Documented debt; a rename in BQ means new column + backfill + dual-read window or CREATE TABLE AS — decide if it's worth it now or stays cosmetic debt.
5. Ticket's "Out of scope: Admin view across users (future)" vs change note "API-key admin keeps global view" — reconciled reading: keep the existing global admin treemap intact (regression guard), build no *new* cross-user admin views.

## Follow-up Research 2026-07-18T16:20+02:00 — user decisions (all open questions resolved)

Decisions from the user (Radek), closing the Open Questions above:

1. **PUL-73 (guest mode) is DROPPED** — the ticket was removed from the backlog; it is not a prerequisite and will never land. Per-user features are for registered users only.
2. **Per-user endpoints go JWT-only** — the core product decision: registration exists (PUL-72), so every registered user has their own wallets/watchlist. The anonymous `X-Client-Id` path is **retired** on all 12 per-user endpoints; no session → 401. This closes the spoofing hole (Open Question 3) by construction. API-key admin keeps the global `/admin/*` views; non-admin API-key sessions lose watchlist/portfolio access (accepted).
3. **Old data: re-key onto the owner's account** — one-time BQ UPDATE moving rows keyed by the owner's browser UUID to their Firebase UID. The owner supplies the UUID from `localStorage.watchlist_client_id`; execution stays human-run (project rule: data-mutating infra scripts are human-in-the-loop). No other browser UUIDs are migrated.
4. **Rename `watchlist.client_id` → `user_id`: YES** — pay the naming debt now. Additive migration path (BQ column adds via `ensure_schema_current`-style migration, backfill, code switch; the final column DROP is destructive → human-only per project rules). `user_id` is not a BQ reserved keyword (verified in PUL-83 for `role`; same list applies).

**Consequences for the plan (blast radius of the JWT-only decision):**

- `_get_client_id` (src/api.py:130-140) becomes a JWT-required `_get_user_id` (401 without a valid session); `_CLIENT_ID_HEADER` (src/api.py:100) and the 400-on-missing-header behavior disappear.
- Frontend: `initClientId` / `localStorage.watchlist_client_id` (static/index.html:1298-1304) and the 13 `X-Client-Id` header sends can be removed; the stale-localStorage-after-logout debt dies with them.
- Tests: the eight missing-`X-Client-Id` → 400 tests in tests/test_api.py flip to 401-without-session tests; the JWT-precedence test becomes the happy path; e2e conftest fakes keyed by `client_id` (tests/e2e/conftest.py:172-221) need re-keying to session users (lesson feedback-e2e-conftest-bq-mocking applies).
- New isolation test (ticket success criterion): user A with a valid JWT cannot read/delete user B's rows — now expressible purely via two JWT sessions.
- The two under-scoped SQL statements (MERGE db/bigquery.py:545, cascade DELETE db/bigquery.py:802-805) should gain `user_id` predicates as defense-in-depth while the schema is being touched anyway.
