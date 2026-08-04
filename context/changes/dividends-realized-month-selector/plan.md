# Month selector for Dywidendy and Zrealizowane — Implementation Plan

## Overview

Both views expose one period selector: `Wszystkie` plus a year. Add a month
selector beside it, independent of the year, so all four combinations work —
including `Wszystkie` year + a single month ("every March on record"). Along the
way, close the UTC/Warsaw attribution defect that month granularity would
otherwise expose twelve times as often, on **both** sides.

## Current State Analysis

See `research.md` for the full map. The three things that shape this plan:

1. **The two views filter in different places** — dividends in SQL
   (`db/bigquery.py:3917`), realized in Python (`src/portfolio_realized.py:93`).
   There is no shared filter to extend; each gets the month at its own site.
2. **The realized filter must stay after the FIFO walk.** Lots are consumed for
   every sale, in or out of period (`src/portfolio_realized.py:78-94`). Moving the
   filter earlier makes every later sale report proceeds at zero cost.
3. **Period attribution runs in UTC on both sides** and must run in
   `Europe/Warsaw`. Verified against production data: the stored instants are true
   UTC, and today zero rows would move — this is a latent defect being closed
   before it becomes visible, not a live wrong number.

## Desired End State

Two selectors above each of the two tables. Every combination returns the right
totals; an empty month renders the empty state with both selectors still usable;
a month request is never served from another month's cache entry; an out-of-range
month is rejected with 422 and leaves no cache entry behind.

## What We're NOT Doing

- The **calendar**, which has its own month navigation.
- Any change to the `data` CTE's `GROUP BY` or the meta-first join direction —
  both are load-bearing and stay exactly as they are.
- Widening the year validation. `year` currently accepts any digit string
  (`"5"` parses to 5); tightening it is a separate concern and changing it here
  would mix an unrelated behaviour change into this diff.
- Any change to the treemap, positions table, or value history.

## Implementation Approach

Thread a `month` parameter alongside the existing `year` at every layer, adding it
at the same site the year filter already occupies. The frontend gains a second
selector rendered by a new sibling of `_renderYearSelect`, with its own delegated
handler and its own state variable, mirroring the year's shape exactly.

The timezone correction is applied at the same two sites the month filter lands
at, so period attribution is defined once per side rather than twice.

## Critical Implementation Details

**Naive datetimes must be treated as UTC, not as system-local.** The only
production caller of `compute_realized_pnl` passes tz-aware UTC rows, but the test
corpus passes naive ones. `astimezone` on a naive datetime assumes the runner's
local timezone, which would make the suite's results depend on where it runs.
Attach UTC explicitly before converting.

---

## Phase 1: Period attribution moves to Europe/Warsaw

### Overview

Correct the timezone on both sides first, alone, so the change is reviewable
without the month feature layered on top. No user-visible behaviour changes today
(verified: zero rows shift).

### Changes Required:

#### 1. Dividend summary SQL

**File**: `db/bigquery.py`

**Intent**: `EXTRACT(YEAR FROM occurred_at)` (`:3894`) reads a UTC year from a
timestamp that records a Warsaw-time event. Extract in `Europe/Warsaw` so a
payout credited just after midnight Warsaw time is attributed to the period the
user actually saw it in.

**Contract**: the `scoped` CTE's `year` column derives from
`occurred_at AT TIME ZONE 'Europe/Warsaw'`. `meta`'s `all_years` follows
automatically since it aggregates that same column. Docstring gains the reason.

#### 2. Realized FIFO period attribution

**File**: `src/portfolio_realized.py`

**Intent**: `sold_year = op["occurred_at"].year` (`:91`) has the same defect. Derive
the sale's period from the Warsaw-local view of the instant, treating a naive
input as UTC so the test corpus does not depend on the runner's timezone.

**Contract**: a module-level helper converting an `occurred_at` to its Warsaw
local `datetime`; `sold_year` and (in Phase 2) `sold_month` read from it. Uses
`zoneinfo.ZoneInfo` — stdlib, no new dependency.

