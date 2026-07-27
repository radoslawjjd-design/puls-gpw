---
date: 2026-07-27T17:10:00+02:00
researcher: Radek
git_commit: fef66fbe5728feff9585a1e0e01ac1516aba21a1
branch: pul-98-official-close-source
repository: radoslawjjd-design/puls-gpw
topic: "Switch company_daily_stats.kurs_zamkniecia from the bankier listing to the official GPW close"
tags: [research, codebase, company_daily_stats, scraper, gpw, newconnect, bigquery, PUL-98]
status: complete
last_updated: 2026-07-27
last_updated_by: Radek
---

# Research: official close source for `company_daily_stats.kurs_zamkniecia`

**Date**: 2026-07-27 17:10 +02:00
**Researcher**: Radek
**Git Commit**: `fef66fb`
**Branch**: `pul-98-official-close-source`
**Repository**: radoslawjjd-design/puls-gpw

## Research Question

PUL-98 / GH #193: `company_daily_stats.kurs_zamkniecia` is not the official GPW close — the
bankier.pl listing page publishes a best-bid / best-ask / last-continuous-trade figure instead.
The ticket proposes switching to the gpw.pl `GPWQuotations` AJAX table. Before planning: what
exactly does that endpoint give us, is NewConnect coverable, does the ticker identity join, what
breaks downstream, and can the already-polluted rows be corrected?

## Summary

Six findings, all verified live against gpw.pl / newconnect.pl / BigQuery on 2026-07-27.

1. **The live `GPWQuotations` table is a ~15-minute-delayed intraday feed, not a close feed.**
   Its column *"Kurs ost. trans. / zamk."* is literally *last transaction* during the session and
   only becomes the close once the delayed feed has passed the 17:00 closing auction. Three fetches
   between ~16:58 and ~17:05 Warsaw returned **different values for 7 of 8 reference tickers**. A
   naive "swap the URL" change would reproduce the exact bug class PUL-98 is about, just with a
   different wrong number. **Timing is a first-class part of this change, not an afterthought.**

2. **The GPW archive endpoint is the authoritative, unambiguous source — and its query parameters
   are now solved.** `https://www.gpw.pl/archiwum-notowan?type=10&instrument=&date=DD-MM-YYYY`
   returns a server-rendered HTML table of one completed session. For 2026-07-24 it matched the
   ticket's official closes **8/8 exactly**. GH #191 records three failed attempts at guessing these
   parameters — this unblocks that too (see finding 6).

3. **NewConnect is covered** — the ticket's open question is closed.
   `https://newconnect.pl/ajaxindex.php?action=NCExternalDataFrontController&start=showTable&tab=all&lang=PL&full=1&format=html`
   returns 332 rows in a nearly identical layout. GPW main (372) + NC (332) = **704 distinct
   `Skrót` values, with zero overlap**.

4. **The identity join works, and ISIN confirms it.** Feed `Skrót` matches `companies.ticker` for
   **697 of 744** companies; of the ticker-matched rows, **0 have a conflicting ISIN**. But ~47
   companies are absent from the feed and at least 18 of them are still receiving daily rows from
   bankier today — a real coverage regression that the plan must decide about explicitly.

5. **Every consumer of this column is money-visible**, and the most sensitive surface is *not* the
   value chart — it is the calendar, where the displayed quantity (`zmiana_kwotowa`) is itself
   ~0.5–1.5% of price, so a 0.1–0.4% price error is 10–40% of what is being rendered.

6. **The archive serves RAW, non-dividend-adjusted prices and reaches back at least to 2001.**
   `KRU` on 2026-01-02 comes back as `498,4000` — exactly the "raw" figure recorded in the PUL-92
   plan against stooq's adjusted `475.38`. This means one source can correct both the PUL-98
   bid/ask error *and* the PUL-96 / GH #191 dividend-adjustment defect. That is a scope question for
   the plan, not a licence to widen this change unasked.

## Detailed Findings

### 1. The live GPWQuotations endpoint — verified contract

