<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Profile Menu Dropdown Implementation Plan

- **Plan**: context/changes/profile-menu-dropdown/plan.md
- **Mode**: Deep
- **Date**: 2026-06-19
- **Verdict**: SOUND
- **Findings**: 0 critical, 0 warnings, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | PASS |
| Plan Completeness | PASS |

## Grounding

6/6 paths verified (static/index.html:236-242, 90-103, 122-156/734-740, 501-503, 555, topbar CSS @61-71/186-190), 4/4 symbols verified (role-badge, logout-btn, doLogout, closeModal), brief↔plan consistent.

Blast-radius check: `doLogout()` is called directly by the idle-timeout path (line 362) and 401 handlers (lines 457, 611) — none reach `#logout-btn` via the DOM, so relocating the button into the menu carries no hidden coupling risk.

## Findings

### F1 — No explicit z-index on .profile-menu

- **Severity**: 📝 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Architectural Fitness
- **Location**: Phase 1, Change #2 (Dropdown styling)
- **Detail**: The `.profile-menu` CSS contract specified position/chrome but no `z-index`, unlike every other overlay in the file (`.ac-dropdown`: 50, `.modal-overlay`: 1000). Low practical risk since the topbar is first in DOM order, but it would have been the only overlay relying on implicit paint order instead of an explicit value.
- **Fix**: Add `z-index: 50` (matching `.ac-dropdown`) to the `.profile-menu` rule.
- **Decision**: FIXED (applied directly to plan.md, Change #2 contract)
