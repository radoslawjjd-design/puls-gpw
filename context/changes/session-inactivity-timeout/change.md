---
change_id: session-inactivity-timeout
title: Session security - inactivity timeout + session duration display
status: implementing
created: 2026-06-19
updated: 2026-06-19
archived_at: null
tracking:
  linear: PUL-32
  github: 28
---

## Notes

Frontend-only panel feature (no backend changes):

- Auto-logout after N minutes of inactivity (no mouse/keyboard/scroll), default `SESSION_IDLE_MINUTES = 30`, configurable via constant.
- Dismissible warning ~2 min before expiry ("Zostaniesz wylogowany za 2 minuty z powodu braku aktywności").
- On timeout: clear API key from sessionStorage/localStorage, redirect to login screen.
- Optional/nice-to-have: "Zalogowano: X min" indicator in panel header, resets on login, counts up until logout.

Related:
- PUL-28 (user profile & auth tiers) — if server-side sessions land there, this ticket should be revisited to also invalidate the token server-side on timeout.
- PUL-30 (cost optimisation) — idle-tab polling would be the main cost driver this guards against.
- PUL-25 (panel UI/UX redesign) — session duration display lives in the header.
