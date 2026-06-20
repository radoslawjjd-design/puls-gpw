<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Portfolio treemap — main + IKZE side-by-side with portfolio-share %

- **Plan**: context/changes/portfolio-treemap-multi-wallet/plan.md
- **Scope**: Full plan (Phases 1-3). Phases 1 and 2 already have individual APPROVED phase reviews (`reviews/impl-review-phase-1.md`, `reviews/impl-review-phase-2.md`) with no outstanding decisions — this full review focuses new scrutiny on Phase 3 (commit `652d770`) and the epilogue (`2fd9cc7`), and re-confirms nothing in those commits broke Phase 1/2 assumptions.
- **Date**: 2026-06-21
- **Verdict**: NEEDS ATTENTION
- **Findings**: 0 critical, 3 warnings, 0 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING |
| Scope Discipline | WARNING |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | PASS |

## Test results

- `uv run pytest --tb=short` (full suite) — 266 passed, 1 failed (`tests/e2e/test_idle_timeout.py::test_stay_logged_in_keeps_session_alive_past_original_deadline`, the pre-existing flaky idle-timeout E2E tracked separately as PUL-49, unrelated to this change)
- `uv run pytest tests/e2e/test_portfolio_treemap.py -q` — 3/3 passed
- `node --test tests/test_treemap_layout.js` — 8/8 passed

## Findings

### F1 — Resize listener added in direct contradiction of an explicit plan guardrail

- **Severity**: ⚠️ WARNING
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding
- **Dimension**: Scope Discipline
- **Location**: static/index.html:966-975 (commit 652d770)
- **Detail**: The plan's "What We're NOT Doing" section states verbatim: "No resize listener — matches PUL-45's existing pattern; the view re-fetches and re-measures only when reopened." Phase 3's commit adds exactly that: a debounced `window.addEventListener('resize', ...)` that re-renders cached treemap data whenever the window resizes. No addendum to plan.md grants this exception — grepping the whole plan file for "resize"/"tooltip"/"600px" finds nothing beyond the original Manual Verification step, which still describes the old reopen-only behavior. The commit message frames it as a fix for truncation discovered during manual testing, but the plan was never updated to reflect the new decision. There is zero test coverage for this behavior anywhere in `tests/`.
- **Fix A ⭐ Recommended**: Keep the listener (it's a genuine UX improvement — without it, resizing across the 768px breakpoint leaves a stale, potentially overflowing layout until the view is reopened) but retroactively document it as a plan addendum, and add a regression test (e.g. an e2e/unit test that resizes the viewport and asserts the layout re-flows).
  - Strength: Preserves verified-working behavior; brings the plan back in sync with reality so future reviews don't re-flag this.
  - Tradeoff: Plan becomes a moving target relative to what was originally scoped and explicitly excluded.
  - Confidence: MED — the UX case for keeping it is reasonable, but it was an explicit, deliberate "NOT doing" decision at planning time, not an oversight; reversing that call deserves the user's own judgment, not just mine.
  - Blind spot: Don't know if the original "no resize listener" call was load-bearing for some other reason (e.g. avoiding a specific perf/jank concern) not visible in the plan text.
- **Fix B**: Remove the listener, matching the plan's explicit decision and PUL-45's precedent; accept that resizing across the breakpoint requires reopening the view (the originally-scoped tradeoff).
  - Strength: Restores exact plan compliance with no further discussion needed.
  - Tradeoff: Reintroduces the stale-layout-on-resize problem that motivated the addition in the first place; loses already-verified behavior.
  - Confidence: HIGH — mechanically simple, reverts cleanly.
  - Blind spot: None significant.
- **Decision**: FIXED via Fix A — addendum written to plan.md; regression test added (`tests/e2e/test_portfolio_treemap.py::test_resizing_window_reflows_treemap_layout_without_reopening_view`)

### F2 — Hover tooltip + ellipsis truncation + container height bump: undocumented scope additions, one directly contradicting a "NOT doing" item

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Scope Discipline
- **Location**: static/index.html:234-256 (CSS), :1006 (tooltip markup) — commit 652d770
- **Detail**: The plan's "What We're NOT Doing" section also states: "No d3/charting library changes, no tooltip/hover detail — same constraints as PUL-45." The Phase 3 commit adds a hover-triggered `.tc-tooltip` showing full uncut text, plus `text-overflow: ellipsis` truncation on cell labels and a `.treemap-container` min-height bump from 400px to 600px — none of which were in Phase 3's contract, which explicitly called the CSS item a no-op ("no rule change required, only verify visually"). Same as F1: no addendum, no test coverage.
- **Fix A ⭐ Recommended**: Document as a plan addendum alongside F1's resolution — these three changes are a coherent response to a real truncation problem found during the plan's own manual verification step, and are low-risk (pure CSS + a defensively-`esc()`'d tooltip, confirmed XSS-safe).
  - Strength: Keeps a verified, working truncation fix; low implementation risk already independently confirmed safe.
  - Tradeoff: Further widens the gap between "what the plan said" and "what shipped" if not written back.
  - Confidence: HIGH — these are presentational, not behavioral, and were exercised manually per Progress 3.4-3.7.
  - Blind spot: No automated test pins the tooltip content or the ellipsis behavior, so a future change could silently break it.
- **Fix B**: Revert to the plan's original no-tooltip, no-ellipsis, 400px-height state and accept that long ticker/value text may overflow small cells.
  - Strength: Exact plan compliance.
  - Tradeoff: Reintroduces the overflow bug the plan's own manual verification step would have caught anyway.
  - Confidence: MED — reverting is mechanical, but re-exposes a known UX bug.
  - Blind spot: None significant.
- **Decision**: FIXED via Fix A — covered by the same plan.md addendum written for F1

### F3 — Permanent global resize listener has no teardown, inconsistent with the file's own lifecycle convention

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: static/index.html:966-975
- **Detail**: `static/index.html` already has an established start/stop convention for global listeners — `startIdleTracking()`/`stopIdleTracking()` (static/index.html:392-414) pair `addEventListener`/`removeEventListener` and are invoked around login/logout. The new treemap `resize` listener is instead registered once at module load and lives for the page's entire lifetime with no corresponding teardown, breaking from that convention. Practical impact is low (no page reload between login/logout, so no detached-DOM leak), but it's an unscoped global side effect for a view-local feature.
- **Fix**: If F1 is resolved as "keep the listener," scope its registration to when the treemap view opens (e.g. inside the function that shows `treemap-view`) and remove it when the view is hidden/on logout, mirroring `startIdleTracking`/`stopIdleTracking`. If F1 is resolved as "remove the listener," this finding is moot.
- **Decision**: FIXED — added `startTreemapResizeTracking()`/`stopTreemapResizeTracking()` wrapping the resize listener; wired from `showTreemapView()` (start) and `showAnnouncementsView()`/`showXHistoryView()`/`doLogout()` (stop)

