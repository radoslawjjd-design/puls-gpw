---
change_id: spa-view-url-routing
title: Admin dashboard — per-view URLs and pagination in browser history
status: impl_reviewed
created: 2026-06-21
updated: 2026-06-22
archived_at: null
tracking:
  linear: PUL-52
  github: 79
---

## Notes

Surfaced during manual verification of PUL-51 (treemap labels/popup) and explicitly
deferred there as an app-wide, pre-existing gap — split out into its own change.

**Confirmed root cause (read `static/index.html` directly, not assumed):**

- `showTreemapView()` (~L884) and `showXHistoryView()` (~L869) never touch the URL
  at all — switching to either view leaves the URL exactly as it was.
- `fetchAnnouncements()` (~L894) does call `history.pushState`/`replaceState`, but
  only as a query string on the same `/` path (`?page=&page_size=`) — no per-view
  path/identifier.
- `fetchXPosts()` (~L935, X-post history pagination) has **no** History API
  integration at all — paging to page 2+ never changes the URL. This is the
  concrete bug the user hit ("nawet jak przejde na 2 strone to strona sie nie
  zmienia").
- The single `popstate` listener (~L602) only restores the announcements view —
  back/forward through treemap or X-history isn't handled, and initial page load
  never parses the URL to pick a starting view.

**Goal**: every view (announcements, treemap, x-history) gets its own URL, and
pagination within a view is part of that URL. Refresh, direct link, and
back/forward should all reproduce the exact view + page the user was on.

**Out of scope** (per Linear PUL-52): no backend routing changes — `api.py` can
keep serving the same `index.html` for any matched client path; this is a
client-side SPA history/URL concern. No new views or pagination features beyond
what already exists.
