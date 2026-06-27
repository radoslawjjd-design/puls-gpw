# company-stats-upsert Implementation Plan

## Overview

Replace the two-step DELETE + streaming INSERT in `company_stats_main.py` with a single atomic BigQuery MERGE. The MERGE eliminates the brief window where today's company stats are absent between the DELETE and INSERT calls on every hourly run.

## Current State Analysis

- `company_stats_main.py:89-90`: `delete_company_daily_stats_for_date(snapshot_date)` followed by `batch_insert_company_daily_stats(rows)` — both run on every invocation (9:01–17:01, hourly, Mon–Fri)
- `db/bigquery.py:1352` `delete_company_daily_stats_for_date`: DML `DELETE WHERE snapshot_date = @date`
- `db/bigquery.py:1374` `batch_insert_company_daily_stats`: streaming insert via `insert_rows_json`
- `db/bigquery.py:497-535` `upsert_company`: existing MERGE precedent using the identical pattern we'll follow
- Table `company_daily_stats` is DAY-partitioned by `snapshot_date`, clustered by `ticker`
- Both old functions are used **only** in `company_stats_main.py`; we keep them in `db/bigquery.py` for manual emergency use

### Key Discoveries:

- `_table_ref(client, name)` returns `f"{client.project}.{_DATASET}.{name}"` — used verbatim in SQL as `` `{ref}` `` (`db/bigquery.py:105`)
- `_COMPANY_DAILY_STATS_SCHEMA` has 11 fields: `ticker`, `snapshot_date`, 7 trading floats/ints, `fetched_at` — no reserved-keyword field names, no backtick issues (`db/bigquery.py:1285-1297`)
- `timedelta` is not yet imported in `db/bigquery.py` — must add to the `datetime` import line
- `load_table_from_json` with `WRITE_TRUNCATE` + `CREATE_IF_NEEDED` overwrites stale temp tables from crashed runs cleanly

## Desired End State

`company_stats_main.main()` calls a single `merge_company_daily_stats(rows)` that atomically upserts all company stats. On re-run (same day), existing rows are UPDATEd with fresh prices + `fetched_at`; new tickers are INSERTed. The table always has data — no deletion window.

## What We're NOT Doing

- Not changing the Cloud Run scheduler (`1 9-17 * * 1-5`)
- Not splitting into two jobs (9:01 INSERT job + 10:01-17:01 UPDATE job)
- Not removing `delete_company_daily_stats_for_date` or `batch_insert_company_daily_stats` from `db/bigquery.py`
- Not modifying the `company_daily_stats` table schema

## Implementation Approach

Single MERGE using a temp table as the source:
1. Load rows into `company_daily_stats_tmp` (same dataset, same schema, 24h BQ expiry) via `load_table_from_json` with `WRITE_TRUNCATE` + `CREATE_IF_NEEDED`
2. Run DML `MERGE ... USING tmp ON (ticker, snapshot_date)`
3. Delete temp table in `finally` block (24h expiry is the safety net if finally is skipped)

## Critical Implementation Details

**`exists_ok=True` on temp table create**: If a previous run crashed before `finally` deleted the temp table, `create_table(exists_ok=True)` succeeds; the `WRITE_TRUNCATE` load overwrites the stale data cleanly without any special handling.

**Load job is async — must call `.result()`**: `load_table_from_json` returns a `LoadJob`; `.result()` blocks until the job completes (5–15 s for ~500 rows). Check `.errors` after `.result()` — a job can complete with a non-empty error list even when it doesn't raise.

---

## Phase 1: merge_company_daily_stats in db/bigquery.py

### Overview

Add the new upsert function to `db/bigquery.py` and its unit tests. The two old functions stay untouched.

### Changes Required:

#### 1. Extend `datetime` import

**File**: `db/bigquery.py:4`

**Intent**: Add `timedelta` — needed to set 24h expiry on the temp table.

**Contract**: `from datetime import date, datetime` → `from datetime import date, datetime, timedelta`

#### 2. New function `merge_company_daily_stats`

**File**: `db/bigquery.py` (append after `batch_insert_company_daily_stats`, currently ending at line 1391)

**Intent**: Upsert all rows for a snapshot run via BigQuery MERGE. Creates a temp table as the MERGE source (same schema, `WRITE_TRUNCATE`, `CREATE_IF_NEEDED`, 24h expiry), runs the MERGE DML, then deletes the temp table in a `finally` block.

