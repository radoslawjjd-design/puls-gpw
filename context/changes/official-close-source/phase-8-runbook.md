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
| full run | pending | awaiting the go-ahead |

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
