# Portfolio Value-History Endpoint (PUL-79 / FARO-5) Implementation Plan

## Overview

Add `GET /api/portfolio/history?portfolio_id=…&range=1w|1m|3m|1y` returning a
per-trading-day series `[{date, value_pln, pnl_pln}]` for the caller's own portfolio.
The series is computed on the fly by generalizing the existing calendar query to an
arbitrary date range; the precomputed `portfolio_snapshots` table is **not** used
(research proved it is owner-only, wallet-keyed, sparse, and stale).

## Current State Analysis

- `get_portfolio_calendar_data` (`db/bigquery.py:362-457`) already computes daily portfolio
  value from `user_portfolio_positions` × historical close, ETF-safe via
  `COALESCE(company_daily_stats, etf_quotes)`, driven off `SELECT DISTINCT snapshot_date`
  (trading-days-only for free). It is scoped to one `year/month` window.
- `GET /api/portfolio/calendar` (`src/api.py:880-912`) is the endpoint pattern to clone:
  JWT deps (`_get_role` + `_get_user_id`), `list_user_portfolios` ownership guard (403),
  `_perf_get/_perf_set` cache (300 s), `BigQueryError → 500`.
- Per-user auth is JWT-only (`_get_user_id`, `src/api.py:153`); the ticket's
  "X-API-Key + X-Client-Id" line is stale (PUL-74) — ignore it.
- `user_portfolio_positions` stores `shares` + `avg_buy_price`, **no transaction dates**
  (`db/bigquery.py:530-535`).
- E2E patches every `src.api.*` BQ function in `tests/e2e/conftest.py`; a new function
  left unmocked raises against a real client.

## Desired End State

A logged-in user calls `GET /api/portfolio/history?portfolio_id=<own>&range=3m` and receives
a JSON array of `{date: "YYYY-MM-DD", value_pln: <float>, pnl_pln: <float>}`, one entry per
trading day in the range, ascending by date. `range=1d` and any unrecognized range → 422.
A portfolio not owned by the caller → 403. ETFs are included in the values. Verified by unit
tests on the BQ function, endpoint tests (valid ranges, 422, 403, 401), and a manual curl
against prod after deploy.

### Key Discoveries:

- Trading-days-only comes free from `SELECT DISTINCT snapshot_date FROM company_daily_stats`
  (`db/bigquery.py:396-399`) — do not generate a synthetic date range.
- ETF union is already in the calendar query (`db/bigquery.py:410-411`) — preserve it.
- `pnl_pln` = `value_pln − SUM(shares × avg_buy_price)` (cumulative unrealized, cost basis is
  a constant baseline). `avg_buy_price` is available in the positions CTE.
- Ownership guard + status: mirror calendar's **403** (`src/api.py:903`), not positions' 404.
- `company_daily_stats` has ~31% per-day gaps ([[project-company-daily-stats-query-pattern]]).
  **The calendar fn's 0-fill (`db/bigquery.py:422`) is WRONG for a continuous line** — verified
  on the live owner portfolio: coverage is 9/12 for most of July then 12/12 from the 17th, so
  0-fill yields a fake ~25% value step and an even deeper `pnl` dip (full cost basis subtracted
  on 9-priced days). History must instead **forward-fill (LOCF)** each ticker's last known close
  and **clamp the series start to the first fully-covered trading day**, computing `pnl` over the
  same priced set as value.

## What We're NOT Doing

- Not using / backfilling / writing `portfolio_snapshots` (owner-only, unusable — see research).
- Not adding a `1d` / intraday range (no intraday data stored) — separate future ticket.
- Not building the frontend line chart / range switcher (Designer adds after deploy).
- Not adding transaction-date tracking to fix the tranche approximation (out of scope).
- Not changing the calendar endpoint or `get_portfolio_calendar_data` (add a sibling, don't
  refactor the existing one).

## Implementation Approach

Two layers, bottom-up. First a new BQ function `get_portfolio_history` that generalizes the
calendar query to `[start_date, CURRENT_DATE()]` and returns `value_pln` + cumulative
`pnl_pln`. Then the API layer: a range→start-date resolver, a Pydantic response model, the
endpoint (clone of calendar), and the conftest mock. TDD-friendly: the BQ function and the
resolver both have pure, unit-testable cores.

