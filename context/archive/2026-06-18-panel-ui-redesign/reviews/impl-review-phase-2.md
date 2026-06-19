<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Panel UI/UX Redesign

- **Plan**: context/changes/panel-ui-redesign/plan.md
- **Scope**: Phase 2 of 5
- **Date**: 2026-06-18
- **Verdict**: NEEDS ATTENTION → APPROVED after triage
- **Findings**: 0 critical  2 warnings  1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | WARNING |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Findings

### F1 — Listener registered inside conditional — may accumulate on re-call

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: static/index.html ~line 267
- **Detail**: `initGdpr()` registers the `#gdpr-accept` click listener inside the `if (!localStorage.getItem(...))` branch. Safe today (called once), but fragile if ever called again — second listener would pile up.
- **Fix**: Add `{ once: true }` to `addEventListener`.
- **Decision**: FIXED — added `{ once: true }` to gdpr-accept listener

### F2 — `_accept_gdpr` autouse fixture will block Phase 5 GDPR tests

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Scope Discipline
- **Location**: tests/e2e/conftest.py:31–34
- **Detail**: Fixture uses `add_init_script` which re-fires on every navigation. `localStorage.clear()` in Phase 5 GDPR tests won't help — the script re-sets the flag before `initGdpr()` runs, making the banner permanently invisible in those tests.
- **Fix A ⭐ Applied**: Added marker guard — fixture skips for tests decorated with `@pytest.mark.gdpr`.
- **Decision**: FIXED via Fix A — marker guard added to `_accept_gdpr` fixture

### F3 — Unplanned `_accept_gdpr` autouse fixture (beneficial addition)

- **Severity**: 👁 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: tests/e2e/conftest.py:31–34
- **Detail**: Fixture not described in Phase 2 plan, but is the correct companion change to prevent banner from blocking 4 existing E2E tests.
- **Decision**: SKIPPED — expected companion change, no action needed
