# E-mail Verification at Registration — Plan Brief

> Full plan: `context/changes/email-verification-registration/plan.md`

## What & Why

A freshly registered account is currently usable immediately — anyone can register with an
arbitrary e-mail and get a session. We gate login on Firebase's `emailVerified`: registration
creates the account but issues no session and sends a branded verification e-mail; login
rejects unverified accounts until the link is clicked (variant A, decided in Linear PUL-86).

## Starting Point

PUL-85 (password reset) just landed the entire pattern this reuses: branded Faro mail via own
SMTP, `_request_origin`, enumeration-safe silent-204 flow with `get_user_by_email` pre-check,
background-task send (impl-review F1), per-IP rate limiting, and matching unit/E2E test
scaffolding. Today `register` auto-logs-in (`_session_response`) and `login` never reads
`emailVerified`. All existing prod accounts have `emailVerified=false` — including the owner.

## Desired End State

A new registration lands on a "sprawdź skrzynkę" screen; logging in before clicking the link
shows "Potwierdź e-mail" with a resend button; after clicking, login works normally. Existing
accounts are untouched (backfilled to verified before deploy). Resend is rate-limited and
leaks no account-existence signal.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Overall approach | Variant A — Firebase-native gate on `emailVerified` | Account exists immediately; no pending-registrations table or custom token flow. | Ticket (Linear) |
| Existing prod accounts | One-time backfill `emailVerified=True` before deploy | Keeps the login gate a single clean condition forever; ~25 accounts, human-run idempotent script. | Plan |
| Post-register UX | Confirmation screen + resend (clone of `#reset-confirmation`) | Pattern already exists in the SPA; user knows what to do next. | Plan |
| Unverified login UX | 403 → message + explicit resend button | User controls mail sending; no auto-mail on every login attempt. | Plan |
| Resend semantics | Silent 204 for unknown AND already-verified accounts | Exact PUL-85 anti-enumeration pattern; no redundant mail paths. | Plan |
| Re-register dead end | Keep 409, SPA adds a resend hint next to it | Frontend-only exit path; backend semantics untouched. | Plan |
| Verified lookup | Admin SDK `get_user(uid)` after password check | Same lookup as ticket's `accounts:lookup` with known exception mapping, no new REST plumbing. | Plan |
| Mail transport | Branded Faro mail via own SMTP + `generate_email_verification_link` | Mirrors PUL-85; Firebase only hosts the action page. | Ticket (PUL-85 note) |

## Scope

**In scope:** register without session + verification mail; login 403 gate;
`POST /api/auth/resend-verification` (5/min/IP, silent 204); SPA confirmation / 403 state /
409 hint; unit + E2E tests; backfill script + rollout order.

**Out of scope:** variant B (pending-registrations), custom sending domain (#20), changes to
409 semantics, "already verified" info mail, session/JWT/role mechanics, reset-password flow.

## Architecture / Approach

Straight clone of the PUL-85 flow: pre-checks on the request path (`_request_origin`,
`get_user_by_email`), response goes out first, link-gen + SMTP in `BackgroundTasks`
(failure → owner alert, silent for requester). Login gains one Admin SDK `get_user` call;
403 is the SPA's distinct "unverified" signal. All three SPA resend surfaces share one
`_submitResendVerification` helper.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Register + mail | No-session register, branded verification mail in background | `generate_email_verification_link` SDK behavior differs from mocks — verify on real Firebase |
| 2. Gate + resend | Login 403 for unverified; enumeration-safe resend endpoint | Gate ordering — must run before BQ side effects |
| 3. SPA + E2E | Confirmation screen, 403 state, 409 hint; Playwright coverage | Existing E2E that register-then-expect-dashboard must be updated |
| 4. Backfill + rollout | Idempotent script + rollout order | Running order — backfill MUST precede merge/deploy or everyone is locked out |

**Prerequisites:** branch `pul-86-email-verification` (checked out); Firebase console
verification template already PL (done in PUL-85); prod creds for the human backfill step.
**Estimated effort:** ~2 sessions across 4 phases (phases 1-3 one PR; phase 4 run pre-merge).

## Open Risks & Assumptions

- Assumed `generate_email_verification_link` behaves like its reset sibling (no
  `UserNotFoundError`); the pre-check makes this moot, but manual real-Firebase verification
  is a phase-1 gate ([[project-firebase-auth-gotchas]]).
- Accounts registered in the minutes between backfill `--apply` and deploy get auto-trusted —
  accepted at current traffic; script is idempotent, re-run if needed.
- Phases 1-3 ship as one PR: after phase 1 alone, E2E register flows are red until phase 3
  updates them.

## Success Criteria (Summary)

- New registration cannot log in before clicking the link; after clicking, login works.
- Resend: rate-limited, identical 204 for unknown/unverified/verified.
- Existing verified-by-backfill accounts (owner) unaffected — confirmed on prod post-deploy.