## Critical Implementation Details

- **Price gaps must be forward-filled, not 0-filled (F1 — the load-bearing constraint):**
  the calendar query's `ELSE 0` on a missing close is fine for a per-day heatmap but produces
  spurious jumps/dips in a continuous line. History must carry each ticker's **last known close
  forward (LOCF)** across trading days, **clamp the emitted series to start at the first day on
  which every held position has a (carried) price**, and compute `pnl_pln` over that same priced
  set so value and cost basis always agree. Consequence to accept: a `1y` request may return
  fewer than 1y of points when a holding's price history is short (e.g. ETFs whose `etf_quotes`
  history starts mid-range) — this is correct behavior, not a bug.
- **1y tranche approximation (accepted, must be documented):** positions carry no purchase
  dates, so "value on day X" uses *today's* share counts against day-X close. Over `1y` this
  can misrepresent history for tranche buyers. Document it in the function docstring and the
  plan; the endpoint still serves `1y`. A UI-side caveat is the Designer's call, out of scope.

## Phase 1: BQ layer — `get_portfolio_history`

### Overview

New function returning the daily value + cumulative P&L series for a portfolio over a range.

### Changes Required:

#### 1. New BQ function

**File**: `db/bigquery.py` (add next to `get_portfolio_calendar_data`, ~line 458)

**Intent**: Compute one row per trading day in `[start_date, CURRENT_DATE()]` giving the
portfolio's total value and cumulative unrealized P&L, ETF-safe, for `(user_id, portfolio_id)`.
Mirrors the calendar query but with a caller-supplied start date and no month/lookback logic.

**Contract**: `def get_portfolio_history(portfolio_id: str, user_id: str, start_date: date) -> list[dict]`.
Returns `[{"snapshot_date": date, "value_pln": float, "pnl_pln": float}]` ascending by date;
`[]` when the portfolio has no positions **or no fully-covered trading day exists in range**.

The query reuses the calendar CTEs for `trading_days` (from `company_daily_stats`,
`snapshot_date BETWEEN @start_date AND CURRENT_DATE()`), `positions` (`ticker, shares,
avg_buy_price` for the user+portfolio) and the ETF-safe close
(`COALESCE(cds.kurs_zamkniecia, etq.kurs_zamkniecia)`), but **must NOT 0-fill** (F1). Instead:

- **Forward-fill (LOCF):** for each `(ticker, trading_day)` take the most recent available close
  on or before that day. Implement via a per-day correlated pick, e.g. `LAST_VALUE(close IGNORE
  NULLS) OVER (PARTITION BY ticker ORDER BY snapshot_date ROWS BETWEEN UNBOUNDED PRECEDING AND
  CURRENT ROW)` over the ticker's own price rows, or an equivalent `ARRAY_AGG(... ORDER BY date
  DESC LIMIT 1)` lateral. To fill early in-range days, the price scan window must reach **before**
  `@start_date` (scan e.g. `DATE_SUB(@start_date, INTERVAL 400 DAY)`), then keep only rows in
  `[@start_date, CURRENT_DATE()]` for output.
- **Clamp start to full coverage:** emit a day only when the carried price is non-NULL for
  **every** held position that day (`COUNTIF(px IS NULL) = 0`). This drops leading days where a
  holding has no prior price yet.
- Per emitted day: `value_pln = SUM(shares * px_ff)`, `pnl_pln = value_pln − SUM(shares *
  avg_buy_price)` — over the same (fully-priced) position set, so the two always agree.
- `ORDER BY snapshot_date`. Wrap in try/except → `BigQueryError`; add a `logger.debug` timing
  line, matching the calendar fn.

#### 2. Unit tests

**File**: `tests/test_bigquery.py` (near the existing calendar-fn tests)

**Intent**: Verify the row-mapping contract and the P&L formula against a mocked client, in the
style of the existing `get_portfolio_calendar_data` tests.

**Contract**: Mock `_get_client().query(...).result()` to return rows with
`snapshot_date/value_pln/pnl_pln`; assert the returned dicts, float coercion, empty-positions
`[]`, and that a query failure raises `BigQueryError`. Additionally assert the query text
carries the F1 constraints (structural check, like `test_etf_bigquery.py` does for the ETF
COALESCE): it must forward-fill (`IGNORE NULLS` / LAST_VALUE or equivalent), scan before
`@start_date`, and gate emitted days on full coverage — i.e. it must NOT contain the calendar's
`ELSE 0` 0-fill.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_bigquery.py -k history`
- Full suite green: `uv run pytest`
- Lint/type clean: `uv run ruff check db/bigquery.py tests/test_bigquery.py`

