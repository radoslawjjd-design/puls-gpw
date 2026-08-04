---
change_id: dividends-realized-month-selector
title: Add a month selector alongside the year filter in Dywidendy and Zrealizowane
status: new
created: 2026-08-04
updated: 2026-08-04
archived_at: null
tracking:
  linear: PUL-120
  github: 239
---

## Notes

PUL-120 — the **Dywidendy** and **Zrealizowane** views each expose a single
period selector (`Wszystkie` plus one option per year). Add a **month** selector
alongside it, with `Wszystkie` available in either selector independently.

All four combinations must work, including `Wszystkie` year + a single month
("every March on record") — the ticket recommends allowing it rather than
disabling the control conditionally.

Three traps the ticket calls out explicitly:

1. **Realized filters the result, never the input.** `compute_realized_pnl`
   (`src/portfolio_realized.py:32`, doc at `:39-42`) walks the whole FIFO history
   first and only then drops sales outside the period. Filtering the operations up
   front strips the buys that priced the remaining shares, and every later sale
   reports a phantom profit at zero cost. The month filter must land at exactly
   the same point as the year filter.
2. **The month must join the cache key.** `_perf_get`/`_perf_set` key on
   `…:{parsed_year or 'all'}`; without the month, a March request served from a
   January entry is a silent wrong answer cached for 300 s.
3. **`EXTRACT` runs in UTC over a Warsaw-time event** (`db/bigquery.py:3894`). At
   year granularity this misfires once a year; at month granularity, twelve times
   as often. Extraction should be in `Europe/Warsaw`.

Two structural constraints that must survive:

- The `data` CTE stays grouped by ticker alone (`db/bigquery.py:3908-3910`) —
  adding a period column to the `GROUP BY` splits one holding into several rows.
- The `meta` CTE stays meta-first, `FROM meta LEFT JOIN data`
  (`db/bigquery.py:3884-3887`) — the other way round the selector empties as soon
  as the chosen period has no payouts, stranding the user on a period they cannot
  leave. A month filter makes empty periods far more common, so this gets sharper.

Out of scope: the calendar, which has its own month navigation.
