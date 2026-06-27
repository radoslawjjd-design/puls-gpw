# PUL-65 — User Portfolio Positions CRUD — Plan Brief

> Full plan: `context/changes/pul-65/plan.md`
> Research: `context/changes/pul-65/research.md`

## What & Why

Allow every logged-in user to maintain their own stock portfolio — a personal ledger of positions (ticker, shares, avg buy price). The existing treemap (PUL-64, Backlog) will consume this data; PUL-65 delivers the data layer and management UI that unlocks it.

## Starting Point

PUL-28 (watchlist) established the auth identity model (`client_id` from `X-Client-Id` header = user partition key, no users table) and the BQ + FastAPI + UI patterns this ticket follows directly. PUL-54 delivered `company_daily_stats` with daily close prices — the pricing source. Both are Done.

## Desired End State

A logged-in user opens "Mój portfel" from the sidebar, sees their positions in a table with current price, daily % change, and P&L. They can add a position via an inline form (ticker + company autocomplete, shares, avg price), edit it (form pre-fills, ticker read-only), and delete it (browser confirm dialog). When no price data exists for a ticker, price columns show "—".

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|----------|--------|------------------|--------|
| User identity | `client_id` from `X-Client-Id` header | No users table — browser UUID is the established identity model from PUL-28 | Research |
| BQ upsert key | MERGE on `(user_id, ticker)` | One row per user+ticker; update on re-add is the intended "edit" primitive | Research |
| Pricing query | LEFT JOIN + ROW_NUMBER() OVER (PARTITION BY ticker) | ~31% of tickers lack daily stats; INNER JOIN would silently drop valid positions | Research |
| Missing price display | Show "—" in price/P&L/daily columns | User sees all their positions; consistent with `.no-data` treemap style | Plan |
| Form style | Inline expandable (no modal) | Matches watchlist "Dodaj" pattern already in the codebase | Plan |
| Edit UX | "Edytuj" button → same inline form pre-filled; ticker read-only | Simple extension of inline form; one form for both add and edit modes | Plan |
| Delete confirmation | Native `confirm()` dialog | Zero extra UI; same as watchlist remove pattern | Plan |
| company_name source | Trust POST body (user-provided via autocomplete) | No extra BQ query; autocomplete already gates to valid names | Plan |
| P&L computation | Python in API layer, not BQ | Consistent with how `compute_treemap_positions` works; avoids SQL floating-point edge cases | Plan |

## Scope

**In scope:**
- `user_portfolio_positions` BQ table (user_id, ticker, company_name, shares, avg_buy_price, timestamps)
- BQ functions: create/ensure, upsert, delete, list-with-pricing-JOIN
- 3 REST endpoints: GET / POST / DELETE `/api/portfolio/positions`
- "Mój portfel" frontend view: table + inline form (add/edit modes) + delete with confirm
- Unit tests + round-trip BQ script + E2E tests (4 scenarios)

**Out of scope:**
- Admin portfolio (XTB upload, `portfolio_snapshots`) — untouched
- User-facing portfolio treemap (PUL-64) — separate ticket
- P&L calendar (PUL-59) — separate ticket
- Historical position tracking

## Architecture / Approach

```
Browser (X-API-Key + X-Client-Id)
  ↓
src/api.py  [GET|POST|DELETE /api/portfolio/positions]
  ↓
db/bigquery.py  [upsert_user_portfolio_position | delete | list]
  ↓
BQ: user_portfolio_positions
  LEFT JOIN
BQ: company_daily_stats  (ROW_NUMBER PARTITION BY ticker)

P&L computed in Python (API layer) before JSON response.
```

## Phases at a Glance

| Phase | What it delivers | Key risk |
|-------|-----------------|----------|
| 1. BQ data layer | Table + 5 functions + round-trip script | Pricing JOIN silently drops rows if INNER JOIN used instead of LEFT |
| 2. FastAPI endpoints | 3 endpoints + Pydantic models + unit tests | P&L div-by-zero if avg_buy_price = 0 |
| 3. Frontend UI | Nav item + table + inline form (add/edit) + delete | Two-mode form state (`_ppEditingTicker`) — must reset on cancel and after save |
| 4. E2E tests | 4 test scenarios + conftest patches | All 5 functions (incl. 2 startup hooks) must be patched or app fails to start |

**Prerequisites:** PUL-54 Done ✓ (company_daily_stats ingesting), PUL-28 Done ✓ (auth model in place), PUL-53 Done ✓ (companies autocomplete available)  
**Estimated effort:** ~1-2 sessions across 4 phases

## Open Risks & Assumptions

- `reference_price` fallback: confirmed absent from `company_daily_stats` schema — not available, no action needed
- `shares` and `avg_buy_price` are validated `> 0` in Pydantic model; BQ doesn't enforce this constraint

## Success Criteria (Summary)

- Round-trip script `scripts/test_bq_user_portfolio_positions.py` exits 0 (upsert idempotent, pricing JOIN works, delete cleans up)
- All 3 endpoints return correct shapes; GET enriches with live price + P&L
- User can add, edit, and delete positions from the UI; null-price tickers show "—"
