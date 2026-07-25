<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Backfill historical daily closes (stooq) into company_daily_stats + etf_quotes

- **Plan**: `context/changes/backfill-historical-closes/plan.md`
- **Scope**: Phases 2–3 of 3 (Phase 1 covered by `reviews/impl-review-phase-1.md`)
- **Date**: 2026-07-25
- **Verdict**: APPROVED (after triage — F1 fixed, F2 recorded, F3 accepted, F4/F5 noted)
- **Findings**: 0 critical, 3 warnings, 2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING (accepted deviation, ticketed) |
| Scope Discipline | PASS (after triage — recorded in addendum) |
| Safety & Quality | PASS (after triage — F1 fixed) |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

Triage outcome: F1 fixed in code, F2 recorded in the plan addendum, F3 accepted as a deliberate ticketed deviation, F4 and F5 kept as notes for any re-run.

## Success criteria

| Step | Command | Result |
|---|---|---|
| 1.1 | `pytest tests/test_bigquery_insert_only_merge.py -q` | 7 passed |
| 1.2 / 2.2 | `pytest --ignore=tests/e2e -q` | 619 passed |
| 1.3 / 2.3 | `ruff check` (changed files) | clean |
| 2.1 | `pytest tests/test_backfill_historical_closes.py -q` | 24 passed (18 + 6 from F1 triage) |
| 1.4, 2.4, 3.1, 3.2, 3.3 | manual | all checked with evidence recorded in Progress |

No rubber-stamped manual items: every `[x]` in Progress carries a measured result (row counts, partition counts, duplicate counts, price spot-checks).

## Findings

### F1 — `--since` accepts any string, silently

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: `scripts/backfill_historical_closes.py:341` (argument), `:253` (`filter_rows_since`)
- **Detail**: `filter_rows_since` compares ISO date strings lexicographically with no validation of the `--since` value. Measured behaviour on a two-row fixture:

  ```
  --since 'garbage'     -> keeps 0/2   ingest writes nothing, exits 0, reports "0 inserted"
  --since '10.05.2011'  -> keeps 2/2   Polish-format date silently disables the partition guard
  ```

  The second case is the dangerous one: `--since` exists specifically to keep the table under BigQuery's 10 000-partition ceiling, and a plausible typo turns that guard off without a word. The archive spans 10 053 trading days, so an unguarded run breaches the ceiling mid-ingest.
- **Fix**: Validate in `main()` before use — `date.fromisoformat(args.since)` when the value is non-empty, printing an error and returning 1 on `ValueError`.
- **Decision**: FIXED — `parse_since()` round-trips through `date.fromisoformat` and rejects anything that is not canonical `YYYY-MM-DD`; `main()` exits 1 with the offending value. 6 new unit tests (`garbage`, `10.05.2011`, `2011`, `20110510`, `2011-13-01` all rejected); CLI verified to exit 1.

### F2 — Chart axis fix is outside the plan's scope

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: `static/index.html:4132`, `tests/e2e/test_portfolio_value_history.py:76`
- **Detail**: The x-axis date formatter gained a year (`DD.MM` → `DD.MM.YYYY`) plus a guarding e2e assertion. Both are correct and break-verified, but the plan describes only the backfill pipeline — no frontend change is listed under "Changes Required", so this is an EXTRA relative to the plan. It arrived as a direct user request while verifying the backfill in the UI.
- **Fix**: Record it in the plan's 2026-07-25 addendum so the plan stays the source of truth for future reviews.
- **Decision**: FIXED — recorded in the plan addendum as an accepted out-of-plan change.

### F3 — Contract decision #3 (raw closes) not met

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Plan Adherence
- **Location**: `scripts/backfill_historical_closes.py:1-20` (module docstring caveat), plan Contract decision #3
- **Detail**: The Contract calls for **raw (unadjusted)** closes. The bulk archive supplies **dividend-adjusted** prices, and 1 899 603 such rows were ingested. Evidence: adjusted values are not tick-compliant (`KGH` 2025-12-30 = `279.596`), and the factor reaches 1.0 only after each ticker's latest ex-dividend date. At least 140/771 tickers (18%, lower bound) are affected inside the trailing 12 months, making past portfolio values understated.
- **Fix**: None required now — the deviation is documented in the plan addendum, carried in the script docstring, stated in PR #196, and tracked in PUL-96/#191. The user accepted it explicitly to unblock the charts.
- **Decision**: ACCEPTED — deliberate, documented, ticketed

### F4 — `--chunk-size` default predates year-splitting

- **Severity**: 📝 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: `scripts/backfill_historical_closes.py:340`
- **Detail**: The default of 25 tickers per flush was chosen before `_flush()` began issuing one MERGE per year. A full-universe run at the default emits roughly 500 MERGE jobs; the actual production run used `--chunk-size 200` and emitted about 64. The default still works, it is just several times slower than necessary.
- **Fix**: Raise the default, or note the recommended value in the module docstring's run examples.
- **Decision**: SKIPPED — one-time script, already run; kept as a note for any re-run.

### F5 — Partition ceiling is guarded by a default, not an invariant

- **Severity**: 📝 OBSERVATION
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: `scripts/backfill_historical_closes.py:312` (`_flush`)
- **Detail**: Nothing checks that `existing partitions + incoming partitions ≤ 10 000` before writing. Protection rests entirely on `_DEFAULT_SINCE`. If it is overridden (see F1) the run proceeds until BigQuery rejects a job, leaving a partially ingested table. Current state is comfortable — 3 917 of 10 000 — so this is about future re-runs rather than today.
- **Fix**: Count distinct incoming dates plus existing partitions in `main()` and refuse to start past a threshold; or leave as-is given this is a one-time script and record the ceiling in the script docstring.
- **Decision**: SKIPPED — mitigated in practice by the F1 fix, which restores `--since` as a reliable guard; ceiling documented in the script docstring and plan addendum.
