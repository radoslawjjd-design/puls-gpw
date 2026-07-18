---
change_id: admin-role-email-accounts
title: Admin role for email accounts — BQ role column + JWT claim + role-aware UI
status: archived
created: 2026-07-18
updated: 2026-07-18
archived_at: 2026-07-18T13:49:25Z
tracking:
  linear: PUL-83
  github: 146
---

## Notes

PUL-83: admin role for email accounts — add role column to BQ users (default user), read role at login and embed as JWT claim, _get_role returns payload role for JWT sessions (today hardcoded "user"), /api/auth/me returns role, UI uses it in boot probe + login/register success; one-time human-run BQ UPDATE to set owner's account to admin. Out of scope: role management UI, per-user data isolation (PUL-74). Tracking: linear PUL-83, github 146

Full spec: Linear PUL-83 (https://linear.app/puls-gpw/issue/PUL-83) / GitHub #146. Builds directly on PUL-71 (auth foundation) and PUL-72 (landing + auth UI, archived at context/archive/2026-07-18-login-register-landing/).

Security invariant: role comes ONLY from the server-signed JWT claim — client storage (sessionStorage.role) stays a UX hint; every admin-gated endpoint keeps server-side enforcement via _get_role/_require_admin. Sliding refresh must carry the role claim forward.