`https://www.gpw.pl/ajaxindex.php?action=GPWQuotations&start=showTable&tab=all&lang=PL&type=&full=1&format=html`

- HTTP 200, no auth, ~483 KB, `text/html; charset=UTF-8`, one `<table class="footable table">`.
- **375 `<tr>` = 3 header rows + 372 data rows.**
- The header is **two-level**: the top row has 22 cells, but *"Najlepsza oferta kupna"* and
  *"Najlepsza oferta sprzedaży"* each span 3 sub-columns (liczba zleceń / wolumen / limit), so
  **data rows carry 26 `<td>`**. Header index ≠ data index — a parser that maps header text to
  positions naively will be off by four from column 15 onwards.

Data-row layout (verified against `ALE`):

| idx | column | ALE sample |
|-----|--------|-----------|
| 2 | Nazwa | `ALLEGRO` |
| 3 | ISIN | `LU2237380790` |
| 4 | **Skrót** (ticker) | `ALE` |
| 5 | Waluta | `PLN` |
| 6 | Czas ost. trans. | `16:45:01` |
| 7 | **Kurs odn.** (previous close) | `44,7350` |
| 9 | Kurs otw. | `45,0000` |
| 10 | Kurs min. | `44,7250` |
| 11 | Kurs maks. | `45,2900` |
| 12 | **Kurs ost. trans. / zamk.** | `44,9050` |
| 14 | Zm. do k.odn. (%) | `0,38` |
| 22 | Liczba transakcji | `14 268` |
| 23 | Wol. obr. — skumul. | `4 446 098` |
| 24 | Wart. obr. — skumul. (tys.) | `199 875,42` |

**`Kurs odn.` is provably the official previous close.** Today's values for the eight tickers in
the ticket — 44.735 / 226.2 / 28.78 / 303.5 / 411.4 / 233.7 / 156.0 / 106.58 — match the ticket's
independently-verified GPW official closes for 2026-07-24 **8/8**, and simultaneously re-confirm
that the BQ values written by the scraper (44.63 / 226.6 / 28.87 / 302.95 / 410.8 / 233.4 / 155.7 /
106.72) are wrong.

**The delayed-feed problem.** Last-trade timestamps trail wall clock by ~15 minutes (fetch at 17:05
showed `16:49:5x`). Across three fetches minutes apart:

```
ticker   ~16:58     ~17:02     ~17:05
ALE      44,9050 → 44,9200 → 44,9200
CDR     233,6000 → 233,7000 → 233,4000
DNP      29,9000 →  29,8600 →  29,8600
KGH     302,2500 → 303,2000 → 304,3000
KRU     424,0000 → 424,0000 → 424,0000
PEO     236,4000 → 236,6000 → 237,0000
PKN     149,9800 → 150,0800 → 150,2600
PKO     108,1000 → 108,2800 → 108,4000
```

So the column is the close only *after* the delayed feed clears the 17:00 closing auction — i.e.
from roughly 17:15–17:20 Warsaw. The existing scheduler's last tick is 17:31 (finding 7), which is
probably late enough, but "probably" is not a basis for a correctness fix. A background poller
sampling every 10 minutes is running to establish the exact settle time; see Open Questions.

### 2. NewConnect — the ticket's open question is closed

`https://newconnect.pl/notowania` renders `div#notowaniaAllAjaxs` from
`ajaxindex.php?action=NCExternalDataFrontController&start=showTable&type=All&system_type=&tab=…&lang=PL&full=1&format=html`.
The page's own `getTableAjaxs()` builds the query string and POSTs it; a plain **GET** with
`tab=all&full=1&format=html` works and returns the full table (332 data rows).

Tab variants exposed by the page: `type=ALL` (all NC instruments — this is what `notowaniaAllAjaxs`
uses), `type=NOTC` (continuous only), `type=NS&system_type=FIX1|FIX2` (single-price auctions). An
`&download_xls=1` variant exists for each.

