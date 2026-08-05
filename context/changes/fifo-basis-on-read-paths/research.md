---
date: 2026-08-05T09:24:38+02:00
researcher: Radek
git_commit: 021ce7e50d624bb1c285bd9dc829f0e5a1f862c6
branch: feat/pul-114-fifo-basis-on-read-paths
repository: puls-gpw
topic: "Swapping the read paths onto the dated lot ledger — what actually moves, and what shape the segments must take"
tags: [research, codebase, fifo, cost-basis, portfolio-history, pul-114, pul-123]
status: complete
last_updated: 2026-08-05
last_updated_by: Radek
---

# Research: FIFO basis on the read paths (PUL-114 part 2)

**Date**: 2026-08-05 09:24 +02:00
**Git Commit**: `021ce7e50d624bb1c285bd9dc829f0e5a1f862c6`
**Branch**: `feat/pul-114-fifo-basis-on-read-paths`
**Repository**: puls-gpw

## Research Question

Part 1 built the dated ledger and moved both Python consumers onto it without shifting
a production number. Part 2 is where the numbers move. So: **what exactly moves, by how
much, and what shape must the ledger's output take for the SQL to consume it without
breaking the three invariants `get_portfolio_history` already defends?**

And the three questions part 1 left open: is any production ticker in the
re-bought state; does the treemap want the dated basis; what is the −4 482 PLN.

## Summary

**The riskiest design question has an empirical answer, and it is the safe one.**

The natural fear with this change is that Python's model of "shares held on day D" and
the SQL's `shares_on_day` (PUL-103's backward correction over today's snapshot) drift
apart — because they are computed from different anchors. If they drift, segments have
to carry *cost* and the SQL's P&L formula has to be rewritten, which puts the PUL-100
right-edge invariant and the coverage gate in play at the same time.

Measured over all 508 production operations: **`ledger_open(D) + residual` equals SQL's
`shares_on_day(D)` exactly, on every operation day, for every ticker.** No exceptions,
no oversell anywhere. So the segments can carry a **basis price per share**, and
`SUM(shares_on_day × (px_ff − basis))` stays character-for-character as it is. The diff
collapses to: make one column time-varying, delete `ops_basis`.

**What moves.** Modelling both bases exactly as production computes them, over the user
carrying 58 781,10 PLN of stored basis:

| | |
| -- | -- |
| operation dates measured | 76 |
| basis **overstated** today (P&L understated) | **69 dates** |
| basis understated | 6 dates |
| worst gap | **+2 143,10 PLN (+7,12%)** on 2025-10-14 |
| **right edge (2026-07-21)** | **−0,00** — PUL-100 invariant holds by construction |

So the historical curve moves *up* across roughly a year, and today's reported P&L does
not move at all. That asymmetry is the whole change in one line.

**The case nothing currently corrects.** Part 1's open question 2 has an answer:
**four tickers are in the re-bought state** — CBF, KRU, SNT and XTB, each sold to zero
and bought again. They have a live position row, so `COALESCE(p.avg_buy_price,
b.avg_op_price)` never reaches `ops_basis` for them; today's basis is simply applied to
every day before the zero crossing. **SNT is the ticket's own headline example** — the
ticket picked the right ticker for the wrong reason.

**`first_buy_date` is not `MIN(buy date)`.** Of 13 live tickers, **3 disagree** — by
**424, 332 and 249 days**. A SQL `MIN(occurred_at)` would report a holding period whose
shares the owner sold long ago. This is why PUL-123 part 2 had to wait for this ticket
rather than being a one-line join.

## Detailed Findings

### The reconciliation that de-risks the whole design

SQL computes (`db/bigquery.py:964-991`):

```
shares_on_day(D) = today_shares − (total_signed − signed_up_to(D))
```

anchored on the **position row**, because `user_broker_operations` records movements the
broker saw, not holdings (PUL-103). The ledger computes open lots anchored purely on
**operations**. The two agree iff

```
shares_on_day(D) − ledger_open(D) = today_shares − total_signed = residual
```

which is time-invariant — *provided* no oversell flooring intervenes, since SQL's raw
signed sum can go where a floored ledger cannot. Measured: **exact on every operation
day, zero oversell**. The residual is therefore a constant per ticker, and

