# PUL-65 — User Portfolio Positions CRUD Implementation Plan

## Overview

Add per-user portfolio position management: a BigQuery table `user_portfolio_positions` (keyed by `user_id + ticker`), three REST endpoints, and a frontend view ("Mój portfel") available to all logged-in users. Each position is priced in real time from `company_daily_stats` via a LEFT JOIN with `ROW_NUMBER()` partitioning.

## Current State Analysis

- **Auth/identity**: `client_id` from `X-Client-Id` header (browser-generated UUID, stored in `localStorage.watchlist_client_id`) is the sole user identifier. No users table. `_get_client_id` dependency at `src/api.py:73-76`.
- **BQ patterns**: MERGE upsert on single composite key follows `upsert_company` at `db/bigquery.py:497-535`. User-scoped SELECT with `WHERE client_id = @client_id` follows `list_watchlist_tickers` at `db/bigquery.py:439-460`.
- **Pricing source**: `company_daily_stats` table (PUL-54, Done). Relevant columns: `ticker`, `snapshot_date`, `kurs_zamkniecia` (close price), `zmiana_procentowa` (daily % change). No `reference_price` column exists — NULL close price shows as "—" in UI; no fallback.
- **Autocomplete**: `/autocomplete/tickers` and `/autocomplete/companies` already exist (`src/api.py:212-236`); their data is loaded at login into `_acTickers` and `_acCompanies` JS arrays.
- **API structure**: single-file `src/api.py`, all routes registered inside `create_app()`. Startup hook at `src/api.py:149-154`.
- **UI patterns**: inline expandable form (watchlist, `static/index.html:1146-1196`). Two-mode form (add/edit) not yet used — new pattern but simple extension of the watchlist form.

### Key Discoveries

- `db/bigquery.py:497-535` — `upsert_company` is the exact MERGE template to copy for `upsert_user_portfolio_position`
- `db/bigquery.py:1393-1466` — `_COMPANY_DAILY_STATS_TABLE_NAME` constant is available for the pricing JOIN
- `src/api.py:149-154` — startup hook where new `create_*` + `ensure_*` calls must be registered
- `tests/e2e/conftest.py:204-223` — all BQ-backed functions imported into `src.api` must be patched (including startup hooks), not just request-handling functions (lesson from PUL-28 conftest)

## Desired End State

A logged-in user can open "Mój portfel" from the sidebar, see their current stock positions with live pricing (close price + daily % change from `company_daily_stats`), add new positions via an inline form, edit existing ones (pre-filled form, ticker read-only), and delete positions with a native confirm dialog. P&L (PLN and %) is computed per position. When no price data exists for a ticker, price/P&L/daily-change columns show "—".

**Verification**: Run the round-trip script (`scripts/test_bq_user_portfolio_positions.py`), run unit tests, run E2E tests, then open the app and manually exercise the full add → edit → delete flow.

## What We're NOT Doing

- Admin portfolio (XTB upload, `portfolio_snapshots`) — untouched
- User-facing portfolio treemap (PUL-64) — separate ticket; this ticket delivers the data layer PUL-64 will consume
- P&L calendar (PUL-59) — separate ticket
- Historical position tracking or cost-basis recalculation — current-state only
- `reference_price` fallback — no such column in `company_daily_stats`
- URL state persistence for portfolio-positions view (`?view=portfolio-positions` in `_applyUrlState`) — consistent with "my-wallet" which also doesn't participate in URL state routing

## Implementation Approach

Four phases: BQ layer → API layer → Frontend → E2E tests. Each phase is independently verifiable before the next starts. Backend (Phases 1-2) can be smoke-tested with the round-trip script before the UI exists.

## Critical Implementation Details

**Pricing JOIN uses LEFT JOIN, not INNER JOIN.** ~31% of GPW tickers lack a `company_daily_stats` entry for any given day. An INNER JOIN would silently drop positions without price data. Always LEFT JOIN so all positions appear; price/P&L columns will be `None`/`null` when no stats exist.

**P&L computed in Python (API layer), not in BQ.** The BQ function returns raw fields (`current_price`, `daily_change_pct`, `price_as_of`). The API endpoint derives `pnl_pln = (current_price - avg_buy_price) * shares` and `pnl_pct = (current_price - avg_buy_price) / avg_buy_price * 100`. Guard `avg_buy_price != 0` before dividing.

