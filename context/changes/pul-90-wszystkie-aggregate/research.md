---
date: 2026-07-24T11:09:21+02:00
researcher: Radek
git_commit: 60fb5d71ed3ce8e9b040063b4159eb8a6ec668f5
branch: pul-90-wszystkie-aggregate
repository: radoslawjjd-design/puls-gpw
topic: "PUL-90 — 'Wszystkie' aggregate view in Mój portfel (all-portfolios positions + combined summary)"
tags: [research, codebase, portfolio, positions, frontend, aggregate]
status: complete
last_updated: 2026-07-24
last_updated_by: Radek
---

# Research: PUL-90 — "Wszystkie" aggregate view in Mój portfel

**Date**: 2026-07-24T11:09:21+02:00
**Researcher**: Radek
**Git Commit**: 60fb5d7
**Branch**: pul-90-wszystkie-aggregate
**Repository**: radoslawjjd-design/puls-gpw

## Research Question

How does Mój portfel render positions and summary today, and what is the cleanest way to add a default-first **"Wszystkie"** wallet-selector entry that aggregates positions across all of a user's portfolios (summed value / combined P&L / combined daily change), read-only? Resolve the three design decisions from the ticket: (1) merge same-ticker rows vs per-portfolio rows, (2) client-side merge vs backend `portfolio_id=all`, (3) how to enforce read-only.

## Summary

The feature is small and mostly frontend, and the backend already supports the hard part. Key findings:

- **The DB layer already supports "all positions for a user"** — `list_user_portfolio_positions(user_id, portfolio_id=None)` drops the portfolio filter and keeps only `user_id` (`db/bigquery.py:758-863`). The treemap endpoint already relies on this (`src/api.py:864`). So a backend `portfolio_id=all` mode on `GET /api/portfolio/positions` needs **no DB change** — only an API-handler branch that skips the per-wallet ownership check and passes `None` down.
- **Market value is never returned by the positions endpoint** — the frontend computes value itself in `_updatePortfolioSummary` as `shares * current_price` (`static/index.html:3121-3168`). So the summary already sums correctly whether rows are merged or not.
- **Recommended approach**: backend `portfolio_id=all` mode (1 call, cheap, DB-ready) + client-side merge of same-ticker rows for the table + read-only via conditional rendering. See "Architecture Insights".
- **Main non-obvious risk**: the wallet-tabs strip is shared with the **Kalendarz** view (`static/index.html:3641`), whose calendar/history endpoints are strictly per-`portfolio_id` and validate ownership — a sentinel `all` id would 404 there. The "Wszystkie" calendar chart is explicitly out of scope (companion ticket), so the plan must decide the fallback behavior when "Wszystkie" is active and the user opens Kalendarz.

## Detailed Findings

### Wallet selector / active-portfolio state (frontend)

- `let _activePortfolioId = null;` (`static/index.html:3107`) — a portfolio-id string, or `null` meaning "none". Truthy checks (`if (_activePortfolioId)`) gate fetches at `:2662`, `:3373`, `:3881`, `:3981`.
- `_renderPortfolioTabs(portfolios)` (`static/index.html:3301-3348`) builds one `<button.pp-portfolio-tab>` per wallet, sets `.active` on the **first** (`i === 0`, `:3319`), attaches a delete-`×` icon per tab (`:3324-3333`), and on click sets `_activePortfolioId`, clears cal/hist caches, and refetches (`:3335-3345`).
- `fetchUserPortfolios()` (`static/index.html:3350-3391`) fetches `GET /api/portfolio/wallets`, resolves `_activePortfolioId` from the `?portfolio=` URL param or defaults to `data[0]` (`:3363-3365`), renders tabs, and fetches positions. It also restores `?tab=` / `?range=` from the URL.
- URL round-trip: `_ppPortfolioParams()` writes `portfolio=<id>` only when `_activePortfolioId` is truthy (`static/index.html:2658-2669`); `_ppWriteUrl()` at `:2671-2675`.
- View entry: `showPortfolioPositionsView()` calls `if (!_portfoliosFetched) fetchUserPortfolios();` (`static/index.html:3797`).

### Positions fetch + table render (frontend)

- `fetchPortfolioPositions()` (`static/index.html:3041-3064`) GETs `/api/portfolio/positions?portfolio_id=<_activePortfolioId>` (returns early with a placeholder when `_activePortfolioId === null`, `:3044-3047`), stores `_ppPositions`, calls `_renderPortfolioTable`.
- `_renderPortfolioTable(positions)` (`static/index.html:3179-3236`) maps each position to a `<tr>` with an **Edytuj** and **Usuń** button per row (`:3208-3211`), wires their click handlers (`:3217-3225`), then calls `_updatePortfolioSummary(positions)` (`:3215`).
- Position fields consumed: `ticker`, `company_name`, `shares`, `avg_buy_price`, `current_price`, `daily_change_pct`, `pnl_pln`, `pnl_pct`, `price_history` (`:3186-3207`).
- Write paths that must be blocked in read-only mode: `_upsertPortfolioPosition` (POST, uses `_activePortfolioId`, `:3066-3085`), `_deletePortfolioPosition` (DELETE, `:3087-3101`), the "Dodaj pozycję" toggle button `#pp-add-toggle-btn` and CSV export `#pp-export-csv-btn` (`:3473-3474`), and the add-form.