**Layout differs from GPW main** — 20 cells, single header row, no best-bid/ask block, and an extra
`Segment` column:

| idx | GPW main | NewConnect |
|-----|----------|-----------|
| 1 | Op. na pap. | Nazwa |
| 2 | Nazwa | ISIN |
| 3 | ISIN | **Skrót** |
| 4 | **Skrót** | Waluta |
| 5 | Waluta | Segment |
| 6 | Czas ost. trans. | Czas ost. trans. |
| 7 | **Kurs odn.** | **Kurs odn.** |
| 12 | **Kurs ost. trans./zamk.** | **Kurs ost. trans.** |

The offsets coincidentally realign from index 6 onward, but `Skrót`/`Nazwa`/`ISIN` differ. **Map
columns by header text, do not hard-code two sets of indices** — the current bankier parser's
positional `cells[1]..cells[8]` (`src/bankier_metrics.py:98-105`) is precisely the fragility that
let this bug live for a month.

### 3. The GPW archive — solved, authoritative, and deep

`https://www.gpw.pl/archiwum-notowan?type=10&instrument=&date=DD-MM-YYYY` → HTML page whose second
table (`class="table footable"`) is the session's quotation sheet.
`?fetch=1&type=10&instrument=&date=DD-MM-YYYY` → the same data as a real BIFF `.xls`
(`Content-Disposition: attachment; filename="_2026-07-24_akcje.xls"`, OLE2 magic `D0 CF 11 E0`) —
the HTML variant is far easier and needs no new dependency.

Columns: `Nazwa | Waluta | Kurs otwarcia | Kurs maksymalny | Kurs minimalny | Kurs zamknięcia |
Zmiana kursu % | Wartość obrotu (w tys.)` — 403 data rows for 2026-07-24.

Verified against the ticket's official closes — **8/8 exact**:

```
ALLEGRO   44,7350      KGHM     303,5000
CDPROJEKT 226,2000     KRUK     411,4000
DINOPL     28,7800     PEKAO    233,7000
PKNORLEN  156,0000     PKOBP    106,5800
```

Historical reach (probed): `2001-01-02` (225 rows), `2005-01-04` (229), `2011-01-03` (400),
`2016-01-04` (491), `2020-01-02` (460), `2025-12-30` (399), `2026-07-24` (403). **At least 25
years.**

**The catch: the archive table carries no ISIN and no ticker — only `Nazwa`.** Rows are
`<td class="left">06MAGNA</td>` with no links or attributes. Joining requires a `Nazwa → Skrót`
map, which the *live* table supplies exactly (its `Nazwa` values are byte-identical: `ALLEGRO`,
`PKOBP`, …). **Measured decay of that map against today's live feed:**

| archive session | rows | matched by name | unmatched |
|---|---|---|---|
| 2026-07-24 (today−1) | 403 | 364 (90.3%) | 39 |
| 2025-07-25 (−1 y) | 404 | 349 (86.4%) | 55 |
| 2024-07-24 (−2 y) | 414 | 342 (82.6%) | 72 |
| 2021-07-23 (−5 y) | 441 | 304 (68.9%) | 137 |

The unmatched tail is renames, delistings and instruments the live "all" tab excludes. A fuzzy
name match would write a plausible-looking price against the wrong ticker — the exact failure class
this change exists to eliminate — so any correction pass must be **hard-gated on an exact,
unambiguous `Nazwa → Skrót` hit** and log the rest rather than guess.

Cost per session: **283 KB, ~1.3 s**. A 13-month correction (~250 sessions) is ~10 minutes of
polite fetching; the full 2011–2026 span (~3 900 sessions) is ~1.1 GB and ~2 hours — feasible, but
see the value analysis below.

Two things the archive does **not** give: `liczba_transakcji`, and same-day availability. As of
17:05 on 2026-07-27, `date=27-07-2026` still returned the bare page with no quotation table — so
the archive lags the session by some amount. Whether it publishes at ~18:00 the same day or only
overnight is being measured (see Open Questions); this single fact decides whether the daily job
can use the archive directly or must use the live table with a timing guard.

