# My Wallet — Personal Watchlist Implementation Plan

## Overview

Ship a minimal, registration-free per-user identity — a browser-generated UUID
persisted in `localStorage` and sent as an `X-Client-Id` header — and build
"My Wallet," a personal ticker watchlist view, on top of it. This is the
narrow foundation-plus-slice scoped by `frame.md`: per-ticker freemium tiers,
plan limits, and usage stats are explicitly out of scope.

## Current State Analysis

- **No per-person identity exists.** Auth in `src/api.py:45-60` (`_get_role`)
  compares the `X-API-Key` header against two static env vars and returns
  only a role (`"admin"` / `"user"`) — never an individual identity.
- **No client-side UUID precedent.** `static/index.html` persists
  `gdpr_consent_v1` in `localStorage` (set at line 500, read at line 497) and
  `apiKey`/`role` in `sessionStorage` (set at lines 570-571, cleared on
  logout at line 461) — but nothing generates or stores a per-browser id
  today.
- **Profile menu shell (PUL-47) is built but role-gated.** The dropdown
  markup lives at `static/index.html:298-304`
  (`#profile-menu-wrap` → `#profile-menu` → `#logout-btn`). The existing
  pattern for adding a menu item (`static/index.html:768-784`,
  `injectAdminOnlyChrome`-style) inserts a new `<li>` before `#logout-btn`
  — but the whole function returns early at line 761 (`if (r !== 'admin')
  return`), so today every dynamic menu item is admin-only. "My Wallet"
  must NOT be gated this way since it's for the "user" role too.
- **BigQuery has a clean table-foundation template.** `portfolio_snapshots`
  (`db/bigquery.py:189-223`) is the simplest end-to-end example: schema
  constant → `create_*_table_if_not_exists()` → `ensure_*_schema_current()`
  thin wrapper over the generic `ensure_schema_current()`
  (`db/bigquery.py:144-176`). No existing table has upsert-if-absent or
  delete-by-composite-key semantics — `watchlist` will be the first.
- **`/announcements` already has an admin/user split and a filter-clause
  builder to mirror.** `_build_filter_clauses()` (`db/bigquery.py:552-579`)
  builds ticker/company/event_type/date WHERE clauses; `list_announcements_user`
  (`db/bigquery.py:742-792`) calls it with `approved_only=True` and returns a
  narrower column set than the admin variant. `list_distinct_tickers()`
  (`db/bigquery.py:943-956`) already returns every known ticker — the
  existing source for ticker autocomplete (`/autocomplete/tickers` in
  `src/api.py`).
- **Table creation is never wired into the API service.** `main.py:41-43`
  and `post_main.py:241-242` both call `create_*_table_if_not_exists()` +
  `ensure_*_schema_current()` at startup — but `api_main.py` (the
  long-running FastAPI service, `api_main.py:16-19`) calls none of them.
  Since `watchlist` is written and read exclusively through the API (no
  scraper/post-job involvement), this is the first table whose creation
  must be wired into the API service itself.

## Desired End State

A user (current single product-owner, designed for future multi-user) opens
the app, gets a `localStorage`-persisted client id transparently on first
load, opens the profile menu, clicks "My Wallet," sees a dedicated view
listing only announcements for tickers they've added, can add a ticker via
autocomplete (validated against known tickers) and remove one instantly, and
sees a friendly empty state with zero tickers. The watchlist survives
logout/page reload (same browser) but is not shared across browsers/devices
and has no plan/tier concept.

**Verification**: `uv run pytest` passes; `uv run python scripts/test_bq.py`
round-trips a watchlist add/remove against real BigQuery; manually adding and
removing a ticker in the browser updates the view and survives a reload.

### Key Discoveries:

- `static/index.html:768-784` is the exact insertion pattern for the new
  profile-menu entry — but must run unconditionally, not behind the
  `r !== 'admin'` guard at line 761.
- `db/bigquery.py:189-223` (`portfolio_snapshots`) is the schema/create/ensure
  template; `db/bigquery.py:552-579` (`_build_filter_clauses`) and
  `db/bigquery.py:742-792` (`list_announcements_user`) are the query
  templates for the watchlist-filtered announcements list.
