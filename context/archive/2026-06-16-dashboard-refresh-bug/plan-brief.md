# Dashboard Refresh Bug — Plan Brief

> Full plan: `context/changes/dashboard-refresh-bug/plan.md`
> Frame brief: `context/changes/dashboard-refresh-bug/frame.md`

## What & Why

> The actual problem to plan around is: the dashboard's "resume session"
> bootstrap runs `showDashboard()` synchronously before the script reaches
> the `const` declarations (`_ADMIN_COLS`/`_USER_COLS`) it transitively
> depends on, throwing a `ReferenceError` on every reload of an
> already-authenticated session and aborting all subsequent listener
> registration (filters, date-toggle, pagination buttons).

This is a day-one latent JS load-order bug (Temporal Dead Zone violation),
not the date-parsing `RangeError` the original ticket (PUL-37 / GitHub #40)
assumed.

## Starting Point

`static/index.html:228` calls `showDashboard(role)` as the first executable
statement of the inline `<script>`, before `_ADMIN_COLS`/`_USER_COLS`
(`:314`, `:324`) are declared. This only fires when a session already
exists in `sessionStorage` — i.e. on refresh, never on a fresh login (which
calls `showDashboard` later, from a click handler, after the whole script
has run). `frame.md`'s live Playwright repro confirmed the exact browser
error and that zero `/announcements` requests fire post-refresh.

## Desired End State

Refreshing the dashboard (F5 or Ctrl+F5) with an existing session reloads
the table and headers correctly regardless of which filter was set
beforehand. No console `ReferenceError`. Filtering, pagination, and the
date-toggle keep working after refresh.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Root cause | TDZ ordering bug, not date RangeError | Live repro + JS async-exception semantics disproved the ticket's theory | Frame |
| Fix structure | Wrap bootstrap in `init()`, call as literal last statement of script | Eliminates the whole class of forward-reference bugs, not just the two known `const`s | Plan |
| Pagination-position-loss | Split into a separate future change | Real but distinct bug (frame.md); keeps this change focused on the reported symptom | Plan |
| Date-parsing hardening | Add minimal validate-before-construct guard (Phase 2) | Cheap, closes the ticket's original (if non-root-cause) concern | Plan |

## Scope

**In scope:**
- Reordering the bootstrap call to run after all top-level declarations
- A Playwright regression test reproducing the exact refresh failure
- A defensive date-parsing guard in `fetchAnnouncements`

**Out of scope:**
- Pagination position not restored on refresh (separate change)
- Any backend/`/announcements`/BigQuery changes
- Pre-existing `colspan` mismatches noticed in passing (cosmetic, unrelated)

## Architecture / Approach

Two independent, separately-committable fixes inside the single
`static/index.html` inline script: (1) move the session-resume bootstrap
into a named `init()` function called as the script's last statement, so
it can never run before a `const`/`let` it depends on is declared; (2)
replace the throwing `new Date(...).toISOString()` date-param construction
with a validate-first helper that returns `null` for unparseable input.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Fix script bootstrap load-order bug | `init()` restructure + regression e2e test (`tests/e2e/test_refresh.py`) | Moving the call must land as the literal last statement — misplacement reintroduces the bug |
| 2. Defensive date-filter parsing guard | `parseDateOrNull` helper + e2e check | Low risk — purely additive validation, no behavior change for valid dates |

**Prerequisites:** None — Playwright/pytest e2e infra already exists (`tests/e2e/conftest.py`).
**Estimated effort:** ~1 session, 2 phases.

## Open Risks & Assumptions

- Assumes no other script in the codebase relies on `showDashboard()`
  running synchronously at script-parse time (grep confirmed only the one
  call site).
- Assumes Playwright's role/locator queries against `<th>`/`<thead>`
  resolve as expected for the header-populated assertion; implementer may
  need to adjust the exact locator when writing the test.

## Success Criteria (Summary)

- `uv run pytest` (full suite, including new `tests/e2e/test_refresh.py`) passes
- Manual F5 and Ctrl+F5 refresh in real Chrome with an existing session:
  table renders, no console errors, filtering/date-toggle still work
- Manual check: invalid date input no longer risks an uncaught rejection
