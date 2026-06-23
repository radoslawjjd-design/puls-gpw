# Frame Brief: My Wallet — personal watchlist (reframed from PUL-28)

> Framing step before /10x-plan. This document captures what is *actually*
> at issue, separated from what was initially assumed.

## Reported Observation

The entire system has **no per-person identity**. Auth is two shared static
API keys compared against env vars (`src/api.py:45-60`, `_get_role()`):
`ADMIN_API_KEY` / `USER_API_KEY` → only a role (`"admin"` / `"user"`) is
returned, never an individual identity. Nobody is distinguishable from anyone
else holding the same key.

PUL-28 asks for three capabilities layered on a `user_id`: a personal
watchlist ("My Wallet"), per-ticker freemium limits (≤10 tickers free /
unlimited premium), and usage stats (visits/last activity) — and itself
flags as unresolved: how to identify a user, where to store profiles, how to
handle payments, and whether to ship My Wallet first or all three together.

## Initial Framing (preserved)

- **User's stated cause/approach**: introduce a `user_id` linked to the API
  key, store profile + watchlist + tier in new BigQuery tables
  (`user_profiles`, `user_watchlist`), build personalization + freemium +
  stats as one bundle sharing that watchlist as backbone.
- **User's proposed direction**: PUL-28 as scoped — undecided whether one
  change or phased.
- **Pre-dispatch narrowing**: Today only the product owner uses the API
  keys, but the explicit intent is to design as if multiple users will exist
  once registration ships later — so build for multi-user shape now, single
  user in practice. Of the three capabilities, **My Wallet is the one
  actually needed now**; freemium tiers and usage stats are "nice to have,"
  not urgent.

## Dimension Map

1. **Identity dimension** — does anything exist (or is anything cheap to
   add) that can anchor "my watchlist" before full registration ships? ←
   initial framing assumes a `user_id` tied to registration
2. **Storage dimension** — where would watchlist/profile data live; downstream
   of #1, not independently risky
3. **Freemium/tiers dimension** — assigning a free/premium plan presupposes
   a durable, fraud-resistant identity (registration-grade), not a throwaway
   browser token
4. **Usage-stats dimension** — same precondition as #3
5. **Scope/sequencing dimension** — does this project have a convention for
   splitting a minimal foundation from the feature slice built on it?

## Hypothesis Investigation

| Hypothesis | Evidence | Verdict |
| --- | --- | --- |
| A lightweight, registration-free identity (browser-persisted token) is enough to ship "my watchlist" today | `static/index.html` already persists `gdpr_consent_v1` (localStorage) and `apiKey`/`role` (sessionStorage, lines ~395-396, 497-500, 570-571) with a full session-idle/logout state machine (lines 406-454, `SESSION_IDLE_MINUTES=10`) — precedent for client-side persisted tokens. `src/api.py:45-60` confirms zero identity concept beyond the two static keys. `db/bigquery.py:45-201` has no user/session table — schema is fully modular, a new `watchlist(client_id, ticker, added_at)` table collides with nothing. | **STRONG** |
| Freemium tiers / usage stats need real registration, not urgent now | `prd.md:136-144` — Access Control is an explicit MVP **non-goal** ("Brak logowania użytkownika"); `roadmap.md:27-29` lists the individual-investor persona as "przyszłość, poza MVP". No payment/plan infra exists anywhere in the codebase. Matches the user's own stated priority (My Wallet now, rest not urgent). | **STRONG** |
| Project has a convention of shipping a narrow foundation before the slice that depends on it | `roadmap.md` Foundations (F-01/F-02/F-03) explicitly precede and unlock Slices (S-01..S-05), e.g. F-02 "BigQuery schema" before S-01/S-03 consume it. Two later real examples: PUL-29 (`x_posts` table) shipped as its own change explicitly marked "Foundation for PUL-27" before the consuming feature; PUL-39 portfolio_snapshots table was Phase 1 ("persistence first since later phases depend on it") before the consuming skill. | **STRONG** |
| Full registration (email/password/login) is a hard prerequisite for any personalization | `prd.md` non-goal language and the absence of any login UI beyond static-key entry both point the other way — no evidence found that registration is imminent or required first. | **NONE** |

## Narrowing Signals

- Product owner: "tylko ja i w sumie tylko ja będę używał tych API_KEY, ale docelowo dodamy rejestrację, więc robimy tak jakby było więcej niż 1 użytkownik" → design for multi-user shape, ship for one user today.
- Product owner: "My Wallet jest realną potrzebą teraz" (freemium/stats are not) → resolves the ticket's own "ship together or phased?" open question in favor of phasing.

## Cross-System Convention

This project consistently ships a minimal, narrowly-scoped foundation (new
table or capability) as its own change before building the feature that
consumes it (F-01..F-03 → S-01..S-05; PUL-29 `x_posts` table → PUL-27;
PUL-39 `portfolio_snapshots` Phase 1 → later phases). PUL-28 bundling
identity + personalization + freemium + stats into one change breaks that
convention by collapsing a foundation and three independent slices into one
ticket.

## Reframed Problem Statement

> **The actual problem to plan around is**: ship a minimal, registration-free
> per-user identity (a browser-persisted client id, no email/password) as a
> narrow foundation, then build "My Wallet" — the personal watchlist view —
> on top of it. Per-ticker freemium tiers and usage stats are NOT part of
> this plan; they depend on real registration (which doesn't exist and isn't
> being built now) and the product owner doesn't need them yet.

PUL-28 as written conflates one urgent, buildable thing (My Wallet) with two
not-yet-needed things (freemium, stats) that share a dependency (durable
identity) this project doesn't have and isn't ready to build. Planning the
full ticket today would either stall on the payment/registration open
questions or produce freemium/usage-stats code built on a throwaway identity
that gets replaced — and likely rewritten — once real registration ships.

## Confidence

**HIGH** — strong evidence across all four hypotheses, matches this
project's own established foundation-before-slice convention, and the
product owner's narrowing answers are decisive (not "not sure").

## What Changes for /10x-plan

Plan only: (1) a lightweight client-identity foundation — generated token
persisted client-side (alongside the existing `gdpr_consent_v1` /
`apiKey` sessionStorage pattern), a new BigQuery `watchlist` table keyed by
that client id, and an API layer extension to read/write it — and (2) the
"My Wallet" slice — dropdown entry in the PUL-47 profile-menu shell, a view
reusing the existing announcements table/layout filtered to the watchlist,
and add/remove-ticker UI.

Explicitly out of scope for this plan: per-ticker freemium limits, plan
tiers (free/premium), payment handling, and usage/visit statistics. Recommend
opening those as separate future Linear tickets once real registration
exists, rather than building them now against an identity model intended to
be replaced.

## References

- Source files: `src/api.py:45-60`, `static/index.html` (gdpr_consent_v1,
  sessionStorage apiKey/role, session-idle state machine), `db/bigquery.py:45-201`
- Related context: `context/foundation/prd.md:136-144`,
  `context/foundation/roadmap.md:27-29, 51-61`
- Prior precedent: `context/archive/2026-06-14-pul-29-bq-x-posts-table/`,
  PUL-39 portfolio_snapshots Phase 1
- Linear: PUL-28 (source ticket), PUL-47 (profile-menu shell, prerequisite, Done)
