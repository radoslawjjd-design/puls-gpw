# Session Inactivity Timeout + Duration Display — Implementation Plan

## Overview

Add a client-side idle-lock to the panel: after `SESSION_IDLE_MINUTES` (default
10 — lowered from the originally planned 30 per explicit request, 2026-06-19)
of no mouse/keyboard/scroll/touch activity, show a 2-minute warning modal,
then auto-clear the cached API key and return to the login screen. Also add a
"Zalogowano: X min" indicator in the topbar. Frontend-only — `static/index.html`
is the only file touched.

Per `context/changes/session-inactivity-timeout/frame.md`, this is a UX/cost
guard (protects an unattended open tab, stops idle-tab polling per PUL-30) —
**not** a real access-revocation mechanism. The panel's `X-API-Key` auth
(`src/api.py::_get_role`) is a shared, non-expiring secret validated by plain
string comparison; no frontend change can revoke it. That correction is
captured as Phase 3 (ticket/doc text), not a code change.

## Current State Analysis

- `static/index.html` is a single-file HTML+CSS+vanilla-JS app served by
  FastAPI. No build step, no framework, no JS test runner — verification is
  via Playwright e2e (`tests/e2e/*.py`).
- Auth state (`apiKey`, `role`) lives in `sessionStorage`, set at login
  (`index.html:409-410`) and cleared at logout (`index.html:420-424`). There is
  no session timestamp tracked anywhere today.
- `logout-btn` handler (`index.html:420-424`) is the only place that currently
  clears storage and returns to the login screen — duplicated nowhere else, so
  this is the natural seam to factor into a shared `doLogout()`.
- `showDashboard(r)` (`index.html:473-481`) and `showLogin()`
  (`index.html:463-467`) are the only two screen-transition functions; idle
  tracking must start/stop from exactly these two hooks to avoid leaking
  timers across login/logout cycles within one page session.
- An existing blocking modal pattern (`#modal-overlay` / `.modal-overlay` /
  `.modal-box`, `index.html:271-280`, opened via `openModal()` /
  `closeModal()` at `index.html:621-660`) is used for announcement detail. It
  has its own Escape-key and click-outside-to-close wiring already bound to
  `#modal-overlay` — the idle warning needs visually identical styling but a
  **separate** overlay element/ID so it can't collide with that existing
  open/close state machine, and so it can be shown even while an announcement
  detail modal happens to be open.
- A non-blocking banner pattern (`#gdpr-banner`, `index.html:283-286`,
  `initGdpr()` at `index.html:335-343`) exists for the GDPR notice — not used
  here per the "blocking modal" decision, but its dismiss-on-click wiring is a
  useful reference for the warning's "stay logged in" button.

### Key Discoveries:

- `index.html:528-531` / `index.html:375-377` — both `fetchAnnouncements()`
  and `loadAutocomplete()` already redirect to login on a `401`, confirming
  the codebase's existing convention: any auth-loss path always goes through
  `sessionStorage.clear()` + `showLogin()`. The idle-timeout path should reuse
  the same end state via the new shared `doLogout()` — and these two existing
  call sites must be migrated to call `doLogout()` too (Phase 1), otherwise a
  401 leaves the idle interval/listeners running under the login screen and
  the warning overlay can later pop up over it.
- Playwright e2e tests (`tests/e2e/conftest.py`, `tests/e2e/test_login_ux.py`,
  `tests/e2e/test_gdpr.py`) use `pytest-playwright>=0.6` against a real
  `uvicorn` server (`live_server_url` fixture). `pytest-playwright`'s
  underlying Playwright version supports `page.clock` (time-travel: install,
  then `fast_forward`/`run_for`), which is the only practical way to test a
  30-minute timer without a 30-minute test.
- `tests/e2e/test_gdpr.py:5` shows the `@pytest.mark.gdpr` convention for
  opting a test out of the `_accept_gdpr` autouse fixture
  (`conftest.py:31-36`) — the new idle-timeout tests don't need this, they run
  with GDPR pre-accepted like the rest of the suite.

## Desired End State

