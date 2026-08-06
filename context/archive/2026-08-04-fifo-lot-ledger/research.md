---
date: 2026-08-04T18:33:39+02:00
researcher: Radek
git_commit: 4307e3a5e9433bcf307dc8905b9695f78cb8c078
branch: feat/pul-114-fifo-lot-ledger
repository: puls-gpw
topic: "Dated FIFO lot ledger — consolidating the codebase's three lot consumptions, and what PUL-114 actually has to fix"
tags: [research, codebase, fifo, cost-basis, portfolio, pul-114]
status: complete
last_updated: 2026-08-04
last_updated_by: Radek
---

# Research: Dated FIFO lot ledger (PUL-114 part 1)

**Date**: 2026-08-04 18:33 +02:00
**Researcher**: Radek
**Git Commit**: `4307e3a5e9433bcf307dc8905b9695f78cb8c078`
**Branch**: `feat/pul-114-fifo-lot-ledger`
**Repository**: puls-gpw

## Research Question

What has to be true for `src/portfolio_lots.py` to become the single dated FIFO
engine behind every lot consumption in the codebase, without moving a single
production number? And — because part 2 depends on the answer — where is the cost
basis *actually* wrong on production, as opposed to where PUL-114 says it is?

## Summary

**The ticket's map of the problem is materially wrong, and the measurements say so.**

`avg_buy_price` is **not** a time-blind weighted average over every buy. It is written
by `src/brokers/xtb.py:reconstruct_positions`, which already runs FIFO, and its
contract is `remaining_cost / remaining_shares` (`xtb.py:204`). Replaying FIFO over all
508 production operation rows and comparing against every stored position gives a
divergence of **exactly zero** on both wallets — 73 873,65 vs 73 873,65 and 41 544,40
vs 41 544,40. The ticket's headline example proves the opposite of what it claims:
SNT's stored `avg_buy_price` is **297,90**, the FIFO number. The 284,28 is what
`ops_basis` computes — and SNT has a live position row, so `COALESCE(p.avg_buy_price,
b.avg_op_price)` never reaches `ops_basis` for it at all.

Two consequences, both load-bearing for part 2:

1. **The −4 482 PLN divergence against the XTB statement cannot come from either
   source.** Today's portfolio-wide basis gap between "what the curve uses" and "what
   FIFO says" is **+0,00 PLN**, measured. Whatever −4 482 is, this ticket will not move
   it. That criterion needs restating before part 2 is planned.
2. **The ticket is still fully justified, but by its third argument, not its first.**
   The basis is applied *backwards*: one constant multiplied across every historical
   day. Measured against the truth, the portfolio-wide cost basis is overstated by up
   to **+2 144,85 PLN (+8,41%)** on 2025-10-13, and sits around +1 800–2 100 PLN
   through most of late 2025. The sign is consistently positive, so the history curve
   **systematically understates past P&L**. Plus `ops_basis` is genuinely wrong for
   8 of the 20 sold-to-zero tickers, worst case **+11,33%** (BAC).

For **part 1** specifically: consolidation is safe. There are six behavioural
divergences between the two existing ledgers; five are resolvable by adopting the
strictly-better variant, and the one that could have changed import output — the
sort tiebreak — is provably inert on the real data (**0 tied buy/sell instants** across
508 rows). Baseline is 51 green tests across the two suites that pin this behaviour.

## Detailed Findings

### There are three lot consumptions, not two

The ticket names two. There is a third, and it is the one that matters most because
it writes the column everything else reads.

| # | location | lot shape | what it produces |
| -- | -- | -- | -- |
| 1 | `src/brokers/xtb.py:209-245` `reconstruct_positions` | `_Lot(shares, unit_price)` frozen dataclass | `avg_buy_price` at import — **already FIFO** |
| 2 | `src/portfolio_realized.py:81-135` `compute_realized_pnl` | `list[float]` `[volume, price]`, mutated in place | realized P&L per ticker |
| 3 | `db/bigquery.py:873-887` `ops_basis` CTE | none — SQL `SAFE_DIVIDE` | plain all-buys average |

Only #3 is the "time-blind weighted average" the ticket describes. Building a fourth
ledger would be the wrong move; part 1 collapses #1 and #2 onto one dated engine and
part 2 deletes #3.

### `get_portfolio_calendar_data` carries no cost basis at all

