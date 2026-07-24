# Per-item "new" badge clearing (PUL-94) — Plan Brief

> Full plan: `context/changes/announcement-seen-badges/plan.md`
> Research: `context/changes/announcement-seen-badges/research.md`

## What & Why

The "NOWE" badges on announcements are driven by a single per-view timestamp that advances the moment the view first renders — so badges reflect only the *previous* visit and clear all-at-once on the *next* one, ignoring what the user actually saw or clicked. PUL-94: an announcement should stop being "new" as soon as the user plausibly saw it — on popup open (per-item), on navigate-away, and on logout/tab close — in both Ogłoszenia and Obserwowane.

## Starting Point

`_seenThreshold` (`static/index.html:2137`) does read-and-advance in one shot at first render; one shared `renderTable` + one shared `openModal` serve both views; there are no page-close listeners and no per-item identifiers in user/my-wallet rows (admin-only `announcement_id` on `/announcements`). Zero test coverage of the badge today.

## Desired End State

Opening an announcement's popup clears its badge instantly and permanently (survives reloads). Leaving the view, logging out, closing or hiding the tab clears the view's badges for the next visit. First-ever visit still shows no badges. Both views, both roles, light+dark, no console errors, no new deps, no backend changes.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Seen-state model | Hybrid: threshold advanced on leave-events + per-item overlay for popup opens | Covers all 3 acceptance criteria with minimal code; prune falls out for free | Plan |
| Per-item key | Synthetic `ticker\|published_at` (client-side) | Zero backend changes; fields present in every row of both views; collision risk negligible | Research → Plan |
| User scoping | Browser-global (like `faro_theme`) | Consistent with existing keys; no identity plumbing; API-key sessions have no identity anyway | Research → Plan |
| Navigate-away semantics | Advance the whole view's threshold | Simplest, matches AC wording; consciously clears unvisited pagination pages too | Plan |
| Close/hide hook | `pagehide` + `visibilitychange:hidden` | Most reliable signal pair; synchronous localStorage needs no keepalive; tab-switch counting as "seen" accepted | Plan |
| E2E scope | 4 core scenarios (render, popup-clear+persist, navigate-away, logout) | Covers all ACs at reasonable cost; pagehide/visibilitychange verified manually | Plan |

## Scope

**In scope:** `static/index.html` mechanism rework (read-only threshold, `_markViewSeen` on leave/logout/pagehide/visibilitychange, `faro_seen_items` set + prune, badge condition, popup hook, `data-seen-key`); e2e fixtures + new 4-test spec.

**Out of scope:** API/backend changes; per-user scoping; per-row precision on navigate-away; forced re-render on tab return; X-posts table/modal; nav counters; dedicated pagehide e2e.

## Architecture / Approach

Badge = `published_at > viewThreshold && key ∉ seenItems`. Threshold advancement moves from first-render to leave events via one helper (`_leaveCurrentView`) called from both entry paths (`_navigateToView` and `_applyUrlState` — the popstate/deep-link bypass), plus `doLogout` and net-new `pagehide`/`visibilitychange` listeners. Per-item set lives in `faro_seen_items` (JSON, lazy Set, write-through), pruned against the older threshold + capped at 500.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Seen-state rework in `static/index.html` | Full new badge lifecycle, all hooks wired | Missing an entry/exit path (mitigated: single helper + lessons); stale in-memory threshold cache |
| 2. E2E coverage (4 scenarios) | Regression lock on all ACs + renderable-badge fixtures | Mutating shared conftest rows breaks existing specs (audit first, PUL-90 lesson) |

**Prerequisites:** none — branch `pul-94-announcement-seen-badges` is off current master.
**Estimated effort:** ~1-2 sessions across 2 phases.

## Open Risks & Assumptions

- `visibilitychange:hidden` fires on browser-tab switches — badges clear more aggressively than a literal reading of the ticket; accepted as "plausibly seen".
- E2E scenario 2 (reload persistence) depends on the if-absent guard in the init-script seeding — clobbering the threshold on re-navigation would invalidate the test.
- Assumes `currentView` string values map cleanly to the two seen-keys; implementer verifies exact router strings before wiring.

## Success Criteria (Summary)

- Popup open clears that announcement's badge immediately and across reloads.
- Navigate-away, logout, and tab close/hide clear the view's badges for the next visit.
- `uv run pytest` fully green including the new 4-test spec; prod behaves per ACs after deploy.