#### Manual Verification:

- Ad-hoc `bq query --project_id=puls-gpw` with the generalized SQL over the real owner
  portfolio returns a plausible value series including the ETF position, with **no spurious
  ~25% step at the 9/12→12/12 coverage boundary** (~2026-07-17) — i.e. the clamp/LOCF actually
  removed the F1 artifact.

**Implementation Note**: After automated verification passes, pause for human confirmation of
the manual bq check before starting Phase 2.

---

## Phase 2: API layer — endpoint, model, resolver, mock

### Overview

Expose the BQ function as `GET /api/portfolio/history`, with range parsing, response model,
ownership guard, cache, and the E2E mock.

### Changes Required:

#### 1. Range resolver

**File**: `src/api.py` (module-level helper near the other portfolio helpers)

**Intent**: Map a `range` string to a start date; reject unsupported values (including `1d`).

**Contract**: `def _history_start_date(range_: str) -> date` (or inline mapping) using
**day-based floors** relative to `date.today()` — `1w=7, 1m=30, 3m=90, 1y=365` days via
`date.today() - timedelta(days=N)`. Do **not** add `dateutil`/`relativedelta` (not a repo dep);
exact calendar-month boundaries don't matter for a "last N" window. Unknown / `1d` → the
endpoint raises `HTTPException(422, "range must be one of 1w|1m|3m|1y")`. Keep the mapping in
one place so the resolver and the 422 message can't drift.

#### 2. Response model

**File**: `src/api.py` (near `PortfolioCalendarResponse` / `PortfolioPositionOut`, ~line 270)

**Intent**: Typed output element for the series.

**Contract**: `class PortfolioHistoryPoint(BaseModel)` with `date: str`, `value_pln: float`,
`pnl_pln: float`. Endpoint serializes each BQ row via `snapshot_date.isoformat()` → `date`.

#### 3. The endpoint

**File**: `src/api.py` (add after `get_portfolio_calendar`, ~line 913)

**Intent**: JWT-authenticated, per-user history endpoint cloning the calendar endpoint's
structure.

**Contract**: `@app.get("/api/portfolio/history")`, params `portfolio_id: str = Query(...)`,
`range: str = Query(...)`, deps `role: Role = Depends(_get_role)`, `user_id: str = Depends(_get_user_id)`.
Flow: resolve range (422 on bad); cache key `f"history:{user_id}:{portfolio_id}:{range}"` via
`_perf_get(..., ttl=300)`; ownership guard `list_user_portfolios(user_id)` → **403** if
`portfolio_id` not owned (match calendar); call `get_portfolio_history(...)` (try/except
`BigQueryError → 500`); map rows to `[PortfolioHistoryPoint(...).model_dump() for …]`;
`_perf_set` and return. Import `get_portfolio_history` in the `db.bigquery` import block.

#### 4. E2E mock

**File**: `tests/e2e/conftest.py`

**Intent**: Patch the new BQ function so the endpoint runs against fakes, not a real client.

**Contract**: Add `_fake_get_portfolio_history(portfolio_id, user_id, start_date)` returning a
small ascending series for `_FAKE_PORTFOLIO_ID` (mirroring `_FAKE_CALENDAR_ROWS`, with
`value_pln`/`pnl_pln` keys) and `[]` otherwise; register
`patch("src.api.get_portfolio_history", side_effect=_fake_get_portfolio_history)` in the
`live_server_url` patch stack.

