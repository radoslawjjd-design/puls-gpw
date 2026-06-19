# Session Inactivity Timeout + Duration Display — Plan Brief

> Full plan: `context/changes/session-inactivity-timeout/plan.md`
> Frame brief: `context/changes/session-inactivity-timeout/frame.md`

## What & Why

Build a client-side idle-lock + idle-tab cost guard (exactly as scoped
technically: auto-logout after 30 min idle, 2-min warning, duration display)
while explicitly *not* presenting it as closing a session-security gap.
With the current shared, non-expiring API key model
(`src/api.py::_get_role` — plain string compare, no session table, no revoke
endpoint), no frontend-only change can revoke access. Real "session
security" requires server-side state and is blocked on PUL-28.

## Starting Point

`static/index.html` is a single-file panel: `apiKey`/`role` cached in
`sessionStorage` at login, cleared by a `logout-btn` click handler, with no
idle tracking, no session timestamp, and no warning UI today. Two existing
overlay patterns already exist to build on: a blocking modal (announcement
detail) and a non-blocking bottom banner (GDPR consent).

## Desired End State

After 30 minutes of no mouse/keyboard/scroll/touch activity, a warning modal
appears 2 minutes out with a live countdown; ignoring it logs the user out
and clears `sessionStorage`. Any activity, or clicking "stay logged in,"
resets the clock. The topbar shows how long the current login has lasted,
surviving page reloads. The PUL-32/#28 ticket text no longer implies this
revokes server-side access.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Security framing | Frontend timer is a UX/cost guard, not key revocation | API key is shared & non-expiring; no frontend change can revoke it | Frame |
| Warning UI | Blocking modal (reusing `.modal-overlay` pattern) | Impossible to miss — the whole point of warning before logout | Plan |
| Warning dismiss | "Stay logged in" counts as activity, resets clock | Matches natural mental model and existing GDPR-dismiss pattern | Plan |
| Multi-tab behavior | Tabs are independent | Matches existing per-tab `sessionStorage` session model, zero new sync code | Plan |
| Duration display | Persists across reload via `sessionStorage` timestamp | Accurate; consistent with the session itself already surviving reload | Plan |
| Limitation caveat placement | Ticket/plan docs only, no UI text | Fixes the misunderstanding where it matters; low-stakes internal panel | Plan |
| Idle check mechanism | Single `setInterval` comparing absolute timestamps | Self-corrects after backgrounded tabs/sleep; a decrementing counter would drift | Plan |

## Scope

**In scope:**
- Idle detection (mousemove/keydown/scroll/click/touchstart), warning modal
  with live countdown, auto-logout, shared `doLogout()` refactor
- "Zalogowano: X min" topbar indicator, persisted across reload
- Correcting PUL-32/#28 issue descriptions to drop the implied security claim

**Out of scope:**
- Any backend change, including a logout/revoke endpoint
- Cross-tab idle-state syncing
- Visible UI disclosure of the revocation limitation
- Making the idle timeout configurable via env var or UI

## Architecture / Approach

Single absolute-timestamp check loop (`setInterval` every 1s comparing
`Date.now() - lastActivityAt`) drives a 3-state machine (active → warning →
logged-out), started in `showDashboard()` and torn down in a new shared
`doLogout()`. The warning reuses existing modal CSS classes under a new
element ID so it can't collide with the announcement-detail modal's own
open/close logic.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Idle detection, warning, auto-logout | Core feature: warn → logout → clear storage | Timer drift on backgrounded tabs if implemented as a countdown instead of timestamp comparison |
| 2. Session duration display | "Zalogowano: X min" topbar indicator | Pre-existing sessions (no `loginAt` key) showing a broken value without a fallback |
| 3. Ticket/doc caveat | Corrected PUL-32/#28 description | None — docs-only |

**Prerequisites:** None — frontend-only, no backend or infra dependency.
**Estimated effort:** ~1 session, 3 phases (single file, no new dependencies).

## Open Risks & Assumptions

- Assumes the installed Playwright version (via `pytest-playwright>=0.6`)
  supports the `page.clock` time-travel API — needed to test a 30-minute
  timer without a 30-minute test. Verify at the start of Phase 1
  implementation; if unavailable, fall back to temporarily lowering the
  constants in a test-only code path (less ideal, not currently planned).

## Success Criteria (Summary)

- Idle tab auto-logs-out and clears storage after the configured timeout,
  with a working warning + reset path
- Duration indicator is accurate and survives reload
- PUL-32/#28 no longer implies this revokes server-side access
