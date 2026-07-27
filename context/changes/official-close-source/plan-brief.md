# Official GPW close as the source for `company_daily_stats` — Plan Brief

> Full plan: `context/changes/official-close-source/plan.md`
> Research: `context/changes/official-close-source/research.md`

## What & Why

`company_daily_stats.kurs_zamkniecia` is not the official GPW close — the bankier.pl listing
publishes a best-bid / best-ask / last-continuous-trade figure, so stored closes are off by 0.1–0.4%
with a random sign. Every consumer of that column is money-visible, and the calendar is the worst
hit: it renders `zmiana_kwotowa`, itself only ~0.5–1.5% of price, so the error is 10–40% of the
number on screen. This switches the source to the official GPW and NewConnect quotation tables and
corrects 19 months of history from the GPW archive.

## Starting Point

Today one Cloud Run job scrapes the bankier listing 18×/day and upserts a full row per ticker.
Measurement narrowed the defect considerably: **only the close is wrong.** Open, min and max match
the official archive to the grosz; turnover and trade count are understated (bankier appears to
exclude the closing auction) but no reader renders them. History carries two further seams — PUL-92
backfilled 2011–2026 from stooq's **dividend-adjusted** series (GH #191, 7 of 11 holdings, 69.8% of
portfolio value), and scraper rows since 2026-06-26 carry the bid/ask error.

## Desired End State

Every close written from the switch date on is the official GPW or NewConnect close, and every row
the app can render carries the official raw close. Each row records which source produced it. A
close captured before the fixing — or missed because the 17:31 run failed — repairs itself on the
next session from the feed's own previous-close column.

## Key Decisions Made

| Decision | Choice | Why | Source |
|---|---|---|---|
| Daily source | Live GPW + NewConnect tables at 17:31 | Close settles at ~17:15 and never moves; the existing cron already clears it, so no human-only scheduler change | Research |
| Bankier's role | Gap-filler only, never overwrites | ~47 companies are in neither feed and ≥18 are still priced daily; official values must always win | User |
| Correction source | `gpw.pl/archiwum-notowan?type=…&date=…` | Authoritative, raw (non-adjusted), reaches to 2001, matched the ticket's official closes 8/8 | Research |
| Correction window | Since 2025-01-01 (~390 sessions) | Covers `range=1y` with wide margin; the 1y window moves forward into corrected data, so one pass suffices permanently | User |
| Correction columns | Close + the two derived change columns only | Open/min/max verified identical; turnover and trade count are unrendered — blast radius equals the defect | User |
| MERGE shape | New update-only primitive | The existing upsert overwrites all nine columns (would NULL turnover on 16.9k rows); insert-only cannot correct at all | Research |
| Final-value guarantee | Self-heal from `Kurs odn.` | A definitionally final number, already free in every fetch; repairs a failed 17:31 run automatically | User |
| Failure policy | Per-source row-count floors → abort + alert | `if not rows` cannot distinguish a healthy partial feed from a dead market now that 704 of 744 is normal | User |
| Audit columns | Add `source` + `kurs_odn` (NULLABLE) | Without `source` the bankier fallback becomes indistinguishable within a week | User |
| ETF history | Out of scope — **measured**, not assumed | `etf_quotes` matched the ETF archive 9/9 across a year including a backfilled date; total-return ETFs carry no dividend adjustment | Plan |
| NewConnect history | Out of scope | No per-date NC archive exists; verified harmless — zero held instruments are on NewConnect | Plan |

## Scope

**In scope:** official GPW + NewConnect parser; job rewiring with source priority and coverage
floors; `source` / `kurs_odn` columns; update-only correction MERGE; self-heal from `Kurs odn.`;
archive reader; corrective script; production run from 2025-01-01.

**Out of scope:** history before 2025-01-01; `etf_quotes`; NewConnect history; cron changes; GH #172
(alert dedup) and GH #192 (junk tickers); retiring bankier.

## Architecture / Approach

A pure parser module (`src/gpw_quotations.py`) mirrors `src/gpw_etf_metrics.py` — no `db.*` imports,
catches `ScraperError` and returns empty. Column indices are derived **structurally** by expanding
`colspan` over the first `<thead>` row, so one algorithm handles both layouts (GPW 26 data columns,
NewConnect 20) without the positional indexing that let this bug live a month. The job merges
sources by priority before writing. A new update-only MERGE primitive serves both the self-heal and
a one-off corrective script that walks archive sessions, maps `Nazwa → Skrót` against the live feed
under a hard exact-match gate, and caches raw HTML to disk so repeat passes cost no network.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Schema | `source` + `kurs_odn`, additive NULLABLE | Live table modes must be checked, not assumed |
| 2. Parser | `src/gpw_quotations.py` for both feeds | Header mapping must fail loud, never guess positionally |
| 3. Job rewiring | Official priority, bankier gap-fill, coverage floors | New alert surface in a job with no alert dedup (GH #172) |
| 4. MERGE primitive | Update-only close correction | An accidental `WHEN NOT MATCHED` would insert dates and reshape the chart |
| 5. Self-heal | Previous session repaired from `Kurs odn.` | Must not fight the corrective pass, must not abort ingest |
| 6. Archive + script | Reader, cache, dry-run, hard-gated mapping | Fuzzy name matching would write a right-looking price to the wrong ticker |
| 7. Production run | ~390 sessions corrected and verified | Touches production financial data; duplicate keys would double-count |

**Prerequisites:** ADC for BigQuery; the corrective run needs ~110 MB of local cache and ~15 minutes
of network.
**Estimated effort:** ~3–4 sessions across 7 phases; Phase 7 is operational, not code.

## Open Risks & Assumptions

- The row-count floors add an alert surface to a job that runs 18×/day with no dedup or throttle
  (GH #172). Thresholds must be loose enough that a normal session never trips them.
- Name mapping is exact-match only; instruments renamed or delisted inside the window will be
  skipped rather than corrected. The dry-run must be read, not skimmed — the unmatched list is the
  evidence that the gate is behaving.
- The corrected 1-year return will move **down** by roughly the dividends paid over the window on
  affected holdings. That is the fix working, not a regression — worth expecting before seeing it.
- ETF and NewConnect history stay as PUL-92 left them; the ETF exclusion rests on a 9/9 measurement,
  the NewConnect one on today's holdings containing no NC instrument.
- `GET /api/portfolio/history` caches 300 s, so post-run verification lags unless a fresh `range` is
  used.

## Success Criteria (Summary)

- A sampled set of (ticker, date) pairs matches `gpw.pl/archiwum-notowan` exactly, across several
  sessions and both before and after the switch date
- The 1-year chart renders the same number of points with the same notes and exclusions — values
  changed, structure did not — and the reported return is now defensible
- The day after deploy, the 17:31 run writes official closes and the self-heal reports zero
  corrections
