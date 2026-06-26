---
date: 2026-06-25T15:23:19+02:00
researcher: Radek (via Claude Code)
git_commit: f446735d460e590a252ed92dbe1e38ebb3bedc94
branch: radoslawjjd/pul-54-daily-company-stats-snapshot-ingestion-append-only-per
repository: radoslawjjd-design/puls-gpw
topic: "Daily company-stats snapshot ingestion — append-only per-ticker trading data (PUL-54)"
tags: [research, codebase, companies, watchlist, portfolio-snapshots, bankier, cloud-run-jobs, cloud-scheduler]
status: complete
last_updated: 2026-06-25
last_updated_by: Radek (via Claude Code)
---

# Research: Daily company-stats snapshot ingestion (PUL-54)

**Date**: 2026-06-25T15:23:19+02:00
**Researcher**: Radek (via Claude Code)
**Git Commit**: f446735d460e590a252ed92dbe1e38ebb3bedc94
**Branch**: radoslawjjd/pul-54-daily-company-stats-snapshot-ingestion-append-only-per
**Repository**: radoslawjjd-design/puls-gpw

## Research Question

How should PUL-54 — a daily job that hops to each active ticker's `hop_url`, scrapes
trading-data fields (kurs odniesienia, kurs otwarcia, min/max, wolumen, wartość obrotu,
liczba transakcji, stopa zwrotu 1R, kapitalizacja, rynek, system), and appends one row per
ticker per day to a new `company_daily_stats` table — be planned, given the existing
`companies`/`hop_url` infrastructure (PUL-53), the existing watchlist/portfolio tables that
define the active-ticker set, and the already-deployed Cloud Run Job + Cloud Scheduler infra?

## Summary

The ticket's own design section (schema, append-only pattern, idempotency guard via
`INSERT ... WHERE NOT EXISTS`) is already well-specified and directly modeled on two existing
tables in the codebase. Three findings sharpen or correct that design:

1. **The "http hop" page is not parseable HTML for these fields — it's a separate JSON API.**
   PUL-54's first listed open question ("exact HTML structure of the hop page") has a
   definitive answer from PUL-53's own archived research: the static `hop_url` page renders
   all trading-data fields **empty server-side** (JS-filled client-side). The real source is a
   public, unauthenticated JSON endpoint, already verified live for two instruments:
   `GET https://api.bankier.pl/quotes/public/company-profile-chart/{ISIN}/?symbols={SYMBOL}&metrics=true&today=true`
   — and its verified field names match PUL-54's design list almost exactly (`Kurs_odniesienia`,
   `Kurs_otwarcia`, `Minimum`, `Maximum`, `Wolumen_obrotu_szt`, `Wartosc_obrotu_zl`,
   `Liczba_transakcji`, `Stopa_zwrotu_1R`, `Kapitalizacja`, `Rynek`, `System_notowan`). No
   BeautifulSoup/HTML parsing is needed for this job — `src/http_client.py`'s `get()` is still
   reusable, calling `.json()` on the response instead of feeding `.text` to BeautifulSoup.

2. **The ticket's "`list_watchlist_tickers` (all clients)" assumption doesn't match the actual
   function signature.** `list_watchlist_tickers(client_id: str)` (`db/bigquery.py:439-460`) is
   scoped to one client — there is no existing "all clients, distinct tickers" query. The plan
   needs either a new `list_all_watchlist_tickers()` (simple `SELECT DISTINCT ticker FROM
   watchlist`) or an inline query; this is a small gap, not a blocker.

3. **The active-ticker set's "portfolio" half needs an explicit reading recipe.** There's no
   single "all portfolio tickers" function either. Portfolio tickers live inside
   `positions_json` (a JSON string column) on the latest `portfolio_snapshots` row per wallet
   (`main`, `ikze` — the two wallets the admin treemap reads, `src/api.py:120-131`). Building
   the ticker-set query means: for each of the two wallets, call
   `get_latest_snapshot_for_wallet(wallet)` (`db/bigquery.py:313-348`), parse `positions_json`,
   extract `ticker` per position, union with watchlist tickers, dedupe.

