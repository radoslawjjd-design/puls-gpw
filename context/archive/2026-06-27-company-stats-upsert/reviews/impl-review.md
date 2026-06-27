<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: company-stats-upsert

- **Plan**: context/changes/company-stats-upsert/plan.md
- **Scope**: All phases (1–3 of 3)
- **Date**: 2026-06-27
- **Verdict**: REJECTED
- **Findings**: 1 critical  1 warning  2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | FAIL |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | PASS |

## Findings

### F1 — Fixed temp table name: race condition across overlapping runs

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: db/bigquery.py:1406
- **Detail**: Temp table name is a fixed constant `company_daily_stats_tmp` with no run-unique suffix. The hourly scheduler fires 9×/day; `scripts/test_bq_company_stats_merge.py` calls the same production function against the same project/dataset. If two invocations overlap: (1) `WRITE_TRUNCATE` in run A silently wipes rows loaded by B before B's MERGE reads them — MERGE "succeeds" against wrong data with no error raised; (2) `finally: delete_table` in whichever run finishes first pulls the temp table from under the other's in-flight MERGE.
- **Fix**: Append UUID suffix: `f"{_COMPANY_DAILY_STATS_TABLE_NAME}_tmp_{uuid4().hex[:8]}"` — `uuid4` already imported and used in 2 sibling functions (`save_x_post`, `save_portfolio_snapshot`). One-line change. 24h expiry still covers leaked tables.
  - Strength: Identical pattern already in this file; eliminates cross-run interference entirely.
  - Tradeoff: Each leaked temp table occupies its own 24h BQ expiry slot (negligible cost).
  - Confidence: HIGH — same module already uses this pattern twice.
  - Blind spot: None significant.
- **Decision**: FIXED — UUID suffix added: `f"{_COMPANY_DAILY_STATS_TABLE_NAME}_tmp_{uuid.uuid4().hex[:8]}"`

### F2 — Unguarded finally: delete_table can mask the original exception

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: db/bigquery.py:1456-1457
- **Detail**: `finally: client.delete_table(tmp_table_id, not_found_ok=True)` is bare and unguarded. If `delete_table` raises (transient network error, permission hiccup), Python's `finally` semantics replace any in-flight exception — original MERGE failure lost, company_stats_main alerts on an unrelated cleanup error even when the data write succeeded.
- **Fix**: Wrap in try/except + logger.warning so cleanup failures are logged but never propagate.
- **Decision**: FIXED — wrapped delete_table in try/except + logger.warning in finally block

### F3 — Redundant create_table call (CREATE_IF_NEEDED already set)

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: db/bigquery.py:1414-1417
- **Detail**: `create_table(tmp_table, exists_ok=True)` runs before every load but `LoadJobConfig` already has `create_disposition=CREATE_IF_NEEDED`. Extra synchronous API round-trip per run (9×/day). Real purpose is setting `tmp_table.expires` (24h TTL) which can't be set via LoadJobConfig.
- **Fix**: Add one-line comment: `# create_table sets the 24h expiry; CREATE_IF_NEEDED alone cannot`
- **Decision**: FIXED — added comment: `# create_table sets the 24h expiry; CREATE_IF_NEEDED alone cannot`

### F4 — Failure tests don't assert delete_table call args

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: tests/test_bigquery.py:1077, 1098
- **Detail**: Both failure tests use `client.delete_table.assert_called()` without asserting which table or `not_found_ok=True`. A bug accidentally deleting the target table instead of the temp table would pass these tests.
- **Fix**: Strengthen to `assert_called_once_with(<expected_tmp_id>, not_found_ok=True)` — defer until after F1 fix since the expected ID will include UUID suffix.
- **Decision**: FIXED — strengthened to `assert_called_once()` + `assert "_tmp_" in call_args.args[0]` + `not_found_ok=True` check
