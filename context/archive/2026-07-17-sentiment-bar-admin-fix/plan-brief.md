# Sentiment Bar Admin Fix — Plan Brief

> Full plan: `context/changes/sentiment-bar-admin-fix/plan.md`

## What & Why

The faro-v2 "Sentyment 7 dni" bar (Obserwowane view) shipped as a non-functional stub: it
renders for every user but always shows zeros, because the backend strips sentiment and
never returns the score on `/announcements/my-wallet`. Sentiment/score are admin-only by
app convention — the bar must work for admins and disappear for everyone else.

## Starting Point

`GET /announcements` already has the exact admin/user split we need (`src/api.py:307-331`).
The my-wallet endpoint lacks that branch, and its BQ query doesn't even select
`analysis_score`. The frontend function has no role check. No data leaks today — users just
see an empty shell.

## Desired End State

Admin sees real counts + average score in the bar; a regular user sees no bar and their
browser issues no sentiment fetch. The user-role response contract is unchanged and locked
by a regression test.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) |
| --- | --- | --- |
| Admin response shape | Mirror `/announcements` via `AnnouncementAdmin` | One consistent "admin sees everything" contract, zero new models |
| Frontend for user role | Early-return, no fetch | Saves a 100-row query the user can never see the result of |
| Test depth | Unit (role contract) + 2 e2e (both roles) | It's a role-visibility bug — guard it at both levels |

## Scope

**In scope:** `analysis_score` in the watchlist SELECT; admin branch on my-wallet; role
gate in `fetchWlSentimentSummary()`; unit + e2e tests; e2e mock enrichment.

**Out of scope:** aggregation window/fetch size changes, other endpoints, sentiment for
regular users, caching.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Backend admin branch | my-wallet returns sentiment+score to admin, user contract locked | Accidental user-contract drift (guarded by regression test) |
| 2. Frontend gate + E2E | Bar admin-only, both behaviors proven in browser | Mock `published_at` aging out of the 7-day window (use dynamic timestamp) |

**Prerequisites:** none — branch `pul-82-sentiment-bar-fix` already open.
**Estimated effort:** ~2–3 h, single session, two commits.

## Open Risks & Assumptions

- Assumes watchlist announcements in prod have non-null `analysis_score` (verified pattern
  exists on `/announcements`; manual BQ round-trip in Phase 1 confirms).

## Success Criteria (Summary)

- Admin: populated bar (counts, avg score, announcement count) on Obserwowane.
- User: no bar, no sentiment fetch, response fields unchanged.
- Full unit + e2e suites green.
