<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Faro Graphics Integration — Implementation Plan

- **Plan**: context/changes/faro-graphics-integration/plan.md
- **Scope**: Full plan (Phase 1) + out-of-plan commits merged under the same PUL-58 PR (#96)
- **Date**: 2026-06-25
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical, 3 warnings, 2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING |
| Scope Discipline | WARNING |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | PASS |

## Findings

### F1 — Out-of-plan API contract + nav IA change shipped under PUL-58 (commit 86f54fe)

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Scope Discipline
- **Location**: `src/api.py:325-351`, `static/index.html` (topbar-nav restructure + mobile grid CSS)
- **Detail**: plan.md scopes this change to a single phase: the login-screen banner. Commit `86f54fe` (merged into the same PR) bundles two unrelated, undocumented pieces of feature work: (1) promotes admin-only nav items from the profile dropdown into the topbar nav bar + adds a responsive 2x2 mobile grid layout, and (2) adds a new `as_of` field to the `/admin/portfolio/treemap` API response, changing its response type to `dict[str, list[dict] | str | None]`. Neither appears anywhere in plan.md's scope, "Changes Required", or "What We're NOT Doing" sections, and the plan's `## Progress` ledger was never updated to add rows for this work — it still only lists Phase 1 / `b487532`. Drift-detection sub-agent confirmed: 511 changed lines in `index.html` + 11 in `src/api.py` + 5 test files updated, none traceable to the plan. Safety/quality sub-agent found the new code itself is correct and safe (role-gated DOM insertion preserved, no XSS, consistent error handling) — this is a documentation/scope-tracking gap, not a functional defect.
- **Fix**: Append an addendum section to `plan.md` (or `change.md` Notes, following the existing "Phase 1 design pivot" note convention already used in this file) documenting the nav-restructure + treemap `as_of` work as an out-of-plan addition, with a one-line rationale and the commit SHA, so the plan accurately reflects what shipped.
  - Strength: Matches the project's own established pattern (the change.md "Phase 1 design pivot #1/#2" notes already do this for undocumented pivots) — cheap, no code risk, restores plan-as-source-of-truth.
  - Tradeoff: Doesn't undo the bundling; future readers still see one PR/commit doing two unrelated things.
  - Confidence: HIGH — purely additive documentation fix, zero behavior change.
  - Blind spot: None significant.
- **Decision**: FIXED — added "Out-of-plan addendum" note to change.md (covers F1 + F2 together)

### F2 — Color rebrand commit violates an explicit plan boundary (commit fcff425)

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Scope Discipline
- **Location**: `static/index.html` — `.topbar h1` rule (styles `#topbar-home`)
- **Detail**: plan.md line 54 explicitly states "No changes to the topbar `#topbar-home` heading or any other 'Faro' text elsewhere in the app." Commit `fcff425` recolors `.topbar h1` (which styles `#topbar-home`) as part of a broader navy-palette rebrand. The commit message self-discloses as "ad-hoc follow-up... outside the formal plan scope," so the deviation was transparent at commit time, but plan.md itself was never amended to reflect it. Risk is low — color-only, no structural/logic change, already covered by the full E2E suite passing.
- **Fix**: Fold into the same addendum note as F1 — one extra line noting the `#topbar-home` color change and that it was a deliberate, disclosed exception to the stated boundary.
- **Decision**: FIXED — covered by the same change.md addendum note as F1

### F3 — Login box max-width drifted from plan contract without updating plan.md (480px → 528px)

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: `static/index.html` `.login-box` rule; plan.md CSS contract block (line 87)
- **Detail**: plan.md's contract specifies `.login-box { max-width: 480px; }`. Shipped code uses `528px` (480 × 1.1), per a manual-review pivot already documented in `change.md`'s "Phase 1 design pivot #2" note ("box rendered too small... raised to 528px"). The pivot is well-documented in change.md but plan.md's own Contract code block still shows the stale `480px` value, so the plan and the change-notes disagree on what the final number is.
- **Fix**: Update plan.md's Contract block (line 87) to `max-width: 528px` to match what's actually in `static/index.html`, so the plan stops contradicting change.md and the live code.
- **Decision**: FIXED — plan.md line 87 updated to `max-width: 528px`

### F4 — Speculative `hasattr` branch in new `as_of` serialization is unreachable given current type guarantees

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: `src/api.py:~340` (`latest_date.isoformat() if hasattr(latest_date, "isoformat") else str(latest_date)`)
- **Detail**: All `_TREEMAP_WALLETS` snapshot sources return `datetime.date` for `snapshot_date` (BQ `DATE` column), so the `hasattr` fallback branch can't currently execute. Sibling endpoints in the same file let Pydantic/FastAPI's JSON encoder handle date serialization directly rather than hand-rolling `.isoformat()`/`str()` branching. Not a bug, just a defensive pattern not used elsewhere in this file.
- **Fix**: Optional cleanup — simplify to `latest_date.isoformat()` directly, since the type is statically known. Not blocking.
- **Decision**: SKIPPED — accepted as-is, low value vs. touching reviewed-safe code

### F5 — `dict[str, list[dict] | str | None]` type annotation is broader than the actual shape

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: `src/api.py:328`
- **Detail**: The annotation allows any key to be `list[dict] | str | None`, but in practice wallet keys are always `list[dict]` and only the `"as_of"` key is `str | None`. Minor readability nit, no runtime risk.
- **Fix**: Could use a `TypedDict` or just leave a comment; low priority, no action required.
- **Decision**: SKIPPED — accepted as-is, low value vs. touching reviewed-safe code

## Success Criteria Verification

**Automated** (from plan.md):
- `uv run pytest tests/e2e/test_login_ux.py` → PASS
- `uv run pytest tests/e2e/test_idle_timeout.py tests/e2e/test_url_routing.py tests/e2e/test_x_post_history.py tests/e2e/test_profile_menu.py` → PASS (25 passed)
- `uv run pytest tests/e2e/` (full suite, extended to `uv run pytest` full repo) → PASS (327 passed)

**Manual** (Progress section, Phase 1, items 1.4–1.8): all marked `[x]`, attributed to `b487532` — consistent with the commit's own description of manual verification performed during that session. No rubber-stamping concern; this is the only phase plan.md formally tracks.
