<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Backfill Historical Daily Closes (stooq)

- **Plan**: `context/changes/backfill-historical-closes/plan.md`
- **Scope**: Phase 1 of 3 (insert-only MERGE write paths)
- **Date**: 2026-07-24
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 3 observations (all fixed in triage)

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS — 3/3 planned items MATCH; shared-helper factoring preserves the contract (names, signatures, SQL shape, error/cleanup behavior) |
| Scope Discipline | PASS — zero EXTRA production code; only change.md/plan.md bookkeeping |
| Safety & Quality | PASS — f-string MERGE interpolates only internal identifiers (`_table_ref` output + hardcoded column lists); row data flows via schema-validated load job; no `WHEN MATCHED` / `WHEN NOT MATCHED BY SOURCE` → production path can only INSERT; no reserved-keyword columns |
| Architecture | PASS — temp-table+MERGE pattern 1:1 with siblings (`merge_company_daily_stats`, `merge_etf_quotes`) |
| Pattern Consistency | PASS — tests mirror `tests/test_bigquery.py:1234-1316` conventions; script mirrors `scripts/test_bq_company_stats_merge.py` (and is safer: throwaway tables vs sentinel rows in real table) |
| Success Criteria | PASS — 7 unit tests green, full suite 600 passed, ruff clean on touched files, round-trip PASS on real BQ (re-run after F1's SQL change per the mocks-don't-parse-SQL lesson) |

## Findings

### F1 — QUALIFY dedup without ORDER BY: nondeterministic winner

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Safety & Quality
- **Location**: db/bigquery.py (insert-only MERGE `USING` subquery)
- **Detail**: `ROW_NUMBER() OVER (PARTITION BY ticker, snapshot_date)` with no ORDER BY picks an arbitrary row when a batch carries the same key with different values.
- **Fix**: `ORDER BY fetched_at DESC` added inside the OVER clause.
- **Decision**: FIXED

### F2 — Throwaway rt_ table lacked 24h expires backstop

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Safety & Quality
- **Location**: scripts/test_bq_insert_only_merge.py
- **Detail**: A hard kill between `create_table` and `finally` would leave the rt_ table forever (production temp tables carry a 24h expiry backstop).
- **Fix**: `rt_table.expires = now+24h` set before `create_table`.
- **Decision**: FIXED

### F3 — `num_dml_affected_rows=None → 0` branch untested

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Success Criteria
- **Location**: tests/test_bigquery_insert_only_merge.py
- **Detail**: Mocks always returned an int; the `or 0` None-guard had no coverage.
- **Fix**: `test_insert_only_none_affected_rows_returns_zero` added (7th test).
- **Decision**: FIXED

## Notes

- Post-fix verification: 7 passed, ruff clean, round-trip re-PASS on real BQ (throwaway tables, dropped in finally).
- `change.md` status intentionally stays `implementing` — this is a mid-stream phase review; `impl_reviewed` is reserved for the full-plan review after Phase 3.