**E2E conftest: patch ALL five imported names.** The startup hook calls `create_user_portfolio_positions_table_if_not_exists` and `ensure_user_portfolio_positions_schema_current` — both must be patched alongside the three request-handling functions, or the app fails to start in the E2E server fixture.

---

## Phase 1: BQ Data Layer

### Overview

Add `user_portfolio_positions` table definition and five BQ functions to `db/bigquery.py`. Write round-trip test script.

### Changes Required

#### 1. Table constants and schema

**File**: `db/bigquery.py`

**Intent**: Define the table name constant and schema list for `user_portfolio_positions`, following the pattern of `_WATCHLIST_TABLE_NAME` / `_WATCHLIST_SCHEMA` (lines 351-357).

**Contract**: 
```
_USER_PORTFOLIO_POSITIONS_TABLE_NAME = "user_portfolio_positions"

_USER_PORTFOLIO_POSITIONS_SCHEMA = [
    SchemaField("user_id",        "STRING",    REQUIRED),
    SchemaField("ticker",         "STRING",    REQUIRED),
    SchemaField("company_name",   "STRING",    NULLABLE),
    SchemaField("shares",         "FLOAT64",   REQUIRED),
    SchemaField("avg_buy_price",  "FLOAT64",   REQUIRED),
    SchemaField("created_at",     "TIMESTAMP", REQUIRED),
    SchemaField("updated_at",     "TIMESTAMP", REQUIRED),
]
```
Place after the watchlist schema block.

#### 2. `create_user_portfolio_positions_table_if_not_exists()`

**File**: `db/bigquery.py`

**Intent**: Create the table on first run. Copy the `create_watchlist_table_if_not_exists` pattern (lines 373-379): `client.get_table()` → `NotFound` → `client.create_table()`.

**Contract**: Function signature `() -> None`. Uses `_USER_PORTFOLIO_POSITIONS_TABLE_NAME` and `_USER_PORTFOLIO_POSITIONS_SCHEMA`.

#### 3. `ensure_user_portfolio_positions_schema_current()`

**File**: `db/bigquery.py`

**Intent**: Additive migration wrapper. Copy `ensure_watchlist_schema_current` (lines 373-379 area) which delegates to `ensure_schema_current(table_name, schema)`.

**Contract**: `() -> None`. Calls `ensure_schema_current(_USER_PORTFOLIO_POSITIONS_TABLE_NAME, _USER_PORTFOLIO_POSITIONS_SCHEMA)`.

#### 4. `upsert_user_portfolio_position(user_id, ticker, company_name, shares, avg_buy_price)`

**File**: `db/bigquery.py`

**Intent**: Insert or update one row keyed on `(user_id, ticker)`. MATCHED → update `company_name`, `shares`, `avg_buy_price`, `updated_at`. NOT MATCHED → full INSERT.

**Contract**: Copy `upsert_company` (lines 497-535). Merge key: `ON T.user_id = S.user_id AND T.ticker = S.ticker`. Parameters: five `ScalarQueryParameter` entries (`user_id STRING`, `ticker STRING`, `company_name STRING`, `shares FLOAT64`, `avg_buy_price FLOAT64`). Raises `BigQueryError` on failure.

#### 5. `delete_user_portfolio_position(user_id, ticker)`

**File**: `db/bigquery.py`

**Intent**: Remove a single position for a given user. Silent no-op if the row doesn't exist (DELETE WHERE returns 0 rows without error).

**Contract**: `(user_id: str, ticker: str) -> None`. Query: `DELETE FROM table WHERE user_id = @user_id AND ticker = @ticker`. Raises `BigQueryError` on query failure. Follow `remove_watchlist_ticker` pattern (watchlist delete function).

#### 6. `list_user_portfolio_positions(user_id)`

**File**: `db/bigquery.py`

**Intent**: Return all positions for a user joined with the latest available close price and daily % change from `company_daily_stats`. One dict per position with raw pricing fields; P&L is NOT computed here.