### Success Criteria:

#### Automated Verification:

- New unit test: a sale at 23:30 UTC on 31 December lands in the following year
- Existing realized tests still pass unchanged (naive input keeps its wall clock)
- Full suite green: `uv run pytest --tb=short`
- Linting passes: `uv run ruff check`

---

## Phase 2: Month reaches the data layer

### Overview

Both filters accept a month. Nothing calls with one yet.

### Changes Required:

#### 1. Dividend summary

**File**: `db/bigquery.py`

**Intent**: `get_dividend_summary` gains a `month` parameter filtering beside the
existing year predicate. The `data` CTE's `GROUP BY ticker` and the meta-first
join stay untouched — the month is one more `WHERE` term, not a grouping key.

**Contract**: `get_dividend_summary(user_id, portfolio_id=None, year=None,
month=None)`; the `scoped` CTE exposes a Warsaw `month` alongside `year`; `data`'s
predicate becomes `(@year IS NULL OR year = @year) AND (@month IS NULL OR month =
@month)`; a third `INT64` query parameter is bound.

#### 2. Realized FIFO

**File**: `src/portfolio_realized.py`

**Intent**: `compute_realized_pnl` gains a `month` parameter applied at the same
point as the year — after the lots are consumed, never before.

**Contract**: `compute_realized_pnl(operations, year=None, month=None)`; the
existing `if year is not None and sold_year != year: continue` (`:93`) gains the
month term. Docstring extends the "narrows the result, never the input" paragraph
to cover it.

### Success Criteria:

#### Automated Verification:

- Unit test: filtering to a single month gives the same per-ticker numbers as
  filtering to that month's year and summing that month's sales — proving FIFO
  still walked the full history
- Unit test: `month` with `year=None` matches that month across every year
- Unit test: a sale whose lots were bought outside the month still reports the
  real cost basis, not zero
- Full suite green, linting passes

---

## Phase 3: Endpoints accept and cache the month

### Overview

Both endpoints validate the month, pass it down, and key their cache on it.

### Changes Required:

#### 1. Both endpoints

**File**: `src/api.py`

**Intent**: `/api/portfolio/dividends` (`:1413`) and `/api/portfolio/realized`
(`:1454`) gain a `month` query parameter, validated **before** the cache key is
built — the existing comment at `:1420-1421` explains why, and an unvalidated
month would carve out an entry per arbitrary string exactly as an unvalidated year
would. The month must join the cache key: without it a March request is served
from a January entry for 300 s, which is a silent wrong answer rather than a
visible failure.

**Contract**: `month: str | None = Query(None)`; `isdigit()` plus a 1–12 range
check, 422 otherwise; cache keys become
`dividends:{user}:{portfolio}:{year or 'all'}:{month or 'all'}` and the realized
equivalent. The invalidation prefix scan (`:131`) is unchanged — it matches on
`dividends:{user}:` and keeps working with a longer key.

### Success Criteria:

#### Automated Verification:

- Unit test: `month=abc` and `month=13` both return 422 and leave no cache entry
- Unit test: two requests differing only in month return different payloads
- Unit test: `_perf_invalidate_portfolio` still clears the longer keys
- Full suite green, linting passes

---

## Phase 4: The month selector

### Overview

A second selector beside each year selector, independent of it.

### Changes Required:

#### 1. Selector rendering

**File**: `static/index.html`

**Intent**: A sibling of `_renderYearSelect` (`:5361`) rendering all twelve months
plus `Wszystkie`. All twelve always, for the reason the year list already spans
quiet years — an empty month is an answer, not a missing option. Reuses
`_PP_MONTHS_PL` (`:5213`).

**Contract**: `_renderMonthSelect(boxId, selected, label)`; emits the same
`<select class="pp-year-select" aria-label=…>` shape so it inherits the existing
styling and the delegated-handler pattern.

#### 2. Containers, state and handlers

**File**: `static/index.html`

