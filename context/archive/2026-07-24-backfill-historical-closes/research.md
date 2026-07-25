---
date: 2026-07-24T16:11:56+02:00
researcher: Claude (Fable 5)
git_commit: d1ff3bb0867de4c88232d67c17863e96eff43145
branch: master
repository: puls-gpw
topic: "PUL-92: Backfill historical daily closes (stooq, from 2026-01-01) into company_daily_stats + etf_quotes"
tags: [research, codebase, bigquery, company-daily-stats, etf-quotes, portfolio-history, backfill, stooq]
status: complete
last_updated: 2026-07-24
last_updated_by: Claude (Fable 5)
---

# Research: Backfill historical daily closes into company_daily_stats + etf_quotes (PUL-92)

**Date**: 2026-07-24T16:11:56+02:00
**Researcher**: Claude (Fable 5)
**Git Commit**: d1ff3bb0867de4c88232d67c17863e96eff43145
**Branch**: master
**Repository**: puls-gpw (github.com/radoslawjjd-design/puls-gpw)

## Research Question

What does the codebase need for a one-time script `scripts/backfill_historical_closes.py` that backfills daily closing prices from 2026-01-01 into `company_daily_stats` and `etf_quotes` (stooq.pl CSV as primary source), so `GET /api/portfolio/history?range=1y` returns a full year and the calendar heatmap has data? Covers: table schemas, MERGE upsert reuse, consumer-query requirements, ticker universe & symbol mapping, `scripts/` + HTTP conventions, and prior decisions (PUL-79/54/67/90/91, company-stats-upsert).

## Summary

**The write path already exists and is directly reusable.** Both tables have idempotent temp-table + MERGE upserts keyed `(ticker, snapshot_date)` — `merge_company_daily_stats` (`db/bigquery.py:2450-2522`) and `merge_etf_quotes` (`db/bigquery.py:2671-2733`). No query changes are needed for the charts to benefit: the whole value-history stack (LOCF + full-coverage gate, ROW_NUMBER dedup, COALESCE company/etf) tolerates sparse history by design and will simply render more points once rows exist.

The five critical facts the plan must build around:

1. **MERGE MATCHED branch clobbers all columns** — feeding a date the daily job already populated would overwrite richer scraped data (`wartosc_obrotu`, `liczba_transakcji`) with NULLs. The backfill must bound its date range per ticker to dates **before the earliest existing row** (or globally before ingestion start).
2. **The trading-day spine comes from `company_daily_stats` ONLY** (`db/bigquery.py:510-514`, `400-404`). A date backfilled only into `etf_quotes` is invisible to both history and calendar. Stocks must be backfilled (or at least one `company_daily_stats` row per trading day must exist) for ETF history to show up.
3. **`zmiana_kwotowa` must be derived and populated** (`close_d − close_{d-1}`), not left NULL — the calendar heatmap consumes it directly (`db/bigquery.py:429-430`); a row with only `kurs_zamkniecia` shows portfolio value but contributes 0 PLN to daily P&L while still marking the day as "data".
4. **The calendar query is NOT duplicate-safe** (raw LEFT JOINs, no dedup — `db/bigquery.py:419-422`): duplicate `(ticker, snapshot_date)` rows fan out and double-count. History IS safe (QUALIFY ROW_NUMBER at `db/bigquery.py:526-530`). Strict MERGE idempotency + in-batch dedup are mandatory (MERGE itself errors if the source batch contains the same key twice).
5. **No stooq/bossa integration exists anywhere** — the source adapter is greenfield. No app-ticker↔stooq-symbol mapping exists; stooq symbol ≈ ticker lowercased, but ETF symbols especially need verification, misses must be logged + skipped.

The full-coverage gate means the backfill must cover **all held tickers** (stocks + ETFs) back to 2026-01-01 — a single uncovered ticker clamps the emitted series at that ticker's first price date.

## Detailed Findings

### 1. Target table schemas (`db/bigquery.py`)

**`company_daily_stats`** — schema at `db/bigquery.py:2338-2354`, DAY-partitioned on `snapshot_date`, clustered by `ticker` (`db/bigquery.py:2369-2373`):