#### 5. Endpoint tests

**File**: `tests/test_api.py` (or the existing portfolio-endpoint test module)

**Intent**: Cover the endpoint contract.

**Contract**: With auth/session fixtures used by the calendar/positions tests: valid range
returns the mapped series; `range=1d` and `range=xx` → 422; unowned `portfolio_id` → 403;
missing session → 401.

### Success Criteria:

#### Automated Verification:

- Endpoint + resolver tests pass: `uv run pytest tests/test_api.py -k history`
- E2E green: `uv run pytest tests/e2e`
- Full suite green: `uv run pytest`
- Lint/type clean: `uv run ruff check src/api.py tests`

#### Manual Verification:

- After merge+deploy, `curl` the prod run.app URL with a real session cookie:
  `GET /api/portfolio/history?portfolio_id=<own>&range=3m` returns an ascending
  `{date,value_pln,pnl_pln}` array including the ETF position; `range=1d` → 422; someone
  else's `portfolio_id` → 403.

**Implementation Note**: After automated verification passes, pause for human confirmation of
the prod curl check before archiving.

---

## Testing Strategy

### Unit Tests:

- `get_portfolio_history`: row mapping, float coercion, empty-positions `[]`, `BigQueryError`
  on query failure, and the F1 query-structure checks (LOCF / `IGNORE NULLS`, pre-`start_date`
  scan window, full-coverage day gate, no `ELSE 0`).
- Range resolver: each of `1w/1m/3m/1y` maps to the expected start date; `1d` and unknown
  strings rejected.

### Integration Tests:

- Endpoint: valid range → series; 422 (1d + garbage); 403 (unowned portfolio); 401 (no session).
- E2E via `live_server_url` with the new fake wired into the patch stack.

### Manual Testing Steps:

1. Ad-hoc `bq query` of the generalized SQL over the owner portfolio (Phase 1).
2. Prod curl for `range=3m`, `range=1d` (422), and an unowned `portfolio_id` (403) (Phase 2).

## Performance Considerations

Query cost equals the existing calendar query scaled by range width (1y ≈ ~250 trading days ×
N positions). Bounded and cached 300 s per `(user, portfolio, range)`. No pagination needed —
payload is a few hundred small objects at most.

## Migration Notes

None — no schema change, no new GCP client (reuse `_get_client()`), no data migration.

## References

- Related research: `context/changes/pul-79-portfolio-value-history/research.md`
- Generalize: `db/bigquery.py:362-457` (`get_portfolio_calendar_data`)
- Clone: `src/api.py:880-912` (`GET /api/portfolio/calendar`)
- Auth: `src/api.py:127-159` (`_get_role`, `_get_user_id`)
- E2E mock pattern: `tests/e2e/conftest.py:346-349, 552-555`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: BQ layer — get_portfolio_history

#### Automated

- [x] 1.1 Unit tests pass: `uv run pytest tests/test_bigquery.py -k history` — 4c4a96c
- [x] 1.2 Full suite green: `uv run pytest` — 4c4a96c
- [x] 1.3 Lint/type clean: `uv run ruff check db/bigquery.py tests/test_bigquery.py` — 4c4a96c

#### Manual

- [x] 1.4 Ad-hoc bq query returns a plausible value series incl. the ETF position, with no spurious step at the 9/12→12/12 coverage boundary — 4c4a96c

### Phase 2: API layer — endpoint, model, resolver, mock

#### Automated

- [x] 2.1 Endpoint + resolver tests pass: `uv run pytest tests/test_api.py -k history` — 7a95b30
- [x] 2.2 E2E green: `uv run pytest tests/e2e` — 7a95b30
- [x] 2.3 Full suite green: `uv run pytest` — 7a95b30
- [x] 2.4 Lint/type clean: `uv run ruff check src/api.py tests` — 7a95b30

#### Manual

- [x] 2.5 Prod curl: route live + auth-gated (/health 200; /api/portfolio/history no-session→401 not 404, deploy c95a883); authenticated body/422/403 contract covered by endpoint tests + E2E + live BQ query
