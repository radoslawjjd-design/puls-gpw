<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Profile Menu Dropdown (PUL-47)

- **Plan**: context/changes/profile-menu-dropdown/plan.md
- **Scope**: Phase 1 of 1 (full plan)
- **Date**: 2026-06-19
- **Verdict**: APPROVED
- **Findings**: 0 critical, 1 warning, 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | WARNING |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Success criteria verification

- Automated: `uv run pytest tests/e2e/test_profile_menu.py tests/e2e/test_idle_timeout.py` — 7 passed.
- Manual: all items in plan.md's Progress section are `[x]` with commit refs (58b5074, 10e8270).

## Findings

### F1 — Unplanned sticky topbar styling

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Scope Discipline
- **Location**: static/index.html:62-64
- **Detail**: `.topbar` gained `position: sticky; top: 0; z-index: 60; background: #f4f6f8; padding: .6rem 0;` in commit 58b5074. Not in plan.md's Contract, not in the Linear PUL-47 description (confirmed directly), but called out as deliberate in the commit message ("...mobile anchor fix, and sticky topbar"). Functionally harmless — no z-index conflicts with `.modal-overlay` (1000) or `#gdpr-banner` (200); all tests and manual checks pass. Scope/documentation gap, not a defect.
- **Fix A ⭐ Recommended**: Add an addendum to plan.md documenting the sticky-topbar decision and why.
  - Strength: Preserves a real, already-shipped UX improvement; keeps plan.md accurate for future readers (PUL-43/44/45/28 extend this same topbar).
  - Tradeoff: Plan retroactively grows scope — mild process debt.
  - Confidence: HIGH — change is isolated, low-risk, already passing all checks.
  - Blind spot: Whether sticky topbar was visually reviewed at very narrow viewports beyond the <640px check already done.
- **Fix B**: Revert the sticky/background/padding/z-index additions to match plan.md literally.
  - Strength: Implementation matches plan.md literally, zero scope ambiguity for reviewers.
  - Tradeoff: Throws away a deliberate, working UX improvement for no functional gain.
  - Confidence: MEDIUM — no regression risk in reverting, but loses value.
  - Blind spot: Unclear if anything visually depends on the sticky topbar already.
- **Decision**: FIXED (via Fix A) — addendum added to plan.md under "## Addendum: sticky topbar (post-implementation, 58b5074)".

### F2 — `.profile-menu` uses `hidden` attribute, not `style.display`

- **Severity**: ℹ️ OBSERVATION
- **Dimension**: Pattern Consistency
- **Location**: static/index.html:259,529,534,539,543
- **Detail**: `.ac-dropdown` and `.modal-overlay`/`closeModal()` toggle via `style.display`; the new profile menu uses the `hidden` attribute — a third show/hide idiom in the file. Likely intentional (native `:not([hidden])` + ARIA semantics pair well with `role="menu"`); plan never mandated reusing `style.display`.
- **Decision**: SKIPPED (no fix needed)

### F3 — Focus set on trigger immediately before `doLogout()` tears down the screen

- **Severity**: ℹ️ OBSERVATION
- **Dimension**: Safety & Quality
- **Location**: static/index.html:522-525,533-537
- **Detail**: `logout-btn`'s click handler calls `closeProfileMenu()` (focuses the trigger) then `doLogout()`, which synchronously hides `#dashboard-screen`. Harmless dead work, no visible flash.
- **Decision**: SKIPPED (no fix needed)

### F4 — `.profile-menu` and `.ac-dropdown` share z-index 50

- **Severity**: ℹ️ OBSERVATION
- **Dimension**: Architecture
- **Location**: static/index.html:80 (vs. 113)
- **Detail**: Both popovers use z-index 50; never visible simultaneously today, so no real conflict. Worth revisiting if a future ticket (PUL-43/44/45) ever nests one inside the other's trigger area.
- **Decision**: SKIPPED (no fix needed)
