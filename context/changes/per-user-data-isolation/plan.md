# Per-User Data Isolation (PUL-74) — Implementation Plan

## Overview

Scope all per-user surfaces (watchlist, portfolio positions/wallets, treemap, calendar — and transitively MTD) to the authenticated JWT `user_id`. The anonymous `X-Client-Id` path is retired: the 12 per-user endpoints require a valid session (401 without one). The `watchlist` table gains a `user_id` column (additive migration + backfill), two under-scoped SQL statements gain `user_id` predicates, and the owner's historical rows are re-keyed onto their Firebase UID by a human-run script. Admin global views (`/admin/*`) stay untouched.

## Current State Analysis

Full picture in `context/changes/per-user-data-isolation/research.md`. The essentials:

- `_get_client_id` (src/api.py:130-140) already prefers the JWT `user_id` (Firebase UID) and falls back to the **unverified** `X-Client-Id` header — the fallback is the only real isolation hole (anyone with the shared `USER_API_KEY` can impersonate any identity).
- Only `watchlist` (db/bigquery.py:460-466) uses the column name `client_id`; portfolio tables already use `user_id`.
- Two SQL statements rely solely on API-layer ownership checks: positions-upsert MERGE key without `user_id` (db/bigquery.py:545) and cascade positions DELETE without `user_id` (db/bigquery.py:802-805).
- Frontend generates `watchlist_client_id` in localStorage (static/index.html:1298-1304) and sends `X-Client-Id` on 13 call sites; every data fetch already does `401 → doLogout()`.
- E2E per-user tests log in via the "Mam klucz API" UI path (e.g. tests/e2e/test_my_wallet.py:6-12); conftest already fakes Firebase email auth (uid = `"e2e-uid-" + email`, tests/e2e/conftest.py:29-44).
- `ensure_watchlist_schema_current` exists (db/bigquery.py:482) — the additive-column migration pattern from PUL-83 (`role`) applies directly.
- PUL-73 (guest mode) was dropped from the backlog — per-user features are for registered users only.

## Desired End State

- A request without a valid `session` cookie gets **401** from all 12 per-user endpoints; `X-Client-Id` is gone from backend and frontend.
- Two registered users see fully disjoint watchlists/portfolios; user A cannot read or mutate user B's rows even with hand-crafted requests (verified by tests).
- `watchlist` rows carry `user_id`; all watchlist SQL predicates use `user_id` (old `client_id` column still present but unused, awaiting human DROP).
- API-key sessions: admin keeps `/admin/*` global views; per-user nav is hidden in the UI; global read endpoints (announcements, autocomplete) still accept `USER_API_KEY`/`ADMIN_API_KEY`.
- The owner, logged in by email, sees their pre-existing watchlist and portfolios (re-keyed by the migration script).

### Key Discoveries:

- Frontend 401-handling already exists on every data fetch (`if (r.status === 401) { doLogout(); return; }` — 15 sites) — the "auto-logout → landing" decision is already implemented; Phase 3 only removes the header machinery.
- E2E fake Firebase auth exists (tests/e2e/conftest.py:29-44); switching per-user e2e tests to email login needs no new infrastructure.
- Watchlist inserts are query-DML (not streaming inserts), so a startup backfill UPDATE is safe from streaming-buffer conflicts.
- `user_id` is not a BQ reserved keyword (verified in PUL-83; lesson `BigQuery — reserved keywords` applies to SQL wording regardless).

## What We're NOT Doing

- **No guest mode** — PUL-73 is dropped, not deferred.
- **No `USER_API_KEY` retirement** — the key keeps working for global read endpoints (announcements, autocomplete). Separate ticket if ever.
- **No DROP of `watchlist.client_id`** — destructive, human-only, after prod verification (runbook in Migration Notes).
- **No admin cross-user browsing UI** — admin keeps today's global `/admin/*` views; no new per-user admin views (Linear: future).
- **No token revocation denylist** (known PUL-71 gap, separate concern).
- **No fix for the watchlist check-then-insert race** (pre-existing debt, my-wallet-watchlist plan-review:62) — unchanged semantics, now keyed by `user_id`.
- **No migration of anonymous UUIDs other than the owner's** — other browser-UUID rows become unreachable dead rows (cleaned up whenever `client_id` is dropped).

## Implementation Approach

