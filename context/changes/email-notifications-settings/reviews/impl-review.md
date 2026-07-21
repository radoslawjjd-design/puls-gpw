<!-- IMPL-REVIEW-REPORT -->
# Implementation Review (Full Plan): Account settings + email-notifications opt-in (PUL-81 slice a)

- **Plan**: context/changes/email-notifications-settings/plan.md
- **Scope**: All 3 phases
- **Date**: 2026-07-21
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Evidence
- Phases 1 & 2 reviewed per-phase (APPROVED; see impl-review-phase-1.md / -phase-2.md). This full pass focuses on Phase 3 (frontend) + cross-phase integration.
- Safety: both `innerHTML` assignments in the settings view are static template literals with NO `${}` interpolation — no XSS surface. Authed fetch follows the inline `X-API-Key`/cookie pattern with 401→doLogout.
- Cross-phase contract: frontend sends `{enabled}`, reads `data.enabled`; backend body model `NotificationSettingsIn{enabled}` and returns the full settings dict — consistent. Email derived from the JWT claim server-side (Phase 2), never from the client body.
- Optimistic save: in-flight disable serializes toggles; failure reverts + inline error; forced-failure path covered by E2E route-intercept.
- Success criteria: every Progress row `[x]` with SHAs; full suite 663 passed; 5 E2E tests (default-off, persist-across-reload, hide-on-tab-switch, save-failure-revert, api-key-hidden).
- Scope discipline: no emails sent, no cron/dedup, no DELETE endpoint, no min_score UI — all "What We're NOT Doing" respected.

## Findings

### F1 — Plan under-specified the sibling-view hide (bug class, now fixed)

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: static/index.html — showAnnouncementsView / _showXHistoryViewDom / _showMyWalletViewDom / showPortfolioPositionsView
- **Detail**: The single-file SPA has no central "hide all views"; each show function hides siblings by explicit id. Phase 3 added `#settings-view` but the plan only had `_showSettingsViewDom` hide the others — it didn't call out updating the 4 existing show functions to hide the new view. Surfaced as the "settings lingers below other tabs" bug (caught in manual review). Fixed in all 4 functions + a regression E2E.
- **Fix**: Already fixed in `bb7249c` + regression-tested.
- **Decision**: FIXED (bb7249c) + ACCEPTED-AS-RULE — recorded in `context/foundation/lessons.md` ("SPA single-file: a new view must be hidden by ALL sibling show*View functions").