- `api_main.py` has no startup table-creation hook today — this plan adds
  the first one.
- `static/index.html` has no fetch wrapper; every endpoint call sets
  `headers: { 'X-API-Key': apiKey }` inline (e.g. lines 1025, 1074, 1154,
  1296) — `X-Client-Id` follows the same inline-header convention, no new
  abstraction.

## What We're NOT Doing

- Per-ticker freemium limits or any free/premium plan concept.
- Payment handling of any kind.
- Usage/visit statistics (visits, last-activity tracking).
- Real registration (email/password/login) — the client id is a throwaway,
  non-durable identity by design.
- A hard UI-facing cap on watchlist size (only a defensive query-side bound).
- A confirm dialog or undo affordance on ticker removal.
- Any change to the existing `_get_role`/API-key auth model — `X-Client-Id`
  is an orthogonal partition key, not a credential.

## Implementation Approach

Three phases, each independently shippable and testable:

1. **BigQuery foundation** — `watchlist` table + CRUD + watchlist-filtered
   announcements query, following the `portfolio_snapshots` template.
2. **API layer** — `X-Client-Id` header dependency, watchlist endpoints,
   server-side ticker validation, and the startup table-creation wiring
   `api_main.py` currently lacks.
3. **Frontend** — client id generation/persistence, unconditional profile
   menu entry, dedicated `#my-wallet-view`, add/remove UI, empty state.

## Critical Implementation Details

**Timing & lifecycle**: `api_main.py` (the long-running Cloud Run service)
has never called any `create_*_table_if_not_exists()` — table creation has
only ever happened in the scheduled `main.py`/`post_main.py` jobs. Because
`watchlist` is written only through API requests, its create/ensure calls
must run at FastAPI startup in `create_app()` (Phase 2), not be added to
`main.py`/`post_main.py`, which have no reason to touch this table. Both
`create_watchlist_table_if_not_exists()` and `ensure_watchlist_schema_current()`
are idempotent (mirroring the existing `NotFound`-catch pattern), so running
them on every cold start of every Cloud Run instance is safe.

## Phase 1: BigQuery Watchlist Foundation

### Overview

Add the `watchlist` table, its CRUD functions, and a watchlist-filtered
announcements query — all in `db/bigquery.py`, following the
`portfolio_snapshots` template exactly.

### Changes Required:

#### 1. Schema, create, and migrate

**File**: `db/bigquery.py`

**Intent**: Define the `watchlist` table schema and add the
create/ensure-schema pair, copying the `portfolio_snapshots` pattern
(`db/bigquery.py:189-223`) verbatim in structure.

**Contract**: New module-level `_WATCHLIST_TABLE_NAME = "watchlist"` and
`_WATCHLIST_SCHEMA` with three `REQUIRED` fields: `client_id` (STRING),
`ticker` (STRING), `added_at` (TIMESTAMP). New functions
`create_watchlist_table_if_not_exists()` and
`ensure_watchlist_schema_current()` (thin wrapper over
`ensure_schema_current(_WATCHLIST_TABLE_NAME, _WATCHLIST_SCHEMA)`), same
signatures and bodies as their `portfolio_snapshots` counterparts.

#### 2. CRUD functions

**File**: `db/bigquery.py`

**Intent**: Add idempotent add, no-op-safe remove, and a plain list of
tickers for one `client_id`.

**Contract**:
- `add_watchlist_ticker(client_id: str, ticker: str) -> None` — INSERT
  guarded by `WHERE NOT EXISTS`, so re-adding an already-watchlisted ticker
  is a silent no-op (no `BigQueryError`, no duplicate row):
  ```sql
  INSERT INTO `watchlist` (client_id, ticker, added_at)
  SELECT @client_id, @ticker, CURRENT_TIMESTAMP()
  WHERE NOT EXISTS (
    SELECT 1 FROM `watchlist` WHERE client_id = @client_id AND ticker = @ticker
  )
  ```
- `remove_watchlist_ticker(client_id: str, ticker: str) -> None` — plain
  `DELETE FROM watchlist WHERE client_id = @client_id AND ticker = @ticker`;
  zero rows matched is not an error.
