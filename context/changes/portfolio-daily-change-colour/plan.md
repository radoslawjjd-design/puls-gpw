# Colour the daily-change column in the portfolio table — Implementation Plan

## Overview

The **Zmiana dzienna** column in "Mój portfel" renders as plain text while every
neighbouring signed number in the same table is already tinted green or red. This
plan closes that one gap: the cell gets the same `positive` / `negative` classes
the **Zysk/strata** cell five lines below it already uses, plus an explicit `+`
sign on positive values so colour is not the only carrier of meaning.

## Current State Analysis

`_renderPortfolioTable` (`static/index.html:3818`) builds every row of `#pp-tbody`.
Within one `.map()` callback it computes both cells:

- `static/index.html:3839` — `daily` is text only, no class, no sign prefix.
- `static/index.html:3840-3845` — `pnlText` / `pnlClass` do exactly the wanted
  thing: `pos.pnl_pln > 0 ? 'positive' : pos.pnl_pln < 0 ? 'negative' : ''`, with
  `'—'` and an empty class when the value is `null`.

The two cells then land at `:3860` (uncoloured) and `:3861` (`class="${pnlClass}"`).

The treemap already tints by the same field (`static/index.html:5812`) and already
prefixes positives with `+` (`:5826`), so both conventions exist in the file —
the table is the only place that opted out.

### Key Discoveries

- **The mobile card layout needs no separate work.** `@media (max-width: 640px)`
  at `static/index.html:800-825` turns the same `<td>` elements into cards via
  `display: block` and a `::before` fed by `data-label`. It never sets `color` on
  the value cells — only on the `::before` label (`:818`). So `td.positive` /
  `td.negative` (`:782-783`) apply at every viewport. The ticket's "check the same
  treatment is wanted in the mobile card layout" resolves to: it comes for free,
  and the muted label keeps its own colour.
- **Dark mode comes for free too.** `html[data-theme="dark"] td.positive` /
  `td.negative` (`:1187-1190`) already override to the lighter `#5ec98f` / `#f08a82`
  pair used everywhere else.
- **Sorting is unaffected.** The `data-key="daily_change_pct"` header sorts
  `_ppPositions` — the data array — via `_sortRows` (`static/index.html:4684`),
  never DOM text. A `+` prefix cannot disturb it.
- **CSV export is unaffected.** `_csvNum(p.daily_change_pct)` (`:5916`) reads the
  raw number, not the rendered cell.
- **Fixture gap.** `_FAKE_PORTFOLIO_POSITIONS` (`tests/e2e/conftest.py:314`) carries
  only a positive (`PKO`, `1.5`) and a `None` (`CDR`) case. Negative and exact-zero
  have no fixture, and both are states this change must distinguish.
- **No test asserts a row count or summary total on `#pp-tbody`**, so extending the
  fixture is safe (checked across `test_portfolio_positions.py`,
  `test_portfolio_wallets.py`, `test_portfolio_mobile_layout.py`,
  `test_csv_export.py`, `test_etf_portfolio.py`).

## Desired End State

In "Mój portfel" → table view, the **Zmiana dzienna** cell reads:

| `daily_change_pct` | text      | class      |
| ------------------ | --------- | ---------- |
| `> 0`              | `+1.50%`  | `positive` |
| `< 0`              | `-0.80%`  | `negative` |
| `0`                | `0.00%`   | *(none)*   |
| `null`             | `—`       | *(none)*   |

…on desktop and on a phone, in light and dark themes.

## What We're NOT Doing

- **PUL-123 part 2 (holding period)** — blocked on PUL-114, which owns the FIFO lot
  ledger and decides how `first_buy_date` / `days_held` and their absence are
  represented. No lot ledger is built here.
- No change to the treemap, the calendar, Dywidendy, Zrealizowane, or the summary
  strip — all already colour their own numbers.
- No change to the CSV export, to sorting, or to the API payload.
- No new CSS. `td.positive` / `td.negative` and their dark-mode overrides exist.

## Implementation Approach

Mirror the `pnlText` / `pnlClass` pair that sits five lines below, then apply the
class to the `<td>` at `:3860` the same way `:3861` does. One `let` block replaces
one `const`, and one attribute is added to one cell.

The `+` prefix is the ticket's "colour must not be the only signal" requirement:
negatives already carry `-` from `toFixed`, so only positives need the explicit
sign to make the direction readable without colour.

