# Portfolio sparklines — price_history on /api/portfolio/positions Implementation Plan

## Overview

Extend `GET /api/portfolio/positions` so each position carries `price_history: number[]` — the close prices (PLN) from the last 30 trading sessions, ascending by date (oldest first), no dates. The frontend sparkline (`_sparklineSvg`, `static/index.html:2969`) is already built and rendered; this plan is the backend slice that feeds it. The work is a near-verbatim generalization of the existing ETF-safe `current_price` lookup in `list_user_portfolio_positions` (`db/bigquery.py:641-703`), from `rn=1` to `rn<=30` with `ARRAY_AGG`.

## Current State Analysis

- `GET /api/portfolio/positions` (`src/api.py:637-672`) is per-user (deps `_get_role` + `_get_user_id`, **not** admin-gated — the user's own wallet, no score/sentiment), 404s on a foreign `portfolio_id`, and caches per-user 30 s at `positions:{user_id}:{portfolio_id}`. It builds each row via `PortfolioPositionOut(**row, pnl_pln=…, pnl_pct=…).model_dump()`.
- `PortfolioPositionOut` (`src/api.py:270-280`) has `model_config = ConfigDict(extra="ignore")` — so any `price_history` key in the BQ row is silently dropped **unless** the field is declared on the model.
- `list_user_portfolio_positions` (`db/bigquery.py:641-703`) already COALESCEs the latest close from `company_daily_stats` **and** `etf_quotes` via two `ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC)` CTEs, `rn=1`. Close-price column = **`kurs_zamkniecia`**; date = **`snapshot_date`** (DATE); change % = `zmiana_procentowa`. Returns `[dict(row) for row in rows]`.
- **The same BQ function is also called by the treemap path** (`src/api.py:801`, `list_user_portfolio_positions(user_id)` with no `portfolio_id`). It must NOT pay for the history aggregation.
- The frontend contract is forgiving: `_sparklineSvg` returns `"—"` when the value is not an array **or** has `length < 2`. So `None`, `[]`, and single-element arrays are all safe.
- E2E fake `_fake_list_user_portfolio_positions` (`tests/e2e/conftest.py:357`, patched at `:505`) has signature `(user_id, portfolio_id=None)` — it will raise `TypeError` the moment the endpoint passes `include_history=True` unless the kwarg is added.

## Desired End State

`GET /api/portfolio/positions` returns each position with a `price_history` field:
- A JSON array of up to 30 floats (close prices, PLN), ascending by `snapshot_date`, when history exists.
- `null` when the ticker has no rows in the 90-day scan window.
- Correct for **ETFs/ETCs/ETNs** (sourced from `etf_quotes`), not just companies.

The treemap path (`src/api.py:801`) is unchanged — it never fetches history. Verify by: (a) unit tests on the BQ query shape and the endpoint contract pass; (b) the e2e asserts a sparkline `<svg>` renders for a position with history and `"—"` for one without; (c) the full suite stays green.

### Key Discoveries:

- The exact ETF-safe pattern to mirror: `db/bigquery.py:681-683` — `COALESCE(ls.kurs_zamkniecia, etf.kurs_zamkniecia) AS current_price`.
- `include_history: bool = False` gating mirrors the existing conditional-SQL branch already used for `portfolio_filter` in the same function.
- `company_daily_stats` has ~31% per-day gaps ([[project-company-daily-stats-query-pattern]]) — rank `snapshot_date DESC`, keep `rn<=30`, take whatever exists; a 90-day floor guarantees ≥30 reachable sessions while bounding the scan.
- ETF freshness depends on the `etf_quotes` scheduler ([[project-etf-quotes-scheduler]]); the union is what keeps ETF sparklines from silently rendering `"—"`.

## What We're NOT Doing

- No new endpoint, no route change, no auth/gating change.
- No BigQuery schema change (read-only over existing tables).
- No frontend change — `_sparklineSvg` and its render site already exist and are correct.
- No treemap change — the treemap call stays on the `include_history=False` default.
- No pagination or cache-TTL change — 30 floats × ~10-30 positions is a few KB, comfortably inside the existing 30 s cache.
- No `[]` normalization — empty history is `None` (decided); the frontend renders `"—"` for both.

## Implementation Approach

Extend the single BQ function with an opt-in history block, add the model field, and flip the one call site (the positions endpoint) to `include_history=True`. Because the endpoint already does `PortfolioPositionOut(**row, …)`, once the BQ row dict carries `price_history` and the model declares the field, it maps automatically with no further call-site change. The treemap call site is left on the default. Phase 1 is the logic (TDD-driven); Phase 2 wires the e2e fake and verifies the render/fallback.

## Critical Implementation Details

- **Ordering inside the array is load-bearing.** `ARRAY_AGG(kurs_zamkniecia ORDER BY snapshot_date ASC)` — oldest→newest. The frontend colours the line green when `hist[last] >= hist[0]` and draws left-to-right; a DESC array would invert both.
- **Rank on a deduped set, not the raw union.** Dedup by `(ticker, snapshot_date)` keeping company-over-etf (`src` 0 vs 1) **before** the `rn<=30` ranking, so a ticker that ever appears in both tables can't consume two slots for one day or double-count. This preserves parity with `current_price`'s company-first COALESCE.
- **Filter `kurs_zamkniecia IS NOT NULL` before aggregating** so the array is clean numbers — the frontend does no null-skipping inside the array.

## Phase 1: Backend — history query + model field

### Overview

Add the opt-in history aggregation to `list_user_portfolio_positions`, declare `price_history` on `PortfolioPositionOut`, and have the positions endpoint request history.

### Changes Required:

#### 1. History aggregation in the BQ function

**File**: `db/bigquery.py`

**Intent**: Extend `list_user_portfolio_positions` with an `include_history: bool = False` parameter. When `True`, compute a per-ticker 30-session close-price array unioned across `company_daily_stats` and `etf_quotes`, and attach it to each returned row as `price_history` (a `list[float]`, or `None` when the ticker has no rows). When `False`, the query and return shape are byte-for-byte what they are today.

**Contract**: New signature `list_user_portfolio_positions(user_id, portfolio_id=None, include_history=False)`. When `include_history=True`, the query gains four CTEs and a `LEFT JOIN` on `ticker`, exposing `ph.price_history`. The history CTEs (only emitted when opted in):

```sql
WITH hist_raw AS (
  SELECT ticker, snapshot_date, kurs_zamkniecia, 0 AS src
  FROM `{company_daily_stats}`
  WHERE snapshot_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY) AND kurs_zamkniecia IS NOT NULL
  UNION ALL
  SELECT ticker, snapshot_date, kurs_zamkniecia, 1 AS src
  FROM `{etf_quotes}`
  WHERE snapshot_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY) AND kurs_zamkniecia IS NOT NULL
),
hist_dedup AS (
  SELECT ticker, snapshot_date, kurs_zamkniecia
  FROM hist_raw
  QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker, snapshot_date ORDER BY src) = 1
),
hist_ranked AS (
  SELECT ticker, snapshot_date, kurs_zamkniecia,
         ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC) AS rn
  FROM hist_dedup
),
price_hist AS (
  SELECT ticker, ARRAY_AGG(kurs_zamkniecia ORDER BY snapshot_date ASC) AS price_history
  FROM hist_ranked WHERE rn <= 30 GROUP BY ticker
)
-- main SELECT … LEFT JOIN price_hist ph ON <positions>.ticker = ph.ticker → ph.price_history
```

Table names come from the existing constants `_COMPANY_DAILY_STATS_TABLE_NAME` (`:2168`) and `_ETF_QUOTES_TABLE_NAME` (`:2368`). A LEFT-JOIN miss yields SQL `NULL` → Python `None`, which is the desired empty representation. Keep the existing `portfolio_filter` conditional-SQL branch as the pattern for splicing the history block in only when opted in.

#### 2. Model field

**File**: `src/api.py`

**Intent**: Declare `price_history` on `PortfolioPositionOut` so `extra="ignore"` no longer drops it.

**Contract**: Add field `price_history: list[float] | None = None` to `PortfolioPositionOut` (`:270-280`). No call-site change needed beyond this — `PortfolioPositionOut(**row, …)` maps it automatically.

#### 3. Endpoint opts into history

**File**: `src/api.py`

**Intent**: The positions endpoint requests history; the treemap path does not.

**Contract**: At `src/api.py:637-672`, change the positions-endpoint call to `list_user_portfolio_positions(user_id, portfolio_id, include_history=True)`. Leave the treemap call at `:801` (`list_user_portfolio_positions(user_id)`) untouched — it stays on the `False` default.

### Success Criteria:

#### Automated Verification:

- Unit tests for the history query shape pass: `uv run pytest tests/test_api.py -k "price_history or sparkline or positions" -q`
- Full backend suite green: `uv run pytest -q`
- Endpoint returns `price_history` on positions and it is absent/None on the treemap path (asserted in tests).

#### Manual Verification:

- Hitting `/api/portfolio/positions` for a wallet with at least one company **and** one ETF position returns a non-null `price_history` array for both.
- A freshly-added ticker with no stats history returns `price_history: null` and does not error.
- Treemap view still renders (no regression, no history payload on that path).

**Implementation Note**: After Phase 1 automated verification passes, pause for manual confirmation before Phase 2.

---

## Phase 2: E2E fake + render verification

### Overview

Update the e2e fake to accept the new kwarg and seed history so an E2E can prove the sparkline renders vs. falls back to `"—"`.

### Changes Required:

#### 1. E2E fake accepts include_history and seeds history

**File**: `tests/e2e/conftest.py`

**Intent**: Prevent the `TypeError` when the endpoint passes `include_history=True`, and give the E2E data to assert against.

**Contract**: Change `_fake_list_user_portfolio_positions` (`:357`, patched at `:505`) signature to `(user_id, portfolio_id=None, include_history=False)`. In `_FAKE_PORTFOLIO_POSITIONS` (`:~283`), give at least one position a `price_history` array of ≥2 ascending floats, and leave at least one position without it (or `None`) so both the render and the `"—"` fallback are exercised.

#### 2. E2E assertion for sparkline render + fallback

**File**: `tests/e2e/` (the portfolio/positions spec)

**Intent**: Assert the sparkline `<svg>` renders for the position with history and `"—"` shows for the one without.

**Contract**: Extend the existing positions/my-wallet E2E: for the seeded position with history, assert an `<svg>` (polyline) is present in the "30 dni" cell; for the one without, assert the cell text is `"—"`. Follow existing E2E locator rules (getByRole/getByText, no CSS/XPath), wait on state not timeouts.

### Success Criteria:

#### Automated Verification:

- E2E suite green: `uv run pytest tests/e2e -q` (or the project's Playwright invocation for this spec).
- The new assertions pass for both the render and the fallback case.

#### Manual Verification:

- Open my-wallet in the browser against a real wallet; sparklines draw for tickers with history, `"—"` for those without, ETFs included.

**Implementation Note**: After Phase 2 automated verification passes, pause for manual confirmation before closing the change.

---

## Testing Strategy

### Unit Tests:

- `include_history=False` leaves the query/return shape unchanged (no `price_history` key, or None) — protects the treemap path.
- `include_history=True` attaches `price_history` as an ascending float array; None when the ticker has no rows.
- ETF ticker (only in `etf_quotes`) still gets history via the union.
- Dedup: a ticker present in both tables yields one value per date (company wins), ≤30 elements.

### Integration Tests:

- `GET /api/portfolio/positions` end-to-end returns `price_history` per position; 404 path and cache behavior unchanged.

### Manual Testing Steps:

1. Call `/api/portfolio/positions` for a mixed company+ETF wallet → both carry `price_history`.
2. Add a ticker with no stats → `price_history: null`, no error.
3. Confirm treemap view unaffected.

## Performance Considerations

30 floats × ~10-30 positions ≈ a few KB — inside the existing 30 s positions cache; no pagination needed. The 90-day scan floor bounds the history CTE scan; `include_history=False` keeps the treemap path free of the aggregation entirely.

## Migration Notes

None — read-only over existing tables, no schema change, no data migration.

## References

- Related research: `context/changes/portfolio-sparklines-price-history/research.md`
- Pattern to generalize: `db/bigquery.py:681-683` (`current_price` COALESCE)
- Multi-day UNION prior art: `db/bigquery.py:372-450` (`get_portfolio_calendar_data`)
- Frontend contract: `static/index.html:2969-2977, 3006` (`_sparklineSvg`)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Backend — history query + model field

#### Automated

- [x] 1.1 Unit tests for history query shape pass (`pytest -k "price_history or sparkline or positions"`) — c95728e
- [x] 1.2 Full backend suite green (`uv run pytest -q`) — c95728e
- [x] 1.3 price_history present on positions path, absent/None on treemap path (asserted) — c95728e

#### Manual

- [x] 1.4 Mixed company+ETF wallet returns non-null price_history for both — c95728e
- [x] 1.5 No-history ticker returns price_history: null without error — c95728e
- [x] 1.6 Treemap view still renders, no history payload on that path — c95728e

### Phase 2: E2E fake + render verification

#### Automated

- [x] 2.1 E2E suite green (`uv run pytest tests/e2e -q`) — 441662a
- [x] 2.2 New assertions pass for both render and fallback cases — 441662a

#### Manual

- [x] 2.3 Browser: sparklines draw for tickers with history, "—" otherwise, ETFs included — 441662a
