# FIFO Basis on the Read Paths Implementation Plan

## Overview

Give the portfolio's cost basis a time axis. `get_portfolio_history` currently multiplies
every historical day's share count by **one constant** basis; this change feeds it a
step function computed from the dated lot ledger built in part 1, deletes the
`ops_basis` CTE, and exposes each position's oldest **open** lot date so PUL-123 part 2
can render a holding period.

## Current State Analysis

Three facts from `research.md` shape everything below.

1. **The basis is correct today and frozen.** `avg_buy_price` is FIFO-exact as of import
   (part 1 measured +0,00 across all 43 positions). The defect is that one number is
   applied backwards across ~a year.
2. **Python's ledger and the SQL's share model reconcile exactly.** `ledger_open(D) +
   residual` equals `shares_on_day(D)` on every operation day, every ticker, zero
   oversell. This is what lets segments carry a **price** and leaves
   `SUM(shares_on_day × (px_ff − basis))` untouched.
3. **`ops_basis` is strictly dominated.** It exists only for tickers whose position row
   the import deleted; the ledger serves that case with a dated cost instead of an
   all-buys average that is wrong for 8 of 20 such tickers (worst +11,33%).

Measured effect of the change, on the user carrying 58 781,10 PLN of stored basis: the
basis is overstated on **69 of 76** operation dates, worst **+2 143,10 PLN (+7,12%)** on
2025-10-14, and the right edge moves by **−0,00**.

## Desired End State

`get_portfolio_history` values every day against the lots that were actually open on
that day. `/api/portfolio/positions` carries `first_buy_date` — the acquisition date of
the oldest open lot, not the first buy ever made. `ops_basis` no longer exists.

**Verified by**: the pre-change series captured in Phase 1 reproduces to the grosz at
the right edge, and diverges on historical days in the direction and magnitude predicted
above.

### Key Discoveries

- `db/bigquery.py:964-991` — PUL-103's residual formula; `held` must project
  `portfolio_id`, which it currently groups away
- `db/bigquery.py:1031-1033` — the P&L formula and `covered > 0` gate that stay intact
- `db/bigquery.py:3970` — `list_broker_trades` does not select `portfolio_id`; without it
  "Wszystkie" mode would let a sale in one wallet consume a lot from another
- `tests/e2e/conftest.py:791` — patches `src.api.get_portfolio_history` wholesale, so
  building the ledger inside the data layer leaves the e2e suite undisturbed
- `src/api.py:454`, `:1260` — two callers of `list_user_portfolio_positions` that must
  **not** pay for the new query; `include_history` is the existing opt-in pattern
- 3 of 13 live tickers have an oldest-open-lot date differing from `MIN(buy)` by 424,
  332 and 249 days

## What We're NOT Doing

- **Not touching the treemap.** `since_purchase_pct` is a statement about today, and
  today's basis is already FIFO-exact. Deliberate omission, recorded in `change.md`.
- **Not touching `get_portfolio_calendar_data`.** It carries no cost basis at all.
- **Not chasing the −4 482 PLN.** Part 1 proved no basis change can move it. PUL-124.
- **Not fixing `compute_realized_pnl`'s cross-wallet lot merging.** Real, pre-existing,
  and adjacent — but this change already moves production numbers on the history chart,
  and a second correction in the Zrealizowane tab would make neither attributable to a
  cause. Separate ticket, opened at the end of this change.
- **Not rendering anything.** The holding-period column is PUL-123 part 2.
- **Not surfacing the correction in the UI.** The curve simply becomes correct.

## Implementation Approach

Same strangler shape as part 1: build the pure thing first with no callers, capture a
baseline while the old behaviour still exists, then swap consumers one at a time in
order of blast radius.

The pure segment builder lives in `src/portfolio_lots.py` alongside the ledger — same
stdlib-only constraint, guarded by the same AST test part 1's impl-review promoted from
a checklist item. The data layer fetches and binds parameters; it does no arithmetic.

## Critical Implementation Details

**Phase ordering is load-bearing.** The Phase 1 baseline must land on disk *before*
Phase 2 deletes `ops_basis` — once it is gone there is nothing left to measure the
change against. Part 1 hit exactly this and the baseline is what made its equivalence
claim checkable rather than asserted.

**The `covered` gate changes meaning and must change consistently.** Today
`px_ff IS NOT NULL` gates value, P&L and the `covered` counter. With the decision that a
basis-less ticker is excluded from *both* value and basis, all three must move to the
same predicate — otherwise `covered > 0` starts measuring something different from what
the day's sums measure, and a day can survive the gate with a value nobody costed.

## Phase 1: Baseline capture and the pure segment builder

### Overview

Freeze the current behaviour as a measurable artefact, then build the segment/date
functions test-first with no caller. Nothing in production can move in this phase.

### Changes Required

