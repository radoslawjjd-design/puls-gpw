# Backfill Historical Daily Closes (stooq) — Plan Brief

> Full plan: `context/changes/backfill-historical-closes/plan.md`
> Research: `context/changes/backfill-historical-closes/research.md`

## What & Why

One-time script that backfills **full daily price history** (back to each ticker's first listing) from stooq.pl into `company_daily_stats` and `etf_quotes` for every ticker known to BQ. Today both tables only have data forward from ~mid-2026, so the 1R (1y) range in the portfolio value charts (PUL-89/91) returns a partial year and the calendar heatmap has no P&L before ingestion start. Linear PUL-92 / GitHub #182.

## Starting Point

Idempotent MERGE write paths and all consumer queries already exist — but the existing MERGE's MATCHED branch overwrites every column, so it would clobber scraped rows. No stooq integration exists anywhere in the repo. Source mechanics were verified live: CSV endpoint format, `&o=1111111` for raw (unadjusted) prices, ETF symbols need a `.pl` suffix, and stooq guards access with a JS proof-of-work challenge + per-symbol page-visit requirement + daily download limit.

## Desired End State

Both tables hold full raw-close history with derived `zmiana_kwotowa`/`zmiana_procentowa`; scraped rows untouched; `?range=1y` returns a dense year; calendar shows P&L back through January 2026; re-runs are no-ops (auto-resume) and can never duplicate or overwrite.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Universe | All BQ tickers (`companies` ∪ `etf_instruments`, ~570+) | User decision — future-proof; every future position has history immediately | User |
| Date range | Full available history (d1=19900101) | Same request count as any shorter range; only file/table size differs | Plan |
| Raw vs adjusted | Raw, via `&o=1111111` | Matches scraper convention and actual share counts (ticket decision #3) | Research + verified live |
| Collision policy | New insert-only MERGE functions (no `WHEN MATCHED`) | Structurally impossible to clobber scraped rows; idempotent without date logic | Plan |
| Derived fields | `zmiana_kwotowa` + `zmiana_procentowa` (+ ETF `kurs_odn`) | Calendar heatmap consumes `zmiana_kwotowa` directly; consistency with scraper rows | Research |
| Limit handling | Auto-resume from BQ (rows `< 2026-06-01` = done marker) | Table is the single source of truth; clean abort on stooq denial, re-run next day continues | Plan |
| ETF source | stooq only (`.pl` suffix); bossa deferred | One adapter; empirical coverage check on sample before deciding bossa is needed | Plan |

## Scope

**In scope:** insert-only MERGE primitives + tests + real-BQ round-trip; the backfill script (stooq session with PoW solver, parser, symbol mapping, auto-resume, `--dry-run`/`--tickers`/`--limit`); human-gated rollout and API/UI verification.

**Out of scope:** bossa.pl adapter, consumer/frontend changes, scheduled jobs, `wartosc_obrotu`/`liczba_transakcji` backfill, transaction-date modeling, automated prod runs.

## Architecture / Approach

Script (`scripts/backfill_historical_closes.py`) → per ticker: stooq page GET + CSV GET (raw, full range) → parse + derive → chunked flush through `merge_*_insert_only` → BQ. Resume state is derived from BQ itself (pre-June-2026 rows = backfilled). Consumers pick everything up unchanged.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Insert-only MERGE | Safe write primitive + unit/SQL-regression tests + real-BQ round-trip | Hand-built SQL — mitigated by round-trip on throwaway table |
| 2. Backfill script | Full fetch/parse/flush pipeline with resume + limit abort | Stooq anti-bot changes behavior mid-implementation |
| 3. Rollout (human) | Sample audit → full run(s) → dense 1y + calendar P&L on prod | Daily download limit stretches run over multiple days |

**Prerequisites:** ADC for real-BQ round-trip; human availability for prod runs; confirm `etf_quotes` has no partition expiry before full run.
**Estimated effort:** ~2 sessions (Phases 1-2) + multi-day human-run rollout (Phase 3).

## Open Risks & Assumptions

- Stooq's PoW challenge/anti-bot may evolve or the daily limit may be lower than expected — script aborts cleanly and resumes, but full-universe completion date is not guaranteed.
- Unknown-symbol rate for ETFs/NewConnect is unmeasured — misses are logged + skipped; bossa follow-up only if material.
- ~~Unconfirmed memory of a 7-day ETF-quote expiry~~ — resolved in plan-review: it's a freshness *filter* in `list_user_portfolio_positions` (`db/bigquery.py:832,842`), not a table expiry; the cheap `bq show` check before the full run stays as a formality.

## Success Criteria (Summary)

- `?range=1y` returns a dense year on prod; calendar heatmap shows P&L for backfilled months.
- Scraped rows byte-identical before/after; zero duplicate `(ticker, snapshot_date)` pairs.
- Re-run of the script reports "0 remaining" and inserts nothing new.
