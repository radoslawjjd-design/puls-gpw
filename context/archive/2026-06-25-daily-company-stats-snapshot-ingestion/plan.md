# Daily company-stats snapshot ingestion — Implementation Plan

## Overview

A daily Cloud Run Job (17:05 weekdays, Warsaw time — right after GPW close) that fetches
trading-data snapshots from bankier's HTML listing pages (one request each for GPW and
NewConnect), maps results to companies in the `companies` dimension table via the `symbol` param
in each company's `hop_url`, and writes all matched rows to `company_daily_stats` via a
delete-for-date + streaming batch insert. Runtime: ~6 seconds.

> **Implemented architecture note** (post-phase pivot): the original plan described a
> per-company bankier JSON API approach (`api.bankier.pl/quotes/.../{ISIN}/`). During
> implementation it was discovered that: (a) the JSON API returns empty `profile_data` for
> ~31% of companies that didn't trade that day, (b) the listing pages provide closing price +
> change fields not available in the JSON API, and (c) a listing-page approach reduces runtime
> from ~15 min (566 sequential DML queries) to ~6 seconds (2 HTTP fetches + 1 batch
> insert). The implemented schema and write strategy differ from the plan's Phase 1–2
> contracts — see the "Implemented Architecture" section below.

## Current State Analysis

- `companies` table (`db/bigquery.py:465-472`) has `ticker`, `name`, `hop_url`, `isin`,
  `created_at`, `updated_at` — populated incrementally by the scraper (`main.py:74`,
  `upsert_company()`) as new ESPI/EBI announcements are parsed. No price/trading-data columns
  exist anywhere in the schema today.
- No `company_daily_stats` (or any daily-stats) table exists yet.
- The static `hop_url` page is JS-rendered and empty server-side for trading-data fields
  (confirmed live, archived PUL-53 research,
  `context/archive/2026-06-23-companies-dictionary-table/frame.md:120-150`) —
  `src/company_profile.py`'s BeautifulSoup parser only reads the static heading +
  `data-isin`/`data-symbol`, not reusable here.
- The real data source is bankier's public, unauthenticated JSON API (verified live for two
  instruments): `GET https://api.bankier.pl/quotes/public/company-profile-chart/{ISIN}/?symbols={SYMBOL}&metrics=true&today=true`.
- `src/http_client.py`'s `get()` (httpx, retry/backoff, 30s timeout) is reusable; only the
  response handling differs (`.json()` instead of feeding `.text` to BeautifulSoup).
- Two existing Cloud Run Jobs (`puls-gpw` scraper, `puls-gpw-post` generator) share one Docker
  image and follow an identical entrypoint shape: `load_dotenv()` first, then
  `configure_logging()`, then `db.bigquery` imports (`main.py:1-14`, `post_main.py:1-17`).
- `.github/workflows/deploy.yml:47-63` updates both jobs' images on every push to `master` via
  `gcloud run jobs update`.

## Desired End State

Every weekday at 17:05 Warsaw time, a new Cloud Run Job runs, reads every row in `companies`, and
for each one with a usable `hop_url`, fetches that day's trading-data snapshot and appends it to
`company_daily_stats`. A failed fetch or missing `hop_url` is skipped and logged; it never blocks
the run for other tickers. Re-running the job for the same day is a safe no-op for tickers that
already have a row (idempotent).

Verify by: querying `company_daily_stats` for today's `snapshot_date` after a real 17:05 run and
confirming one row per company with a valid `hop_url`.

### Key Discoveries:

- `db/bigquery.py:1189-1241` has only `list_distinct_tickers()`, `list_distinct_companies()`,
  `list_tickers_missing_from_companies()` — none returns the full row set with `hop_url`/`isin`.
  A new batch-read function is needed.
- `add_watchlist_ticker()` (`db/bigquery.py:382-410`) is the established
  `INSERT ... WHERE NOT EXISTS` idempotency pattern to mirror for the new `(ticker,
  snapshot_date)` dedup guard.
- bankier's `symbol` URL param can differ from the GPW `ticker` (confirmed: ECHO ticker `ECH` vs.
  bankier symbol `ECHO`) — `symbol` must be parsed out of the stored `hop_url`'s query string,
  never derived from `ticker`.