**Contract**:
- Signature: `def merge_company_daily_stats(rows: list[dict]) -> None`
- Returns immediately if `rows` is empty
- Temp table ID: `_table_ref(client, f"{_COMPANY_DAILY_STATS_TABLE_NAME}_tmp")` (fixed name, overwritten each run)
- `LoadJobConfig`: `schema=_COMPANY_DAILY_STATS_SCHEMA`, `write_disposition=WRITE_TRUNCATE`
- MERGE key: `T.ticker = S.ticker AND T.snapshot_date = S.snapshot_date`
- `WHEN MATCHED THEN UPDATE SET`: all 7 trading fields + `fetched_at`
- `WHEN NOT MATCHED THEN INSERT`: all 11 schema columns
- Raises `BigQueryError` on load job or MERGE job failure
- `client.delete_table(tmp_table_id, not_found_ok=True)` in `finally`

#### 3. Unit tests for `merge_company_daily_stats`

**File**: `tests/test_bigquery.py` (append after existing `batch_insert_company_daily_stats` tests, ~line 1013)

**Intent**: Cover the four paths: (a) happy path — `load_table_from_json`, `client.query` (MERGE), and `client.delete_table` all called; (b) empty rows → noop, no BQ calls; (c) load job failure → `BigQueryError` raised and `delete_table` still called; (d) MERGE job failure → `BigQueryError` raised and `delete_table` still called.

**Contract**: Tests mock `db.bigquery._get_client()` return value. Happy-path setup must explicitly set `client.load_table_from_json.return_value.errors = None` and `client.load_table_from_json.return_value.result.return_value = None` — `_mock_bq_client()` only configures `client.query`, not `load_table_from_json`, so its default `.errors` is a truthy `MagicMock` that would falsely trigger `BigQueryError`. For the failure cases, assert `client.delete_table.assert_called()` to verify the `finally` cleanup path runs.

### Success Criteria:

#### Automated Verification:

- `uv run pytest tests/test_bigquery.py -k "company_daily_stats"` — all existing + new tests pass
- `uv run pytest` — full suite green

#### Manual Verification:

- Read `merge_company_daily_stats` in `db/bigquery.py` and confirm the MERGE SQL field list matches `_COMPANY_DAILY_STATS_SCHEMA` column-for-column (no missing or extra fields)

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding.

---

## Phase 2: Update company_stats_main.py + tests

### Overview

Wire up the new function in the entrypoint and update its unit tests to reflect the new collaborator set.

### Changes Required:

#### 1. Replace imports in `company_stats_main.py`

**File**: `company_stats_main.py:17-19`

**Intent**: Remove the two old function imports, add `merge_company_daily_stats`.

**Contract**: The `from db.bigquery import (...)` block drops `batch_insert_company_daily_stats` and `delete_company_daily_stats_for_date`, adds `merge_company_daily_stats`.

#### 2. Replace write calls in `main()`

**File**: `company_stats_main.py:88-90`

**Intent**: Replace the two-step delete+insert with a single merge call.

**Contract**: Remove the comment `# Delete today's rows first (safe re-run), then batch-insert all at once` and the two calls; replace with `merge_company_daily_stats(rows)`.

#### 3. Update fixture and tests in `tests/test_company_stats_main.py`

**File**: `tests/test_company_stats_main.py`

**Intent**: Drop `delete` and `batch` mocks from the fixture, add a `merge` mock. Update all assertions that reference the old collaborators.

**Contract**:
- Fixture: remove `delete` / `batch` `MagicMock` declarations and their `monkeypatch.setattr` calls; add `merge = MagicMock(name="merge_company_daily_stats")` + `monkeypatch.setattr(company_stats_main, "merge_company_daily_stats", merge)`
- `test_happy_path_calls_all_collaborators_in_order`: replace `m["delete"].assert_called_once()` + `m["batch"].assert_called_once()` with `m["merge"].assert_called_once()`; update `rows = m["merge"].call_args[0][0]`
- `test_happy_path_row_contains_snapshot_date_and_fetched_at`: update row extraction to use `m["merge"].call_args[0][0]`
- `test_all_companies_skipped_triggers_alert_and_exits`: replace `m["delete"].assert_not_called()` with `m["merge"].assert_not_called()`
- `test_batch_insert_failure_triggers_alert_and_exit`: rename to `test_merge_failure_triggers_alert_and_exit`, change `m["batch"].side_effect` → `m["merge"].side_effect = BigQueryError("merge failed")`

### Success Criteria:

#### Automated Verification:

- `uv run pytest tests/test_company_stats_main.py` — all 7 tests pass (names updated where renamed)
- `uv run pytest` — full suite green

