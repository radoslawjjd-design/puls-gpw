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

2026-07-19 session findings (Phase 1 manual verification):
- Scope extended (user): branded reset e-mail (Faro logo, PL) via own SMTP +
  generate_password_reset_link — plan Phase 3. Firebase action page stays.
- Infra fix: PUL-71 rotation deleted the Firebase auto Browser key, but Identity
  Platform pins it in output-only `client.apiKey` → every action link 400-ed
  ("API key expired"). Fixed: undelete key 4e569a03 + identitytoolkit-only
  restriction. Web app "Faro web" created (fresh restricted key ...UEBXQA, not
  used by action links). PATCH of client.apiKey is silently ignored — key
  selection is not user-controllable.
- authorizedDomains = [localhost, puls-gpw.firebaseapp.com, puls-gpw.web.app] —
  Cloud Run domain missing; UNAUTHORIZED_DOMAIN → 503 confirmed for 127.0.0.1.
  Must be added before prod verification (2.5).