A user who stops interacting with the dashboard for `SESSION_IDLE_MINUTES`
(default 10 — lowered from the originally planned 30 per explicit request,
2026-06-19) sees a warning modal at the 2-minute mark, and — if they don't
interact with it — is logged out and returned to the login screen with
`sessionStorage` cleared. Any mouse/keyboard/scroll/touch activity, or
clicking "stay logged in" on the warning, resets the clock. The topbar shows
how long the current login has lasted, and that figure survives a page
reload. The PUL-32/`#28` ticket description no longer implies this revokes
server-side access.

### Verification

```bash
uv run pytest tests/e2e/test_idle_timeout.py -v
```
Manual: log in, leave the tab idle past the warning threshold, confirm the
modal appears with a live countdown, click "Zostań zalogowany" and confirm
the dashboard stays usable; in a second run, let it run out and confirm
redirect to login + `sessionStorage` is empty (devtools).

## What We're NOT Doing

- Not revoking the API key server-side, and not adding a logout/revoke
  endpoint. That requires per-user sessions (PUL-28) and is out of scope here
  — confirmed by the frame brief.
- Not syncing idle state across multiple browser tabs — each tab tracks its
  own activity independently (per decision).
- Not adding any visible UI text disclosing the revocation limitation — the
  caveat is corrected in the ticket/plan text only (per decision).
- Not making `SESSION_IDLE_MINUTES` configurable via env var, query param, or
  UI — it's a hardcoded JS constant, matching change.md's "configurable via
  constant" framing.
- No backend changes of any kind.

## Implementation Approach

Single absolute-timestamp check loop, not a chain of `setTimeout`s. A
`lastActivityAt` variable is bumped (cheaply) by activity listeners; one
`setInterval` ticking every second compares `Date.now() - lastActivityAt`
against two thresholds (warn, logout) and drives a small state machine:
`active → warning → logged-out`. Reusing the existing `.modal-overlay`/
`.modal-box` CSS classes (new element IDs) keeps the warning visually
consistent without touching the announcement-detail modal's own JS.

## Critical Implementation Details

**Timing & lifecycle**: Compare against absolute timestamps (`Date.now() -
lastActivityAt`) on every tick, never decrement a counter. Background tabs
get their timers throttled by the browser and laptops sleep — a counter-based
countdown would drift or stall, while an absolute-timestamp comparison
self-corrects the instant the tab/tick resumes. Start the activity listeners
and the interval inside `showDashboard()` (covers both fresh login and
reload-with-existing-session via `init()`), and tear both down inside the new
`doLogout()` — otherwise a stray interval keeps running after logout and can
fire a redundant logout call, or leak into the next login.

**State sequencing**: On idle timeout, the order is: stop the interval and
remove activity listeners → close the announcement-detail modal if open
(`closeModal()`) → run the same clear-storage-and-redirect logic as the manual
logout button. Tearing down listeners first prevents any in-flight activity
event from resurrecting a timer right as logout happens.

## Phase 1: Idle detection, warning modal, auto-logout

### Overview

Core feature: detect inactivity, warn at -2 minutes, log out at 0, all
reusing the existing logout exit path.

### Changes Required:

#### 1. Shared logout function

**File**: `static/index.html`

**Intent**: Factor the existing `logout-btn` handler body into a standalone
`doLogout()` function so both the manual button and the idle-timeout path
call the same code — same as the existing `401` handling already does
ad-hoc in `fetchAnnouncements()`/`loadAutocomplete()`, but now centralized.
Also replace the two ad-hoc `sessionStorage.clear(); showLogin();` blocks in
`fetchAnnouncements()` (`index.html:531`) and `loadAutocomplete()`
(`index.html:375-377`) with calls to `doLogout()`, so a 401 tears down the
idle interval/listeners the same way a manual or idle-triggered logout does.

**Contract**: `doLogout()` clears `sessionStorage` (including the Phase 2
`loginAt` key), resets the module-level `apiKey`/`role` variables to `null`
(matching what the existing `logout-btn` handler already does — these are
read directly by `fetchAnnouncements()`, `loadAutocomplete()`, the delete
handler, and the `popstate` listener's `if (!apiKey) return;` guard, so
leaving them stale would let an authenticated fetch fire after logout),
closes the announcement-detail modal if open, stops the idle
interval/listeners, and calls `showLogin()`. `logout-btn`'s click handler and
the idle-timeout state machine both call `doLogout()` instead of duplicating
its body.

