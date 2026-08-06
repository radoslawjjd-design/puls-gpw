---
date: 2026-08-05
researcher: Radek
git_commit: 7dd9815
branch: feat/pul-96-dividend-adjusted-closes
repository: puls-gpw
topic: "Recovering unadjusted NewConnect closes after PUL-98 left them behind"
tags: [research, company_daily_stats, stooq, newconnect, dividend-adjustment]
status: complete
last_updated: 2026-08-05
last_updated_by: Radek
---

# Research: unadjusted NewConnect closes

## Research question

PUL-96 asks for raw (as-quoted) historical closes. PUL-98 delivered them for the main
market from the GPW archive and closed GH #191. What remains, and is there a source
for it?

## Summary

**What remains:** every NewConnect name. 47 tickers carry adjusted closes inside the
trailing year; 46 of them are absent from the GPW archive sheet, so PUL-98's corrective
pass could never have reached them.

**Is there a source:** yes, and the ticket was wrong to rule it out. stooq's historical
page takes an `o=` bitmask that disables each adjustment class. `o=1111111` returns the
prices actually quoted. Verified against production, not assumed.

**Root cause is ours, not the vendor's.** PUL-92 ran its backfill in `--from-db-dir`
mode against the `d_pl_txt` bulk archive, which is dividend-adjusted. The script's own
docstring says so (`scripts/backfill_historical_closes.py:16-18`, referencing PUL-92
plan Addendum 2026-07-25). BigQuery's stored values are that file rounded to 4 decimals
— `2.3303` is `round(2.33026, 4)`.

## Detailed findings

### Contamination by ingestion source

`company_daily_stats`, trailing 365 days, share of closes that miss the RTS 11 tick grid:

| `source` | rows | off-tick | % |
|---|---:|---:|---:|
| `(null)` — PUL-92 bulk backfill | 126 828 | 8 314 | **6.555** |
| `archive` — PUL-98 corrective pass | 25 189 | 7 | 0.028 |
| `nc` — live NewConnect scraper | 1 404 | 42 | 2.991 |
| `gpw` — live main-market scraper | 2 131 | 11 | 0.516 |
| `bankier` | 208 | 0 | 0.000 |

Only the PUL-92 rows are materially contaminated. Going forward the `nc` scraper
(`src/gpw_quotations.py:34`) writes each session as quoted, so this is a bounded,
one-time historical repair — not a leak that keeps producing bad rows.

### The tick detector undercounts

The ">3 decimals" heuristic used during triage is a lower bound, because rounding an
adjusted value to 4 decimals sometimes lands back on a legal tick. Measured on BAC:

- detector flagged **195** rows
- actually wrong against the raw series: **208** of 250

So the earlier "5 407 rows / 47 tickers" figure understates the true blast radius. The
reliable detector is **fractional volume** in `d_pl_txt`: stooq scales volume by the
same factor it scales price by, and a fractional share count is impossible.

### `etf_quotes` is not affected

142 off-tick rows across 5 tickers, all crypto ETNs (`ETNVIRSOL`, `ETNVIRXRP`,
`ETNVCOIN50`, `ETNVIRALT`), all first seen 2026-02 or later — i.e. written by the live
scraper, not the PUL-92 backfill. Crypto ETNs quote in finer increments and pay no
dividends. The ticket's acceptance criterion naming `etf_quotes` is satisfied as-is.

### Sources ruled out (measured, not assumed)

| Avenue | Result |
|---|---|
| GPW archive sheet (`type=10`) | 399 rows, main market only. **1 of 47** affected tickers present (MCR). |
| `newconnect.pl/archiwum-notowan` | HTTP 200, zero tables. |
| NewConnect AJAX controller | Returns a table but **ignores** `&date=` / `&data=`; always the current session. |
| bankier.pl per-symbol history | Controls pass (profile 200/3 tables, NC listing 332 rows); every `notowania-historyczne` variant returns a page with no table. |
| Reversing the adjustment from `d_pl_txt` volume | Fails. No single factor makes every volume whole; greedy segmentation yields 33 runs of 1-2 days with unrelated factors — a degenerate fit, not a step function. |