- `list_watchlist_tickers(client_id: str) -> list[str]` — `SELECT ticker
  FROM watchlist WHERE client_id = @client_id ORDER BY added_at DESC`.

All three follow the existing try/except-wrap-into-`BigQueryError` pattern
used throughout the file (e.g. `db/bigquery.py:630-633`).

#### 3. Watchlist-filtered announcements query

**File**: `db/bigquery.py`

**Intent**: Return announcements for only the tickers in one client's
watchlist, reusing the existing user-facing filter/column shape.

**Contract**: `list_announcements_for_watchlist(client_id: str, page: int =
1, page_size: int = 20, from_dt: datetime | None = None, to_dt: datetime |
None = None) -> list[dict]`. Mirrors `list_announcements_user`
(`db/bigquery.py:742-792`): same `approved_only=True` filter via
`_build_filter_clauses()`, same returned column set. Adds an `INNER JOIN`
against a **bounded** watchlist subquery (defensive cap, not a user-facing
limit — see Open Risks):
```sql
INNER JOIN (
  SELECT ticker FROM `watchlist` WHERE client_id = @client_id LIMIT 200
) AS w ON a.ticker = w.ticker
```

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_bigquery.py`
- Full suite passes: `uv run pytest`
- Watchlist round-trip succeeds: `uv run python scripts/test_bq.py`

#### Manual Verification:

- Inspect the BigQuery console after running `scripts/test_bq.py` and
  confirm the `watchlist` table has the expected three-column schema.

---

## Phase 2: API Layer

### Overview

Expose the watchlist as REST endpoints under a new `X-Client-Id` header
dependency, validate added tickers server-side, and wire watchlist table
creation into the FastAPI startup path for the first time.

### Changes Required:

#### 1. Client-id header dependency

**File**: `src/api.py`

**Intent**: Require an `X-Client-Id` header on every watchlist-related
endpoint; reject requests missing it.

**Contract**: New `APIKeyHeader`-style dependency
`_CLIENT_ID_HEADER = APIKeyHeader(name="X-Client-Id", auto_error=False)` and
`_get_client_id(client_id: str | None = Security(_CLIENT_ID_HEADER)) -> str`
that raises `HTTPException(400, "Missing X-Client-Id header")` when absent
or blank. Mirrors the structure of `_get_role` (`src/api.py:45-50`) but
returns the raw id rather than a parsed role — no validation against any
allow-list, since the id is client-generated and not a credential.

#### 2. Watchlist endpoints

**File**: `src/api.py`

**Intent**: List, add, and remove tickers for the calling client; add is
server-side validated against known tickers.

**Contract**: All three require `role: Role = Depends(_get_role)` (either
role — not `_require_admin`) **and** `client_id: str =
Depends(_get_client_id)`:
- `GET /watchlist` → `list_watchlist_tickers(client_id)`, returns `{"tickers": [...]}`.
- `POST /watchlist/{ticker}` → validate `ticker` is in
  `list_distinct_tickers()`; if not, `HTTPException(422, "Unknown ticker")`.
  Otherwise call `add_watchlist_ticker(client_id, ticker)`, return `{"ticker": ticker, "added": True}`.
- `DELETE /watchlist/{ticker}` → call `remove_watchlist_ticker(client_id,
  ticker)` unconditionally (no-op-safe), return 204.

All three wrap their DB call in the same try/except `BigQueryError` →
`HTTPException(500)` pattern used by every existing endpoint
(e.g. `src/api.py:630-633`).

#### 3. My Wallet announcements endpoint

**File**: `src/api.py`

**Intent**: Same shape as `GET /announcements` for the `"user"` role, but
filtered to the caller's watchlist.

**Contract**: `GET /announcements/my-wallet` with the same query params as
`/announcements` (`page`, `page_size`, `from`, `to` — no `ticker`/`company`/
`event_type` filters, since the watchlist itself is the filter), requiring
`role: Role = Depends(_get_role)` + `client_id: str =
Depends(_get_client_id)`. Calls `list_announcements_for_watchlist(...)`,
maps rows through the existing `AnnouncementUser` model
(`src/api.py` — same model `/announcements` uses for the `"user"` role),
same `BigQueryError` → 500 handling as the existing `/announcements` route.

#### 4. Startup table creation

**File**: `src/api.py`

**Intent**: Ensure the `watchlist` table exists before any request can hit
it — the gap identified in Current State Analysis.

**Contract**: Inside `create_app()` (`src/api.py:128`), register a startup
hook that calls `create_watchlist_table_if_not_exists()` then
`ensure_watchlist_schema_current()`, using FastAPI's `@app.on_event("startup")`
(no existing lifespan context manager to extend).

**Critical**: `tests/e2e/conftest.py`'s `live_server_url` fixture
(`tests/e2e/conftest.py:168-196`) boots a real `uvicorn.Server`, whose
ASGI lifespan genuinely runs this startup hook — unlike `tests/test_api.py`,
which uses a plain `TestClient` without a `with` block (lifespan never
fires there). The fixture's existing `with (...)` patch block
(`tests/e2e/conftest.py:174-181`) already stubs 7 other `src.api.*`
functions for exactly this reason — to keep the e2e server BigQuery-free.
Add `patch("src.api.create_watchlist_table_if_not_exists")` and
`patch("src.api.ensure_watchlist_schema_current")` to that same block, or
every e2e test (not just My Wallet's) will attempt a live BigQuery call at
server startup and fail/hang without GCP credentials.

#### 5. Patch e2e test fixture for the new startup hook

**File**: `tests/e2e/conftest.py`

**Intent**: Keep the e2e test server BigQuery-free now that `create_app()`
performs a real DB call at startup (item 4) — without this, every e2e
test attempts a live BigQuery call and fails/hangs without GCP credentials.

**Contract**: Add `patch("src.api.create_watchlist_table_if_not_exists")`
and `patch("src.api.ensure_watchlist_schema_current")` to the existing
`with (...)` block in `tests/e2e/conftest.py:174-181`, alongside the 7
other `src.api.*` patches already there.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_api.py`
- Full suite passes: `uv run pytest`
- Type checking passes (if configured): `uv run python -m py_compile src/api.py`
- E2E test server boots without a live BigQuery call: `uv run pytest tests/e2e/`

