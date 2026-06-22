<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Admin Dashboard — Per-View URLs and Pagination in Browser History

- **Plan**: context/changes/spa-view-url-routing/plan.md
- **Scope**: Phase 2 of 4
- **Date**: 2026-06-22
- **Verdict**: APPROVED
- **Findings**: 0 critical, 1 warning, 1 observation

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

### F1 — _isoToLocalInputValue() has no invalid-date guard

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: static/index.html:1343 (called from :919, :922)
- **Detail**: A crafted/garbage `from=`/`to=` query param flows from `_applyAnnouncementsParams()` into `_isoToLocalInputValue()`, which does `new Date(iso)` with no validity check. The file already has an established convention for this exact failure mode — `parseDateOrNull()` guards with `Number.isNaN(d.getTime())` "to guard against RangeError on garbage input" — but the new helper doesn't reuse it. Result: a garbage URL param silently writes `NaN-NaN-NaNTNaN:NaN` into the date input's `.value` (browsers reject it silently, no crash, but inconsistent with the codebase's own documented pattern).
- **Fix**: Guard with the same `Number.isNaN(d.getTime())` check used by `parseDateOrNull`; on invalid input, have `_applyAnnouncementsParams` clear the field instead of writing a NaN string.
- **Decision**: FIXED — `_isoToLocalInputValue` now returns `null` on an invalid date; `_applyAnnouncementsParams` clears the field when that happens.

### F2 — currentPage restore accepts negative page numbers

- **Severity**: 📝 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: static/index.html:925
- **Detail**: `currentPage = Number(params.get('page')) || 1` accepts `page=-5` as-is and sends it to the API. Pre-existing gap (same risk existed before this diff for any page mutation), not a regression — flagged only because URL restore is a new entry point for it. Non-blocking.
- **Fix**: `Math.max(1, Number(params.get('page')) || 1)`.
- **Decision**: FIXED — `currentPage` restore now clamps to a minimum of 1.
