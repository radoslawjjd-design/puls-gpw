<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Treemap D/D:/Total: labels, since-purchase P&L, hover highlight + click-to-filter

- **Plan**: context/changes/portfolio-treemap-labels-since-purchase-pnl-click-filter/plan.md
- **Scope**: Full plan (Phases 1, 2, 3)
- **Date**: 2026-06-21
- **Verdict**: APPROVED
- **Findings**: 0 critical, 2 warnings, 0 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | PASS |

## Summary

Reviewed the final state of all 3 phases. Git diff scope across `5c5effc..4ff2aba`
touches exactly the 7 files named in the plan (`src/portfolio_treemap.py`,
`src/api.py`, `static/index.html`, `tests/test_portfolio_treemap.py`,
`tests/test_api.py`, `tests/e2e/conftest.py`, `tests/e2e/test_portfolio_treemap.py`)
— no unplanned files. Both documented mid-implementation deviations (hover
tooltip removed and replaced with a click-triggered popup modal; popup's
navigate button fixed to set `#f-company` instead of `#f-ticker` since the
treemap's "ticker" field is actually a company display name from XTB OCR) are
faithfully implemented in code, with no dead code left from the removed
tooltip (zero references to `.tc-tooltip`/`tc-active`/flip classes). XSS-safe:
all popup content escaped on write (`esc()`) and read via `.textContent` only.
No event-listener leaks across repeated `injectAdminOnlyChrome()` calls (old
DOM subtree destroyed/recreated each time); the one genuinely persistent
listener (document-level Escape) is correctly guarded by `_treemapEscBound`
against double-registration. Full suite: 276/276 passed.

## Findings

### F1 — Popup duplicates an existing generic modal pattern

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Pattern Consistency
- **Location**: static/index.html (`.tc-popup-backdrop`/`.tc-popup`/`_openTreemapPopup`/`_closeTreemapPopup` vs. existing `.modal-overlay`/`openModal()`/`closeModal()` around line 346-353)
- **Detail**: The codebase already has a generic modal pattern used for announcement detail and X-post tweets. The treemap popup introduces a parallel, separately-named implementation instead of extending it — functionally correct and fully tested, but duplicates CSS/JS shape.
- **Fix A ⭐ Recommended**: Leave as-is, note for a future follow-up
  - Strength: Avoids touching already-tested, user-confirmed code for a maintainability-only concern.
  - Tradeoff: A third popup added later would be a third copy-paste instead of a shared component.
  - Confidence: HIGH — no functional issue, purely a DRY observation.
  - Blind spot: Haven't checked whether `openModal()`'s existing contract is compatible with this popup's centered/backdrop needs without changes.
- **Fix B**: Refactor now to reuse `openModal()`/`.modal-overlay`
  - Strength: One modal pattern going forward; less CSS to maintain.
  - Tradeoff: Touches already-shipped code for a non-functional concern; re-verification needed across existing modal callers and this popup's 5 E2E tests.
  - Confidence: MED — plausible but unverified compatibility.
  - Blind spot: Haven't traced every existing `openModal()` call site.
- **Decision**: Fixed via Fix A — deferred as a future follow-up note, no code change now.

### F2 — Two independent document-level Escape-key listeners

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: static/index.html:837-841 (treemap popup) vs. :1244-1248 (existing modal)
- **Detail**: Both listeners fire on every Escape press; both are idempotent no-ops when their own overlay is already hidden — no functional bug, just a second copy-pasted handler instead of one shared dispatcher.
- **Fix**: No action needed now; consolidate into a single Escape dispatcher if a third modal/popup is ever added.
- **Decision**: ACCEPTED — no code change; consolidate only if a third modal/popup appears.
