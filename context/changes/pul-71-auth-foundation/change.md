---
change_id: pul-71-auth-foundation
title: Auth foundation — Firebase Auth, JWT session, user model
status: plan_reviewed
created: 2026-07-17
updated: 2026-07-17
archived_at: null
tracking:
  linear: PUL-71
  github: 127
---

## Notes

Auth foundation: Firebase Auth + JWT session + user model (Linear PUL-71, GitHub #127)

Pierwszy pod-ticket epiku PUL-70 (rejestracja userów, izolacja danych per-user, landing). Kolejne w kolejności: PUL-72 (login/landing UI) → PUL-73 (guest mode) → PUL-74 (izolacja danych).

Zakres wg ticketa: Firebase Auth (Email/Password) do haseł, JWT w HttpOnly cookie (HS256, `JWT_SECRET`, 7 dni), tabela BQ `users`, endpointy `/api/auth/{register,login,logout,me}`, walidacja hasła 8–128 znaków (litera+cyfra) → 422, rate limiting (register 5/IP/min, login 10/IP/min → 429 z Retry-After), middleware rozszerzony o JWT cookie — istniejący X-API-Key działa bez zmian.

Prereq zrobiony 2026-07-17 (poza pipeline'em, ręcznie+CLI): Firebase podpięty do projektu GCP puls-gpw (Blaze), provider Email/Password włączony (subtype FIREBASE_AUTH, bez upgrade'u do Identity Platform), service account `firebase-adminsdk-fbsvc@puls-gpw.iam.gserviceaccount.com` (roles: firebase.sdkAdminServiceAgent, firebaseauth.admin), klucz JSON poza repo: `C:\Users\PC KOMPUTER\.secrets\puls-gpw-firebase-adminsdk.json` → env var `FIREBASE_SERVICE_ACCOUNT_JSON`. Produkcja: klucz do Secret Managera + wpięcie do puls-gpw-api (do ustalenia w planie).

Branch: `pul-71-auth-foundation`.
