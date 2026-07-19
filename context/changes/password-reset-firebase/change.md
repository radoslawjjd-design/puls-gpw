---
change_id: password-reset-firebase
title: Password reset via Firebase e-mail flow
status: implementing
created: 2026-07-19
updated: 2026-07-19
archived_at: null
tracking:
  linear: PUL-85
  github: 153
---

## Notes

Users who forget their password can reset it themselves from the login screen.

Scope (from Linear PUL-85):
- `POST /api/auth/reset-password` — Identity Toolkit `accounts:sendOobCode` with
  `requestType: PASSWORD_RESET` (same REST family as `verify_password_rest` in
  `src/auth.py`); rate-limited like login/register; always 204 regardless of whether
  the e-mail exists (no account enumeration).
- Login panel: "Nie pamiętasz hasła?" link → minimal e-mail form → confirmation state
  ("sprawdź skrzynkę").
- Firebase sends the e-mail and hosts the default reset-password action page — no
  custom SMTP, no custom action handler.
- Tests: unit (endpoint + rate limit + enumeration-safe response), e2e (form flow with
  faked sendOobCode).

Human steps: review/adjust the e-mail template and sender in the Firebase console.

Success criteria: reset e-mail works for an existing account and the new password logs
in; unknown e-mail gets the same 204; endpoint rate-limited.

Pair note: PUL-86 (e-mail verification at registration, GH #154) builds in the same
files (src/auth.py + login panel) — planned as the immediate follow-up change.
