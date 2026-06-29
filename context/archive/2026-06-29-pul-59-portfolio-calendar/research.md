---
date: 2026-06-29T00:00:00+02:00
researcher: Claude Sonnet 4.6
git_commit: 07afaf9bbd9b63ce8cc3dcf69483a2b27a554bc3
branch: radoslawjjd/pul-59-profitloss-calendar-monthly-view-of-daily-portfolio-pl
repository: puls-gpw
topic: "PUL-59: P&L Calendar monthly view in Mój portfel"
tags: [research, portfolio, calendar, portfolio_snapshots, bigquery, frontend]
status: complete
last_updated: 2026-06-29
last_updated_by: Claude Sonnet 4.6
---

# Research: PUL-59 — P&L Calendar monthly view in Mój portfel

**Date**: 2026-06-29  
**Git Commit**: 07afaf9bbd9b63ce8cc3dcf69483a2b27a554bc3  
**Branch**: radoslawjjd/pul-59-profitloss-calendar-monthly-view-of-daily-portfolio-pl  

## Research Question

How to implement a monthly P&L calendar view (green/red days) as a third tab in the "Mój portfel"
section, alongside Tabela and Treemapa? Need: backend BQ query, FastAPI endpoint, frontend tab
+ calendar grid with month navigation (prev/next), and color coding (green/red/neutral).

## Summary

Research confirms the full implementation path. `portfolio_snapshots` already stores `day_change_abs`
(pre-computed daily P&L in PLN) per wallet per day — no delta computation needed in the new code.
The frontend tab system uses a simple `data-mode` button pattern; adding a third "Kalendarz" tab
requires ~4 surgical changes to `index.html`. A new BQ range query and FastAPI endpoint follow
established patterns from `/api/portfolio/treemap`. One **architectural ambiguity** must be resolved
before planning: the calendar's data source (`portfolio_snapshots.wallet` = "main"/"ikze" admin
strings) diverges from the user-facing portfolio model (`user_portfolios.portfolio_id` UUIDs used
by Tabela and Treemapa tabs in the same section).

## Detailed Findings

### Area 1: Frontend View Tabs — HTML/CSS/JS

**HTML (static/index.html:2048–2051)** — view-mode tab buttons:
```html
<div id="pp-view-tabs">
  <button type="button" class="pp-view-tab active" data-mode="table">Tabela</button>
  <button type="button" class="pp-view-tab" data-mode="treemap">Treemapa</button>
</div>
```
Add third button: `<button type="button" class="pp-view-tab" data-mode="calendar">Kalendarz</button>`
— no new CSS needed, `.pp-view-tab` (lines 592–597) covers it.

**CSS (static/index.html:590–597)**:
```css
#pp-view-tabs { display: flex; gap: .25rem; margin-bottom: .75rem; }
.pp-view-tab {
  padding: .35rem .9rem; border: 1px solid var(--border); border-radius: var(--radius-pill);
  background: #fff; color: var(--text-muted); cursor: pointer; font-size: .875rem;
}
.pp-view-tab.active { background: var(--brand); color: #fff; border-color: var(--brand); }
.pp-view-tab:hover:not(.active) { background: var(--brand-tint); color: var(--brand); }
```

**JS toggle logic (static/index.html:2166–2182)**:
```javascript
view.querySelectorAll('.pp-view-tab').forEach(btn => {
  btn.addEventListener('click', () => {
    view.querySelectorAll('.pp-view-tab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const mode = btn.dataset.mode;
    $('pp-portfolio-tabs-wrap').style.display = mode === 'table' ? '' : 'none';
    $('pp-table-wrap').style.display = mode === 'table' ? '' : 'none';
    $('pp-treemap-wrap').style.display = mode === 'treemap' ? '' : 'none';
    if (mode === 'table') {
      stopPortfolioTreemapResize();
    } else {
      if (!_ppTreemapData) fetchPortfolioTreemap();
      startPortfolioTreemapResize();
    }
  });
});
```
Must extend to handle `mode === 'calendar'`: hide treemap + table wraps, show calendar wrap,
stop treemap resize, trigger `fetchPortfolioCalendar()` if not cached.

