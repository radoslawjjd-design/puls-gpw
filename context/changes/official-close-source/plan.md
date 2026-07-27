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
(identical at 17:15, 17:25, 17:35, 17:45, 17:56, 21:55 and 22:45 — later ticks changed only the
transaction *timestamp*, as dogrywka trades execute at the fixed price). The same poll pinned the
archive's publication window: `archiwum-notowan` for 2026-07-27 still returned no sheet at **17:56**
and the full 403-row sheet by **21:55**. The archive is therefore a next-morning oracle, never a
same-evening one — which is why the self-heal targets the *previous* session.
The existing scheduler `1,31 9-17 * * 1-5` fires
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
- **Not deleting phantom non-session rows.** The corrective script reports them (one exists today:
  2026-06-27, a Saturday, 739 rows), but removing them — and adding a trading-day guard so no more
  appear — is a separate change. No holiday calendar or market-open check exists anywhere in the
  project today.

## Implementation Approach

Build the official-source parser as a pure `src/` module mirroring `src/gpw_etf_metrics.py`, rewire
the job to merge sources by priority before writing, then add the narrow correction primitive that
both the self-heal and the historical pass share. The corrective script follows the PUL-92 shape
(`scripts/backfill_historical_closes.py`): pure logic segregated and unit-tested via the importlib
idiom, `--dry-run` first, per-year chunking, and a disk cache so repeated passes cost no network.

Ordering matters: the schema migration lands before any writer references the new columns, per
`context/foundation/lessons.md:294-326`.

## Critical Implementation Details

**`Kurs odn.` is a reference price, not a previous close — never write it as a price.** GPW adjusts
the reference price for corporate actions, so on ex-dividend and split dates it legitimately differs
from the previous session's close. Measured over 14 sessions, 41 of 5 230 comparisons diverged by
more than 0.3 pp and up to 11 pp; `ECHO` on 2026-06-24 closed **+0.10% officially against −7.40%
computed naively**. Two places in this plan are exposed to that confusion — the self-heal (Phase 6)
and the corrective script's derived columns (Phase 7) — and both resolve it the same way: **the
archive is the oracle, arithmetic on closes is not.** Any future step that needs a previous close or
a daily change must take it from the archive's own columns.

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

**Intent**: Extend `_COMPANY_DAILY_STATS_SCHEMA` with the two new columns and propagate them through
every hard-coded column list, so writers can populate them.

