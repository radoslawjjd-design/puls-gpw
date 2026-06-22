<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Admin Dashboard — Per-View URLs and Pagination in Browser History

- **Plan**: context/changes/spa-view-url-routing/plan.md
- **Scope**: Phase 3 of 4
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

### F1 — Double-click on xp-btn-prev/next can write a stale URL

- **Severity**: ⚠️ WARNING
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding
- **Dimension**: Safety & Quality
- **Location**: static/index.html:735-741, 1055-1080
- **Detail**: `xp-btn-prev`/`xp-btn-next` don't disable themselves before calling `fetchXPosts()`; disabling happens inside `fetchXPosts()` after `_xPostsParams()` already snapshotted `xpPage`. A fast double-click fires two overlapping fetches with different `xpPage` snapshots; if responses resolve out of order, the later-resolving response's `_writeUrl()` call wins, leaving the visible URL out of sync with the rendered table. Pre-existing pattern (same race already existed for announcements' `btn-prev`/`btn-next`), not introduced by Phase 3 — but Phase 3 extends the consequence from "stale table" to "stale URL".
- **Fix A ⭐ Recommended**: Accept as pre-existing, record as a lesson/follow-up
  - Strength: The plan's "What We're NOT Doing" never scoped request cancellation; a proper fix (AbortController/request-id guard) touches both fetchAnnouncements() and fetchXPosts() symmetrically, bigger than a phase-3-only fix.
  - Tradeoff: The bug stays live in production a while longer.
  - Confidence: HIGH — same pattern, same risk window already existed pre-Phase-3.
  - Blind spot: Haven't measured real-world double-click frequency.
- **Fix B**: Disable both buttons synchronously in the click handlers before calling fetchXPosts(), mirrored into announcements handlers
  - Strength: Closes the window for both views in one pass; cheap.
  - Tradeoff: Doesn't fully fix it (network can still reorder in-flight requests); expands phase diff into the announcements view.
  - Confidence: MEDIUM — reduces but doesn't eliminate the race.
  - Blind spot: True fix needs request sequencing, not just earlier disabling.
- **Decision**: ACCEPTED-AS-RULE — saved to context/foundation/lessons.md ("SPA pagination — out-of-order fetch responses can desync the URL"); no code change applied (user chose lesson-only).

### F2 — showXHistoryView() always fetches, showAnnouncementsView() never does

- **Severity**: 📋 OBSERVATION
- **Dimension**: Pattern Consistency
- **Location**: static/index.html:880-883 vs :854-861
- **Detail**: Intentional per the plan's "Critical Implementation Details" sequencing rule, not a deviation. Flagged only as a foot-gun for future call sites: any new caller of `showAnnouncementsView()` must remember to pair it with `fetchAnnouncements()` itself, since the view function won't do it. Both current call sites (:827-828, :907-908) already do this correctly.
- **Decision**: DISMISSED — informational only, no action needed.