**Treemap container pattern (static/index.html:2121–2139)**:
```html
<div id="pp-treemap-wrap" style="display:none">
  <div id="pp-treemap-no-price-notice" class="pp-notice" style="display:none"></div>
  <div class="treemap-wallets" id="pp-treemap-wallets"></div>
  ...
</div>
```
New `pp-calendar-wrap` div goes immediately after `pp-treemap-wrap`, also `style="display:none"`.

**`pp-portfolio-tabs-wrap` (static/index.html:2045–2046, 2172)**:
Hidden in treemap mode because treemap shows all wallets at once. For calendar: decision needed
(see architectural ambiguity below). If calendar is per-wallet, show tabs-wrap; if combined, hide it.

### Area 2: BigQuery portfolio_snapshots

**Schema (db/bigquery.py:191–201)**:
| Field | Type | Notes |
|---|---|---|
| `snapshot_id` | STRING REQUIRED | UUID |
| `wallet` | STRING REQUIRED | "main", "ikze" — admin XTB wallet names |
| `snapshot_date` | DATE REQUIRED | Calendar date |
| `total_value` | FLOAT64 REQUIRED | Total portfolio value |
| `currency` | STRING NULLABLE | "PLN" |
| `day_change_abs` | FLOAT64 NULLABLE | **Pre-computed** day P&L in PLN |
| `day_change_pct` | FLOAT64 NULLABLE | **Pre-computed** day P&L % |
| `positions_json` | STRING NULLABLE | JSON with position breakdown |
| `created_at` | TIMESTAMP REQUIRED | Insertion time |

**Critical finding**: `day_change_abs` and `day_change_pct` are **stored pre-computed** — passed
by the caller before INSERT (line 257). No delta computation needed in the new query.

**Existing read functions (db/bigquery.py)**:
- `get_latest_snapshot_before(wallet, before_date)` — line 273, returns one row before a date
- `get_latest_snapshot_for_wallet(wallet)` — line 313, returns most recent row

**Gap**: No date-range query exists. Must add `get_portfolio_snapshots_for_month(wallet, year, month)`.

**Date parameter pattern (lines 288–292)**:
```python
job_config = bigquery.QueryJobConfig(
    query_parameters=[
        bigquery.ScalarQueryParameter("wallet", "STRING", wallet),
        bigquery.ScalarQueryParameter("start_date", "DATE", date(year, month, 1)),
        bigquery.ScalarQueryParameter("end_date", "DATE", date(year, month, days_in_month)),
    ]
)
```

**`_table_ref()` helper (db/bigquery.py:105–106)**:
```python
def _table_ref(client, table=_TABLE_NAME):
    return f"{client.project}.{_DATASET}.{table}"
```

**Warning from lessons.md**: Backtick any BQ reserved keyword used as column name.
`snapshot_date`, `wallet`, `total_value` are safe. Check full BQ reserved list for any new column refs.

### Area 3: API Endpoint Pattern

**Auth helpers (src/api.py)**:
- `_get_role(key)` — line 70: validates `X-API-Key` → returns `"admin"` or `"user"`, raises 401
- `_get_client_id(client_id)` — line 84: extracts `X-Client-Id`, raises 400 if missing
- `_require_admin(role)` — line 78: raises 403 if not admin

**`/api/portfolio/treemap` endpoint (src/api.py:552–589)** — template for calendar:
```python
@app.get("/api/portfolio/treemap")
async def get_portfolio_treemap(
    role: Role = Depends(_get_role),
    client_id: str = Depends(_get_client_id),
):
    try:
        wallets = list_user_portfolios(client_id)   # BQ call
    except BigQueryError as exc:
        logger.error("BQ error...: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    if not wallets:
        return {"portfolios": [], "as_of": None}
    ...
```
Response: `{"portfolios": [{portfolio_id, portfolio_type, portfolio_name, positions: [...]}], "as_of": str}`