**Intent**: Each header row (`:4623-4628`, `:4633-4636`) gains a container for the
month selector. Two new state variables mirror `_ppDivYear` / `_ppRealYear`, and
two new delegated handlers mirror those at `:4749` and `:4758` — delegated for the
same reason, since the `<select>` is rebuilt on every response.

**Contract**: `#pp-div-months` / `#pp-real-months` containers; `_ppDivMonth` /
`_ppRealMonth` state; handlers that refetch on change; both renderers call
`_renderMonthSelect` where they already call `_renderYearSelect`.

#### 3. Requests

**File**: `static/index.html`

**Intent**: Both fetches append `&month=` when a month is chosen, mirroring the
existing `&year=` at `:5441` and `:5469`.

**Contract**: `if (_ppRealMonth !== null) url += \`&month=${…}\`` and the dividends
equivalent.

### Success Criteria:

#### Automated Verification:

- E2E: choosing a month refetches without leaving the tab, and the request carries
  the month
- E2E: a month with no data renders the empty state and **both** selectors stay
  usable — the user can leave an empty period
- E2E: year `Wszystkie` + a month issues a request with month and no year
- Full suite green, linting passes

#### Manual Verification:

- All four year/month combinations return correct totals on the real imported
  history
- The two selectors sit sensibly on a phone, not overflowing the header row

**Implementation Note**: the e2e fake `_fake_get_dividend_summary`
(`tests/e2e/conftest.py:518`) ignores its `year` argument today. It must honour
both `year` and `month` for the dividends e2e assertions to mean anything.

---

## Testing Strategy

### Unit Tests

- `tests/test_portfolio_realized.py` — month filtering, the FIFO-integrity
  equivalence, the Warsaw boundary
- `tests/test_api.py` — month validation, cache-key separation, invalidation

### E2E Tests

- `tests/e2e/test_portfolio_dividends.py`, `tests/e2e/test_portfolio_realized.py` —
  selector rendering, refetch on change, empty-month escape hatch

### Manual Testing Steps

1. Open **Dywidendy**, pick a year and a month with known payouts; check the totals
2. Set year to `Wszystkie`, keep the month; confirm it sums that month across years
3. Pick a month with no payouts; confirm the empty state and that both selectors
   still respond
4. Repeat 1–3 on **Zrealizowane**
5. Narrow to ~360 px; confirm the header row still fits

## References

- Research: `context/changes/dividends-realized-month-selector/research.md`
- Ticket: Linear PUL-120, GitHub #239
- FIFO-filter rationale: `src/portfolio_realized.py:39-42`, `:78-80`
- Meta-first join rationale: `db/bigquery.py:3884-3887`
- Cache-key-before-validation rationale: `src/api.py:1420-1421`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands.

### Phase 1: Period attribution moves to Europe/Warsaw

#### Automated

- [x] 1.1 Sale at 23:30 UTC on 31 December lands in the following year
- [x] 1.2 Existing realized tests pass unchanged
- [x] 1.3 Full suite green
- [x] 1.4 Linting passes

### Phase 2: Month reaches the data layer

#### Automated

- [x] 2.1 Month filter equals year-filter-then-pick-that-month (FIFO integrity)
- [x] 2.2 Month with no year matches that month across every year
- [x] 2.3 Lots bought outside the month still price the sale
- [x] 2.4 Full suite green, linting passes

### Phase 3: Endpoints accept and cache the month

#### Automated

- [x] 3.1 `month=abc` and `month=13` return 422 and leave no cache entry
- [x] 3.2 Two requests differing only in month return different payloads
- [x] 3.3 Invalidation still clears the longer keys
- [x] 3.4 Full suite green, linting passes

### Phase 4: The month selector

#### Automated

- [ ] 4.1 Choosing a month refetches in place and the request carries it
- [ ] 4.2 An empty month keeps both selectors usable
- [ ] 4.3 Year `Wszystkie` + a month issues month without year
- [ ] 4.4 Full suite green, linting passes

#### Manual

- [ ] 4.5 All four combinations correct on the real imported history
- [ ] 4.6 Header row survives a phone width