### Summary computation (frontend) — value is client-side

- `_updatePortfolioSummary(positions)` (`static/index.html:3121-3168`): iterates positions and computes `totalValue += shares*current_price`, `totalPnl += pnl_pln`, `dailyPln += shares*current_price*(daily_change_pct/100)`. **There is no server-supplied market value** — it is derived here. This means the summary is already an aggregate over whatever position list it is handed, so passing it the merged all-portfolios list "just works". Renders into `#pp-sum-value` / `#pp-sum-pnl` / `#pp-sum-daily` (`#pp-summary` markup at `:3516-3529`).

### View-tab switching & the shared wallet strip

- `#pp-view-tabs` = **Tabela | Treemapa | Kalendarz** (`static/index.html:3467-3471`). The wallet-tabs strip `#pp-portfolio-tabs-wrap` is shown for `table` **and** `calendar` modes (`:3641`).
- **Treemapa already aggregates all portfolios** — its endpoint uses `list_user_portfolio_positions(user_id)` with no portfolio filter (`src/api.py:864`), so treemap is user-wide regardless of the active wallet.
- **Kalendarz is per-portfolio** — `fetchPortfolioCalendar` / `fetchPortfolioHistory` build URLs with `&portfolio_id=${_activePortfolioId}` and bail on `null` (`static/index.html:3881-3888`, `:3981-3991`). A sentinel `all` would hit ownership validation and 404. The "Wszystkie" calendar/value chart is out of scope (companion ticket) → plan must pick a fallback (e.g. hide Kalendarz tab, or disable it, or auto-switch to a real wallet) when "Wszystkie" is active.

### Backend: positions endpoint & DB (already all-portfolios-capable)

- `GET /api/portfolio/positions` handler (`src/api.py:700-735`): `portfolio_id: str = Query(...)` is **required**; JWT-only user via `_get_user_id` (`:154-160`); validates the wallet belongs to the user (404 "Wallet not found" otherwise, `:711-716`); returns a JSON **array** of `PortfolioPositionOut` (`:722-735`), 30s-cached under `positions:{user_id}:{portfolio_id}` (`:706-709`). No aggregation / sentinel today.
- `PortfolioPositionOut` fields (`src/api.py:308-319`): `ticker, company_name, shares, avg_buy_price, current_price, daily_change_pct, pnl_pln, pnl_pct, price_as_of, price_history`. **`portfolio_id` is intentionally dropped** (`extra="ignore"`). `pnl_pln`/`pnl_pct` are computed in the handler (`:727-732`); there is **no market-value field**.
- DB `list_user_portfolio_positions(user_id, portfolio_id=None, include_history=False)` (`db/bigquery.py:758-863`): the `portfolio_id is None` path returns **all** the user's positions — `portfolio_filter = "AND p.portfolio_id = @portfolio_id" if portfolio_id is not None else ""` (`:777`), `WHERE p.user_id = @user_id {portfolio_filter}` (`:849`). `current_price` / `daily_change_pct` are computed in SQL from `company_daily_stats` / `etf_quotes` (`:814-847`); the row **includes `portfolio_id`**, so an all-mode result can be grouped/merged.
- `GET /api/portfolio/wallets` (`src/api.py:786-795`) → `list_user_portfolios(user_id)`; rows have `user_id, portfolio_id, portfolio_type, portfolio_name, display_order, created_at` (`db/bigquery.py:868-875`), ordered by `display_order ASC, created_at ASC`.

### E2E test harness (what a "Wszystkie" test needs)

- `tests/e2e/conftest.py` fakes a **single** portfolio `_FAKE_PORTFOLIO_ID = "test-portfolio-glowny-001"` (`:291-301`) with two positions PKO/CDR (`:302-316`).
- `_fake_list_user_portfolio_positions(user_id, portfolio_id=None, include_history=False)` (`:395-414`) already mirrors production: `portfolio_id == _FAKE_PORTFOLIO_ID` → that wallet's rows; **else (incl. `None`) → all of the user's stored positions**. So the "all" path is already representable in the harness.
- To meaningfully test "Wszystkie", the fixture needs a **second** fake portfolio + positions (incl. a shared ticker to exercise the merge). Existing e2e pattern: real login via `e2e_login_email`, live_server, role-scoped locators (`tests/e2e/test_portfolio_positions.py:1-60`).

## Code References