**All `/api/portfolio/*` endpoints**:
- `GET /api/portfolio/positions` — list positions in wallet
- `POST /api/portfolio/positions` — upsert position
- `DELETE /api/portfolio/positions/{ticker}` — delete position
- `GET /api/portfolio/wallets` — list all wallets for user
- `POST /api/portfolio/wallets` — create wallet
- `DELETE /api/portfolio/wallets/{portfolio_id}` — delete wallet
- `GET /api/portfolio/treemap` — all positions across wallets + compute fields

**Error pattern**: try/except `BigQueryError` → `HTTPException(status_code=500, detail=str(exc))`
**Pydantic models**: `ConfigDict(extra="ignore")`, all optional fields use `float | None = None`
**App factory**: `create_app()` function (src/api.py:183); all endpoints registered inside via closures.

### Area 4: JS Fetch + Render Pattern

**`fetchPortfolioTreemap()` (static/index.html:2334–2347)** — template for calendar fetch:
```javascript
async function fetchPortfolioTreemap() {
  try {
    const r = await fetch('/api/portfolio/treemap', {
      headers: { 'X-API-Key': apiKey, 'X-Client-Id': clientId }
    });
    if (r.status === 401) { doLogout(); return; }
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    _ppTreemapData = data;
    _renderPortfolioTreemap(data);
  } catch (e) {
    $('pp-treemap-wallets').innerHTML = `<div class="empty">Błąd ładowania danych treemapy</div>`;
  }
}
```
Both `X-API-Key` and `X-Client-Id` headers required for all `/api/portfolio/*` calls.

**`$()` helper (line 753)**: `const $ = id => document.getElementById(id);` — NOT querySelector.

**Caching pattern**: `let _ppTreemapData = null;` (line 1763). Lazy fetch on first mode switch,
re-use cached data on resize. Reset to `null` on data-invalidating events (wallet delete, line 2029).
New: `let _ppCalendarData = null;` with same lazy-load + reset pattern.

**Month navigation approach** (based on announcements prev/next, lines 1036–1044):
```javascript
// CRITICAL (lessons.md): disable btn SYNCHRONOUSLY before async call, not inside the async fn
$('pp-cal-prev').addEventListener('click', () => {
  $('pp-cal-prev').disabled = true;   // synchronous guard
  _ppCalMonth--;
  if (_ppCalMonth < 1) { _ppCalMonth = 12; _ppCalYear--; }
  _ppCalendarData = null;
  fetchPortfolioCalendar();
});
```

**Currency formatting (lines 1789–1799)**: No dedicated helper — use inline:
```javascript
n.toLocaleString('pl-PL', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' PLN'
```

**Error display**: `$('pp-calendar-wrap').innerHTML = '<div class="empty">Błąd ładowania</div>';`

**Resize handlers (`start/stopPortfolioTreemapResize`)**: Use debounced resize listener (150ms).
Calendar likely doesn't need resize handler (CSS grid auto-sizes); skip or add if needed.

### Area 5: Historical Context from Prior Portfolio Changes

**PUL-45** (`context/archive/2026-06-20-admin-ui-portfolio-treemap/`): Established admin treemap
endpoint from `portfolio_snapshots`. `day_change_abs` computed from two consecutive snapshots
(today vs yesterday via `get_latest_snapshot_before`). Two-layer test split: pure compute fn
unit tests + BQ mock tests + API tests.

**PUL-50** (`context/archive/2026-06-20-portfolio-treemap-multi-wallet/`): Extended admin treemap
to multi-wallet (main + ikze) side-by-side. Established per-wallet object response structure.

