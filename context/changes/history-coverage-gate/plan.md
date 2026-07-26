# Full-coverage gate — backward-fill debut prices (PUL-100)

## Overview

`get_portfolio_history` emits a day only when **every** held position has a price that
day. The series therefore starts at the *latest* first-price date across all holdings,
so a single freshly listed company truncates the whole chart. We add a symmetric
backward-fill (BOCF) so pre-debut days carry the debut close, demote the coverage gate
to a real safety net (fires only when a ticker has no price anywhere), and surface the
assumption in the UI instead of hiding it.

## Current State Analysis

- `db/bigquery.py:465` `get_portfolio_history(portfolio_id, user_id, start_date) -> list[dict]`.
  - `filled` CTE (`:537`) forward-fills only: `LAST_VALUE(px IGNORE NULLS) OVER (... UNBOUNDED PRECEDING AND CURRENT ROW)`.
  - `daily` (`:546`) computes `COUNTIF(px_ff IS NULL) AS missing`; the outer `WHERE missing = 0` (`:558`) is the gate.
  - The scan window reaches `start_date − 400 days`, so a debut inside the requested range is visible to the query.
- `src/api.py:994` `GET /api/portfolio/history` returns a **bare JSON array** of
  `PortfolioHistoryPoint(date, value_pln, pnl_pln)`.
- `static/index.html:4212` `_fetchPortfolioHistorySlot` consumes that array directly and
  hands it to `_renderPortfolioHistory(data, chartEl)` (`:4240`ish). Chart titles are set
  via `.textContent` on `#pp-history-title-active` / `#pp-history-title-all` (`:4194`, `:4197`).
- Tests that pin the current contract: `tests/test_bigquery.py:1428/1449/1473`,
  `tests/test_api.py:1623`, and the e2e mock `tests/e2e/conftest.py:378`.

Measured on real BigQuery, 2026-07-26, `?range=1y`:

| view | points | latency | span |
|---|---|---|---|
| Wszystkie | 71 | 1878 ms | 2026-04-16 → 2026-07-24 |
| Główny (13 positions) | 71 | 1616 ms | 2026-04-16 → 2026-07-24 |
| second portfolio (8 positions) | 249 | 1371 ms | 2025-07-28 → 2026-07-24 |