#### 2. Idle tracking state machine

**File**: `static/index.html`

**Intent**: Track activity, show/hide the warning, and trigger `doLogout()`
when idle time exceeds `SESSION_IDLE_MINUTES`.

**Contract**: Two constants near the top of the `<script>` block:
`SESSION_IDLE_MINUTES = 10` (lowered from the originally planned 30 per
explicit request, 2026-06-19), `SESSION_WARNING_MINUTES = 2`. A module-level
`lastActivityAt` timestamp updated by listeners on `mousemove`, `keydown`,
`scroll`, `click`, `touchstart` (all on `window`/`document`, passive where
applicable). A single `setInterval(checkIdle, 1000)` started in
`showDashboard()` and cleared in `doLogout()`. `checkIdle()` computes
`idleMs = Date.now() - lastActivityAt` and: shows the warning overlay once
`idleMs >= (SESSION_IDLE_MINUTES - SESSION_WARNING_MINUTES) * 60000` (updating
a live `mm:ss` countdown each tick), hides it again if activity resumes below
that threshold, and calls `doLogout()` once
`idleMs >= SESSION_IDLE_MINUTES * 60000`.

#### 3. Warning modal markup + styling

**File**: `static/index.html`

**Intent**: A blocking modal, visually matching the existing `.modal-box`
pattern, shown by the idle state machine.

**Contract**: New `#idle-warning-overlay` element (separate from
`#modal-overlay`) reusing the `.modal-overlay`/`.modal-box` CSS classes,
containing the fixed message "Zostaniesz wylogowany za 2 minuty z powodu
braku aktywności", a live countdown span, and a "Zostań zalogowany" button.
The button's click handler resets `lastActivityAt = Date.now()` and hides the
overlay (counts as activity, per decision) — no Escape/click-outside dismiss,
since accidentally dismissing this one should require a deliberate click.

### Success Criteria:

#### Automated Verification:

- `uv run pytest tests/e2e/test_idle_timeout.py -v` passes
- `uv run pytest tests/e2e/` (full suite) passes — no regressions in
      login/logout/GDPR/pagination/autocomplete tests

#### Manual Verification:

- Leaving the dashboard idle past the warning threshold shows the modal
      with a live countdown
- Clicking "Zostań zalogowany" hides the modal and keeps the session
      alive past the original deadline
- Any mouse/keyboard/scroll/touch activity before the warning threshold
      prevents the warning from appearing
- Letting the countdown run out clears `sessionStorage` (verify in
      devtools) and shows the login screen
- Manually clicking "Wyloguj" still works identically to before

**Implementation Note**: Pause here for manual confirmation before Phase 2.

---

## Phase 2: Session duration display

**Status: Dropped — see Progress.** After seeing Phase 1 working, the user
decided the duration indicator has no practical value (it was always
"Optional/nice-to-have" per `change.md`) and doesn't fit the topbar visually.
No code for this phase ships; the change closes with Phase 1 + Phase 3.

### Overview

Add a "Zalogowano: X min" indicator in the topbar, backed by a persisted
login timestamp.

### Changes Required:

#### 1. Persist and display login timestamp

**File**: `static/index.html`

**Intent**: Record when the current session started, and show elapsed
minutes in the topbar, surviving page reload (per decision).

**Contract**: On successful login (`index.html:409-410` block) and in
`init()` when an existing `apiKey`/`role` pair is found but no `loginAt` key
exists yet (pre-existing sessions from before this feature shipped), set
`sessionStorage.setItem('loginAt', Date.now())`. Add a `#session-duration`
span next to `#role-badge` in the topbar, updated by a `setInterval` (e.g.
every 30s) computing `Math.floor((Date.now() - loginAt) / 60000)` and
rendering "Zalogowano: X min". `doLogout()` removes the `loginAt` key
alongside `apiKey`/`role`.

### Success Criteria:

#### Automated Verification:

- `uv run pytest tests/e2e/test_idle_timeout.py -v` passes (duration
      display cases included in the same file)

#### Manual Verification:

- The topbar shows an increasing "Zalogowano: X min" value while logged
      in
- Reloading the page preserves the elapsed value rather than resetting to
      0
- Logging out and back in resets the counter to 0 for the new session