### 4. Identity and coverage — measured against the real `companies` table

Run against production BigQuery (`espi_ebi.companies`, 744 rows, 740 with ISIN):

```
GPW main feed   372 Skrót
NewConnect feed 332 Skrót      overlap between the two: 0      union: 704

ticker (Skrót) join:  697 / 744 companies matched
ISIN join:            699 / 739 companies-with-ISIN matched
  → rescued by ISIN but missed by ticker: 3  (DGN + the two junk rows "przejęty"/"Żabka" from GH #192)
ticker matched but ISIN conflicts:  0
feed tickers absent from companies: 7  (EMS, FIB, MPS, PGH, QNT, REX, ROBA — unseeded new listings)
```

`companies.ticker` is the GPW abbreviation, not the bankier symbol — confirmed by sampling prod
(`AGL`/`AGROLIGA`, `KGN`/`KOGENERA`, `QNA`/`QNATECHNO`), and it is extracted from the parenthesised
part of the bankier profile heading (`src/company_profile.py:40-50`). So the join is `Skrót →
companies.ticker` directly, with ISIN available as a free cross-check. Given 0 conflicts, ISIN is
worth using as an assertion rather than as the join key.

**The coverage regression is real.** Of the 47 companies missing from the feed, a 25-name sample
shows **18 still receiving daily rows from bankier as of 2026-07-27** — including `CREOTECH-PDA`
(a PDA/rights line trading at 752 PLN). Scraper-era rows total **16 857 across 743 tickers,
2026-06-26 → 2026-07-27**. Switching sources without a fallback silently stops pricing ~40+
instruments; keeping bankier as a fallback keeps the wrong-value class alive for exactly those
instruments. Both are defensible; neither should be implicit.

### 5. Blast radius — every consumer is money-visible

Schema `db/bigquery.py:2363-2379`; only `ticker`, `snapshot_date`, `fetched_at` are REQUIRED,
`kurs_zamkniecia` is NULLABLE. There is **no `kurs_odn` column** on `company_daily_stats` (unlike
`etf_quotes`) — capturing the free previous-close self-check would need an additive NULLABLE column,
which `ensure_company_daily_stats_schema_current()` (`db/bigquery.py:2403-2409`) can add safely.

Four SQL readers (`db/bigquery.py:393, 519, 819, 858`):

| reader | what it computes | sensitivity to a 0.1–0.4% close error |
|---|---|---|
| `get_portfolio_calendar_data()` `db/bigquery.py:362-462` → `/api/portfolio/calendar` | daily P/L `Σ shares × zmiana_kwotowa`, MTD total | **Highest.** The displayed quantity is itself ~0.5–1.5% of price, so the error is **10–40% of the rendered number**, and it drives cell colour. |
| `get_portfolio_history()` `db/bigquery.py:465-651` → `/api/portfolio/history` | `value_pln`, `pnl_pln`, LOCF+BOCF filled | Value scales linearly (largely cancels, randomly signed). **PnL has a much smaller base** — a 0.2% value move can be a ~2% PnL move. A corrected debut close shifts an entire BOCF flat segment. |
| `list_user_portfolio_positions()` `db/bigquery.py:795-900` → `/api/portfolio/positions`, treemap, CSV export | `current_price`, `pnl_pln = (current_price − avg_buy_price) × shares`, 30-day `price_history` sparkline | Headline per-position P/L and total portfolio value. Feeds the XTB reconciliation in GH #186. |
| `get_latest_company_stats_fetched_at()` `db/bigquery.py:2858-2882` | "data as of" stamp | Display only — but moves if the job's write time changes. |

Structurally, the PUL-100 coverage gate is **presence-driven, not value-driven**: day membership
depends on `covered > 0` and `kurs_zamkniecia IS NOT NULL` (`db/bigquery.py:540-545, 596, 603`).
Correcting values changes **no day count, no note, no exclusion** — only the numbers.