**PUL-64** (`context/archive/2026-06-25-non-admin-portfolio-treemap/`): User-facing portfolio
treemap using `user_portfolio_positions` (different from admin `portfolio_snapshots`). Established
`portfolio_id` as the wallet key in user context. MERGE key changed from `(user_id, ticker)` to
`(portfolio_id, ticker)`.

**PUL-65** (`context/archive/2026-06-27-pul-65/`): Full CRUD for user portfolio positions.
Pricing from `company_daily_stats` via LEFT JOIN + `ROW_NUMBER() OVER PARTITION BY ticker`.

**Test patterns**:
- Pure unit tests: `tests/test_portfolio_treemap.py` — no BQ, no mocks, direct fn calls
- API tests: `tests/test_api.py` — `patch("src.api.function_name", return_value=...)`
- E2E: `tests/e2e/conftest.py` — module-level fake stores, all BQ functions mocked in `live_server_url`
- E2E locators: `getByRole/getByLabel/getByText` only; no CSS/XPath; no `waitForTimeout()`

## Code References

- `static/index.html:2048–2051` — view tab buttons (Tabela | Treemapa)
- `static/index.html:2121–2139` — pp-treemap-wrap container structure
- `static/index.html:2166–2182` — JS view tab toggle logic
- `static/index.html:2334–2347` — fetchPortfolioTreemap() function
- `static/index.html:590–597` — CSS for .pp-view-tab
- `static/index.html:753` — $() = getElementById helper
- `db/bigquery.py:189–201` — portfolio_snapshots table name + schema
- `db/bigquery.py:226–270` — save_portfolio_snapshot()
- `db/bigquery.py:273–310` — get_latest_snapshot_before()
- `db/bigquery.py:313–348` — get_latest_snapshot_for_wallet()
- `db/bigquery.py:578–597` — list_user_portfolios()
- `db/bigquery.py:105–106` — _table_ref() helper
- `src/api.py:70–87` — _get_role(), _require_admin(), _get_client_id() auth helpers
- `src/api.py:552–589` — /api/portfolio/treemap endpoint (model for calendar)
- `src/api.py:132–140` — TreemapPosition Pydantic model
- `src/portfolio_treemap.py` — compute_user_portfolio_treemap_positions() pattern
- `context/archive/2026-06-20-admin-ui-portfolio-treemap/plan.md` — PUL-45 plan (448 lines)
- `context/archive/2026-06-25-non-admin-portfolio-treemap/plan.md` — PUL-64 plan (1010 lines)
- `context/archive/2026-06-27-pul-65/plan.md` — PUL-65 plan (645 lines)

## Architecture Insights

### Data source ambiguity (must resolve before /10x-plan)

`portfolio_snapshots.wallet` stores admin-uploaded XTB wallet names (`"main"`, `"ikze"`).
`user_portfolios.portfolio_id` stores UUID strings for user-created portfolios.

**Two possible interpretations**:

**Option A — Admin snapshot calendar** (matches ticket's original intent):
- Data: `portfolio_snapshots` with `wallet IN ("main", "ikze")`
- Who sees it: admin only (or all users but only admin has XTB-uploaded data)
- Wallet selector: show "Główny / IKZE" as hardcoded tabs (like PUL-45/50 admin treemap)
- Pro: `day_change_abs` pre-computed, simple BQ query
- Con: non-admin users see empty calendar (no XTB uploads)

**Option B — User portfolio calendar** (fits PUL-64 user-facing pattern):
- Data: compute daily portfolio value from `user_portfolio_positions` + `company_daily_stats`
- Who sees it: any user with positions
- Problem: no historical daily snapshots for user positions — would need to compute value for
  each past trading day, which requires historical price data not currently stored
- Con: significant additional scope, not feasible without historical price storage

**Recommendation**: Option A for now (matches ticket's explicit `portfolio_snapshots` reference).
Calendar shown in "Mój portfel" section but reads admin XTB snapshot data. Non-admin users
see a "Brak danych" state. Revisit Option B when/if historical pricing is added.

### SQL query design for `get_portfolio_snapshots_for_month()`

```sql
SELECT snapshot_date, total_value, day_change_abs, day_change_pct, currency
FROM `{project}.{dataset}.portfolio_snapshots`
WHERE wallet = @wallet
  AND snapshot_date >= @start_date
  AND snapshot_date <= @end_date
ORDER BY snapshot_date ASC
```
- `day_change_abs` is the day P&L in PLN (pre-computed at upload time)
- Returns only days with uploaded snapshots; frontend fills gaps with neutral color
- One row per (wallet, day) — no ROW_NUMBER needed (wallet+snapshot_date unique)

### Month navigation lessons.md constraint

**SPA pagination lesson**: disable prev/next button SYNCHRONOUSLY in the click handler
(before calling the async fetch), not inside the async function after state snapshot.
This prevents double-click race conditions.

### Calendar grid CSS approach

Use CSS `display: grid; grid-template-columns: repeat(7, 1fr)` with day cells.
Color-code using CSS variables already defined:
- `var(--positive)` (#1d7a46) — gain days
- `var(--negative)` (#b3261e) — loss days  
- `var(--neutral-warm)` (#aca28e) — no-data days (no snapshot uploaded)
- Zero days: neutral white or mild grey

## Historical Context (from prior changes)

- `context/archive/2026-06-20-admin-ui-portfolio-treemap/` — PUL-45: admin treemap from portfolio_snapshots, two-layer test split established
- `context/archive/2026-06-20-portfolio-treemap-multi-wallet/` — PUL-50: multi-wallet side-by-side response shape
- `context/archive/2026-06-25-non-admin-portfolio-treemap/` — PUL-64: user-facing portfolio, portfolio_id as wallet key
- `context/archive/2026-06-27-pul-65/` — PUL-65: full CRUD + pricing JOIN pattern

## Decisions Made (2026-06-29)

**Data source → Option B**: User portfolio positions (`user_portfolio_positions`) + historical
prices from `company_daily_stats`. Calendar computes per-day portfolio value using current
positions × historical closing prices. Goes as far back as `company_daily_stats` has data.
Days with no price data for any position → neutral color. `portfolio_snapshots` NOT used.

**Wallet selector → User portfolio tabs**: Reuse existing `pp-portfolio-tabs` (UUID-based).
Calendar is scoped to `_activePortfolioId` (same wallet selected in Tabela view). Calendar
fetches data per `portfolio_id`.

## Open Questions (for /10x-plan to resolve)

1. **Daily P&L formula**: For day D, compute total portfolio value as
   `SUM(shares_i × kurs_zamkniecia_i_on_D)` using current positions. Delta vs day D-1.
   Caveat: uses current shares (position history not tracked) — approximation for past months
   if user changed positions. Plan must decide whether to document this limitation or store
   position history snapshots.

2. **Gaps in company_daily_stats**: ~31% of tickers may lack a price for a given date.
   Plan must decide: skip the day entirely (neutral), or use last-known price (carry-forward)?
   Recommend: neutral/no-data if ANY position lacks price for that day.

3. **BQ query approach for per-day portfolio value**: Single query joining
   `user_portfolio_positions` × `company_daily_stats` for the full month, or N daily queries?
   Recommend: single JOIN with GROUP BY snapshot_date for efficiency.

4. **Endpoint signature**: `GET /api/portfolio/calendar?year=2026&month=6&portfolio_id=UUID`
   — scoped per portfolio, returns array of day objects. Plan must define response shape.

5. **Compute function location**: Add `compute_calendar_pnl(positions, daily_prices_by_date)`
   to `src/portfolio_treemap.py` (extend existing module) or new `src/portfolio_calendar.py`?