**Contract**: `(user_id: str) -> list[dict]`. Returns list of dicts with keys: `ticker`, `company_name`, `shares`, `avg_buy_price`, `current_price` (float | None), `daily_change_pct` (float | None), `price_as_of` (str | None — ISO date). Uses the query below (include it verbatim — the ROW_NUMBER + LEFT JOIN is the non-obvious part):

```sql
WITH latest_stats AS (
  SELECT
    ticker,
    kurs_zamkniecia,
    zmiana_procentowa,
    CAST(snapshot_date AS STRING) AS price_as_of,
    ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC) AS rn
  FROM `{_table_ref(client, _COMPANY_DAILY_STATS_TABLE_NAME)}`
)
SELECT
  p.ticker,
  p.company_name,
  p.shares,
  p.avg_buy_price,
  ls.kurs_zamkniecia   AS current_price,
  ls.zmiana_procentowa AS daily_change_pct,
  ls.price_as_of
FROM `{_table_ref(client, _USER_PORTFOLIO_POSITIONS_TABLE_NAME)}` p
LEFT JOIN latest_stats ls
  ON p.ticker = ls.ticker AND ls.rn = 1
WHERE p.user_id = @user_id
ORDER BY p.ticker
```

Single `@user_id` STRING parameter. Each result row → `dict(row)` in the return list. Raises `BigQueryError` on query failure.

#### 7. Round-trip test script

**File**: `scripts/test_bq_user_portfolio_positions.py`

**Intent**: Verify the full BQ lifecycle on a real dataset: create/ensure, upsert (add), upsert again (update same key), list with pricing JOIN, delete, verify row gone.

**Contract**: Follow `scripts/test_bq_company_stats_merge.py` pattern. Steps:
1. Call `create_user_portfolio_positions_table_if_not_exists()` + `ensure_user_portfolio_positions_schema_current()`
2. Upsert sentinel row (`user_id="e2e-test-user"`, `ticker="PKO"`, `shares=10.0`, `avg_buy_price=40.0`)
3. Upsert same key with `shares=15.0` → assert only one row, shares updated
4. Call `list_user_portfolio_positions("e2e-test-user")` → verify row present, `current_price` is float or None (both valid)
5. Call `delete_user_portfolio_position("e2e-test-user", "PKO")`
6. List again → assert empty
7. `finally`: cleanup any orphan test rows by `DELETE WHERE user_id = 'e2e-test-user'`

### Success Criteria

#### Automated Verification

- Round-trip script exits 0: `uv run python scripts/test_bq_user_portfolio_positions.py`
- Existing non-E2E tests still pass: `uv run pytest tests/ -k "not e2e"`

#### Manual Verification

- BQ console (or script output) confirms: table `user_portfolio_positions` created, upsert idempotent (no duplicates on re-run), pricing JOIN returns a `current_price` value for at least one PKO-range ticker

**Implementation Note**: After automated passes, confirm manually that the round-trip script printed step-by-step confirmation (no silent failures). Then proceed to Phase 2.

---

## Phase 2: FastAPI Endpoints + Unit Tests

### Overview

Add three REST endpoints to `src/api.py` (GET list, POST upsert, DELETE), Pydantic models, startup registration, and unit tests.

### Changes Required

#### 1. Pydantic models

**File**: `src/api.py`

**Intent**: Define request and response shapes for portfolio position endpoints, following existing model patterns (`TreemapPosition` at lines 121-129).

**Contract**: 
```
class PortfolioPositionIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ticker: str
    company_name: str
    shares: float     # must be > 0
    avg_buy_price: float  # must be > 0

class PortfolioPositionOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ticker: str
    company_name: str | None
    shares: float
    avg_buy_price: float
    current_price: float | None = None
    daily_change_pct: float | None = None
    pnl_pln: float | None = None
    pnl_pct: float | None = None
    price_as_of: str | None = None
```

Place after existing Pydantic models, before `create_app()`.

#### 2. Import new BQ functions

**File**: `src/api.py`

**Intent**: Make all five new BQ functions available inside `create_app()`.

**Contract**: Add to the existing `from db.bigquery import (...)` block (or as a new import line):
`create_user_portfolio_positions_table_if_not_exists`, `ensure_user_portfolio_positions_schema_current`, `upsert_user_portfolio_position`, `delete_user_portfolio_position`, `list_user_portfolio_positions`.

#### 3. Startup hook registration