| column | type | mode | backfill source |
|---|---|---|---|
| `ticker` | STRING | REQUIRED | app ticker (= `companies.ticker`) |
| `snapshot_date` | DATE | REQUIRED | stooq `Date` (ISO string) |
| `kurs_zamkniecia` | FLOAT64 | NULLABLE | stooq `Close` — **load-bearing** |
| `zmiana_procentowa` | FLOAT64 | NULLABLE | derivable from consecutive closes |
| `zmiana_kwotowa` | FLOAT64 | NULLABLE | derive `close_d − close_{d-1}` — **needed by calendar** |
| `kurs_otwarcia` | FLOAT64 | NULLABLE | stooq `Open` |
| `kurs_min` / `kurs_max` | FLOAT64 | NULLABLE | stooq `Low` / `High` |
| `wartosc_obrotu` | FLOAT64 | NULLABLE | NULL (turnover *value* PLN; stooq Volume is share count — not equivalent) |
| `liczba_transakcji` | INTEGER | NULLABLE | NULL (trade count unavailable) |
| `fetched_at` | TIMESTAMP | REQUIRED | backfill run timestamp (ISO UTC) |

**`etf_quotes`** — schema at `db/bigquery.py:2538-2552`, same partitioning/clustering (`db/bigquery.py:2580-2586`). Differences: has `kurs_odn` (reference/prior close) and `wolumen_skum` (cumulative volume — direct fit for stooq `Volume`) instead of `wartosc_obrotu`/`liczba_transakcji`. Sibling `etf_instruments` master table (`db/bigquery.py:2527-2536`) does not need touching.

Only `ticker`, `snapshot_date`, `fetched_at` are REQUIRED — everything else NULLABLE, and consumers only require `kurs_zamkniecia` (+ `zmiana_kwotowa` for calendar P&L).

### 2. MERGE upsert pattern — reusable as-is, with one caveat

- `merge_company_daily_stats(rows: list[dict])` — `db/bigquery.py:2450-2522`. Loads rows via `load_table_from_json` into temp table `company_daily_stats_tmp_<uuid8>` (WRITE_TRUNCATE, 24h expiry), then `MERGE ... ON T.ticker = S.ticker AND T.snapshot_date = S.snapshot_date`; temp table deleted in `finally`. Load job, not streaming insert — no streaming-buffer DML lock, one MERGE per run.
- `merge_etf_quotes(rows)` — `db/bigquery.py:2671-2733`, identical shape.
- **Clobber caveat**: `WHEN MATCHED THEN UPDATE` overwrites **all** value columns. A backfill row colliding with a scraped date would NULL-out `wartosc_obrotu`/`liczba_transakcji` (stocks) or degrade ETF fields. Plan options: (a) cap the backfill range at `MIN(snapshot_date)` per ticker (query existing data first), (b) global cap at ingestion start date, or (c) a dedicated MERGE variant with `WHEN NOT MATCHED THEN INSERT` only. Option (a) or (c) is safest.
- **In-batch dedup required**: MERGE fails with "UPDATE/MERGE must match at most one source row" if the source batch repeats a `(ticker, snapshot_date)` key.
- Do NOT use `batch_insert_company_daily_stats` (`db/bigquery.py:2431-2447`) — streaming insert, duplicates on re-run, streaming buffer blocks subsequent MERGE ~90 min.

### 3. Consumer queries — what backfilled rows must satisfy

**`get_portfolio_history`** (`db/bigquery.py:465-580`, SQL at 503-560):

- Prices unioned from both tables in `px_raw` (`:515-525`) with `src` marker; `QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker, snapshot_date ORDER BY src) = 1` (`:529`) prefers `company_daily_stats` and makes history duplicate-safe. Filter: `kurs_zamkniecia IS NOT NULL`.
- **Spine** = `SELECT DISTINCT snapshot_date FROM company_daily_stats` (`:510-514`) — etf_quotes-only dates don't exist as days.
- **LOCF** via `LAST_VALUE(px IGNORE NULLS)` per ticker (`:537-545`); price scan window reaches 400 days before `@start_date`.
- **Full-coverage gate**: `COUNTIF(px_ff IS NULL) AS missing` → `WHERE missing = 0` (`:551`, `:558`). Every held ticker needs a (carried) close for a day to be emitted — this is exactly why 1y is partial today, and why the backfill must cover the complete held-ticker set.
- Range → start_date computed in Python: `_HISTORY_RANGE_DAYS = {"1w": 7, "1m": 30, "3m": 90, "1y": 365}` (`src/api.py:258`), `_history_start_date` (`src/api.py:261-264`). **Even after backfill to 2026-01-01, the 1y window (today−365 ≈ 2025-07-24) will still lack its first ~5 months** — ticket scope is explicitly "from 2026-01-01"; the chart renders whatever points exist (no fixed point-count assumption, per PUL-89/91).
- `portfolio_id=all` sentinel (`src/api.py:324`, `1008-1018`) → `portfolio_filter` empty; no impact on backfill.