#### 1. Pre-change baseline

**File**: scratchpad (`.../scratchpad/baseline_history.json`) — **not committed**

**Intent**: Run the *current* `get_portfolio_history` SQL against production for each
real wallet and for all-wallets mode at the widest range, and serialize the series.
Phase 2 diffs against this; after `ops_basis` is deleted the old numbers are
unrecoverable.

**Contract**: One entry per `(user_id, portfolio_id|all)` holding the full
`snapshot_date / value_pln / pnl_pln` series plus `notes`, `excluded` and `data_from`.

**The artefact stays out of git.** The repository is public, and this file is a full
year of the owner's portfolio value keyed by Firebase user id. Part 1's baselines were
committed before this was weighed; from here the rule is that the *evidence* enters the
repo and the *data* does not. What lands in the change folder is
`baseline-report.md` — the derived comparison Phase 2 produces (right-edge agreement,
per-date divergence direction and magnitude), which is what any future reader actually
needs to check the equivalence claim.

#### 2. The segment builder

**File**: `src/portfolio_lots.py`

**Intent**: Turn lot events plus today's stored positions into a step function of cost
basis per share, and into the oldest-open-lot date per ticker. Pure — no client, no I/O,
stdlib only, exactly like `build_ledger`.

**Contract**: Two additions.

`basis_segments(events, positions)` → an ordered sequence of
`BasisSegment(portfolio_id, ticker, valid_from: date, basis: float)`, where a segment
holds from `valid_from` until the next segment for the same key. `positions` supplies
`(shares, avg_buy_price)` per key so the residual can be priced:

```
residual   = today_shares − total_signed          # time-invariant; measured exact
basis(D)   = (ledger_cost(D) + residual × avg_buy_price)
             / (ledger_open(D) + residual)
```

A key whose denominator is ≤ `DUST` on a given day emits no segment for that day — the
caller decides what a basis-less holding means, the builder does not.

