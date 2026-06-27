---
date: 2026-06-27T00:00:00+02:00
researcher: Radek
git_commit: 4575c182b89559a1b09a5c0a5dffe13cd78c0049
branch: radoslawjjd/pul-65-user-portfolio-positions-crud-ticker-shares-avg-buy-price
repository: puls-gpw
topic: "PUL-65 — User portfolio positions CRUD (ticker, shares, avg buy price) + treemap pricing from company_daily_stats"
tags: [research, codebase, portfolio, bigquery, auth, fastapi, ui]
status: complete
last_updated: 2026-06-27
last_updated_by: Radek
---

# Research: PUL-65 — User portfolio positions CRUD

**Date**: 2026-06-27  
**Researcher**: Radek  
**Git Commit**: 4575c182b89559a1b09a5c0a5dffe13cd78c0049  
**Branch**: radoslawjjd/pul-65-user-portfolio-positions-crud-ticker-shares-avg-buy-price  
**Repository**: puls-gpw

## Research Question

How to implement per-user portfolio position CRUD (ticker, shares, avg buy price) backed by BigQuery, with current pricing pulled from `company_daily_stats`, following existing patterns from PUL-28 (watchlist) and PUL-54 (company stats ingestion)?

## Summary

All three building blocks are already in the codebase — auth/identity (PUL-28 watchlist pattern), BQ table creation/upsert (companies MERGE pattern), and company_daily_stats pricing (PUL-54). The new feature is a clean composition of these three. The critical decision: **user identity = `client_id` from `X-Client-Id` header** (browser-persisted UUID, no users table). The BQ schema and API surface are straightforward; the pricing JOIN via `ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC)` is the one non-trivial query.

---

## Detailed Findings

### Auth & User Identity

**Files**: `src/api.py:54-76`, `api_main.py:12-14`

The project uses two static env-var API keys (`ADMIN_API_KEY`, `USER_API_KEY`) plus a separate **client-id** for per-user data scoping:

```python
# src/api.py:54-55
_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
_CLIENT_ID_HEADER = APIKeyHeader(name="X-Client-Id", auto_error=False)

# src/api.py:59-76
def _get_role(key: str | None = Security(_API_KEY_HEADER)) -> Role:
    if key == os.environ.get("ADMIN_API_KEY"):
        return "admin"
    if key == os.environ.get("USER_API_KEY"):
        return "user"
    raise HTTPException(status_code=401, detail="Invalid or missing API key")

def _get_client_id(client_id: str | None = Security(_CLIENT_ID_HEADER)) -> str:
    if not client_id:
        raise HTTPException(status_code=400, detail="Missing X-Client-Id header")
    return client_id
```

**Client-id origin** (`static/index.html:693-699`): browser generates a UUID with `crypto.randomUUID()` on first load and persists it in `localStorage.watchlist_client_id`. This UUID is the **sole user identity** — no registration, no email, no server-side user record.

**Implication for PUL-65**: `user_id` column in `user_portfolio_positions` = this `client_id` string. Both `_get_role` and `_get_client_id` dependencies are injected on every portfolio endpoint.

---

### BigQuery — Table & Schema Pattern

**File**: `db/bigquery.py` (1 493 lines)

Every table follows a strict three-step setup:

1. **Schema list** — `bigquery.SchemaField` entries (`db/bigquery.py:191-201` for portfolio_snapshots, `db/bigquery.py:353-357` for watchlist)
2. **`create_*_table_if_not_exists()`** — `client.get_table()` + `NotFound` → `client.create_table()` (`db/bigquery.py:118-128`)
3. **`ensure_*_schema_current()`** — additive migration: computes missing fields, calls `client.update_table(table, ["schema"])` (`db/bigquery.py:144-176`)

Both functions are always paired and called in the `@app.on_event("startup")` hook (`src/api.py:149-154`):

```python
@app.on_event("startup")
async def _init_dimension_tables():
    create_watchlist_table_if_not_exists()
    ensure_watchlist_schema_current()
    create_companies_table_if_not_exists()
    ensure_companies_schema_current()
```

**Critical rule** (lessons.md:232): new columns added after initial creation must be `NULLABLE` — `ensure_schema_current` uses `update_table` which rejects adding `REQUIRED` columns to an existing table.

---

### BigQuery — MERGE Upsert Pattern

**File**: `db/bigquery.py:497-535` (`upsert_company`) — canonical single-row keyed upsert:

```python
MERGE `{target}` T
USING (SELECT @user_id AS user_id, @ticker AS ticker, ...) S
ON T.user_id = S.user_id AND T.ticker = S.ticker
WHEN MATCHED THEN
  UPDATE SET shares = S.shares, avg_buy_price = S.avg_buy_price, updated_at = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN
  INSERT (user_id, ticker, company_name, shares, avg_buy_price, created_at, updated_at)
  VALUES (S.user_id, S.ticker, S.company_name, S.shares, S.avg_buy_price, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP())
```

For PUL-65: composite merge key is `(user_id, ticker)`. No batch/temp-table pattern needed (single-row upsert, not bulk).

---

### BigQuery — Watchlist Pattern (User-Scoped Reads)

**File**: `db/bigquery.py:353-460`

The watchlist is the direct template for `user_portfolio_positions`:

| Aspect | Watchlist | Portfolio positions |
|--------|-----------|---------------------|
| Key | `(client_id, ticker)` | `(user_id, ticker)` |
| List query | `WHERE client_id = @client_id ORDER BY added_at DESC` | `WHERE user_id = @user_id` |
| Add | INSERT with EXISTS guard | MERGE upsert |
| Delete | `DELETE WHERE client_id = @client_id AND ticker = @ticker` | same pattern |

`list_watchlist_tickers` (`db/bigquery.py:439-460`): scopes all reads with a `@client_id` parameter — identical pattern to use for portfolio positions.

---

### BigQuery — Pricing Join from company_daily_stats

**File**: `db/bigquery.py:1393-1492` (merge + stats functions)

`company_daily_stats` columns available for pricing:
- `ticker` STRING
- `snapshot_date` DATE
- `kurs_zamkniecia` FLOAT64 — close price (primary price source)
- `zmiana_procentowa` FLOAT64 — daily % change
- `zmiana_kwotowa` FLOAT64 — daily PLN change
- `kurs_otwarcia`, `kurs_min`, `kurs_max` FLOAT64

**Memory note** (project memory): ~31% of companies have no entry for a given day → use `LEFT JOIN` + `ROW_NUMBER()` for "latest per ticker" to avoid dropping positions with no recent price.

Recommended query for `list_user_portfolio_positions(user_id)`:

```sql
WITH latest_stats AS (
  SELECT
    ticker,
    kurs_zamkniecia,
    zmiana_procentowa,
    snapshot_date,
    ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC) AS rn
  FROM `{company_daily_stats_table}`
)
SELECT
  p.ticker,
  p.company_name,
  p.shares,
  p.avg_buy_price,
  p.created_at,
  p.updated_at,
  ls.kurs_zamkniecia AS current_price,
  ls.zmiana_procentowa AS daily_change_pct,
  ls.snapshot_date AS price_as_of
FROM `{positions_table}` p
LEFT JOIN latest_stats ls
  ON p.ticker = ls.ticker AND ls.rn = 1
WHERE p.user_id = @user_id
ORDER BY p.ticker
```

P&L computed in Python (not SQL) to avoid floating-point edge cases:
- `current_value = shares * current_price`
- `pnl_pln = (current_price - avg_buy_price) * shares`
- `pnl_pct = (current_price - avg_buy_price) / avg_buy_price * 100` (guard div-by-zero)

---

### FastAPI — Routing Structure

**File**: `src/api.py` (single file, no `routers/` subdirectory)

All endpoints live inside `create_app()` with `@app.get/post/delete()` decorators. The three new endpoints for PUL-65 slot in naturally after the watchlist block (after line 276):

```python
@app.get("/api/portfolio/positions")
async def get_portfolio_positions(
    role: Role = Depends(_get_role),
    client_id: str = Depends(_get_client_id),
): ...

@app.post("/api/portfolio/positions", status_code=201)
async def post_portfolio_position(
    body: PortfolioPositionIn,
    role: Role = Depends(_get_role),
    client_id: str = Depends(_get_client_id),
): ...

@app.delete("/api/portfolio/positions/{ticker}", status_code=204)
async def delete_portfolio_position(
    ticker: str,
    role: Role = Depends(_get_role),
    client_id: str = Depends(_get_client_id),
): ...
```

**Pydantic model pattern** (`src/api.py:88-142`, e.g. `TreemapPosition`):
```python
class PortfolioPositionIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ticker: str
    company_name: str
    shares: float
    avg_buy_price: float
```

**Ticker validation**: check against `list_distinct_tickers()` (same as watchlist POST at line 253) → 422 if unknown ticker.

---

### Companies Autocomplete

**File**: `src/api.py:212-236`

Two endpoints already exist, both cached for 5 min in `_AC_CACHE`:
- `GET /autocomplete/tickers` → `list[str]` — all known GPW ticker symbols
- `GET /autocomplete/companies` → `list[str]` — all known company names