### 6. Writer path and MERGE semantics — the clobber caveat

`company_stats_main.py:87` calls `merge_company_daily_stats(rows)` — a **full upsert**
(`db/bigquery.py:2475-2547`): match on `(ticker, snapshot_date)`, `WHEN MATCHED` updates **all nine
non-key columns including `fetched_at`**. Missing dict keys are not an error — the load job is
schema-driven, so an absent NULLABLE key lands as `NULL` and **overwrites** whatever was there on a
match. An *extra* key not in the schema fails the load (no `ignore_unknown_values`). There is no
source-side dedup in this variant.

Consequences for the plan:
- A corrective pass that supplies only `kurs_zamkniecia` through `merge_company_daily_stats` would
  **NULL out `wartosc_obrotu` and `liczba_transakcji`** on every touched row.
- `merge_company_daily_stats_insert_only` (`db/bigquery.py:2828-2840`, generic
  `_merge_insert_only` at `:2764-2825`) has no MATCHED branch and dedups the source with `QUALIFY
  ROW_NUMBER() … = 1` — it is the PUL-92 primitive and it **cannot** correct existing rows by
  design. Correcting scraper-era rows therefore needs either a full-row rebuild through the
  existing upsert or a new targeted-column MERGE.
- Duplicate `(ticker, snapshot_date)` keys are a correctness bug, not hygiene: the calendar query
  joins raw with no dedup (`db/bigquery.py:410-423`) and would double-count.
- `batch_insert_company_daily_stats` (`:2456-2472`) is dead and must stay dead — streaming inserts
  duplicate on re-run and block MERGE for ~90 minutes.

Also note `company_stats_main.py:34-36`: `listing = {**gpw, **nc}` discards market origin, and
**neither fetch's return is validated** — if one market returns `{}` the job still writes rows,
exits 0, and sends no alert. That asymmetric hole is pre-existing; a two-source design makes it
worse unless addressed.

### 7. Scheduling and timing

- Cloud Run job `puls-gpw-company-stats`, deployed by CI (`.github/workflows/deploy.yml:65-71`),
  `--task-timeout=300s` (`context/foundation/infra.md:100-117`).
- Cloud Scheduler `puls-gpw-company-stats-trigger`, cron **`1,31 9-17 * * 1-5`**, `Europe/Warsaw`
  (`context/foundation/infra.md:96`) — 18 runs/day, last at **17:31**.
- Because the MERGE upserts, the last successful run of the day wins; the ticket's observation that
  `fetched_at` is always 15:31 UTC = 17:31 Warsaw is consistent with this.
- **Scheduler and job changes are human-only** (`CLAUDE.md:11`, `AGENTS.md:11`,
  `context/foundation/infra.md:102`). If the plan needs a different cron, that is a hand-off step,
  not an automated one.
- GH #183 ("5-min cadence") targets the **announcements** scraper `puls-gpw-trigger`, not this job —
  no interaction.

### 8. Alerting constraints

`send_alert` (`src/notifier.py:383-396`) has no dedup, throttle, cooldown or consecutive-failure
counter — every call is one email. `company_stats_main.py:96-101` alerts on any exception then
exits 1. The only data-quality guard is all-or-nothing (`:82-85`, `raise RuntimeError("no rows
built …")`).

GH #172 (open) documents both halves of the existing problem: transient Bankier dips fire false
alerts, and a hollow HTTP 200 challenge page is a **silent miss**. Adding a second host to an
18×/day job multiplies both surfaces. And partial coverage is now the *normal* state (704 of 744
instruments), so `if not rows` cannot distinguish "healthy partial feed" from "one market died" —
a per-source row-count floor is the natural guard, and it is new work.

### 9. Conventions the new parser must follow

- `src/http_client.py:34` — `get(url)` takes **URL only**: no params, no per-call headers, no POST.
  Our endpoints work as a single fixed URL string, so this is fine, but nothing else is available.
  Shared process-wide `httpx.Client`, UA `Mozilla/5.0 (compatible; puls-gpw/1.0)`, 30 s timeout,
  3 attempts with linear backoff, raises `ScraperError` (`src/exceptions.py:16-17`).