- `today=true` is required on the bankier JSON endpoint — omitting it returns a multi-day
  aggregate instead of the live session snapshot.

## What We're NOT Doing

- Not filtering the active-ticker set by watchlist or portfolio membership — every row in
  `companies` gets fetched daily (user decision; removes the ticker drop-in/drop-out problem).
- Not touching `portfolio_snapshots` or the admin treemap — this is pure data ingestion;
  treemap price-refresh is a separate, unrelated ticket (PUL-61, Backlog).
- Not building shared code with PUL-61 beyond `bankier_metrics.py` — no PUL-61 code exists
  or is touched in this plan.
- Not provisioning the live Cloud Run Job / Cloud Scheduler resources — per `CLAUDE.md`, new
  infra creation is human-only; this plan documents the exact one-time commands as a runbook.

> **Pivot from original**: the original plan said `company_daily_stats` stays "strictly
> append-only." The implemented write strategy is **delete-for-date + batch insert** (clean
> replace): on each run, today's existing rows are deleted first, then all new rows are
> inserted via `insert_rows_json`. This is idempotent (same-day re-run produces the same
> result) but not append-only. The pivot was driven by the switch to a bulk listing-page
> fetch — the per-row `WHERE NOT EXISTS` guard is incompatible with batch streaming insert.

## Implemented Architecture

> This section documents the actual implementation, which diverged from Phases 1–2 contracts
> during development.

**Data source**: `https://www.bankier.pl/gielda/notowania/akcje` (GPW, ~404 symbols) and
`/new-connect` (NewConnect, ~335 symbols) — static HTML, 2 requests total. Symbol matched
via the `symbol` query param in each company's `hop_url`.

**Schema** (10 columns, down from the planned 14): `ticker` (REQUIRED), `snapshot_date`
(DATE, REQUIRED), `kurs_zamkniecia`, `zmiana_procentowa`, `zmiana_kwotowa`, `kurs_otwarcia`,
`kurs_min`, `kurs_max`, `wartosc_obrotu`, `liczba_transakcji`, `fetched_at` (TIMESTAMP,
REQUIRED). Partitioned by `snapshot_date`, clustered by `ticker`. Dropped from original plan:
`kurs_odniesienia`, `wolumen_obrotu`, `stopa_zwrotu_1r`, `kapitalizacja`, `rynek`, `system`
(not available from listing pages).

**Write strategy**: `delete_company_daily_stats_for_date(snapshot_date)` followed by
`batch_insert_company_daily_stats(rows)` via `insert_rows_json`. Guard: if `rows` is empty
(total scrape failure), raise `RuntimeError` before the delete so existing data is preserved.

**Coverage**: ~394 companies with data per day (~70%); ~176 companies don't appear on the
listing page on a given day (no trades, suspended, or low liquidity NC stocks). Query pattern
for "latest data per company" must use `ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY
snapshot_date DESC)` rather than `WHERE snapshot_date = CURRENT_DATE()`.

## Implementation Approach