- `static/index.html:3107` — `_activePortfolioId` state (sentinel target)
- `static/index.html:3301-3348` — `_renderPortfolioTabs` (where the "Wszystkie" tab is prepended, first + default)
- `static/index.html:3350-3391` — `fetchUserPortfolios` (default selection + URL restore)
- `static/index.html:3041-3064` — `fetchPortfolioPositions` (fetch URL to switch to `portfolio_id=all`)
- `static/index.html:3179-3236` — `_renderPortfolioTable` (per-row Edit/Delete → hide in read-only)
- `static/index.html:3121-3168` — `_updatePortfolioSummary` (client-side value/P&L/daily aggregation)
- `static/index.html:2658-2675` — `_ppPortfolioParams` / `_ppWriteUrl` (URL round-trip for `portfolio=all`)
- `static/index.html:3636-3657` — view-tab switching; `:3641` shares wallet strip with Kalendarz
- `static/index.html:3473-3474` — add-toggle & CSV export buttons (hide in read-only)
- `src/api.py:700-735` — positions endpoint (add `all` sentinel branch here)
- `src/api.py:308-319` — `PortfolioPositionOut` (no `portfolio_id`, no market value)
- `db/bigquery.py:758-863` — `list_user_portfolio_positions` (already all-mode capable at `:777`, `:849`)
- `src/api.py:864` — treemap already calls all-mode (precedent)
- `tests/e2e/conftest.py:289-414` — portfolio fakes (needs a 2nd portfolio for "Wszystkie")

## Architecture Insights

- **Decision 2 (where to aggregate) → backend `portfolio_id=all`.** The DB function is already all-capable and the treemap endpoint is a working precedent (`src/api.py:864`). A backend sentinel = one call, no client fan-out, and preserves the 30s cache (`positions:{user_id}:all`). The only handler change is: when `portfolio_id == "all"`, skip the per-wallet 404 check and call `list_user_portfolio_positions(user_id, None, include_history=True)`. Cheap and clean; client-side N-call fan-out is unnecessary given this.
- **Decision 1 (same ticker across portfolios) → merge into one row.** In all-mode the DB returns one row per (portfolio_id, ticker), so a ticker held in two wallets appears twice. Merge = sum `shares`, weighted-average `avg_buy_price`, keep the (identical) `current_price`/`daily_change_pct`/`price_history`, recompute `pnl_pln`/`pnl_pct` from summed cost. This can live **either** in the backend all-branch (return already-merged rows — keeps `PortfolioPositionOut` unchanged, no `portfolio_id` needed) **or** client-side. Merging server-side is tidier and keeps the frontend table render untouched. Recommend server-side merge in the all-branch; leave the summary math as-is (it sums correctly either way).
- **Decision 3 (read-only) → conditional render.** Gate on `_activePortfolioId === 'all'`: omit the Edytuj/Usuń buttons in `_renderPortfolioTable`, hide `#pp-add-toggle-btn` (and optionally CSV export), and early-return in `_upsertPortfolioPosition`/`_deletePortfolioPosition` as a belt-and-braces guard.
- **Sentinel choice.** A string constant (e.g. `_ALL_PORTFOLIOS = 'all'`) keeps `if (_activePortfolioId)` truthy checks working and round-trips through `?portfolio=all`. `fetchUserPortfolios` must be taught to (a) render "Wszystkie" first + default when no `?portfolio=` is present, and (b) match `?portfolio=all` back to the "Wszystkie" tab.
- **Kalendarz coupling is the sharp edge.** Because the wallet strip is shared with Kalendarz (`:3641`) and calendar/history are strictly per-wallet, the plan must define behavior when "Wszystkie" is active and Kalendarz is opened (recommend: hide/disable the Kalendarz tab in "Wszystkie" mode, since its aggregate chart is the companion ticket). Treemapa needs no change (already user-wide).

## Historical Context (from prior changes)

- PUL-79 (`context/archive/**pul-79**`) added `GET /api/portfolio/history` (value-history endpoint) and, per its plan-review, chose **LOCF forward-fill + full-coverage gate** over 0-fill to avoid false value jumps — relevant only to the out-of-scope "Wszystkie" chart.
- PUL-89 added the frontend value chart under the calendar and the `_ppHistReqSeq` out-of-order guard — the pattern to copy if/when the companion "Wszystkie" chart ticket is built.
- PUL-74 retired the anonymous `X-Client-Id` path; positions endpoints are JWT-only via `_get_user_id` (`src/api.py:154-160`).

## Related Research

- None prior for this change. Builds on PUL-79 / PUL-89 archives (value-history endpoint + chart).

## Open Questions

1. **Kalendarz in "Wszystkie" mode** — hide the Kalendarz view-tab, disable it, or auto-fall-back to a real wallet? (Recommend hide/disable; aggregate chart is the companion ticket.)
2. **Merge location** — server-side merge in the `all` branch (recommended, keeps `PortfolioPositionOut` and table render untouched) vs client-side merge. Confirm in /10x-plan.
3. **Empty state** — behavior when the user has portfolios but zero positions across all of them (summary hides at `:3124`; "Wszystkie" should show the same empty table message).
4. **Single-portfolio users** — still show "Wszystkie" first + default (ticket says default on entry), or only surface it when ≥2 portfolios exist? (Ticket implies always-first/default.)
