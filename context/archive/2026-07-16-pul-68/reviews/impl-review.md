<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Portfolio Calendar MTD Value Difference

- **Plan**: context/changes/pul-68/plan.md
- **Scope**: All phases (1–2 of 2)
- **Date**: 2026-07-16
- **Verdict**: APPROVED
- **Findings**: 0 critical  2 warnings  2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | PASS |

## Findings

### F1 — Algorithm drift not reflected in plan.md spec

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: context/changes/pul-68/plan.md (Changes Required)
- **Detail**: "Changes Required" described old approach (portfolio_value - baseline_value). Implementation uses cumulative daily_change_pln sum (correct fix for deposit-inflation bug). Plan spec was misleading for future readers.
- **Fix**: Added impl note in plan.md "Changes Required" documenting the algorithm revision.
- **Decision**: FIXED

### F2 — `pnl or 0.0` conflates None with 0.0

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/portfolio_calendar.py:112
- **Detail**: `pnl or 0.0` is numerically correct but conflates missing BQ field (None) with flat trading day (0.0). Explicit guard is cleaner.
- **Fix**: Replaced `pnl or 0.0` with `pnl if pnl is not None else 0.0`.
- **Decision**: FIXED

### F3 — `test_day_object_has_all_required_fields` missing `mtd_diff`

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: tests/test_portfolio_calendar.py:162
- **Detail**: `required` set did not include `mtd_diff`. Accidental field removal would not be caught.
- **Fix**: Added `"mtd_diff"` to `required` set.
- **Decision**: FIXED

### F4 — Agent flagged `display='block'` (DISMISSED)

- **Severity**: ℹ️ OBSERVATION
- **Dimension**: Pattern Consistency
- **Location**: static/index.html
- **Detail**: Safety agent suggested `display=''` to match file convention. Dismissed — `#pp-cal-mtd-summary` is hidden by a CSS rule, so `''` restores CSS display:none (keeps hidden). `'block'` is correct here. Other 12 call-sites use `''` because those elements are hidden via inline style, not CSS rule.
- **Decision**: DISMISSED