#### Manual Verification:

- `company_stats_main.py` imports: confirm `batch_insert_company_daily_stats` and `delete_company_daily_stats_for_date` are gone from the import block

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation before proceeding.

---

## Phase 3: Round-trip verification script

### Overview

Verify the MERGE SQL syntax and both code paths (INSERT and UPDATE) against the real BigQuery instance, per the `lessons.md` rule for manual SQL changes.

### Changes Required:

#### 1. New script `scripts/test_bq_company_stats_merge.py`

**File**: `scripts/test_bq_company_stats_merge.py`

**Intent**: End-to-end smoke test for `merge_company_daily_stats`. Inserts a sentinel row, verifies the INSERT path, re-merges with different values, verifies the UPDATE path (kurs_zamkniecia changed, row count stays 1), then cleans up via `delete_company_daily_stats_for_date`.

**Contract**:
- `load_dotenv()` called before any `db.*` import
- Sentinel: `ticker="_TEST_MERGE_"`, `snapshot_date="2000-01-01"` (past date, safe)
- All nullable fields set to non-null test values so the MERGE INSERT path exercises every column
- Run 1: `merge_company_daily_stats([row_v1])` with `kurs_zamkniecia=100.0`
  - Query BQ: assert `kurs_zamkniecia == 100.0` and `COUNT(*) == 1`; print `✓ INSERT path OK`
- Run 2: `merge_company_daily_stats([row_v2])` with `kurs_zamkniecia=105.0`
  - Query BQ: assert `kurs_zamkniecia == 105.0` and `COUNT(*) == 1` (no duplicate); print `✓ UPDATE path OK`
- Cleanup: `delete_company_daily_stats_for_date(date(2000, 1, 1))`
- Script exits non-zero on any assertion failure

### Success Criteria:

#### Manual Verification:

- `uv run python scripts/test_bq_company_stats_merge.py` exits 0 and prints both `✓ INSERT path OK` and `✓ UPDATE path OK`
- BigQuery Console: confirm no `_TEST_MERGE_` row remains in `company_daily_stats` after the script finishes

**Implementation Note**: This is the final manual gate before declaring the change complete.

---

## Testing Strategy

### Unit Tests:

- `tests/test_bigquery.py`: 4 new tests for `merge_company_daily_stats` (happy, empty, load-fail, merge-fail)
- `tests/test_company_stats_main.py`: existing 7 tests updated to reflect new collaborator (`merge` instead of `delete` + `batch`)

### Manual Testing Steps:

1. Run `uv run python scripts/test_bq_company_stats_merge.py` — both paths green
2. Manually trigger the Cloud Run Job (or run `uv run python company_stats_main.py` locally) and confirm it completes without errors
3. Query BQ: `SELECT COUNT(*) FROM company_daily_stats WHERE snapshot_date = CURRENT_DATE()` — one row per company, no duplicates

## References

- `db/bigquery.py:497-535` — existing `upsert_company` MERGE pattern to follow
- `db/bigquery.py:1285-1391` — `_COMPANY_DAILY_STATS_SCHEMA`, `delete_company_daily_stats_for_date`, `batch_insert_company_daily_stats`
- `company_stats_main.py:29-108` — entrypoint `main()`
- `context/foundation/lessons.md` — BQ reserved keywords rule + round-trip mandate

---

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands.

### Phase 1: merge_company_daily_stats in db/bigquery.py

#### Automated

- [x] 1.1 `uv run pytest tests/test_bigquery.py -k "company_daily_stats"` — all tests pass including new ones — 452d9aa
- [x] 1.2 `uv run pytest` — full suite green — 452d9aa

#### Manual

- [x] 1.3 MERGE SQL field list matches `_COMPANY_DAILY_STATS_SCHEMA` column-for-column (manual read) — 452d9aa

### Phase 2: Update company_stats_main.py + tests

#### Automated

- [x] 2.1 `uv run pytest tests/test_company_stats_main.py` — all tests pass — 9539407
- [x] 2.2 `uv run pytest` — full suite green — 9539407

#### Manual

- [x] 2.3 `company_stats_main.py` imports no longer reference `batch_insert_company_daily_stats` or `delete_company_daily_stats_for_date` — 9539407

### Phase 3: Round-trip verification script

#### Manual

- [x] 3.1 `uv run python scripts/test_bq_company_stats_merge.py` exits 0 with `OK: INSERT path OK` and `OK: UPDATE path OK`
- [x] 3.2 BigQuery Console: no `_TEST_MERGE_` row remains in `company_daily_stats` after script completes
