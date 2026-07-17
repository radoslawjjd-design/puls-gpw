---
change_id: login-register-landing
title: Onboarding landing page + email/password login and registration (PUL-72)
status: preparing
created: 2026-07-18
updated: 2026-07-18
archived_at: null
tracking:
  linear: PUL-72
  github: 128
---

## Notes

PUL-72: replace bare API-key login with onboarding landing page (hero + 3 top-score announcement cards from new public endpoint GET /api/public/top-announcements, 60s cache) + email/password register & login forms calling PUL-71 auth endpoints; "Mam klucz API" secondary path stays; out of scope: guest mode (PUL-73), data isolation (PUL-74). Tracking: linear PUL-72, github 128

Full spec: Linear PUL-72 (https://linear.app/puls-gpw/issue/PUL-72) / GitHub #128. Prerequisite PUL-71 (auth foundation: Firebase Auth, JWT session, user model) is merged and deployed (#133).

Session context (2026-07-18): the UI was just replaced by faro-v2 (PR #135, `static/index.html`) — the ticket's references to the "current login form" predate that swap; research must reconcile the spec against the faro-v2 login flow (`doLogout`, `showLogin`, role handling) and against the auth endpoints actually delivered by PUL-71 (`src/auth*`, router mounted in `create_app`).
