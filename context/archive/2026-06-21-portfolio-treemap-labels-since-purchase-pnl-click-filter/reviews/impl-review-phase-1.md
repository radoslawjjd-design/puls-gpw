<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Treemap D/D:/Total: labels, since-purchase P&L, hover highlight + click-to-filter

- **Plan**: context/changes/portfolio-treemap-labels-since-purchase-pnl-click-filter/plan.md
- **Scope**: Phase 1 of 3
- **Date**: 2026-06-21
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Summary

Diff scope is exactly commit `5c5effc`. Changed files: `src/portfolio_treemap.py`, `src/api.py`,
`tests/test_portfolio_treemap.py`, plus `tests/test_api.py` (not named in the plan's Phase 1 file
list, but a justified EXTRA — its pre-existing exact-equality assertions against
`/admin/portfolio/treemap` broke once `TreemapPosition` gained two new fields; fixing it was
necessary, not scope creep). All 3 planned changes (`compute_treemap_positions()` derivation,
`TreemapPosition` model fields, new/updated unit tests) verified MATCH against plan contract.
55/55 relevant tests pass (`tests/test_portfolio_treemap.py` + `tests/test_api.py`), 271/271 full
suite passed during implementation. Manual verification (1.3) confirmed via live curl against real
BQ data — plausible values (e.g. Digital Network: 285.19% / 2887.51 PLN, matching
`cost = 3900/(1+2.8519) ≈ 1012.49`; negative-pct positions correctly show negative PLN).

## Findings

### F1 — `isinstance(pct, (int, float))` would also accept a bool

- **Severity**: OBSERVATION
- **Impact**: LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/portfolio_treemap.py:52
- **Detail**: Python's `bool` is a subclass of `int`, so this guard would treat a JSON `true`/`false` value as numeric. Not reachable in practice: `pct` is produced exclusively by `gemini_client.py`'s structured-output schema (`"pct": <float|null>`), which Pydantic-validates before this function ever sees the data — a boolean can't arrive here.
- **Fix**: No action needed; documented as an accepted non-issue.
- **Decision**: ACCEPTED — no fix needed, non-reachable path

### F2 — No test for non-numeric `pct` value (e.g. a string)

- **Severity**: OBSERVATION
- **Impact**: LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: tests/test_portfolio_treemap.py
- **Detail**: Existing tests cover missing `pct` key, `pct == -100`, and a normal positive `pct`, satisfying the plan's required cases exactly. A defensive case for `pct` arriving as a non-numeric JSON value (string) isn't covered, though it's already handled correctly by the `isinstance` guard.
- **Fix**: Optional belt-and-suspenders test; not required by the plan.
- **Decision**: SKIPPED — optional, not required by plan
