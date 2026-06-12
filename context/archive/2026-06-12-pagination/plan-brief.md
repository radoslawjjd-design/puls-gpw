# Pagination — Plan Brief

> Full plan: `context/changes/pagination/plan.md`

## What & Why

Replace the single `limit` cap on `GET /announcements` with proper OFFSET-based pagination (`page` + `page_size`). The current `limit=100` hard cap makes it impossible to browse older announcements — the panel can only ever show the most recent batch.

## Starting Point

`GET /announcements?limit=20` returns the 20 most recent rows. The BQ functions use `LIMIT @limit`. The frontend has a number input capped at 100. There is no way to fetch page 2.

## Desired End State

`GET /announcements?page=2&page_size=50` returns rows 51–100. The panel shows Prev/Next buttons and a page_size selector (20/50/100). `limit` param is removed. E2E Playwright tests cover the 4 key pagination behaviours.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) |
|---|---|---|
| Pagination style | OFFSET (`LIMIT @page_size OFFSET @offset`) | Sufficient for current data scale (hundreds of rows/day); zero new concepts |
| page_size bounds | default=20, max=100 | Matches current limit cap; existing tests need minimal change |
| `limit` compat | Remove entirely | Panel is the only client; no backwards-compat risk |
| Session mgmt | Skip | 401→showLogin() already works; API keys are static |
| Frontend UX | Prev/Next + page_size select | Classic, readable; no total-count BQ query needed |
| E2E framework | pytest-playwright (new dep) | Not yet installed; Phase 4 adds it |

## Scope

**In scope:**
- `db/bigquery.py` — page/page_size params, OFFSET query
- `src/api.py` — replace limit with page+page_size
- `static/index.html` — Prev/Next buttons + page_size select
- `tests/test_bigquery.py`, `tests/test_api.py` — updated unit tests
- `tests/e2e/` — Playwright setup + 4 E2E tests

**Out of scope:**
- Total page count ("Page X of Y") — requires extra COUNT(*) BQ query
- Cursor-based pagination
- Infinite scroll
- Session token refresh (API keys don't expire)

## Architecture / Approach

Data flows BQ → API → Frontend; phases follow the same order. Each phase is independently testable. OFFSET computation (`(page-1) * page_size`) lives in the BQ layer; the API layer is a thin pass-through.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. BQ Layer | `list_announcements_*` accept page/page_size, compute OFFSET | BQ test mock must match new signature |
| 2. API Layer | `/announcements` drops `limit`, exposes `page`+`page_size` | 422 regression if test still sends `?limit=` |
| 3. Frontend | Prev/Next + page_size select replacing limit input | State reset on filter submit must be explicit |
| 4. E2E | Playwright installed + 4 pagination scenarios | `conftest.py` in-process server fixture needs care |

**Prerequisites:** None — self-contained change.
**Estimated effort:** ~1 session across 4 phases.

## Open Risks & Assumptions

- Playwright `TestClient` in-process fixture approach may need adjustment depending on how `create_app()` handles repeated instantiation in tests.
- `Next` disabled heuristic (returned rows < page_size) gives a false positive if total rows happen to be exactly divisible by page_size — acceptable UX tradeoff.

## Success Criteria (Summary)

- `GET /announcements?page=2&page_size=20` returns the correct offset rows
- Panel Prev/Next navigation works; filter resets to page 1
- All 4 Playwright E2E tests pass