The Cloud Run Job + Cloud Scheduler infra, the per-ticker skip+log failure pattern, and the
`hop_url`/`isin` lookup gap (no `get_company(ticker)` exists yet) are all confirmed reusable —
covered in detail below.

## Detailed Findings

### A. `companies`/`hop_url` — what exists and what the hop actually returns

- `companies` schema (`db/bigquery.py:465-472`): `ticker`, `name`, `hop_url`, `isin`,
  `created_at`, `updated_at` — no price/trading-data columns.
- `upsert_company()` (`db/bigquery.py:497-536`) is the MERGE-on-`ticker` pattern to copy for
  any new per-ticker upsert; not directly applicable here since PUL-54 is append-only, but its
  query-parameter/error-handling shape is the template for the new `insert_company_daily_stats`
  function.
- `list_distinct_tickers()` (`db/bigquery.py:1189-1201`) returns every ticker in `companies`
  (i.e. every ticker ever seen in an announcement) — broader than the watchlist∪portfolio
  active-ticker set PUL-54 wants; not the right source for "which tickers to fetch today."
  **No `get_company(ticker)` single-row lookup exists** — the new job needs one (or an inline
  `WHERE ticker = @ticker` query) to resolve `hop_url`/`isin` per active ticker.
- `hop_url` values are bankier.pl profile pages:
  `https://www.bankier.pl/inwestowanie/profile/quote.html?symbol={SYMBOL}`. The bankier
  `symbol` query param is **not always the same string as the GPW ticker** (confirmed live:
  ECHO ticker `ECH` vs. bankier symbol `ECHO`; MOL ticker = MOL symbol — same). Since `hop_url`
  stores the full URL, `symbol` should be parsed back out of its query string, never derived
  from `ticker`. `isin` is already its own column, usable directly in the JSON API path.
- **Critical finding** (`context/archive/2026-06-23-companies-dictionary-table/frame.md:120-150`,
  on `master`): the static `hop_url` page's `div.m-quotes-metric-table__data` renders **empty**
  server-side — confirmed live for two different instruments. `src/company_profile.py`'s
  BeautifulSoup approach only reads the static heading + `data-isin`/`data-symbol` attributes;
  it cannot be extended to read any trading-data field.
- The verified JSON endpoint is:
  `GET https://api.bankier.pl/quotes/public/company-profile-chart/{ISIN}/?symbols={SYMBOL}&metrics=true&today=true`
  (`today=true` required — omitting it returns a multi-day aggregate instead of the live
  session snapshot). Verified fields: `Kurs_odniesienia`, `Kurs_otwarcia`, `Minimum`, `Maximum`
  (+ `_data` timestamps), `Wolumen_obrotu_szt`, `Wartosc_obrotu_zl`, `Liczba_transakcji`,
  `Stopa_zwrotu_1R`, `Kapitalizacja`, `Rynek`, `System_notowan` — a 1:1 match to PUL-54's design
  field list (`kurs_odniesienia`, `kurs_otwarcia`, `kurs_min`/`kurs_max`, `wolumen_obrotu`,
  `wartosc_obrotu`, `liczba_transakcji`, `stopa_zwrotu_1r`, `kapitalizacja`, `rynek`, `system`).
- `src/http_client.py:34-56` (`get()`) returns a raw `httpx.Response` with retry/backoff (3
  attempts, exponential delay, 30s timeout) — directly reusable via `resp.json()`. No new HTTP
  dependency needed.
- No code anywhere in the repo currently parses any of these trading-data fields — confirmed by
  whole-repo search. This is new ground, not an extension of existing parsing logic.

### B. Active-ticker set — watchlist ∪ portfolio, both sides need new read paths

- `watchlist` schema (`db/bigquery.py:351-357`): `client_id`, `ticker`, `added_at`.
  `add_watchlist_ticker()` (`db/bigquery.py:382-410`) uses an `INSERT ... WHERE NOT EXISTS`
  idempotency guard — this is the exact pattern PUL-54's design doc points to for the
  `(ticker, snapshot_date)` dedup guard on the new table.
  `list_watchlist_tickers(client_id)` (`db/bigquery.py:439-460`) is **per-client** — there is no
  all-clients variant in the codebase today; the new job needs a small new query (`SELECT
  DISTINCT ticker FROM watchlist`) or equivalent.
