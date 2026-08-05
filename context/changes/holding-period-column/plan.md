# Holding Period Column Implementation Plan

## Overview

Render the holding period PUL-114 already computes: in **Mój portfel** from
`first_buy_date` (the oldest still-open FIFO lot), and in **Zrealizowane** from
`days_held_weighted`, with `days_held_max` in the cell's title.

## Current State Analysis

Both numbers are already on the wire — this change adds no arithmetic and no request.

- `static/index.html:4570` — the positions `<thead>`, nine columns, `th-sortable` with a
  `data-key` per sortable column
- `static/index.html:3904-3916` — the positions row renderer; every `<td>` carries a
  `data-label` that drives the mobile card layout (PUL-108)
- `static/index.html:5489-5499` — the Zrealizowane table, seven columns, same
  `data-label` convention
- `static/index.html:6008` — the positions CSV export's fixed column list
- `static/index.html:2496` — `_sortRows`, which needs no change (see below)

## Desired End State

A position bought 424 days ago reads `1 rok 2 mies.`; one bought 15 days ago reads
`15 dni`; a hand-entered one reads `—`. A realized sale reads its volume-weighted
holding period, with the oldest consumed lot available on hover. The column sorts by
real date, and the CSV carries it.

### Key Discoveries

- **`_sortRows` needs no change.** ISO date strings compare lexicographically in
  chronological order, and `null` is already forced to the end in both directions
  (`static/index.html:2499-2501`). A cell that renders in two different formats would
  otherwise sort by its text, which is meaningless.
- **No new CSS.** Part 1 established that the mobile cards restyle the same `<td>`, so a
  new column appears at every viewport for free.
- Polish plurals are not optional here: `1 dzień` / `2 dni` / `5 dni`, and
  `1 rok` / `2 lata` / `5 lat`. Months use the invariant abbreviation `mies.`, which
  sidesteps the problem entirely.

## What We're NOT Doing

- **No lot arithmetic.** PUL-114 owns the ledger; this change reads two fields.
- **Not touching the treemap popup's "Zakup" line** — that is a return figure, not a
  holding period.
- **Not adding a holding period to the dividends tab** — dividends are not held.

## Implementation Approach

One formatting helper, used by both views, plus three render sites and the CSV.
The helper is the only thing with logic in it, so it is where the tests point.

## Phase 1: The formatter and the two views

### Overview

Single phase — the change is one helper and four call sites, and splitting it would put
a half-rendered column in front of the manual gate.

### Changes Required

#### 1. The formatting helper

**File**: `static/index.html`

**Intent**: Turn an ISO acquisition date, or a day count, into the agreed text. One
function so the two views cannot drift apart.

**Contract**: Two small functions next to the existing formatting helpers —
`_holdingText(days)` returning `null` for `null`/negative input, `N dni` below 90, else
years and months (`1 rok 2 mies.`, `3 mies.`, `2 lata`); and `_daysSince(isoDate)`
returning whole days or `null`. Polish plural rules for `dzień` and `rok`; `mies.` is
invariant.

**Correction to this plan, made while implementing.** It originally required months to
be derived from the date parts rather than from a day count. That is impossible for
Zrealizowane: PUL-114 supplies `days_held_weighted` as a *number of days* — a volume
weighted average across several lots — and there is no single date to take parts from.
Insisting on it would have forced the two views apart, so a position and a sale of the
same age would read differently. `_holdingText` therefore takes days in both views and
converts with the mean month length (365.25/12). The boundary error is under a day on a
figure whose unit is months.

#### 2. Positions table

**File**: `static/index.html` (`pp-thead` ~`:4570`, row renderer ~`:3904`)

**Intent**: A tenth column, sortable, after `Śr. cena zakupu` — the purchase facts stay
together before the market ones.

**Contract**: `<th class="th-sortable" data-key="first_buy_date">Okres posiadania</th>`
and a matching `<td data-label="Okres posiadania">`, rendering the helper's output or
the existing neutral `—` when `first_buy_date` is absent. Sorting is by `data-key` alone
— `_sortRows` handles the rest.

#### 3. Zrealizowane table

**File**: `static/index.html` (~`:5489`)

**Intent**: An eighth column showing the weighted holding period, with the oldest
consumed lot on hover.

**Contract**: `<th>Okres posiadania</th>` after `Sprzedano`, and a
`<td data-label="Okres posiadania" title="Najstarszy lot: …">`. The title is omitted
when `days_held_max` is absent or equals the weighted figure, so it appears only when it
says something the cell does not.

#### 4. CSV export

**File**: `static/index.html` (~`:6008`)

**Intent**: Keep the export a faithful copy of the table.

**Contract**: `Okres posiadania` added to the header list and the row builder, in the
same position as in the table.

#### 5. E2E coverage

**File**: `tests/e2e/test_portfolio_positions.py`, `tests/e2e/test_portfolio_realized.py`,
`tests/e2e/conftest.py`

**Intent**: Pin what a reader sees, including the case the ticket warns about.

**Contract**: The conftest fake gains `first_buy_date` on some positions and leaves it
absent on at least one, and `days_held_weighted` / `days_held_max` on the realized
rows. Tests assert: a long-held position renders years and months, a recent one renders
days, a position with no acquisition date renders `—` and **not** `0 dni`, and the
Zrealizowane cell carries the oldest lot in its title. Fixtures are dated relative to
today rather than pinned, so the suite does not rot — `tests/e2e/_dates.py` is the
existing home for that.

### Success Criteria

#### Automated Verification

- New e2e tests pass: `uv run pytest tests/e2e/test_portfolio_positions.py tests/e2e/test_portfolio_realized.py -q`
- Full suite green: `uv run pytest -q`
- Lint clean: `uv run ruff check .`

#### Manual Verification

- A long-held position (CBF, 424 days) reads `1 rok 2 mies.` in the browser
- A hand-entered position reads `—`, not `0 dni`
- The column sorts chronologically, and unsorted rows are unaffected
- The mobile card layout shows the new row with its label, at 375 px width
- The CSV export opens with the column in the right place

## Testing Strategy

The formatter carries all the logic and has no JS unit-test harness in this repo, so it
is exercised through the Playwright e2e suite — the same route PUL-123 part 1 took. The
cases that matter are the two format branches, the absent value, and the plural
boundaries.

## References

- Ticket: PUL-123 part 2 · data from PUL-114 (`fifo-basis-on-read-paths`)
- Part 1: `context/archive/portfolio-daily-change-colour/`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands.

### Phase 1: The formatter and the two views

#### Automated

- [x] 1.1 New e2e tests pass — 7cc9163
- [x] 1.2 Full suite green (1114) — 7cc9163
- [x] 1.3 Lint clean — 7cc9163

#### Manual

- [x] 1.4 A long-held position reads years and months in the browser (automated —
      test_holding_period_switches_units_instead_of_printing_raw_days asserts the
      rendered text in Chromium)
- [x] 1.5 A hand-entered position reads an em dash, not 0 dni (automated —
      test_a_position_with_no_acquisition_date_shows_no_holding_period, which also
      asserts the cell contains no digit at all)
- [x] 1.6 The column sorts chronologically (automated —
      test_holding_period_sorts_by_date_not_by_the_text_it_prints, both directions)
- [x] 1.7 The mobile card layout shows the new row at 375 px (automated — the card
      layout labels cells *positionally*, so an inserted column either inherits the
      mechanism or silently loses its label; part 1 assumed this rather than checking)
- [x] 1.8 The CSV export carries the column in the right place (automated — position
      asserted relative to its neighbour, since anything reading by index lands one
      field off otherwise)
