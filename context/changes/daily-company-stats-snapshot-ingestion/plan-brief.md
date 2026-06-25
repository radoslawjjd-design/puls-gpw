# Daily company-stats snapshot ingestion — Plan Brief

> Full plan: `context/changes/daily-company-stats-snapshot-ingestion/plan.md`
> Research: `context/changes/daily-company-stats-snapshot-ingestion/research.md`

## What & Why

A daily Cloud Run Job (17:05 weekdays Warsaw time, right after GPW close) that fetches
trading-data metrics for every company in the `companies` table from bankier's public API and
appends one row per ticker per day to a new `company_daily_stats` table. This builds the
foundational per-company daily dataset the project doesn't have yet (PUL-53 built the `hop_url`
dictionary; this is its first consumer).

## Starting Point

`companies` (ticker, name, `hop_url`, `isin`) exists and is populated incrementally by the
scraper, but holds no trading-data fields. No daily-stats table exists anywhere. The static
`hop_url` page itself is JS-rendered and empty server-side — the actual data lives behind a
separate, already-verified bankier JSON API. Two Cloud Run Jobs + Cloud Scheduler entries
already run in production today, giving this job an exact infra pattern to copy.

## Desired End State

Every weekday evening, `company_daily_stats` gains one fresh row per company with a usable
`hop_url` — kurs odniesienia/otwarcia, min/max, wolumen, wartość obrotu, liczba transakcji,
stopa zwrotu 1R, kapitalizacja, rynek, system. A failed fetch for one ticker never blocks the
rest; re-running the same day is a safe no-op.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Active-ticker set | Every row in `companies` (not watchlist ∪ portfolio) | Simpler, removes the drop-in/drop-out problem entirely | Plan (user override of ticket's original design) |
| Data source | Bankier JSON API, not HTML scraping | The static `hop_url` page renders trading-data empty server-side | Research |
| Run time | 17:05 weekdays Warsaw | Right after GPW close; matches the related PUL-61 ticket's slot; no scheduler collisions | Plan |
| Same-day retry | None — append-only, no overwrite | Matches the ticket's explicit "append, never update" design | Plan |
| `hop_url`/`isin` lookup | One batch query for all companies | 1 BQ query instead of N at GPW's ticker count | Plan |
| Job placement | New standalone Cloud Run Job | Failure isolation; matches existing one-job-per-concern pattern | Plan |
| Fetch module scope | Full verified field set in one shared function | Keeps the door open for the related PUL-61 ticket without duplicating the API client | Plan |

## Scope

**In scope:**
- New `company_daily_stats` BigQuery table (partitioned/clustered, append-only, idempotent)
- New `src/bankier_metrics.py` fetch+parse module against bankier's JSON API
- New `company_stats_main.py` Cloud Run Job entrypoint with per-ticker skip+log isolation
- CI wiring (`deploy.yml`) + documented one-time manual infra provisioning runbook

**Out of scope:**
- Any change to `portfolio_snapshots` or the admin treemap (separate ticket, PUL-61)
- Watchlist/portfolio-scoped ticker filtering
- Same-day retry/overwrite of partial failures
- Actually provisioning live Cloud Run Job/Scheduler resources (human-only)

## Architecture / Approach

Four layers, built bottom-up: BigQuery schema + read/write functions → JSON fetch+parse module
→ entrypoint wiring the two together with per-ticker failure isolation → deployment wiring. Each
layer mirrors an existing, already-proven pattern in the codebase (the watchlist's idempotency
guard, the scraper's per-item skip+log loop, the post-job's entrypoint shape).

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. BigQuery layer | New table + batch read + idempotent insert | Partitioning/clustering is new ground in this codebase |
| 2. `bankier_metrics.py` | JSON fetch+parse against bankier's API | Field mapping was last verified live during PUL-53, not now |
| 3. `company_stats_main.py` | Cloud Run Job entrypoint, wired end-to-end | One bad ticker must not abort the whole run |
| 4. Deployment | CI step + manual provisioning runbook | Live infra creation is human-only and easy to forget before the next push |

**Prerequisites:** PUL-53 (Done) and PUL-28 (Done) — both already satisfied.
**Estimated effort:** ~1-2 sessions across 4 phases.

## Open Risks & Assumptions

- The bankier JSON API's field mapping (`Kurs_odniesienia` etc.) was verified live during PUL-53
  research, not during this plan — Phase 2's manual step re-confirms it before the entrypoint
  depends on it.
- Fetching daily stats for every company ever seen (not just watchlist/portfolio tickers) may
  grow `company_daily_stats` faster than originally scoped if `companies` grows quickly — judged
  acceptable at GPW's scale (a few hundred companies).

## Success Criteria (Summary)

- Every weekday after 17:05, `company_daily_stats` has one new row per company with a usable
  `hop_url`, with correct field values.
- A single ticker's fetch or write failure is logged and skipped without affecting any other
  ticker that run.
- Re-running the job for the same day never duplicates a row.