---

## Phase 1: Colour and sign the daily-change cell

### Overview

Replace the single-expression `daily` const with a text/class pair, and apply the
class to the cell.

### Changes Required:

#### 1. Row rendering

**File**: `static/index.html`

**Intent**: Compute a `dailyClass` alongside the existing `daily` text, using the
same three-way comparison the `pnl` block below already uses, and prefix positive
values with `+`. `null` must keep rendering `—` with no class — it is a different
state from zero, which renders `0.00%` with no class.

**Contract**: `_renderPortfolioTable` (`:3818`) gains a `dailyClass` binding
alongside `daily` (`:3839`); the `<td data-label="Zmiana dzienna">` at `:3860`
gains `class="${dailyClass}"`, matching the shape of `:3861`.

#### 2. E2E fixture — negative and zero cases

**File**: `tests/e2e/conftest.py`

**Intent**: `_FAKE_PORTFOLIO_POSITIONS` (`:314`) has no negative and no exact-zero
daily change, so neither the red path nor the "zero is not a loss" boundary can be
asserted. Add two positions covering them.

**Contract**: two entries appended to `_FAKE_PORTFOLIO_POSITIONS` with distinct
tickers, one with a negative `daily_change_pct` and one with `0.0`. Both need
`current_price` set so they are not confused with the `CDR` no-price row that
`test_positions_show_dashes_when_no_price_data` relies on.

#### 3. E2E test

**File**: `tests/e2e/test_portfolio_positions.py`

**Intent**: Assert the four states render distinctly. Follow the luminance pattern
already established in `test_portfolio_mobile_layout.py:138-163` rather than
hardcoding hex values — the claim is "up and down are visibly different colours,
and flat is neither", which must survive a palette change. Assert in both themes
and at a phone viewport, since the mobile card path is the one the ticket asked to
check.

**Contract**: new test(s) in `tests/e2e/test_portfolio_positions.py` reading the
computed `color` of `td[data-label="Zmiana dzienna"]` per row, plus the rendered
text for the sign/`—`/`0.00%` distinction.

### Success Criteria:

#### Automated Verification:

- New e2e test fails before the `static/index.html` change and passes after it
- Full suite green: `uv run pytest --tb=short`
- Linting passes: `uv run ruff check`

#### Manual Verification:

- In "Mój portfel" a position up on the day is green with a `+`, one down is red, a
  flat one is neutral, and one without price data still shows a neutral `—`
- The same holds at a phone width, where rows become cards, and in dark mode
- Sorting by **Zmiana dzienna** still orders numerically, `+` prefix notwithstanding

---

## Testing Strategy

### E2E Tests (Playwright, `tests/e2e/`):

- Positive row: cell text starts with `+`, computed colour differs from the flat row's
- Negative row: computed colour differs from both the positive and the flat row
- Zero row: text is `0.00%`, colour matches the table's default text colour
- `null` row: text is `—`, colour matches the default — zero and unknown must not
  collapse into the same rendering
- Phone viewport: the same holds once rows are cards

### Manual Testing Steps:

1. Log in, open **Mój portfel** → **Tabela**
2. Confirm the four states above against the seeded wallet
3. Toggle dark mode; confirm all four remain legible
4. Narrow to ~360 px; confirm the card layout keeps the colours and the muted labels
5. Click the **Zmiana dzienna** header twice; confirm ordering is numeric both ways

## References

- Ticket: Linear PUL-123 (part 1 only)
- Sibling implementation to mirror: `static/index.html:3840-3845`, applied at `:3861`
- Existing colour conventions: `static/index.html:782-783`, `:1187-1190`, `:5812`, `:5826`
- Luminance-assertion pattern: `tests/e2e/test_portfolio_mobile_layout.py:138-163`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands.

### Phase 1: Colour and sign the daily-change cell

#### Automated

- [ ] 1.1 New e2e test fails before the change and passes after it
- [ ] 1.2 Full suite green: `uv run pytest --tb=short`
- [ ] 1.3 Linting passes: `uv run ruff check`

#### Manual

- [ ] 1.4 Four states render correctly in "Mój portfel"
- [ ] 1.5 Holds at phone width and in dark mode
- [ ] 1.6 Sorting by Zmiana dzienna still orders numerically
