<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Admin UI portfolio treemap with daily P&L colouring

- **Plan**: context/changes/admin-ui-portfolio-treemap/plan.md
- **Mode**: Deep
- **Date**: 2026-06-20
- **Verdict**: REVISE (pre-fix) → SOUND (post-fix, all findings resolved)
- **Findings**: 0 critical, 2 warnings, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | WARNING |
| Plan Completeness | WARNING |

## Grounding

5/5 paths ✓ (db/bigquery.py, src/api.py, static/index.html, src/portfolio_thread_composer.py, tests/test_portfolio_thread_composer.py), 3/3 symbols ✓ (`get_latest_snapshot_before`, `_require_admin`, `ConfigDict(extra="ignore")` convention), brief↔plan ✓.

## Findings

### F1 — Empty-state mechanism contradicts itself across Phase 3

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 3, §1 (view shell) vs §2 (fetch + render)
- **Detail**: §1 described a separate "empty-state message element" toggled by JS; §2's `fetchTreemap` contract instead injected the empty message directly into `#treemap-container`. Two different mechanisms for the same UI state.
- **Fix**: Drop the separate empty-state element from §1. Single `#treemap-container`; `fetchTreemap()` sets its innerHTML to either rendered cells or "Brak danych portfela" text, matching §2 and the `renderXPostsTable` precedent (`static/index.html:820`).
- **Decision**: FIXED

### F2 — No instruction to clear stale rectangles on re-render

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 3, §2 (fetch + render)
- **Detail**: §2 said to create cells "inside #treemap-container" but never said to clear prior content first. `showTreemapView()` re-fetches every time the menu item is clicked, so reopening the view would accumulate stale rectangles on top of new ones unless content is replaced, not appended.
- **Fix**: Build cells as one HTML string and assign to `#treemap-container.innerHTML` in one shot, matching the `renderXPostsTable` replace pattern (`static/index.html:820`).
- **Decision**: FIXED

### F3 — Manual resize test assumes a re-layout that isn't wired

- **Severity**: 👁️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 3 Manual Verification, step 3.5 / Progress 3.5
- **Detail**: No resize listener is wired (consistent with the plan's lean scope). The original step "Shrink the browser window ... confirm small rectangles fall back to ticker-only text" could be misread as expecting live re-layout while the view stays open.
- **Fix**: Reworded step 3.5 to clarify the tester must close and reopen the treemap view after resizing (since `showTreemapView()` re-fetches and re-measures), or use the many-position-wallet alternative already present.
- **Decision**: FIXED

## Notable strengths (not findings)

- Current State Analysis matches live code exactly: confirmed `positions_json` shape against `SKILL.md:287-300`, confirmed `get_latest_snapshot_before` signature (`db/bigquery.py:273-310`), confirmed no `StaticFiles` mount exists, confirmed `static/` has no other files that would be unintentionally exposed by the new mount.
- Scope boundaries ("What We're NOT Doing") are explicit and respected by every phase — no scope creep found.
- Progress section mechanically matches all phase success-criteria bullets 1:1 (Phase 1: 4 automated + 2 manual; Phase 2: 3 automated + 1 manual; Phase 3: 2 automated + 5 manual).
