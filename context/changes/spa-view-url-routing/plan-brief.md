# Admin Dashboard: Per-View URLs and Pagination — Plan Brief

> Full plan: `context/changes/spa-view-url-routing/plan.md`

## What & Why

The admin panel's three views (announcements, portfolio treemap, X-post
history) and their pagination don't show up in the browser URL. Switching
views or paging through x-history never changes the address bar, so refresh,
copy-link, and back/forward all silently dump the user back to announcements
page 1. Surfaced during PUL-51 manual testing and split into its own ticket
(PUL-52 / GitHub #79).

## Starting Point

`static/index.html` (vanilla JS, no framework) already has a *partial*
History API integration for announcements pagination (`?page=&page_size=`
via `pushState`, from PUL-23) — but no filters, no treemap/x-history
awareness, and `fetchXPosts()` (x-history pagination) has zero History API
code at all. `src/api.py` has only one route (`GET /`) and needs no changes.

## Desired End State

Every view switch is a back-navigable step. The URL always encodes the
active view + its pagination + (for announcements/x-history) its filters.
Refresh, deep link, and back/forward all reproduce exactly what was on
screen. Modals (treemap popup, x-post detail) stay outside routing.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) |
| --- | --- | --- |
| URL scheme | Query param (`?view=treemap`) | Zero backend changes — `GET /` already ignores query params; a path-based scheme (`/treemap`) would need a new catch-all route. |
| Filter scope | Full filter state in URL, both announcements and x-history | User chose full shareable/reproducible links over the minimal page-only scope. |
| Default view | No `view=` param → announcements | Matches today's behavior exactly; old bookmarks (`?page=2`) keep working with zero migration. |
| View-switch history | Every view switch pushes a new entry | Makes back/forward between views actually work, which is the whole point of this change. |
| Modals & URL | Modals never touch history | Preserves the PUL-51 invariant (closing the popup must not navigate); avoids reintroducing that exact bug class. |
| Testing | Extend existing E2E suites + one new file | Routing bugs are real-browser-history bugs — Playwright is the only thing that actually exercises `popstate`. |

## Scope

**In scope:** view-level URLs for all three views; full filter+page state in
URL for announcements and x-history; popstate/init restore; old-bookmark
compatibility; E2E coverage for all of the above.

**Out of scope:** backend routing changes; new views or new treemap
pagination; history entries for modal open/close; redirect/migration logic
for old-format URLs (not needed — they already resolve correctly).

## Architecture / Approach

Two shared param-builders (`_announcementsParams()`, `_xPostsParams()`) feed
both the existing API fetch calls *and* the new URL writer, so the visible
URL and the actual API query can never drift apart. A single
`_navigateToView()` centralizes view switching, and a single
`_applyUrlState()` (used by both `init()` and `popstate`) is the one place
that parses `location.search` and restores view + filters + page — one
code path instead of two that could diverge.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Routing core | `view=` param for treemap/x-history, centralized nav, view-aware init/popstate | Double-pushState if view-switch and fetch both write the URL on the same click |
| 2. Announcements full state | Filters + page in URL, restore on refresh/deep-link | Date round-trip must use local time, not UTC slice, or restored dates shift |
| 3. X-history full state | Same treatment, from zero — this is the exact bug reported | Filter form is built lazily; restore must run after `showXHistoryView()`, not before |
| 4. Polish + tests | Modal-isolation regression guard, new `test_url_routing.py`, extended existing E2E files | Existing `test_refresh.py` test doesn't actually assert the old "reset to page 1" behavior — easy to assume it's covered when it isn't |

**Prerequisites:** none — builds directly on the existing partial
announcements History API integration.
**Estimated effort:** ~4 phases, single file (`static/index.html`) plus test
files — one focused session per phase is a reasonable size.

## Open Risks & Assumptions

- Assumes no other code outside `static/index.html` reads/writes
  `location.search` or `history.state` — not verified beyond grep in this
  plan's research; worth a quick re-check at Phase 1 start if anything
  surprises.
- `_isoToLocalInputValue()` (Critical Implementation Details in the full
  plan) is the one genuinely fiddly piece — gets its own explicit manual
  verification step in Phase 2 rather than being assumed correct from code
  review alone.

## Success Criteria (Summary)

- Switching views, paging, filtering, refreshing, and using back/forward all
  keep the URL and the on-screen state in sync, for all three views.
- An old-format bookmark (`?page=2&page_size=20`) still works exactly as
  before.
- Opening/closing modals never changes the URL or consumes a back-press.