The ticket places `ops_basis` in both `get_portfolio_calendar_data` and
`get_portfolio_history`. Verified: `ops_basis` appears **only** at `db/bigquery.py:873`,
inside `get_portfolio_history`. The calendar (`db/bigquery.py:390`) computes
`SUM(shares_on_day × zmiana_kwotowa)` — the day's *move*, not P&L against purchase
(`db/bigquery.py:403-404`). The calendar is out of scope for the whole of PUL-114.

### Six divergences between the two Python ledgers

These are what consolidation has to reconcile. Each one is a decision, not a merge.

**1. Sort tiebreak — adopt `portfolio_realized`'s.**
`portfolio_realized._sort_key` (`:42-51`) sorts `(occurred_at, buy_before_sell)`, with a
documented reason: a fill and its closing fill occasionally share a timestamp to the
microsecond, and consuming a lot not yet recorded would drop the cost basis on the
floor. `xtb.py:216` sorts on `occurred_at` alone, leaving ties to Python's stable sort
(i.e. export row order). **Measured: 0 instants on production where a buy and a sell
share a timestamp for the same ticker.** So adopting the stronger tiebreak cannot
change what the import reconstructs today — it is a latent guard, kept for free.

**2. Ticker normalization — must NOT live in the ledger.**
`xtb.py:217` calls `normalize_ticker()` inside the consumption loop, stripping the
`.PL` suffix XTB appends. `portfolio_realized` does not, and must not: rows read out of
`user_broker_operations` are already normalized at write time (`src/api.py:486`, noted
in the PUL-103 research at `context/archive/2026-07-30-calendar-holdings-as-of-date/research.md:152`).
The shared ledger takes tickers as given; the XTB adapter normalizes before handing
rows in. Putting normalization in the ledger would double-strip nothing today but
would silently couple the storage layer to one broker's quirk.

**3. Oversell reporting — the ledger must report, not decide.**
`portfolio_realized:124-125` records the ticker in `unmatched_tickers` when a sale
exceeds the open lots, and charges those shares at zero cost — deliberately, so an
export beginning after the purchase cannot manufacture a gain. `xtb._consume_oldest`
(`:298-309`) floors at zero **silently**; PUL-103's research already flagged this
(`.../research.md:133-137`). The shared ledger must surface the uncovered quantity as
part of its result. This is not cosmetic: decision 2 of this change (residual shares
priced at stored `avg_buy_price`) needs exactly that number in part 2.

**4. Instrument-name resolution — a genuine bug in one of them.**
`xtb.py:221` uses `names.setdefault(ticker, op.instrument_name)`, which locks in `None`
if the first operation happens to carry no name. `portfolio_realized:98-99` retries
until it finds a non-`None`. First-non-`None` is strictly better; adopt it. Low
impact — but it is a behaviour change in the import, so it belongs in the plan
explicitly rather than as a silent improvement.

**5. Dust tolerance during consumption — equivalent in practice, worth a property test.**
`xtb:304` treats a lot as fully consumed when `lot.shares <= remaining + _DUST`,
discarding up to 1e-9 shares. `portfolio_realized:113` uses `take = min(lot[0],
remaining)`, leaving a sliver behind. Both use `_DUST = 1e-9` and both filter
`> _DUST` at the end (`xtb:231`), so the surviving lot sets agree. The cost difference
is bounded by `1e-9 × price` — nine orders of magnitude below a grosz.

**6. Lot mutability — cosmetic.** `portfolio_realized` mutates `list[float]` in place;
`xtb` replaces a frozen dataclass by index. The dated ledger wants a mutable
`remaining` field alongside immutable `price`/`occurred_at`, so neither survives
unchanged.

Additional shape difference: `compute_realized_pnl` takes plain `dict`s (so the same
function serves a parsed export and BigQuery rows — `portfolio_realized.py:3-4`), while
`reconstruct_positions` takes `Operation` dataclasses. The ledger should take one
normalized internal lot-event shape with thin adapters on both sides.

### Measurement 1 — `avg_buy_price` is already FIFO-exact on production

FIFO replayed over all 508 buy/sell rows, per `(portfolio_id, ticker)`, compared
against every one of the 43 stored positions:

```
glowny   positions=26 (no-ops=2)  stored=73,873.65  fifo=73,873.65  delta=+0.00
ikze     positions=17 (no-ops=1)  stored=41,544.40  fifo=41,544.40  delta=+0.00
```

