# Non-admin Portfolio Treemap (PUL-64) — Plan Brief

> Full plan: `context/changes/non-admin-portfolio-treemap/plan.md`
> Frame brief: `context/changes/non-admin-portfolio-treemap/frame.md`
> Research: `context/changes/non-admin-portfolio-treemap/research.md`

## What & Why

> **The actual problem to plan around is**: this is not an access-control change — it's a
> new feature with two real components: (a) a per-user positions ledger (ticker + quantity,
> reusing the watchlist's `client_id` pattern and the existing ticker/company autocomplete),
> and (b) a treemap rendering path that has no price to render with until PUL-61 ships.

Both blockers are now resolved: PUL-65 shipped the positions ledger and PUL-54 shipped
the daily price feed. PUL-64 can now complete the feature — one new API endpoint, one new
compute function, and a reworked frontend that extends "Mój portfel" with multi-wallet
support and a treemap sub-view for all roles.

## Starting Point

`user_portfolio_positions` (with `kurs_zamkniecia` JOIN), `renderTreemap()`, and the
ticker autocomplete all exist on master after PUL-65/PUL-54. The admin-only treemap
(`#treemap-btn`, `#treemap-view`, `GET /admin/portfolio/treemap`) is fully functional
but admin-gated and reads XTB-snapshot data, not user positions. There is no
`user_portfolios` wallet-registry table and no per-portfolio scoping anywhere.

## Desired End State

Any authenticated user opens "Mój portfel," creates wallets (Główny/IKZE/IKE/…), adds
positions scoped to each wallet, and switches between a per-portfolio positions table
and an all-wallets-side-by-side treemap tab — with a notice listing tickers that have no
current price. Admin users see the same UI; the old XTB-snapshot treemap button is gone.

## Key Decisions Made

| Decision | Choice | Why | Source |
|---|---|---|---|
| Admin treemap fate | Unified — admin uses user-positions treemap | One code path; XTB snapshot endpoint kept in code but UI-invisible | Plan |
| Multi-portfolio display | Side-by-side, wrapping | Consistent with existing admin treemap CSS; most users have 1–2 wallets | Research → Plan |
| Table scope | Per-portfolio tabs (scoped to selected wallet) | Enables context-sensitive add; consistent with treemap's per-wallet model | Plan |
| Position add UX | Context-sensitive — active tab determines portfolio | No extra selector; user navigates to correct tab first | Plan |
| API shape | One endpoint, all portfolios in one response | Mirrors admin pattern; one BQ traversal; simpler frontend | Plan |
| Compute function | New `compute_user_portfolio_treemap_positions()` | `compute_treemap_positions()` is XTB-JSON-specific and cannot be reused | Research |
| No-price positions | Excluded from layout + notice above treemap | `computeTreemapLayout` already filters `position_value_pln ≤ 0` | Research → Plan |
| Migration (orphan positions) | Auto-assign to Główny on wallet creation | User-initiated, no surprise data moves; research precedent | Research → Plan |

## Scope

**In scope:**
- `user_portfolios` BQ table (wallet registry: type, name, display_order)
- `portfolio_id` NULLABLE migration on `user_portfolio_positions`
- Wallet management API: `GET/POST/DELETE /api/portfolio/wallets`
- Updated positions API: require `portfolio_id` on GET/POST/DELETE
- `GET /api/portfolio/treemap` — all wallets in one response
- `compute_user_portfolio_treemap_positions()` pure function
- Frontend: portfolio selector tabs, "Dodaj portfel" modal, table scoped per wallet,
  "Treemapa" tab with side-by-side rendering, no-price notice, URL deep-link
- Removal of admin-only XTB-snapshot treemap from `injectAdminOnlyChrome()`
- Unit + integration + E2E test coverage

**Out of scope:**
- `GET /admin/portfolio/treemap` endpoint — kept in code, not removed
- Reusing `compute_treemap_positions()` for non-admin data
- Per-portfolio treemap (one wallet at a time)
- PUL-61 price ingestion — already merged (PUL-54 resolved this)

## Architecture / Approach

BQ layer: `user_portfolios` table + `portfolio_id` column migration → new CRUD functions
for wallets → updated upsert MERGE key `(portfolio_id, ticker)`.
API layer: wallet CRUD endpoints + `GET /api/portfolio/treemap` that iterates over user's
wallets and calls `list_user_portfolio_positions()` per wallet, then runs
`compute_user_portfolio_treemap_positions()` on each result set.
Frontend: "Mój portfel" gains portfolio selector tabs, wallet management modal, and a
"Tabela | Treemapa" toggle; all position operations carry `portfolio_id` from the active
tab; the treemap tab calls one endpoint and renders each wallet with the existing
`renderTreemap()` function into dynamically created containers inside the view.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Data model | `user_portfolios` table, `portfolio_id` column, BQ CRUD, conftest mocks, unit tests | MERGE key change breaks PUL-65 API until Phase 3 ships |
| 2. Backend | Wallet CRUD API, positions endpoint updates, treemap endpoint + compute function, integration tests | Breaking change in position endpoints requires coordinated Phase 3 deploy |
| 3. Frontend | Portfolio tabs, wallet modal, treemap tab, admin cleanup | Largest phase; ships in same PR as Phase 2 (breaking change) |
| 4. E2E tests | Full E2E coverage for wallets + treemap | conftest mock scope creep if PUL-65 mocks are missing or incompatible |

**Prerequisites:** Merge master (PUL-65 + PUL-54) before starting Phase 1. Verify
conftest has PUL-65 portfolio position mocks after merge.

**Phases 2 and 3 must ship in the same PR** — the upsert MERGE key change in Phase 2
breaks the position endpoints until the frontend in Phase 3 passes `portfolio_id`.

**Estimated effort:** ~3–4 sessions across 4 phases.

## Open Risks & Assumptions

- `tests/e2e/conftest.py` does not yet have PUL-65 portfolio position mocks (verified on
  this branch); they must be present after master merge, or Phase 1 adds them before adding
  user_portfolios mocks.
- `showPortfolioPositionsView()` URL routing (PUL-65) may or may not push
  `?view=portfolio-positions` to history — Phase 3 must verify and add if missing.
- With 7 wallets (Główny + IKZE + IKE + 2×Inny + PPK + PPE), side-by-side layout wraps
  to two rows on most screens; acceptable given most users will have 1–3 wallets.

## Success Criteria (Summary)

- Any authenticated role can create wallets, manage positions per wallet, and view a
  multi-wallet treemap within "Mój portfel"
- Admin role: no "Treemapa portfela" button from old XTB-snapshot path visible in nav
- `uv run pytest tests/` (unit + integration + E2E) passes with zero failures