**`get_portfolio_calendar_data`** (`db/bigquery.py:362-462`, SQL at 398-439):

- `trading_days` also from `company_daily_stats` only (`:400-404`).
- `daily_change_pln = SUM(shares * COALESCE(cds.zmiana_kwotowa, etq.zmiana_kwotowa))` (`:415-416`, `:429-430`) — consumed directly, not derived at query time → **backfill must populate `zmiana_kwotowa`**.
- **No dedup** — raw LEFT JOINs (`:419-422`) fan out on duplicate keys and double-count value and P&L. Idempotency is a hard correctness requirement, not just hygiene.
- No LOCF/coverage gate here; day with any price = `state="data"` (`src/portfolio_calendar.py:90-97`, pnl at `:106`).

**API layer**: `GET /api/portfolio/history` (`src/api.py:994-1032`), `GET /api/portfolio/calendar` (`src/api.py:958-992`); both have a **5-minute in-memory cache** (`history:{user_id}:{portfolio_id}:{range}`) — post-backfill verification on prod may lag up to 300 s or need a restart.

### 4. Ticker universe & symbol mapping

- Positions live in `user_portfolio_positions` (`db/bigquery.py:651-662`): `user_id`, `ticker` (REQUIRED), `shares`, `avg_buy_price`, `portfolio_id`, … — **no instrument-type flag**; stock-vs-ETF is determined by which dimension table the ticker appears in (`companies` at `db/bigquery.py:1262-1271` vs `etf_instruments` with `instrument_type` ∈ {ETF,ETC,ETN} at `:2527-2536`).
- **No helper returns DISTINCT held tickers** — "held tickers only" universe (ticket's recommendation) needs a new `SELECT DISTINCT ticker FROM user_portfolio_positions` (verified absent). Full-universe alternative: `list_distinct_portfolio_tickers()` (`db/bigquery.py:2245`, companies ∪ etf_instruments); stocks-only: `list_distinct_tickers()` (`:2231`).
- To classify a held ticker as stock vs ETF for table routing, intersect with `companies` / `etf_instruments` (mirrors the consumers' union semantics; on both → company_daily_stats wins downstream anyway).
- **Ticker formats**: everything stored uppercase, bare, no `.WA` suffix. Stocks: `PKO`, `CDR`, `XTB`, `ALE`, `KGH` (`tests/e2e/conftest.py:91,148-187`). ETFs: `ETFBW20TR`, `ETCGLDRMAU`, `ETNVIRBTCP` — GPW short code doubles as name (`src/gpw_etf_metrics.py:94-95,114-116`).
- **No existing external-symbol mapping** (stooq/bossa/yahoo — verified absent). Note: `company_daily_stats.ticker` = `companies.ticker`, and the bankier scrape key is a *different* symbol derived via `symbol_from_hop_url` (`src/bankier_metrics.py:17-30`) — precedent that ticker↔source-symbol mapping is a per-source concern. Stooq symbol ≈ ticker lowercased (e.g. `pko`), ETFs uncertain; unmapped/missing symbols → log + skip.

### 5. Script + HTTP conventions

Canonical one-off pattern (`scripts/backfill_companies.py`, `scripts/reanalyze_failed.py`; 14 scripts total under `scripts/`):

- Module docstring with `Run with: uv run python scripts/<name>.py --dry-run`.
- `sys.path.insert(0, str(Path(__file__).parent.parent))` → `load_dotenv()` **before any `db.*`/`src.*` import** (env read at module import time — `db/bigquery.py:44`; lessons.md GCP rule). `load_dotenv()` bez ścieżki działa z cwd=repo root; ze scratchpada podać jawnie `C:/puls-gpw/.env` (memory gotcha).
- `argparse` with `--dry-run` (`action="store_true"`); ticket adds `--tickers` subset. Dry-run prints `[dry-run] would …` lines.
- `configure_logging()` from `src.logging_setup`, `%`-style lazy logging.
- BQ: call public wrappers; startup ritual = `create_*_table_if_not_exists()` + `ensure_*_schema_current()` (mirrors `company_stats_main.py:30-31`); client singleton `_get_client()` has the `with_quota_project` guard (`db/bigquery.py:85-105`).
- Rate-limiting precedent: `_BETWEEN_CALLS_S` + `time.sleep` between iterations (`scripts/reanalyze_failed.py:33,128-129`).
- **HTTP: `httpx` via `src/http_client.py`** — singleton `get(url)` with UA `Mozilla/5.0 (compatible; puls-gpw/1.0)`, `HTTP_TIMEOUT` (30 s), `HTTP_MAX_RETRIES` (3), `REQUEST_DELAY` (0.5 s) with escalating backoff, raises `ScraperError` (`src/http_client.py:12-56`). Route stooq CSV GETs through it + explicit inter-ticker sleep. Stooq CSV uses dot decimals — Polish-number helpers (`src/bankier_metrics.py:33-54`) not needed.

## Code References

- `db/bigquery.py:2338-2354` — `company_daily_stats` schema; `:2369-2373` partitioning/clustering
- `db/bigquery.py:2538-2552` — `etf_quotes` schema; `:2580-2586` partitioning
- `db/bigquery.py:2450-2522` — `merge_company_daily_stats` (temp table + MERGE ON ticker+snapshot_date)
- `db/bigquery.py:2671-2733` — `merge_etf_quotes` (same pattern)
- `db/bigquery.py:2431-2447` — `batch_insert_company_daily_stats` (streaming — do NOT use)
- `db/bigquery.py:465-580` — `get_portfolio_history` (spine `:510-514`, dedup `:526-530`, LOCF `:537-545`, gate `:551,:558`)
- `db/bigquery.py:362-462` — `get_portfolio_calendar_data` (trading_days `:400-404`, zmiana_kwotowa `:415-416,:429-430`, dup-unsafe joins `:419-422`)
- `src/api.py:258-264` — range→start_date; `:994-1032` history endpoint (5-min cache `:1004-1007`); `:958-992` calendar endpoint
- `src/portfolio_calendar.py:26-127` — heatmap P&L consumption of `daily_change_pln`
- `db/bigquery.py:651-662` — `user_portfolio_positions` schema; `:2231` `list_distinct_tickers`; `:2245` `list_distinct_portfolio_tickers`; `:2387-2406` `list_companies_with_hop_info`
- `db/bigquery.py:44` — `_DATASET` read at import; `:85-105` `_get_client` quota guard; `:108-109` `_table_ref`
- `company_stats_main.py:7-31` — init order + create/ensure ritual; `:42-43` Warsaw snapshot_date / UTC fetched_at
- `etf_quotes_main.py:7-48` — ETF entry point (tickers from page, no master list)
- `src/http_client.py:12-56` — httpx singleton (UA, retry, delay)
- `src/bankier_metrics.py:17-30` — `symbol_from_hop_url` (per-source symbol-mapping precedent); `:57-113` `fetch_listing_page`
- `src/gpw_etf_metrics.py:56-140` — `fetch_etf_page` (ticker verbatim from GPW cell)
- `scripts/backfill_companies.py:14-43`, `scripts/reanalyze_failed.py:20-33,128-129` — script conventions

## Architecture Insights

- **Idempotent writes = temp-table load job + MERGE** keyed `(ticker, snapshot_date)`; chosen (company-stats-upsert, 2026-06-27) over DELETE+streaming-INSERT to be atomic and sidestep the streaming buffer. One MERGE per run, not per-row DML.
- **Sparse-history tolerance is baked into consumers**: ROW_NUMBER latest-per-ticker (PUL-54's "~31% of companies miss a day"), LOCF + full-coverage gate (PUL-79), COALESCE(company, etf) (PUL-67). Backfill only needs to land rows; zero query changes.
- **`company_daily_stats` is the calendar authority** — both consumers derive the set of trading days exclusively from it. Backfilling stocks is what creates the days; ETFs fill in on top.
- **Sql gotcha prior** (lessons.md): mocked BQ tests don't parse SQL — any new/changed hand-built SQL needs a round-trip on real BQ (`scripts/test_bq*.py` precedent); backtick reserved-keyword columns.
- **Prod writes are human-run** (CLAUDE.md project rule + ticket): script must be verifiable via `--dry-run` and `--tickers` subset first.

## Historical Context (from prior changes)

- `context/archive/2026-07-22-pul-79-portfolio-value-history/reviews/plan-review.md:26-43` + `plan.md:100-122` — **0-fill rejected** (fake ~25% value step on 9/12-coverage days); LOCF + full-coverage gate chosen. `research.md:81-88` — "today's share counts × historical close" approximation accepted & documented (no transaction dates stored); PUL-92 inherits it: pre-split/pre-purchase holdings will misvalue — accept & document.
- `context/archive/2026-07-22-pul-79-portfolio-value-history/reviews/impl-review-phase-1.md:32-40` — LOCF edge: delisted/halted ticker's close carried flat up to 400 days; recorded, accepted.
- `context/archive/2026-06-27-company-stats-upsert/plan.md:36-46,76-80` — MERGE decision, temp-table mechanics, async load-job `.result()` + `.errors` check.
- `context/archive/2026-06-25-daily-company-stats-snapshot-ingestion/plan.md:94-108,394-405` — schema origin, ROW_NUMBER rule, pivot from per-company JSON API (31% gaps, 15 min) to listing pages (6 s); *"no backfill needed — history accrues from first run"* = the exact gap PUL-92 closes.
- `context/archive/2026-06-29-pul-67/plan.md:59-61,99-118,140-168` — `etf_quotes` origin; **`zmiana_kwotowa` derived (`kurs_odn × pct / 100`), never scraped** — precedent for deriving it in the backfill; GPW em-dash `—` = no trade = NULL.
- `context/archive/2026-07-24-pul-91-kalendarz-wszystkie-chart-dynamic-titles/research.md:217-220` + `2026-07-22-pul-89-portfolio-value-history-frontend/plan-brief.md:67` — "1Y partial, backfill is a separate ticket" = PUL-92's direct antecedent; frontend renders whatever point count arrives.
- **Zero mentions of stooq/bossa anywhere in the archive** — source adapter is greenfield.

## Related Research

- `context/archive/2026-07-22-pul-79-portfolio-value-history/research.md` — value-history data-source analysis (snapshot table rejected)
- `context/archive/2026-06-25-daily-company-stats-snapshot-ingestion/research.md` — company_daily_stats ingestion research
- `context/archive/2026-07-24-pul-91-kalendarz-wszystkie-chart-dynamic-titles/research.md` — current chart/range behavior

## Open Questions

1. **ETF quote retention** — memory notes "kursy ETF wygasały po 7 dniach" (pre-scheduler); archive documents only the 24h temp-table expiry, no table-level expiry. **Verify on prod** (`bq show --project_id=puls-gpw` partition expiration on `etf_quotes`) before assuming backfilled ETF history persists. Likely the 7-day figure was a freshness *filter* in the positions query, but confirm.
2. **Stooq symbol coverage for GPW ETFs** — `ETFBW20TR`-style codes may map to different stooq symbols (e.g. `etfbw20tr.pl`? Beta ETF naming?). Needs empirical verification with a small `--tickers` run; bossa.pl EOD as gap-fill.
3. **Stooq rate limits / daily download cap** — stooq enforces an unauthenticated daily download limit (returns "Przekroczony dzienny limit wywolan" text instead of CSV). Script must detect non-CSV responses and fail loudly, not write garbage.
4. **Collision policy** — cap backfill at per-ticker `MIN(snapshot_date)` vs global ingestion-start date vs insert-only MERGE variant (decision for /10x-plan; per-ticker MIN or insert-only variant safest given the clobber caveat).
5. **Universe** — held tickers only (ticket recommendation; needs new DISTINCT query) vs full universe (~570 companies — stooq rate limits make this expensive). Decision for /10x-plan.
6. **`zmiana_procentowa` too?** — trivially derivable alongside `zmiana_kwotowa`; nothing in history/calendar consumes it, but positions views might for day-change display. Cheap to include for consistency.