Every position matched on shares **and** on unit basis. No oversell anywhere. The
three `no-ops` rows are `_CASH` (two wallets) and one hand-entered XTB position —
exactly the legitimate-fallback case the ticket describes.

Note on reading the raw audit output: the 43 rows span **three `user_id`s**, two of
which hold structurally identical imports, so every imported ticker appears twice.
Confirmed not a duplication bug — 43 rows, 43 distinct `(user_id, portfolio_id,
ticker)` keys, 9 portfolios across 7 users.

### Measurement 2 — where `ops_basis` actually bites

20 distinct sold-to-zero tickers reach `ops_basis` (40 groups across the two mirrored
users). Comparing the all-buys average against the FIFO basis genuinely open just
before the closing sale:

| ticker | ops_basis | true FIFO | error | peak cost |
| -- | -- | -- | -- | -- |
| BAC | 3,4513 | 3,1000 | **+11,33%** | 1 138,92 |
| TOR | 38,3145 | 35,0000 | **+9,47%** | 1 455,95 |
| LPP | 14 820,589 | 14 120,00 | **+4,96%** | 1 673,24 |
| WLT | 6,3969 | 6,3500 | +0,74% | 332,64 |
| PZU | 59,6477 | 59,2800 | +0,62% | 1 865,84 |
| BEP | 0,1708 | 0,1700 | +0,49% | 2 649,23 |
| DIA | 171,0500 | 171,3500 | −0,18% | 684,20 |
| PEO | 159,3995 | 159,6500 | −0,16% | 567,61 |

The other 12 are exactly 0,00% — every buy preceded every sell, so the all-buys average
*is* the FIFO basis. Absolute magnitudes are small (order 100–200 PLN) and apply only
to historical days on which the ticker was held.

### Measurement 3 — the backward error, which is the real payload

This is what PUL-114 exists to fix, and nothing above measures it. `get_portfolio_history`
multiplies each day's share count by **one constant** basis. Walking every operation
date and comparing that constant against the lots actually open:

```
date          true basis    flat basis     gap PLN    gap %
2025-10-13     25,510.08     27,654.94   +2,144.85   +8.41%
2025-12-29     32,656.73     34,789.31   +2,132.57   +6.53%
2025-12-12     32,584.73     34,714.67   +2,129.93   +6.54%
2025-12-10     30,404.69     32,489.58   +2,084.88   +6.86%
2025-10-29     21,128.30     23,003.86   +1,875.55   +8.88%
...
2026-07-21     56,536.96     56,536.96       +0.00    0.00%   <- right edge
```

Worst single ticker-day: **PAS +1 299,71 PLN on 2025-12-10** (11 buys), then XTB
+454,06 on 2025-12-09 and XTB +400,62 on 2025-04-07.

Two readings matter:

* The gap is **consistently positive** — the owner kept buying at rising prices, so
  today's blended basis exceeds what was open in the past. The curve therefore reports
  *less* profit than was real, systematically, across roughly a year.
* The gap at the last operation date is **exactly +0,00**. That is the PUL-100
  right-edge invariant holding by construction, and it is the strongest evidence that
  part 2's swap is a purely historical correction: today's number does not move.

### Test coverage that pins part 1

Baseline: **51 passed in 0,42 s** across the two suites.

* `tests/test_portfolio_realized.py` — 16 tests, 19 references to `compute_realized_pnl`
* `tests/test_brokers_xtb.py` — 8 references to `reconstruct_positions`
* `tests/e2e/test_portfolio_realized.py` — 12 tests over the endpoint

These are the contract for "no behaviour change". Part 1 must leave all 51 green
without editing their assertions; any assertion that has to change is a finding, not a
maintenance task.

## Code References