Partition scan for the 1y+400d window: **2.5 MB**. PUL-100 point 3 ("measure the
endpoint") is therefore already answered — clustering absorbed the growth from 16k to
1.9M rows delivered by PUL-92. No optimisation work is in scope.

## Desired End State

Requesting `1y` on a portfolio holding a recent IPO returns a full year of points. Days
before a holding's debut value it at its debut close, so the series is continuous — no
phantom step on the first quoted day. The chart carries an `(i)` affordance naming every
holding that was valued at its debut price and from when. A holding with no price data
anywhere is excluded from the valuation (rather than blanking the chart) and reported
through the same affordance.

Verified by: re-running the baseline benchmark and seeing ~249 points for both the
"Wszystkie" view and "Główny"; and by asserting that the value on the day before a
debut equals the value on the debut day for the backfilled leg.

### Key Discoveries

- The gate is mathematically correct but UX-hostile: `S2B` (4 shares, 267 PLN, **0.66%**
  of a 40 261 PLN portfolio, listed 2026-04-16) clamps nine months of history.
- After LOCF+BOCF, `px_ff IS NULL` becomes **all-or-nothing per ticker** — it can only
  happen when a ticker has no price anywhere in the scan window. That invariant is what
  makes per-day conditional aggregation safe: an excluded ticker is excluded on *every*
  day, so excluding it cannot introduce a step.
- Consequently the cost basis must be excluded together with the value for such a ticker,
  or P&L would show a permanent phantom loss equal to its purchase cost.
- The only consumer of `GET /api/portfolio/history` is our own SPA (JWT-authed), so
  changing the response from an array to an object carries no external breakage risk.

## What We're NOT Doing

- No query optimisation or caching — 2.5 MB/1.6 s measured, no budget exceeded.
- Not valuing pre-debut days at `avg_buy_price` — rejected in the ticket: a holder who
  bought above the debut price would see a cliff on debut day, worse than the bug.
- Not skipping missing positions day-by-day — rejected in the ticket: introduces a step
  in portfolio value that reads as a gain that never happened.
- Not storing purchase dates or fixing the tranche approximation (PUL-79's accepted
  limitation: today's share counts valued at historical closes).
- Not touching the calendar, treemap, or any other consumer of `company_daily_stats`.

## Implementation Approach

One SQL statement keeps doing all the work — a second round trip would double a 1.6 s
user-facing latency. `filled` gains a `COALESCE(LOCF, BOCF)`; the gate moves from
"every ticker priced" to "at least one ticker priced"; per-ticker coverage is aggregated
into a small `STRUCT` array and cross-joined onto the daily rows, read once from row 0
in Python. `get_portfolio_history` returns a dict; the endpoint mirrors that shape; the
frontend reads `.series` and renders the `(i)` affordance from `.notes` / `.excluded`.

## Critical Implementation Details

**Note suppression.** A note is emitted only when the ticker's first real price is
*later than `start_date`* — i.e. only when the backward-fill actually affected the
requested window. At `range=1w` `S2B` has genuine prices throughout, so no note appears.
Emitting unconditionally would footnote every chart forever.

**Accessibility of the `(i)` affordance.** Hover alone is unreachable on touch and hides
the assumption from anyone who doesn't hover — the exact failure mode this change exists
to fix. The affordance must therefore respond to click and keyboard focus as well, and
carry the full text in `aria-label`.

---

## Phase 1: DB layer — backward-fill, safety-net gate, coverage metadata

### Overview

Make `get_portfolio_history` stop clamping, start reporting, and return a richer shape.

### Changes Required:

#### 1. History query

**File**: `db/bigquery.py`

**Intent**: Days before a ticker's first quote must carry its earliest known close, so a
recent IPO stops truncating the series. The `missing = 0` gate is replaced by a
per-day conditional aggregation that drops only genuinely priceless tickers.

**Contract**: `filled.px_ff` becomes the coalesce of the existing LOCF window and a new
BOCF window; `daily` aggregates conditionally and counts covered positions.

```sql
COALESCE(
  LAST_VALUE(px IGNORE NULLS) OVER (
    PARTITION BY ticker ORDER BY snapshot_date
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
  ),
  FIRST_VALUE(px IGNORE NULLS) OVER (      -- days before the debut
    PARTITION BY ticker ORDER BY snapshot_date
    ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING
  )
) AS px_ff
```

`daily` aggregates `SUM(IF(px_ff IS NOT NULL, shares * px_ff, 0))` for value and
`SUM(IF(px_ff IS NOT NULL, shares * (px_ff - avg_buy_price), 0))` for P&L — value and
cost basis must be dropped together — plus `COUNTIF(px_ff IS NOT NULL) AS covered`. The
outer filter becomes `covered > 0`.

#### 2. Coverage metadata

**File**: `db/bigquery.py`

**Intent**: The caller needs to know which holdings were valued at a debut price and
which were dropped, without paying for a second query.

**Contract**: A `coverage` CTE derives, per position ticker, `first_px_date`
(`MIN(snapshot_date)` over `px_dedup`) and `first_px` (the close on that date, via
`ARRAY_AGG(px IGNORE NULLS ORDER BY snapshot_date LIMIT 1)[SAFE_OFFSET(0)]`). A `meta`
CTE aggregates two arrays — backfilled (`first_px_date > @start_date`) and excluded
(`first_px_date IS NULL`) — and is cross-joined onto `daily`. `meta` always yields
exactly one row, so the cross join cannot drop days.

#### 3. Return shape

**File**: `db/bigquery.py`

**Intent**: Carry the metadata to the caller.

**Contract**: `get_portfolio_history(...) -> dict` with keys `series`
(`list[{snapshot_date, value_pln, pnl_pln}]`, unchanged element shape), `notes`
(`list[{ticker, listed_from: date, price: float}]`) and `excluded` (`list[str]`).
Empty result → `{"series": [], "notes": [], "excluded": []}`. The docstring's F1
paragraph is rewritten: the gate no longer clamps.

#### 4. Unit tests

**File**: `tests/test_bigquery.py`

**Intent**: Pin the new SQL shape and the new return contract; the three existing
`get_portfolio_history` tests assert the old list contract and must move to the dict.

**Contract**: Extend `test_get_portfolio_history_query_forward_fills_and_gates_coverage`
to also assert `FIRST_VALUE` presence and the absence of `missing = 0`. Add coverage for:
notes extracted from row 0, excluded tickers, empty result, and a metadata array that
comes back `NULL` from `ARRAY_AGG` over zero rows.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_bigquery.py -q`
- Full suite minus e2e stays green: `uv run pytest --ignore=tests/e2e -q`
- Lint passes: `uv run ruff check db/ tests/`

#### Manual Verification:

- Against real BigQuery, `1y` on "Główny" returns ~249 points instead of 71
- The value on the trading day before 2026-04-16 differs from the debut-day value by less than the daily move of the other holdings (no phantom step from `S2B`)
- `notes` names `S2B` with `listed_from = 2026-04-16` and its debut close

---

## Phase 2: API layer — object response

### Overview

Mirror the new dict through the endpoint.

### Changes Required:

#### 1. Response model

**File**: `src/api.py`

**Intent**: The endpoint must return the series alongside the metadata rather than a
bare array.

**Contract**: New Pydantic models beside the existing `PortfolioHistoryPoint`: a note
model (`ticker: str`, `listed_from: str` ISO date, `price: float`) and an envelope
(`series: list[PortfolioHistoryPoint]`, `notes: list[...]`, `excluded: list[str]`).
`GET /api/portfolio/history` returns the envelope. Auth, ownership check, `all` sentinel
handling, and the 401/403/422/500 paths are untouched.

#### 2. API tests

**File**: `tests/test_api.py`

**Intent**: `test_get_portfolio_history_returns_200_with_series` asserts a bare list and
must assert the envelope; the mock return values in five tests must become dicts.

**Contract**: Update `_HIST_ROWS` to the dict shape; add a test asserting notes and
excluded survive the endpoint, and one asserting an empty history still returns a
well-formed envelope rather than `null`.

### Success Criteria:

#### Automated Verification:

- API tests pass: `uv run pytest tests/test_api.py -q`
- Full suite minus e2e green: `uv run pytest --ignore=tests/e2e -q`
- Lint passes: `uv run ruff check src/ tests/`

#### Manual Verification:

- `GET /api/portfolio/history?range=1y&portfolio_id=<główny>` against real BQ returns an envelope whose `series` spans a full year and whose `notes` names `S2B`

---

## Phase 3: Frontend — `(i)` affordance on the chart

### Overview

Surface the assumption without burying it.

### Changes Required:

#### 1. Fetch and render

**File**: `static/index.html`

**Intent**: The fetch path reads a bare array today; it must read `.series` and keep the
metadata alongside the cached series so the Wartość↔Zysk/strata toggle can redraw from
cache without refetching.

**Contract**: `_fetchPortfolioHistorySlot` stores `{series, notes, excluded}` in
`_ppHistDataActive` / `_ppHistDataAll`; `_renderPortfolioHistory` takes the series for
the SVG and the metadata for the affordance. The existing out-of-order guards
(`_ppHistReqSeqActive` / `_ppHistReqSeqAll`) stay as they are.

#### 2. Affordance markup and behaviour

**File**: `static/index.html`

**Intent**: A discreet `(i)` next to the chart title, revealing which holdings were
valued at a debut price and which were dropped.

**Contract**: A focusable `<button class="pp-hist-info">` appended next to
`#pp-history-title-active` / `#pp-history-title-all`, rendered only when `notes` or
`excluded` is non-empty. Opens on click and on keyboard focus, not hover alone; carries
the full text in `aria-label`. All interpolated values go through the existing `esc()`
helper — ticker strings originate in user-entered positions. Text follows the ticket's
wording: *"S2B notowany od 16.04.2026, wcześniej wyceniony kursem debiutu 35,70 zł"*;
excluded holdings read *"<TICKER> — brak notowań, pominięty w wycenie"*.

#### 3. e2e coverage

**File**: `tests/e2e/conftest.py`, `tests/e2e/test_portfolio_value_history.py`

**Intent**: The shared fake returns the old list shape and will break every chart test;
it must return the envelope. A new test proves the affordance appears and reveals its
content on click.

**Contract**: `_fake_get_portfolio_history` returns
`{"series": _FAKE_HISTORY_ROWS, "notes": [...], "excluded": []}` with one note so the
affordance has something to show. New test: locate the button by role, click it, assert
the ticker and its listing date are visible. Locators stay `getByRole`/`getByText` per
project rules; no `waitForTimeout`.

### Success Criteria:

#### Automated Verification:

- e2e chart suite passes: `uv run pytest tests/e2e/test_portfolio_value_history.py -q`
- Full e2e suite green: `uv run pytest tests/e2e -q`
- Full suite green: `uv run pytest -q`

#### Manual Verification:

- With the app running locally against real BQ, the Kalendarz view shows a full year at `1r` and the `(i)` next to "Główny" reveals the `S2B` note
- The affordance opens with keyboard alone (Tab to it, Enter/Space) and on a touch tap
- Charts without backfilled holdings show no `(i)` at all

---

## Phase 4: Verification on real data

### Overview

Prove the fix on the data that exposed the bug, and close the ticket's acceptance criteria.

### Changes Required:

#### 1. Re-run the baseline benchmark

**File**: (scratchpad script, not committed)

**Intent**: The Current State table above is the before-picture; produce the after-picture
from the identical script so the comparison is honest.

**Contract**: Same script, same three views, same `1y` range. Record points, latency and
span. Append the result table to `change.md` under Notes.

### Success Criteria:

#### Automated Verification:

- Full suite green on the branch: `uv run pytest -q`
- Lint clean: `uv run ruff check .`

#### Manual Verification:

- "Wszystkie" and "Główny" both return ~249 points at `1y` (up from 71)
- Latency stays within ~2× the 1.6 s baseline
- The 12 universe tickers with no archive file (`AAS`, `QUANTUM`, `REXA`, …) no longer clamp any portfolio
- Deployed revision serves the new envelope and `/health` is ok

---

## Testing Strategy

### Unit Tests

- SQL shape: `FIRST_VALUE` present, `missing = 0` gone, `covered > 0` present, both price tables still unioned.
- Return contract: dict with three keys; notes/excluded lifted from row 0; `NULL` arrays coerce to `[]`.
- Conditional aggregation: an excluded ticker removes both its value and its cost basis.

### Integration Tests

- Endpoint returns the envelope for a valid range; empty history yields `{"series": [], "notes": [], "excluded": []}` not `null`.
- 401/403/422/500 paths unchanged.

### Manual Testing Steps

1. Run the benchmark script against real BQ; confirm ~249 points at `1y` for "Główny".
2. Open Kalendarz, switch to `1r`, confirm the chart spans a year and the `(i)` appears.
3. Tab to the `(i)` and open it with the keyboard; confirm the `S2B` text.
4. Switch to `1t`; confirm the `(i)` disappears (no backfill affected that window).
5. Toggle Wartość↔Zysk/strata; confirm no refetch and the affordance survives the redraw.

## Performance Considerations

Two extra window functions over the same `grid`, plus one aggregate over `px_dedup`.
The scan window is unchanged, so bytes processed stay at ~2.5 MB. The metadata array is
repeated on every returned row by the cross join; at ≤250 rows and ≤3 structs this is
negligible over the wire. Re-measured in Phase 4.

## Migration Notes

No schema change, no data migration. The API response shape changes from array to object
in the same deploy as the frontend that reads it — both live in this repo and ship
together, so there is no window where one is ahead of the other.

## References

- Linear PUL-100, GitHub #195 — diagnosis, rejected alternatives, acceptance criteria
- `db/bigquery.py:465` `get_portfolio_history`; gate documented at `:480-487`, `filled` CTE at `:537`
- PUL-79 (endpoint + the F1 LOCF decision), PUL-89 / PUL-91 (charts), PUL-92 (backfill that exposed this)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: DB layer — backward-fill, safety-net gate, coverage metadata

#### Automated

- [ ] 1.1 Unit tests pass: `uv run pytest tests/test_bigquery.py -q`
- [ ] 1.2 Full suite minus e2e stays green: `uv run pytest --ignore=tests/e2e -q`
- [ ] 1.3 Lint passes: `uv run ruff check db/ tests/`

#### Manual

- [ ] 1.4 Against real BigQuery, `1y` on "Główny" returns ~249 points instead of 71
- [ ] 1.5 No phantom step from `S2B` on 2026-04-16
- [ ] 1.6 `notes` names `S2B` with `listed_from = 2026-04-16` and its debut close

### Phase 2: API layer — object response

#### Automated

- [ ] 2.1 API tests pass: `uv run pytest tests/test_api.py -q`
- [ ] 2.2 Full suite minus e2e green: `uv run pytest --ignore=tests/e2e -q`
- [ ] 2.3 Lint passes: `uv run ruff check src/ tests/`

#### Manual

- [ ] 2.4 Endpoint against real BQ returns an envelope spanning a full year with `S2B` in `notes`

### Phase 3: Frontend — `(i)` affordance on the chart

#### Automated

- [ ] 3.1 e2e chart suite passes: `uv run pytest tests/e2e/test_portfolio_value_history.py -q`
- [ ] 3.2 Full e2e suite green: `uv run pytest tests/e2e -q`
- [ ] 3.3 Full suite green: `uv run pytest -q`

#### Manual

- [ ] 3.4 Kalendarz shows a full year at `1r` and the `(i)` reveals the `S2B` note
- [ ] 3.5 Affordance opens with keyboard alone and on a touch tap
- [ ] 3.6 Charts without backfilled holdings show no `(i)`

### Phase 4: Verification on real data

#### Automated

- [ ] 4.1 Full suite green on the branch: `uv run pytest -q`
- [ ] 4.2 Lint clean: `uv run ruff check .`

#### Manual

- [ ] 4.3 "Wszystkie" and "Główny" both return ~249 points at `1y`
- [ ] 4.4 Latency stays within ~2× the 1.6 s baseline
- [ ] 4.5 Tickers with no archive file no longer clamp any portfolio
- [ ] 4.6 Deployed revision serves the new envelope and `/health` is ok