**File**: `src/api.py:149-154`

**Intent**: Initialize the new table on app startup so it exists before any endpoint handles a request.

**Contract**: Inside `_init_dimension_tables()`, append after the existing watchlist + companies calls:
```python
create_user_portfolio_positions_table_if_not_exists()
ensure_user_portfolio_positions_schema_current()
```

#### 4. `GET /api/portfolio/positions`

**File**: `src/api.py`

**Intent**: Return all positions for the authenticated user, enriched with current pricing and P&L computed in Python.

**Contract**: Dependencies: `role = Depends(_get_role)`, `client_id = Depends(_get_client_id)`. Calls `list_user_portfolio_positions(client_id)`. For each row, compute:
- `pnl_pln = (current_price - avg_buy_price) * shares` if `current_price is not None` else `None`
- `pnl_pct = (current_price - avg_buy_price) / avg_buy_price * 100` if both non-None and `avg_buy_price != 0` else `None`
Returns `list[PortfolioPositionOut]`. Catches `BigQueryError` → HTTP 500.

#### 5. `POST /api/portfolio/positions`

**File**: `src/api.py`

**Intent**: Add a new position or silently overwrite an existing one (same ticker = update). Validates ticker exists before upserting.

**Contract**: Body: `PortfolioPositionIn`. Validate `body.shares > 0` and `body.avg_buy_price > 0` → HTTP 422 if invalid. Validate `body.ticker in list_distinct_tickers()` → HTTP 422 `"Unknown ticker"` if not found (same guard as watchlist POST at line 253). Calls `upsert_user_portfolio_position(client_id, body.ticker, body.company_name, body.shares, body.avg_buy_price)`. Returns HTTP 200 with `{"ticker": body.ticker, "upserted": True}`. Catches `BigQueryError` → HTTP 500.

#### 6. `DELETE /api/portfolio/positions/{ticker}`

**File**: `src/api.py`

**Intent**: Remove a position. Silent no-op if the position doesn't exist.

**Contract**: Path param `ticker: str`. Dependencies: `role`, `client_id`. Calls `delete_user_portfolio_position(client_id, ticker)`. Returns HTTP 204. Catches `BigQueryError` → HTTP 500.

#### 7. Unit tests

**File**: `tests/test_api.py`

**Intent**: Verify all three endpoints behave correctly under mocked BQ — authorization, happy path, error cases.

**Contract**: Follow the existing watchlist test block pattern (lines 428-524). Reuse the existing `_CLIENT_ID` and test client fixture. Add a test class or block covering:
- `GET /api/portfolio/positions` with empty list, non-empty list (mock returns one position with and without `current_price`)
- `POST /api/portfolio/positions` — valid ticker (mock `list_distinct_tickers` returns `["PKO"]`) → 200; unknown ticker → 422; invalid shares (≤0) → 422
- `DELETE /api/portfolio/positions/{ticker}` → 204
- `GET` and `POST` without `X-Client-Id` header → 400
- `GET` without `X-API-Key` → 401

### Success Criteria

#### Automated Verification

- New unit tests pass: `uv run pytest tests/test_api.py -k "portfolio"`
- Full non-E2E suite still green: `uv run pytest tests/ -k "not e2e"`

#### Manual Verification

- `curl` (or httpie) against local dev server: `GET /api/portfolio/positions` returns `[]` for fresh client; `POST` then `GET` returns the added position; `DELETE` removes it

**Implementation Note**: Verify endpoints manually with curl before moving to Phase 3 (the UI depends on correct JSON shapes).

---

## Phase 3: Frontend UI

### Overview

Add "Mój portfel" nav item (all users), a portfolio positions view with table + inline two-mode form, and all supporting JS fetch functions.

### Changes Required

#### 1. "Mój portfel" nav item

**File**: `static/index.html`

**Intent**: Add a sidebar nav button for the portfolio positions view, visible to all logged-in users (not admin-gated). It sits alongside "Obserwowane" in the nav.

**Contract**: Add a `<button type="button" class="nav-item" id="nav-portfolio-positions-btn" data-view="portfolio-positions">` button with a chart/wallet SVG icon and label "Mój portfel". Insert after the existing "Obserwowane" nav button (line ~477). No role check — rendered for both `"admin"` and `"user"` roles.

