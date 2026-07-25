# Backfill Historical Daily Closes (stooq) Implementation Plan

## Overview

One-time backfill of **full available daily price history** (stooq.pl, raw/unadjusted closes) for **every ticker known to BQ** (`companies` ∪ `etf_instruments`, ~570+) into `company_daily_stats` and `etf_quotes`, via new **insert-only MERGE** write paths. Unblocks the 1R (1y) range in the portfolio value-history charts (PUL-89/91) and gives the calendar heatmap P&L over backfilled days. Linear PUL-92 / GitHub #182.

## Current State Analysis

- Both tables accumulate data only forward from ingestion start (~2026-06-25 stocks, ~2026-06-29 ETFs); no history before that. `get_portfolio_history` (LOCF + full-coverage gate) and `get_portfolio_calendar_data` therefore return partial series by design.
- Idempotent write paths exist: `merge_company_daily_stats` (`db/bigquery.py:2450-2522`) and `merge_etf_quotes` (`:2671-2733`) — temp-table load job + `MERGE ON (ticker, snapshot_date)`. **Their MATCHED branch overwrites all columns**, so they cannot be used as-is without clobbering scraped rows.
- No stooq integration or app-ticker↔stooq-symbol mapping exists anywhere (verified).
- Full research: `context/changes/backfill-historical-closes/research.md`.

## Desired End State