Frontend loads both at login (`static/index.html:737-751`) into `_acTickers` and `_acCompanies` arrays. Autocomplete inputs are wired via `_setupAcInput(inputId, dropdownId, dataArray)`.

For the "Dodaj pozycję" form: reuse `_acTickers` for ticker input and `_acCompanies` for company_name input — no new endpoints needed.

---

### Frontend — UI Structure & Patterns

**File**: `static/index.html`

**Navigation item pattern** (`static/index.html:475-477`):
```html
<button type="button" class="nav-item" id="nav-portfel-pozycje-btn" data-view="portfel-pozycje">
  <svg class="nav-icon">...</svg> Mój portfel
</button>
```
Nav items are registered at `static/index.html:794+`. For non-admin users the treemap nav button is injected only when `role === "admin"` (`static/index.html:1019-1025`); the new "Mój portfel" button should be visible to all logged-in users (no role gate).

**View container pattern** (`static/index.html:532-533`):
```html
<div id="portfel-pozycje-view" style="display:none"></div>
```

**Dynamic content builder pattern** (`static/index.html:1146-1196` — `_buildMyWalletViewContent`):
```javascript
function _buildPortfelPozycjeViewContent(view) {
  view.innerHTML = `
    <div class="view-header"><h2>Mój portfel</h2></div>
    <button type="button" id="pp-add-btn">Dodaj pozycję</button>
    <div id="pp-add-form" style="display:none">...</div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Ticker</th><th>Spółka</th><th>Ilość akcji</th>
          <th>Śr. cena zakupu</th><th>Aktualny kurs</th>
          <th>Zmiana dzienna</th><th>Zysk/strata</th><th></th>
        </tr></thead>
        <tbody id="pp-table-body"></tbody>
      </table>
    </div>
  `;
}
```

**Remove button pattern** (from `static/index.html:1500-1507` watchlist chips):
```html
<button class="wl-remove-btn" data-ticker="${esc(t)}"
        aria-label="Usuń ${esc(t)} z portfela">&times;</button>
```
Delete needs a confirmation prompt (per PUL-65 spec) — use `confirm()` dialog or an inline confirmation toggle.

**Treemap cells** (`static/index.html:388-402`): existing `.treemap-cell.positive/.negative/.neutral` CSS classes are available if the user treemap (PUL-64) is implemented later. This ticket is CRUD only.

---

### E2E Test Pattern

**Files**: `tests/e2e/conftest.py`, `tests/e2e/test_my_wallet.py`, `tests/e2e/test_portfolio_treemap.py`

**Conftest pattern** — new BQ-backed endpoints must be patched in `live_server_url` fixture:

```python
# Module-level fake store
_portfolio_positions_store: dict[str, list[dict]] = {}

def _fake_upsert_user_portfolio_position(user_id, ticker, company_name, shares, avg_buy_price):
    positions = _portfolio_positions_store.setdefault(user_id, [])
    for p in positions:
        if p["ticker"] == ticker:
            p.update({"shares": shares, "avg_buy_price": avg_buy_price,
                       "company_name": company_name})
            return
    positions.append({"ticker": ticker, "company_name": company_name,
                       "shares": shares, "avg_buy_price": avg_buy_price,
                       "current_price": 50.0, "daily_change_pct": 1.5,
                       "pnl_pln": 0.0, "pnl_pct": 0.0})

def _fake_delete_user_portfolio_position(user_id, ticker):
    positions = _portfolio_positions_store.get(user_id, [])
    _portfolio_positions_store[user_id] = [p for p in positions if p["ticker"] != ticker]

def _fake_list_user_portfolio_positions(user_id):
    return list(_portfolio_positions_store.get(user_id, []))
```

All three fake functions must be patched inside the `live_server_url` fixture (lesson from conftest-bq-mocking memory: patch ALL db.bigquery.* functions called by new endpoints, not just startup hooks).

---

## Code References