Build bottom-up: BigQuery layer first (schema + read/write functions, unit-testable with a
mocked `_get_client()`), then the fetch+parse module (unit-testable with a mocked
`http_client.get()`), then the entrypoint that wires them together (unit-testable with
monkeypatched collaborators, mirroring `tests/test_post_main.py`'s fixture style), then
deployment wiring last.

## Phase 1: BigQuery schema + read/write functions

### Overview

Add the `company_daily_stats` table (schema, create/ensure) plus two new `db/bigquery.py`
functions: a batch read of all `companies` rows, and an append-only insert with an idempotency
guard.

### Changes Required:

#### 1. New table schema + create/ensure functions

**File**: `db/bigquery.py`

**Intent**: Define the `company_daily_stats` table and its create/ensure-schema functions,
following the exact shape of every other table in this file (`_TABLE_NAME`, `_SCHEMA` list,
`create_..._table_if_not_exists()`, `ensure_..._schema_current()`).

**Contract**: Table name `company_daily_stats`. Columns: `ticker` (STRING, REQUIRED),
`snapshot_date` (DATE, REQUIRED), `kurs_odniesienia` (FLOAT64, NULLABLE), `kurs_otwarcia`
(FLOAT64, NULLABLE), `kurs_min` (FLOAT64, NULLABLE), `kurs_max` (FLOAT64, NULLABLE),
`wolumen_obrotu` (INTEGER, NULLABLE), `wartosc_obrotu` (FLOAT64, NULLABLE),
`liczba_transakcji` (INTEGER, NULLABLE), `stopa_zwrotu_1r` (FLOAT64, NULLABLE),
`kapitalizacja` (FLOAT64, NULLABLE), `rynek` (STRING, NULLABLE), `system` (STRING, NULLABLE),
`fetched_at` (TIMESTAMP, REQUIRED). Partitioned by `snapshot_date`, clustered by `ticker` (per
the ticket's own design) — set `.time_partitioning` / `.clustering_fields` on the
`bigquery.Table` object before `create_table()`, since none of the existing tables in this file
use partitioning/clustering yet (new ground, not copy-paste from an existing table). Add a
one-line comment above the new `_SCHEMA` list noting that any field added *after* initial table
creation must be NULLABLE — `ensure_schema_current()`'s additive `ALTER TABLE ADD COLUMN` path
(`db/bigquery.py:168-170`) only succeeds for NULLABLE columns in BigQuery.

#### 2. Batch read of all companies

**File**: `db/bigquery.py`

**Intent**: Replace the need for a per-ticker `get_company(ticker)` call with one batch query the
entrypoint runs once per job invocation, returning every company row needed to drive the day's
fetch loop.

**Contract**: New function `list_companies_with_hop_info() -> list[dict]` returning
`{"ticker", "name", "hop_url", "isin"}` for every row in `companies` — no `WHERE hop_url IS NOT
NULL` filter at the query level; the missing-`hop_url` skip+log decision happens in the
entrypoint's per-ticker loop (Phase 3), matching `main.py`'s established skip+log shape rather
than silently filtering inside the query.

#### 3. Append-only insert with idempotency guard

**File**: `db/bigquery.py`

**Intent**: Insert one `company_daily_stats` row per (ticker, day); silently no-op on a same-day
re-run for a ticker that already has a row.

**Contract**: New function `insert_company_daily_stats(ticker, snapshot_date,
kurs_odniesienia, kurs_otwarcia, kurs_min, kurs_max, wolumen_obrotu, wartosc_obrotu,
liczba_transakcji, stopa_zwrotu_1r, kapitalizacja, rynek, system) -> None`, structured as
`INSERT ... SELECT ... WHERE NOT EXISTS (SELECT 1 FROM company_daily_stats WHERE ticker =
@ticker AND snapshot_date = @snapshot_date)` — the exact shape of `add_watchlist_ticker()`
(`db/bigquery.py:382-410`), substituting the dedup key from `(client_id, ticker)` to `(ticker,
snapshot_date)`. Raises `BigQueryError` on query failure, matching every other write function in
this file.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_bigquery.py -k company_daily_stats`
- Full suite still green: `uv run pytest`
- Lint passes: `uv run ruff check .`

---

## Phase 2: `src/bankier_metrics.py` — JSON fetch+parse module

### Overview

A new module that, given an ISIN and bankier `symbol`, hits the verified JSON endpoint and
returns the full field set as a dict — the single client this job (and any future consumer)
calls.

### Changes Required:

#### 1. Symbol extraction + fetch function

**File**: `src/bankier_metrics.py` (new)

**Intent**: Parse the bankier `symbol` query-string param back out of a stored `hop_url` (never
derive it from `ticker` — confirmed they can differ), then fetch and parse that instrument's
daily trading-data snapshot.

**Contract**: `symbol_from_hop_url(hop_url: str) -> str | None` — extracts the `symbol` query
parameter (e.g. via `urllib.parse.urlparse` + `parse_qs`); returns `None` if absent or
malformed. `fetch_daily_stats(isin: str, symbol: str) -> dict | None` — calls
`http_client.get(f"https://api.bankier.pl/quotes/public/company-profile-chart/{isin}/?symbols={symbol}&metrics=true&today=true")`,
parses `.json()`, and returns a dict with keys `kurs_odniesienia`, `kurs_otwarcia`, `kurs_min`,
`kurs_max`, `wolumen_obrotu`, `wartosc_obrotu`, `liczba_transakcji`, `stopa_zwrotu_1r`,
`kapitalizacja`, `rynek`, `system`, mapped from the API's verified field names
(`Kurs_odniesienia`, `Kurs_otwarcia`, `Minimum`, `Maximum`, `Wolumen_obrotu_szt`,
`Wartosc_obrotu_zl`, `Liczba_transakcji`, `Stopa_zwrotu_1R`, `Kapitalizacja`, `Rynek`,
`System_notowan`). Catches `ScraperError` from `http_client.get()` (exhausted retries) and
returns `None` — mirrors `fetch_company_profile`'s return-`None`-on-failure shape
(`src/company_profile.py`) so the caller's skip+log loop works identically for both hop-based
fetchers. Unlike `fetch_company_profile` (which logs the failure at `logger.debug(...)`,
invisible at this project's production INFO log level), `fetch_daily_stats` logs its failure at
`logger.warning(...)` — this job's Desired End State explicitly promises a failed fetch "is
skipped and logged," so the skip must actually be visible in Cloud Logging.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_bankier_metrics.py` — covering happy path (mocked
  JSON response → correct dict), missing/malformed `symbol` in `hop_url`, and `ScraperError`
  from `http_client.get()` → `None` return (mirroring `tests/test_company_profile.py`'s
  `patch("src.company_profile.get", ...)` pattern)
- Full suite still green: `uv run pytest`
- Lint passes: `uv run ruff check .`

#### Manual Verification:

- Hit the real bankier endpoint for 2-3 live tickers (e.g. a throwaway script or REPL) shortly
  after this phase lands, to reconfirm the field-name mapping still matches the live API
  response shape documented in the PUL-53 research — the API was last verified live during
  PUL-53, not during this plan.

---

## Phase 3: `company_stats_main.py` — Cloud Run Job entrypoint

### Overview

The new entrypoint that wires Phases 1 and 2 together: loads every company, fetches each one's
daily stats, and writes the row — with per-ticker failure isolation throughout.

### Changes Required:

#### 1. Entrypoint script

**File**: `company_stats_main.py` (new, repo root — sibling to `main.py`/`post_main.py`)

**Intent**: Run once per invocation (no `--window`-style flag needed, unlike `post_main.py` —
this job always processes "today" for every company). `load_dotenv()` must be the first
import-time action (per `.claude/rules/db-bigquery.md`), before any `db.bigquery` import.

**Contract**: `main()` calls `create_company_daily_stats_table_if_not_exists()` +
`ensure_company_daily_stats_schema_current()` (self-sufficient schema setup, matching
`post_main.py:239-242`'s pattern), then `list_companies_with_hop_info()`, then loops: for each
company, skip+log (no insert attempt) if `hop_url` is falsy; else
`symbol_from_hop_url(hop_url)`, skip+log if `None`; else `fetch_daily_stats(isin, symbol)`,
skip+log if `None` (fetch failure already logged inside `bankier_metrics`); else
`insert_company_daily_stats(...)` wrapped in `try/except BigQueryError` →
`logger.warning(...)` + `continue` (best-effort per-ticker write, matching
`main.py:73-78`'s `upsert_company` try/except shape — one ticker's BQ failure must never abort
the run for the rest). `snapshot_date` is `datetime.now(WARSAW).date()`. A top-level
`try/except Exception` around the whole loop calls `send_alert(exc)` + `sys.exit(1)` only for a
catastrophic, non-per-ticker failure (e.g. `list_companies_with_hop_info()` itself failing) —
mirrors `main.py`'s/`post_main.py`'s outer alert-and-exit shape. Logs a final summary line
(`processed=N skipped=M` style, matching `main.py:102-104`'s completion log).

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_company_stats_main.py` — covering: happy path (all
  mocked collaborators called in order), missing-`hop_url` skip, fetch-failure skip,
  `BigQueryError`-on-insert skip-and-continue (one bad ticker doesn't stop the loop), and the
  catastrophic-failure → `send_alert` + exit path — mirroring `tests/test_post_main.py`'s
  `monkeypatch.setattr` fixture style
- Full suite still green: `uv run pytest`
- Lint passes: `uv run ruff check .`

#### Manual Verification:

- Run `uv run python company_stats_main.py` locally against a real (or sandboxed) BQ dataset
  and confirm rows land in `company_daily_stats` with sane values for a few known tickers.
- Confirm a deliberately-broken `hop_url` (or a temporarily-unreachable network) for one ticker
  doesn't stop the rest of the run — check the log for the skip+log line and confirm other
  tickers still got rows.

---

## Phase 4: Deployment wiring

### Overview

Wire the new job into CI's per-push update step, and write the one-time manual runbook for
provisioning the live Cloud Run Job + Cloud Scheduler entry (human-only per `CLAUDE.md`).

### Changes Required:

#### 1. CI update step

**File**: `.github/workflows/deploy.yml`

**Intent**: Add a `gcloud run jobs update` step for the new job, identical in shape to the
existing scraper/post-job steps, so every push to `master` keeps its image current.

**Contract**: New step after the existing "Update Cloud Run Job (post)" step:
`gcloud run jobs update puls-gpw-company-stats --image=... --command=uv
--args="run,--no-dev,python,company_stats_main.py" --region=... --project=...` — same image,
same `${{ env.* }}` vars already in scope in this workflow file.

#### 2. One-time manual provisioning runbook

**File**: `context/foundation/infra.md`

**Intent**: Document the exact one-time `gcloud run jobs create` + `gcloud scheduler jobs create
http` commands a human must run once before CI's update step has a job to target — append to
the existing Cloud Run Jobs / Cloud Scheduler tables in this file rather than creating a new
doc.

**Contract**: New row in the "Cloud Run Jobs" table (`puls-gpw-company-stats`, same shared
image, `uv run python company_stats_main.py`) and new row in the "Cloud Scheduler" table
(`puls-gpw-company-stats-trigger`, cron `5 17 * * 1-5`, Warsaw time, weekdays). Note inline that
CPU/RAM/secrets/env vars follow the existing common-config section verbatim (this job needs no
new secrets). Use the **standard `--task-timeout=300s`** (same as existing jobs) — the
implemented batch approach runs in ~6 seconds (2 HTTP fetches + 1 BQ streaming insert), well
within the existing 300s budget. The original plan called for 1800s due to a planned
per-company sequential DML loop (~15 min); that approach was not implemented.

### Success Criteria:

#### Automated Verification:

- `deploy.yml` is valid YAML and the new step references the same `${{ env.* }}` vars already
  defined earlier in the file (no dedicated workflow-file lint command exists in this repo —
  visual diff review against the existing two job-update steps)

#### Manual Verification:

- Human confirms the current `companies` row count (`SELECT COUNT(*) FROM companies`) before
  provisioning, to size `--task-timeout` headroom (plan defaults to 1800s).
- Human runs the documented one-time `gcloud run jobs create` (including `--task-timeout=1800s`)
  + `gcloud scheduler jobs create http` commands against the `puls-gpw` project (region
  `europe-central2`) and confirms the job appears in `gcloud run jobs list` and the scheduler
  entry in `gcloud scheduler jobs list`.
- After the next push to `master`, confirm CI's new step updates the job's image without
  erroring.
- Wait for (or manually trigger via `gcloud run jobs execute`) the first real 17:05 weekday run,
  confirm `company_daily_stats` gets populated for the day, and check the run's total duration
  against the 1800s budget.

---

## Testing Strategy

### Unit Tests:

- `db/bigquery.py` additions: schema/create/ensure (table existence check, mirroring every
  other table's test), `list_companies_with_hop_info()` (row mapping), and
  `insert_company_daily_stats()` (INSERT query shape + bound params + idempotency
  `WHERE NOT EXISTS` clause present) — pattern: `patch("db.bigquery._get_client",
  return_value=_mock_bq_client())`, assert on `client.query.call_args`.
- `src/bankier_metrics.py`: `symbol_from_hop_url()` (normal URL, missing param, malformed URL),
  `fetch_daily_stats()` happy path + `ScraperError` → `None`.
- `company_stats_main.py`: full per-ticker loop with monkeypatched collaborators
  (`monkeypatch.setattr` fixture style from `tests/test_post_main.py`), covering
  missing-`hop_url` skip, fetch-failure skip, insert-failure skip-and-continue, and the
  catastrophic top-level alert path.

### Integration Tests:

- None planned — this job has no HTTP-facing API surface (`src/api.py`/`api_main.py`
  untouched) and the existing E2E suite (`tests/e2e/`) covers browser-level admin/user flows,
  not Cloud Run Job entrypoints. Manual verification (Phases 2-4) covers the real-world JSON
  API + BQ round-trip instead.

### Manual Testing Steps:

1. After Phase 2 lands, hit the live bankier JSON endpoint for 2-3 real tickers to reconfirm
   the field mapping.
2. After Phase 3 lands, run `company_stats_main.py` locally end-to-end against a real/sandbox
   BQ dataset.
3. After Phase 4's human-run provisioning, wait for (or manually trigger) the first scheduled
   17:05 run and inspect `company_daily_stats` for that day.

## Performance Considerations

The implemented job: 2 HTTP fetches (listing pages, ~1s total) + in-memory dict lookup for
~570 companies + 1 DELETE DML (~1s) + 1 `insert_rows_json` batch call (~0.5s) = **~6 seconds
total**. Standard 300s Cloud Run timeout is more than sufficient. The original concern about
per-company sequential DML latency (~15 min) was resolved by the listing-page batch approach.

## Migration Notes

Pure additive change — new table, new module, new entrypoint, new CI step. No existing table
schema changes, no backfill needed (the table starts empty; history accrues from the first
scheduled run onward).

## References

- Related research: `context/changes/daily-company-stats-snapshot-ingestion/research.md`
- Idempotency pattern to mirror: `db/bigquery.py:382-410` (`add_watchlist_ticker`)
- Per-ticker skip+log pattern to mirror: `main.py:55-101`
- Entrypoint shape to mirror: `post_main.py:1-17,214-243`
- Hop-fetch failure→`None` pattern to mirror: `src/company_profile.py` (`fetch_company_profile`)
- Test mocking patterns to mirror: `tests/test_bigquery.py:831-846`,
  `tests/test_post_main.py:51-79`, `tests/test_company_profile.py:27-53`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not
> rename step titles. See `references/progress-format.md`.

### Phase 1: BigQuery schema + read/write functions

#### Automated

- [x] 1.1 Unit tests pass: `uv run pytest tests/test_bigquery.py -k company_daily_stats` — 21aed8d
- [x] 1.2 Full suite still green: `uv run pytest` — 21aed8d
- [x] 1.3 Lint passes: `uv run ruff check .` — 21aed8d

### Phase 2: `src/bankier_metrics.py` — JSON fetch+parse module

#### Automated

- [x] 2.1 Unit tests pass: `uv run pytest tests/test_bankier_metrics.py` — 165b143
- [x] 2.2 Full suite still green: `uv run pytest` — 165b143
- [x] 2.3 Lint passes: `uv run ruff check .` — 165b143

#### Manual

- [x] 2.4 Hit the real bankier endpoint for 2-3 live tickers to reconfirm field-name mapping — 165b143

### Phase 3: `company_stats_main.py` — Cloud Run Job entrypoint

#### Automated

- [x] 3.1 Unit tests pass: `uv run pytest tests/test_company_stats_main.py` — be06fb8
- [x] 3.2 Full suite still green: `uv run pytest` — be06fb8
- [x] 3.3 Lint passes: `uv run ruff check .` — be06fb8

#### Manual

- [x] 3.4 Local run against real/sandbox BQ dataset produces sane rows for known tickers — be06fb8
- [x] 3.5 A broken `hop_url`/unreachable network for one ticker doesn't stop the rest of the run — be06fb8

### Phase 4: Deployment wiring

#### Automated

- [x] 4.1 `deploy.yml` is valid YAML and the new step matches the existing job-update steps

#### Manual

- [x] 4.2 Confirm current `companies` row count before provisioning, to size
  `--task-timeout` headroom (plan defaults to 1800s) — 008baa2 (batch approach: 300s sufficient)
- [x] 4.3 Human runs the one-time `gcloud run jobs create` (with `--task-timeout=300s`) +
  `gcloud scheduler jobs create http` commands; job and scheduler entry confirmed via
  `gcloud ... list` — job updated + scheduler `1 9-17 * * 1-5` Europe/Warsaw created
- [x] 4.4 Next push to `master` updates the new job's image via CI without erroring — d8a9b4f
- [ ] 4.5 First real (or manually triggered) 17:05 run populates `company_daily_stats`; run
  duration checked against the 1800s budget