```
basis(D) = (ledger_cost(D) + residual × stored_avg_buy_price) / (ledger_open(D) + residual)
```

is a well-defined price the SQL can multiply by its own `shares_on_day`. Python never
has to tell SQL how many shares there were — which is exactly the coupling that would
have been dangerous.

### `ops_basis` is strictly dominated, so deleting it is a simplification

`ops_basis` (`db/bigquery.py:873-887`) exists for one case: a ticker sold to zero, whose
position row the import deleted, which would otherwise add value to historical days
while adding no basis. The ledger covers that case natively and better — it still holds
the buys, so it produces the *dated* cost of the lots open on each day rather than one
all-buys average. Part 1 measured `ops_basis` wrong for 8 of 20 sold-to-zero tickers,
worst +11,33% (BAC), +9,47% (TOR), +4,96% (LPP).

`ops_basis` is a fallback the ledger makes unnecessary; the stored `avg_buy_price`
remains as the declared fallback for residual shares the ledger cannot explain.

### Basis-less holdings: a latent hole, not a live bug

`SUM(IF(px_ff IS NOT NULL, shares_on_day * (px_ff - avg_price), 0))` returns NULL when
`avg_price` is NULL, and BigQuery's `SUM` skips NULLs — so a ticker held with *no* basis
from either source contributes value with no cost, and the day reads as pure profit.
**Measured: 0 such tickers on production today** (a ticker with no position row and no
buy anywhere). The hole is reachable in principle — a sell-only export window — so it
deserves an explicit guard rather than a repair.

### Sizing the `UNNEST` parameter

One segment per (portfolio, ticker, distinct operation date):

| wallet | segments |
| -- | -- |
| `6c6fdd5b…` | 93 |
| `d49d0121…` | 93 |
| `10414536…` | 36 |
| `57ed5830…` | 36 |
| **total, all users** | **258** |

93 structs is a trivial query parameter. The decision to compute per request with no
materialised table is comfortably within budget.

### Segments must key on `(portfolio_id, ticker)`, not ticker alone

In "Wszystkie" mode (`portfolio_id=None`) the `holders` CTE keys on
`(portfolio_id, ticker)` — two wallets holding the same ticker keep separate bases.
Merging their lots into one FIFO stream would let a sale in one wallet consume a lot
from the other. `list_broker_trades` (`db/bigquery.py:3970`) does **not** currently
select `portfolio_id`; part 2 needs it added (additive — `compute_realized_pnl` reads by
key and ignores extras).

> Observation, not this change's business: `compute_realized_pnl` builds its ledger
> keyed by ticker alone, so in all-wallets mode it already merges lots across wallets.
> Pre-existing; worth a separate look, not a scope expansion here.

### Where the ledger must be built — and why it matters for e2e

`tests/e2e/conftest.py:791` patches `src.api.get_portfolio_history` wholesale. Building
the ledger **inside `db.bigquery.get_portfolio_history`** therefore leaves the e2e suite
untouched, whereas moving the orchestration up into `src/api.py` would change the fake's
signature and every history e2e test with it.

The pure part — events + positions → segments — belongs in `src/portfolio_lots.py`,
where it is unit-testable with no infrastructure, exactly like the ledger itself. The
data layer stays responsible only for fetching and parameter-binding.

### `first_buy_date` — the ledger, not a SQL `MIN`

| ticker | `MIN(buy)` | oldest **open** lot | apart |
| -- | -- | -- | -- |
| CBF | 2025-05-19 | 2026-07-17 | **424 days** |
| XTB | 2025-01-31 | 2025-12-29 | **332 days** |
| SNT | 2025-05-09 | 2026-01-13 | **249 days** |

10 of 13 live tickers agree; these three do not, and they are exactly the re-bought
ones. "How long have I held this" is a question about the shares held *now*, and under
FIFO those are the oldest open lot's.

### The treemap does not want the dated basis (part 1, open question 3)

`src/portfolio_treemap.py:40-46` computes `since_purchase_pct` as
`(current_price / avg_buy_price − 1) × 100` — a statement about **today**. Part 1 proved
`avg_buy_price` is FIFO-exact today (delta +0,00 across all 43 positions), so today's
basis is the correct input. **Decision: no change.** Recorded so it is an omission on
purpose rather than by oversight.