- `src/api.py:54-76` — auth dependencies (`_get_role`, `_get_client_id`)
- `src/api.py:149-154` — `@app.on_event("startup")` — register new table init here
- `src/api.py:212-236` — companies/tickers autocomplete endpoints (reuse as-is)
- `src/api.py:238-276` — watchlist endpoints — template for portfolio position endpoints
- `db/bigquery.py:118-176` — `create_table_if_not_exists` + `ensure_schema_current` pattern
- `db/bigquery.py:191-201` — portfolio_snapshots schema — TIMESTAMP + FLOAT64 field pattern
- `db/bigquery.py:353-460` — watchlist table + CRUD functions — direct template
- `db/bigquery.py:497-535` — `upsert_company` — single-row MERGE pattern to copy
- `db/bigquery.py:1393-1492` — `merge_company_daily_stats` + `get_latest_company_stats_fetched_at`
- `static/index.html:475-477` — nav item pattern
- `static/index.html:532-533` — view container pattern
- `static/index.html:693-699` — client-id init from localStorage
- `static/index.html:737-751` — autocomplete arrays loaded at login
- `static/index.html:1019-1025` — conditional admin-only nav injection (treemap)
- `static/index.html:1146-1196` — `_buildMyWalletViewContent` — view builder template
- `tests/e2e/conftest.py:137-223` — watchlist mock + patch pattern
- `tests/e2e/test_my_wallet.py:17-39` — E2E test template

---

## Architecture Insights

1. **No users table, ever.** User identity is the browser UUID in `X-Client-Id`. All per-user tables use `client_id`/`user_id` as a raw string partition key. For PUL-65, name it `user_id` for clarity but the value is exactly the `client_id` from the header.

2. **Single-file API.** No `routers/` abstraction. Three new endpoints go inline in `src/api.py` after the watchlist block. Keep the file flat.

3. **Startup registration is mandatory.** Any new table's `create_*` + `ensure_*` pair must be added to `@app.on_event("startup")` — the app will fail silently on first BQ call otherwise.

4. **LEFT JOIN for pricing, not INNER JOIN.** ~31% of tickers have no `company_daily_stats` entry for a given day. An INNER JOIN silently drops positions without price data. Always LEFT JOIN so all positions appear (price columns NULL when unavailable).

5. **P&L computed in Python.** BQ can compute it in SQL, but computed-in-Python is consistent with how the treemap computes deltas (`src/api.py:326-357` + `compute_treemap_positions`). Use the same approach: raw data from BQ, enrichment in Python before returning JSON.

6. **Ticker validation on POST.** Watchlist validates ticker against `list_distinct_tickers()` before inserting — do the same for portfolio positions to prevent orphaned rows for non-existent tickers.

7. **No reserved keywords in schema.** `user_id`, `ticker`, `company_name`, `shares`, `avg_buy_price`, `created_at`, `updated_at` — none collide with BQ reserved keywords. No backtick escaping needed in column names (only in identifiers like table refs).

8. **Caching.** The autocomplete data (`_AC_CACHE`) is already populated at login; no new cache needed for portfolio endpoints. The position list is per-user and not suitable for module-level caching.

---

## Historical Context

- `context/archive/2026-06-22-my-wallet-watchlist/` — PUL-28 implementation: established `client_id` identity model and watchlist BQ pattern; the direct template for this ticket.
- `context/archive/2026-06-26-daily-company-stats-snapshot-ingestion/` — PUL-54: `company_daily_stats` table schema and ingestion pipeline; price source for PUL-65.
- `context/foundation/lessons.md:211-235` — BQ reserved keyword gotcha (PUL-29) — no action needed for this schema but keep in mind.
- `context/foundation/lessons.md:1-21` — GCP client init: `load_dotenv()` before GCP imports, `with_quota_project` guard — not applicable here (no new entry point, no new GCP client).

## Related Research

No prior `research.md` artifacts for this change.

---

## Open Questions

1. **ticker → company_name auto-fill**: Should the POST body require `company_name`, or should the API look it up from the `companies` table given a ticker? The spec includes `company_name` in the POST body (user-provided via autocomplete) — that's simpler and avoids an extra BQ call. Confirm: accept from body, store as-is.

2. **Price fallback**: If `kurs_zamkniecia` is NULL (e.g. auction day, no close recorded), should we fall back to `reference_price`? PUL-65 spec says "close_price or reference_price fallback" — check if `reference_price` exists in `company_daily_stats` schema or if it's available elsewhere (not found in current `_COMPANY_DAILY_STATS_SCHEMA` during research; may need to verify at implementation time).

3. **Confirmation on delete**: Spec says "confirmation prompt" — use native `confirm()` dialog (consistent with existing remove patterns) or build inline state? Keep it simple: `confirm()` is sufficient for MVP.

4. **PUL-64 treemap dependency**: PUL-64 ("Treemapa portfela for non-admin users") is listed as Backlog and will consume `user_portfolio_positions` data. This ticket does not implement the user-facing treemap, but the data model must be compatible with PUL-64's expected shape. Ensure the GET response includes all fields PUL-64 will need (current_price, shares, avg_buy_price, pnl_pln, pnl_pct).
