# Phase 8 — production correction runbook (PUL-98)

## Pre-correction snapshot

Created 2026-07-28, before any write:

```sql
CREATE TABLE IF NOT EXISTS `puls-gpw.espi_ebi.company_daily_stats_pre_pul98` AS
SELECT * FROM `puls-gpw.espi_ebi.company_daily_stats`
WHERE snapshot_date >= '2025-01-01';
```

**241 277 rows, 2025-01-02 .. 2026-07-28** — verified equal to the source count for the same window
before the first correction ran.

## Restore

The corrective MERGE overwrites in place with no undo. To put the window back exactly as it was:

```sql
MERGE `puls-gpw.espi_ebi.company_daily_stats` T
USING `puls-gpw.espi_ebi.company_daily_stats_pre_pul98` S
ON T.ticker = S.ticker AND T.snapshot_date = S.snapshot_date
WHEN MATCHED THEN UPDATE SET
  kurs_zamkniecia = S.kurs_zamkniecia,
  zmiana_procentowa = S.zmiana_procentowa,
  zmiana_kwotowa = S.zmiana_kwotowa,
  source = S.source;
```

This restores exactly the four columns the correction is allowed to touch. It deliberately does not
delete rows or insert them: the corrective pass never inserts, so the key set is unchanged.

Dropping `company_daily_stats_pre_pul98` afterwards is a **human-only** action (`CLAUDE.md`).

## Run log

| step | when | result |
|---|---|---|
| snapshot | 2026-07-28 | 241 277 rows, count matches source |
| sample audit (`--tickers KRU,ALE,PKO`) | 2026-07-28 | 510 rows corrected, audit clean |
| full run | 2026-07-28 | 47 324 of 47 324 rows corrected (2025: 34 352, 2026: 12 972) |

## Full-run verification

Against the snapshot, across **all 241 277 joined rows**:

| check | result |
|---|---|
| duplicate `(ticker, snapshot_date)` keys | **0** |
| rows in window before / after | 241 277 / 241 277 — nothing inserted |
| `kurs_zamkniecia` changed | 47 368 |
| `source` = `archive` | 47 834 (the extra 466 are percentage-only corrections) |
| `kurs_otwarcia`, `kurs_min`, `kurs_max` changed | **0** |
| `wartosc_obrotu`, `liczba_transakcji`, `fetched_at` changed | **0** |
| unit suite | 684 passed |

User-visible surfaces, before -> after:

| surface | before | after |
|---|---|---|
| 1-year chart points (all three views) | 251 | 251 — unchanged |
| chart notes / exclusions | S2B listed_from note / none | identical |
| 1-year return, combined view | +39.66% | **+34.71%** |
| 1-year return, per wallet | +39.48% / +39.98% | +35.65% / +33.02% |
| calendar (2026-06, 2026-07) | — | renders, 21 of 21 positions priced |
| `ECHO` 2026-06-24 | — | +0.10% (official), not the naive -7.40% |

The return moving **down ~5 pp is the point of the change, not a regression**: the start of the
window was previously valued with stooq's dividend-adjusted prices, which understate what the
holdings actually cost at the time. The end value is unchanged, so only the baseline moved.

`2026-06-27` (Saturday, 739 rows) still renders as a calendar day with a +180.73 PLN change. That
phantom is deliberately out of scope here — reported, not deleted.

## Still pending

`8.9` — the next daily job must write official closes and the self-heal must report zero
corrections. That cannot be verified until this branch is merged and deployed: **production is still
running the old bankier-sourced job**, so tomorrow's 17:31 run will write wrong closes again until
the deploy lands.

### Sample audit result

510 rows corrected across 1 179 joined rows for KRU / ALE / PKO. Compared against the snapshot:

| column | rows changed |
|---|---|
| `kurs_zamkniecia` | 507 |
| `zmiana_kwotowa` | 507 |
| `zmiana_procentowa` | 20 |
| `source` = `archive` | 510 |
| `kurs_otwarcia`, `kurs_min`, `kurs_max` | **0** |
| `wartosc_obrotu`, `liczba_transakcji` | **0** |
| `fetched_at` | **0** |

Row count 1 179 before and after — **nothing was inserted**. The 3 rows corrected without a close
change are the percentage-only defect: the close already matched, the move did not.

Five pairs verified against the archive after the write, all exact:

| ticker | date | before | after | archive |
|---|---|---|---|---|
| KRU | 2026-01-02 | 475.379 | **498.40** (0.99%) | 498.40 (0.99%) |
| KRU | 2025-03-14 | 363.188 | **398.40** (1.74%) | 398.40 (1.74%) |
| ALE | 2026-07-24 | 44.63 | **44.735** (−0.33%) | 44.735 (−0.33%) |
| PKO | 2026-07-24 | 106.72 | **106.58** (−0.60%) | 106.58 (−0.60%) |
| ALE | 2025-09-15 | 35.935 | 35.935 (0.76%) | 35.935 (0.76%) |

The KRU rows are the clearest evidence of scope: 475.379 and 363.188 are stooq's
dividend-**adjusted** prices from the PUL-92 backfill, not what the market quoted. Correcting them
is GH #191 being closed as a side effect of this pass.
