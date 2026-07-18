# Admin Role for Email Accounts (PUL-83) — Implementation Plan

## Overview

Give email/password accounts a real role: `role` column in BQ `users`, read once at
login, embedded as a claim in the server-signed session JWT, honored by `_get_role`
and the UI. Ends with a one-time human-run BQ UPDATE promoting the owner's account
to `admin`. Full UI parity: an email admin sees exactly what an API-key admin sees.

Tracking: Linear PUL-83, GitHub #146.

## Current State Analysis

Verified in-session (2026-07-18, post PUL-72):

- `_get_role` (src/api.py:104-119) is cookie-first and returns hardcoded `"user"`
  for ANY valid JWT session; `admin` exists only via `X-API-Key == ADMIN_API_KEY`.
  `_require_admin` (src/api.py:122) gates 3 admin endpoints server-side.
- `_USERS_SCHEMA` (db/bigquery.py:851-856) has no `role` column: user_id, email,
  created_at, last_login_at. `ensure_users_schema_current()` migrates additively.
- `insert_user` (db/bigquery.py:877) and `upsert_user_login` (db/bigquery.py:903,
  MERGE) both INSERT explicit column lists — old rows will have `role = NULL`.
- `create_session_token` (src/auth.py:68) builds the payload; `decode_session_token`
  requires exp/iat + user_id/email only; `refresh_session_if_stale` (src/auth.py:145)
  re-issues the token from the old payload — a new claim must be carried forward.
- `/api/auth/me` (src/auth.py:388) answers from the JWT alone — the PUL-71 ticket
  requirement "no BQ round-trip" holds and must keep holding.
- UI gates everything on one `role` variable (static/index.html:1181): showDashboard
  → renderHeaders/injectAdminOnlyChrome/admin-table class; score/sentiment/CSV/
  x-history/delete all branch on `role === 'admin'`. After email login the code
  currently hardcodes `'user'` in `_enterUserSession` and `_bootProbeSession`.
- E2E conftest patches `src.auth.verify_password_rest` (echoes submitted email,
  rejects `E2E_WRONG_PASSWORD`), `src.auth.insert_user`, `src.auth.upsert_user_login`;
  JWT_SECRET is set. `tests/e2e/test_landing_auth.py` covers the email auth flows.

## Desired End State

- Owner logs in with email → dashboard shows the full admin view (Score column,
  sentiment, sources, delete, CSV export, X-post history) — identical to API-key admin.
- Fresh registrations and every other email account stay `user`.
- Anyone's role changes take effect at their next login (claim fixed at login).
- API-key paths (admin and user) unchanged.

### Key Discoveries:

- Server-side admin enforcement already exists (`_require_admin`) — the change is
  about *issuing* the admin identity, not about gating.
- `upsert_user_login`'s MATCHED branch must NOT touch `role` — otherwise every
  login would wipe the promotion back to NULL/user.
- `role` is not a BigQuery reserved keyword — no backticks needed (lessons.md rule
  checked against the reserved-keywords list).
- `verify_password_rest` mock returns a constant uid — phase 2's role-aware e2e
  needs per-email uids so the `get_user_role` fake can distinguish admin.

## What We're NOT Doing

- No role re-read during sliding refresh or `/api/auth/me` (decision 2026-07-18:
  claim fixed at login; demotion = re-login, capped at 30d by the absolute session cap).
- No backfill UPDATE of existing rows (decision: `NULL` means `user`, read via COALESCE).
- No role management UI/endpoint — promotion is a one-time human-run BQ UPDATE.
- No reduced "browser admin" subset (decision: full parity; server gates stay).
- No per-user data isolation (PUL-74), no changes to API-key auth or `/auth/role`.

## Implementation Approach

Two phases, each independently green and committable:

1. **Backend** — schema + `get_user_role` + role claim in the token + `_get_role`
   honoring it + `/me` and login/register responses exposing it + unit tests + e2e mocks.
2. **UI + e2e** — UI consumes the server-provided role (login response + boot probe),
   role-aware e2e fakes, admin-flow e2e tests, faro-v8 sync. Manual close: owner runs
   the promotion UPDATE post-merge and verifies the admin view on prod.

## Critical Implementation Details

**Role source of truth**: the role reaches the client only as a claim inside the
server-signed JWT and in server response bodies. `sessionStorage.role` stays a UX
hint — every admin endpoint keeps `_require_admin` server-side.

**Login availability over freshness**: if `get_user_role` raises `BigQueryError`
during login, log a warning and default to `"user"` — a BQ blip must not turn into
a 5xx on login. The owner can re-login later to regain admin.

**MERGE containment**: `upsert_user_login` MATCHED branch updates `last_login_at`
only; `role` appears solely in the NOT MATCHED INSERT (explicit `'user'`). Lock this
with a query-string regression test (lessons.md pattern).

**Legacy tokens**: sessions issued before this change carry no `role` claim —
`payload.get("role", "user")` everywhere; nobody gets logged out by the deploy.

