---
change_id: url-state-jwt-sessions
title: URL state persistence for JWT sessions — deep links no longer gated on apiKey
status: archived
created: 2026-07-19
updated: 2026-07-19
archived_at: 2026-07-19T11:57:04Z
tracking:
  linear: PUL-84
  github: 148
---

## Notes

`_writeUrl` and the `popstate` handler in `static/index.html` guard on `apiKey`, so URL
state (`?view=…`, pagination/filters) is neither written nor restored for JWT cookie
sessions — i.e. for every registered user after PUL-74, and for email admins after
PUL-83. Deep links, reload-restore, and back/forward work only for API-key sessions.

Scope (from Linear PUL-84):
- Replace the `apiKey` guards in `_writeUrl`/`popstate` (and any other URL-state
  read/write sites) with a "has an authenticated session" check covering both auth
  paths (e.g. a session flag set by `showDashboard`).
- E2E: deep-link restore + back/forward for an email-login session (admin and user),
  regression for the API-key path.
- Bonus: un-skip `test_calendar_url_contains_tab_calendar_after_switch`
  (tests/e2e/test_portfolio_calendar.py:110), skipped pending this change.

Out of scope: any auth/role changes (PUL-83 delivered those).

Origin: observation F3 in the PUL-83 impl-review (pre-existing gap from PUL-72;
functional, not a security issue — all data stays server-gated). Priority raised after
PUL-74 made JWT the only session type for regular users.