**The residual must never be priced at nothing.** A key with no entry in `positions` —
which is exactly the sold-to-zero case that replaces `ops_basis` — has `today_shares =
0` and therefore no stored `avg_buy_price`. If its operations do not net to zero (a
position deleted by hand leaves buys on record; `db/bigquery.py:987-991` guards the same
shape in SQL), the residual is non-zero with no price attached. In that case the residual
is folded in at **the ledger's own open-lot basis** — i.e. `basis(D) = ledger_cost(D) /
ledger_open(D)` — so the shares SQL will still count inherit the price of the shares the
ledger can explain. Pricing them at zero would render as 100% profit, which is the
failure this whole design exists to make unreachable.

`first_open_lot_dates(events)` → `{(portfolio_id, ticker): date}` from the *oldest open
lot*, absent for a key with no open lots. Deliberately not `MIN(buy)`.

Both take `portfolio_id` as part of the key: two wallets holding the same ticker must
not share a FIFO stream.

#### 3. Tests, written first

**File**: `tests/test_portfolio_lots.py`

**Intent**: Pin the behaviours the two implementations could plausibly get wrong, and
the ones production actually exhibits.

**Contract**: Test-first, RED before GREEN. At minimum:
a re-bought ticker's segments drop to the re-buy's cost rather than reusing spent lots;
`first_open_lot_dates` returns the re-buy date, not the first-ever buy; residual shares
are priced at the stored `avg_buy_price` and never at zero; a key with no position row
still yields segments from its buys (the `ops_basis` case); segments are keyed per
wallet so two wallets with identical tickers do not merge; a fully-sold key emits no
segment rather than a zero-cost one.

### Success Criteria

#### Automated Verification

- Baseline artefact captured and non-empty (scratchpad path, not committed)
- New unit tests pass: `uv run pytest tests/test_portfolio_lots.py -q`
- The ledger module is still stdlib-only (the part 1 guard): `uv run pytest tests/test_portfolio_lots.py -k imports -q`
- Full suite green: `uv run pytest -q`
- Lint clean: `uv run ruff check .`

#### Manual Verification

- Spot-check one baseline series against the chart in the browser — the right-hand value matches today's reported P&L

---

## Phase 2: History onto the segments, ops_basis deleted

### Overview

The only phase that moves a production number. `get_portfolio_history` gains a
time-varying basis; the weighted-average fallback CTE is removed.

### Changes Required

#### 1. The operations feed gains its wallet

**File**: `db/bigquery.py` (`list_broker_trades`, ~`:3970`)

**Intent**: Select `portfolio_id` so segments can be keyed per wallet.

**Contract**: Additive column on the returned dicts. `compute_realized_pnl` reads by key
and ignores extras, so its behaviour is unchanged — pinned by the part 1 suite staying
green without edits.

#### 2. Segments bound as a query parameter

**File**: `db/bigquery.py` (`get_portfolio_history`)

**Intent**: Fetch operations and stored positions, build the segments in Python, and
bind them as an array parameter. Orchestration stays in the data layer so
`tests/e2e/conftest.py`'s wholesale patch of `get_portfolio_history` keeps working.

**Contract**: `bigquery.ArrayQueryParameter("basis_segments", "STRUCT<...>", …)` carrying
`portfolio_id, ticker, valid_from DATE, basis FLOAT64`. Production's largest wallet
produces 93 rows, so no chunking or materialisation is warranted.

#### 3. The query

**File**: `db/bigquery.py` (`get_portfolio_history` SQL)

**Intent**: Resolve each (wallet, ticker, day) to the segment in force on that day, use
it as the basis, and delete the CTE it replaces.

**Contract**: Four edits.

- **Delete** the `ops_basis` CTE and its `LEFT JOIN` in `holders`; `avg_price` becomes
  plain `p.avg_buy_price`.
- **Add** a `dated_basis` CTE resolving the latest segment with `valid_from <=
  snapshot_date` per (portfolio_id, ticker, day), over the in-range spine only.
- **Project `portfolio_id`** out of `held` (currently grouped away) and join
  `dated_basis` on it, taking `COALESCE(dated_basis.basis, h.avg_price)`. **The fallback
  arm is not dead defensive code**: a position entered by hand carries no operations at
  all, so it never produces a segment, and `shares_on_day` equals its residual on every
  day of the range. `h.avg_price` is the only thing that prices it. Do not tidy it away.
- **Move value, P&L and `covered` onto one predicate** — priced *and* costed:

  ```sql
  -- all three must agree, or a day survives the gate carrying value nobody costed
  SUM(IF(usable, shares_on_day * px_ff, 0))                     AS value_pln,
  SUM(IF(usable, shares_on_day * (px_ff - basis), 0))           AS pnl_pln,
  COUNTIF(usable)                                               AS covered
  ```

  where `usable = px_ff IS NOT NULL AND basis IS NOT NULL`.

#### 4. The basis-less holding is counted, not silent

**File**: `db/bigquery.py` (`get_portfolio_history`, `meta` CTE)

**Intent**: A ticker held with no basis from either source is now dropped from the day
entirely. Dropping it silently would understate portfolio value with no trace.

**Contract**: A diagnostic count carried by `meta` alongside `residual_holders`, surfaced
through the same warning-level helper. Not returned to the client. **Not `logger.debug`**
— that level is dead on production.

#### 5. The docstring

**File**: `db/bigquery.py:808-819`

**Intent**: The docstring currently records the flat basis as an accepted approximation,
citing the SNT 284,28-vs-297,90 figure part 1 disproved. It must now describe what the
code does.

**Contract**: Replace the approximation paragraph with the step-function behaviour and
the surviving approximation (residual shares priced at the stored basis). Keep the
BOCF/LOCF and right-edge paragraphs.

### Success Criteria

#### Automated Verification

- Full suite green: `uv run pytest -q`. Phase 2 changes behaviour deliberately, so
  unlike part 1 it cannot promise an untouched suite — every assertion that changes
  is named and justified in `baseline-report.md`, and any that changes without a
  reason recorded there is a finding.
- Lint clean: `uv run ruff check .`
- The `ops_basis` CTE is gone: `! grep -rn "avg_op_price" db/ src/` (the name survives
  only in the comment explaining what replaced it, which is worth keeping)

#### Manual Verification

- Diffed against Phase 1's baseline on production: the right edge matches to the grosz on every wallet
- Historical days move in the predicted direction and magnitude (up, worst ≈ +2 143 PLN around 2025-10-14)
- The four re-bought tickers (CBF, KRU, SNT, XTB) show a basis that steps at the re-buy rather than a flat line
- `baseline-report.md` written to the change folder, carrying the comparison rather than the data
- Pre-change endpoint latency recorded (the post-deploy half is a release check, not a phase gate — this branch is not on production until the release branch merges)

---

## Phase 3: first_buy_date on the positions read path

### Overview

Additive API field, opt-in so the two callers that do not need it do not pay for it.

### Changes Required

#### 1. Opt-in enrichment

**File**: `db/bigquery.py` (`list_user_portfolio_positions`)

**Intent**: When asked, attach the oldest open lot's date to each position row.

**Contract**: New keyword-only flag defaulting to off, mirroring `include_history`. When
off, not a single extra query runs — `src/api.py:454` (import resolution, wants tickers
only) and `src/api.py:1260` (treemap, deliberately on today's basis) keep their current
cost. When on, one `list_broker_trades` call feeds `first_open_lot_dates`. Rows for keys
with no open lot carry `None`.

#### 2. The response model

**File**: `src/api.py` (`PortfolioPositionOut`, `:602-618`)

**Intent**: Carry the date to the client.

**Contract**: `first_buy_date: str | None = None`, ISO date. Optional so the field's
absence is never an error — the same shape the model already uses for `price_history`.

#### 3. "Wszystkie" merging

**File**: `src/api.py` (`_merge_positions_by_ticker`, `:654-691`)

**Intent**: Give the merged row a date.

**Contract**: The **earliest** non-null `first_buy_date` across the merged wallets. It
answers "since when do I hold any of this" and, unlike a share-weighted date,
corresponds to a purchase that actually happened.

#### 4. Endpoint contract test

**File**: `tests/test_api.py`

**Intent**: Pin the HTTP contract so PUL-123 part 2 renders against a field proven to
exist and to be spelled correctly.

**Contract**: Via FastAPI's `TestClient` — `/api/portfolio/positions` returns
`first_buy_date`, and in "Wszystkie" mode it is the earliest open lot across wallets.

**Not in `tests/e2e/`.** That suite is Playwright (`tests/e2e/test_portfolio_positions.py:1`
imports `playwright.sync_api` and drives the DOM), and this change renders nothing — a
browser test could only assert on a field the UI does not display. `tests/test_api.py` is
where this codebase already tests HTTP contracts. The browser test belongs to PUL-123
part 2, where the column actually appears.

### Success Criteria

#### Automated Verification

- New API contract test passes: `uv run pytest tests/test_api.py -q -k first_buy_date`
- Full suite green: `uv run pytest -q`
- Lint clean: `uv run ruff check .`

#### Manual Verification

- `/api/portfolio/positions` in the browser returns `first_buy_date` for held tickers
- CBF, SNT and XTB report the re-buy date, not the first-ever purchase
- The treemap and the import-resolution path issue no extra query (flag defaults off)

---

## Testing Strategy

### Unit Tests

The segment builder carries the weight: re-bought keys, residual pricing, per-wallet
keying, sold-to-zero keys with no position row, and the empty-denominator case. All pure,
no infrastructure — the same standard part 1's ledger tests set.

### Integration Tests

None new. The SQL cannot be exercised without BigQuery, which is why Phase 2's
verification is a production diff against a captured baseline rather than a test.

### Manual Testing Steps

1. Open the value chart on the 1y range and confirm the right-hand end matches the
   reported P&L (the PUL-100 invariant).
2. Compare the curve against the Phase 1 baseline — historical days should sit higher.
3. Check a re-bought ticker's basis steps at the re-buy.
4. Confirm the treemap's "od zakupu" figures are unchanged.

## Performance Considerations

Two small queries are added to a path that caches for 300 s. The scan is 508 rows; the
cost is BigQuery's fixed job overhead, not the data. Phase 2 measures the real
before/after rather than predicting it — the research attempt to time it through the
`bq` CLI was swamped by ~6 s of CLI startup and is not evidence.

## Migration Notes

No schema change, no backfill, no materialised table. The change is pure computation, so
rollback is `git revert` — the next request recomputes from operations.

## References

- Research: `context/changes/fifo-basis-on-read-paths/research.md`
- Part 1: `context/changes/fifo-lot-ledger/plan.md`, PR #248
- The engine this consumes: `src/portfolio_lots.py`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Baseline capture and the pure segment builder

#### Automated

- [x] 1.1 Baseline artefact captured and non-empty (scratchpad, not committed) — 1836eae
- [x] 1.2 New unit tests pass — 1836eae
- [x] 1.3 Ledger module is still stdlib-only — 1836eae
- [x] 1.4 Full suite green — 1836eae
- [x] 1.5 Lint clean — 1836eae

#### Manual

- [ ] 1.6 Spot-check one baseline series against the chart in the browser

### Phase 2: History onto the segments, ops_basis deleted

#### Automated

- [x] 2.1 Full suite green; changed assertions named in baseline-report.md — 5a7a795
- [x] 2.2 Lint clean — 5a7a795
- [x] 2.3 The ops_basis CTE is gone — 5a7a795

#### Manual

- [x] 2.4 Right edge matches the baseline to the grosz on every wallet — 5a7a795
- [x] 2.5 Historical days move in the predicted direction and magnitude — 5a7a795
- [x] 2.6 The four re-bought tickers show a stepping basis — 5a7a795
- [x] 2.7 baseline-report.md written to the change folder — 5a7a795
- [ ] 2.8 Pre-change endpoint latency recorded

### Phase 3: first_buy_date on the positions read path

#### Automated

- [x] 3.1 New API contract test passes
- [x] 3.2 Full suite green
- [x] 3.3 Lint clean

#### Manual

- [x] 3.4 Positions endpoint returns first_buy_date for held tickers
- [x] 3.5 CBF, SNT and XTB report the re-buy date
- [x] 3.6 Treemap and import-resolution paths issue no extra query
