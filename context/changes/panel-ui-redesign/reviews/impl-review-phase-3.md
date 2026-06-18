<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Panel UI/UX Redesign

- **Plan**: context/changes/panel-ui-redesign/plan.md
- **Scope**: Phase 3 of 5
- **Date**: 2026-06-18
- **Verdict**: APPROVED (after triage fixes)
- **Findings**: 0 critical  4 warnings  1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | PASS |

## Findings

### F1 — 401 from autocomplete endpoints silently ignored

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: static/index.html:363–372
- **Detail**: `loadAutocomplete()` skips non-ok responses silently. If the session key expires mid-flight, dropdowns stay empty with no feedback. `fetchAnnouncements()` already handles 401 by calling `showLogin()` — autocomplete was inconsistent.
- **Fix**: Added 401 guard before the `if (tRes.ok)` checks, mirroring the pattern in `fetchAnnouncements()`.
- **Decision**: FIXED via Fix now

### F2 — Concurrent loadAutocomplete() calls make redundant BQ fetches

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: static/index.html:341–376
- **Detail**: `_acInitialized` blocks double setup but not concurrent fetch calls. Two rapid `showDashboard()` calls (double-click, popstate race) could fire two parallel BQ fetches before the first resolves.
- **Fix**: Added `_acLoading` promise guard — `loadAutocomplete()` returns the in-flight promise if one is already active.
- **Decision**: FIXED via Fix now

### F3 — 150ms blur-to-hide delay may lose tap selection on mobile

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: static/index.html:326
- **Detail**: `mousedown` + `e.preventDefault()` works on desktop. On mobile/touch, blur can fire before the emulated mousedown, and the 150ms gap may be exceeded on slow devices.
- **Fix**: Changed `mousedown` → `pointerdown` (covers touch + mouse); increased blur delay 150ms → 200ms.
- **Decision**: FIXED via Fix now

### F4 — After-cache-clear skipped on assertion failure in two tests

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: tests/test_api.py:172–173, 187–188
- **Detail**: After-cache-clear sat after the `with patch` block but before the assert — skipped on failure. Backstop existed in cache-hit tests, but fragile.
- **Fix**: Added `autouse` fixture `_clear_ac_cache` that clears before and after every test in the module. Removed all 8 inline `_AC_CACHE.clear()` calls.
- **Decision**: FIXED via Fix A (autouse fixture)

### F5 — No enforced cache-clearing contract for future autocomplete tests

- **Severity**: 👁️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: tests/test_api.py:20
- **Detail**: `_AC_CACHE` is shared module-level state. F4 fix (autouse fixture) resolves this by enforcing the contract automatically for all tests.
- **Decision**: RESOLVED by F4 fix
