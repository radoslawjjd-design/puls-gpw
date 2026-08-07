<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Daily Cost Report

- **Plan**: `context/changes/daily-cost-report/plan.md`
- **Scope**: Phases 1–5 of 5 (all automated criteria complete)
- **Date**: 2026-08-07
- **Verdict**: NEEDS ATTENTION → **APPROVED after triage** (F1–F4 fixed)
- **Findings**: 0 critical, 4 warnings, 2 observations

## Verdicts

| Dimension | Verdict | After triage |
|-----------|---------|--------------|
| Plan Adherence | PASS | PASS |
| Scope Discipline | WARNING | WARNING (F5, F6 accepted) |
| Safety & Quality | WARNING | PASS |
| Architecture | WARNING | PASS |
| Pattern Consistency | WARNING | PASS |
| Success Criteria | WARNING | PASS |

## Grounding

Files in the diff match the plan's file list exactly — no unplanned source
files. Automated criteria at review time: full suite 1177 passed, `ruff check .`
clean, `tests/test_deploy_workflow_filter.py` 4 passed. After the triage fixes:
1178 passed, ruff clean.

## Findings

### F1 — `_BASELINE_WINDOW_DAYS` declared twice

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Architecture
- **Location**: `cost_report_main.py:26` + `src/cost_report.py:40`
- **Detail**: One constant said how many days to *fetch*, a second said how many
  to *judge*, and nothing coupled them. Shrinking the entry point's copy below
  `_MIN_BASELINE_DAYS` would make `trailing_median` return `None` permanently:
  the flag stops firing forever, the mail still renders, and every test stays
  green because each side is internally consistent. The code carried a comment
  acknowledging exactly this risk instead of a guard against it — and the project
  already learned this rule once on `deploy.yml` × `.dockerignore`: two configs
  that must agree need a test or a single source, not a comment.
- **Fix**: Promote the constant to `BASELINE_WINDOW_DAYS` in `src/cost_report.py`
  and import it in the entry point, removing the second copy.
- **Decision**: FIXED

### F2 — `classify_sku` keyed the model on the bare substring `"GA"`

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: `src/cost_report.py:94`
- **Detail**: `elif "GA" in desc` never verified the SKU was a *Flash* line. A
  future `"Gemini 3 Pro GA Text Input - Predictions"` would be filed under
  `gemini-2.5-flash`. The dangerous part is that it would reconcile: per-model
  gross would still sum to the Vertex AI service line, so the one check designed
  to catch dropped SKUs would pass while the table attributed another model's
  spend to Flash. Precisely the quiet-misattribution class Phase 2 exists to
  prevent. All seven live SKUs carry the full name, so tightening costs nothing.
- **Fix**: Match `"Flash Lite"` / `"Flash GA"`; unrecognised models fall through
  to the visible `"other"` row.
- **Decision**: FIXED (+ regression test
  `test_another_models_ga_sku_does_not_land_in_the_flash_row`)

### F3 — `test_the_report_covers_yesterday_not_today` asserted nothing

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: `tests/test_cost_report_main.py:78`
- **Detail**: `assert summary["report_date"] == cost_report_main._report_date()`
  compared the function under test against itself. It would pass unchanged if
  `_report_date()` returned today, tomorrow, or a date last year. A test whose
  name promises coverage its assertion does not provide is worse than no test,
  because the next reader treats the question as settled.
- **Fix**: Compute the expected Warsaw yesterday independently in the test, and
  additionally assert both fetch windows are anchored to that same day.
- **Decision**: FIXED

### F4 — Two interpolations skipped `quote=True`

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: `src/notifier.py:446, 455`
- **Detail**: Both sit in element text rather than an attribute and both carry
  internally-generated numbers, so there is no exploitable path. But the
  function's own docstring states that every interpolated value is escaped with
  `quote=True`, and an exception a reader has to reason through to clear is worse
  than uniformity.
- **Fix**: Add `quote=True` to both.
- **Decision**: FIXED

### F5 — `usage_unit` crosses the data layer and is never read

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: `db/bigquery.py:4445, 4464`
- **Detail**: Fetched, packed into the row dict, and dropped by `build_report`;
  the mail hard-codes "Tokeny". Deliberate — the export's unit field reads
  `requests` and is wrong — but a reader will assume the field is load-bearing.
- **Fix**: Keep it and say why in the docstring, or drop it.
- **Decision**: ACCEPTED — deliberate, harmless, and the row dict mirroring the
  query's SELECT list is its own kind of clarity.

### F6 — Post-trigger schedule correction in `infra.md` was unplanned

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: `context/foundation/infra.md:93-95`
- **Detail**: All three post triggers were documented five minutes later than
  they actually run (08:25/12:55/17:25, not 08:30/13:00/17:30). Found while
  verifying this change's own new rows against `gcloud scheduler jobs list`.
  Formally out of PUL-125's scope.
- **Fix**: Leave as corrected.
- **Decision**: ACCEPTED — leaving known-wrong rows adjacent to a newly added
  correct one would be worse than the scope creep.

## Notes

Two of the four warnings (F1, F3) were places where the implementation left a
comment describing a risk instead of a mechanism preventing it. Worth watching
for as a pattern rather than as two isolated defects.
