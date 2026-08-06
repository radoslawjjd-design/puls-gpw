# Dated FIFO Lot Ledger Implementation Plan

## Overview

Build `src/portfolio_lots.py` — one FIFO lot ledger whose lots carry an acquisition
timestamp — and rebuild the codebase's two existing undated lot consumptions on top of
it. Part 1 of PUL-114. Part 2 (`fifo-basis-on-read-paths`) swaps the read paths onto
this engine; nothing here touches SQL, the history curve, or the positions endpoint.

The one user-visible delta is additive: the realized endpoint gains two days-held
fields per ticker.

## Current State Analysis

Three lot consumptions exist. The ticket names two of them, and misidentifies which is
broken (see `research.md` for the measurements).

| # | location | lot shape | produces |
| -- | -- | -- | -- |
| 1 | `src/brokers/xtb.py:194-245` `reconstruct_positions` | `_Lot(shares, unit_price)` | `avg_buy_price` at import — **already FIFO-exact** |
| 2 | `src/portfolio_realized.py:81-135` `compute_realized_pnl` | `list[float]` `[volume, price]` | realized P&L per ticker |
| 3 | `db/bigquery.py:873-887` `ops_basis` CTE | none (SQL average) | the only genuine time-blind average |

#1 and #2 are what this change collapses. #3 is deleted in part 2.

Measured on production (43 positions, 508 operation rows): replaying FIFO reproduces
every stored `avg_buy_price` to `+0.00` on both wallets. The stored basis is correct
*today* and wrong *backwards*; the backward gap reaches `+2 144,85 PLN (+8,41%)`. None
of that is fixed here — this change only makes the correct engine exist in one place,
dated.

### Key Discoveries

- `compute_realized_pnl`'s return value is serialised to the client **raw**, with no
  response model (`src/api.py:1543-1548`). Any new key reaches the payload.
- `_sort_key` (`src/portfolio_realized.py:42-51`) breaks same-instant ties buy-before-sell;
  `xtb.py:216` does not. **Measured: 0 tied buy/sell instants across 508 production rows**,
  so adopting the stronger rule cannot change what the import reconstructs.
- `xtb.py:217` normalizes `.PL` inside the consumption loop. Rows in
  `user_broker_operations` are already normalized at write (`src/api.py:486`), so
  normalization must stay in the XTB adapter, never in the ledger.
- `xtb.py:221` `names.setdefault(...)` locks in `None`; `portfolio_realized.py:98-99`
  keeps looking. Adopting first-non-`None` is a deliberate, recorded import fix.
- `xtb._consume_oldest` (`:298-309`) floors oversell **silently**;
  `portfolio_realized.py:124-125` records it in `unmatched_tickers`. The ledger must
  report the uncovered quantity so part 2 can price residual shares at the stored
  `avg_buy_price` rather than at zero.
- Period filtering in `compute_realized_pnl` happens **after** consumption and only for
  in-period sells — including the `unmatched` bookkeeping (`:118-125`). That ordering is
  load-bearing and must survive the refactor byte-for-byte.
- Baseline to hold green: **51 tests** across `tests/test_portfolio_realized.py` and
  `tests/test_brokers_xtb.py`, plus `tests/e2e/test_portfolio_realized.py`.

## Desired End State

`src/portfolio_lots.py` is the only place in the codebase where a lot is opened or
consumed. It is pure (no BigQuery, no FastAPI, no I/O), its lots carry `occurred_at`,
and it reports what it could not cover instead of silently flooring.

`compute_realized_pnl` and `reconstruct_positions` both delegate to it and return the
same numbers as before — proven by the existing 51 tests passing without a single
assertion edited. `compute_realized_pnl` additionally returns `days_held_weighted` and
`days_held_max` per ticker, `None` where no matched lot exists.

Verify: `uv run pytest --tb=short` green, and `git grep -n "_consume_oldest\|lots\[ticker\].append"`
returns nothing outside `src/portfolio_lots.py`.

## What We're NOT Doing

- **Not touching `db/bigquery.py`.** `ops_basis`, `COALESCE(p.avg_buy_price,
  b.avg_op_price)` and `get_portfolio_history` are part 2.