- **No inter-request throttle exists** despite the docstring's claim — pacing is the caller's job.
- Established contract: parser catches `ScraperError` → returns empty (`src/bankier_metrics.py:71-75`,
  `src/gpw_etf_metrics.py:66-70`); the entrypoint converts empty into a hard abort so a bad scrape
  never overwrites good data (`company_stats_main.py:82-85`).
- Tests: inline module-level HTML fixture strings; patch `get` at the **importing module's**
  namespace (`patch("src.<module>.get", …)`); `pytest.approx` for floats; mandatory negative cases
  (HTTP failure → empty, table missing → empty). See `tests/test_gpw_etf_metrics.py:123-131`.
  There is **no root `conftest.py`** — a new `src/` module gets zero global mocking and must not
  import `db.*`.
- 746 tests total; `uv run pytest` runs Playwright too — use `--ignore=tests/e2e` for fast loops.
- URLs: hardcoded module constants is the metrics-scraper precedent (`GPW_ETF_URL`,
  `src/gpw_etf_metrics.py:23`; `_LISTING_URLS`, `src/bankier_metrics.py:11-14`).
- Scripts: `sys.path.insert` → `load_dotenv()` → `configure_logging()` → `db.*` imports; pure logic
  segregated under a banner comment and unit-tested via the importlib idiom
  (`tests/test_backfill_historical_closes.py:13-17`).

### 10. Interaction with PUL-92 and PUL-96 / GH #191

Three price regimes already exist in `company_daily_stats`:

- **Seam A — dividend adjustment (PUL-96 / #191, open).** Backfilled 2011–2026 rows carry stooq's
  adjusted series; the factor reaches 1.0 only after each ticker's last ex-dividend date, so the
  boundary is **per-ticker**, landing 2026-03-31 … 2026-06-24. Magnitude 2–8%. The owner measured
  7 of 11 holdings / 69.8% of portfolio value affected over the trailing year.
- **Seam B — bid/ask source (this ticket).** From 2026-06-26 on, bankier-written rows, 0.1–0.4%,
  randomly signed.
- **Seam C — the switch date**, if this change corrects going forward but not backward. Removing a
  randomly-signed ≤0.4% error is **an order of magnitude smaller than seam A and buried inside it**.

The PUL-92 overlap analysis quantified seam B precisely: over 2026-06-22…2026-07-24, of 1 221
divergent rows, **925 are genuine "both tick-compliant" disagreements — exactly this bid/ask class**,
296 are rows where the file looks adjusted and the DB holds a real quote, and **0** the other way.
That is why insert-only was the right call for PUL-92 and why a naive overwrite would damage 296
rows and repair none.

Finding 6 changes the calculus: the archive gives **raw** closes with 25 years of reach, so a
correction pass sourced from the archive repairs seam A and seam B in one motion rather than
trading one against the other. Whether that belongs in PUL-98 or in PUL-96 is a scoping call for
the plan — this research does not assume it.

## Code References

- `company_stats_main.py:34-36` — `listing = {**gpw, **nc}`, market origin discarded, neither fetch validated
- `company_stats_main.py:82-85` — all-or-nothing abort guard
- `company_stats_main.py:87` — the single production write, a full upsert
- `src/bankier_metrics.py:57-113` — the parser being replaced; positional `cells[1]..cells[8]`
- `src/bankier_metrics.py:33-54` — `_parse_polish_float` / `_parse_int`, reusable for the new source (comma decimal, NBSP thousands)
- `src/gpw_etf_metrics.py:56-140` — the gpw.pl parser to mirror; already scrapes gpw.pl for `etf_quotes`
- `src/http_client.py:34-56` — `get(url)`, retries, `ScraperError`
- `db/bigquery.py:2363-2379` — `company_daily_stats` schema
- `db/bigquery.py:2475-2547` — `merge_company_daily_stats`, MATCHED updates all nine columns
- `db/bigquery.py:2764-2840` — `_merge_insert_only` / `merge_company_daily_stats_insert_only`
- `db/bigquery.py:2412-2431` — `list_companies_with_hop_info` (`SELECT ticker, name, hop_url, isin`)
- `db/bigquery.py:362-462` — calendar query, the most error-sensitive reader
- `db/bigquery.py:465-651` — history query, LOCF+BOCF, `covered > 0` gate
- `db/bigquery.py:795-900` — positions, `price_history`, treemap inputs
- `src/company_profile.py:40-50` — where `companies.ticker` comes from
- `src/notifier.py:383-396` — `send_alert`, no dedup/throttle
- `context/foundation/infra.md:96` — `1,31 9-17 * * 1-5`
- `scripts/backfill_historical_closes.py:204-234, 281-345` — PUL-92 row building, per-year chunking
- `tests/test_gpw_etf_metrics.py:123-131` — the negative-case test pattern to copy

## Architecture Insights

- **Positional column indexing is the root enabler of this bug class.** Both the old parser and a
  naive new one break silently when a column is inserted; the two GPW/NC layouts already differ by
  one column in the identity block, and the GPW header is two-level so header index ≠ data index.
  Header-text-driven mapping with an explicit assertion on the expected column set is the
  structural fix, not just a new URL.
- **The pipeline has a silent-wrong-value blind spot.** Every guard in place —
  `fetch → {} on failure`, `if not rows: raise` — protects against *absence*. Nothing protects
  against a *plausible wrong number*, which is why a month of bad closes went unnoticed. The feed
  hands us a free invariant: today's `Kurs odn.` must equal yesterday's stored `kurs_zamkniecia`.
  That is a cheap, self-healing correctness check and arguably the most valuable thing this change
  can add.
- **Two clean layers already exist and should not be collapsed**: pure parser in `src/` (no `db.*`
  imports, fully unit-testable with inline HTML), orchestration + BQ in the entrypoint. The new
  module belongs alongside `gpw_etf_metrics.py`.
- **`etf_quotes` and `company_daily_stats` are near-twins** (same REQUIRED trio, same partition and
  clustering, 9 shared columns) and their insert-only writers are already factored through one
  helper. The full-upsert pair is the remaining duplication — worth noting, not worth fixing here.

## Historical Context (from prior changes)

- `context/archive/2026-07-24-backfill-historical-closes/plan.md:276` — the PUL-92 load: 1 899 603
  rows, 744 tickers, 2011-01-03…2026-07-24, 0 duplicates.
- `.../plan.md:275` — overlap audit: `KRU` 2026-07-24 kept the scraper's 410.8 over stooq's 411.4 —
  i.e. **insert-only deliberately preserved the wrong values this ticket is about**.
- `.../plan.md:225` — dividend adjustment accepted knowingly → PUL-96 / GH #191.
- `.../plan.md:24` — "`KRU` 2026-01-02: 498.40 raw vs 475.38 adjusted" — the archive returns
  `498,4000`, which is what makes finding 6 load-bearing.
- `.../research.md:33` — the trading-day spine comes from `company_daily_stats` only; a date present
  only in `etf_quotes` is invisible to history and calendar.
- `context/archive/2026-07-26-history-coverage-gate/` — the `covered > 0` gate and LOCF+BOCF fill;
  71 → 249 points. Presence-driven, so value corrections do not change chart structure.
- `context/foundation/lessons.md:211-235` — mocked BQ tests do not validate SQL; real round-trip is
  mandatory for hand-built SQL.
- `context/foundation/lessons.md:294-326` — REQUIRED-column migrations: change the schema *before*
  shipping code that stops writing a column; `dry_run` does not prove DML feasibility.

## Related Research

- `context/archive/2026-07-24-backfill-historical-closes/research.md` — stooq/bossa source survey,
  clobber caveat, duplicate-safety analysis
- `context/archive/2026-07-26-history-coverage-gate/` — history query semantics

## Open Questions

1. ~~When exactly does the live table publish the closing-auction price?~~ **ANSWERED — measured
   2026-07-27 by a 10-minute poller.** The closing-auction price appears at **~17:15 Warsaw** and
   never moves afterwards (identical values at 17:15, 17:25, 17:35, 17:45, 17:56):

   ```
   17:05  ALE 44,9200@16:49:56  KGH 304,3000@16:49:57  PKN 150,2600@16:49:59   ← intraday, delayed
   17:15  ALE 45,0000@17:00:00  KGH 302,5000@17:00:41  PKN 151,0000@17:00:17   ← close landed
   17:25  ALE 45,0000@17:03:50  KGH 302,5000@17:00:41  PKN 151,0000@17:04:40   ← stable (dogrywka)
   17:56  ALE 45,0000@17:03:50  KGH 302,5000@17:00:41  PKN 151,0000@17:04:40   ← unchanged
   ```

   So the existing `1,31 9-17` cron's last tick at **17:31 is late enough, with ~16 minutes of
   margin** — no scheduler change (i.e. no human-only step) is required. Every earlier tick of the
   day still writes an intraday price, but the MERGE upserts, so the 17:31 run overwrites them. The
   plan should nonetheless treat "was this row written before the close settled?" as a real state,
   because any failed 17:31 run silently leaves an intraday price as the day's final value.

2. ~~When does the archive publish a completed session?~~ **ANSWERED — it does publish same-day.**
   `date=27-07-2026` was still empty at 17:56 but returned the full 403-row sheet by 21:55, with
   `ALLEGRO=45,0000` and `PKOBP=108,0000` — **identical to the live table's settled close**, an
   independent cross-source confirmation. The exact publication moment is only bracketed
   (17:56 < t ≤ 21:55) because the sampling machine slept through the interval.

   Design consequence: the daily job still uses the **live table at 17:31**, because moving to a
   late-evening archive fetch would need a scheduler change (human-only) and would depend on a
   publication time we have only bracketed. The archive's role is (a) the corrective pass and
   (b) an optional next-morning verification of the previous session — both of which are strictly
   better than a same-day dependency.
3. **NewConnect archive.** `newconnect.pl/archiwum-notowan` redirects to `/statystyki-okresowe` and
   returns no quotation table; the per-date NC archive endpoint is unfound. If the design uses the
   archive for NC too, this needs a spike.
4. **The ~47 uncovered companies** — drop them, or keep bankier as a fallback for tickers absent
   from the GPW/NC feeds? At least 18 are actively priced today.
5. **Retro-correction scope.** Sizing, since PUL-92 loaded ~1.9 M rows:

   | slice | rows (approx) | what it fixes | rendered anywhere today? |
   |---|---|---|---|
   | scraper era 2026-06-26→now | 16 857 (743 tickers) | seam B only (≤0.4%) | yes |
   | trailing 13 months | ~186 000 | seam A **and** B | yes — this is the whole visible surface |
   | full 2011–2026 | ~1 897 000 | same, on invisible rows | **no** |

   Nothing in the product renders more than one year: `/api/portfolio/history` accepts
   `range=1w|1m|3m|1y`, `price_history` is 30 sessions (`db/bigquery.py:816-843`), the calendar is a
   month. So **~90% of the backfilled rows are invisible to every current surface**, and the
   marginal value of correcting beyond ~13 months is zero until a longer range is added — while the
   name-mapping risk grows with age (69% match at 5 years). Recommended scope: trailing ~13 months,
   hard-gated mapping, everything else logged.
6. **`liczba_transakcji` / `wartosc_obrotu` under a source switch.** The live feed has both
   (indices 22 and 24); the archive has turnover only. Whichever source is chosen, the MERGE's
   MATCHED branch overwrites all nine columns, so a partial row is a silent data loss.
7. **Should `kurs_odn` be added to `company_daily_stats`?** It is free from both feeds, additive and
   NULLABLE, and enables the previous-close self-check invariant.