**Implementation Note**: Pause here for manual confirmation before Phase 3.

---

## Phase 3: Ticket/plan documentation caveat

### Overview

Correct the "session security" framing in the tracked issues, per the frame
brief's finding that this feature cannot revoke server-side access.

### Changes Required:

#### 1. Update tracked issue descriptions

**File**: Linear `PUL-32`, GitHub `#28` (via `mcp__linear-server__save_issue`
and `gh issue edit`)

**Intent**: State plainly, in the issue description, that this feature is a
client-side idle-lock and idle-tab cost guard, and explicitly does **not**
revoke the underlying `ADMIN_API_KEY`/`USER_API_KEY` — real key revocation
requires server-side sessions (PUL-28).

**Contract**: Append a short "Limitations" note to both issue descriptions
(not a UI change). No code changes in this step.

### Success Criteria:

#### Automated Verification:

- None (documentation-only step)

#### Manual Verification:

- PUL-32 description includes the limitation note
- GitHub issue #28 description includes the limitation note

---

## Testing Strategy

### Integration (e2e) Tests:

New file `tests/e2e/test_idle_timeout.py`, following the existing
`page`/`live_server_url` fixture pattern:

- Warning appears at `SESSION_IDLE_MINUTES - SESSION_WARNING_MINUTES` of
  simulated idle time (via `page.clock.install()` + `fast_forward`)
- Clicking "Zostań zalogowany" prevents logout at the original deadline
- Simulated activity (dispatching a `mousemove`/`keydown` via
  `page.evaluate` or `page.mouse.move`) before the warning threshold prevents
  the warning
- Full idle duration triggers logout: login screen visible, `sessionStorage`
  empty (checked via `page.evaluate("() => sessionStorage.length")`)
- Duration display increments over simulated time, and survives
  `page.reload()` (value after reload is not lower than before)

### Manual Testing Steps:

1. Log in, leave idle past warning threshold, confirm modal + countdown
2. Click "stay logged in", confirm session continues
3. Let a second session idle all the way out, confirm redirect + cleared
   storage
4. Reload mid-session, confirm duration display continues rather than
   resetting

## Performance Considerations

A single 1-second `setInterval` per dashboard session is negligible. Activity
listeners only write a timestamp variable — no per-event work beyond that, so
high-frequency `mousemove` firing has no measurable cost.

## Migration Notes

Sessions already active in a browser tab when this ships (an existing
`apiKey`/`role` in `sessionStorage` with no `loginAt` key) are handled by the
`init()` fallback in Phase 2, which back-fills `loginAt = Date.now()` rather
than showing a broken/negative duration.

## References

- Frame brief: `context/changes/session-inactivity-timeout/frame.md`
- `static/index.html:393-481` — screen-transition functions and current auth
  state handling
- `src/api.py::_get_role` — confirms why this is a UX/cost guard, not access
  revocation
- `tests/e2e/conftest.py`, `tests/e2e/test_gdpr.py`, `tests/e2e/test_login_ux.py`
  — existing Playwright test conventions

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Idle detection, warning modal, auto-logout

#### Automated

- [x] 1.1 `uv run pytest tests/e2e/test_idle_timeout.py -v` passes — fa50366
- [x] 1.2 Full e2e suite passes, no regressions — fa50366

#### Manual

- [x] 1.3 Warning modal with live countdown appears at threshold
- [x] 1.4 "Zostań zalogowany" keeps session alive past original deadline
- [x] 1.5 Activity before threshold prevents warning
- [x] 1.6 Full idle clears sessionStorage and shows login screen
- [x] 1.7 Manual "Wyloguj" still works

### Phase 2: Session duration display — Dropped 2026-06-19, not needed (no code shipped)

#### Automated

- [ ] 2.1 `uv run pytest tests/e2e/test_idle_timeout.py -v` passes (duration cases)

#### Manual

- [ ] 2.2 Topbar shows increasing "Zalogowano: X min"
- [ ] 2.3 Reload preserves elapsed value
- [ ] 2.4 Re-login resets counter to 0

### Phase 3: Ticket/plan documentation caveat

#### Manual

- [x] 3.1 PUL-32 description includes limitation note — f828c33
- [x] 3.2 GitHub issue #28 description includes limitation note — f828c33
