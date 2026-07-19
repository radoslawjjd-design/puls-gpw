# Per-User Data Isolation (PUL-74) — Plan Brief

> Full plan: `context/changes/per-user-data-isolation/plan.md`
> Research: `context/changes/per-user-data-isolation/research.md`

## What & Why

Every registered user gets their own watchlist, portfolios, treemap and calendar, scoped to the JWT `user_id` (Firebase UID). The anonymous browser-UUID path (`X-Client-Id`) is retired — it was the last real isolation hole: the header is unverified, so anyone holding the shared `USER_API_KEY` could impersonate any identity. Registration exists (PUL-72), so per-user features become registered-users-only.

## Starting Point

PUL-71 already laid the seam: `_get_client_id` prefers the signed JWT `user_id` and falls back to the raw header. JWT users are therefore *functionally* isolated already; portfolio tables already have `user_id` columns. What's left: the unverified fallback, the `watchlist.client_id` column name, two SQL statements without a `user_id` predicate, frontend header machinery, e2e tests logging in via API key, and the owner's historical rows keyed by a browser UUID.

## Desired End State

No session cookie → 401 on all 12 per-user endpoints. Two accounts see fully disjoint data (test-proven, including hand-crafted cross-user requests). API-key admin keeps global `/admin/*` views; API-key sessions no longer see per-user nav. The owner, logged in by email, sees their historical watchlist and wallets (one-time re-key, human-run).

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Guest mode (PUL-73) | Dropped, not a prereq | Ticket removed from backlog; per-user = registered users only | User |
| Anonymous `X-Client-Id` path | Retired; per-user endpoints JWT-only (401) | Closes the spoofing hole by construction | User |
| Old data | One-time re-key of owner's rows onto Firebase UID | Script with `--dry-run`, human-run; other anonymous UUIDs become dead rows | User |
| `watchlist.client_id` → `user_id` | Rename now, additively | Add column + startup backfill + dual-write; DROP later, human-only (PUL-83 `role` pattern) | User + Plan |
| API-key sessions in UI | Hide per-user nav | No dead buttons/401s; admin keeps `/admin/*` | User |
| Expired session UX | 401 → `doLogout()` → landing | Already implemented on all 15 fetch sites — nothing to build | Research |
| Isolation testing | Unit two-user matrix + one cheap e2e spot-check | Covers ticket criterion deterministically; full browser matrix deferred to `/10x-e2e` | User |
| `USER_API_KEY` | Stays for global read endpoints | Minimal blast radius; retirement is a separate ticket | User |
| Under-scoped SQL (MERGE :545, cascade DELETE :804) | Add `user_id` predicates | Defense-in-depth while the schema is touched anyway | Research |

## Scope

**In scope:** watchlist column migration + backfill; JWT-only dependency (`_get_user_id`); SQL predicate tightening; frontend `X-Client-Id` removal + nav gating; e2e switch to email login; owner re-key script; cross-user isolation tests.

**Out of scope:** guest mode; `USER_API_KEY` retirement; `client_id` column DROP (human, later); admin cross-user views; token revocation denylist; watchlist duplicate-insert race.

## Architecture / Approach

Additive-first so every commit deploys safely: schema+backfill land while the old path still works → API flips to JWT-only → frontend strips the header and gates nav → e2e re-authenticates via fake-Firebase email login → human-run re-key script ships last. Single PR; CI deploys on merge.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. BQ schema + predicates | `watchlist.user_id` + backfill; SQL scoped | Mocked tests don't parse SQL → real-BQ round-trip is the gate |
| 2. API JWT-only | 401 without session; two-user isolation tests | Breaking global endpoints by over-reaching (only 12 per-user deps change) |
| 3. Frontend | No `X-Client-Id`; nav gated by session type | `faro-v8.html` byte-parity invariant |
| 4. E2E email login | Suite green with real auth flow | Fake-BQ state persists → unique email per test required |
| 5. Re-key script | `scripts/migrate_owner_identity.py` + runbook | Needs owner's browser UUID — human-run only |

**Prerequisites:** PUL-71/72/83 deployed (done); feature branch off master; owner's browser UUID needed only at Phase 5 runbook time.
**Estimated effort:** ~2-3 sessions across 5 phases.

## Open Risks & Assumptions

- Assumes no third-party consumer relies on `X-Client-Id` against per-user endpoints (none known; only the SPA and e2e tests send it).
- E2E migration touches 8+ spec files — mechanical but wide; lesson `feedback-e2e-conftest-bq-mocking` applies if any new BQ call surfaces.
- The owner's historical rows sit under an unknown-until-read browser UUID; if the old browser profile is gone, the re-key target can be recovered by listing distinct identities in BQ (human step).

## Success Criteria (Summary)

- Two registered users cannot see or mutate each other's data — proven by unit tests and an e2e spot-check.
- Requests without a session get 401 on per-user endpoints; API-key admin's global views unchanged; no 500s post-deploy.
- Owner sees their pre-existing watchlist/wallets after email login (post re-key).
