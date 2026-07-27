<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Full-coverage gate — backward-fill debut prices (PUL-100)

- **Plan**: `context/changes/history-coverage-gate/plan.md`
- **Scope**: Phases 1-4 of 4 (full plan)
- **Date**: 2026-07-26
- **Verdict**: APPROVED
- **Findings**: 0 critical, 1 warning, 2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | WARNING |

## Evidence

Diff `master..HEAD`: 11 files, +1140/−73. Production files touched are exactly those the
plan named — `db/bigquery.py`, `src/api.py`, `static/index.html` — plus their tests. No
file outside the plan's scope was modified; no planned file was left untouched.

Automated re-verification at review time:

| check | result |
|---|---|
| `pytest tests/test_bigquery.py tests/test_api.py` | 263 passed |
| `pytest tests/e2e` | 118 passed |
| `ruff check` on all six changed files | clean |
| CI on PR #200 | `ai-code-review/verdict` pass (min score 9), `ai-security-review/verdict` pass (min score 10), Tests pass |

Safety spot-checks: the query is fully parameterised (`@user_id`, `@start_date`,
`@portfolio_id`) with no interpolation of user data; ticker strings reaching the DOM pass
through `esc()`, which escapes `"` to `&quot;`, so the `aria-label` attribute cannot be
broken out of; no new external boundary and therefore no new error path; read-only query,
no data-safety surface.

The two documented deviations from the plan — the meta-first join (plan-review F1) and the
affordance living in `.pp-hist-head` rather than beside the `<h3>` — are both recorded as
addenda in the plan with their reasoning, so neither is silent drift.

## Findings

### F1 — The `excluded` branch renders in no test

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: `tests/e2e/conftest.py:378`, `static/index.html` (`_ppHistNoteLines`)
- **Detail**: The DB layer has a unit test for excluded tickers and the endpoint passes
  `excluded` through, but the shared e2e fake returned `excluded: []` for every wallet, so
  the rendered line *"<TICKER> — brak notowań, pominięty w wycenie"* was never exercised.
  A typo or logic error in that branch would have shipped unnoticed — and it is the branch
  a user only ever sees in the worst case, when a holding could not be priced at all.
- **Fix**: Give the active portfolio's fake an excluded ticker (`AAS`) alongside its
  existing note, and assert the rendered line. Deliberately *not* added to the aggregate
  fake: that wallet's empty metadata is what covers the negative branch — a chart with
  nothing to disclose must grow no affordance — and the existing `to_have_count(0)`
  assertion depends on it staying empty.
- **Decision**: FIXED

### F2 — Two success criteria remain unchecked

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: `## Progress` 4.2, 4.6
- **Detail**: `4.2` (`ruff check .`) does not pass and never did — 33 errors predating this
  change in `api_main.py`, `post_main.py`, `tests/test_scraper.py` and others, none in
  files this change touches. `4.6` requires a deployed revision, so it cannot be satisfied
  before merge. Both are documented in `change.md`; neither was quietly ticked.
- **Fix**: Leave unchecked. `4.6` closes after deploy; the repo-wide lint debt belongs in
  its own change.
- **Decision**: ACCEPTED

### F3 — The metric toggle collapses an open note

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: `static/index.html:3877-3878`
- **Detail**: Switching Wartość↔Zysk/strata re-runs `_renderPortfolioHistory` from cache,
  which rebuilds the affordance in its closed state. Standard for a full redraw, but a
  user reading the note and switching modes has to reopen it.
- **Fix**: Persist the open/closed state per chart across redraws, if it ever proves
  irritating in use. Not worth the state-tracking today.
- **Decision**: SKIPPED

## Triage Summary

Fixed: F1 (1). Accepted: F2 (1). Skipped: F3 (1).

► Verdict after fixes: **APPROVED**