#### 2. View container

**File**: `static/index.html`

**Intent**: Static placeholder div for the portfolio positions view, hidden by default.

**Contract**: `<div id="portfolio-positions-view" style="display:none"></div>`. Place after the `my-wallet-view` div (line ~533).

#### 3. `_buildPortfolioPositionsViewContent(view)` function

**File**: `static/index.html`

**Intent**: Dynamically build the full view HTML: heading, toggle button, inline form, table skeleton. Called once when the user first navigates to this view.

**Contract**: Function sets `view.innerHTML` with:
- `<div class="view-header"><h2>Mój portfel</h2></div>`
- `<button type="button" id="pp-add-toggle-btn">Dodaj pozycję</button>`
- `<div id="pp-form-wrap" style="display:none">` containing the form (see item 4)
- `<div class="table-wrap"><table>` with `<thead id="pp-thead">` and `<tbody id="pp-tbody">`

After setting innerHTML, wire event handlers (toggle button, form submit, autocomplete). Called from the view navigation handler if `view.dataset.built !== "1"`, then set `view.dataset.built = "1"` to avoid rebuilding on every visit.

#### 4. Inline form — add / edit modes

**File**: `static/index.html`

**Intent**: Single form that operates in two modes. **Add mode** (default): ticker editable with autocomplete, company editable with autocomplete, shares + avg_buy_price inputs, "Dodaj" button. **Edit mode** (triggered by "Edytuj"): ticker shown as `<span>` (not editable), company shown as `<span>`, shares + avg_buy_price pre-filled and editable, buttons "Zapisz zmiany" + "Anuluj".

**Contract**: Form HTML inside `pp-form-wrap`:
```
<form id="pp-form">
  <div id="pp-ticker-field">
    <!-- add mode: ac-wrap + input#pp-ticker-input + autocomplete dropdown -->
    <!-- edit mode: <span id="pp-ticker-label"> shown instead -->
  </div>
  <div id="pp-company-field">
    <!-- add mode: ac-wrap + input#pp-company-input + autocomplete dropdown -->
    <!-- edit mode: <span id="pp-company-label"> shown instead -->
  </div>
  <input type="number" id="pp-shares-input" step="any" min="0.001" placeholder="Ilość akcji" required>
  <input type="number" id="pp-price-input" step="0.01" min="0.01" placeholder="Śr. cena zakupu (PLN)" required>
  <button type="submit" id="pp-submit-btn">Dodaj</button>
  <button type="button" id="pp-cancel-btn" style="display:none">Anuluj</button>
</form>
```
Module-level variable `let _ppEditingTicker = null`. When `null` → add mode; when set → edit mode (ticker/company fields become spans, submit label "Zapisz zmiany", cancel visible). "Anuluj" click resets `_ppEditingTicker = null` and restores add mode.

Wire autocomplete for ticker via `_setupAcInput('pp-ticker-input', 'ac-pp-ticker', _acTickers)` and for company via `_setupAcInput('pp-company-input', 'ac-pp-company', _acCompanies)` — add mode only.

#### 5. Table columns and row rendering

**File**: `static/index.html`

**Intent**: Table header and per-row rendering with "—" fallback for null price fields, and "Edytuj" + "Usuń" buttons per row.

**Contract**: `<thead>` columns: Ticker | Spółka | Ilość akcji | Śr. cena zakupu | Aktualny kurs | Zmiana dzienna | Zysk/strata | (empty — Akcje).

Row rendering function `_renderPortfolioRow(pos)` (or inline in the refresh function). For each column:
- `Aktualny kurs`: `pos.current_price != null ? pos.current_price.toFixed(2) + ' PLN' : '—'`
- `Zmiana dzienna`: `pos.daily_change_pct != null ? pos.daily_change_pct.toFixed(2) + '%' : '—'`
- `Zysk/strata`: `pos.pnl_pln != null ? pos.pnl_pln.toFixed(2) + ' PLN (' + pos.pnl_pct.toFixed(2) + '%)' : '—'`

Color class: positive P&L → `class="positive"`, negative → `class="negative"`, null → no class.

