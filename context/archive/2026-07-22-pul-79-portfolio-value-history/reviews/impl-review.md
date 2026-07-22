<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Portfolio Value-History Endpoint (PUL-79 / FARO-5)

- **Plan**: context/changes/pul-79-portfolio-value-history/plan.md
- **Scope**: Full plan (Phase 1 + Phase 2 of 2)
- **Date**: 2026-07-22
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 1 observation (fixed)

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS (2.5 pending post-deploy) |

## Success Criteria (re-run fresh, post-commit)

- Phase 1: `pytest -k history` (BQ) + full unit suite 582 passed; ruff clean.
- Phase 2: history unit tests (16 after F1 fix), E2E 108 passed, full suite green, my files ruff-clean.
- 2.5 (prod curl) — pending post-deploy; cannot run before merge.

Cross-phase contract verified: endpoint calls `get_portfolio_history(portfolio_id, user_id, start_date)` matching the Phase 1 signature; `row["snapshot_date"].isoformat()` matches the date returned by the BQ fn.

Beneficial deviations (not findings): resolver typed `-> date | None` for direct unit-testability; endpoint fn named `get_portfolio_value_history` to avoid clashing with the imported `get_portfolio_history`. Range is validated before it reaches the cache key (no cache poisoning via arbitrary range strings).

## Findings

### F1 — Endpoint tests slightly thinner than the calendar sibling

- **Severity**: 🔵 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: tests/test_api.py — history endpoint tests
- **Detail**: Endpoint tests covered 200/shape, 401, 403, 422-for-1d. The 422-for-garbage-range and 500-on-BigQueryError paths were covered only transitively (resolver test + shared try/except pattern), unlike the calendar sibling which tests them explicitly.
- **Fix**: Added two endpoint tests mirroring the calendar sibling — `test_get_portfolio_history_returns_422_for_unknown_range` and `test_get_portfolio_history_returns_500_on_bq_error`.
- **Decision**: FIXED — 2 tests added; history endpoint tests now 16 passing.

## Note

Phase 1's earlier observation (LOCF can carry a stale price for a delisted/halted holding, `reviews/impl-review-phase-1.md`) remains recorded and SKIPPED — revisit only if delisted holdings appear.

change.md advanced to `impl_reviewed`. The sole remaining plan item is the post-deploy prod curl (2.5), to be verified after merge+deploy, before archive.