Additive-first ordering so every commit deploys safely: (1) schema + SQL scoping while the old identity path still works, (2) flip the API dependency to JWT-only, (3) strip the frontend, (4) migrate e2e auth, (5) ship the human-run re-key script. Single PR branch; deploy happens on merge via CI (never manual `gcloud run deploy`).

## Critical Implementation Details

**`faro-v8.html` must stay byte-identical to `index.html`** — after every Phase 3 edit, copy `static/index.html` → `static/faro-v8.html` (established invariant from the PUL-83 session).

**Backfill ordering** — `ensure_watchlist_schema_current` runs at app startup (src/api.py:292-293 pattern); extend it (or add a sibling called next to it) so the idempotent backfill (`UPDATE … SET user_id = client_id WHERE user_id IS NULL`) executes before any request reads the new predicate. Same deploy = no window where reads on `user_id` miss un-backfilled rows.

**Dual-write until DROP** — watchlist INSERT keeps writing `client_id` (same value as `user_id`) so rolling back to the previous revision cannot lose rows. The dual-write dies together with the column DROP (human step).

**E2E test independence after the auth switch** — conftest fake-BQ state persists for the whole live-server session; per-test isolation previously came from a fresh `watchlist_client_id` per browser context. After the email-login switch, isolation must come from **unique emails per test** (timestamp suffix), per the `/10x-e2e` independence rule.

**Mocked tests don't parse SQL** (lesson) — after Phases 1-2, run the real-BQ round-trip `scripts/test_bq.py` (extended for watchlist `user_id`) as a manual gate; add cheap string-assert regression tests on the query texts.

---

## Phase 1: BQ — `watchlist.user_id` + tightened predicates

### Overview

Additive schema migration and SQL scoping while the legacy identity path still works. Deploy-safe on its own.

### Changes Required:

#### 1. Watchlist schema + backfill

**File**: `db/bigquery.py`

**Intent**: Add `user_id` STRING NULLABLE to `_WATCHLIST_SCHEMA`; extend `ensure_watchlist_schema_current` (db/bigquery.py:482) to add the column *and* run the idempotent backfill so old rows become readable under the new predicate at startup.

**Contract**: `ensure_watchlist_schema_current()` after this change guarantees: column exists AND no row has `user_id IS NULL` (backfill `SET user_id = client_id WHERE user_id IS NULL`). Idempotent, safe on every startup.

#### 2. Watchlist SQL switches to `user_id`

**File**: `db/bigquery.py`

**Intent**: `add_watchlist_ticker`, `remove_watchlist_ticker`, `list_watchlist_tickers` (:979-1039) and the join subquery in `list_announcements_for_watchlist` (:1644-1652) predicate on `user_id` instead of `client_id`; rename their parameters `client_id` → `user_id`. INSERT dual-writes both columns with the same value (rollback safety).

**Contract**: all four watchlist query texts contain `user_id = @user_id`; INSERT column list contains both `user_id` and `client_id`.

#### 3. Defense-in-depth predicates on portfolio SQL

**File**: `db/bigquery.py`

**Intent**: The positions-upsert MERGE (:539-555) and the cascade positions DELETE inside `delete_user_portfolio` (:802-805) gain a `user_id` predicate so no single missing API-layer check can ever cross user boundaries.

**Contract**: MERGE `ON` clause becomes `ON T.portfolio_id = S.portfolio_id AND T.ticker = S.ticker AND T.user_id = S.user_id` (S carries `@user_id`); cascade DELETE becomes `WHERE user_id = @user_id AND portfolio_id = @portfolio_id`. Behavior for legitimate calls is unchanged — ownership was already enforced upstream.

#### 4. Unit tests for schema + predicates

**File**: `tests/test_bigquery.py`

**Intent**: Update watchlist schema assertions (new column), flip predicate assertions to `user_id = @user_id`, assert dual-write column list, assert the MERGE/cascade-DELETE now carry `user_id` (string asserts on query text, per the reserved-keyword lesson).

**Contract**: existing watchlist tests updated in place; new asserts on the two tightened statements.

#### 5. Real-BQ round-trip coverage

**File**: `scripts/test_bq.py`

**Intent**: Extend the round-trip script to exercise the migrated watchlist schema (ensure → insert → list → remove under `user_id`), since mocked tests never hit the SQL parser.

**Contract**: script calls `ensure_watchlist_schema_current()` (not only `create_*`) before exercising queries.

### Success Criteria:

#### Automated Verification:

- Full test suite green: `uv run pytest`
- Query-text regression asserts pass (watchlist `user_id` predicates, dual-write, MERGE/DELETE scoping): `uv run pytest tests/test_bigquery.py`

#### Manual Verification:

- Real-BQ round-trip green: `uv run python scripts/test_bq.py` (against dev credentials; verifies column add + backfill + watchlist CRUD under `user_id`)

---

## Phase 2: API — JWT-only identity (401 without session)

### Overview

Flip the identity dependency: per-user endpoints require a valid JWT session; the `X-Client-Id` header disappears from the backend.

### Changes Required:

#### 1. `_get_client_id` → `_get_user_id`

**File**: `src/api.py`

**Intent**: Replace `_get_client_id` (:130-140) with `_get_user_id`: valid session → `payload["user_id"]`; no/invalid session → 401. Delete `_CLIENT_ID_HEADER` (:100). All 12 `Depends(_get_client_id)` sites switch to `Depends(_get_user_id)`; endpoint-local variables rename `client_id` → `user_id` (cache keys keep their existing string shapes, now always keyed by UID).

**Contract**: `def _get_user_id(request: Request) -> str` — raises `HTTPException(401)` when `session_payload_from_request(request)` is `None`. `_get_role` and the API-key path for global endpoints are untouched.

#### 2. Unit tests — 401 flips + cross-user isolation

**File**: `tests/test_api.py`

**Intent**: The eight missing-`X-Client-Id` → 400 tests become no-session → 401 tests. The JWT-precedence test becomes the plain happy path. Add the ticket's success-criterion tests with two JWT cookies (users A and B): A's watchlist invisible to B; B's DELETE on A's ticker removes nothing; B gets 403/404 for A's `portfolio_id` on positions read/write/delete and calendar.

**Contract**: no test sends `X-Client-Id` anymore; two-user isolation tests assert both response codes and that the underlying (mocked) DB calls were invoked with the caller's UID only.

### Success Criteria:

#### Automated Verification:

- Full unit suite green: `uv run pytest tests/`
- New isolation tests pass and fail if a `user_id` predicate is removed (deliberate-break check on one predicate)

#### Manual Verification:

- Local run: request to `GET /watchlist` without cookie → 401; with a logged-in session → 200 and own data
- `GET /announcements` with `USER_API_KEY` still works (global endpoints unaffected)

---

## Phase 3: Frontend — remove `X-Client-Id`, hide per-user nav for API-key sessions

### Overview

The SPA stops carrying the browser UUID; per-user views are only offered to JWT sessions.

### Changes Required:

#### 1. Remove client-id machinery

**File**: `static/index.html`

**Intent**: Delete `initClientId`, the `clientId` variable and all 13 `'X-Client-Id': clientId` header entries; remove the stale `watchlist_client_id` localStorage key on boot (one-time cleanup `localStorage.removeItem`). Existing `401 → doLogout()` handlers stay as the session-expiry behavior.

**Contract**: no reference to `watchlist_client_id` or `X-Client-Id` remains in the file.

#### 2. Hide per-user navigation for API-key sessions

**File**: `static/index.html`

**Intent**: When the session is API-key-based (`sessionStorage.apiKey` set, no JWT session), the nav/views for Obserwowane (watchlist), Portfel (positions/wallets), Treemapa and Kalendarz are not rendered; admin API-key keeps `/admin/*` views (X-history, admin treemap). JWT sessions see everything per their role.

**Contract**: view-routing guard mirrors the existing role-guard pattern (`_buildXHistoryChrome`-style early return); deep links (`?view=portfolio-positions`) for an API-key session fall back to the default view instead of rendering a dead view.

#### 3. Sync copy

**File**: `static/faro-v8.html`

**Intent**: Byte-identical copy of the updated `static/index.html` (established invariant).

**Contract**: `fc /b static\index.html static\faro-v8.html` reports no differences.

### Success Criteria:

#### Automated Verification:

- Unit suite still green: `uv run pytest tests/ --ignore=tests/e2e`
- No `X-Client-Id` / `watchlist_client_id` occurrences: `grep -c` returns 0 for both in `static/index.html`
- `faro-v8.html` byte-identical to `index.html`

#### Manual Verification:

- Email login → watchlist/portfolio/treemap/calendar all work as before
- API-key login (admin key) → per-user nav absent, admin views (X-history, admin treemap) present
- API-key login (user key) → only global views (announcements, autocomplete search)
- Logout → landing; re-login by email → same data (identity = UID, no localStorage involvement)