- `src/brokers/xtb.py:67-69` — `_Lot(shares, unit_price)`, the undated lot the ticket does not mention
- `src/brokers/xtb.py:194-245` — `reconstruct_positions`; `:204-207` the load-bearing `remaining_cost / remaining_shares` contract
- `src/brokers/xtb.py:298-309` — `_consume_oldest`, silent oversell flooring
- `src/brokers/xtb.py:184-186` — `normalize_ticker`, must stay outside the shared ledger
- `src/portfolio_realized.py:42-51` — `_sort_key`, the buy-before-sell tiebreak to adopt
- `src/portfolio_realized.py:101-125` — the consumption loop being replaced
- `src/portfolio_realized.py:25-35` — `_local()`, Warsaw period logic that stays in the caller
- `db/bigquery.py:873-887` — `ops_basis`, the only true weighted average; deleted in part 2
- `db/bigquery.py:904` — `COALESCE(p.avg_buy_price, b.avg_op_price)`, the call site to empty
- `db/bigquery.py:808-819` — the docstring recording the accepted approximation this ticket closes
- `db/bigquery.py:3953-3986` — `list_broker_trades`, already the right feed for a Python ledger
- `db/bigquery.py:1455` — `p.avg_buy_price` on the positions read path
- `src/api.py:602-618` — `PortfolioPositionOut`, which carries no date field (part 2)
- `src/api.py:657-689` — `_merge_positions_by_ticker`, re-averages by cost in "Wszystkie" mode
- `src/api.py:1114-1119` — positions-endpoint P&L from `avg_buy_price`
- `src/portfolio_treemap.py:40-46` — a fourth consumer of `avg_buy_price` the ticket does not list

## Architecture Insights

**The column was never the problem; the constant was.** The system already computes a
correct FIFO basis — once, at import, and then freezes it. Everything downstream is
correct *today* and wrong *yesterday*. That reframes part 2 from "replace a bad number"
to "restore the time dimension of a good one", which is a smaller and much safer change
than the ticket implies — and it explains why the right-edge invariant costs nothing to
preserve.

**The ledger's output shape is dictated by part 2's SQL feed.** Decision 3 fixed the
interface: step-function segments passed as an `UNNEST` array parameter. Basis changes
only on operation days, so the natural ledger output is, per ticker, a list of
`(effective_from, shares, cost)` snapshots — which is precisely what measurement 3's
script had to build to take the measurement. That is a strong signal the shape is
right: the analysis and the implementation want the same structure.

**Residual is a ledger output, not a ledger decision.** PUL-103 established that
`user_broker_operations` is a complete record of *movements the broker saw*, not of
holdings (`context/archive/2026-07-30-calendar-holdings-as-of-date/research.md:341-345`).
The ledger therefore cannot know a position's full history and must not pretend to. It
reports what the lots cover; the caller reconciles the remainder against the stored
snapshot. Keeping that boundary is what prevents "no lot found" from collapsing into a
zero basis.

## Historical Context (from prior changes)

- `context/archive/2026-07-30-calendar-holdings-as-of-date/research.md:133-137` — oversell flooring in `_consume_oldest` vs a bare SQL `SUM` going negative; the residual formula neutralises it
- `context/archive/2026-07-30-calendar-holdings-as-of-date/research.md:139-140` — float dust and the phantom-position risk
- `context/archive/2026-07-30-calendar-holdings-as-of-date/research.md:147-153` — tickers already normalized at write, so joins need no `.PL` stripping
- `context/archive/2026-07-30-calendar-holdings-as-of-date/research.md:331` — PUL-103 explicitly named `portfolio_realized.py` as the reuse candidate for the cost basis; this change is that reuse
- `context/archive/2026-07-30-calendar-holdings-as-of-date/research.md:341-349` — why operations correct the snapshot backwards rather than rebuild it forwards
- `context/archive/portfolio-daily-change-colour/change.md:20-21` — PUL-123 part 1 deferring `first_buy_date` / `days_held` to this ticket

## Open Questions

1. **What is the −4 482 PLN?** Measured: today's basis gap is exactly 0,00, so it is not
   the cost basis of open positions. Candidates worth one query before part 2 is
   planned: commissions absent from `unit_price`, the statement's figure including
   realized results or dividends, or the number simply predating PUL-98's 47 834-row
   close correction and PUL-102's repair. **This must be settled before part 2 adopts
   the ticket's second verification criterion, which currently cannot be met by
   anything part 2 does.**
2. **`ops_basis` for a re-bought ticker.** A ticker sold to zero and later re-bought has
   a live position row, so it never reaches `ops_basis` — but its *historical* days
   before the re-buy need the consumed lots, which the stored snapshot cannot supply.
   Part 2 should confirm whether any production ticker is in this state.
3. **Does the treemap's `since_purchase` want the dated basis too?** It reads
   `avg_buy_price` at `src/portfolio_treemap.py:41` and the ticket does not list it. It
   is a "since purchase" figure about today, so today's basis is arguably correct —
   but it should be a stated decision in part 2, not an omission.
