---
change_id: history-coverage-gate
title: Full-coverage gate collapses the value-history range when a holding is a recent IPO
status: plan_reviewed
created: 2026-07-26
updated: 2026-07-26
archived_at: null
tracking:
  linear: PUL-100
  github: 195
---

## Notes

`get_portfolio_history` (`db/bigquery.py:465`) emits a day only when **every** held
position has a forward-filled price that day. The series therefore starts at the
*latest* first-price date across all holdings — a sub-1% position in a freshly
listed company clamps the whole chart.

Baseline measured on real BigQuery before any change (2026-07-26, `?range=1y`):

| view | points | latency | span |
|---|---|---|---|
| Wszystkie | 71 | 1878 ms | 2026-04-16 → 2026-07-24 |
| Główny (13 positions) | 71 | 1616 ms | 2026-04-16 → 2026-07-24 |
| second portfolio (8 positions) | 249 | 1371 ms | 2025-07-28 → 2026-07-24 |

`S2B` (4 shares, 267 PLN, 0.66% of a 40 261 PLN portfolio, listed 2026-04-16) is
the clamp. Partition scan for the 1y+400d window is 2.5 MB — the performance
concern raised in PUL-100 point 3 is already answered: clustering holds despite
`company_daily_stats` growing from 16k to 1.9M rows in PUL-92. No optimisation needed.

Design decisions and rejected alternatives (backfill at `avg_buy_price`, skip
missing positions) are argued in the Linear ticket; the plan carries them forward
rather than re-deriving them.
