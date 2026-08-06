<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Repair NewConnect historical closes

- **Plan**: `context/changes/newconnect-raw-closes/plan.md`
- **Scope**: Phases 1-4 (all)
- **Date**: 2026-08-05
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

Two plan items were dropped mid-flight and both are recorded in the plan with the
measurement that killed them, rather than silently omitted: Phase 5 (merge-conflict risk
against the open PR #249) and the MCR name-mapping fix (the premise was false — MCR is
absent from the archive on the contaminated dates). Phase 1 also reused
`parse_stooq_csv` / `classify_response` instead of writing the new parser the plan
specified; that is a reduction in scope, documented in the plan before the code landed.

## Findings

### F1 — A pure row-builder transitively imports BigQuery

- **Severity**: OBSERVATION
- **Impact**: LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Architecture
- **Location**: `scripts/correct_newconnect_closes.py:122`
- **Detail**: `build_correction_rows` is pure logic with pure-logic tests, but it calls
  `_load_sibling("correct_official_closes")` to reach `derive_zmiana_kwotowa`, and that
  module imports `db.bigquery` at module scope. The tests pass today because
  `db.bigquery` imports without credentials, so the coupling is invisible — it would
  become a broken test suite the day that stops being true.
- **Fix**: Either move `derive_zmiana_kwotowa` into `src/` where both scripts can reach
  it without pulling in the DB layer, or duplicate its three lines with a cross-reference.
- **Decision**: ACCEPTED — the alternatives are a refactor of the PUL-98 script (risk
  against a working corrective pass) or duplicating a load-bearing financial formula that
  would then drift. The current coupling is explicit and tested. Revisit if a second
  consumer appears.

### F2 — The contamination report loads every stored close for 328 tickers

- **Severity**: OBSERVATION
- **Impact**: LOW
- **Dimension**: Safety & Quality
- **Location**: `scripts/correct_newconnect_closes.py:258`
- **Detail**: `--report-contaminated` pulls `(ticker, snapshot_date, kurs_zamkniecia)`
  for every ticker with an adjusted bulk series, back to 2011, and compares in Python.
  Measured acceptable for a one-off report, but it is an unbounded scan that grows with
  history.
- **Fix**: Restrict the query to the dates the bulk archive marks adjusted, which is
  already computed before the query runs.
- **Decision**: SKIPPED — reporting is manual and infrequent; not worth the complexity
  until it hurts.

### F3 — `load_stored_closes` scans the whole window for every ticker

- **Severity**: OBSERVATION
- **Impact**: MEDIUM — worth knowing before the deferred repairs run
- **Dimension**: Safety & Quality
- **Location**: `scripts/correct_official_closes.py:266` (inherited, not introduced here)
- **Detail**: The helper queries `WHERE snapshot_date BETWEEN @since AND @until` with no
  ticker predicate and filters in Python. Harmless for BAC (2024-2026, one ticker), but
  a deferred repair of MCR spans 2011 onwards and would scan ~15 years across every
  ticker in the table to extract one.
- **Fix**: Push the ticker set into the query as an array parameter.
- **Decision**: SKIPPED here — it is PUL-98 code and changing it would put a working
  corrective pass at risk inside an unrelated change. Recorded so the next per-ticker
  repair does not discover the cost by surprise.

## Verification performed

- Full suite: **1094 passed**.
- `ruff check .`: clean.
- Production repair re-run: **0 of 574** sessions need correcting — idempotent.
- Guard exercised both ways against real files: accepts the download, rejects the
  archive 532/532.
- Cross-validation: the dry run's visible-year count (208 of 250) matches the figure
  research reached through an entirely separate code path.
