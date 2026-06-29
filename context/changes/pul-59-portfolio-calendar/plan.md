# PUL-59 — P&L Calendar: Monthly Portfolio Performance View

## Overview

Add a monthly P&L calendar as a third tab in the "Mój portfel" section, alongside Tabela and
Treemapa. Each calendar cell represents one day of the month, colour-coded green (gain) /
red (loss) / neutral (no data or non-trading day), showing the daily P&L in PLN. The user
navigates between months with prev/next buttons. Data is computed from the user's current
portfolio positions (user_portfolio_positions) joined with historical closing prices
(company_daily_stats). Non-trading days (GPW holidays, weekends) are detected automatically
from the absence of rows in company_daily_stats.

Reference: https://www.gpw.pl/szczegoly-sesji (GPW session calendar — no-session days are
naturally absent from company_daily_stats, so no separate holiday list is needed)

## Current State Analysis

- `user_portfolio_positions` stores current positions (ticker, shares, avg_buy_price) per
  portfolio_id. No position history — plan uses current shares for all historical computations.
- `company_daily_stats` stores per-ticker daily closing prices (kurs_zamkniecia, snapshot_date),
  partitioned by DATE, clustered by ticker. ~31% of tickers may lack an entry for any given
  trading day.
- `list_user_portfolio_positions()` already uses ROW_NUMBER for the LATEST price — the calendar
  needs a different query: JOIN on SPECIFIC snapshot_date.
- No date-range query exists for portfolio data — this plan adds one.
- Frontend: view-mode tabs (Tabela | Treemapa) at index.html:2048–2051 use data-mode buttons.
  JS toggle at lines 2166–2182. A third button + container follows the exact same pattern.
