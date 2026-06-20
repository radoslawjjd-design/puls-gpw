<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Admin UI portfolio treemap with daily P&L colouring

- **Plan**: context/changes/admin-ui-portfolio-treemap/plan.md
- **Scope**: Full plan (Phases 1–3 of 3)
- **Date**: 2026-06-20
- **Verdict**: APPROVED
- **Findings**: 0 critical 1 warning 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Findings

### F1 — Unhandled ValidationError if positions_json has wrong-typed value

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/api.py:241 (`TreemapPosition(**p)` construction)
- **Detail**: `compute_treemap_positions()` guards against malformed `positions_json` (bad JSON, missing keys, non-dict items → returns `[]`), but doesn't guard against a present `"value"` key holding a non-numeric type. That dict still reaches `TreemapPosition(**p)` unwrapped in a try/except — a pydantic `ValidationError` there isn't caught by the existing `except BigQueryError` block, surfacing as an unhandled 500 instead of the route's normal clean-error pattern. Low severity: upstream writer is the internal `/portfolio-xpost` skill, which always writes numeric `"value"` — this is defense against a hypothetical future regression in that skill, not an observed failure.
- **Fix**: Wrap the `[TreemapPosition(**p) for p in positions]` construction (or the route body generally) in a broader `except Exception` → 500, matching the defensive posture already used in `compute_treemap_positions` for malformed input.
- **Decision**: FIXED — added `except ValidationError` (pydantic) in `src/api.py` around the `TreemapPosition` construction, returning a clean 500 instead of an unhandled exception. Regression test added: `test_admin_treemap_malformed_position_value_returns_500` in `tests/test_api.py`.

### O1 — get_latest_snapshot() duplicates get_latest_snapshot_before()'s row-mapping

- **Severity**: 📝 OBSERVATION
- **Dimension**: Architecture
- **Location**: db/bigquery.py:313-343
- **Detail**: Near-identical dict-building boilerplate to the existing `get_latest_snapshot_before()`. Not a defect — just a candidate for a future `_row_to_snapshot_dict(row)` extraction if a third caller ever shows up. Not worth doing for two call sites.
- **Fix**: N/A — no action recommended at this scope.
- **Decision**: SKIPPED
