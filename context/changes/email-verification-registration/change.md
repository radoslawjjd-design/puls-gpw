---
change_id: email-verification-registration
title: E-mail verification at registration — gate login on emailVerified
status: implementing
created: 2026-07-19
updated: 2026-07-20
archived_at: null
tracking:
  linear: PUL-86
  github: 154
---

## Notes

A freshly registered account is unusable until the user confirms the e-mail address.

Approach (variant A — decided in Linear PUL-86): Firebase-native — account created
immediately, but **login gated on `emailVerified`**:
- Register (`src/auth.py`): after `create_user`, send a verification e-mail; response
  no longer sets a session cookie — returns a "confirm your e-mail" state.
- Login: after `verify_password_rest`, check `emailVerified` (accounts:lookup) —
  unverified → 403 with a distinct error code + UI "Potwierdź e-mail" + resend button.
- `POST /api/auth/resend-verification` — rate-limited, enumeration-safe.
- SPA: post-register info screen, unverified-login state, resend flow.
- Tests: unit for the gate + resend; e2e with faked oobCode/lookup.

Out of scope (variant B rejected in ticket): pending-registrations table + custom
token flow.

Design note from PUL-85 (to settle in planning): verification e-mail should follow
the SAME branded-mail pattern as password reset — `generate_email_verification_link`
(Admin SDK, correct exception mapping to verify!) + `send_*` via own SMTP
(gpw.okiem.ai, From "Faro"), background-task send after existence checks. Also reuse
`_request_origin` + BackgroundTasks + enumeration lessons (silent 204, no
post-check-shaped responses). Firebase gotchas: [[project-firebase-auth-gotchas]]
(memory) — verify SDK exception paths against real Firebase, not mocks.

Human steps: Firebase console — verification template already PL (set during PUL-85).

Success criteria (from ticket): new registration cannot log in before clicking the
link; after clicking, login works; resend rate-limited + enumeration-safe; existing
verified accounts unaffected (owner admin keeps working). NOTE: existing PROD
accounts have emailVerified=false (owner: verified=False seen during PUL-85
diagnostics!) — the gate must not lock out pre-existing accounts; needs a decision
in planning (grandfather clause vs one-time verification).
