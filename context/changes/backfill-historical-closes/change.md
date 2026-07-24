---
change_id: backfill-historical-closes
title: Backfill historical daily closes (stooq) into company_daily_stats + etf_quotes
status: planned
created: 2026-07-24
updated: 2026-07-24
archived_at: null
tracking:
  linear: PUL-92
  github: 182
---

## Notes

PUL-92: Backfill historical daily closes (stooq, from 2026-01-01) into company_daily_stats + etf_quotes; tracking linear=PUL-92 github=182

Key points from the Linear issue (https://linear.app/puls-gpw/issue/PUL-92):

- **Problem**: `company_daily_stats` (akcje/NC) and `etf_quotes` (ETF) are fed by daily scrapers that only see the current day — no history before ingestion start (~mid-2026). `get_portfolio_history` values holdings against `kurs_zamkniecia` per trading day, so `range=1y` returns a partial year by design.
- **Goal**: backfill daily closes from 2026-01-01 to ingestion start, keyed `(ticker, snapshot_date)`. Unblocks the 1R range in PUL-89/PUL-91 charts.
- **Source**: stooq.pl CSV primary (`https://stooq.pl/q/d/l/?s=<symbol>&d1=20260101&d2=<end>&i=d`, symbol ≈ ticker lowercased; rate-limit gently); bossa.pl EOD as cross-check/gap-fill.
- **Design decisions for /10x-plan**: (1) universe — held tickers only (recommended) vs full universe; (2) app-ticker ↔ stooq symbol mapping, log+skip misses; (3) raw (unadjusted) closes — accept & document the PUL-79 "today's shares × historical close" approximation; (4) idempotent MERGE upsert keyed `(ticker, snapshot_date)`; (5) trading days only (stooq omits non-trading days); decide whether to also derive `zmiana_kwotowa` = close_d − close_{d-1} for the calendar heatmap.
- **Execution**: one-time script `scripts/backfill_historical_closes.py` (not a scheduled job) with `--dry-run` and `--tickers` subset; prod BQ write is a human-run step.
- **Acceptance**: rows back to 2026-01-01 for targeted tickers; `?range=1y` ~full year; idempotent re-runs; unmapped symbols logged not dropped.
- **References**: consumers `db/bigquery.py:get_portfolio_history` / `get_portfolio_calendar_data`; current-day-only sources `src/bankier_metrics.py:fetch_listing_page`, `src/gpw_etf_metrics.py:fetch_etf_page`; MERGE upsert pattern in `db/bigquery.py` (company-stats).