### The −4 482 PLN (part 1, open question 1)

Settled out of scope. Part 1 measured today's portfolio-wide basis gap at exactly
+0,00 on both wallets, so no change to either basis source can move it. Tracked as
**PUL-124**, with the three candidate causes and the instruction to check the cheapest
first (a stale measurement predating PUL-98 and PUL-102).

### Latency — to be measured, not predicted

The added query scans 508 rows (a few KB); the endpoint caches for 300 s
(`src/api.py:1361`). An attempt to time it through the `bq` CLI was worthless — ~6 s of
CLI startup swamped the signal, and the first attempt measured a *failure* because
`bq.cmd` cannot be invoked from Bash with a space in the path. The honest position: the
scan is trivial, the fixed job overhead is what costs, and the plan should verify the
real endpoint's before/after latency rather than assert a number here.

## Code References

- `db/bigquery.py:873-887` — `ops_basis`, deleted by this change
- `db/bigquery.py:904` — `COALESCE(p.avg_buy_price, b.avg_op_price)`, the call site
- `db/bigquery.py:964-991` — `held`, PUL-103's residual formula; must project `portfolio_id`
- `db/bigquery.py:1022-1040` — `valued` / `daily`, where the basis price is consumed
- `db/bigquery.py:1031-1033` — the P&L formula and the `covered > 0` gate that stay intact
- `db/bigquery.py:808-819` — the docstring recording the approximation this change closes
- `db/bigquery.py:3953-3986` — `list_broker_trades`, the feed; needs `portfolio_id` added
- `db/bigquery.py:1450-1476` — `list_user_portfolio_positions`, where `first_buy_date` lands
- `src/api.py:602-618` — `PortfolioPositionOut`, gains one optional field
- `src/api.py:654-691` — `_merge_positions_by_ticker`, needs a rule for merging dates
- `src/api.py:1350-1377` — the history endpoint, unchanged by this design
- `src/portfolio_lots.py` — where the pure segment builder belongs
- `src/portfolio_treemap.py:40-46` — deliberately untouched
- `tests/e2e/conftest.py:791` — the wholesale patch that shapes the layering decision

## Architecture Insights

**The change is a restoration of a time axis, not a replacement of a number.** Every
basis in the system is already correct for today; all of them are frozen. That framing
is what keeps the right edge invariant free — and it is why the diff is far smaller than
the ticket implies.

**The dangerous coupling was avoided by measurement, not by design cleverness.** Had the
shares reconciliation failed on even one day, the safe option would have been segments
carrying cost and a rewritten aggregation — touching the coverage gate and the
right-edge invariant simultaneously. The measurement bought the small diff.

**Purity is the layering rule that already paid off once.** Part 1's impl-review
promoted the ledger's stdlib-only guard to a real test because `src/brokers` came to
depend on it. The segment builder belongs on the same side of that line: pure input,
pure output, no client. The data layer binds parameters; it does not do arithmetic.

## Historical Context (from prior changes)

- `context/changes/fifo-lot-ledger/research.md:183-210` — measurement 3, the backward error this change fixes
- `context/changes/fifo-lot-ledger/research.md:276-292` — the three open questions answered above
- `context/archive/2026-07-30-calendar-holdings-as-of-date/research.md:341-349` — why operations correct the snapshot backwards
- `context/archive/portfolio-daily-change-colour/change.md:20-21` — PUL-123 part 1 deferring `first_buy_date` here

## Open Questions

1. **`first_buy_date`: computed per request, or written at import?** The import already
   runs the ledger (`reconstruct_positions`), so it could store the open-lot date as a
   column and cost the read path nothing — at the price of a schema change, a backfill,
   and a value that goes stale if operations change outside an import.
2. **Merging `first_buy_date` in "Wszystkie" mode.** `_merge_positions_by_ticker` sums
   shares and cost-weights the price; a date has no natural weighted average. Earliest
   open lot across the merged wallets is the obvious rule, but it should be a decision.
3. **Does the ~+2 100 PLN lift on historical days need to be surfaced to the user?** The
   chart will visibly move. Silent correction, or a note like the existing `notes` /
   `data_from` metadata already carries?
