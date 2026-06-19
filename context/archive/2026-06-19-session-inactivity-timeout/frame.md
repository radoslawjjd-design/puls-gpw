# Frame Brief: Session inactivity timeout + session duration display

> Framing step before /10x-plan. This document captures what is *actually*
> at issue, separated from what was initially assumed.

## Reported Observation

Panel "sessions" (an `X-API-Key` cached in `sessionStorage` after login) never
expire from inactivity. Once a user enters the key, it remains usable
indefinitely until manual logout or the browser tab is closed — there is no
idle timeout, no warning before timeout, and no "how long have I been
logged in" indicator.

## Initial Framing (preserved)

- **User's stated cause / approach**: Build a frontend-only timer (no
  backend changes) — auto-logout after `SESSION_IDLE_MINUTES = 30` of no
  mouse/keyboard/scroll activity, dismissible warning ~2 min before expiry,
  clear `sessionStorage`/`localStorage` and redirect to login on timeout,
  optional "Zalogowano: X min" header indicator.
- **User's proposed direction**: Implement exactly as scoped in
  `context/changes/session-inactivity-timeout/change.md`, titled "Session
  security – inactivity timeout."
- **Pre-dispatch narrowing**: Primary driver — user deferred to
  recommendation ("not sure, per recommendation"). Duration display — "not
  sure" whether it's a real ask or bundled nice-to-have. Security
  expectation — **user explicitly assumed the timeout would actually close
  off API access** once triggered, not just hide the UI on one browser tab.
  This last answer is the decisive signal driving the reframe below.

## Dimension Map

The observation ("sessions never expire") could be addressed at any of
these dimensions:

1. **Client-side idle lock (UX)** — stop showing the dashboard / require
   re-entry of the key after idle, on the device where the tab is open.
   Fully achievable with a frontend timer.
2. **Cost guard (PUL-30)** — stop idle tabs from polling the API in the
   background, since polling continues even while the user is away.
   Fully achievable with the same frontend timer.
3. **Real access revocation (security)** — make the cached key actually
   stop working against the API once the user is "logged out," so a copy
   of the key obtained another way (devtools, logs, shoulder-surfed before
   timeout fires) can't keep being used.  ← **user's framing implicitly
   assumes this**
4. **Session-duration display** — cosmetic header indicator, independent
   of the other three.

## Hypothesis Investigation

| Hypothesis | Evidence | Verdict |
| --- | --- | --- |
| (1) Idle lock achievable client-side | `static/index.html` login flow caches `apiKey`/`role` in `sessionStorage`; logout (lines ~420-424) clears it client-side and gates the UI back to the login screen. A timer doing the same on idle fully blocks a casual walk-up user with no devtools access. | STRONG |
| (2) Cost guard achievable client-side | Same mechanism (stop polling + redirect) directly addresses PUL-30's stated concern about idle-tab polling cost. | STRONG |
| (3) Real access revocation via frontend-only timeout | `src/api.py` `_get_role()` validates `X-API-Key` by **plain string comparison against `ADMIN_API_KEY`/`USER_API_KEY` env vars** — no session table, no token, no expiry, no logout/revoke endpoint. The key is shared across all admins (one key) / all users (one key) and stays valid until someone manually rotates it in Secret Manager (human-only infra action per `CLAUDE.md`). Clearing `sessionStorage` removes the *browser's local copy* but does nothing server-side — a key captured any other way keeps working forever. **A frontend-only change cannot revoke access, by construction.** | **NONE** — and the gap is structural, not a missing feature to add later |
| (4) Duration display | Cosmetic; no dependency on the other dimensions. User unsure if it's a real ask — treat as the optional/nice-to-have it's already labeled as in change.md. | N/A (uncontested) |

## Narrowing Signals

- User answered **"No, assumed it closes access"** when asked whether they
  knew the timeout wouldn't revoke the key server-side — this is the
  decisive signal. The ticket's title ("session security") and the user's
  mental model both expect a security outcome that the proposed
  frontend-only implementation structurally cannot deliver.
- change.md *already* half-anticipates this: "if server-side sessions land
  [in PUL-28], this ticket should be revisited to also invalidate the token
  server-side." That note was written as a future caveat — the narrowing
  answer shows it's actually a **present-tense gap**, not a future one.

## Cross-System Convention

The archived auth design (`context/archive/2026-06-11-auth-public-url/plan.md`)
explicitly scoped out real per-user auth and session state: "Pełne
username/password auth — to PUL-23" was listed under "What We're NOT
Doing." The two-shared-keys model was a deliberate, minimal choice to
satisfy a certification requirement quickly — not an oversight. Layering a
client-side idle timer on top of that doesn't change the underlying
contract; it's a UX/cost control, not a security control, and the existing
design never claimed otherwise until this ticket's title did.

## Reframed (or Confirmed) Problem Statement

> **The actual problem to plan around is**: build a client-side idle-lock +
> idle-tab cost guard (exactly as scoped technically), while explicitly
> *not* presenting it as closing a session-security gap — because with the
> current shared, non-expiring API key model, no frontend-only change can
> revoke access. Real "session security" (key compromise / forced logout)
> requires server-side session state and is blocked on PUL-28.

The proposed implementation (idle timer, warning, clear storage, redirect,
optional duration display) doesn't need to change — it's the right build
for dimensions (1), (2), and (4). What changes is the *claim*: the plan,
the ticket title/description, and any related documentation should state
plainly that this guards against an unattended/shared screen and reduces
idle polling cost, and is **not** a substitute for revoking a compromised
key. Otherwise this ships as a false sense of closed security gap.

## Confidence

**HIGH** — structural evidence (`src/api.py` plain string-compare auth, no
session table, no revoke endpoint) is unambiguous and corroborated by the
deliberate "no session state" design decision in the archived auth plan.
The narrowing signal (user expected revocation) is decisive and directly
contradicts what the architecture can deliver.

## What Changes for /10x-plan

Plan the frontend-only idle timer + duration display as originally scoped
(no technical redesign needed) — but add an explicit "Limitations" note in
the plan and recommend updating the PUL-32/issue #28 description to drop
or qualify "session security" framing, so it's clear this is a UX/cost
control, not key revocation. If real revocation is wanted, that's a
separate, larger problem gated on PUL-28 (server-side sessions) and should
not be implied as solved by this ticket.

## References

- `static/index.html` — login flow, `sessionStorage` caching, logout
  (client-side only)
- `src/api.py` — `_get_role()` plain string-compare auth, no
  session/token/revoke
- `context/archive/2026-06-11-auth-public-url/plan.md:57-60` — deliberate
  exclusion of real per-user/session auth ("What We're NOT Doing")
- `context/changes/session-inactivity-timeout/change.md` — original ticket
  scope, PUL-28/PUL-30 cross-references