- Auth: `_get_role` + `_get_client_id` — consistent across all /api/portfolio/* endpoints.
- Portfolio_id: UUID string from user_portfolios, used as the wallet key in user_portfolio_positions.

## Desired End State

After this plan:
- Clicking "Kalendarz" in the "Mój portfel" view shows a full monthly calendar grid.
- Each cell: day number + PLN delta (e.g. "+320 PLN" or "−150 PLN"), coloured green/red.
- Days with no price data (weekends, GPW holidays, scraper gaps) appear neutral/grey.
- Prev/next buttons navigate between months; current month is the default.
- The active wallet (portfolio tab) drives which portfolio_id is queried.

To verify: open Mój portfel → click Kalendarz → see a month grid → navigate months → switch wallet → grid updates.

### Key Discoveries

- `company_daily_stats.kurs_zamkniecia` (FLOAT64, NULLABLE) is the closing price field.
  Table is partitioned by snapshot_date (DAY), clustered by ticker — efficient date-range queries.
- `db/bigquery.py:105` — `_table_ref(client, table)` helper used in all BQ queries.
- `db/bigquery.py:1663` — `_COMPANY_DAILY_STATS_TABLE_NAME = "company_daily_stats"`.
- `db/bigquery.py:444` — `_USER_PORTFOLIO_POSITIONS_TABLE_NAME`.
- `src/api.py:552–589` — `/api/portfolio/treemap` is the canonical template for the new endpoint.
- `static/index.html:2166–2182` — view tab toggle JS (extend to handle `mode === 'calendar'`).
- `static/index.html:753` — `$()` is `getElementById`, not querySelector.
- Lessons: BQ reserved keywords must be backticked; synchronous button disable before async fetch.

## What We're NOT Doing

- No position history tracking — calendar uses current shares for all past months (approximation).
- No scraping/storing the GPW holiday calendar — inferred from company_daily_stats absence.
- No per-position breakdown in calendar cells — aggregate portfolio P&L only.
- No click-to-drill-down on a day cell in this ticket.
- No admin portfolio_snapshots data (XTB) — user_portfolio_positions only.
- No percentage shown in the cell (just PLN amount) — matches user decision.

## Implementation Approach

4-layer pipeline: BQ function → pure compute function → FastAPI endpoint → frontend.

**BQ query strategy**: Extended date range (month_start − 35 days through month_end) fetched
in a single query. CROSS JOIN trading_days × user positions, LEFT JOIN company_daily_stats for
prices on each day. Returns one row per trading day with best-effort portfolio value. Python
then extracts the lookback baseline (last entry before month_start) and builds the calendar grid.

**Holiday/no-session detection**: Any weekday (Mon–Fri) not present in company_daily_stats for
the queried range is a non-trading day (holiday or scraper miss). Displayed the same as weekend.

**Best-effort P&L**: On a given day, if some tickers lack prices, the portfolio value is computed
from available tickers only (partial sum). This is indicated in the response (prices_found vs
total_positions) but the cell still shows a colour-coded value.

**Delta computation**: Python compute function uses consecutive trading-day portfolio values to
compute daily P&L (no LAG in SQL). Lookback entry provides the baseline for the first day of
the queried month.

## Critical Implementation Details

**CROSS JOIN vs individual ticker queries**: The BQ query uses CROSS JOIN trading_days × positions
so that every trading day appears in the result even if some tickers have no price (LEFT JOIN
returns NULL for those). If portfolio has zero positions, CROSS JOIN produces no rows → API returns
empty calendar (all days neutral). Guard for this in the compute function.

**Extended range for lookback**: `@lookback_start` = month_start − 35 calendar days (not 30)
guarantees at least one trading entry before the month even across long holiday stretches
(Christmas + New Year can be 10+ consecutive non-trading days).

**Disable nav buttons synchronously**: Per lessons.md SPA pagination rule — the prev/next click
handlers must set `btn.disabled = true` BEFORE calling fetchPortfolioCalendar(), not inside the
async function after page state is captured.

---

## Phase 1: BigQuery — `get_portfolio_calendar_data()`

### Overview

Add a new BQ function to db/bigquery.py that retrieves daily portfolio values for a user's
portfolio over an extended date range (lookback + queried month). Returns one dict per trading
day with portfolio_value (best-effort sum) and metadata about price coverage.

### Changes Required

#### 1. New function in db/bigquery.py

**File**: `db/bigquery.py`

**Intent**: Add `get_portfolio_calendar_data(portfolio_id, user_id, year, month)` after the
existing `get_latest_snapshot_for_wallet()` function (around line 350). It queries the extended
date range (month_start − 35 days through month_end) by crossing all trading days in that range
against the user's current positions and left-joining closing prices.

**Contract**: Signature:
```python
def get_portfolio_calendar_data(
    portfolio_id: str,
    user_id: str,
    year: int,
    month: int,
) -> list[dict]:
```
Returns `list[dict]` where each dict has keys: `snapshot_date` (Python `date`), `portfolio_value`
(float — best-effort sum of shares × kurs_zamkniecia for tickers with price, 0.0 for tickers
without), `prices_found` (int — count of positions with price data on this day), `total_positions`
(int — total positions in the portfolio). Returns `[]` when portfolio has no positions.
Raises `BigQueryError` on query failure.

The SQL structure uses four CTEs:
- `trading_days`: DISTINCT snapshot_date from company_daily_stats in the extended range
- `positions`: user's tickers + shares from user_portfolio_positions WHERE user_id + portfolio_id
- `daily_prices`: CROSS JOIN trading_days × positions, LEFT JOIN company_daily_stats ON
  (ticker, snapshot_date)
- `daily_portfolio`: GROUP BY snapshot_date — SUM(CASE WHEN price IS NOT NULL THEN shares × price
  ELSE 0 END), COUNTIF(price IS NOT NULL), COUNT(*) from total_positions subquery

Query parameters: `portfolio_id` (STRING), `user_id` (STRING),
`lookback_start` (DATE — `date(year, month, 1) − timedelta(days=35)`),
`end_date` (DATE — last day of month using `calendar.monthrange`).

### Success Criteria

#### Automated Verification

- `uv run pytest tests/test_bigquery.py -k calendar` passes — unit test with mocked BQ client
  verifies function is called with correct date params and returns expected dict structure
- `uv run ruff check db/bigquery.py` passes (no linting errors)
- `uv run mypy db/bigquery.py --ignore-missing-imports` passes

#### Manual Verification

- Round-trip test via `uv run python -c "from db.bigquery import get_portfolio_calendar_data; print(get_portfolio_calendar_data(...))"` against real BQ returns rows for trading days in the queried month
- SQL does not contain un-backticked reserved keywords (verify by reading the query string)

---

## Phase 2: Compute Function — `src/portfolio_calendar.py`

### Overview

New pure-Python module that transforms raw BQ rows into a structured calendar response. No BQ
calls — takes the list of dicts from Phase 1 and produces a full monthly grid with state and P&L
for every calendar day.

### Changes Required

#### 1. New file src/portfolio_calendar.py

**File**: `src/portfolio_calendar.py`

**Intent**: Create `compute_calendar_pnl(rows, year, month)` that builds a complete calendar dict.
This is pure Python — no BQ, no HTTP, no I/O — making it fully unit-testable. The function
determines which days are weekends (Saturday=5, Sunday=6 by weekday()), which are non-trading
weekdays (weekday Mon–Fri but absent from rows), and which have P&L data.

**Contract**: Signature:
```python
def compute_calendar_pnl(
    rows: list[dict],   # from get_portfolio_calendar_data()
    year: int,
    month: int,
) -> dict:
```

Returned dict shape:
```python
{
    "year": int,
    "month": int,
    "days": [
        {
            "date": "YYYY-MM-DD",   # ISO date string
            "day": int,             # 1-31
            "weekday": int,         # 0=Mon … 6=Sun
            "state": str,           # "weekend" | "no_session" | "data" | "no_data" | "future"
            "portfolio_value": float | None,
            "pnl_abs": float | None,   # day P&L in PLN; None if no data or no baseline
            "prices_found": int,
            "total_positions": int,
        },
        # … one entry per calendar day of the month
    ]
}
```

State definitions:
- `"weekend"`: Saturday or Sunday
- `"no_session"`: Mon–Fri weekday with no row in company_daily_stats for that date
  (holiday or scraper gap) — displayed identically to weekend in the UI
- `"data"`: trading day with at least one position price; pnl_abs is set (may be partial)
- `"no_data"`: trading day in company_daily_stats (appears in rows with prices_found=0) — rare
- `"future"`: date is after today

Baseline for first day: from rows, find the last entry with snapshot_date < month_start
(the lookback entry in the extended range). If none exists, pnl_abs for the first trading day
of the month is None.

P&L delta: for each consecutive pair of trading-day entries, pnl_abs = portfolio_value[D] −
portfolio_value[D−1]. Days are ordered by snapshot_date from the BQ result.

### Success Criteria

#### Automated Verification

- `uv run pytest tests/test_portfolio_calendar.py` passes — comprehensive unit tests covering:
  - Normal month with full price data
  - Month with missing prices on some days (best-effort: prices_found < total_positions)
  - Month starting with no lookback baseline (pnl_abs = None for first trading day)
  - Month with all days neutral (empty portfolio → rows = [])
  - Month containing GPW holidays (weekday with no row → state = "no_session")
  - Future days (state = "future")
  - Edge: February (28/29 days), months starting on weekend
- `uv run ruff check src/portfolio_calendar.py` passes
- `uv run mypy src/portfolio_calendar.py --ignore-missing-imports` passes

#### Manual Verification

- `python -c "from src.portfolio_calendar import compute_calendar_pnl; print(compute_calendar_pnl([], 2026, 6))"` produces a 30-day grid of all-neutral entries without error

---

## Phase 3: FastAPI Endpoint — `GET /api/portfolio/calendar`

### Overview

Add a new user-facing endpoint to src/api.py that combines the BQ function (Phase 1) and
compute function (Phase 2) to serve the calendar JSON. Follows the established treemap endpoint
pattern exactly.

### Changes Required

#### 1. Import in src/api.py

**File**: `src/api.py`

**Intent**: Add `get_portfolio_calendar_data` to the db.bigquery import block and
`compute_calendar_pnl` to the src.portfolio_calendar import.

**Contract**: Two new imports alongside existing `list_user_portfolios`, `list_user_portfolio_positions`.

#### 2. Pydantic response models in src/api.py

**File**: `src/api.py`

**Intent**: Add `PortfolioCalendarDay` and `PortfolioCalendarResponse` Pydantic models (near the
existing `TreemapPosition` model at line 132).

**Contract**:
```python
class PortfolioCalendarDay(BaseModel):
    model_config = ConfigDict(extra="ignore")
    date: str
    day: int
    weekday: int
    state: str
    portfolio_value: float | None = None
    pnl_abs: float | None = None
    prices_found: int = 0
    total_positions: int = 0

class PortfolioCalendarResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    year: int
    month: int
    days: list[PortfolioCalendarDay]
```

#### 3. Endpoint handler in src/api.py

**File**: `src/api.py`

**Intent**: Add `GET /api/portfolio/calendar` endpoint inside `create_app()`, after the
`/api/portfolio/treemap` handler (around line 590). Validates year/month query params, checks
portfolio_id ownership, calls BQ + compute, returns validated response.

**Contract**:
```python
@app.get("/api/portfolio/calendar")
async def get_portfolio_calendar(
    year: int,
    month: int,
    portfolio_id: str,
    role: Role = Depends(_get_role),
    client_id: str = Depends(_get_client_id),
):
```
Validation: month must be 1–12, year within [current_year − 5, current_year + 1].
Portfolio ownership check: `portfolio_id` must be present in `list_user_portfolios(client_id)`;
raise 403 if not found (prevents querying other users' portfolios).
On BQ error: HTTPException(500, detail=str(exc)) — same pattern as treemap endpoint.
Returns `PortfolioCalendarResponse(...).model_dump()`.

### Success Criteria

#### Automated Verification

- `uv run pytest tests/test_api.py -k calendar` passes — API tests cover:
  - 200 OK with valid year/month/portfolio_id for authenticated user
  - 401 when X-API-Key missing/invalid
  - 400 when X-Client-Id missing
  - 403 when portfolio_id belongs to different user
  - 422 when month out of range (0, 13) or year out of range
  - 500 when BQ raises BigQueryError
- `uv run pytest tests/test_api.py` (full suite) passes — no regressions
- `uv run ruff check src/api.py` passes
- `uv run mypy src/api.py --ignore-missing-imports` passes

#### Manual Verification

- `curl -H "X-API-Key: $USER_API_KEY" -H "X-Client-Id: test-id" "http://localhost:8080/api/portfolio/calendar?year=2026&month=6&portfolio_id=UUID"` returns JSON with 30 day objects
- Endpoint appears in FastAPI docs at /docs

---

## Phase 4: Frontend — Kalendarz Tab in Mój portfel

### Overview

Extend `static/index.html` to add the Kalendarz view tab, its container, CSS for the calendar
grid, and the JS logic for fetching, rendering, and navigating between months.

### Changes Required

#### 1. HTML: third tab button (static/index.html)

**File**: `static/index.html`

**Intent**: Insert a third `pp-view-tab` button with `data-mode="calendar"` after the existing
Treemapa button at line 2050.

**Contract**: Add immediately after line 2050:
```html
<button type="button" class="pp-view-tab" data-mode="calendar">Kalendarz</button>
```

#### 2. HTML: calendar container (static/index.html)

**File**: `static/index.html`

**Intent**: Add `pp-calendar-wrap` div after the `pp-treemap-wrap` closing tag (around line 2138),
initially hidden. Contains: month navigation row (prev button, month+year label, next button)
and the calendar grid container.

**Contract** (structure, not verbatim):
```html
<div id="pp-calendar-wrap" style="display:none">
  <div id="pp-cal-nav">
    <button type="button" id="pp-cal-prev">&#8249;</button>
    <span id="pp-cal-label"></span>
    <button type="button" id="pp-cal-next">&#8250;</button>
  </div>
  <div id="pp-cal-grid"></div>
  <div id="pp-cal-legend">
    <span class="pp-cal-legend-item pp-cal-gain">zysk</span>
    <span class="pp-cal-legend-item pp-cal-loss">strata</span>
    <span class="pp-cal-legend-item pp-cal-neutral">brak danych / bez sesji</span>
  </div>
</div>
```

#### 3. CSS: calendar grid and cell styles (static/index.html)

**File**: `static/index.html`

**Intent**: Add CSS rules for the calendar grid (7-column, equal cells), day cells
(weekday header row, cell with day number + PLN amount, colour states), and navigation row.
Add near the existing treemap CSS rules.

**Contract**: Key rules (implement near the treemap section):
- `#pp-cal-nav` — flex row, centered, gap, margin-bottom
- `#pp-cal-grid` — `display: grid; grid-template-columns: repeat(7, 1fr); gap: 4px;`
- `.pp-cal-header` — weekday header cells (Pn, Wt, Śr, Cz, Pt, Sb, Nd), small text, muted
- `.pp-cal-cell` — base cell: `background: #fff; border-radius: 8px; padding: 6px 8px;
  min-height: 56px; border: 1px solid var(--border); font-size: .8rem;`
- `.pp-cal-cell .pp-cal-day` — day number, small, muted, top-right corner
- `.pp-cal-cell .pp-cal-pnl` — P&L amount, bold, centered
- `.pp-cal-gain` — `background: var(--positive); color: #fff; border-color: var(--positive);`
- `.pp-cal-loss` — `background: var(--negative); color: #fff; border-color: var(--negative);`
- `.pp-cal-neutral` — `background: var(--fill); color: var(--text-muted); border-color: var(--border);`
- `.pp-cal-empty` — `background: transparent; border: none;` (padding cells before day 1)
- `@media (max-width: 600px)` — reduce min-height, smaller font

#### 4. JS: state variables and fetchPortfolioCalendar() (static/index.html)

**File**: `static/index.html`

**Intent**: Add state variables for calendar (`_ppCalData`, `_ppCalYear`, `_ppCalMonth`) near
the existing `_ppTreemapData` declaration (line 1763). Add `fetchPortfolioCalendar()` async
function that calls `/api/portfolio/calendar` with current year/month/portfolio_id params.
Follows the exact same pattern as `fetchPortfolioTreemap()` (lines 2334–2347).

**Contract**: State: `let _ppCalData = null; let _ppCalYear = null; let _ppCalMonth = null;`
Guard at function start (mirrors fetchPortfolioPositions() at line 1702):
```javascript
if (_activePortfolioId === null) {
  $('pp-cal-grid').innerHTML = '<div class="empty">Wybierz portfel powyżej.</div>';
  return;
}
```
Fetch URL: `/api/portfolio/calendar?year=${_ppCalYear}&month=${_ppCalMonth}&portfolio_id=${encodeURIComponent(_activePortfolioId)}`
Headers: `{ 'X-API-Key': apiKey, 'X-Client-Id': clientId }`
On success: set `_ppCalData`, call `_renderPortfolioCalendar(data)`.
On 401: call `doLogout()`.
On error: show error in `$('pp-cal-grid')` with `class="empty"`.

#### 5. JS: _renderPortfolioCalendar() (static/index.html)

**File**: `static/index.html`

**Intent**: Render the calendar grid from the API response. Fills `#pp-cal-label` with
the month name + year. Prepends 7 weekday header cells (Pn–Nd). Adds padding empty cells
for days before the 1st of the month. Renders each day cell with appropriate CSS class and
P&L text.

**Contract**: Month label uses `new Date(year, month-1).toLocaleString('pl-PL', {month:'long', year:'numeric'})`.
Padding: `day.weekday` of the first day gives offset (0=Mon → 0 padding cells, 6=Sun → 6 padding cells).
Cell text: for state="data", show `sign + Math.round(Math.abs(pnl_abs)) + " PLN"` where sign is
"+" for gain or "−" for loss. For all neutral states (weekend/no_session/no_data/future/null pnl_abs):
show only the day number, class `pp-cal-neutral`. pnl_abs = 0: white background, show "0 PLN".

#### 6. JS: month navigation event handlers (static/index.html)

**File**: `static/index.html`

**Intent**: Wire prev/next button click handlers that update `_ppCalYear`/`_ppCalMonth`,
reset `_ppCalData` to null, and call `fetchPortfolioCalendar()`. Buttons are disabled
synchronously before the async call (lessons.md SPA pagination rule).

**Contract**:
```javascript
$('pp-cal-prev').addEventListener('click', () => {
  $('pp-cal-prev').disabled = true;       // synchronous — before async
  $('pp-cal-next').disabled = true;
  _ppCalData = null;
  _ppCalMonth--;
  if (_ppCalMonth < 1) { _ppCalMonth = 12; _ppCalYear--; }
  fetchPortfolioCalendar();
});
// Mirror for pp-cal-next
```
Re-enable both buttons after render completes (inside fetchPortfolioCalendar() finally block).

#### 7. JS: extend view-tab toggle logic (static/index.html)

**File**: `static/index.html`

**Intent**: Extend the existing toggle handler (lines 2166–2182) to handle `mode === 'calendar'`.
Show `pp-calendar-wrap` when in calendar mode; hide it for table and treemap. Also reset calendar
state and trigger fetch when entering calendar mode for the first time.

**Contract**: In the existing click handler, make two changes:

1. Update the `pp-portfolio-tabs-wrap` visibility line (currently `mode === 'table' ? '' : 'none'`)
   to also show wallet tabs in calendar mode:
   ```javascript
   // Before (line 2172):
   $('pp-portfolio-tabs-wrap').style.display = mode === 'table' ? '' : 'none';
   // After:
   $('pp-portfolio-tabs-wrap').style.display = (mode === 'table' || mode === 'calendar') ? '' : 'none';
   ```

2. Add calendar container show/hide and lazy-fetch:
   ```javascript
   $('pp-calendar-wrap').style.display = mode === 'calendar' ? '' : 'none';
   if (mode === 'calendar') {
     stopPortfolioTreemapResize();
     if (!_ppCalData) fetchPortfolioCalendar();
   }
   ```

#### 8. JS: reset calendar state on wallet change (static/index.html)

**File**: `static/index.html`

**Intent**: When the user switches portfolio tabs (clicks a different wallet), reset
`_ppCalData = null` so the calendar refetches for the new wallet. If the calendar tab is
currently active, also trigger an immediate refetch so the user sees the new wallet's data.

**Contract**: In `_renderPortfolioTabs()` wallet button click handler (around line 1948),
after `_activePortfolioId = p.portfolio_id`, add:
```javascript
_ppCalData = null;
const curMode = view.querySelector('.pp-view-tab.active')?.dataset.mode;
if (curMode === 'calendar') fetchPortfolioCalendar();
```
Note: `_ppTreemapData` is NOT reset here (treemap shows all wallets simultaneously and does
not change on wallet switch). The calendar is per-portfolio_id so it always needs a reset.

#### 9. JS: initialize calendar year/month on view build (static/index.html)

**File**: `static/index.html`

**Intent**: Set `_ppCalYear` and `_ppCalMonth` to the current year/month when the
portfolio view is first built (`_buildPortfolioPositionsViewContent`), so the initial
calendar fetch targets the current month.

**Contract**: In `_buildPortfolioPositionsViewContent()` (around line 2155), add:
```javascript
const _now = new Date();
_ppCalYear = _now.getFullYear();
_ppCalMonth = _now.getMonth() + 1;
```

### Success Criteria

#### Automated Verification

- `uv run pytest tests/test_api.py` passes — no regressions from import changes in Phase 3

> E2E tests for the frontend are created in a separate `/10x-e2e pul-59-portfolio-calendar`
> session after all 4 phases pass manual verification. No automated E2E gate at this phase.

#### Manual Verification

- Clicking "Kalendarz" tab shows a full monthly grid for the current month
- Green cells for days with positive P&L, red for negative, grey for weekends/holidays
- Days with partial price data (some tickers missing) still show coloured P&L
- Prev/next buttons navigate to previous/next month; data reloads
- Switching wallet tab while in Calendar mode triggers a fresh calendar fetch
- Switching back to Tabela or Treemapa hides the calendar correctly
- Calendar renders on mobile (cells readable, no overflow)
- Both prev and next buttons are disabled during loading and re-enabled on completion

---

## Testing Strategy

### Unit Tests

**tests/test_portfolio_calendar.py** (new file):
- `compute_calendar_pnl` with realistic BQ rows for a full June 2026:
  - Normal case: trading days with full prices → green/red cells with correct PLN amounts
  - Partial prices (prices_found < total_positions) → coloured cell with best-effort amount
  - No lookback baseline (empty extended range) → first trading day has pnl_abs=None
  - Empty portfolio (rows=[]) → all days neutral, no error
  - GPW holiday (weekday absent from rows) → state="no_session"
  - Future days (rows only go up to today) → state="future"
  - February edge case (28/29 days)

**tests/test_bigquery.py** additions:
- `get_portfolio_calendar_data` with mocked BQ client verifies correct ScalarQueryParameter
  types (DATE for lookback_start, end_date; STRING for portfolio_id, user_id)

### API Tests

**tests/test_api.py** additions (patch `src.api.get_portfolio_calendar_data` and
`src.api.compute_calendar_pnl`):
- 200 with valid params and matching portfolio_id
- 403 with portfolio_id not belonging to client_id
- 422 with invalid month (0, 13, non-integer)
- 422 with invalid year (out of allowed range)
- 500 when BQ raises BigQueryError

### E2E Tests (Phase 5 — separate /10x-e2e session)

**tests/e2e/test_portfolio_calendar.py**:
- Switch to Kalendarz tab → calendar grid visible
- Navigate prev/next month → month label updates, grid reloads
- Green/red cells present when mock data has positive/negative pnl_abs
- Neutral cells present for days with pnl_abs=None
- Switching wallet tab → calendar refetches for new portfolio_id
- Locators: getByRole, getByText only — no CSS selectors

### Manual Testing Steps

1. Open Mój portfel → click Kalendarz → verify current month grid appears
2. Click Prev → verify previous month loads (including correct day-of-week alignment)
3. Click Next (back to current month) → verify month label returns to current
4. Switch to a different wallet tab → verify calendar reloads (different data or empty)
5. Verify weekend cells (Sat/Sun) are grey
6. If a GPW holiday is in the queried month, verify it's also grey (check https://www.gpw.pl/szczegoly-sesji)
7. Verify green cell shows e.g. "+320 PLN", red cell shows "−150 PLN"
8. Verify layout on mobile viewport (no horizontal overflow, cells readable)
9. Tab back to Tabela → calendar disappears; tab back to Treemapa → treemap shows

## Performance Considerations

The BQ query performs a CROSS JOIN (trading_days × positions). For a user with 20 positions and
a typical month with 22 trading days (extended to ~35), the CROSS JOIN produces at most 700 rows
before the LEFT JOIN aggregation. This is negligible for BigQuery. No pagination needed.

The company_daily_stats table is partitioned by snapshot_date — the WHERE clause on
snapshot_date BETWEEN @lookback_start AND @end_date is partition-pruned by BQ automatically.

## References

- Research: `context/changes/pul-59-portfolio-calendar/research.md`
- GPW session calendar: https://www.gpw.pl/szczegoly-sesji
- Treemap endpoint (template): `src/api.py:552–589`
- View tab toggle (extend): `static/index.html:2166–2182`
- Treemap fetch function (template): `static/index.html:2334–2347`
- BQ date parameter pattern: `db/bigquery.py:288–292`
- Lessons (reserved keywords, SPA pagination): `context/foundation/lessons.md`
- Prior portfolio change plans: `context/archive/2026-06-25-non-admin-portfolio-treemap/plan.md`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands.

### Phase 1: BigQuery — get_portfolio_calendar_data()

#### Automated

- [x] 1.1 `uv run pytest tests/test_bigquery.py -k calendar` passes
- [x] 1.2 `uv run ruff check db/bigquery.py` passes
- [x] 1.3 `uv run mypy db/bigquery.py --ignore-missing-imports` passes

#### Manual

- [x] 1.4 Round-trip test against real BQ returns rows for trading days in queried month
- [x] 1.5 SQL has no un-backticked reserved keywords

### Phase 2: Compute Function — src/portfolio_calendar.py

#### Automated

- [ ] 2.1 `uv run pytest tests/test_portfolio_calendar.py` passes (all edge cases)
- [ ] 2.2 `uv run ruff check src/portfolio_calendar.py` passes
- [ ] 2.3 `uv run mypy src/portfolio_calendar.py --ignore-missing-imports` passes

#### Manual

- [ ] 2.4 Python one-liner with empty rows produces 30-day neutral grid without error

### Phase 3: FastAPI Endpoint — GET /api/portfolio/calendar

#### Automated

- [ ] 3.1 `uv run pytest tests/test_api.py -k calendar` passes (auth, validation, 500 cases)
- [ ] 3.2 `uv run pytest tests/test_api.py` passes (no regressions)
- [ ] 3.3 `uv run ruff check src/api.py` passes
- [ ] 3.4 `uv run mypy src/api.py --ignore-missing-imports` passes

#### Manual

- [ ] 3.5 curl against local server returns JSON with 30 day objects
- [ ] 3.6 Endpoint visible in /docs

### Phase 4: Frontend — Kalendarz Tab in Mój portfel

#### Automated

- [ ] 4.1 `uv run pytest tests/test_api.py` passes (no regressions)

#### Manual

- [ ] 4.2 Clicking Kalendarz tab shows monthly grid
- [ ] 4.3 Prev/next navigation loads correct months
- [ ] 4.4 Green/red/grey cells render correctly
- [ ] 4.5 Wallet switch triggers calendar refetch
- [ ] 4.6 Tabela/Treemapa tabs still work (no regressions)
- [ ] 4.7 Mobile layout renders without overflow
