---
change_id: fifo-basis-on-read-paths
title: Swap the read paths onto the dated lot ledger and retire ops_basis
status: plan_reviewed
created: 2026-08-05
updated: 2026-08-05
archived_at: null
tracking:
  linear: PUL-114
  github: 229
---

## Notes

Part 2 of PUL-114 (Linear PUL-114, GitHub #229). Part 1 (`fifo-lot-ledger`, branch
`feat/pul-114-fifo-lot-ledger`, PR #248) built `src/portfolio_lots.py` and moved both
Python lot consumptions onto it **without moving a single production number**. This
change is where the numbers move.

### What this change carries

1. **`get_portfolio_history` stops applying a flat basis backwards.** Today one constant
   cost basis is applied to every historical day. Measured on production, that overstates
   the portfolio's cost basis by up to **+2 144,85 PLN (+8,41%)** on 2025-10-13,
   consistently signed — so the history curve systematically *understates* past P&L.
   Worst single ticker-day: PAS +1 299,71 on 2025-12-10.
2. **`ops_basis` is deleted** (`db/bigquery.py:873`). It is separately wrong for 8 of 20
   sold-to-zero tickers — BAC +11,33%, TOR +9,47%, LPP +4,96%.
3. **Acquisition dates reach the endpoint** (`first_buy_date`), which is what unblocks
   PUL-123 part 2.

### Decisions already taken with the owner (2026-08-04, before part 1)

- **Residual shares** — held on a day but covered by no lot (spin-offs, hand-entered
  positions, an export window starting after the purchase) — are priced at the stored
  `avg_buy_price` as an **explicit declared fallback**. Never zero: a zero basis renders
  as 100% profit. Part 1 built the reporting side of this (`Sale.uncovered`,
  `TickerLedger.uncovered`); this change consumes it.
- **The ledger feeds SQL as step-function segments** passed as an `UNNEST` array
  parameter — the basis only changes on operation days, so the segments are few. The
  valuation, LOCF/BOCF fill and the PUL-100 coverage gate stay in SQL, untouched.
  Computed per request; **no materialised table**.

### Decisions taken with the owner after research (2026-08-05)

3. **`first_buy_date` is computed per request from the ledger**, not stored at import.
   Same rule as the basis segments — no materialised column, no backfill, and the date
   can never drift out of step with the basis it belongs to. Costs one 508-row query on
   an endpoint that caches for 30 s.
4. **"Wszystkie" merges `first_buy_date` as the earliest open lot** across the merged
   wallets — it answers "since when do I hold any of this", and unlike a share-weighted
   date it corresponds to a purchase that actually happened and can be checked by hand.
5. **The curve's correction ships silently.** The existing `notes` / `data_from`
   metadata describes *data coverage*; a methodology note would conflate two different
   kinds of caveat and would be noise a week after deploy.

### Invariant this change must not break

**PUL-100 right edge**: the history chart's right edge must equal today's reported P&L.
Part 1 measured today's portfolio-wide basis gap at exactly **+0,00**, so the right edge
holds by construction — but it is the first thing to verify, not to assume.

### Explicitly out of scope

- **The −4 482 PLN divergence against the XTB statement.** PUL-114 lists it as a
  verification criterion, but part 1 proved it cannot come from either basis source —
  today's gap is +0,00 on both wallets. Split out as **PUL-124**; do not chase it here.
- **`get_portfolio_calendar_data`** (`db/bigquery.py:390`). The ticket places `ops_basis`
  there too; it is not. The calendar computes `shares_on_day × zmiana_kwotowa` and
  carries no cost basis at all.