---

## Phase 4: E2E — per-user tests switch to email login

### Overview

Browser tests for per-user features authenticate like real users now: fake-Firebase email login instead of the API key.

### Changes Required:

#### 1. Login helpers in per-user specs

**Files**: `tests/e2e/test_my_wallet.py`, `test_watchlist_guard.py`, `test_watchlist_sentiment.py`, `test_portfolio_positions.py`, `test_portfolio_wallets.py`, `test_portfolio_calendar.py`, `test_user_portfolio_treemap.py`, `test_etf_portfolio.py`, and any other spec whose `_login` uses the API-key panel for per-user views

**Intent**: Replace the API-key `_login` helper with an email register-or-login flow (fake Firebase accepts any credentials; uid = `"e2e-uid-" + email`). Each test uses a unique email (timestamp suffix) for state isolation, since conftest fake-BQ state persists across the server session.

**Contract**: locators follow the existing login-form roles/labels (getByRole/getByLabel — no CSS selectors); no `page.waitForTimeout`.

#### 2. Conftest adjustments

**File**: `tests/e2e/conftest.py`

**Intent**: Per-user fake-BQ functions already key by the identity string (now always a uid) — verify no fake depends on `X-Client-Id` reaching the server; keep API-key envs for specs that still test the key path (admin views, landing auth). Follow lesson `feedback-e2e-conftest-bq-mocking`: every BQ function the JWT path calls must be faked in the live-server context.

**Contract**: full e2e suite runs against the live server with no real BQ/Firebase calls.

#### 3. Isolation spot-check in e2e (cheap)

**File**: one of the watchlist specs (e.g. `tests/e2e/test_my_wallet.py`)

**Intent**: One scenario: user A adds a ticker, logs out, user B (different email) logs in and sees an empty watchlist. This is the browser-level echo of the Phase 2 isolation tests — not a full two-user matrix (that stays for `/10x-e2e` if ever needed).

**Contract**: single test, standalone, own setup/cleanup, unique emails.

### Success Criteria:

#### Automated Verification:

- Full e2e suite green: `uv run pytest tests/e2e`
- Whole suite green: `uv run pytest`

#### Manual Verification:

- Skim one headed run (`--headed`) of the my-wallet spec to confirm the email-login flow drives the real UI

---

## Phase 5: Owner data re-key script (human-run)

### Overview

One-time migration tool moving the owner's historical rows from their browser UUID to their Firebase UID. Code lands in the PR; execution is human-only.

### Changes Required:

#### 1. Migration script

**File**: `scripts/migrate_owner_identity.py`

**Intent**: CLI: `--old-uuid <browser-uuid> --new-uid <firebase-uid> [--dry-run]`. Dry-run prints per-table matched-row counts; real run executes UPDATEs re-keying `watchlist` (`user_id` and `client_id` both — dual-write consistency), `user_portfolios.user_id`, `user_portfolio_positions.user_id`, then prints post-update counts. No DELETEs anywhere.

**Contract**: entry-point script → `load_dotenv()` first, before any `db.*` import; `with_quota_project` guard applies (rules `db-bigquery.md`); parameterized queries only. Exit code 0 only when all statements succeed (lesson: verify by exit code, not grep).

#### 2. Script test

**File**: `tests/test_migrate_owner_identity.py`

**Intent**: Mocked-client tests: dry-run issues only SELECT counts; real run issues three UPDATEs with both identifiers bound as parameters; string asserts on query texts.

**Contract**: no real BQ access in tests.

### Success Criteria:

#### Automated Verification:

- Script tests green: `uv run pytest tests/test_migrate_owner_identity.py`
- Full suite green: `uv run pytest`

#### Manual Verification:

- **Human runbook (owner, on prod, after deploy):** read `localStorage.watchlist_client_id` from the old browser; `uv run python scripts/migrate_owner_identity.py --old-uuid <uuid> --new-uid <uid> --dry-run` → verify counts look right; run without `--dry-run`; log in by email and confirm watchlist + wallets show the historical data
- Prod smoke after merge/deploy: `/health` OK; two fresh accounts see disjoint data

---

## Testing Strategy

### Unit Tests:

- Watchlist SQL predicate/dual-write string asserts; MERGE + cascade-DELETE `user_id` asserts (Phase 1)
- 401-without-session across all 12 endpoints; two-user isolation matrix for watchlist + portfolio + calendar (Phase 2)
- Migration script query-shape tests (Phase 5)

### Integration Tests:

- Real-BQ round-trip `scripts/test_bq.py` extended for the watchlist migration (manual gate, dev credentials)

### Manual Testing Steps:

1. Email login → add ticker, add position → visible in watchlist/treemap/calendar
2. Second account → sees nothing of account one's data
3. API-key admin → `/admin/*` views work, per-user nav absent
4. No cookie, `curl GET /watchlist` → 401
5. Owner re-key runbook (Phase 5 manual)

## Performance Considerations

None material: same query shapes with one extra predicate; caches keep their key shapes. The startup backfill UPDATE is a one-time small-table DML, idempotent afterwards (matches `WHERE user_id IS NULL` on zero rows).

## Migration Notes

- **Deploy**: merge to master → CI deploys `puls-gpw-api`; verify `/health`.
- **Owner re-key**: human-run per Phase 5 runbook (needs the browser UUID from the owner's old browser profile — ask before running).
- **Column DROP (later, human-only)**: after ≥1 week of verified prod operation, drop `watchlist.client_id` and remove the dual-write + backfill code in a follow-up chore. Destructive → never automated.
- **Rollback**: any phase alone is safe to revert; dual-write guarantees the previous revision still reads `client_id` correctly.

## References

- Research (incl. user decisions follow-up): `context/changes/per-user-data-isolation/research.md`
- Identity seam: `src/api.py:130-140`; JWT: `src/auth.py:68-118`
- Additive-column precedent (PUL-83 `role`): `context/archive/2026-07-18-admin-role-email-accounts/plan.md`
- E2E fake auth: `tests/e2e/conftest.py:29-44`
- Tracking: Linear PUL-74 · GitHub #130

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: BQ — watchlist.user_id + tightened predicates

#### Automated

- [x] 1.1 Full test suite green: `uv run pytest` — 8aea973
- [x] 1.2 Query-text regression asserts pass: `uv run pytest tests/test_bigquery.py` — 8aea973

#### Manual

- [x] 1.3 Real-BQ round-trip green: `uv run python scripts/test_bq.py` — 8aea973

### Phase 2: API — JWT-only identity (401 without session)

#### Automated

- [x] 2.1 Full unit suite green: `uv run pytest tests/` (bez e2e — te wracają do zieleni w Fazie 4 zgodnie z sekwencją planu) — e21cfcf
- [x] 2.2 Isolation tests pass + deliberate-break check on a `user_id` predicate — e21cfcf

#### Manual

- [x] 2.3 Local: `/watchlist` bez cookie → 401; z sesją → własne dane — e21cfcf
- [x] 2.4 `GET /announcements` z `USER_API_KEY` nadal działa — e21cfcf

### Phase 3: Frontend — remove X-Client-Id, hide per-user nav for API-key sessions

#### Automated

- [x] 3.1 Unit suite green: `uv run pytest tests/ --ignore=tests/e2e`
- [x] 3.2 Zero occurrences of `X-Client-Id` / `watchlist_client_id` in `static/index.html` (poza planowaną jednorazową linią `localStorage.removeItem` + komentarzem)
- [x] 3.3 `faro-v8.html` byte-identical to `index.html`

#### Manual

- [x] 3.4 Email login: wszystkie widoki per-user działają
- [x] 3.5 API-key admin: brak nav per-user, widoki admin obecne
- [x] 3.6 API-key user: tylko widoki globalne
- [x] 3.7 Logout → landing; re-login e-mail → te same dane

### Phase 4: E2E — per-user tests switch to email login

#### Automated

- [ ] 4.1 E2E suite green: `uv run pytest tests/e2e`
- [ ] 4.2 Whole suite green: `uv run pytest`

#### Manual

- [ ] 4.3 Headed run my-wallet spec — email-login flow drives the UI

### Phase 5: Owner data re-key script (human-run)

#### Automated

- [ ] 5.1 Script tests green: `uv run pytest tests/test_migrate_owner_identity.py`
- [ ] 5.2 Full suite green: `uv run pytest`

#### Manual

- [ ] 5.3 Owner runbook executed on prod (dry-run → run → e-mail login shows historical data)
- [ ] 5.4 Prod smoke: `/health` OK; dwa świeże konta widzą rozłączne dane
