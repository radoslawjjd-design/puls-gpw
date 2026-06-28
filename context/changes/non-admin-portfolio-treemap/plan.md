# Non-admin Portfolio Treemap (PUL-64) Implementation Plan

## Overview

Add a per-user multi-wallet portfolio treemap available to all roles. Users manage wallets
(Główny / IKZE / IKE / PPK / PPE / Inny), add positions scoped to a wallet, and see a
"Treemapa" tab inside "Mój portfel" that renders all wallets side-by-side using
`renderTreemap()` and price data from `company_daily_stats`. Admin users lose the old
XTB-snapshot treemap button; they get the same user-positions treemap as every other role.

## Current State Analysis

All four blockers from `frame.md` are resolved by PRs already merged to master:

| Blocker | Status |
|---|---|
| Per-user data model (`user_portfolio_positions`) | ✅ PUL-65 merged |
| Price feed (`company_daily_stats.kurs_zamkniecia`) | ✅ PUL-54 merged |
| Input/UI surface (positions table + CRUD) | ✅ PUL-65 merged |
| JOIN between both in `list_user_portfolio_positions()` | ✅ PUL-65 merged |

**What does NOT yet exist** (this plan's scope):
- `user_portfolios` table (portfolio wallet registry per user)
- `portfolio_id` column on `user_portfolio_positions`
- Portfolio management API endpoints
- `compute_user_portfolio_treemap_positions()` pure function (the XTB-adapted
  `compute_treemap_positions()` is NOT reusable — different input format)
- `GET /api/portfolio/treemap` endpoint
- Frontend: portfolio selector tabs, wallet management modal, treemap tab

**This branch predates PUL-65 merge. Merge master before starting Phase 1.**

### Key Discoveries

- `compute_treemap_positions()` (`src/portfolio_treemap.py:4-73`) takes XTB JSON strings
  as input — cannot be reused for user positions. New function needed.
- `renderTreemap(data, container)` (`static/index.html:1718`) is a pure renderer —
  reusable without modification. Same CSS classes and field names apply.
- The admin-only treemap is entirely inside `injectAdminOnlyChrome()` (lines 985–1090)
  and `showTreemapView()` (line 1221); both become dead code after Phase 3.
- `_applyUrlState()` at line 1302: `view === 'treemap' && role === 'admin'` guard;
  no `portfolio-positions` case yet — both need updating in Phase 3.
- `tests/e2e/conftest.py:202–238`: does NOT yet have portfolio positions mocks
  (PUL-65 will add them); user_portfolios mocks must be added in Phase 1.
- upsert MERGE key change `(user_id, ticker) → (portfolio_id, ticker)` is a breaking
  change to the PUL-65 API; Phase 2 frontend update must ship in the same PR.

## Desired End State

After this plan ships, any authenticated user can:
1. Open "Mój portfel" and see their portfolio wallets as tabs (Główny, IKZE, etc.)
2. Create wallets via "Dodaj portfel" modal; positions from before wallet creation
   are auto-assigned to Główny when Główny is first created
3. Add/remove positions scoped to the active wallet tab
4. Switch to "Treemapa" within "Mój portfel" and see all wallets side-by-side,
   with a notice listing tickers whose price is unavailable
5. Admins see the same UI — the old XTB-snapshot treemap button is gone

**Verification**: `GET /api/portfolio/treemap` with a valid API key returns
`{portfolios: [...], as_of: "YYYY-MM-DD"}` with positions computed from
`user_portfolio_positions` joined to `company_daily_stats`.

### Key Discoveries

- `list_user_portfolio_positions()` already LEFT JOINs with `company_daily_stats`
  and returns `current_price` (nullable), `daily_change_pct` (nullable) per row
- All treemap fields derivable from that output — no additional BQ calls needed
- `computeTreemapLayout()` already filters cells with `position_value_pln ≤ 0` —
  positions without a price naturally exclude themselves from layout

## What We're NOT Doing

- Reusing `compute_treemap_positions()` for non-admin data — it is XTB-snapshot-specific
- Keeping the XTB-snapshot treemap (`GET /admin/portfolio/treemap`) in the frontend UI —
  the endpoint stays in code for potential API use but no button calls it
- Per-portfolio treemap (one at a time) — treemap view shows all wallets side-by-side
- Lazy-loading treemap per portfolio — one endpoint call, all wallets in one response
- Multi-step "which portfolio?" modal for adding positions — context-sensitive (active tab)
- Flat "all positions" table view — table is scoped to the active portfolio tab

## Implementation Approach

Layer-by-layer, matching the project's established sequencing: BQ schema → BQ functions →
API endpoints → compute function → frontend → tests. Breaking change (upsert MERGE key)
ships together with the frontend that passes `portfolio_id`.

## Critical Implementation Details

**Breaking change — upsert MERGE key**: `upsert_user_portfolio_position()` MERGE key
changes from `(user_id, ticker)` to `(portfolio_id, ticker)` to allow the same ticker
in multiple wallets. Every caller in `src/api.py` must pass `portfolio_id`. **All three
phases ship in a single PR** — Phase 1 changes BQ function signatures, Phase 2 updates
the API callers, and Phase 3 updates the frontend that sends `portfolio_id`; none of
these can land independently without breaking CI.

**`as_of` field for treemap response**: Derive as the max non-null `price_as_of` value
across all positions of all portfolios. `price_as_of` is already returned by
`list_user_portfolio_positions()` (cast from `snapshot_date`). If all positions have
null price, `as_of` is null.

**Admin treemap cleanup ordering**: In Phase 3, `injectAdminOnlyChrome()` cleanup of
`#treemap-btn` and `#treemap-view` at lines 990–993 must also be removed — otherwise
the function tries to delete elements that no longer exist in the DOM and logs silent
errors.

---

## Phase 1: Data Model + BQ Functions + conftest

### Overview

Create the `user_portfolios` table, add `portfolio_id` to `user_portfolio_positions`,
add all BQ CRUD functions for wallet management, and update conftest with new mocks.
After master merge, verify PUL-65 portfolio position mocks are present before adding
user_portfolios mocks.

### Changes Required

#### 1. `user_portfolios` table schema + creation functions

**File**: `db/bigquery.py`

**Intent**: Define the `user_portfolios` table that acts as a wallet registry per user.
Add `create_user_portfolios_table_if_not_exists()` and
`ensure_user_portfolios_schema_current()` startup hooks, following the exact same
pattern as watchlist and companies tables.

**Contract**: Table name constant `_USER_PORTFOLIOS_TABLE_NAME = "user_portfolios"`.
Schema: `user_id` (STRING, REQUIRED), `portfolio_id` (STRING, REQUIRED),
`portfolio_type` (STRING, REQUIRED — values: "glowny"/"ikze"/"ike"/"ppk"/"ppe"/"inny"),
`portfolio_name` (STRING, NULLABLE — custom name for "inny" type only),
`display_order` (INTEGER, REQUIRED), `created_at` (TIMESTAMP, REQUIRED).
`ensure_user_portfolios_schema_current()` is a one-liner binding over
`ensure_schema_current(_USER_PORTFOLIOS_TABLE_NAME, _USER_PORTFOLIOS_SCHEMA)`.

#### 2. `list_user_portfolios(user_id: str) -> list[dict]`

**File**: `db/bigquery.py`

**Intent**: Return all wallets for a user in display order; used by the portfolio
selector tabs and the wallet management API endpoints.

**Contract**: `SELECT * FROM user_portfolios WHERE user_id=@user_id ORDER BY
display_order ASC, created_at ASC`; returns each row as a dict with all columns.

#### 3. `create_user_portfolio(user_id: str, portfolio_type: str, portfolio_name: str | None) -> str`

**File**: `db/bigquery.py`

**Intent**: Insert a new wallet row with a server-generated UUID portfolio_id and a
fixed display_order per type; return the new portfolio_id.

**Contract**: `portfolio_id = str(uuid.uuid4())`. Display order map (enforced here,
not at API layer): glowny→1, ikze→2, ike→3, inny→4 or 5 (assign 4 if no existing
inny, else 5), ppk→6, ppe→7. INSERT INTO user_portfolios with all fields. Returns
`portfolio_id` string.

#### 4. `delete_user_portfolio(user_id: str, portfolio_id: str) -> None`

**File**: `db/bigquery.py`

**Intent**: Delete a portfolio wallet and cascade-delete all its positions in two
explicit queries (positions first to avoid orphaned rows).

**Contract**: Two sequential queries:
1. `DELETE FROM user_portfolio_positions WHERE portfolio_id = @portfolio_id`
2. `DELETE FROM user_portfolios WHERE user_id = @user_id AND portfolio_id = @portfolio_id`

#### 5. `assign_orphan_positions_to_portfolio(user_id: str, portfolio_id: str) -> None`

**File**: `db/bigquery.py`

**Intent**: Migrate pre-PUL-64 positions (NULL portfolio_id) to the given wallet;
called when user creates Główny to avoid leaving their existing positions invisible.

**Contract**: `UPDATE user_portfolio_positions SET portfolio_id = @portfolio_id WHERE
user_id = @user_id AND portfolio_id IS NULL`.

#### 6. Add `portfolio_id` to `_USER_PORTFOLIO_POSITIONS_SCHEMA`

**File**: `db/bigquery.py`

**Intent**: Make `ensure_user_portfolio_positions_schema_current()` add `portfolio_id`
NULLABLE STRING to the existing BQ table on next startup; existing rows get NULL
(PUL-65 positions, before any wallet is created).

**Contract**: Append `bigquery.SchemaField("portfolio_id", "STRING", mode="NULLABLE")`
to `_USER_PORTFOLIO_POSITIONS_SCHEMA`. `ensure_schema_current()` at `db/bigquery.py:144`
handles the table ALTER safely — NULLABLE column, no data loss.

#### 7. Update `upsert_user_portfolio_position()` — add `portfolio_id`, change MERGE key

**File**: `db/bigquery.py`

**Intent**: Allow the same ticker to appear in multiple wallets (e.g., PKO in both
Główny and IKZE) by keying the MERGE on `(portfolio_id, ticker)` instead of
`(user_id, ticker)`.

**Contract**: New signature: `upsert_user_portfolio_position(user_id: str,
portfolio_id: str, ticker: str, company_name: str, shares: float, avg_buy_price: float) -> None`.
MERGE `ON T.portfolio_id = S.portfolio_id AND T.ticker = S.ticker`.
WHEN NOT MATCHED: include `portfolio_id` in the INSERT column list.

#### 8. Update `delete_user_portfolio_position()` — add `portfolio_id`

**File**: `db/bigquery.py`

**Intent**: Scope the deletion to the correct wallet so the same ticker can be in
multiple portfolios without cross-deletion.

**Contract**: New signature: `delete_user_portfolio_position(user_id: str,
portfolio_id: str, ticker: str) -> None`. WHERE clause: `user_id=@user_id AND
portfolio_id=@portfolio_id AND ticker=@ticker`.

#### 9. Update `list_user_portfolio_positions()` — optional portfolio_id filter

**File**: `db/bigquery.py`

**Intent**: Allow scoping the query to a single wallet (table view) while still
supporting unfiltered fetch (treemap endpoint, which calls per-portfolio after listing
wallets).

**Contract**: New signature: `list_user_portfolio_positions(user_id: str,
portfolio_id: str | None = None) -> list[dict]`. When `portfolio_id` is provided,
add `AND p.portfolio_id = @portfolio_id` to the WHERE clause. The LEFT JOIN to
`company_daily_stats` and all returned columns remain unchanged.

#### 10. Update `tests/e2e/conftest.py` — verify PUL-65 mocks, add user_portfolios mocks

**File**: `tests/e2e/conftest.py`

**Intent**: After master merge, verify PUL-65 added portfolio position mocks to the
`live_server_url` fixture; add user_portfolios function mocks alongside them so
Phase 4 E2E tests can run against a live server without BQ access.

**Contract**: Confirm presence (or add if missing) PUL-65 mocks:
`list_user_portfolio_positions`, `upsert_user_portfolio_position`,
`delete_user_portfolio_position`, `create_user_portfolio_positions_table_if_not_exists`,
`ensure_user_portfolio_positions_schema_current`.
Add new patches: `create_user_portfolios_table_if_not_exists`,
`ensure_user_portfolios_schema_current`, `list_user_portfolios`,
`create_user_portfolio`, `delete_user_portfolio`,
`assign_orphan_positions_to_portfolio`. Each BQ write function → `MagicMock()`;
`list_user_portfolios` → `side_effect` fake returning `_FAKE_PORTFOLIOS` (a list with
one test Główny wallet whose positions are in `_FAKE_PORTFOLIO_POSITIONS`);
`create_user_portfolio` → side_effect returning a fixed test UUID.

#### 11. `compute_user_portfolio_treemap_positions()` + unit tests

**Files**: `src/portfolio_treemap.py`, `tests/test_portfolio_treemap.py`

**Intent**: Implement the pure compute function and establish its correctness contract
before Phase 2 wires it into the API endpoint. Phase 2 change 1 is a no-op — the
function is ready to import from Phase 1.

**Contract**: Min 6 tests covering: position with full price data (verify all 7 output
fields); position with `current_price=None` (position_value_pln=None, all change fields
None); empty input → empty output; multiple positions → `portfolio_share_pct` sums to
~100%; `avg_buy_price=0` → `since_purchase_pct=None` (guard division by zero);
no-price positions excluded from `portfolio_share_pct` denominator (only priced
positions contribute to total_value).

### Success Criteria

#### Automated Verification

- `uv run pytest tests/test_portfolio_treemap.py` — new compute function tests pass
- `uv run mypy db/bigquery.py src/portfolio_treemap.py` — no new type errors
- `uv run ruff check db/ src/portfolio_treemap.py tests/test_portfolio_treemap.py` — clean

#### Manual Verification

- After `ensure_user_portfolio_positions_schema_current()` runs: `portfolio_id` column
  appears in BigQuery table; existing rows show NULL in that column
- `create_user_portfolios_table_if_not_exists()` creates the table in dev BQ dataset
- `list_user_portfolios("test-uid")` returns `[]` for a new user

**After completing this phase and passing automated verification, confirm manual BQ
verification before proceeding to Phase 2.**

---

## Phase 2: Backend API + Treemap Computation

### Overview

Wire all new wallet management endpoints and the treemap endpoint into `src/api.py`,
update the existing positions endpoints to require `portfolio_id`, and write integration
tests for all new endpoints. `compute_user_portfolio_treemap_positions()` is already in
`src/portfolio_treemap.py` from Phase 1 — Phase 2 only imports it.

### Changes Required

#### 1. `compute_user_portfolio_treemap_positions()` — implemented in Phase 1

**No action required.** Function and its full contract are in Phase 1 change 11
(`src/portfolio_treemap.py`). Import it in Phase 2 change 2 via
`from src.portfolio_treemap import compute_user_portfolio_treemap_positions`.

#### 2. Update `src/api.py` — imports and startup

**File**: `src/api.py`

**Intent**: Import all new BQ functions and wire them into the startup hook so tables
are created and schemas are migrated on every API boot.

**Contract**: Add to the `from db.bigquery import (...)` block:
`list_user_portfolios`, `create_user_portfolio`, `delete_user_portfolio`,
`assign_orphan_positions_to_portfolio`, `create_user_portfolios_table_if_not_exists`,
`ensure_user_portfolios_schema_current`. Also import
`compute_user_portfolio_treemap_positions` from `src.portfolio_treemap`.
In the startup hook (where `create_watchlist_table_if_not_exists()` is called): add
calls to `create_user_portfolios_table_if_not_exists()` and
`ensure_user_portfolios_schema_current()`.

#### 3. `class PortfolioWalletCreate(BaseModel)`

**File**: `src/api.py`

**Intent**: Request body schema for POST /api/portfolio/wallets.

**Contract**: Fields: `portfolio_type: Literal["glowny", "ikze", "ike", "ppk", "ppe", "inny"]`,
`portfolio_name: str | None = None`.

#### 4. `GET /api/portfolio/wallets`

**File**: `src/api.py`

**Intent**: Return all wallets for the authenticated user in display order; used by the
frontend portfolio selector on every view load.

**Contract**: Auth: `Depends(_get_role)` + `Depends(_get_client_id)`.
Returns `list_user_portfolios(client_id)` directly (list of dicts). 200 OK.

#### 5. `POST /api/portfolio/wallets`

**File**: `src/api.py`

**Intent**: Create a new wallet; enforce type constraints; auto-assign orphan positions
when the first Główny wallet is created.

**Contract**: Auth: `Depends(_get_role)` + `Depends(_get_client_id)`. Body:
`PortfolioWalletCreate`. Fetch `list_user_portfolios(client_id)` to check constraints:
if `portfolio_type` in `{"glowny","ikze","ike","ppk","ppe"}` and a wallet of that type
already exists → raise `HTTPException(409, "Wallet type already exists")`. If
`portfolio_type == "inny"` and already 2 "inny" wallets → raise `HTTPException(409,
"Maximum 2 'Inny' wallets allowed")`. Call `create_user_portfolio(...)` → `portfolio_id`.
If `portfolio_type == "glowny"`: call `assign_orphan_positions_to_portfolio(client_id,
portfolio_id)`. Return `{"portfolio_id": portfolio_id, "portfolio_type": ..., ...}` 201.

#### 6. `DELETE /api/portfolio/wallets/{portfolio_id}`

**File**: `src/api.py`

**Intent**: Delete a wallet and all its positions; validates ownership before deleting.

**Contract**: Auth: `Depends(_get_role)` + `Depends(_get_client_id)`. Check that
`portfolio_id` appears in `list_user_portfolios(client_id)` → 404 if not.
Call `delete_user_portfolio(client_id, portfolio_id)`. Return 204.

#### 7. Update `GET /api/portfolio/positions` — require `portfolio_id`

**File**: `src/api.py`

**Intent**: Scope the positions list to a specific wallet (matches per-portfolio table
view decision); validates ownership before querying.

**Contract**: Add `portfolio_id: str = Query(...)` required query parameter. Validate
portfolio belongs to user (check in `list_user_portfolios(client_id)`) → 404 if not.
Pass `portfolio_id` to `list_user_portfolio_positions(client_id, portfolio_id)`.
Response shape unchanged.

#### 8. Update `POST /api/portfolio/positions` — add `portfolio_id`

**File**: `src/api.py`

**Intent**: Route the new position to the correct wallet; breaking change to PUL-65
API that ships together with the Phase 3 frontend update.

**Contract**: Add `portfolio_id: str` field to the existing position request body model
(alongside ticker, shares, avg_buy_price, etc.). Validate portfolio belongs to user.
Pass `portfolio_id` to `upsert_user_portfolio_position(client_id, portfolio_id, ...)`.

#### 9. Update `DELETE /api/portfolio/positions/{ticker}` — require `portfolio_id`

**File**: `src/api.py`

**Intent**: Scope position deletion to a specific wallet.

**Contract**: Add `portfolio_id: str = Query(...)` required query parameter. Validate
portfolio belongs to user. Call `delete_user_portfolio_position(client_id, portfolio_id,
ticker)`.

#### 10. `GET /api/portfolio/treemap`

**File**: `src/api.py`

**Intent**: Return all user wallets with computed treemap positions in one response;
used by `fetchPortfolioTreemap()` in the frontend treemap tab.

**Contract**: Auth: `Depends(_get_role)` + `Depends(_get_client_id)`. For each
portfolio in `list_user_portfolios(client_id)`: call `list_user_portfolio_positions(
client_id, portfolio.portfolio_id)`, then `compute_user_portfolio_treemap_positions(rows)`.
Collect all `price_as_of` values; `as_of = max(non-null price_as_of values) or None`.
Return:
```json
{
  "portfolios": [
    {
      "portfolio_id": "...",
      "portfolio_type": "glowny",
      "portfolio_name": null,
      "positions": [<TreemapPosition>.model_dump(), ...]
    }
  ],
  "as_of": "YYYY-MM-DD"
}
```
Reuse the existing `TreemapPosition` Pydantic model (already defined for the admin
endpoint) — output field names match exactly. **Before using it here, update
`TreemapPosition.position_value_pln` to `float | None` in `src/api.py` (currently
`float`, which rejects `None` for no-price positions and causes Pydantic `ValidationError`).
The admin endpoint is unaffected — it always produces a float value; Pydantic accepts
`float` for `float | None` fields.**
Empty positions list → include portfolio with `"positions": []`.
Zero portfolios → `{"portfolios": [], "as_of": null}`.

#### 11. Integration tests for all new endpoints

**File**: `tests/test_api.py`

**Intent**: Cover the API contracts for wallet management + treemap; follow the pattern
at lines 273–421 (mock BQ functions with `side_effect`, validate response shape).

**Contract**: Min 10 new tests:
- `GET /api/portfolio/wallets` → 200 list; → 401 without API key
- `POST /api/portfolio/wallets` Główny → 201 + auto-assigns orphans
- `POST /api/portfolio/wallets` duplicate type → 409
- `POST /api/portfolio/wallets` third "inny" → 409
- `DELETE /api/portfolio/wallets/{id}` own wallet → 204
- `DELETE /api/portfolio/wallets/{id}` wrong user → 404
- `GET /api/portfolio/positions?portfolio_id=...` → 200 scoped
- `GET /api/portfolio/positions` without portfolio_id → 422
- `GET /api/portfolio/treemap` → 200 with correct shape + no-price positions included
- `GET /api/portfolio/treemap` zero portfolios → `{"portfolios": [], "as_of": null}`

### Success Criteria

#### Automated Verification

- `uv run pytest tests/test_api.py tests/test_portfolio_treemap.py` — all pass
- `uv run mypy src/api.py src/portfolio_treemap.py` — no new errors
- `uv run ruff check src/` — clean

#### Manual Verification

- `GET /api/portfolio/treemap` with `X-API-Key` returns correct JSON structure
- `POST /api/portfolio/wallets` with `{"portfolio_type": "glowny"}` → 201
- Second `POST` with same type → 409
- `GET /admin/portfolio/treemap` still returns 200 (endpoint kept, not removed)

**After passing automated verification and manual spot-checks, confirm before Phase 3.**

---

## Phase 3: Frontend — Portfolio-Scoped UI, Wallet Management, Treemap Tab

### Overview

Remove the admin-only treemap from `injectAdminOnlyChrome()`, replace it with a
"Treemapa" tab inside `#portfolio-positions-view` for all roles, add portfolio selector
tabs, wallet management modal, and update all position CRUD calls to pass `portfolio_id`.
Ships together with Phase 2 (breaking change).

### Changes Required

#### 1. `injectAdminOnlyChrome()` — remove treemap creation block

**File**: `static/index.html`

**Intent**: Remove all treemap-related DOM injection from the admin-only function;
the treemap now lives in the common portfolio view.

**Contract**: Remove from `injectAdminOnlyChrome()`:
- The cleanup block at lines 990–993 (`$('treemap-btn').remove()`,
  `$('treemap-view').remove()`)
- The `treemapBtn` creation + `topbarNav.appendChild(treemapBtn)` block
- The `treemapView` creation with its full HTML + `view.insertAdjacentElement()` block
- `$('treemap-btn').addEventListener(...)` line
- The `['treemap-main', 'treemap-ikze'].forEach(...)` click handler block
- `$('tc-popup-close').addEventListener(...)`, `$('treemap-popup-backdrop').addEventListener(...)`,
  `$('tc-popup-goto').addEventListener(...)` lines
- The `_treemapEscBound` guard block
The function retains x-history button/view creation only.

#### 2. Remove dead treemap globals and functions

**File**: `static/index.html`

**Intent**: Remove variables and functions that referenced the now-removed `#treemap-view`
DOM; keep `renderTreemap()` and `computeTreemapLayout()` (still used in Phase 3's new
treemap tab).

**Contract**: Remove: `_TREEMAP_WALLET_CONTAINERS`, `_treemapData` variable,
`_treemapEscBound` variable, `_treemapResizeTimer`, `_renderTreemapWallets()`,
`_renderTreemapAsOf()`, `fetchTreemap()`, `startTreemapResizeTracking()`,
`stopTreemapResizeTracking()`, `_onTreemapResize()`, `showTreemapView()`.
Keep: `renderTreemap(data, container)`, `_openTreemapPopup()`, `_closeTreemapPopup()`.
The popup DOM (`#treemap-popup-backdrop` and children) moves inside
`#portfolio-positions-view` HTML in step 4.

**Also replace 4 call sites of the removed `stopTreemapResizeTracking()` with
`stopPortfolioTreemapResize()` (introduced in step 9) to avoid `ReferenceError` on
view switches and logout:**
- `doLogout()` line 654
- `showAnnouncementsView()` line 1114
- `_showXHistoryViewDom()` line 1132
- `_showMyWalletViewDom()` line 1210

Note: `startTreemapResizeTracking()` caller at line 1228 is inside `showTreemapView()`
which is also being removed — no separate update needed.

#### 3. Update `_navigateToView()` and `_applyUrlState()`

**File**: `static/index.html`

**Intent**: Remove the now-dead `treemap` view routing; add `portfolio-positions`
URL deep-link support for all roles.

**Contract**:
- In `_navigateToView()`: remove the `if (view === 'treemap')` branch (becomes dead
  code — no button triggers it); `portfolio-positions` case (added by PUL-65) should
  `history.pushState({view:'portfolio-positions'}, '', '?view=portfolio-positions')`.
- In `_applyUrlState()`: remove `if (view === 'treemap' && role === 'admin')` block;
  add (or confirm PUL-65 added) `else if (view === 'portfolio-positions')` →
  `showPortfolioPositionsView()` — no role gate.

#### 4. Portfolio selector tabs + wallet management area

**File**: `static/index.html`

**Intent**: Add a portfolio tabs strip and "Dodaj portfel" button at the top of
`#portfolio-positions-view`; portfolio switching reloads the table and updates
`_activePortfolioId`.

**Contract**: Inside `_buildPortfolioPositionsViewContent()` (the PUL-65 lazy builder),
prepend a `<div id="pp-portfolio-tabs-wrap">` containing:
- `<div id="pp-portfolio-tabs"></div>` (populated by `_renderPortfolioTabs(portfolios)`)
- `<button id="pp-add-portfolio-btn">+ Dodaj portfel</button>`
Module-level variable `let _activePortfolioId = null`.
`_renderPortfolioTabs(portfolios)`: clears `#pp-portfolio-tabs`, creates one
`<button class="pp-portfolio-tab" data-portfolio-id="...">` per wallet (label:
`portfolio_type === 'inny' ? portfolio_name : TYPE_LABELS[portfolio_type]`), marks the
first as active; if `portfolios` is empty, shows `<p class="pp-notice">Utwórz swój
pierwszy portfel ↑</p>`. Clicking a tab sets `_activePortfolioId`, removes active class
from others, calls `fetchPortfolioPositions(_activePortfolioId)`.

#### 5. `fetchUserPortfolios()` function

**File**: `static/index.html`

**Intent**: Fetch wallet list and render tabs; called on first `showPortfolioPositionsView()`
(lazy, guarded by `_portfoliosFetched` flag), and after any wallet create/delete.

**Contract**: `GET /api/portfolio/wallets` with both auth headers. On success: set
`_activePortfolioId = data[0]?.portfolio_id ?? null`; call `_renderPortfolioTabs(data)`;
if `_activePortfolioId` is not null, call `fetchPortfolioPositions(_activePortfolioId)`.
On error: show error in `#pp-portfolio-tabs`.

#### 6. "Dodaj portfel" modal

**File**: `static/index.html`

**Intent**: Inline modal (overlay pattern matching `#pp-edit-overlay`) for creating a
new wallet; portfolio_type dropdown collapses to just the permitted types; optional name
field shown only for "inny".

**Contract**: Modal HTML (static, inside `#portfolio-positions-view`):
`#pp-add-portfolio-overlay` / `#pp-add-portfolio-modal` with a `<select
id="pp-portfolio-type-select">` listing all 6 types, a `<div
id="pp-portfolio-name-wrap" style="display:none"><input id="pp-portfolio-name-input"
placeholder="Nazwa portfela"></div>`, Save + Cancel buttons. Show name-wrap when
"inny" is selected. Submit calls `POST /api/portfolio/wallets`; on 201: close modal,
call `fetchUserPortfolios()`; on 409: show type-specific error message in modal.
Disable "Inny" option in type select when user already has 2 (check against
`_renderPortfolioTabs`'s current data).

#### 7. "Widok" tab toggle — Tabela | Treemapa

**File**: `static/index.html`

**Intent**: Two-tab strip inside `#portfolio-positions-view` (below portfolio selector,
above content area) to switch between table and treemap sub-views.

**Contract**: Static HTML inside `#portfolio-positions-view`: `<div id="pp-view-tabs">
<button class="pp-view-tab active" data-mode="table">Tabela</button>
<button class="pp-view-tab" data-mode="treemap">Treemapa</button></div>`.
Clicking "Tabela": show `#pp-table-wrap`, hide `#pp-treemap-wrap`, call
`stopPortfolioTreemapResize()`. Clicking "Treemapa": hide `#pp-table-wrap`, show
`#pp-treemap-wrap`, call `fetchPortfolioTreemap()` (if not yet loaded or if
`_ppTreemapData` is null), call `startPortfolioTreemapResize()`.

#### 8. Treemap container + popup inside `#portfolio-positions-view`

**File**: `static/index.html`

**Intent**: Add the treemap sub-view HTML inside `#portfolio-positions-view`; move the
popup backdrop here (was inside the removed `#treemap-view`).

**Contract**: Inside `_buildPortfolioPositionsViewContent()`, add:
```html
<div id="pp-treemap-wrap" style="display:none">
  <div id="pp-treemap-no-price-notice" class="pp-notice" style="display:none"></div>
  <div class="treemap-wallets" id="pp-treemap-wallets"></div>
  <div class="treemap-legend">...</div>
  <div id="treemap-popup-backdrop" class="tc-popup-backdrop">
    <div class="tc-popup" ...> ... </div>
  </div>
</div>
```
Popup event listeners (`tc-popup-close`, `treemap-popup-backdrop`, `tc-popup-goto`,
Escape key) wired in `_buildPortfolioPositionsViewContent()` (lazy, once). Popup
`goto` button navigates to announcements view filtered by company name — same logic as
before.

#### 9. `fetchPortfolioTreemap()` + `_renderPortfolioTreemap(data)`

**File**: `static/index.html`

**Intent**: Fetch `GET /api/portfolio/treemap` and render each portfolio side-by-side;
display no-price notice; trigger resize tracking.

**Contract**: Module-level `let _ppTreemapData = null`.
`fetchPortfolioTreemap()`: `GET /api/portfolio/treemap` with both auth headers;
on success: `_ppTreemapData = data`; call `_renderPortfolioTreemap(data)`.
`_renderPortfolioTreemap(data)`: clear `#pp-treemap-wallets`; for each portfolio in
`data.portfolios`: create `<div class="treemap-wallet"><h3>{label}</h3><div
class="treemap-container" id="pp-treemap-{portfolio_id}"></div></div>` appended to
`#pp-treemap-wallets`; call `renderTreemap(priced_positions, container)` where
`priced_positions = positions.filter(p => p.position_value_pln !== null)`; collect
tickers where `position_value_pln === null` → update `#pp-treemap-no-price-notice`
text (`"Brak aktualnej ceny dla: PKO, CDR"`) and toggle display; render `as_of` in
view header. `startPortfolioTreemapResize()` / `stopPortfolioTreemapResize()` mirror
the old `startTreemapResizeTracking()` but reference `_ppTreemapData` and
`_renderPortfolioTreemap`.

#### 10. Position CRUD — pass `portfolio_id` from active tab context

**File**: `static/index.html`

**Intent**: All position operations (fetch, add, delete) must now include
`portfolio_id` from `_activePortfolioId` so the breaking Phase 2 API change is
handled correctly.

**Contract**:
- `fetchPortfolioPositions()` (PUL-65): append `?portfolio_id=${_activePortfolioId}`
  to the GET request URL. Guard: if `_activePortfolioId` is null, return early
  (show "Wybierz portfel" prompt in table body).
- `_upsertPortfolioPosition()` (PUL-65): include `portfolio_id: _activePortfolioId`
  in the POST request body.
- Delete handler (PUL-65): append `?portfolio_id=${_activePortfolioId}` to the
  DELETE request URL.

### Success Criteria

#### Automated Verification

- `uv run mypy static/` (if configured) — or browser console shows 0 JS errors on load
- `uv run ruff check src/` — clean

#### Manual Verification

- Admin role: no "Treemapa portfela" button in the topbar nav (old admin button gone)
- Any role: "Mój portfel" → portfolio-positions-view opens; "Utwórz portfel" prompt
  shown when no wallets exist
- "Dodaj portfel" modal opens; creating "Główny" triggers orphan-assign; wallet tab appears
- Selecting wallet tab filters positions table to that wallet
- "Dodaj pozycję" (using existing ticker autocomplete) → position appears in active wallet
- "Treemapa" toggle shows all wallets side-by-side; no-price notice shows if kurs=null
- Refreshing `?view=portfolio-positions` lands on correct view
- Admin role: clicking "Mój portfel" → same wallet view as user role

**Phase 3 and Phase 2 must ship in the same PR (breaking change in position endpoints).**

---

## Phase 4: E2E Tests

### Overview

Add user_portfolios fake data and function mocks to conftest; write E2E tests for
wallet management, per-portfolio table, and treemap rendering.

### Changes Required

#### 1. `tests/e2e/conftest.py` — fake portfolios + mocks

**File**: `tests/e2e/conftest.py`

**Intent**: Provide deterministic fake data for portfolio E2E tests; extend
`live_server_url` fixture with all user_portfolios function mocks.

**Contract**: Add module-level:
```python
_FAKE_PORTFOLIO_ID = "test-portfolio-glowny-001"
_FAKE_PORTFOLIOS = [{
    "portfolio_id": _FAKE_PORTFOLIO_ID, "portfolio_type": "glowny",
    "portfolio_name": None, "display_order": 1, "user_id": "test-client-id",
    "created_at": "2026-01-01T00:00:00+00:00"
}]
_FAKE_PORTFOLIO_POSITIONS = [
    {"ticker": "PKO", "company_name": "PKO BP", "shares": 100.0,
     "avg_buy_price": 45.0, "current_price": 50.0,
     "daily_change_pct": 1.5, "price_as_of": "2026-06-27"},
    {"ticker": "CDR", "company_name": "CD Projekt", "shares": 10.0,
     "avg_buy_price": 130.0, "current_price": None,
     "daily_change_pct": None, "price_as_of": None},
]
```
In `live_server_url` fixture: add patches for `list_user_portfolios` (returns
`_FAKE_PORTFOLIOS`), `create_user_portfolio` (returns `_FAKE_PORTFOLIO_ID`),
`delete_user_portfolio`, `assign_orphan_positions_to_portfolio`,
`create_user_portfolios_table_if_not_exists`, `ensure_user_portfolios_schema_current`.
Update PUL-65's `list_user_portfolio_positions` mock to return
`_FAKE_PORTFOLIO_POSITIONS` when called with `portfolio_id=_FAKE_PORTFOLIO_ID`.

#### 2. E2E tests — wallet management (`tests/e2e/test_portfolio_wallets.py`)

**File**: `tests/e2e/test_portfolio_wallets.py` (new)

**Intent**: Cover the create-wallet flow, portfolio selector rendering, and
context-sensitive position add.

**Contract**: Min 5 tests:
1. `test_portfolio_tabs_show_after_login` — user role: "Mój portfel" shows "Główny" tab
2. `test_add_portfolio_modal_opens` — "Dodaj portfel" button opens modal with type select
3. `test_add_portfolio_creates_tab` (mock create returns fake portfolio) — tab appears
4. `test_positions_table_scoped_to_active_tab` — switching tab fetches positions for
   that portfolio (verify no cross-contamination)
5. `test_add_position_sends_portfolio_id` — adding position calls POST with portfolio_id
   in request body (intercept network call or verify position appears in correct tab)

#### 3. E2E tests — portfolio treemap (`tests/e2e/test_user_portfolio_treemap.py`)

**File**: `tests/e2e/test_user_portfolio_treemap.py` (new)

**Intent**: Cover treemap rendering, no-price notice, admin chrome cleanup, and
popup click. Adapt patterns from `tests/e2e/test_portfolio_treemap.py` (admin tests).

**Contract**: Min 6 tests:
1. `test_admin_nav_has_no_old_treemap_btn` — admin role: no `#treemap-btn` in DOM
2. `test_treemap_tab_visible_for_user_role` — "Treemapa" toggle button exists in
   portfolio-positions-view for user role
3. `test_treemap_renders_cells_for_priced_positions` — PKO cell appears; CDR excluded
   from layout cells
4. `test_no_price_notice_shows_unpriceable_tickers` — "Brak aktualnej ceny dla: CDR"
   visible in notice area
5. `test_treemap_cell_popup_opens_on_click` — clicking PKO cell opens popup with data
6. `test_portfolio_positions_url_deeplink` — `?view=portfolio-positions` loads view
   directly

### Success Criteria

#### Automated Verification

- `uv run pytest tests/e2e/` — all E2E tests pass (including pre-existing ones)
- `uv run pytest tests/e2e/test_user_portfolio_treemap.py tests/e2e/test_portfolio_wallets.py`
  — all new tests pass

#### Manual Verification

- Run full E2E suite against local server; zero failures
- E2E no-price notice test correctly identifies CDR as the unpriced ticker
- Admin-role E2E confirms `#treemap-btn` absence in nav

---

## Testing Strategy

### Unit Tests

- `compute_user_portfolio_treemap_positions()` — 6+ cases (see Phase 1 change 11)
- No unit tests for BQ functions themselves (follow existing convention: BQ functions
  are covered by integration tests via API mocking, not direct BQ unit tests)

### Integration Tests

- All wallet management endpoints (CRUD) + updated positions endpoints + treemap
  endpoint — min 10 tests (see Phase 2 change 11)
- Follow existing pattern: mock BQ functions at `src.api.*` import path with
  `side_effect` callables for reads, `MagicMock()` for writes

### E2E Tests

- Wallet management: 5 tests (create wallet, tab rendering, position scoping)
- Portfolio treemap: 6 tests (admin cleanup, render, no-price, popup, URL)
- All tests independent: own setup via conftest mocks, no shared state between tests

### Manual Testing Steps

1. Merge master → create Główny wallet → verify existing positions auto-assigned
2. Create IKZE wallet → add PKO position → switch tabs → verify PKO in IKZE only
3. Toggle "Treemapa" → verify both Główny and IKZE treemapa containers rendered
4. Add a position with no price in `company_daily_stats` → verify no-price notice
5. Log in as admin → verify no "Treemapa portfela" nav button in topbar
6. Log in as admin → go to "Mój portfel" → verify same wallet/treemap UI as user role
7. Refresh `?view=portfolio-positions` → verify correct view restored

## Migration Notes

`portfolio_id` NULLABLE is added to `user_portfolio_positions` via
`ensure_user_portfolio_positions_schema_current()` which runs on every API startup.
Existing rows get NULL. Those rows remain "invisible" in per-portfolio table views
until the user creates a Główny wallet, at which point `assign_orphan_positions_to_portfolio()`
moves them. No data is lost in any scenario.

## References

- Frame brief: `context/changes/non-admin-portfolio-treemap/frame.md`
- Research: `context/changes/non-admin-portfolio-treemap/research.md`
- Admin treemap plan (PUL-45): `context/archive/2026-06-20-admin-ui-portfolio-treemap/plan.md`
- `src/api.py:53-75` — auth dependencies
- `src/api.py:325-354` — admin treemap endpoint (reference; stays in code, removed from UI)
- `src/portfolio_treemap.py:4-73` — `compute_treemap_positions()` (NOT reused)
- `db/bigquery.py:491-530` — `list_user_portfolio_positions()` (base of new compute)
- `db/bigquery.py:1281-1325` — `company_daily_stats` schema (kurs_zamkniecia)
- `static/index.html:985-1090` — `injectAdminOnlyChrome()` (gutted in Phase 3)
- `static/index.html:1718-1763` — `renderTreemap()` (reused unchanged)
- `tests/e2e/conftest.py:202-238` — live_server_url fixture (extended in Phase 1 + 4)
- `tests/e2e/test_portfolio_treemap.py:1-166` — E2E patterns to adapt

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Data Model + BQ Functions + conftest

#### Automated

- [ ] 1.1 `uv run pytest tests/test_portfolio_treemap.py` passes (new compute function tests)
- [ ] 1.2 `uv run mypy db/bigquery.py src/portfolio_treemap.py` — no new errors
- [ ] 1.3 `uv run ruff check db/ src/portfolio_treemap.py tests/test_portfolio_treemap.py` — clean

#### Manual

- [ ] 1.4 `portfolio_id` column visible in BQ `user_portfolio_positions` table after startup
- [ ] 1.5 `user_portfolios` BQ table created successfully in dev dataset
- [ ] 1.6 `list_user_portfolios("test-uid")` returns `[]` for a new user

### Phase 2: Backend API + Treemap Computation

#### Automated

- [ ] 2.1 `uv run pytest tests/test_api.py tests/test_portfolio_treemap.py` — all pass
- [ ] 2.2 `uv run mypy src/api.py src/portfolio_treemap.py` — no new errors
- [ ] 2.3 `uv run ruff check src/` — clean

#### Manual

- [ ] 2.4 `GET /api/portfolio/treemap` returns correct JSON structure with `portfolios` + `as_of`
- [ ] 2.5 `POST /api/portfolio/wallets {"portfolio_type":"glowny"}` → 201
- [ ] 2.6 Second POST with same type → 409
- [ ] 2.7 `GET /admin/portfolio/treemap` still returns 200 (endpoint kept in code)

### Phase 3: Frontend

#### Automated

- [ ] 3.1 Browser console: 0 JS errors on page load (user role)
- [ ] 3.2 Browser console: 0 JS errors on page load (admin role)
- [ ] 3.3 `uv run ruff check src/` — clean

#### Manual

- [ ] 3.4 Admin role: no "Treemapa portfela" nav button in topbar
- [ ] 3.5 Any role: "Mój portfel" opens portfolio-positions-view with "Utwórz portfel" prompt
- [ ] 3.6 "Dodaj portfel" → creates Główny → existing positions auto-assigned → tab appears
- [ ] 3.7 Portfolio tab switch filters positions table to that wallet
- [ ] 3.8 "Treemapa" toggle renders all wallets side-by-side
- [ ] 3.9 No-price notice shows for positions without kurs_zamkniecia
- [ ] 3.10 `?view=portfolio-positions` deep-link restores view on refresh
- [ ] 3.11 Admin "Mój portfel" → same wallet UI as user role

### Phase 4: E2E Tests

#### Automated

- [ ] 4.1 `uv run pytest tests/e2e/test_portfolio_wallets.py` — all 5 pass
- [ ] 4.2 `uv run pytest tests/e2e/test_user_portfolio_treemap.py` — all 6 pass
- [ ] 4.3 `uv run pytest tests/e2e/` — full suite passes (no regressions)

#### Manual

- [ ] 4.4 Full E2E suite run against local server — zero failures