## Phase 1: Backend — role column, claim, gates

### Overview

Ship the full server-side role path: BQ column + read function, claim issued at
login, honored by `_get_role`, exposed via `/me` and the login/register responses.

### Changes Required:

#### 1. BQ schema + role read

**File**: `db/bigquery.py`

**Intent**: Add the `role` column and a single-row read used only at login.

**Contract**: `_USERS_SCHEMA` += `SchemaField("role", "STRING", mode="NULLABLE")`
(migration lands via existing `ensure_users_schema_current()` at startup). New
`get_user_role(user_id: str) -> str`: parameterized
`SELECT COALESCE(role, 'user') FROM users WHERE user_id = @user_id LIMIT 1`;
no row → `"user"` (self-heal window); wraps errors in `BigQueryError`; docstring
states the NULL-means-user semantics. `insert_user` INSERT gains explicit
`role` = `'user'`. `upsert_user_login` NOT MATCHED INSERT gains `role` = `'user'`;
MATCHED branch unchanged (must never touch `role`).

#### 2. Role claim in the session token + login read

**File**: `src/auth.py`

**Intent**: Issue the role claim at login/register and expose it wherever identity
is answered.

**Contract**: `create_session_token(..., role: str = "user")` adds `"role"` to the
payload; `refresh_session_if_stale` passes `payload.get("role", "user")` through.
`login`: after `verify_password_rest` (and alongside `upsert_user_login`), call
`get_user_role(user_id)`; on `BigQueryError` → warn + `"user"`. `register`: always
`"user"`. `_session_response` gains the role and returns
`{"user_id", "email", "role"}` for both endpoints. `/api/auth/me` returns
`payload.get("role", "user")` — still zero BQ.

#### 3. `_get_role` honors the claim

**File**: `src/api.py`

**Intent**: JWT sessions map to their claimed role instead of hardcoded `"user"`.

**Contract**: in the cookie branch, `return "admin" if payload.get("role") == "admin"
else "user"` — unknown/missing claim values degrade to `"user"`. API-key branches
untouched.

#### 4. Unit tests

**File**: the PUL-71 auth unit-test module (locate via `grep -l create_session_token tests/`) + `tests/test_api.py`

**Intent**: Lock the claim contract and the SQL strings.

**Contract**: (a) token issued with `role="admin"` decodes with that claim; default
token → `"user"`; (b) `_get_role` with an admin-claim cookie → `"admin"`, legacy
payload without claim → `"user"`, garbage claim value → `"user"`; (c) `/me` includes
`role`; (d) login returns 200 + `role: "user"` when `get_user_role` raises
`BigQueryError`; (e) query-string regressions: `get_user_role` query contains
`COALESCE(role, 'user')`; `upsert_user_login` MATCHED UPDATE clause does NOT contain
`role` while the INSERT column list does; `insert_user` inserts `role`.
(f) update the three exact-equality response asserts in
`tests/test_auth_api.py:60,134,239` — register/login/me bodies gain `"role"`.

#### 5. E2E mock registration

**File**: `tests/e2e/conftest.py`

**Intent**: Patch the new BQ function (conftest rule: every BQ function an endpoint
calls must be mocked) and make identities distinguishable for phase 2.