The first probe of these gave a false negative: it used a hand-rolled `httpx.get` and
every candidate failed *including the gpw.pl control that provably works in production*.
Re-run through `src.http_client.get` the control returned 399 rows. A local-network
result is not evidence about a remote source.

### The source that works

`https://stooq.pl/q/d/?s=<sym>&d1=<from>&d2=<to>&o=1111111`

The `o=` bitmask maps to the page's "Pomijaj" checkboxes — splits, dividends,
pre-emptive rights, acquisition rights, subscription rights, denominations, other.
All set means no adjustment is applied.

Two independent signals confirm the returned series is raw:

1. **Volumes become integers.** Adjusted: `818587.4098075391`. Raw: `737459`.
2. **Closes land on the tick grid.** The CSV carries float round-trip noise from the
   division (`3.139996958116`), and **all 574** rows resolve to a 2-decimal tick.

Scripted fetching is still blocked by TLS fingerprinting (PUL-92 Addendum 2026-07-24);
the download has to come through a real browser. The CSV endpoint honours the same
parameter: `https://stooq.pl/q/d/l/?s=<sym>&i=d&o=1111111`.

### Magnitude, measured on BAC

```
BQ rows in visible year : 250
matched with stooq raw  : 250
differing (>0.5 gr)     : 208  = 83.2%
understatement: min 3.42%  max 3.42%  mean 3.42%
```

The error is **exactly constant** across all 208 rows. That is the signature of a single
dividend event smeared backwards, and it is the strongest available evidence that the
raw series is the right one — a mismatched source would scatter.

### Derived columns behave differently

Under a constant factor *k*, percentage change is invariant but absolute change is not.
Measured against raw-differenced values on BAC:

- `zmiana_procentowa` — **248 match / 1 differs**. Already correct; the single
  exception is the ex-dividend day itself.
- `zmiana_kwotowa` — 220 match / 29 differ. Needs rescaling. The apparent majority
  match is an artefact of tolerance: a 5 gr move rescaled by 1.0342 shifts by 0.0017,
  inside the 0.005 threshold.

This matters because `_CLOSE_CORRECTION_COLUMNS` writes all three plus `source`, so the
repair must supply a deliberate value for each rather than differencing blindly —
PUL-98's docstring warns that GPW measures change against a corporate-action-adjusted
reference, which is precisely what the ex-dividend day exposes.

### Exposure

Zero of the 47 affected tickers sit in any portfolio or on any watchlist today. BAC is
the only one ever traded, and its position is closed. Worst case on that position was
~107 PLN against a ~72k portfolio (~0.15%).

## Code references

- `scripts/backfill_historical_closes.py:16-18` — bulk archive documented as adjusted
- `scripts/correct_official_closes.py:1-35` — PUL-98 corrective pass, `--tickers` / `--apply`
- `db/bigquery.py:3594` — `_CLOSE_CORRECTION_COLUMNS`
- `db/bigquery.py:3597` — `merge_company_daily_stats_close_correction`, update-only, no INSERT branch
- `src/gpw_quotations.py:34,38` — live NewConnect feed, `source='nc'`

## Architecture insights

The write path is already source-agnostic. `merge_company_daily_stats_close_correction`
takes rows and updates four columns with no knowledge of where they came from, and
refuses to insert dates the table does not already hold — so a repair cannot redefine
what counts as a trading day. Nothing in the DB layer needs to change; the new work is
a reader for stooq's raw CSV plus the ticker mapping.

## Open questions

- What to write into `zmiana_kwotowa` on the ex-dividend day, where no official
  reference price is available for NewConnect.
- Whether the 45 unheld tickers should be flagged as unreliable in the read path or
  left silent until someone holds one.