- **Not touching the positions read path or `PortfolioPositionOut`.** `first_buy_date`
  is part 2 — it comes from a read path this change does not enter.
- **Not building the as-of-date query.** The ledger will need a
  `basis_as_of(date)` / step-function snapshot accessor for part 2's `UNNEST` feed.
  It is deliberately deferred to where it has a consumer and a real test; the ledger
  already walks events in order, so part 2 adds an accumulator rather than a
  restructure.
- **Not changing the treemap, the calendar, or `avg_buy_price`'s write path.**
- **Not switching float to Decimal.** Every number must stay identical; a precision
  change would move them.
- **Not chasing the −4 482 PLN.** Measured as not coming from either basis source;
  raised as an open question against part 2 (`research.md`, Open Questions 1).

## Implementation Approach

Strangler-style, three phases, each independently green.

Phase 1 builds the ledger with no callers — it is pure logic, so it is fully
test-drivable up front and cannot break anything. Phases 2 and 3 then swap one consumer
each, in order of blast radius: the realized path (read-only, cached, additive change)
before the import path (writes `avg_buy_price` to BigQuery).

The ledger takes a normalized internal event shape; each consumer keeps a thin adapter
that maps its own input (plain `dict`s from BigQuery, `Operation` dataclasses from the
parser) onto it. That is what lets the two consumers keep their different input
contracts while sharing one engine.

## Critical Implementation Details

**Period filtering must stay downstream of consumption.** `compute_realized_pnl` walks
every trade, then drops out-of-period sales — filtering the input first would strip the
buys that priced the remaining shares and every later sale would report a phantom
profit at zero cost (`src/portfolio_realized.py:63-67`). In the rebuilt version the
ledger produces *all* matches and the caller filters them; the ledger itself must have
no concept of a reporting period.

**`unmatched_tickers` is recorded only for in-period sells.** Today the `unmatched.add`
at `:124-125` sits *after* the two `continue` statements. A rebuild that hoists it to
the ledger would start reporting tickers whose only oversold sale falls outside the
selected year, changing the disclosure the UI shows.

**Aggregate the money figures from sells, never from matches.** `shares_sold`,
`proceeds` and `sales` are counted per *sale* today (`src/portfolio_realized.py:132-135`),
and `all_years` per *sale* too (`:119`) — `cost` is the only figure drawn from matched
lots. A rebuild that folds the ledger's match list into the per-ticker totals silently
drops the uncovered part of every partially-matched sale: proceeds and P&L come out low,
and a sale spanning three lots counts as three sales instead of one. The existing suite
does **not** catch this — `tests/test_portfolio_realized.py:89-97` checks `cost` and
`pnl_pct` for an unmatched sale but never `shares_sold` or `proceeds`, and no test
covers a *partially* covered sale at all. Phase 2 adds that test before the refactor.

**Days held must be absent, not zero, when there is no lot.** Shares sold with no
matching buy carry no acquisition date. They already contribute proceeds at zero cost;
they must contribute nothing at all to either days-held figure, and a ticker whose
in-period sales matched no lot must report `None` for both.

## Phase 1: The dated lot ledger

### Overview

A new pure module with no callers yet. Test-driven from scratch.

### Changes Required:

#### 1. The ledger module

**File**: `src/portfolio_lots.py` (new)

**Intent**: Own every lot open and every lot consumption in the codebase, with lots
that carry their acquisition instant, and report the quantity a sell could not cover
instead of discarding it.

**Contract**: A normalized input event carrying `ticker`, `op_type` (`buy`/`sell`),
`occurred_at`, `volume`, `unit_price`, `instrument_name`. Building a ledger over a
sequence of events returns, per ticker: the still-open lots (each with `volume`,
`price`, `occurred_at`), the sell→lot matches (each with the acquisition instant, the
sale instant, the volume and the cost drawn from that lot), the total uncovered volume,
and the instrument name resolved as **first non-`None`**.

Deliberately *not* included: a `first_acquired_at` convenience accessor. It has no
consumer in this change — the positions read path is part 2 — and it is one `min()` over
the open lots when part 2 needs it. Adding it here for the same reason `basis_as_of` was
deferred would be inconsistent.