- `company_daily_stats` and `etf_quotes` contain full stooq history (raw closes, back to each ticker's first listing) for every ticker in the BQ universe, with `zmiana_kwotowa`/`zmiana_procentowa` (and ETF `kurs_odn`) derived.
- Rows written by the daily scrapers are untouched (byte-identical before/after).
- `GET /api/portfolio/history?range=1y` returns a dense year for held tickers; calendar heatmap shows P&L for months back to January 2026 and beyond.
- Re-running the script is a no-op for covered tickers (auto-resume from BQ) and never duplicates or overwrites rows.

### Key Discoveries (verified live on stooq, 2026-07-24):

- CSV endpoint = the page's "Pobierz dane w pliku csv..." link: `https://stooq.pl/q/d/l/?s=<sym>&d1=YYYYMMDD&d2=YYYYMMDD&i=d`; header `Data,Otwarcie,Najwyzszy,Najnizszy,Zamkniecie,Wolumen`, ISO dates, dot decimals, CRLF.
- **`&o=1111111` disables all adjustments** (splits/dividends/rights…) → raw closes matching the bankier/gpw scraper convention (KRU 2026-01-02: 498.40 raw vs 475.38 adjusted; raw volume is integer). Raw values carry float noise (`498.39977480839`) — round to 4 decimals.
- Symbol mapping: stocks = ticker lowercased (`kru`); **ETF/ETC/ETN = ticker lowercased + `.pl`** (`etfbw20tr.pl` — verified, 1885 days of history).
- Anti-bot stack: (1) a JS **proof-of-work challenge** on first contact (SHA-256: find `n` where `sha256(c + n)` hex starts with `d` zeros; POST `c`,`n` form-encoded to `/__verify` → cookie); (2) the CSV endpoint returns `Odmowa dostępu` unless the symbol's own page `q/d/?s=<sym>` was fetched first in the same session (referer/cookie context); (3) a daily download limit that switches responses to a captcha page.
- Consumers need: `kurs_zamkniecia` non-NULL (both), `zmiana_kwotowa` non-NULL for calendar P&L; the trading-day spine comes from `company_daily_stats` only; the calendar query is NOT duplicate-safe (see research).

## What We're NOT Doing

- No bossa.pl adapter (follow-up only if stooq coverage gaps materialize in practice).
- No changes to consumer queries, API endpoints, or frontend — they pick the data up as-is.
- No scheduled job — this is a one-off script; the daily scrapers remain the forward-fill source.
- No backfill of `wartosc_obrotu`/`liczba_transakcji` (stooq has share volume, not turnover value/trade count — left NULL).
- No transaction-date modeling — the PUL-79 "today's share counts × historical close" approximation stands (documented there).
- No automatic prod run — full-universe write to prod BQ is human-triggered (project rule).

## Implementation Approach

Phase 1 adds the safe write primitive (insert-only MERGE) with unit + real-BQ round-trip tests. Phase 2 builds the script: stooq session (PoW solver + per-symbol page GET), CSV parsing, row building with derived fields, universe + auto-resume queries, chunked flushes, clean abort on limit. Phase 3 is the human-gated rollout: dry-run → small `--tickers` sample on prod → full run (possibly split over days by the stooq limit) → API/UI verification.

## Critical Implementation Details

- **Insert-only MERGE still inserts duplicates from a duplicated *source* batch** — `WHEN NOT MATCHED` is evaluated per source row, so two identical source rows both insert. Defense in depth: the script dedups rows per `(ticker, snapshot_date)` before flushing, AND the MERGE's `USING` clause dedups via `QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker, snapshot_date) = 1`.
- **Session order matters**: bootstrap (solve PoW if challenged) → per ticker: GET `q/d/?s=<sym>` → GET CSV with `Referer: https://stooq.pl/q/d/?s=<sym>`. Skipping the page GET yields `Odmowa dostępu` even for valid symbols.
- **Denial disambiguation**: `Odmowa dostępu` after a successful page GET whose HTML contains the instrument name = treat as limit/abort; a page GET that lands on "Symbol … nie istnieje w bazie" = unknown symbol → log + skip. A captcha/challenge page mid-run = daily limit → clean abort with summary; re-run next day resumes.
- **Resume marker**: a ticker is "done" iff it has any row with `snapshot_date < 2026-06-01` (scrapers started ~2026-06-25/29, so pre-June rows can only come from this backfill). Tickers listed after June 2026 (IPOs) will re-fetch on every run and no-op on merge — harmless, noted.
- **Derived fields need ascending order**: sort parsed rows by date; `zmiana_kwotowa = close_d − close_{d-1}`, `zmiana_procentowa = (close_d/close_{d-1} − 1) × 100`, ETF `kurs_odn = close_{d-1}`; first row of each ticker's history gets NULLs (no prior close — matches scraper semantics for no-reference days).
- **BQ DML volume**: flush accumulated rows per chunk of tickers (default 25) per table, not per ticker — ~25 MERGE jobs per run instead of ~570.
- **Ex-dividend `zmiana_*` semantics (accepted)**: derived changes come from raw consecutive closes, so ex-dividend days show the mechanical price drop; scraper rows compute change against GPW's dividend-adjusted reference price, so semantics differ on those days. Accepted and documented (consistent with the raw-prices decision), like the PUL-79 share-count approximation.
- **`get_latest_company_stats_fetched_at` cosmetic edge (accepted)**: `LIMIT 1` without `ORDER BY` (`db/bigquery.py:2747`) means that when the backfill fills a recent scraper-outage gap, the treemap "as of" timestamp for that date may show the backfill's `fetched_at`. Known, accepted, no query change.

## Phase 1: Insert-only MERGE write paths

### Overview

Add `merge_company_daily_stats_insert_only(rows)` and `merge_etf_quotes_insert_only(rows)` to `db/bigquery.py` so the backfill can never clobber scraped rows, with unit tests and a real-BQ round-trip script (lesson: mocked tests don't parse SQL).

### Changes Required:

#### 1. Insert-only MERGE functions

**File**: `db/bigquery.py`

**Intent**: Two new functions next to the existing merges, reusing the temp-table load-job pattern (WRITE_TRUNCATE, 24h expiry, `finally` cleanup) but with **no `WHEN MATCHED` branch** — existing `(ticker, snapshot_date)` rows are structurally untouchable. Return the number of inserted rows (`query_job.num_dml_affected_rows`) for script reporting.

**Contract**: `merge_company_daily_stats_insert_only(rows: list[dict]) -> int`, `merge_etf_quotes_insert_only(rows: list[dict]) -> int`. Rows use the same dict shape as the existing merges (ISO strings for dates/timestamps; NULLABLE fields omitted or None). The MERGE source dedups defensively — non-obvious SQL, other phases depend on it:

```sql
MERGE `{target}` T
USING (
  SELECT * FROM `{tmp_table_id}`
  QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker, snapshot_date) = 1
) S
ON T.ticker = S.ticker AND T.snapshot_date = S.snapshot_date
WHEN NOT MATCHED THEN
  INSERT (…all columns…) VALUES (S.…)
```

#### 2. Unit + SQL-string regression tests

**File**: `tests/test_bigquery_insert_only_merge.py` (new)

**Intent**: Mock-client tests: (a) the built SQL contains `WHEN NOT MATCHED` and does NOT contain `WHEN MATCHED` (regression on the string — cheap guard per lessons.md); (b) temp table deleted in `finally` even when the query raises; (c) empty `rows` → no-op without client calls; (d) returns affected-row count.

#### 3. Real-BQ round-trip script

**File**: `scripts/test_bq_insert_only_merge.py` (new)

**Intent**: Round-trip on real BigQuery (pattern: `scripts/test_bq_company_stats_merge.py`) but against a **throwaway table** — create `company_daily_stats_rt_<uuid8>` with the real schema, monkeypatch the module-level table-name constant, then verify: insert new key → row appears; re-merge same key with different values → row unchanged (no clobber); duplicated source batch → single row. Drop the table in `finally`. Never touches the real tables.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_bigquery_insert_only_merge.py -q`
- Full suite green: `uv run pytest --ignore=tests/e2e -q`
- Lint passes: `uv run ruff check .`

#### Manual Verification:

- Round-trip on real BQ passes: `uv run python scripts/test_bq_insert_only_merge.py` (requires ADC; prints PASS/FAIL per assertion)

**Implementation Note**: After completing this phase and all automated verification passes, pause for the human round-trip run before Phase 2 relies on the primitive.

---

## Phase 2: Backfill script

### Overview

`scripts/backfill_historical_closes.py` — fetch full history per ticker from stooq (raw prices), build rows with derived fields, flush via insert-only MERGE, auto-resume from BQ, clean abort on stooq limit.

### Changes Required:

#### 1. The script

**File**: `scripts/backfill_historical_closes.py` (new)

**Intent**: Single-file script following repo conventions (`sys.path.insert` → `load_dotenv()` → `configure_logging()` → `db.bigquery` imports; docstring with run examples; `if __name__ == "__main__"`). Pure logic (CSV parsing, row building, symbol mapping, response classification, PoW solving) lives in module-level functions so tests import them without network.

**Contract** (REVISED 2026-07-24 during implementation — see Addendum below; live-fetch path proved impossible):
- CLI: `--from-dir <folder>` (REQUIRED: directory of manually browser-downloaded stooq CSV files), `--dry-run` (parse+report, zero BQ writes), `--tickers KRU,ETFBW20TR` (optional subset filter), `--chunk-size` (default 25, tickers per BQ flush).
- Universe query (script-local, via `_get_client()`): `companies.ticker` → kind `stock`, `etf_instruments.ticker` → kind `etf`; on both lists, `stock` wins (mirrors consumer COALESCE precedence). Used to route each file's rows to the right table.
- File→ticker matching: filename stem normalized (strip `_d`/`.csv`/`.pl`, casefold) and matched against `map_symbol()` output per universe ticker; unmatched files and universe tickers without a file are listed in the summary (log, not error).
- File-content validation: `classify_response()` guards against accidentally saved error/limit pages — only `ok` (starts with `Data,`) content is ingested; anything else is reported and skipped.
- Row building unchanged: derived `zmiana_kwotowa`/`zmiana_procentowa`/ETF `kurs_odn` per Critical Implementation Details; chunked flush via `merge_*_insert_only`.
- Row building: closes/OHLC rounded to 4 decimals; stocks → `company_daily_stats` dict (volume dropped, `wartosc_obrotu`/`liczba_transakcji` absent), ETFs → `etf_quotes` dict (`wolumen_skum` = Volumen, `kurs_odn` = prior close); `fetched_at` = run start UTC ISO; derived `zmiana_kwotowa`/`zmiana_procentowa` per Critical Implementation Details.
- Flush: accumulate per-table row lists, dedup, call `merge_*_insert_only` every `--chunk-size` tickers and at end; log inserted counts.
- Startup ritual: `create_*_table_if_not_exists()` + `ensure_*_schema_current()` for both tables (skipped in `--dry-run`).

#### 2. Unit tests

**File**: `tests/test_backfill_historical_closes.py` (new)

**Intent**: No-network tests of the pure functions: CSV parsing (fixture with CRLF, float-noise values, em-dash-free), derived-field computation incl. first-row NULLs and rounding, symbol mapping (stock vs ETF `.pl`), response classification (ok / unknown symbol / limit / challenge HTML), PoW solver against a difficulty-1 fixture, row-shape correctness for both tables (REQUIRED fields present, volume handling), in-batch dedup.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_backfill_historical_closes.py -q`
- Full suite green: `uv run pytest --ignore=tests/e2e -q`
- Lint passes: `uv run ruff check .`

#### Manual Verification:

- `uv run python scripts/backfill_historical_closes.py --dry-run --tickers KRU,ETFBW20TR` fetches live stooq, prints parsed row counts, date ranges, sample rows, and writes nothing to BQ.

**Implementation Note**: Pause after dry-run verification for human confirmation before Phase 3 touches prod BQ.

---

## Phase 3: Verification & rollout (human-gated)

### Overview

Prove the pipeline on a small sample against prod BQ, then hand the full run to the human, then verify the user-visible outcome.

### Changes Required:

#### 1. Sample run + data audit

**File**: (no code — operational)

**Intent**: Human-approved run `--tickers KRU,ETFBW20TR` (or the user's actual holdings). Audit SQL afterwards: (a) pre-2026 rows exist for the sample tickers; (b) a scraped overlap row (e.g. KRU on a July 2026 date) is byte-identical before/after; (c) no duplicate `(ticker, snapshot_date)` (`GROUP BY ... HAVING COUNT(*) > 1` returns empty); (d) spot-check 2-3 closes against the stooq page table.

#### 2. Full-universe run

**File**: (no code — operational)

**Intent**: Human runs without `--tickers`, optionally with `--limit` to ration the stooq daily quota; re-runs on subsequent days auto-resume until the summary reports 0 remaining. Verify `etf_quotes` has no partition-expiration set (`bq show --project_id=puls-gpw espi_ebi.etf_quotes`) — research flagged an unconfirmed 7-day-expiry memory; if an expiry exists, resolve with the human before the full run.

#### 3. Outcome verification

**File**: (no code — operational)

**Intent**: On prod (after the 5-min API cache rolls): `GET /api/portfolio/history?range=1y` returns a dense series back ~365 days for the owner portfolio; calendar heatmap shows P&L for January-June 2026; charts render 1R correctly in the UI (Kalendarz view, both active-portfolio and "Wszystkie" charts).

### Success Criteria:

#### Manual Verification:

- Sample-run audit (a)-(d) all pass
- Full-universe run completes across N days; final summary reports 0 remaining, unknown symbols listed and reviewed
- `?range=1y` dense on prod; calendar P&L visible for backfilled months; 1R chart renders in UI

---

## Testing Strategy

### Unit Tests:

- SQL-string regression for insert-only MERGE (no `WHEN MATCHED`)
- CSV parse, derived fields (first-row NULLs, rounding), symbol mapping, response classification, PoW solver, dedup

### Integration Tests:

- Real-BQ round-trip against throwaway table (`scripts/test_bq_insert_only_merge.py`) — the only trustworthy SQL check (mocks don't parse SQL)

### Manual Testing Steps:

1. Dry-run on 2 tickers (1 stock + 1 ETF) — inspect parsed output
2. Prod sample run + 4-point audit SQL
3. Full run(s) + API/UI verification per Phase 3

## Performance Considerations

~570 tickers × 2 requests, 1.5 s spacing → ~30 min per uninterrupted run; the stooq daily limit is the real constraint, hence `--limit` + auto-resume. BQ: ~4M rows total is trivial; date-partitioning keeps consumer queries pruned; chunked flushes keep MERGE-job count low.

## Migration Notes

Purely additive rows; no schema changes. Rollback if ever needed: `DELETE FROM <table> WHERE snapshot_date < '2026-06-01'` — destructive, human-only per project rules.

## Addendum (2026-07-24): live-fetch → manual-download pivot

Verified during Phase 2: stooq denies the CSV endpoint to non-browser clients at the **TLS-fingerprint level** — the PoW challenge is solvable (verified: `/__verify` 200 + auth cookie), but the server then serves a chaff page and `Odmowa dostępu` regardless of header mimicry or HTTP/2. bossa.pl bulk EOD archives moved behind a login. Decision (user): **the user downloads the CSV files manually in their browser** ("Pobierz dane w pliku csv..." per symbol) **and provides a directory; the script ingests via `--from-dir`**. The StooqSession/PoW/`--cookie`/`--limit`/`--sleep` machinery was removed as dead code (PoW solver + challenge parsing preserved in git history at `e27cb5d`..`fbc0e1b` if ever needed). Resume semantics simplify: ingest whatever files are present; insert-only MERGE makes re-ingestion a no-op. Phase 3 "full-universe run" becomes: user downloads in batches (respecting stooq's daily limit), then runs the ingest per batch.

## Addendum (2026-07-25): manual per-symbol download → bulk archive

The per-symbol manual download from the 2026-07-24 addendum was superseded before it ran at scale: stooq publishes a **bulk archive** (`stooq.pl/db/h/` → `d_pl_txt.zip`) holding the whole market in one download, which removes the per-symbol rate limit as a constraint entirely rather than working around it. The user placed it at `d_pl_txt/` (gitignored). Layout: `data/daily/pl/{wse stocks, nc stocks, wse etfs, wse stocks intl, …}/<symbol>.txt`, ASCII `<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>` with `YYYYMMDD` dates. Ingested via a new `--from-db-dir` mode (`ecd330a`); `--from-dir` stays for the per-symbol CSV path.

Three findings from the audit, each with a ticket:

1. **Prices are dividend-adjusted** — contradicts Contract decision #3 (raw closes). Adjusted values are not tick-compliant (`KGH` 2025-12-30 = `279.596`), and the factor reaches 1.0 only after each ticker's latest ex-dividend date (clustering May–Jul 2026). At least 140/771 tickers (18%, lower bound) are affected inside the trailing 12 months that `?range=1y` renders; past portfolio values come out understated. **Accepted knowingly** (user decision) to unblock Phase 3. → PUL-96 / GH #191
2. **Two junk rows in `companies`** (`przejęty`, `Żabka`) that are not tickers. → PUL-97 / GH #192
3. **Scraper `kurs_zamkniecia` is a pre-close intraday snapshot**, off by a randomly-signed 0.1–0.4% versus the official close (scheduler's last tick ~17:01 precedes the ~17:05 fixing). Only 1 of 24 sampled pairs matched. → PUL-98 / GH #193

Plus an opportunity: the archive carries per-ticker P/E, P/BV and market-cap history (`* indicators` dirs) → PUL-99 / GH #194.

**Out-of-plan change, accepted** (`e82fa77`): while verifying the backfill in the UI the user reported that the value-history chart's x-axis read `16.04` / `24.07` with no year, which is ambiguous the moment a range crosses a year boundary — `1r` always does. `fmtDate` in `static/index.html` now renders `DD.MM.YYYY`, guarded by an e2e assertion on both charts and break-verified. The plan describes only the backfill pipeline, so this is scope beyond "Changes Required"; recorded here rather than reverted (impl-review F2).

**Partition ceilings** (`a3fb72f`): both target tables are DAY-partitioned on `snapshot_date`. The archive spans 10053 trading days (from 1987-01-02 via `UCG`'s Milan history), which breaches BigQuery's 10k-partitions-per-table limit and, per flush, the 4000-partitions-per-job limit. Mitigations: `_flush()` merges year by year, and `--since` (default `2011-01-01`) trims history to 3916 partitions, leaving ~24 years of headroom for the scraper.

## References

- Ticket: Linear PUL-92 / GitHub #182 (tracking in `change.md`)
- Follow-ups: PUL-96/#191, PUL-97/#192, PUL-98/#193, PUL-99/#194
- Research: `context/changes/backfill-historical-closes/research.md`
- MERGE pattern: `db/bigquery.py:2450-2522`; consumers: `db/bigquery.py:465-580`, `:362-462`
- Script conventions: `scripts/backfill_companies.py`, `scripts/reanalyze_failed.py`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Insert-only MERGE write paths

#### Automated

- [x] 1.1 Unit tests pass: `uv run pytest tests/test_bigquery_insert_only_merge.py -q` — e27cb5d
- [x] 1.2 Full suite green: `uv run pytest --ignore=tests/e2e -q` — e27cb5d
- [x] 1.3 Lint passes: `uv run ruff check .` — e27cb5d

#### Manual

- [x] 1.4 Round-trip on real BQ passes: `uv run python scripts/test_bq_insert_only_merge.py` — e27cb5d

### Phase 2: Backfill script

#### Automated

- [x] 2.1 Unit tests pass: `uv run pytest tests/test_backfill_historical_closes.py -q`
- [x] 2.2 Full suite green: `uv run pytest --ignore=tests/e2e -q`
- [x] 2.3 Lint passes: `uv run ruff check .`

#### Manual

- [x] 2.4 Dry-run `--from-dir` on user-downloaded sample files (1 stock + 1 ETF) parses, reports, writes nothing — superseded by the bulk-archive pivot; dry-run over the full archive reported 771/783 tickers, 1917971 rows, 0 bad files, 0 written — `a3fb72f`

### Phase 3: Verification & rollout (human-gated)

#### Manual

- [x] 3.1 Sample-run audit (pre-2026 rows, no clobber, no dupes, spot-check) passes — KRU + ETFBW20TR: 5657/5685 inserted (28 already present), KRU 2026-07-24 still 410.8 from the scraper vs stooq's 411.4, 0 duplicate keys
- [x] 3.2 Full-universe run(s) complete; 0 remaining; unknown symbols reviewed; etf_quotes expiry confirmed absent — 1899603 rows inserted, 0 errors; `company_daily_stats` 1897457 rows / 3917 partitions / 744 tickers / 2011-01-03..2026-07-24, `etf_quotes` 23913 rows / 3761 partitions / 39 tickers; 0 duplicates in both; 89 unmatched files reviewed (foreign listings in `wse stocks intl`, outside the universe); 12 universe tickers without a file (2 of them junk rows → PUL-97); no table or partition expiry set on either table
- [x] 3.3 `?range=1y` dense on prod; calendar P&L for backfilled months; 1R chart renders in UI — deploy verified (`/health` ok, `2712064`); `get_portfolio_history` against prod BQ returns **250 points covering the full year** (2025-07-25..2026-07-24) for 2 of 3 portfolios, versus ~21 before the backfill. The third portfolio and the aggregate stop at 71 points (2026-04-16) because 4 shares of `S2B` — 0.66% of that portfolio, listed 2026-04-16 — trip the full-coverage gate; that is a gate design issue, not a data gap, and is tracked in PUL-100/#195 with a recommended fix. Chart rendering covered by 4 e2e tests.