- `portfolio_snapshots` schema (`db/bigquery.py:191-201`): `snapshot_id`, `wallet`
  (`main`/`ikze` are the two read by the admin treemap, `src/api.py:120-131`), `snapshot_date`,
  `total_value`, `currency`, `day_change_abs`, `day_change_pct`, `positions_json`,
  `created_at`. `positions_json` shape: `{"positions": [{"ticker", "value", "pct"}, ...],
  "media_attached": bool}` — tickers live inside this JSON blob, not as queryable rows.
  `get_latest_snapshot_for_wallet(wallet)` (`db/bigquery.py:313-348`) returns the newest row per
  wallet; the new job parses `positions_json` from each wallet's latest row to get that wallet's
  current tickers.
- Combining the two: active-ticker set = `DISTINCT ticker` from `watchlist` ∪ tickers parsed out
  of `positions_json` for each of `main`/`ikze`'s latest `portfolio_snapshots` row. Both halves
  need to skip tickers missing from `companies` (no `hop_url` to hop to) — log and continue,
  matching the per-ticker failure pattern below.

### C. Cloud Run Job + Cloud Scheduler infra — directly reusable, fully documented

- Project `puls-gpw`, region `europe-central2`. Two existing Cloud Run Jobs share one Docker
  image: `puls-gpw` (scraper, `main.py`, every 15 min) and `puls-gpw-post` (X-post generator,
  `post_main.py`, 08:30/13:00/17:30 Warsaw weekdays) — `context/foundation/infra.md:7-12,53-60`.
- All Cloud Scheduler crons run on Warsaw time (not UTC); the 08:30/13:00/17:30/`*/15` slots are
  the only ones in use today — any new daily slot (e.g. once after 17:00 close, weekdays) is
  free of collisions.
- Entry-point pattern, identical across all three existing entrypoints: `load_dotenv()` as the
  very first import-time action, before any `db.bigquery`/GCP import (`main.py:5-14`) — per
  `.claude/rules/db-bigquery.md`, `BIGQUERY_DATASET`/`GOOGLE_CLOUD_PROJECT` are read at import
  time, so this ordering is load-bearing, not stylistic.
- `Dockerfile` (repo root): `python:3.13-slim` + `uv sync --frozen --no-dev`; `CMD` is overridden
  per-job via `gcloud run jobs update --command=uv --args="run,--no-dev,python,<entry>.py"`.
- `.github/workflows/deploy.yml:47-63` shows the exact CI pattern for an existing job: one
  `gcloud run jobs update <job-name> --image=... --command=uv --args=...` step per job, run on
  every push to `master`. A **brand-new** job needs one-time, human-run
  `gcloud run jobs create` + `gcloud scheduler jobs create http` (per `CLAUDE.md`: new/destructive
  infra provisioning is human-only) before CI's per-push update step can target it.