Events are ordered `(occurred_at, buy-before-sell)` — the tiebreak from
`src/portfolio_realized.py:42-51`, adopted wholesale. Tickers are taken as given; the
module must not normalize them. Ticker iteration order is first-appearance in
chronological order, which is what `reconstruct_positions` relies on today
(`xtb.py:211,230`). `_DUST = 1e-9` moves here and becomes the single definition.

#### 2. Ledger unit tests

**File**: `tests/test_portfolio_lots.py` (new)

**Intent**: Pin the ledger's behaviour independently of either consumer, including the
cases the two existing implementations disagreed on.

**Contract**: Cover — FIFO order (oldest consumed first); a partial sale splitting one
lot; a sale spanning several lots producing several matches; oversell reporting a
positive uncovered volume with lots floored at zero, never negative; a ticker sold to
zero and re-bought not reusing consumed lots; the buy-before-sell tiebreak at an
identical instant; first-non-`None` instrument name; dust-sized residue not surviving
as an open lot; and `occurred_at` preserved on every lot and match.

#### 3. Capture the pre-refactor baseline

**File**: scratchpad — **not committed** (see `baseline-report.md`)

**Intent**: Phases 2 and 3 have to prove "the same numbers as before". Once the old
code is gone there is nothing left to compare against, so the comparison has to be
captured now, while `master`'s behaviour is still running.

**Contract**: Two JSON artefacts committed in this phase, both generated over the real
508-row production operation history — `realized.json` (`compute_realized_pnl`
unfiltered, year-filtered, and month-filtered) and `positions.json`
(`reconstruct_positions` per portfolio, positions and closed tickers). Generated by a
short throwaway script run against the current code.
They are what criteria 2.6 and 3.6 diff against.

**They were originally committed here, and are not any more.** This repository is
public and the artefacts carry the owner's real holdings, share counts and purchase
prices. The `ai-security-review` gate flagged it on PR #249 and the history was
rewritten to remove them. What a future reader needs is the comparison, and that is
in `baseline-report.md`.

Production data, not a test fixture: `tests/test_brokers_xtb.py:307` records that no
export file is committed to the repo — every fixture there is synthesized inline — so
there is no stored export to baseline against, and the real history is the stronger
comparison anyway.

### Success Criteria:

#### Automated Verification:

- New ledger tests pass: `uv run pytest tests/test_portfolio_lots.py --tb=short`
- Nothing else regressed: `uv run pytest --tb=short`
- Linting passes: `uv run ruff check .`
- The module imports nothing outside the standard library — `uv run python -c "import ast;t=ast.parse(open('src/portfolio_lots.py').read());mods={(n.module or '') if isinstance(n,ast.ImportFrom) else n.names[0].name for n in ast.walk(t) if isinstance(n,(ast.Import,ast.ImportFrom))};assert not {m for m in mods if m.split('.')[0] in {'db','src','google','fastapi','pydantic'}}, mods"`
- Baseline artefacts captured and non-empty (scratchpad, not committed)

#### Manual Verification:

- The ledger's public shape reads as something part 2 can hang an as-of-date query on without restructuring

---

## Phase 2: Realized P&L on the ledger, plus days held

### Overview

Swap `compute_realized_pnl`'s hand-rolled consumption for the ledger and derive the two
days-held figures decided with the user. Every existing assertion must hold unchanged.

### Changes Required:

#### 1. Rebuild the realized computation

**File**: `src/portfolio_realized.py`

**Intent**: Delete the local lot list and consumption loop; build a ledger from the
input rows, then aggregate its matches into the existing per-ticker totals.

**Contract**: The returned dict keeps every existing key with identical values —
`total_pnl`, `total_proceeds`, `total_cost`, `by_ticker` (same ordering, by result
descending), `all_years`, `unmatched_tickers`. `year`/`month` still narrow the result
and never the input; `unmatched_tickers` is still populated only from in-period sells.
`_local()` and the Warsaw period convention stay in this module — the ledger has no
period concept.

#### 2. Days-held fields