**Contract**: `_fake_verify_password_rest` returns `("e2e-uid-" + email, email)`
(uid now embeds the email) AND the `firebase_auth.create_user` mock
(conftest:439) becomes a side_effect reading its `email` kwarg and returning
`SimpleNamespace(uid="e2e-uid-" + email)` — both mocks MUST issue the same uid
for the same email, or a register→login sequence would split portfolio/watchlist
state across two identities. New `E2E_ADMIN_EMAIL = "admin@example.com"` +
`patch("src.auth.get_user_role", side_effect=...)` returning `"admin"` iff the
user_id starts with `"e2e-uid-" + E2E_ADMIN_EMAIL`, else `"user"`. Existing tests
keep passing (they never inspect the uid).

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/ --ignore=tests/e2e -q`
- Role-claim contract tests green (claim in token, `_get_role` mapping, `/me` role, legacy fallback, login BQ-failure fallback)
- Query-string regressions green (COALESCE read; MERGE UPDATE without `role`, INSERTs with `role`)
- Full e2e suite still green: `uv run pytest tests/e2e -q`

#### Manual Verification:

- BQ round-trip (lessons.md — mocked tests don't validate SQL): run
  `scripts/test_bq_users.py` (update its expected-columns assert at ~line 90 to
  include `role` first — part of this phase) against real BQ; confirm the `role`
  column lands via `ensure_users_schema_current()` and `get_user_role` returns
  `"user"` for an existing (NULL-role) row

**Implementation Note**: After this phase and all automated verification passes,
pause for manual confirmation before proceeding to Phase 2.

---

## Phase 2: UI consumes the role + e2e admin flow

### Overview

UI stops hardcoding `'user'` after email auth, e2e proves an email admin gets the
admin view, and the owner's promotion happens post-merge.

### Changes Required:

#### 1. Role from server responses

**File**: `static/index.html` (and byte-identical `static/faro-v8.html` via Copy-Item)

**Intent**: Use the role the server just returned instead of assuming `user`.

**Contract**: login/register success handlers parse the response body and call
`_enterUserSession(data.role)`; `_enterUserSession(role)` stores it in
`sessionStorage.role` and passes it to `showDashboard(role)` (fallback `'user'` when
absent). `_bootProbeSession` uses the `role` field from the `/api/auth/me` JSON the
same way. No other UI branches change — everything downstream already keys off
`role === 'admin'`.

#### 2. E2E admin-flow tests

**File**: `tests/e2e/test_landing_auth.py`

**Intent**: Prove the full-parity decision end to end against the role-aware mocks.

**Contract**: new tests — (a) login with `E2E_ADMIN_EMAIL` → dashboard shows the
admin surface (Score column header visible / `#data-table` has `admin-table` class)
and survives reload via boot probe still as admin; (b) regression: a non-admin email
login shows no Score column and no sentiment text (locator discipline per /10x-e2e:
role/label first, container scoping where duplicated).

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/ --ignore=tests/e2e -q`
- Full e2e suite green incl. new admin-flow tests: `uv run pytest tests/e2e -q`
- `static/index.html` and `static/faro-v8.html` byte-identical (hash check)

#### Manual Verification:

- Local round-trip (port-8123 pattern, real Firebase+BQ from .env): email login as a
  normal user unchanged (no admin columns); reload keeps session
- Post-merge (CI deploys; verify `/health` first): owner runs the promotion UPDATE
  (human-run, SQL below), logs in on prod with email → full admin view (Score,
  sentiment, CSV, X-post history); a second (non-promoted) account still sees the
  user view

Promotion SQL (human-run, one time — replace `<dataset>` with `BIGQUERY_DATASET`):

```sql
UPDATE `puls-gpw.<dataset>.users`
SET role = 'admin'
WHERE email = 'radoslaw.jjd@gmail.com';
```

---

## Testing Strategy

### Unit Tests:

- Claim lifecycle: issue (admin/default), decode, refresh carry-over, legacy fallback
- `_get_role` mapping incl. garbage claim values; `/me` role field
- Login resilience: BQ error → role "user", still 200
- SQL string regressions (COALESCE; MERGE role containment; INSERT columns)

### Integration Tests:

- E2E: admin email → admin dashboard (+ reload via probe); user email → user
  dashboard; existing 82-test suite stays green

### Manual Testing Steps:

1. Phase 1: local BQ schema round-trip (`role` column lands; NULL row reads "user")
2. Phase 2: local user-flow regression; post-merge owner promotion UPDATE + prod
   admin/user verification

## Performance Considerations

- Exactly one extra BQ query per login (`get_user_role`); zero added cost on any
  other request — `/me`, refresh, and all data endpoints stay BQ-free for identity.

## Migration Notes

- Schema migration is additive and automatic (`ensure_users_schema_current` at
  startup). No backfill: NULL role reads as `user` via COALESCE.
- Legacy sessions (no claim) keep working as `user`; roles activate at next login.
- Rollback = revert PR; the `role` column is inert without the claim code.

## References

- Ticket: Linear PUL-83 / GitHub #146
- Auth foundation: `context/archive/2026-07-17-pul-71-auth-foundation/plan.md`
- Landing/auth UI: `context/archive/2026-07-18-login-register-landing/plan.md`
- Lessons applied: BQ round-trip + query-string regression (`context/foundation/lessons.md`)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Backend — role column, claim, gates

#### Automated

- [x] 1.1 Unit tests pass: `uv run pytest tests/ --ignore=tests/e2e -q`
- [x] 1.2 Role-claim contract tests green (token claim, _get_role mapping, /me role, legacy + BQ-failure fallbacks)
- [x] 1.3 Query-string regressions green (COALESCE read; MERGE UPDATE without role, INSERTs with role)
- [x] 1.4 Full e2e suite still green: `uv run pytest tests/e2e -q`

#### Manual

- [x] 1.5 BQ round-trip: role column lands via ensure_users_schema_current; NULL-role row reads "user"

### Phase 2: UI consumes the role + e2e admin flow

#### Automated

- [ ] 2.1 Unit tests pass: `uv run pytest tests/ --ignore=tests/e2e -q`
- [ ] 2.2 Full e2e suite green incl. admin-flow tests: `uv run pytest tests/e2e -q`
- [ ] 2.3 index.html and faro-v8.html byte-identical (hash check)

#### Manual

- [ ] 2.4 Local round-trip: user email flow unchanged, reload keeps session
- [ ] 2.5 Post-merge: owner promotion UPDATE run + prod email login shows full admin view; non-promoted account stays user
