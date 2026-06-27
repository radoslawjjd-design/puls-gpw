# company-stats-upsert — Plan Brief

> Full plan: `context/changes/company-stats-upsert/plan.md`

## What & Why

Replace the hourly DELETE + streaming INSERT in `company_stats_main.py` with a single atomic BigQuery MERGE. The current flow creates a brief window where today's company stats are missing from the table between the DELETE and the INSERT; MERGE eliminates that window and is idempotent by design.

## Starting Point

`company_stats_main.py` calls `delete_company_daily_stats_for_date(snapshot_date)` then `batch_insert_company_daily_stats(rows)` on every run (9:01–17:01, Mon–Fri). Both functions exist only in `db/bigquery.py` and are not used anywhere else. The codebase already has an identical MERGE pattern in `upsert_company` (line 497).

## Desired End State

A single `merge_company_daily_stats(rows)` call in `main()` handles every run atomically: existing rows for the day are UPDATEd with fresh prices, new tickers are INSERTed. The table always has current data. Scheduler and infra are unchanged.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| MERGE source | Temp table (`load_table_from_json` + MERGE) | Safe for 500+ rows, no SQL injection risk, atomically overwrites stale temp tables with `WRITE_TRUNCATE` | Plan |
| Old functions | Keep in `db/bigquery.py` | `delete_company_daily_stats_for_date` is useful for manual data recovery; `batch_insert` costs nothing to keep | Plan |
| Round-trip test | New `scripts/test_bq_company_stats_merge.py` | `lessons.md` mandates BQ round-trip for every manual SQL change | Plan |
| Scheduler | Unchanged (`1 9-17 * * 1-5`, one job) | MERGE handles INSERT and UPDATE in one step — no need for two separate jobs | Plan |

## Scope

**In scope:**
- New `merge_company_daily_stats()` in `db/bigquery.py` + unit tests
- `company_stats_main.py`: swap imports + replace two-line write block with single call
- `tests/test_company_stats_main.py`: updated fixture and assertions
- `scripts/test_bq_company_stats_merge.py`: round-trip smoke test (INSERT + UPDATE paths)

**Out of scope:**
- Schema changes to `company_daily_stats`
- Infra / scheduler changes
- Removing `delete_company_daily_stats_for_date` or `batch_insert_company_daily_stats`

## Architecture / Approach

```
main() → merge_company_daily_stats(rows)
           ├─ load_table_from_json(rows, tmp_table, WRITE_TRUNCATE)  [5-15s]
           ├─ MERGE target USING tmp ON (ticker, snapshot_date)
           │    WHEN MATCHED → UPDATE trading fields + fetched_at
           │    WHEN NOT MATCHED → INSERT full row
           └─ finally: delete_table(tmp_table)
```

Temp table: `company_daily_stats_tmp` in the same BQ dataset, 24h expiry, fixed name (overwritten each run via `WRITE_TRUNCATE`).

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. `merge_company_daily_stats` | New BQ function + unit tests | MERGE SQL field list drifts from schema → manual read required |
| 2. Wire into `company_stats_main.py` | Entrypoint uses MERGE; old test mocks updated | Test fixture leaves stale `delete`/`batch` references → CI red |
| 3. Round-trip script | Verified INSERT + UPDATE paths on real BQ | BQ syntax error not caught by mocked tests → this script is the gate |

**Prerequisites:** BQ credentials available locally (ADC or `GOOGLE_APPLICATION_CREDENTIALS`)
**Estimated effort:** ~1 session across 3 phases

## Open Risks & Assumptions

- `load_table_from_json` job takes 5–15 s for ~500 rows — acceptable for an hourly job, but if Bankier returns a large number of companies this could grow
- Fixed temp table name (`_tmp`) means a concurrent run (e.g., manual trigger during scheduled run) would have a race; Cloud Run Jobs prevent this in practice

## Success Criteria (Summary)

- `uv run pytest` green (all phases)
- `scripts/test_bq_company_stats_merge.py` exits 0: both `✓ INSERT path OK` and `✓ UPDATE path OK` printed, no `_TEST_MERGE_` row remains
- Cloud Run Job completes without errors on next scheduled or manual run