**File**: `src/portfolio_realized.py`

**Intent**: Report how long the sold shares were held, derived from the same matches
that already price them.

**Contract**: Each `by_ticker` entry gains `days_held_weighted` (volume-weighted mean
over that ticker's in-period matches) and `days_held_max` (from the oldest matched lot).
Both are `float | None` / `int | None`; both are `None` when the ticker's in-period
sales matched no lot. A span is counted in whole days between the Warsaw-local dates of
acquisition and sale, consistent with the module's existing period convention.
Uncovered (zero-cost) shares contribute to neither figure.

#### 3. Close the partial-oversell test gap FIRST

**File**: `tests/test_portfolio_realized.py`

**Intent**: The suite cannot currently tell the difference between aggregating from
sells and aggregating from matches. Write this test **before** touching
`portfolio_realized.py`, so it fails loudly if the refactor takes the wrong source.

**Contract**: A ticker whose sale is only *partially* covered — say 4 shares bought,
10 sold — reports `shares_sold` of 10 and `proceeds` over all 10, while `cost` covers
only the 4 matched shares, and the ticker appears in `unmatched_tickers`. A second case
pins `sales == 1` for a single sale that consumes three separate lots.

#### 4. Tests for the new fields

**File**: `tests/test_portfolio_realized.py`

**Intent**: Pin the two new fields, including the absence case the ticket calls out.

**Contract**: A two-tranche ticker partially sold reports `days_held_max` from the
**oldest** lot, not the newest, and a `days_held_weighted` strictly between the two lot
spans. A ticker whose sale matched no buy reports `None` for both and still appears in
`unmatched_tickers`. Existing assertions in this file are not edited.

### Success Criteria:

#### Automated Verification:

- Realized suite passes with no assertion edited: `uv run pytest tests/test_portfolio_realized.py --tb=short`
- Endpoint suite passes: `uv run pytest tests/e2e/test_portfolio_realized.py --tb=short`
- Full suite passes: `uv run pytest --tb=short`
- Linting passes: `uv run ruff check .`
- No lot consumption left in this module — `git grep -c "lots\[ticker\]" src/portfolio_realized.py` must exit non-zero (git grep signals "no match" with exit 1; treating exit 1 as failure inverts this check)

#### Manual Verification:

- Re-running `compute_realized_pnl` over the production trade history diffs clean against `baseline/realized.json`, except for the two added days-held keys
- `GET /api/portfolio/realized` shows the same figures as before, with days-held plausible against a known two-tranche ticker

---

## Phase 3: XTB position reconstruction on the ledger

### Overview

The last remaining consumption. This one writes `avg_buy_price` into BigQuery, so the
bar is that the import produces byte-identical positions.

### Changes Required:

#### 1. Rebuild `reconstruct_positions`

**File**: `src/brokers/xtb.py`

**Intent**: Delete `_Lot` and `_consume_oldest`; normalize tickers, map `Operation`s
onto ledger events, and read the open lots back out.

**Contract**: `reconstruct_positions(operations) -> (positions, closed_tickers)` keeps
its signature and its ordering. `avg_buy_price` remains exactly
`remaining_cost / remaining_shares` — the contract at `xtb.py:204-207` that
`_merge_positions_by_ticker` and the P&L arithmetic depend on. A ticker whose lots are
fully consumed is still reported as closed rather than as a zero-share position.
`normalize_ticker` is applied in this module, before events reach the ledger.
`company_name` now resolves to the first non-`None` instrument name — the one
deliberate behaviour change, agreed with the user.

#### 2. Cover the name-resolution change

**File**: `tests/test_brokers_xtb.py`

**Intent**: Make the agreed behaviour change explicit rather than incidental.

**Contract**: A ticker whose first operation carries no instrument name but whose later
operation does now reports that name. Existing assertions are not edited.

### Success Criteria:

#### Automated Verification:

- XTB suite passes: `uv run pytest tests/test_brokers_xtb.py --tb=short`
- Full suite passes: `uv run pytest --tb=short`
- Linting passes: `uv run ruff check .`
- No lot machinery left outside the ledger — `git grep -l "_consume_oldest\|_Lot"` lists only `src/portfolio_lots.py` and `tests/test_portfolio_lots.py` (exit 1, meaning no match at all, is also a pass)

#### Manual Verification:

- Re-running the FIFO audit against production still reports `delta=+0.00` on both wallets — the import's arithmetic is unchanged
- `reconstruct_positions` over the production history diffs clean against `baseline/positions.json`

---

## Testing Strategy

### Unit Tests

- `tests/test_portfolio_lots.py` — the ledger in isolation, including every case the two
  old implementations disagreed on (tiebreak, oversell, name resolution, dust).
- `tests/test_portfolio_realized.py` — existing 16 unchanged, plus days-held including
  the `None` case.
- `tests/test_brokers_xtb.py` — existing unchanged, plus the name-resolution case.

### Integration Tests

- `tests/e2e/test_portfolio_realized.py` — the realized endpoint still answers with the
  same figures; the two new keys ride along additively.

### Manual Testing Steps

1. Run the FIFO audit script from research against production; confirm `delta=+0.00` on
   both wallets after phase 3.
2. Open **Zrealizowane**, pick a year with a partially-sold multi-tranche ticker, and
   confirm the totals match what the page showed before the change.
3. Confirm the two new fields are present in the response and that a ticker with an
   unmatched sale reports `null` rather than `0`.

## Performance Considerations

Production carries 508 operation rows; a real account is a couple of hundred
(`db/bigquery.py:3957-3960`). The consumption is O(sells × open lots) — today's
`compute_realized_pnl` never prunes consumed lots either, so this is not a regression.
No new I/O: the ledger is pure, and both consumers already have their rows in hand.

## Migration Notes

None. No schema change, no backfill, no stored value rewritten. `avg_buy_price` keeps
being written by the same code path with the same arithmetic, so a rollback is a plain
revert.

## References

- Research (with the three production measurements): `context/changes/fifo-lot-ledger/research.md`
- Change identity and the four decisions: `context/changes/fifo-lot-ledger/change.md`
- The tiebreak to adopt: `src/portfolio_realized.py:42-51`
- The contract that must not move: `src/brokers/xtb.py:204-207`
- Prior art on oversell and residuals: `context/archive/2026-07-30-calendar-holdings-as-of-date/research.md:133-137`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: The dated lot ledger

#### Automated

- [x] 1.1 New ledger tests pass — 1fee426
- [x] 1.2 Nothing else regressed (full suite) — 1fee426
- [x] 1.3 Linting passes — 1fee426
- [x] 1.4 The module imports nothing outside the standard library — 1fee426
- [x] 1.5 Baseline artefacts captured and non-empty (scratchpad, not committed) — 1fee426

#### Manual

- [x] 1.6 Public shape can carry part 2's as-of-date query without restructuring — 1fee426 (build_ledger already walks events chronologically, so a per-ticker snapshot list appends inside the existing loop; Sale.sold_at and OpenLot.occurred_at are already dated)

### Phase 2: Realized P&L on the ledger, plus days held

#### Automated

- [x] 2.1 Realized suite passes with no assertion edited — fc7e33f
- [x] 2.2 Endpoint suite passes — fc7e33f
- [x] 2.3 Full suite passes — fc7e33f
- [x] 2.4 Linting passes — fc7e33f
- [x] 2.5 No lot consumption left in portfolio_realized.py — fc7e33f

#### Manual

- [x] 2.6 compute_realized_pnl diffs clean against the captured realized baseline — fc7e33f
- [ ] 2.7 Realized endpoint shows unchanged figures plus plausible days-held

### Phase 3: XTB position reconstruction on the ledger

#### Automated

- [x] 3.1 XTB suite passes — 6d62ad6
- [x] 3.2 Full suite passes — 6d62ad6
- [x] 3.3 Linting passes — 6d62ad6
- [x] 3.4 No lot machinery left outside the ledger — 6d62ad6

#### Manual

- [x] 3.5 Production FIFO audit still reports delta=+0.00 on both wallets — 6d62ad6
- [x] 3.6 reconstruct_positions over production history diffs clean against the captured positions baseline — 6d62ad6