- Per-ticker failure isolation pattern, established in `main.py:55-101`: inner try/except per
  item, `logger.warning(...)`/`logger.exception(...)` + `continue` on non-critical failures
  (e.g. a best-effort BQ write), re-raising only `BigQueryError` from the core write path. PUL-54
  should copy this shape for per-ticker scrape failures — confirmed direction, not an open
  question (matches the ticket's own "skip and continue" framing).

## Code References

- `db/bigquery.py:191-201` — `portfolio_snapshots` schema (tickers live inside `positions_json`)
- `db/bigquery.py:313-348` — `get_latest_snapshot_for_wallet()` / `get_latest_snapshot_before()`
- `db/bigquery.py:351-357` — `watchlist` schema
- `db/bigquery.py:382-410` — `add_watchlist_ticker()` — idempotency guard pattern (`INSERT ...
  WHERE NOT EXISTS`) PUL-54's `(ticker, snapshot_date)` dedup should mirror
- `db/bigquery.py:439-460` — `list_watchlist_tickers(client_id)` — per-client, no all-clients
  variant exists yet
- `db/bigquery.py:465-472` — `companies` schema (`hop_url`, `isin`, no trading-data columns)
- `db/bigquery.py:497-536` — `upsert_company()` — MERGE pattern, query-param/error-handling
  template for the new insert function
- `db/bigquery.py:1189-1201` — `list_distinct_tickers()` (all `companies` tickers — broader than
  the active-ticker set PUL-54 needs)
- `db/bigquery.py:1220-1241` — `list_tickers_missing_from_companies()` (pattern for "ticker
  present but no hop_url" detection)
- `src/api.py:120-131` — `_TREEMAP_WALLETS` (`main`, `ikze` — the two wallets with portfolio data)
- `src/company_profile.py:1-90` — full file; static-page parsing pattern, **not reusable** for
  trading-data fields
- `src/http_client.py:34-56` — `get()`, reusable for the JSON endpoint via `.json()`
- `main.py:5-14,50-101` — `load_dotenv()` placement + per-item skip+log error-handling pattern
- `context/foundation/infra.md:7-60` — existing Cloud Run Jobs + Cloud Scheduler cron table
- `.github/workflows/deploy.yml:47-63` — CI pattern for updating a Cloud Run Job
- `.claude/rules/db-bigquery.md` — `load_dotenv()` ordering + `with_quota_project` guard rules

## Architecture Insights

- **Static-page scraping vs. JSON API is a project-wide correction, not just for PUL-61.**
  PUL-54's own ticket text says "exact HTML structure... needs a real sample URL to design the
  parser against" — but there is no HTML to parse for these fields. The httpx client
  (`src/http_client.py`) is shared infrastructure; the parsing layer is a small new
  JSON-response module, not a BeautifulSoup extension.
- **The active-ticker set has two missing read-helpers, not zero.** Both `watchlist` (per-client
  only) and `portfolio_snapshots` (tickers embedded in JSON, not queryable as rows) need a small
  new query/parse step to produce a flat distinct-ticker list. Neither is a schema change — both
  are additive read-side helpers.
- **One shared scraper module would serve PUL-54 and PUL-61 (Backlog, related ticket) if PUL-61
  is ever picked up.** PUL-61 (separately tracked) wants a treemap-refresh job that hits the
  exact same `hop_url` → ISIN/symbol → bankier JSON API path for a narrower purpose (one price
  field vs. PUL-54's full field set). Worth keeping the JSON-fetch function's boundary
  (`src/bankier_metrics.py` or similar — fetch + parse, returning the full verified field set)
  generic enough that a future, separate ticket could call it for a subset of fields without
  duplicating the API client. This is a forward-compatibility note, not a requirement — PUL-54
  should not be scoped or delayed by PUL-61.

## Historical Context (from prior changes)

- `context/archive/2026-06-23-companies-dictionary-table/frame.md` (on `master`) — PUL-53's
  frame brief; contains the live-verified bankier JSON API endpoint and field list this job
  depends on, plus the "static page renders empty" finding.
- `context/archive/2026-06-23-companies-dictionary-table/plan.md:10` — explicitly names "the
  daily company-stats ingestion job (separate ticket)" as the reason `hop_url` (and `isin`) was
  added to `companies` — i.e. PUL-53 → PUL-54 was the intended lineage from the start.

## Open Questions

1. **Ticker drop-out behavior**: when a ticker is removed from every watchlist and isn't held in
   any portfolio anymore, should the daily job stop fetching it (smaller daily cost, but loses
   continuity for any later analysis), or keep fetching it for some retention window (continuity,
   but unbounded growth of "stale" tickers being fetched forever)? Listed as open in the Linear
   ticket itself — `/10x-plan` must pick one.
2. **New `get_company`/`hop_url` lookup shape**: add a single `get_company(ticker) -> dict |
   None` function, or batch-fetch `hop_url`/`isin` for the whole active-ticker set in one query
   before the per-ticker loop? Affects BQ query count per run (N single-row queries vs. 1 batch
   query) — `/10x-plan` should decide given GPW's ticker count is low-volume either way.
3. **All-clients watchlist query**: add `list_all_watchlist_tickers()` to `db/bigquery.py`
   following the existing function-per-table-operation convention, or inline the `SELECT DISTINCT
   ticker FROM watchlist` query directly in the new job's module? Minor, but affects where the
   new code lives.
