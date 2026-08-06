# Executed data repair — BAC

**Date:** 2026-08-05 · **Table:** `puls-gpw.espi_ebi.company_daily_stats`

## What ran

```
uv run python scripts/correct_newconnect_closes.py --from-dir stooq_raw --tickers BAC --apply
```

Input: `stooq_raw/bac_d.csv`, downloaded through a browser from
`https://stooq.pl/q/d/l/?s=bac&i=d&o=1111111` (574 rows, 2024-04-16 .. 2026-08-05).
Rejected by the guard before writing: nothing — the file passed `assert_unadjusted`
against `d_pl_txt`, which marks 532 of its own rows adjusted.

## Before-snapshot

`before-bac.csv` in this folder: all 574 stored rows with the four correction columns
plus `source`, captured immediately before the write. Replaying it restores the prior
state exactly. These are public market prices with no portfolio or user data, which is
why the snapshot lives in the repo — unlike the PUL-114 baselines, which held portfolio
values and had to be rewritten out of git history.

## Result

| | |
|---|---|
| Rows updated | **531** of 574 |
| Span | 2024-04-17 .. 2026-06-08 |
| Rows left alone | 43 (already agreed — the factor had reached 1.0) |
| Scraper rows touched | **0** |

The acceptance criterion "scraper-written rows stay untouched" holds without a special
guard: all 531 changed rows carried `source IS NULL` (the PUL-92 backfill). The pass only
rewrites rows that disagree with the authoritative series, and the 6 `source='nc'` rows
already carried correct prices.

Sources after the run: `stooq_raw` 531, `(null)` 37, `nc` 6.

## Spot checks

| Date | close before | close after | `zmiana_procentowa` | `zmiana_kwotowa` after |
|---|---|---|---|---|
| 2025-08-20 | 3.0361 | **3.14** | 4.67 (unchanged) | 0.1401 |
| 2025-08-27 | 3.2005 | **3.31** | -3.22 (unchanged) | **-0.1101** |
| 2026-07-20 | 3.62 | 3.62 (untouched) | 0.0 | 0.0 |

The 2025-08-27 figure is the one worth reading twice. Differencing consecutive raw closes
would have produced **+0.03** — the wrong sign. Deriving it from the reference price via
`derive_zmiana_kwotowa` gives -0.1101, consistent with the -3.22% the day actually moved.

## Verification

- Re-running the pass reports **0 of 574** sessions needing correction; the repair is
  complete and idempotent.
- Portfolio history right edge cannot have moved: BAC has **0 open positions**, and no
  row after 2026-06-08 was touched.

## Effect

Historical values on days BAC was held rise by 3.42%, so reported growth over that window
*falls*. This is a correction, not a regression — the same shape as PUL-98's, and it ships
silently, consistent with the decision taken for PUL-114's curve correction in this batch.
Worst case on the only position ever held was ~107 PLN against a ~72k portfolio, and that
position is closed.

## Not repaired

`--report-contaminated` measures what remains:

- **320** tickers carry adjusted history somewhere (of 328 with an adjusted bulk series)
- **55** of those are adjusted inside the visible year — the ones a chart can reach today
- **265** are adjusted only before 2025-08-05, outside the window PUL-98 corrected

Two corrections to earlier figures in this change, both from replacing the tick-precision
heuristic with a comparison against the known-adjusted bulk series:

1. The visible-year count is **55**, not the 47 the heuristic reported. Rounding an
   adjusted value to 4 decimals sometimes lands back on a legal tick, so it undercounts.
2. The defect is **not confined to NewConnect**. MCR sits in `wse stocks`; it is
   contaminated up to 2025-10-31 and only enters the GPW archive between 2025-12-15 and
   2026-01-15, so PUL-98 could never have reached it — a fact that no name-mapping fix
   would have changed.

None of the 55 is held or watched. Each is repairable on its own with one browser
download; the recipe is printed by `--report-contaminated`.