"Edytuj" button: `data-ticker="${ticker}"`, click handler sets `_ppEditingTicker = ticker`, fills form inputs from `pos.*`, shows form, switches to edit mode. "Usuń" button: `data-ticker="${ticker}"`, click handler calls `_deletePortfolioPosition(ticker)`.

#### 6. Fetch functions

**File**: `static/index.html`

**Intent**: Three JS functions to call the three API endpoints, all including `X-Client-Id: clientId` and `X-API-Key: apiKey` headers.

**Contract**:
- `async function fetchPortfolioPositions()` — `GET /api/portfolio/positions`, on 200 calls `_renderPortfolioTable(positions)`, on error logs + shows error message in a `<div id="pp-error">`.
- `async function _upsertPortfolioPosition(ticker, companyName, shares, avgBuyPrice)` — `POST /api/portfolio/positions` with JSON body, on 200 calls `fetchPortfolioPositions()` + resets form to add mode.
- `async function _deletePortfolioPosition(ticker)` — calls `confirm("Czy na pewno usunąć pozycję " + ticker + " z portfela?")`, on confirm sends `DELETE /api/portfolio/positions/${encodeURIComponent(ticker)}`, on 204 calls `fetchPortfolioPositions()`.

Form submit handler calls `_upsertPortfolioPosition(...)` with values from inputs (both add and edit modes — the ticker differs: from input or from `_ppEditingTicker`).

#### 7. Navigation binding

**File**: `static/index.html`

**Intent**: Wire the "Mój portfel" nav button and ensure `fetchPortfolioPositions()` is called when the view activates.

**Contract**: Add to the nav button registration block (near line 794):
```js
$('nav-portfolio-positions-btn').addEventListener('click', () => _navigateToView('portfolio-positions'));
```
Add a new `showPortfolioPositionsView()` helper following the `showMyWalletView()` pattern: hide all other view containers (set `display:none`), show `portfolio-positions-view` (`display = ''`), build content if `view.dataset.built !== '1'` (call `_buildPortfolioPositionsViewContent(view)`, set `view.dataset.built = '1'`), then call `fetchPortfolioPositions()`.

Add to `_navigateToView` (line 1237 if-else chain) a new branch before the `else` fallback:
```js
} else if (view === 'portfolio-positions') {
  showPortfolioPositionsView();
}
```

### Success Criteria

#### Automated Verification

- No JS console errors on page load and on navigating to "Mój portfel"

#### Manual Verification

- "Mój portfel" nav item visible after login (both admin and user roles)
- "Dodaj pozycję" button expands the inline form; ticker and company autocomplete work
- Submitting a valid position adds a row to the table with correct columns
- "—" shown in Aktualny kurs / Zmiana / Zysk columns for a ticker with no `company_daily_stats` entry
- "Edytuj" button pre-fills the form; save updates the row; cancel returns to add mode
- "Usuń" button triggers confirm dialog; OK removes the row; Cancel leaves it
- No regression in "Obserwowane" (watchlist) view or admin treemap

**Implementation Note**: Test with an actual user-role API key (not admin) to confirm the view is accessible and the nav item is visible. Verify all three columns with null pricing show "—", not `null`, `undefined`, or `NaN`.

---

## Phase 4: E2E Tests

### Overview

Add portfolio positions mocks to the E2E conftest and a new test file covering the four key scenarios.

### Changes Required

#### 1. Fake store and mock functions

**File**: `tests/e2e/conftest.py`

**Intent**: Module-level fake in-memory store and five mock functions (2 startup, 3 request-handling) to patch all portfolio-position-related BQ calls.

**Contract**: Add at module level:
```python
_portfolio_positions_store: dict[str, list[dict]] = {}

def _fake_create_user_portfolio_positions_table_if_not_exists(): pass
def _fake_ensure_user_portfolio_positions_schema_current(): pass

def _fake_upsert_user_portfolio_position(user_id, ticker, company_name, shares, avg_buy_price):
    positions = _portfolio_positions_store.setdefault(user_id, [])
    for p in positions:
        if p["ticker"] == ticker:
            p.update({"company_name": company_name, "shares": shares,
                       "avg_buy_price": avg_buy_price})
            return
    positions.append({
        "ticker": ticker, "company_name": company_name,
        "shares": shares, "avg_buy_price": avg_buy_price,
        "current_price": 52.0, "daily_change_pct": 1.5,
        "pnl_pln": (52.0 - avg_buy_price) * shares,
        "pnl_pct": (52.0 - avg_buy_price) / avg_buy_price * 100 if avg_buy_price else None,
        "price_as_of": "2026-06-27",
    })

def _fake_delete_user_portfolio_position(user_id, ticker):
    store = _portfolio_positions_store.get(user_id, [])
    _portfolio_positions_store[user_id] = [p for p in store if p["ticker"] != ticker]

def _fake_list_user_portfolio_positions(user_id):
    return list(_portfolio_positions_store.get(user_id, []))
```