**Contract**: `source` STRING NULLABLE — one of `gpw`, `nc`, `bankier`, `archive` — and `kurs_odn`
FLOAT64 NULLABLE (the feed's reference price, matching the existing `etf_quotes.kurs_odn`). Both
must be NULLABLE: `ensure_schema_current()`'s additive `ALTER TABLE ADD COLUMN` path only succeeds
for NULLABLE columns (`db/bigquery.py:2364-2365`).

**There are exactly four edit sites, and three of them fail silently if missed:**

| site | location | failure if missed |
|---|---|---|
| schema literal | `db/bigquery.py:2367-2379` | loud — the load job rejects an unknown field |
| MERGE `UPDATE SET` | `db/bigquery.py:2512-2521` | **silent — worst case** |
| MERGE `INSERT` + `VALUES` (both lists) | `db/bigquery.py:2523-2528` | silent NULLs on first write of a day |
| `_merge_insert_only` `columns` arg | `db/bigquery.py:2834-2838` | silent — temp table holds the data, the narrower INSERT drops it |

The `UPDATE SET` omission is the dangerous one: with 18 scheduler ticks per day only the first takes
the INSERT branch, so forgetting it would freeze both columns at their 9:01 values and the official
close would never land in `source`. No existing test would catch it — the SQL assertions in
`tests/test_bigquery.py:1230-1254` and `tests/test_bigquery_insert_only_merge.py:60-67` are
substring-only.

#### 2. Guard the silent failure modes with query-string assertions

**File**: `tests/test_bigquery.py`, `tests/test_bigquery_insert_only_merge.py`

**Intent**: Add cheap regression assertions on the generated SQL so a missed column list fails a
test rather than production data, per `context/foundation/lessons.md:211-235`.

**Contract**: Assert `"source = S.source"` and `"kurs_odn = S.kurs_odn"` appear in the
`merge_company_daily_stats` SQL, and that both names appear in the insert-only SQL. Also update
`tests/test_bigquery.py:1088-1103` (`test_company_daily_stats_schema_has_required_columns`) — it
compares the column-name **set** with `==` and is the only test that breaks mechanically; extend it
to assert `mode == "NULLABLE"` for both new fields, since the migration path depends on that.

#### 3. Stop the second writer from clobbering provenance

**File**: `scripts/seed_companies.py`

**Intent**: Remove the `--with-stats` write path into `company_daily_stats`.

**Contract**: The script's job is company reconciliation, not price authority. It shares the
full-upsert primitive and builds rows by splatting `trading_data`, which never contains `source` or
`kurs_odn` — so running it after the daily job would overwrite both with `NULL` for every listed
ticker, and it swallows the failure in `except BigQueryError → logger.warning`
(`scripts/seed_companies.py:152-162`), so the clobber would not even surface as a non-zero exit.
Drop the flag and its write block; keep the company-seeding behaviour untouched.

#### 4. Real-BigQuery round-trip

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
- SQL regression assertions cover all four column-list sites
- Linting passes: `uv run ruff check .`
- Layering passes: `uv run tach check`

#### Manual Verification:

- `ensure_company_daily_stats_schema_current()` adds both columns to the live table without error
- `scripts/test_bq_company_stats_merge.py` round-trip succeeds and returns both new columns
- Live table schema shows both columns as NULLABLE
- `scripts/seed_companies.py` no longer writes to `company_daily_stats`

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

## Phase 5: Archive reader

### Overview

A reader for the date-parameterized GPW archive. It lands before the self-heal because the archive —
not the feed's reference price — is the only trustworthy oracle for a completed session.

### Changes Required:

#### 1. Archive reader module

**File**: `src/gpw_archive.py`

**Intent**: Fetch one completed session's quotation sheet from the GPW archive and return it keyed
by the sheet's `Nazwa`.

**Contract**: `fetch_archive_session(session_date, instrument_type="10") -> dict[str, dict]` against
`https://www.gpw.pl/archiwum-notowan` with `type`, `instrument=` and `date=DD-MM-YYYY`. Type codes
are module constants: `10` akcje, `241` ETF, `560` ETC, `561` ETN. Returns `kurs_otwarcia`,
`kurs_max`, `kurs_min`, `kurs_zamkniecia`, `zmiana_procentowa`, `wartosc_obrotu` (×1000).

Three contracts that are not obvious:
- A non-session date (weekend, holiday, or a session not yet published) yields no quotation table.
  Return `{}`, which callers treat as "no session", never as an error.
- **A failed fetch must not be reported as `{}`.** Once retries are exhausted the reader raises
  `ScraperError`. Conflating the two would make a transient network fault look like a market
  holiday: the corrective script's phantom-date report (Phase 7) would name a perfectly normal
  session, and the self-heal would skip a date it should have repaired — both silently.
- The archive resets connections both under load and at rest. A measured 0.4 s cadence produced
  `ConnectionReset`; a single isolated fetch **ten minutes** after the previous one also failed
  (`ConnectionError`, 2026-07-27 22:35, succeeding again on the next tick). Pacing alone therefore
  does not prevent it: the module reuses one HTTP session, paces requests at **≥1.5 s**, *and*
  retries with increasing backoff — the retry is load-bearing, not a nicety. `src/http_client.py`
  provides no inter-request throttle despite its docstring, so pacing is this module's
  responsibility.

#### 2. Unit tests

**File**: `tests/test_gpw_archive.py`

**Intent**: Cover parsing and the no-session path.

**Contract**: Inline HTML fixture with the archive's 8-column layout, Polish decimals and NBSP
thousands. Cases: happy path, non-session date → `{}`, **exhausted retries → `ScraperError` (not
`{}`)**, a transient failure followed by a success → the retry returns the sheet, `×1000` turnover
conversion. Patch `get` at `src.gpw_archive.get`.

### Success Criteria:

#### Automated Verification:

- New tests pass: `uv run pytest tests/test_gpw_archive.py`
- Full unit suite passes: `uv run pytest --ignore=tests/e2e`
- Linting and layering pass

#### Manual Verification:

- Live fetch for a known session returns ~400 rows; `ALE` on 2026-07-24 is `44.735`
- A Saturday date returns `{}` rather than raising
- A ~20-session sequential fetch retrieves every session — resets may occur, the retry must absorb
  them; zero sessions may be silently reported as empty

**Implementation Note**: Pause for manual confirmation before proceeding.

---

## Phase 6: Self-heal the previous session

### Overview

Detect a wrong close for the previous session using the feed's reference price, then repair it from
the archive. The detector is cheap; the archive is the authority.

### Changes Required:

#### 1. Reconciliation step in the job

**File**: `company_stats_main.py`

**Intent**: Compare each ticker's `kurs_odn` against the stored close for the most recent earlier
session; where they disagree, fetch that session from the archive and correct from it through the
Phase 4 primitive.

**Contract**: `kurs_odn` is a **detector only, never a value source.** GPW's reference price is the
previous close adjusted for corporate actions, so on an ex-dividend or split date it legitimately
differs from the previous close — measured on 2026-06-23/24, 41 of 5 230 comparisons diverged by
more than 0.3 pp, up to 11 pp (`ECHO` reference 4.94 against a previous close of 5.34). Writing
`kurs_odn` into `kurs_zamkniecia` would reintroduce exactly the dividend-adjustment defect this
change removes.

So: a mismatch on any ticker triggers **one** archive fetch for that session date; each mismatched
ticker is then corrected to the archive's close, with `zmiana_procentowa` taken from the archive and
`zmiana_kwotowa` derived from it. Tickers whose archive value already equals the stored close (the
ex-dividend case) are left alone and counted separately in the log.

Operates only on the single most recent `snapshot_date` strictly before today that exists in the
table — never a wider sweep, so it cannot fight the historical corrective pass. Corrected rows get
`source="archive"`. Mismatches are logged with a count summary and do **not** alert. The step is
idempotent, and a failure here must not abort the main ingest — catch, log, continue.

#### 2. Reader helper

**File**: `db/bigquery.py`

**Intent**: Fetch `(ticker, kurs_zamkniecia)` for the previous session in one query.

**Contract**: Returns a dict keyed by ticker for the most recent `snapshot_date < @today`, plus the
date it resolved to, so the caller can log which session was reconciled.

### Success Criteria:

#### Automated Verification:

- Self-heal tests pass: matching values produce no archive fetch; a genuine mismatch corrects from
  the archive with `source="archive"`; an **ex-dividend-shaped** mismatch where the archive confirms
  the stored close produces no write; a failure does not abort ingest
- Full unit suite passes: `uv run pytest --ignore=tests/e2e`
- Linting and layering pass

#### Manual Verification:

- Deliberately write a wrong close for the previous session on a sentinel ticker, run the job, and
  confirm it is corrected to the archive's value
- Log reports the reconciled session date, corrections made, and reference-price divergences ignored
- A second consecutive run reports zero corrections

**Implementation Note**: Pause for manual confirmation before proceeding.

---

## Phase 7: Corrective script

### Overview

A script that corrects stored closes from 2025-01-01 onward using the Phase 5 archive reader, gated
on exact name mapping.

### Changes Required:

#### 1. Corrective script

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
  `zmiana_procentowa` is taken **directly from the archive's `Zmiana kursu %` column**, and
  `zmiana_kwotowa` is derived from it as `close − close/(1 + pct/100)`. It must **not** be
  recomputed from consecutive closes: GPW's percentage is measured against the reference price,
  which is adjusted for corporate actions, and naive close-to-close differencing is wrong across
  every ex-dividend and split. Measured over 14 sessions, 41 of 5 230 pairs (0.78%) diverged by more
  than 0.3 pp, reaching 11 pp — and `ECHO` on 2026-06-24 moved **+0.10% officially against −7.40%
  naively**. The calendar renders this quantity directly. Using the archive column also removes the
  "first session in the range has no predecessor" edge case entirely.
- **Non-session detection.** The script already probes every calendar date; a date where the archive
  reports no session but `company_daily_stats` holds rows is a phantom trading day. Report these —
  they inflate the trading-day spine and double-count a session's P/L in the calendar. **One already
  exists in production: 2026-06-27, a Saturday, with 739 rows.** Reporting only; deletion is a
  separate decision (`delete_company_daily_stats_for_date` already exists at
  `db/bigquery.py:2434-2453`).
- **Per-year chunking** of the MERGE, as `scripts/backfill_historical_closes.py:281-291` does, to
  stay well inside BigQuery's 4 000-modified-partitions-per-job cap.
- **Fetch pacing is a correctness concern, not politeness.** ≥1.5 s between requests, one reused
  HTTP session, retry with increasing backoff — a 0.4 s cadence was measured to trigger
  `ConnectionReset` from gpw.pl. The disk cache doubles as the resume mechanism: an interrupted run
  restarts without re-fetching what it already has.

#### 2. Script tests

**File**: `tests/test_correct_official_closes.py`

**Intent**: Cover the pure logic — no network, no BigQuery.

**Contract**: Loaded via the importlib idiom (`tests/test_backfill_historical_closes.py:13-17`),
with the script segregating testable functions under a banner comment. Cover: session-date
enumeration skipping weekends, name-map construction and its rejection of ambiguous or missing
names, `zmiana_kwotowa` derivation from the archive percentage (including a case where naive
close-to-close would disagree, so the test pins the corporate-action behaviour), non-session
detection, per-year grouping, and cache-path resolution.

### Success Criteria:

#### Automated Verification:

- Script tests pass: `uv run pytest tests/test_correct_official_closes.py`
- Full unit suite passes: `uv run pytest --ignore=tests/e2e`
- Linting and layering pass

#### Manual Verification:

- `--dry-run` over the full range completes and writes zero rows (needs network and BigQuery
  credentials — not CI-runnable)
- Dry-run report shows a plausible session count (~390) and a mapped-ticker count near 697
- Unmatched archive names are listed and are recognisably delisted or renamed instruments
- Non-session dates holding rows are reported, and the list includes 2026-06-27
- Spot-check: the script's computed correction for `KRU` on 2026-01-02 is `498.40`, and for
  `ALE` on 2026-07-24 is `44.735`
- Cache directory is populated; a second dry-run performs no network requests

**Implementation Note**: Pause for manual confirmation before proceeding.

---

## Phase 8: Production correction run and verification

### Overview

Apply the correction to production in stages and verify the user-visible result.

### Changes Required:

#### 1. Pre-correction snapshot

**File**: (operational — no code change)

**Intent**: Capture a restorable copy of the correction window before any destructive write.

**Contract**: `CREATE TABLE company_daily_stats_pre_pul98 AS SELECT * FROM company_daily_stats
WHERE snapshot_date >= '2025-01-01'` — roughly 270 000 rows, seconds and pennies in BigQuery. The
corrective MERGE overwrites in place with no undo; PUL-92 avoided this class of risk entirely by
being insert-only, and this change deliberately gives that protection up, so it must put something
back. Record the exact restore statement in the change folder alongside the run log. Dropping the
snapshot table afterwards is a human-only action (`CLAUDE.md:11`).

#### 2. Sample audit

**File**: (operational — no code change)

**Intent**: Run the corrective script restricted to a few tickers, then verify against the archive
and confirm nothing outside the intended columns moved.

**Contract**: `--tickers KRU,ALE,PKO` over the full range. Confirm the three price columns changed,
`kurs_otwarcia` / `kurs_min` / `kurs_max` / `wartosc_obrotu` / `liczba_transakcji` did not, no rows
were inserted, and `source="archive"` is stamped.

#### 3. Full run

**File**: (operational — no code change)

**Intent**: Apply across all mapped tickers from 2025-01-01.

**Contract**: Record rows affected per year. Then assert zero duplicate `(ticker, snapshot_date)`
keys — the calendar query joins without dedup (`db/bigquery.py:410-423`), so duplicates would
double-count value and P/L.

#### 4. User-visible verification

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

- Snapshot table exists and row count matches the window before the run starts
- Sample audit shows only the three intended columns changed
- Spot-check of 5 (ticker, date) pairs against `gpw.pl/archiwum-notowan` matches exactly
- An ex-dividend date (e.g. `ECHO` 2026-06-24) carries the official `+0.10%`, not a naive `−7.40%`
- 1-year chart point count unchanged; notes and exclusions unchanged
- Calendar renders with plausible daily P/L
- Next day: the 17:31 job writes official closes and the self-heal reports zero corrections

**Implementation Note**: This phase touches production data. Take the snapshot first, then pause for
explicit human confirmation between the sample audit and the full run.

---

## Testing Strategy

### Unit Tests

- Schema: column-list regression assertions on the generated SQL for all four edit sites
- Parser: both header layouts, colspan expansion, `×1000` turnover, exact `zmiana_kwotowa`, and four
  negative paths (HTTP failure, missing table, unexpected headers, unknown market)
- Job: source priority, bankier gap-fill only, coverage floors, ISIN conflict skip
- MERGE primitive: query-string regression asserting no `WHEN NOT MATCHED`
- Archive reader: happy path, non-session date → `{}`, HTTP failure → `{}`, turnover conversion
- Self-heal: match, genuine mismatch corrected from the archive, ex-dividend-shaped mismatch left
  alone, failure isolation, idempotency
- Script: date enumeration, name-map gating, derivation from the archive percentage, non-session
  detection, per-year grouping

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

- [x] 1.1 Unit tests pass
- [x] 1.2 SQL regression assertions cover all four column-list sites
- [x] 1.3 Linting passes
- [x] 1.4 Layering passes

#### Manual

- [x] 1.5 Schema migration adds both columns to the live table
- [x] 1.6 Round-trip returns both new columns
- [x] 1.7 Live table shows both columns as NULLABLE
- [x] 1.8 `seed_companies.py` no longer writes to `company_daily_stats`

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

### Phase 5: Archive reader

#### Automated

- [ ] 5.1 Archive reader tests pass
- [ ] 5.2 Full unit suite passes
- [ ] 5.3 Linting and layering pass

#### Manual

- [ ] 5.4 Live fetch returns ~400 rows and ALE 2026-07-24 is 44.735
- [ ] 5.5 A Saturday date returns empty rather than raising
- [ ] 5.6 A ~20-session sequential fetch retrieves every session, retries absorbing any reset

### Phase 6: Self-heal the previous session

#### Automated

- [ ] 6.1 Self-heal tests pass
- [ ] 6.2 Full unit suite passes
- [ ] 6.3 Linting and layering pass

#### Manual

- [ ] 6.4 Deliberately wrong previous close is corrected from the archive
- [ ] 6.5 Log reports reconciled session, corrections, and ignored reference divergences
- [ ] 6.6 Second consecutive run reports zero corrections

### Phase 7: Corrective script

#### Automated

- [ ] 7.1 Script tests pass
- [ ] 7.2 Full unit suite passes
- [ ] 7.3 Linting and layering pass

#### Manual

- [ ] 7.4 Dry-run over the full range writes zero rows
- [ ] 7.5 Dry-run reports plausible session and mapped-ticker counts
- [ ] 7.6 Unmatched names are recognisably delisted or renamed
- [ ] 7.7 Non-session dates holding rows are reported, including 2026-06-27
- [ ] 7.8 KRU 2026-01-02 computes to 498.40 and ALE 2026-07-24 to 44.735
- [ ] 7.9 Second dry-run performs no network requests

### Phase 8: Production correction run and verification

#### Automated

- [ ] 8.1 Duplicate-key check returns 0
- [ ] 8.2 Full unit suite still green

#### Manual

- [ ] 8.3 Snapshot table exists and row count matches the window
- [ ] 8.4 Sample audit shows only the three intended columns changed
- [ ] 8.5 Five (ticker, date) pairs match the archive exactly
- [ ] 8.6 ECHO 2026-06-24 carries the official +0.10%, not a naive −7.40%
- [ ] 8.7 1-year chart point count, notes and exclusions unchanged
- [ ] 8.8 Calendar renders with plausible daily P/L
- [ ] 8.9 Next-day job writes official closes and self-heal reports zero corrections
