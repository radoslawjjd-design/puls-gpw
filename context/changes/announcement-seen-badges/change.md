---
change_id: announcement-seen-badges
title: Per-item "new" badge clearing on open, navigate-away and logout/close
status: implemented
created: 2026-07-24
updated: 2026-07-24
archived_at: null
tracking:
  linear: PUL-94
  github: 185
---

## Notes

Per-item "new" badge clearing for announcements (Ogłoszenia + Obserwowane): clear on popup open, on navigate-away, and on logout/close. Tracking: Linear PUL-94, GitHub #185.

From Linear PUL-94:
- Current mechanism is a single coarse per-view timestamp (`faro_seen_<key>` in localStorage; `_seenThreshold` — `static/index.html:2130-2140`); badges clear all-at-once on the *next* visit.
- Desired: (1) open popup → that item stops being new (persists across reloads); (2) navigate-away → shown items stop being new; (3) logout/close without clicking → also stop being new.
- Decide in planning: per-item seen-set vs. timestamp advanced on leave; where to hook logout/close (`pagehide`/`visibilitychange`/`beforeunload`).
- Acceptance: works in both Ogłoszenia and Obserwowane; light + dark; no console errors; no new deps.