#### 2. Patch all five functions in `live_server_url`

**File**: `tests/e2e/conftest.py`

**Intent**: All five mock functions must be active in the `live_server_url` fixture so the startup hook and all request handlers use fakes. Reset the store between test runs.

**Contract**: Inside the `live_server_url` fixture's `with patch(...)` block, add five entries:
```python
patch("src.api.create_user_portfolio_positions_table_if_not_exists",
      side_effect=_fake_create_user_portfolio_positions_table_if_not_exists),
patch("src.api.ensure_user_portfolio_positions_schema_current",
      side_effect=_fake_ensure_user_portfolio_positions_schema_current),
patch("src.api.upsert_user_portfolio_position",
      side_effect=_fake_upsert_user_portfolio_position),
patch("src.api.delete_user_portfolio_position",
      side_effect=_fake_delete_user_portfolio_position),
patch("src.api.list_user_portfolio_positions",
      side_effect=_fake_list_user_portfolio_positions),
```
**Do NOT add `_portfolio_positions_store.clear()`** — `live_server_url` is `scope="session"` and runs once per session. Test isolation already works through per-test client_id: each test gets a fresh browser context → new `localStorage` → new `crypto.randomUUID()` → a unique key in the store. This is identical to the existing `_watchlist_store` pattern.

#### 3. Test file

**File**: `tests/e2e/test_portfolio_positions.py`

**Intent**: Four E2E scenarios covering the core user flows: add, edit, delete, and null-price display.

**Contract**: Four test functions, each starting from login + navigating to "Mój portfel":

**`test_user_can_add_position_and_see_it_in_table`**:
- Navigate to "Mój portfel"
- Click "Dodaj pozycję" → form appears
- Fill ticker "PKO", company "PKO BP SA", shares "10", avg price "40.00"
- Click "Dodaj"
- `expect(page.locator("#pp-tbody")).to_contain_text("PKO")`
- `expect(page.locator("#pp-tbody")).to_contain_text("10")`

**`test_user_can_edit_position_and_see_updated_values`**:
- Add PKO position first (via POST API call or UI)
- Click "Edytuj" on PKO row
- Verify form pre-filled with shares=10, price=40.00; ticker shown as read-only
- Change shares to "20", click "Zapisz zmiany"
- Verify row updated: shares column shows "20"

**`test_user_can_delete_position_with_confirmation`**:
- Add PKO position
- Page dialog: `page.on("dialog", lambda d: d.accept())`
- Click "Usuń" on PKO row
- `expect(page.locator("#pp-tbody")).not_to_contain_text("PKO")`

**`test_positions_show_dashes_when_no_price_data`**:
- Login and navigate to "Mój portfel" first (ensures `localStorage` client_id is set)
- Read client_id: `client_id = page.evaluate("() => localStorage.getItem('watchlist_client_id')")`
- Import and directly inject a null-price position into the fake store:
  ```python
  from tests.e2e.conftest import _portfolio_positions_store
  _portfolio_positions_store[client_id] = [{
      "ticker": "XYZ", "company_name": "Firma XYZ",
      "shares": 5.0, "avg_buy_price": 30.0,
      "current_price": None, "daily_change_pct": None,
      "price_as_of": None,
  }]
  ```
- Reload or re-navigate to "Mój portfel" so `fetchPortfolioPositions()` re-fetches
- `expect(page.locator("#pp-tbody")).to_contain_text("XYZ")`
- Verify price/P&L/daily columns show "—" (not `null`, `NaN`, or empty)

Note: `_fake_upsert_user_portfolio_position` always stores `current_price=52.0` so null-price data cannot be created via the UI flow — direct store injection is the correct approach for this scenario.

