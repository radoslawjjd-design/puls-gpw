# Official GPW close as the source for `company_daily_stats` — Implementation Plan

## Overview

`company_daily_stats.kurs_zamkniecia` is not the official GPW closing price. The bankier.pl listing
page publishes a best-bid / best-ask / last-continuous-trade figure instead, so stored closes differ
from the official close by 0.1–0.4%, randomly signed. This plan switches the source to the official
GPW and NewConnect quotation tables, keeps bankier only as a gap-filler that may never overwrite an
official value, adds a self-healing invariant based on the feed's own previous-close column, and
corrects 19 months of history from the GPW archive — which also removes the dividend-adjustment
defect (PUL-96 / GH #191) across every rendered surface.

## Current State Analysis

**The defect is narrower than the ticket implies.** Measured on the 2026-07-27 session, bankier's
open / min / max are correct to the grosz; only the close is wrong, and the two derived change
columns with it:

| column | BQ (bankier) | GPW official | verdict |
|---|---|---|---|
| `kurs_zamkniecia` | 44.905 / 234.0 / 301.95 | **45.0 / 234.5 / 302.5** | **wrong** |
| `kurs_otwarcia`, `kurs_min`, `kurs_max` | 45.0 / 229.6 / 309.7 | identical | correct |
| `wartosc_obrotu` | 196.5 M | 243.0 M | bankier understates ~19% |
| `liczba_transakcji` | 13 975 | 14 828 | bankier understates ~6% |

The same holds against the archive for 2026-07-24 (ALE / KGH / PKO: open, min, max identical; close
off). The turnover and trade-count gaps are consistent with bankier excluding the closing auction;
**no reader renders either column**, so their discontinuity at the switch date is harmless.

**Timing is real but already satisfied.** The live GPW table is a ~15-minute-delayed feed whose
column is literally *"Kurs ost. trans. / zamk."* — the last transaction during the session. Polling
on 2026-07-27 showed the closing-auction price landing at **~17:15 Warsaw** and never moving after
(identical at 17:15, 17:25, 17:35, 17:45, 17:56). The existing scheduler `1,31 9-17 * * 1-5` fires
last at **17:31**, giving ~16 minutes of margin. No cron change — and therefore no human-only infra
step — is required. But every run from 9:01 to 17:01 writes an intraday price, and the MERGE
upserts, so a failed 17:31 run silently leaves an intraday value as the day's close.

**Identity joins cleanly.** Feed `Skrót` matches `companies.ticker` for 697 of 744 companies, with
**0 ISIN conflicts** among matches. GPW main (372) and NewConnect (332) do not overlap. ~47
companies are in neither feed and at least 18 are still priced daily by bankier — hence the
gap-filler.

**Write path is a full upsert.** `merge_company_daily_stats` (`db/bigquery.py:2475-2547`) matches on
`(ticker, snapshot_date)` and its `WHEN MATCHED` branch updates **all nine non-key columns**; a row
dict missing a NULLABLE key writes `NULL` over existing data. `merge_company_daily_stats_insert_only`
(`db/bigquery.py:2828-2840`) has no MATCHED branch and cannot correct anything. Neither primitive can
perform a narrow correction, so one must be added.

**ETF history has no defect — measured, not assumed.** `etf_quotes` closes for the three held ETFs
match the GPW ETF archive (`type=241`) exactly on 2026-07-24, 2026-01-02 and 2025-07-01 (a
backfilled date) — 9 of 9. Total-return ETFs accumulate distributions, so stooq's dividend
adjustment is a no-op for them, and the existing ETF scraper already reads the official close.

### Key Discoveries

- Column indices are **derivable structurally**: the GPW `<thead>` uses proper `colspan`/`rowspan`
  (`Najlepsza oferta kupna|sprzedaży` carry `colspan=3`, everything else `rowspan=2`), so expanding
  colspan yields an exact 26-column map. NewConnect has a single header row and 20 columns. One
  algorithm covers both — no hard-coded indices, which is what let this bug survive a month
  (`src/bankier_metrics.py:98-105` reads `cells[1]..cells[8]` positionally).
- `Kurs odn.` (previous session's close) is **provably official** — 8/8 against the ticket's
  independently verified 2026-07-24 closes. It is free in every fetch and is the basis of the
  self-heal.
- **Unit trap**: GPW/NC publish `Wart. obr. - skumul. (tys.)` in thousands; `company_daily_stats`
  stores PLN. Without `×1000` the column would be understated a thousandfold.
- The archive is authoritative, raw (non-dividend-adjusted) and deep: `KRU` 2026-01-02 returns
  `498,4000` — exactly the "raw" figure recorded in the PUL-92 plan against stooq's adjusted
  `475.38`. Reach verified to 2001. Cost 283 KB / ~1.3 s per session.
- The archive table has **no ticker and no ISIN — only `Nazwa`**. Exact-name match against today's
  live feed covers 90.3% of archive rows today, 86.4% at 1 year, 82.6% at 2 years.
- The trading-day spine is `SELECT DISTINCT snapshot_date FROM company_daily_stats`
  (`db/bigquery.py:531-535`), so a correction that *inserts* dates would change chart structure.
- `context/foundation/lessons.md:211-235` — mocked BQ tests do not validate SQL; a real round-trip is
  mandatory for any hand-built DML.

## Desired End State

Every `kurs_zamkniecia` written from the switch date on is the official GPW or NewConnect close, and
every row rendered by the app (trailing 19 months) carries the official raw close. Each row records
which source produced it. A close captured before the fixing — or missed entirely because the 17:31
run failed — is repaired automatically on the next session from a definitionally final number.

Verification: for a sample of tickers across several sessions, `kurs_zamkniecia` equals the value at
`gpw.pl/archiwum-notowan` for that date; `SELECT COUNT(*) - COUNT(DISTINCT (ticker, snapshot_date))`
is 0; the 1-year value chart and the calendar render without gaps or new exclusions.

## What We're NOT Doing

- **Not correcting history before 2025-01-01.** ~1.7 M backfilled rows stay as PUL-92 left them.
  Nothing renders beyond `range=1y`, and the name-mapping reliability decays with age.
- **Not correcting `etf_quotes`.** Measured clean (9/9) — no defect to fix.
- **Not correcting NewConnect history.** No per-date NewConnect archive endpoint exists; verified
  harmless because all 14 held instruments are GPW main market or ETFs, zero on NewConnect.
- **Not changing the Cloud Scheduler cron.** 17:31 already clears the ~17:15 publication.
- **Not touching `kurs_otwarcia` / `kurs_min` / `kurs_max` in the corrective pass.** Verified
  identical to the archive — rewriting them is churn.
- **Not retiring bankier.** It remains the gap-filler for ~47 companies absent from both feeds.
- **Not fixing GH #172** (alert dedup / browser headers) or GH #192 (junk ticker rows).
- **Not adding a new Cloud Run job or scheduler** — those are human-only.

## Implementation Approach

Build the official-source parser as a pure `src/` module mirroring `src/gpw_etf_metrics.py`, rewire
the job to merge sources by priority before writing, then add the narrow correction primitive that
both the self-heal and the historical pass share. The corrective script follows the PUL-92 shape
(`scripts/backfill_historical_closes.py`): pure logic segregated and unit-tested via the importlib
idiom, `--dry-run` first, per-year chunking, and a disk cache so repeated passes cost no network.

Ordering matters: the schema migration lands before any writer references the new columns, per
`context/foundation/lessons.md:294-326`.

## Critical Implementation Details

**Column mapping must be structural, not positional.** Expanding `colspan` over the first `<thead>`
row is the load-bearing contract of Phase 2 — the two feeds differ in their identity block
(`Skrót` at data index 4 on GPW, 3 on NewConnect) yet realign from index 6, which is exactly the
kind of coincidence that produces a silently-wrong parser:

```python
# first thead row; each th claims int(colspan or 1) data columns, mapped to its label
idx, out = 0, {}
for th in thead.find_all("tr")[0].find_all("th"):
    out.setdefault(normalize(th.get_text(" ", strip=True)), idx)
    idx += int(th.get("colspan") or 1)
```

**The corrective MERGE must be update-only.** A `WHEN NOT MATCHED` branch would insert dates absent
from `company_daily_stats`, and the trading-day spine is derived from that table's distinct dates —
new dates would change the chart's shape, not just its values.

**Ordering inside the job.** Official sources are fetched and keyed first; bankier is consulted only
for tickers still unresolved. Any construction that lets bankier win a key (for example
`{**official, **bankier}`) reintroduces the defect.

## Phase 1: Schema — provenance and reference-price columns

### Overview

Add two additive NULLABLE columns to `company_daily_stats` so that source provenance is auditable
and the self-heal's input is persisted. Ship the schema before any writer fills it.

### Changes Required:

#### 1. Schema literal and merge column lists

**File**: `db/bigquery.py`

**Intent**: Extend `_COMPANY_DAILY_STATS_SCHEMA` with the two new columns, and add them to the
column lists of `merge_company_daily_stats` (both the temp-table explicit schema and the MATCHED /
NOT MATCHED branches) and `merge_company_daily_stats_insert_only`, so writers can populate them.

**Contract**: `source` STRING NULLABLE — one of `gpw`, `nc`, `bankier`, `archive`, `kurs_odn` —
and `kurs_odn` FLOAT64 NULLABLE (the feed's reference price, matching the existing
`etf_quotes.kurs_odn`). Both must be NULLABLE: `ensure_schema_current()`'s additive
`ALTER TABLE ADD COLUMN` path only succeeds for NULLABLE columns (`db/bigquery.py:2364-2365`).
Note that `merge_company_daily_stats`'s MATCHED branch will now also overwrite these two columns, so
any writer that omits them writes `NULL` — `scripts/seed_companies.py:143-157` is such a writer and
must be updated or explicitly accepted.

#### 2. Real-BigQuery round-trip

**File**: `scripts/test_bq_company_stats_merge.py`

**Intent**: Extend the existing round-trip so it writes and reads back both new columns on a
sentinel date, proving the migrated schema accepts them. Mocked tests do not parse SQL
(`context/foundation/lessons.md:211-235`).

**Contract**: Script exits 0 and prints the read-back row including `source` and `kurs_odn`.
Before running, assert the **live** table's column modes via `client.get_table(ref).schema` rather
than trusting the repo definition (`context/foundation/lessons.md:317-320`).

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest --ignore=tests/e2e`
- Linting passes: `uv run ruff check .`
- Layering passes: `uv run tach check`

#### Manual Verification:

- `ensure_company_daily_stats_schema_current()` adds both columns to the live table without error
- `scripts/test_bq_company_stats_merge.py` round-trip succeeds and returns both new columns
- Live table schema shows both columns as NULLABLE

**Implementation Note**: After completing this phase and all automated verification passes, pause
for manual confirmation before proceeding.

---

## Phase 2: Official quotations parser

### Overview

A pure parser module for the GPW main-market and NewConnect quotation tables, mapping columns by
header structure rather than position.

### Changes Required:

#### 1. New parser module

**File**: `src/gpw_quotations.py`

**Intent**: Fetch and parse both official quotation tables into the dict shape the job already
consumes, keyed by exchange abbreviation (`Skrót`). Mirrors `src/gpw_etf_metrics.py` — no `db.*`
imports, catches `ScraperError` and returns empty rather than propagating.

**Contract**: Module constants `GPW_QUOTATIONS_URL` and `NC_QUOTATIONS_URL` (the two verified GET
URLs). Public `fetch_quotations(market: str) -> dict[str, dict]` where `market` is `"gpw"` or `"nc"`;
unknown market logs a warning and returns `{}`, following `src/bankier_metrics.py:66-69`.

Each value carries: `company_name`, `isin`, `kurs_zamkniecia`, `kurs_odn`, `kurs_otwarcia`,
`kurs_min`, `kurs_max`, `zmiana_procentowa`, `zmiana_kwotowa`, `liczba_transakcji`,
`wartosc_obrotu`.

Three contracts that are not obvious from the file path:
- Column indices come from expanding `colspan` over the first `<thead>` row (see Critical
  Implementation Details). If the expected header labels are not found, log a warning and return
  `{}` — fail loud, never fall back to positional guessing.
- `wartosc_obrotu` is the `Wart. obr. - skumul. (tys.)` value **× 1000** (feed is in thousands, the
  column stores PLN).
- `zmiana_kwotowa = kurs_zamkniecia − kurs_odn`, computed exactly rather than derived from the
  percentage as `src/gpw_etf_metrics.py:110-112` does.

Reuse `_parse_polish_float` / `_parse_int` from `src/bankier_metrics.py:33-54` (comma decimal, NBSP
thousands) — extract to a shared helper if importing across scraper modules is awkward.

#### 2. Unit tests

**File**: `tests/test_gpw_quotations.py`

**Intent**: Cover both layouts and the failure modes, following the established fixture pattern.

**Contract**: Inline module-level HTML fixture strings preserving the real structural quirks — the
two-row `<thead>` with `colspan=3` offer blocks for GPW, the flat 20-column header with `Segment`
for NewConnect, Polish decimals, NBSP thousands, `-` for missing values. Patch `get` at
`src.gpw_quotations.get`. Mandatory negative cases: HTTP failure → `{}`, table missing → `{}`,
unexpected header labels → `{}`, unknown market → `{}`. Assert the `×1000` turnover conversion and
the exact `zmiana_kwotowa` derivation. Floats via `pytest.approx`.

### Success Criteria:

#### Automated Verification:

- New tests pass: `uv run pytest tests/test_gpw_quotations.py`
- Full unit suite passes: `uv run pytest --ignore=tests/e2e`
- Linting passes: `uv run ruff check .`
- Layering passes: `uv run tach check`

#### Manual Verification:

- Ad-hoc run against the live endpoints returns ~372 GPW and ~332 NewConnect entries
- Spot-check 5 tickers against gpw.pl in a browser — close, open, min, max, previous close all match
- `wartosc_obrotu` for a large-cap is in the hundreds of millions, not hundreds of thousands

**Implementation Note**: Pause for manual confirmation before proceeding.

---

## Phase 3: Rewire the job to official sources

### Overview

Make the daily job prefer official sources, use bankier strictly as a gap-filler, and detect
partial or hollow responses that today's all-or-nothing guard cannot see.

### Changes Required:

#### 1. Source merge with priority

**File**: `company_stats_main.py`

**Intent**: Fetch both official tables, build the row set keyed by ticker from them, and consult the
bankier listing only for companies still unresolved. Stamp `source` on every row and carry
`kurs_odn` from the official feeds.

**Contract**: Official entries are keyed by `Skrót` and joined directly to `companies.ticker`
(no `symbol_from_hop_url` indirection — that path remains only for the bankier fallback). A bankier
value must never replace an official one; rows sourced from bankier carry `source="bankier"` and
`kurs_odn = NULL`. Where an official row and a `companies` row both carry an ISIN and they differ,
log a warning and skip that ticker — 0 conflicts exist today, so any occurrence is a signal.

#### 2. Per-source coverage floors

**File**: `company_stats_main.py`

**Intent**: Abort the run and alert when a source returns implausibly few rows, so a hollow HTTP 200
or a half-dead feed cannot become the day's data.

**Contract**: Minimum row counts per source (GPW ≥ 300, NewConnect ≥ 250) checked before the merge;
a breach raises, which the existing handler at `company_stats_main.py:94-101` turns into an alert
and `exit(1)`. Thresholds are module constants with a comment recording the observed baselines
(372 / 332). The existing `if not rows` guard stays. Note this adds an alert surface to a job that
runs 18×/day with no alert dedup (GH #172) — the floors must be loose enough that a normal session
never trips them.

#### 3. Job tests

**File**: `tests/test_company_stats_main.py`

**Intent**: Cover the priority rule and the floors.

**Contract**: A ticker present in both official and bankier data keeps the official value and
`source`; a ticker only in bankier is written with `source="bankier"`; a below-floor GPW response
raises and triggers `send_alert` + `sys.exit(1)`; a normal partial gap (companies absent from all
sources) does not alert.

### Success Criteria:

#### Automated Verification:

- Job tests pass: `uv run pytest tests/test_company_stats_main.py`
- Full unit suite passes: `uv run pytest --ignore=tests/e2e`
- Linting and layering pass: `uv run ruff check .`, `uv run tach check`

#### Manual Verification:

- Local run against live sources writes to a sentinel date and shows ~697 official + ~40 bankier rows
- `source` distribution in the written rows matches expectation
- A simulated empty GPW response aborts without writing

**Implementation Note**: Pause for manual confirmation before proceeding.

---

## Phase 4: Narrow close-correction MERGE primitive

### Overview

A BigQuery primitive that updates only the close and its two derived columns on rows that already
exist — shared by the self-heal and the historical corrective pass.

### Changes Required:

#### 1. Update-only MERGE

**File**: `db/bigquery.py`

**Intent**: Add `merge_company_daily_stats_close_correction(rows)` alongside the existing merge
family, updating a deliberately narrow column set and never inserting.

**Contract**: Match on `(ticker, snapshot_date)`. `WHEN MATCHED THEN UPDATE SET kurs_zamkniecia,
zmiana_procentowa, zmiana_kwotowa, source` — and **no `WHEN NOT MATCHED` branch**, so dates absent
from the table are never introduced (the trading-day spine derives from this table's distinct
dates). Source rows deduped with `QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker, snapshot_date
ORDER BY fetched_at DESC) = 1`, mirroring `_merge_insert_only` (`db/bigquery.py:2799-2809`). Returns
`num_dml_affected_rows` so callers can report how many rows actually changed. Temp table dropped in
`finally`. Follow the existing temp-table + explicit-schema + 24 h expiry idiom.

#### 2. Real-BigQuery round-trip

**File**: `scripts/test_bq_close_correction.py`

**Intent**: Prove on a throwaway table that the primitive updates the three columns, leaves the
others untouched, and inserts nothing for unmatched keys.

**Contract**: Numbered-step script in the established shape (`scripts/test_bq_insert_only_merge.py`),
cleaning up in `finally`. Must assert that `wartosc_obrotu` and `liczba_transakcji` survive a
correction unchanged, and that a row for a non-existent `(ticker, snapshot_date)` is not created.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest --ignore=tests/e2e`
- Query-string regression test asserts the absence of a `WHEN NOT MATCHED` clause
- Linting passes: `uv run ruff check .`

#### Manual Verification:

- `uv run python scripts/test_bq_close_correction.py` completes all steps and prints success
- Round-trip confirms untouched columns and no phantom inserts

**Implementation Note**: Pause for manual confirmation before proceeding.

---

## Phase 5: Self-heal the previous session from `Kurs odn.`

### Overview

Use the feed's own previous-close column to repair yesterday's stored close whenever it disagrees —
which covers a failed 17:31 run and any pre-fixing capture.

### Changes Required:

#### 1. Reconciliation step in the job

**File**: `company_stats_main.py`

**Intent**: After fetching the official tables, compare each ticker's `kurs_odn` against the stored
close for the most recent earlier session and correct the mismatches through the Phase 4 primitive.

**Contract**: Operates only on the single most recent `snapshot_date` strictly before today that
exists in the table — never a wider sweep, so it cannot fight the historical corrective pass.
Corrected rows get `source="kurs_odn"`; `zmiana_procentowa` / `zmiana_kwotowa` for the corrected day
are recomputed against that day's own preceding close. Mismatches are logged with a count summary
and do **not** alert — occasional corrections are the expected steady state, and this job runs
18×/day with no alert dedup. The step is idempotent: once corrected, subsequent runs find no
mismatch. A failure here must not abort the main ingest — catch, log, continue.

#### 2. Reader helper

**File**: `db/bigquery.py`

**Intent**: Fetch `(ticker, kurs_zamkniecia)` for the previous session in one query.

**Contract**: Returns a dict keyed by ticker for the most recent `snapshot_date < @today`, with the
date it resolved to, so the caller can log which session was reconciled.

### Success Criteria:

#### Automated Verification:

- Self-heal tests pass: matching values produce no correction call; a mismatch produces exactly one
  correction row with `source="kurs_odn"`; a correction failure does not abort ingest
- Full unit suite passes: `uv run pytest --ignore=tests/e2e`
- Linting and layering pass

#### Manual Verification:

- Deliberately write a wrong close for the previous session on a sentinel ticker, run the job, and
  confirm it is corrected to the official value
- Log line reports the reconciled session date and the number of corrections
- A second consecutive run reports zero corrections

**Implementation Note**: Pause for manual confirmation before proceeding.

---

## Phase 6: Archive reader and corrective script

### Overview

A reader for the date-parameterized GPW archive and a script that corrects stored closes from
2025-01-01 onward, gated on exact name mapping.

### Changes Required:

#### 1. Archive reader

**File**: `src/gpw_archive.py`

**Intent**: Fetch one completed session's quotation sheet from the GPW archive and return it keyed
by the sheet's `Nazwa`.

**Contract**: `fetch_archive_session(session_date, instrument_type="10") -> dict[str, dict]` against
`https://www.gpw.pl/archiwum-notowan` with `type`, `instrument=` and `date=DD-MM-YYYY`. Type codes
are module constants: `10` akcje, `241` ETF, `560` ETC, `561` ETN. Returns `kurs_otwarcia`,
`kurs_max`, `kurs_min`, `kurs_zamkniecia`, `zmiana_procentowa`, `wartosc_obrotu` (×1000). A
non-session date (weekend, holiday, or a session not yet published) yields no quotation table —
return `{}`, which callers must treat as "skip", not as an error.

#### 2. Corrective script

**File**: `scripts/correct_official_closes.py`

**Intent**: Walk sessions from `--since` to today, map archive `Nazwa` to `companies.ticker`, and
correct stored closes through the Phase 4 primitive.

**Contract**: CLI `--since` (default `2025-01-01`), `--until`, `--cache-dir`, `--dry-run`,
`--tickers`, `--chunk-size`. Preamble follows the established scripts pattern (`sys.path.insert` →
`load_dotenv()` → `configure_logging()` → `db.*` imports).

Behaviours that are not obvious:
- **Disk cache of raw HTML per session date.** The first pass costs ~390 fetches (~110 MB, ~15 min);
  dry-run, audit and apply then re-read from disk. Never re-fetch a cached date.
- **Hard-gated name mapping.** The `Nazwa → Skrót` map is built once from the live GPW feed and
  intersected with `companies`. Only exact hits are written; unmatched archive names and unmapped
  companies are counted and logged. No fuzzy matching — a mis-mapped name writes a plausible price
  against the wrong ticker, which is the failure class this change removes.
- **Only the close and its derived columns are corrected**, with `source="archive"`.
  `zmiana_kwotowa` / `zmiana_procentowa` are recomputed from consecutive corrected closes within the
  fetched series; the first session in the range has no predecessor and leaves them untouched.
- **Per-year chunking** of the MERGE, as `scripts/backfill_historical_closes.py:281-291` does, to
  stay well inside BigQuery's 4 000-modified-partitions-per-job cap.
- Politeness delay between fetches; the script is the only caller hitting the archive.

#### 3. Script tests

**File**: `tests/test_correct_official_closes.py`

**Intent**: Cover the pure logic — no network, no BigQuery.

**Contract**: Loaded via the importlib idiom (`tests/test_backfill_historical_closes.py:13-17`),
with the script segregating testable functions under a banner comment. Cover: session-date
enumeration skipping weekends, name-map construction and its rejection of ambiguous or missing
names, derived-column recomputation including the first-session case, per-year grouping, and
cache-path resolution.

### Success Criteria:

#### Automated Verification:

- Script tests pass: `uv run pytest tests/test_correct_official_closes.py`
- Full unit suite passes: `uv run pytest --ignore=tests/e2e`
- Linting and layering pass
- `--dry-run` over the full range completes and writes zero rows

#### Manual Verification:

- Dry-run report shows a plausible session count (~390) and a mapped-ticker count near 697
- Unmatched archive names are listed and are recognisably delisted or renamed instruments
- Spot-check: the script's computed correction for `KRU` on 2026-01-02 is `498.40`, and for
  `ALE` on 2026-07-24 is `44.735`
- Cache directory is populated; a second dry-run performs no network requests

**Implementation Note**: Pause for manual confirmation before proceeding.

---

## Phase 7: Production correction run and verification

### Overview

Apply the correction to production in stages and verify the user-visible result.

### Changes Required:

#### 1. Sample audit

**File**: (operational — no code change)

**Intent**: Run the corrective script restricted to a few tickers, then verify against the archive
and confirm nothing outside the intended columns moved.

**Contract**: `--tickers KRU,ALE,PKO` over the full range. Confirm the three price columns changed,
`kurs_otwarcia` / `kurs_min` / `kurs_max` / `wartosc_obrotu` / `liczba_transakcji` did not, no rows
were inserted, and `source="archive"` is stamped.

#### 2. Full run

**File**: (operational — no code change)

**Intent**: Apply across all mapped tickers from 2025-01-01.

**Contract**: Record rows affected per year. Then assert zero duplicate `(ticker, snapshot_date)`
keys — the calendar query joins without dedup (`db/bigquery.py:410-423`), so duplicates would
double-count value and P/L.

#### 3. User-visible verification

**File**: (operational — no code change)

**Intent**: Confirm the rendered surfaces are correct and no structure changed.

**Contract**: The 1-year value chart still returns the same number of points as before the run
(correction changes values, not day membership — day membership depends only on `covered > 0`,
`db/bigquery.py:596`), the `(i)` notes and exclusions are unchanged, the calendar renders, and the
reported 1-year return has moved down by roughly the dividends paid over the window on the affected
holdings. Note `GET /api/portfolio/history` caches for 300 s (`src/api.py:1025`), so verification
must use a fresh `range` value or wait out the cache.

### Success Criteria:

#### Automated Verification:

- Duplicate-key check returns 0
- `uv run pytest --ignore=tests/e2e` still green after the run

#### Manual Verification:

- Sample audit shows only the three intended columns changed
- Spot-check of 5 (ticker, date) pairs against `gpw.pl/archiwum-notowan` matches exactly
- 1-year chart point count unchanged; notes and exclusions unchanged
- Calendar renders with plausible daily P/L
- Next day: the 17:31 job writes official closes and the self-heal reports zero corrections

**Implementation Note**: This phase touches production data. Pause for explicit human confirmation
between the sample audit and the full run.

---

## Testing Strategy

### Unit Tests

- Parser: both header layouts, colspan expansion, `×1000` turnover, exact `zmiana_kwotowa`, and four
  negative paths (HTTP failure, missing table, unexpected headers, unknown market)
- Job: source priority, bankier gap-fill only, coverage floors, ISIN conflict skip
- MERGE primitive: query-string regression asserting no `WHEN NOT MATCHED`
- Self-heal: match, mismatch, failure isolation, idempotency
- Script: date enumeration, name-map gating, derived recomputation, per-year grouping

### Integration Tests

- Real-BigQuery round-trips for the schema migration (Phase 1) and the correction primitive
  (Phase 4), both on sentinel or throwaway tables with cleanup in `finally`

### Manual Testing Steps

1. Run the parser against live endpoints; compare 5 tickers to gpw.pl in a browser
2. Run the job locally to a sentinel date; inspect the `source` distribution
3. Break the previous session's close deliberately; run the job; confirm self-heal repairs it
4. Dry-run the corrective script; check session count, mapped tickers, unmatched names
5. Sample-audit three tickers on production; verify only the three columns moved
6. Full run; verify duplicate count is 0 and the chart's point count is unchanged
7. Next trading day: confirm the 17:31 write is official and self-heal reports zero corrections

## Performance Considerations

The job gains two HTTP fetches per run (18 runs/day) and one small BigQuery query for the self-heal;
the Cloud Run job's 300 s timeout has ample headroom. The corrective script is a one-off local run:
~390 archive fetches (~110 MB, ~15 min) on the first pass, seconds thereafter from cache, plus a
few minutes of BigQuery MERGE across ~390 partitions — far inside the 4 000-partition job cap.

## Migration Notes

Phase 1 must land and be applied to the live table before Phases 3–5 write the new columns. Both
columns are NULLABLE, so ordering carries no outage risk — unlike the REQUIRED-column case in
`context/foundation/lessons.md:294-326` — but the additive `ALTER TABLE ADD COLUMN` path only works
for NULLABLE columns, so they must not be tightened later.

Existing rows keep `source = NULL`, which reads as "written before provenance tracking". The
corrective pass stamps `source="archive"` on every row it touches, so after Phase 7 a NULL `source`
inside the corrected window means the row was never matched — a useful audit signal.

## References

- Research: `context/changes/official-close-source/research.md`
- Decisions: `context/changes/official-close-source/change.md`
- Parser to mirror: `src/gpw_etf_metrics.py:56-140`
- Script shape to mirror: `scripts/backfill_historical_closes.py`
- Insert-only MERGE to mirror: `db/bigquery.py:2764-2825`
- Round-trip script shape: `scripts/test_bq_insert_only_merge.py`
- Test fixture pattern: `tests/test_gpw_etf_metrics.py:123-131`
- Prior backfill: `context/archive/2026-07-24-backfill-historical-closes/`
- Lessons: `context/foundation/lessons.md:211-235`, `:294-326`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Schema — provenance and reference-price columns

#### Automated

- [ ] 1.1 Unit tests pass
- [ ] 1.2 Linting passes
- [ ] 1.3 Layering passes

#### Manual

- [ ] 1.4 Schema migration adds both columns to the live table
- [ ] 1.5 Round-trip returns both new columns
- [ ] 1.6 Live table shows both columns as NULLABLE

### Phase 2: Official quotations parser

#### Automated

- [ ] 2.1 New parser tests pass
- [ ] 2.2 Full unit suite passes
- [ ] 2.3 Linting passes
- [ ] 2.4 Layering passes

#### Manual

- [ ] 2.5 Live run returns ~372 GPW and ~332 NewConnect entries
- [ ] 2.6 Five tickers spot-checked against gpw.pl
- [ ] 2.7 Turnover magnitude confirms the ×1000 conversion

### Phase 3: Rewire the job to official sources

#### Automated

- [ ] 3.1 Job tests pass
- [ ] 3.2 Full unit suite passes
- [ ] 3.3 Linting and layering pass

#### Manual

- [ ] 3.4 Local run to a sentinel date shows expected official/bankier split
- [ ] 3.5 Source distribution matches expectation
- [ ] 3.6 Simulated empty GPW response aborts without writing

### Phase 4: Narrow close-correction MERGE primitive

#### Automated

- [ ] 4.1 Unit tests pass
- [ ] 4.2 Query-string regression asserts no WHEN NOT MATCHED
- [ ] 4.3 Linting passes

#### Manual

- [ ] 4.4 Real-BQ round-trip completes successfully
- [ ] 4.5 Untouched columns survive and no phantom inserts occur

### Phase 5: Self-heal the previous session from `Kurs odn.`

#### Automated

- [ ] 5.1 Self-heal tests pass
- [ ] 5.2 Full unit suite passes
- [ ] 5.3 Linting and layering pass

#### Manual

- [ ] 5.4 Deliberately wrong previous close is corrected
- [ ] 5.5 Log reports reconciled session and correction count
- [ ] 5.6 Second consecutive run reports zero corrections

### Phase 6: Archive reader and corrective script

#### Automated

- [ ] 6.1 Script tests pass
- [ ] 6.2 Full unit suite passes
- [ ] 6.3 Linting and layering pass
- [ ] 6.4 Dry-run over the full range writes zero rows

#### Manual

- [ ] 6.5 Dry-run reports plausible session and mapped-ticker counts
- [ ] 6.6 Unmatched names are recognisably delisted or renamed
- [ ] 6.7 KRU 2026-01-02 computes to 498.40 and ALE 2026-07-24 to 44.735
- [ ] 6.8 Second dry-run performs no network requests

### Phase 7: Production correction run and verification

#### Automated

- [ ] 7.1 Duplicate-key check returns 0
- [ ] 7.2 Full unit suite still green

#### Manual

- [ ] 7.3 Sample audit shows only the three intended columns changed
- [ ] 7.4 Five (ticker, date) pairs match the archive exactly
- [ ] 7.5 1-year chart point count, notes and exclusions unchanged
- [ ] 7.6 Calendar renders with plausible daily P/L
- [ ] 7.7 Next-day job writes official closes and self-heal reports zero corrections
