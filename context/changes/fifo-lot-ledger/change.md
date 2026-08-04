---
change_id: fifo-lot-ledger
title: Dated FIFO lot ledger as the single engine behind every lot consumption
status: plan_reviewed
created: 2026-08-04
updated: 2026-08-04
archived_at: null
tracking:
  linear: PUL-114
  github: 229
---

## Notes

Part 1 of PUL-114 (Linear PUL-114, GitHub #229). Builds `src/portfolio_lots.py` — a
FIFO lot ledger whose lots carry an acquisition timestamp — and rebuilds the two
existing undated consumptions on top of it. **No API change, no read-path change, no
SQL change in this change.** Part 2 (`fifo-basis-on-read-paths`) swaps the read paths
onto the ledger and exposes the dates.

### Why the split

PUL-114 covers a ledger *and* the swap of two read paths, plus two new exposed fields.
Change A is pure Python, fully TDD-able, and cannot move a production number — every
consumer must produce byte-identical results to today. Change B carries all the
behavioural risk (historical curve, P&L reconciliation against the XTB statement) with
a small diff. Splitting keeps the reconciliation attributable to one cause.

### Correction to the ticket's map of the problem

The ticket names two time-blind sources and locates `ops_basis` in both
`get_portfolio_calendar_data` and `get_portfolio_history`. Verified against the code:

- `get_portfolio_calendar_data` (`db/bigquery.py:390`) **does not use a cost basis at
  all** — it computes `shares_on_day × zmiana_kwotowa`, the daily move, not P&L against
  purchase. The calendar is out of scope.
- `ops_basis` exists **only** in `get_portfolio_history` (`db/bigquery.py:873`).
- There is a **third** undated FIFO lot ledger the ticket does not mention:
  `src/brokers/xtb.py:209` (`reconstruct_positions`, `_Lot(shares, unit_price)`). It is
  what writes `avg_buy_price`, so that column is already a FIFO-remaining basis as of
  import time — undated, therefore still wrong backwards, but not the plain average the
  ticket describes. Whether the measured SNT 284.28 came from `ops_basis` rather than
  from the column is a research question for part 2.

Building a fourth ledger would be the wrong move. One module, three consumers.

### Consumers rebuilt here

| consumer | today | after |
| -- | -- | -- |
| `src/portfolio_realized.py:102` | `lots[ticker].append([volume, price])` | shared ledger; gains days-held |
| `src/brokers/xtb.py:224` | `_Lot(shares, unit_price)` | shared ledger; behaviour unchanged |

### Decisions taken with the user before planning (2026-08-04)

1. **Split into two changes / two PRs** onto one release branch.
2. **Residual shares** (held on a day but not covered by any lot — spin-offs,
   hand-entered positions, an export starting after the purchase) are priced at the
   stored `avg_buy_price` as an explicit declared fallback. Never zero — a zero basis
   reads as 100% profit. Consumed in part 2; the ledger's job here is to *report* the
   uncovered quantity rather than to silently absorb it.
3. **Ledger feeds SQL as step-function segments** passed as an `UNNEST` array
   parameter — basis only changes on operation days. The valuation, LOCF/BOCF and the
   coverage gate stay in SQL untouched. Computed per request; no materialised table.
   (Applies to part 2; recorded here because it constrains the ledger's output shape.)
4. **`days_held` for a sale spanning several lots**: two scalars — a volume-weighted
   average and the figure from the oldest consumed lot. The oldest-lot number is what
   PUL-114's verification criterion asks for; the weighted one is what the question
   "how long did I hold this" actually means.

### Hard constraint for this change

`compute_realized_pnl` and `reconstruct_positions` must return the same numbers as
before, proven by the existing test suites. New fields may be added; existing ones may
not move.