Use `getByRole` / `getByLabel` / `getByText` locators; no CSS selectors. No `page.waitForTimeout()` — use `expect(...).to_be_visible()` / `to_contain_text()` for waits.

### Success Criteria

#### Automated Verification

- New E2E tests pass: `uv run pytest tests/e2e/test_portfolio_positions.py -v`
- Full E2E suite still green: `uv run pytest tests/e2e/ -v`

#### Manual Verification

- All four E2E test scenarios confirmed passing in CI output

---

## Testing Strategy

### Unit Tests

- All three endpoints: GET (empty + populated), POST (valid + unknown ticker + invalid shares), DELETE
- Missing `X-Client-Id` → 400, missing `X-API-Key` → 401
- `BigQueryError` from BQ layer → HTTP 500

### Integration Tests (Round-Trip)

- `scripts/test_bq_user_portfolio_positions.py` — exercises real BQ: create → upsert (add) → upsert (update, no dup) → list (with pricing JOIN) → delete → verify empty

### E2E Tests

- Add position → visible in table
- Edit position → form pre-filled, save updates row
- Delete position → confirm → row removed
- Null price data → "—" shown in price/P&L/daily columns

### Manual Testing Steps

1. Login with user-role API key; confirm "Mój portfel" visible in nav
2. Add position PKO with 10 shares @ 40.00 PLN; confirm row appears with correct values
3. Confirm current price column shows actual close price (or "—" if unavailable)
4. Click "Edytuj" on PKO; confirm form pre-fills; change shares to 15; save; confirm row updates
5. Click "Usuń" on PKO; confirm dialog; OK; confirm row removed
6. Add position for a ticker known to have no daily stats; confirm "—" in price columns
7. Verify "Obserwowane" watchlist view still works correctly after changes

## References

- Research doc: `context/changes/pul-65/research.md`
- Auth pattern: `src/api.py:54-76` (`_get_role`, `_get_client_id`)
- MERGE upsert template: `db/bigquery.py:497-535` (`upsert_company`)
- User-scoped read template: `db/bigquery.py:439-460` (`list_watchlist_tickers`)
- Watchlist UI template: `static/index.html:1146-1196` (`_buildMyWalletViewContent`)
- E2E conftest pattern: `tests/e2e/conftest.py:137-223`
- Pricing table: `db/bigquery.py:1393-1492` (`_COMPANY_DAILY_STATS_TABLE_NAME`, merge function)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: BQ Data Layer

#### Automated

- [x] 1.1 Round-trip script exits 0: `uv run python scripts/test_bq_user_portfolio_positions.py`
- [x] 1.2 Existing non-E2E tests still pass: `uv run pytest tests/ -k "not e2e"`

#### Manual

- [x] 1.3 BQ console confirms: table created, upsert idempotent, pricing JOIN returns value for at least one ticker

### Phase 2: FastAPI Endpoints + Unit Tests

#### Automated

- [ ] 2.1 New unit tests pass: `uv run pytest tests/test_api.py -k "portfolio"`
- [ ] 2.2 Full non-E2E suite green: `uv run pytest tests/ -k "not e2e"`

#### Manual

- [ ] 2.3 curl/httpie smoke test: POST → GET → DELETE all return correct shapes on local dev server

### Phase 3: Frontend UI

#### Automated

- [ ] 3.1 No JS console errors on page load and on navigating to "Mój portfel"

#### Manual

- [ ] 3.2 "Mój portfel" nav item visible for both admin and user roles after login
- [ ] 3.3 Add position → row appears in table with correct values
- [ ] 3.4 "—" shown for null-price ticker in all three price-dependent columns
- [ ] 3.5 "Edytuj" pre-fills form, save updates row, cancel restores add mode
- [ ] 3.6 "Usuń" → confirm dialog → OK removes row
- [ ] 3.7 No regression in "Obserwowane" or admin treemap views

### Phase 4: E2E Tests

#### Automated

- [ ] 4.1 New E2E tests pass: `uv run pytest tests/e2e/test_portfolio_positions.py -v`
- [ ] 4.2 Full E2E suite still green: `uv run pytest tests/e2e/ -v`

#### Manual

- [ ] 4.3 All four E2E test scenarios confirmed passing in CI output