#### Manual Verification:

- `curl` (or browser dev tools) confirms: missing `X-Client-Id` → 400;
  adding an unknown ticker → 422; adding a known ticker twice → no error,
  one row; removing a never-added ticker → no error.

---

## Phase 3: Frontend — My Wallet View

### Overview

Generate and persist the client id, add an unconditional "My Wallet" entry
to the profile menu, build the dedicated view, and wire add/remove ticker
UI with autocomplete and an empty state.

### Changes Required:

#### 1. Client id generation and persistence

**File**: `static/index.html`

**Intent**: Generate a UUID on first load and persist it the same way
`gdpr_consent_v1` is persisted, so it survives logout/reload.

**Contract**: Near the existing `initGdpr()` (`static/index.html:496-504`),
add a `clientId` read at startup:
```js
let clientId = localStorage.getItem('watchlist_client_id');
if (!clientId) {
  clientId = crypto.randomUUID();
  localStorage.setItem('watchlist_client_id', clientId);
}
```
Every watchlist-related `fetch()` call adds `'X-Client-Id': clientId`
alongside the existing inline `'X-API-Key': apiKey` header (matching the
codebase's existing inline-header convention, e.g. `static/index.html:1025`)
— no new fetch wrapper.

#### 2. Profile menu entry

**File**: `static/index.html`

**Intent**: Add a "My Wallet" entry to the profile dropdown, visible to
both roles (unlike the admin-only items injected at lines 768-784).

**Contract**: Insert a new `<li role="none"><button id="my-wallet-btn"
role="menuitem">Moje obserwowane</button></li>` before `#logout-btn`
(`static/index.html:303`) unconditionally — not inside the
`injectAdminOnlyChrome`-style function gated by `r !== 'admin'`
(line 761). Click handler calls `_navigateToView('my-wallet')`.

#### 3. My Wallet view and URL state

**File**: `static/index.html`

**Intent**: A dedicated view, consistent with the existing
view-as-URL-state convention (`_navigateToView`, `_applyUrlState`).

**Contract**: New `#my-wallet-view` DOM block (built lazily, mirroring
`#x-history-view`/`_showXHistoryViewDom()`), with its own pagination state
variables (mirroring `currentPage`/`xpPage`). Extend `_navigateToView`
(`static/index.html:905-916`) with a `'my-wallet'` branch that shows the
view and writes `?view=my-wallet` via `history.pushState`. Extend
`_applyUrlState` (`static/index.html:959-975`) with a matching branch that
fetches `/announcements/my-wallet` on direct load/refresh — both available
to either role (no `role === 'admin'` guard, unlike the treemap/x-history
branches).

#### 4. Add/remove ticker UI

**File**: `static/index.html`

**Intent**: Autocomplete-driven add, instant remove, empty-state CTA. The
add-ticker submit handler must disable its button synchronously, before
the `await fetch(...)` call — `add_watchlist_ticker`'s `INSERT ... WHERE
NOT EXISTS` (Phase 1, item 2) does the existence check and insert as two
logical steps, so two rapid clicks can both pass the check before either
commits, producing a duplicate row. This mirrors the fix `lessons.md`
already prescribes for the SPA-pagination double-click race.

**Contract**: An input + datalist (or reuse the existing autocomplete
pattern backing `f-ticker`) sourced from `/autocomplete/tickers`
(`static/index.html:533`); submit calls `POST /watchlist/{ticker}` and
re-renders the ticker list + table on success, surfaces the 422 response
inline on rejection (no client-side allow-list duplication — server is the
source of truth). Each watchlisted ticker row has a remove button calling
`DELETE /watchlist/{ticker}` with no confirmation, re-rendering immediately.
When `GET /watchlist` returns an empty list, render a message prompting the
user to add their first ticker instead of an empty table.

`renderTable(data, r)` (`static/index.html:1234`) currently hardcodes
`$('table-body')` — not reusable as-is. Add a third parameter,
`renderTable(data, r, containerId = 'table-body')`, so the existing
`fetchAnnouncements` call site (`static/index.html:1031`) needs no change,
while the My Wallet view calls `renderTable(data, role,
'my-wallet-table-body')`. The existing `r !== 'admin'` branch (no delete
button, no `url` column) already matches what My Wallet needs.

### Success Criteria:

#### Automated Verification:

- Full suite passes: `uv run pytest`

#### Manual Verification:

- Fresh browser profile: load the app, confirm a `watchlist_client_id`
  appears in `localStorage` without any user action.
- Open profile menu as the `"user"` role: confirm "Moje obserwowane" is
  present (not admin-gated).
- Add a known ticker via autocomplete: it appears in the watchlist and its
  announcements show in the table.
- Attempt to add an unknown ticker string: rejected with a visible error,
  nothing added.
- Remove a ticker: it disappears immediately, no confirmation prompt.
- Remove the last ticker: empty-state CTA appears.
- Reload the page and log out/back in: watchlist persists (same browser).
- Deep-link directly to `?view=my-wallet`: the view loads and fetches
  correctly without first navigating through the menu.
- Rapidly double-click the add-ticker submit button: only one row is
  added, no duplicate (button disables synchronously on first click).

---

## Testing Strategy

### Unit Tests:

- `tests/test_bigquery.py`: `create_watchlist_table_if_not_exists` creates
  on `NotFound` (mirrors `test_create_portfolio_snapshots_table_creates_on_not_found`);
  `add_watchlist_ticker` is a no-op on duplicate; `remove_watchlist_ticker`
  is a no-op when nothing matches; `list_watchlist_tickers` returns only
  the calling client's rows; `list_announcements_for_watchlist` includes
  the bounded JOIN in its query string.
- `tests/test_api.py`: missing `X-Client-Id` → 400 on all three watchlist
  endpoints and `/announcements/my-wallet`; unknown ticker → 422 on `POST
  /watchlist/{ticker}`; known ticker → 200; `DELETE` on a non-watchlisted
  ticker → 204, not an error. Follow the existing `patch("src.api.<fn>",
  return_value=...)` + `TestClient` pattern (`tests/test_api.py:1-28`).

### Integration Tests:

- `scripts/test_bq.py`: extend with a watchlist round-trip — add a ticker,
  list it back, remove it, confirm the list is empty again — against real
  BigQuery, per the mandatory-round-trip rule in `lessons.md` for any new
  hand-written SQL.

### Manual Testing Steps:

See Phase 3 Manual Verification above — covers the full add/remove/persist/
deep-link golden path plus the unknown-ticker and empty-watchlist edge
cases.

## Performance Considerations

The watchlist-filtered announcements JOIN bounds the watchlist subquery to
200 tickers per client (Phase 1, item 3) — a defensive guardrail, not a
user-facing limit, since freemium tiers are out of scope and today's real
usage is a single client.

## Migration Notes

`watchlist` is a brand-new table with no existing data to migrate or
backfill. No changes to the `announcements` or `x_posts` schemas.

## Open Risks & Assumptions

- The 200-ticker defensive bound is arbitrary and untested under load —
  acceptable given today's single real user; revisit if/when registration
  and multiple concurrent users ship.
- `X-Client-Id` is unsigned and client-generated, so it is trivially
  spoofable or resettable (clearing `localStorage` "forgets" a watchlist
  and starts a new one). This is accepted by the frame brief as inherent to
  a throwaway, registration-free identity — not a regression to fix here.

## References

- Frame brief: `context/changes/my-wallet-watchlist/frame.md`
- `db/bigquery.py:189-223` (portfolio_snapshots template), `:552-579`
  (`_build_filter_clauses`), `:742-792` (`list_announcements_user`),
  `:943-956` (`list_distinct_tickers`)
- `src/api.py:45-60` (`_get_role`), `:128` (`create_app`)
- `static/index.html:298-304` (profile menu markup), `:496-504`
  (`initGdpr`), `:768-784` (menu-item injection pattern), `:905-916`
  (`_navigateToView`), `:959-975` (`_applyUrlState`)
- `tests/test_api.py:1-28` (test client + mocking pattern)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: BigQuery Watchlist Foundation

#### Automated

- [x] 1.1 Unit tests pass: `uv run pytest tests/test_bigquery.py` — 6ae5653
- [x] 1.2 Full suite passes: `uv run pytest` — 6ae5653
- [x] 1.3 Watchlist round-trip succeeds: `uv run python scripts/test_bq.py` — 6ae5653

#### Manual

- [x] 1.4 Inspect BigQuery console — `watchlist` table has expected schema — 6ae5653

### Phase 2: API Layer

#### Automated

- [x] 2.1 Unit tests pass: `uv run pytest tests/test_api.py` — 7bc2204
- [x] 2.2 Full suite passes: `uv run pytest` — 7bc2204
- [x] 2.3 Type/compile check passes: `uv run python -m py_compile src/api.py` — 7bc2204
- [x] 2.4 E2E test server boots without a live BigQuery call: `uv run pytest tests/e2e/` — 7bc2204

#### Manual

- [x] 2.5 curl/dev-tools check: missing header 400, unknown ticker 422, duplicate add no-op, remove-nonexistent no-op — 7bc2204

### Phase 3: Frontend — My Wallet View

#### Automated

- [x] 3.1 Full suite passes: `uv run pytest` — 3c0f1d3

#### Manual

- [x] 3.2 Fresh browser: `watchlist_client_id` appears in localStorage automatically — 3c0f1d3
- [x] 3.3 Profile menu shows "Obserwowane" for the user role (not admin-gated) — 3c0f1d3
- [x] 3.4 Add known ticker via autocomplete — appears in watchlist + announcements show — 3c0f1d3
- [x] 3.5 Add unknown ticker — rejected with visible error — 3c0f1d3
- [x] 3.6 Remove ticker — disappears instantly, no confirmation — 3c0f1d3
- [x] 3.7 Remove last ticker — empty-state CTA appears — 3c0f1d3
- [x] 3.8 Reload + logout/login — watchlist persists — 3c0f1d3
- [x] 3.9 Deep-link `?view=my-wallet` — loads and fetches correctly — 3c0f1d3
- [x] 3.10 Rapid double-click on add-ticker button does not create a duplicate row — 3c0f1d3
