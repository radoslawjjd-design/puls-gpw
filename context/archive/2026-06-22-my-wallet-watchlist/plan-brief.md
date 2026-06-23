# My Wallet — Personal Watchlist — Plan Brief

> Full plan: `context/changes/my-wallet-watchlist/plan.md`
> Frame brief: `context/changes/my-wallet-watchlist/frame.md`

## What & Why

Ship a minimal, registration-free per-user identity (a browser-persisted
client id, no email/password) as a narrow foundation, then build "My
Wallet" — the personal watchlist view — on top of it. Per-ticker freemium
tiers and usage stats are NOT part of this plan; they depend on real
registration (which doesn't exist and isn't being built now) and the
product owner doesn't need them yet.

## Starting Point

Today the app has zero per-person identity — two static API keys resolve
only to a role (`"admin"`/`"user"`), never an individual
(`src/api.py:45-60`). The PUL-47 profile-menu shell exists but every
dynamic item it injects is admin-gated. `static/index.html` already
persists a token (`gdpr_consent_v1`) client-side, and `db/bigquery.py` has
a clean, repeatable table-foundation template (`portfolio_snapshots`) —
both are precedent this plan reuses rather than reinvents.

## Desired End State

A user opens the app, silently gets a persistent browser-level id, opens
the profile menu, clicks "My Wallet," and sees a dedicated view of
announcements for only the tickers they've chosen to track — with
autocomplete-validated add and one-click remove. The watchlist survives
reloads and logout in the same browser; it is not shared across devices and
carries no plan/tier concept.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Scope (My Wallet vs. freemium/stats) | My Wallet only; freemium/stats deferred to future tickets | Both depend on durable identity that doesn't exist; product owner confirmed only My Wallet is urgent | Frame |
| Identity mechanism | Browser-persisted UUID via `localStorage`, no registration | Cheapest thing that unblocks "my watchlist" today; matches existing `gdpr_consent_v1` precedent | Frame |
| Identity transport | Custom `X-Client-Id` header on watchlist requests | Mirrors the existing `X-API-Key`/`APIKeyHeader` pattern already in `src/api.py` | Plan |
| My Wallet view shape | Dedicated full view (`#my-wallet-view`), not a modal | Preserves the app's existing view=URL-state convention (deep-link/back-button) | Plan |
| Add-ticker validation | Autocomplete + server-side rejection of unknown tickers | Guarantees every watchlisted ticker actually has data to show; reuses `/autocomplete/tickers` | Plan |
| Watchlist size | No UI cap; defensive 200-ticker query bound only | Avoids inventing throwaway freemium-shaped limits while still guarding the JOIN query | Plan |
| Remove-ticker UX | Instant remove, no confirm dialog, empty-state CTA | Matches the lightweight, low-friction framing of the whole feature | Plan |
| Menu visibility | Unconditional (both roles), unlike existing admin-only injected items | My Wallet is a personal feature, not an admin one | Plan |

## Scope

**In scope:**
- BigQuery `watchlist` table (client_id, ticker, added_at) + CRUD
- `X-Client-Id` header auth-adjacent dependency (not a credential)
- `GET/POST/DELETE /watchlist`, `GET /announcements/my-wallet`
- Client id generation/persistence, profile-menu entry, dedicated UI view
- Wiring table creation into `api_main.py` startup (a gap that exists today)

**Out of scope:**
- Per-ticker freemium limits, plan tiers, payment handling
- Usage/visit statistics
- Real registration (email/password/login)
- Confirm dialogs, undo, or a user-facing watchlist size limit

## Architecture / Approach

Three phases, each independently shippable: (1) BigQuery foundation
mirroring the existing `portfolio_snapshots` schema/create/ensure template,
(2) API endpoints gated by a new header dependency plus the first-ever
startup table-creation hook in the FastAPI service, (3) frontend wiring
that follows the codebase's existing inline-fetch-header and
view-as-URL-state conventions rather than introducing new abstractions.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. BigQuery foundation | `watchlist` table + CRUD + watchlist-filtered announcements query | First table with upsert/delete-by-composite-key semantics in this codebase |
| 2. API layer | Header dependency, 4 endpoints, startup table creation | `api_main.py` has never created a table at startup before — new wiring |
| 3. Frontend | Client id, menu entry, dedicated view, add/remove UI | Must avoid the existing admin-only gating pattern when adding the menu item |

**Prerequisites:** PUL-47 profile-menu shell (Done).
**Estimated effort:** ~1 session across 3 phases — small table, ~4 endpoints, one new view reusing existing rendering patterns.

## Open Risks & Assumptions

- The 200-ticker defensive query bound is arbitrary and untested under
  load — fine for today's single real user, revisit once registration and
  real multi-user traffic exist.
- `X-Client-Id` is unsigned and resettable (clearing `localStorage` starts
  a fresh, empty watchlist) — accepted as inherent to a throwaway identity,
  not a bug to fix here.

## Success Criteria (Summary)

- A user can add/remove tickers and see a personal, filtered announcements
  view that persists across reload and logout in the same browser.
- No freemium, payment, or usage-stats code is introduced.
- All new BigQuery SQL passes a real round-trip via `scripts/test_bq.py`,
  not just mocked unit tests.
