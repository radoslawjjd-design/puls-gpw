# "Wszystkie" Aggregate View in Mój portfel — Implementation Plan

## Overview

Add a default, first-listed **"Wszystkie"** entry to the Mój portfel wallet selector that aggregates every portfolio the user owns. In this mode the Tabela lists positions merged across all portfolios (same ticker → one row), the Summary sums value / P&L / daily change, and — per the planning decision — the **Kalendarz grid and the value-history chart also aggregate across all portfolios** so the user sees how their combined holdings behaved each day. The mode is read-only (positions belong to a specific wallet).

Aggregation is done on the **backend** via a `portfolio_id=all` sentinel on the three per-wallet endpoints (`positions`, `calendar`, `history`), reusing the DB layer's already-present "no portfolio filter → all of the user's positions" capability.

## Current State Analysis

- Mój portfel is always scoped to one wallet via `_activePortfolioId` (a portfolio-id string or `null`), set from the wallet tabs (`static/index.html:3301-3348`) or the `?portfolio=` URL param (`:3350-3391`).
- Positions: `fetchPortfolioPositions()` GETs `/api/portfolio/positions?portfolio_id=<id>` (`:3041-3064`); `_renderPortfolioTable` renders per-row **Edytuj/Usuń** buttons (`:3179-3236`) and calls `_updatePortfolioSummary` (`:3121-3168`), which computes value/P&L/daily **client-side** from `shares × current_price` — so it already aggregates any position list correctly.
- The wallet-tabs strip `#pp-portfolio-tabs-wrap` is shared by the **Tabela** and **Kalendarz** views (`:3641`). Treemapa already renders all wallets side-by-side (`src/api.py:864-886`), so it is already user-wide and needs no change.
- Backend positions endpoint requires `portfolio_id` (`src/api.py:700-735`), validates wallet ownership (404 otherwise, `:715-716`), computes `pnl_pln`/`pnl_pct` per row (`:727-732`), 30s-cached under `positions:{user_id}:{portfolio_id}`.
- **DB already supports all-portfolios**: `list_user_portfolio_positions(user_id, portfolio_id=None)` drops the portfolio filter and keeps `user_id` (`db/bigquery.py:758-863`, filter at `:777`/`:849`); treemap relies on this. Rows include `portfolio_id`.
- Calendar/history DB functions **require** `portfolio_id` and filter on it: `get_portfolio_calendar_data` (`db/bigquery.py:362-457`, filter `:403`) and `get_portfolio_history` (`:460-…`, filter `:498`). Their handlers validate ownership (403) and cache under keys containing `portfolio_id` (`src/api.py:900-969`).

## Desired End State

Entering Mój portfel selects **"Wszystkie"** first by default. Tabela shows one merged row per ticker across all wallets; Summary shows summed value / combined P&L / combined daily change; no Edytuj/Usuń/Dodaj-pozycję controls. Switching to Kalendarz keeps "Wszystkie" active and shows a combined daily P&L grid + a combined value-history chart. Clicking an individual wallet tab scopes everything back to that one portfolio and restores editing. `?portfolio=all` round-trips through the URL. Light + dark correct, no console errors, no new deps.

### Key Discoveries:

- `list_user_portfolio_positions(user_id, None)` already returns all-user positions (`db/bigquery.py:777`) — positions all-mode needs **no DB change**, only a handler branch + a same-ticker merge.
- Calendar/history need a small DB change: make `portfolio_id` optional and drop the filter when `None` — the CTE `positions` then spans all wallets and the daily `SUM(...)` becomes the combined figure automatically (`db/bigquery.py:400-430`, `:495-499`).
- Summary math is client-side and list-agnostic (`static/index.html:3126-3140`) — merged list "just works".
- `PortfolioPositionOut` has `extra="ignore"` (`src/api.py:308-309`) — passing a merged row that still carries `portfolio_id` is safe (dropped).
- Sentinel `"all"` cannot collide with real portfolio_ids (UUID-like); keeps `if (_activePortfolioId)` truthy checks working.

## What We're NOT Doing

- No treemap change (already user-wide / per-wallet tiles).
- No new "portfel source" column in the merged table; same-ticker rows are merged, not labeled per wallet.
- No editing/add/delete of positions in "Wszystkie"; "Dodaj portfel" and "Eksport CSV" remain available.
- No schema/table changes, no new dependencies.
- No purchase-date tranche accuracy work (history keeps the documented today's-share-counts approximation).

## Implementation Approach

Backend-first. Introduce a single `_ALL_PORTFOLIOS = "all"` sentinel. On `positions`, `calendar`, and `history` handlers: when `portfolio_id == "all"`, skip the per-wallet ownership check and pass `None` to the DB layer. For positions, merge the all-mode rows by ticker (summed shares, weighted-average buy price) before the existing per-row P&L computation. For calendar/history, make the DB functions accept `portfolio_id: str | None` and apply the filter conditionally. Then the frontend adds the "Wszystkie" tab (first + default), routes the sentinel through existing fetches, and hides edit controls in that mode. Finally extend the e2e harness with a second portfolio + a shared ticker and add coverage.

## Critical Implementation Details

- **Weighted-average buy price on merge**: `avg_buy_price = Σ(shares_i × avg_buy_price_i) / Σ(shares_i)`; `shares = Σ(shares_i)`. `current_price`, `daily_change_pct`, `price_as_of`, `price_history`, `company_name` are identical across wallets for a given ticker (same market-data scan) — take the first non-null. P&L is then recomputed by the existing handler loop from the merged `current_price`/`avg_buy_price`/`shares`, so it stays correct.
- **Calendar/history combine for free**: dropping the `portfolio_id` filter makes the `positions` CTE union all wallets; the existing `SUM(shares × …)` per `snapshot_date` becomes the combined daily value/change with no other query change.

---

## Phase 1: Backend — `portfolio_id=all` sentinel across positions / calendar / history + same-ticker merge

### Overview

Teach the three per-wallet endpoints to accept `portfolio_id="all"` and return user-wide aggregates, merging same-ticker positions.

### Changes Required:

#### 1. DB: optional portfolio filter on calendar + history

**File**: `db/bigquery.py`

**Intent**: Let `get_portfolio_calendar_data` and `get_portfolio_history` compute a combined-across-all-wallets series when no specific portfolio is requested, mirroring how `list_user_portfolio_positions` already treats `portfolio_id=None`.

**Contract**: Change both signatures to `portfolio_id: str | None = None`. In each, build the positions-CTE filter conditionally — `"AND portfolio_id = @portfolio_id"` only when `portfolio_id is not None`, else `""` — and bind the `portfolio_id` query parameter only in that case (same pattern as `db/bigquery.py:777` / `:855-856`). No other query logic changes; the daily `SUM(...)` aggregation already produces the combined figure.

#### 2. API: sentinel handling + positions merge

**File**: `src/api.py`

**Intent**: Add an `_ALL_PORTFOLIOS = "all"` constant and branch each of the three handlers on it: skip the per-wallet ownership check (404/403) and pass `None` to the DB layer. For positions, merge same-ticker rows before the existing P&L loop.

**Contract**:
- `_ALL_PORTFOLIOS = "all"` module-level constant.
- `get_portfolio_positions` (`src/api.py:700-735`): when `portfolio_id == _ALL_PORTFOLIOS`, skip the `list_user_portfolios` ownership check, call `list_user_portfolio_positions(user_id, None, include_history=True)`, then pass rows through a new `_merge_positions_by_ticker(rows)` helper before the existing `for row in rows` P&L loop. Cache key stays `positions:{user_id}:all` (unchanged f-string). Non-`all` path is untouched.
- `_merge_positions_by_ticker(rows: list[dict]) -> list[dict]`: groups by `ticker`, sums `shares`, computes weighted-average `avg_buy_price`, carries first non-null `current_price`/`daily_change_pct`/`price_as_of`/`price_history`/`company_name`. Returns rows shaped like `list_user_portfolio_positions` output (so the existing loop + `PortfolioPositionOut(**row, …)` works unchanged).
- `get_portfolio_calendar` (`src/api.py:900-932`) and `get_portfolio_value_history` (`:934-969`): when `portfolio_id == _ALL_PORTFOLIOS`, skip the ownership check and call the DB function with `None`. Cache keys unchanged (already interpolate `portfolio_id`, becoming `…:all:…`).

### Success Criteria:

#### Automated Verification:

- [ ] Unit tests pass: `uv run pytest tests/test_api.py`
- [ ] `_merge_positions_by_ticker` unit test: two wallets holding the same ticker merge to one row with summed shares and weighted-avg buy price; distinct tickers stay separate.
- [ ] Positions all-mode branch test (mocked db): `portfolio_id=all` returns merged rows and does **not** raise 404 even though "all" is not a real wallet.
- [ ] Calendar + history all-mode branch tests (mocked db): `portfolio_id=all` bypasses the 403 ownership check and calls the DB function with `portfolio_id=None`.
- [ ] Full suite green: `uv run pytest`

#### Manual Verification:

- [ ] `curl` (JWT) `GET /api/portfolio/positions?portfolio_id=all` returns one merged row per ticker across wallets.
- [ ] `GET /api/portfolio/calendar?...&portfolio_id=all` and `GET /api/portfolio/history?range=3m&portfolio_id=all` return combined series without 403.

**Implementation Note**: After automated verification passes, pause for manual confirmation before Phase 2.

---

## Phase 2: Frontend — "Wszystkie" tab (first + default), aggregate views, read-only

### Overview

Surface the aggregate mode in the wallet selector and route the sentinel through the existing fetches; hide editing in that mode.

### Changes Required:

#### 1. "Wszystkie" tab, default selection, URL round-trip

**File**: `static/index.html`

**Intent**: Render "Wszystkie" as the first wallet tab (no delete icon), select it by default on entry, and round-trip `?portfolio=all`.

**Contract**:
- Add an `_ALL_PORTFOLIOS = 'all'` JS constant near `_activePortfolioId` (`static/index.html:3107`).
- `_renderPortfolioTabs` (`:3301-3348`): prepend a "Wszystkie" `.pp-portfolio-tab` (no `.pp-tab-del-icon`); its click handler sets `_activePortfolioId = _ALL_PORTFOLIOS`, clears `_ppCalData`/`_ppHistData`, refetches positions (and calendar+history when in calendar mode), and writes the URL — mirroring the existing per-wallet handler. Mark it `.active` when `_activePortfolioId === _ALL_PORTFOLIOS`; ensure exactly one tab is active.
- `fetchUserPortfolios` (`:3350-3391`): default `_activePortfolioId` to `_ALL_PORTFOLIOS` when no `?portfolio=` match (instead of `data[0]`); treat `?portfolio=all` as selecting the "Wszystkie" tab. Always fetch positions on load (the `if (_activePortfolioId)` guard already passes for `'all'`).

#### 2. Read-only rendering + write guards

**File**: `static/index.html`

**Intent**: In "Wszystkie" mode, omit per-row edit controls and the add-position toggle, and hard-guard the write functions.

**Contract**:
- `_renderPortfolioTable` (`:3179-3236`): when `_activePortfolioId === _ALL_PORTFOLIOS`, render rows **without** the Edytuj/Usuń action cell (and skip wiring those handlers). The ticker-link and sparkline stay.
- Hide `#pp-add-toggle-btn` in all-mode (in `_renderPortfolioTabs`/render path where its visibility is set, `:3307-3312`).
- `_upsertPortfolioPosition` (`:3066`) and `_deletePortfolioPosition` (`:3087`): early-return when `_activePortfolioId === _ALL_PORTFOLIOS` (belt-and-braces).

#### 3. Aggregate calendar + history fetches

**File**: `static/index.html`

**Intent**: Ensure the calendar grid and value chart fetch the aggregate when "Wszystkie" is active.

**Contract**: `fetchPortfolioCalendar` (`:3881-`) and `fetchPortfolioHistory` (`:3981-`) already interpolate `_activePortfolioId` into the URL — confirm they send `portfolio_id=all` and drop the `_activePortfolioId === null` early-return only for the null case (sentinel `'all'` is truthy, so no code change is expected; verify no guard rejects it).

### Success Criteria:

#### Automated Verification:

- [ ] No JS syntax regressions: existing e2e positions/calendar suites still pass locally (`uv run pytest tests/e2e/test_portfolio_positions.py tests/e2e/test_portfolio_calendar.py`).

#### Manual Verification:

- [ ] On entering Mój portfel, "Wszystkie" is first and selected; Tabela shows merged positions from all wallets; Summary shows summed value/P&L/daily.
- [ ] No Edytuj/Usuń buttons and no "Dodaj pozycję" in "Wszystkie"; clicking an individual wallet restores them and scopes to that wallet.
- [ ] Kalendarz in "Wszystkie" shows a combined daily grid + combined value chart; range switch works.
- [ ] `?portfolio=all` in the URL restores the "Wszystkie" tab on reload; light + dark correct; no console errors.

**Implementation Note**: After automated verification passes, pause for manual confirmation before Phase 3.

---

## Phase 3: E2E coverage + test fixtures

### Overview

Extend the e2e harness with a second portfolio and a shared ticker, and add a "Wszystkie" browser test.

### Changes Required:

#### 1. Fixtures: second portfolio + shared ticker + all-mode fakes

**File**: `tests/e2e/conftest.py`

**Intent**: Make the fake data represent ≥2 portfolios (with an overlapping ticker to exercise the merge) and make the calendar/history/positions fakes answer the `all` sentinel.

**Contract**:
- Add a second entry to `_FAKE_PORTFOLIOS` (`:292-301`, e.g. an `ikze` wallet) and seed positions for it in the positions store, including one ticker also held in the główny wallet.
- `_fake_list_user_portfolio_positions` (`:395-414`): the `portfolio_id in (None,)` / non-matching branch already returns all user positions — confirm it returns both wallets' rows so the real handler's merge runs.
- `_fake_get_portfolio_calendar_data` (`:346-349`) and `_fake_get_portfolio_history` (`:359-362`): return combined rows when `portfolio_id` is `None` (all-mode call), not just for `_FAKE_PORTFOLIO_ID`.

#### 2. E2E test

**File**: `tests/e2e/test_portfolio_positions.py` (or a new `test_portfolio_all_view.py`)

**Intent**: Verify the aggregate view end-to-end.

**Contract**: Following the existing pattern (`e2e_login_email`, role/`get_by_*` locators, `#pp-tbody` assertions, `tests/e2e/test_portfolio_positions.py:1-60`): on entry "Wszystkie" is active; the merged table contains the shared ticker exactly once with summed shares; `#pp-summary` is visible; no "Edytuj"/"Usuń" buttons exist in the table; clicking an individual wallet tab shows edit controls again. Unique-id/cleanup discipline per the E2E rules.

### Success Criteria:

#### Automated Verification:

- [ ] New/updated e2e test passes: `uv run pytest tests/e2e/test_portfolio_positions.py` (or `test_portfolio_all_view.py`).
- [ ] Full suite green: `uv run pytest`

#### Manual Verification:

- [ ] E2E run shows the merged shared-ticker row and the read-only aggregate view as expected.

---

## Testing Strategy

### Unit Tests:

- `_merge_positions_by_ticker`: weighted-avg buy price, summed shares, null-price carry, single-ticker passthrough.
- Handler all-mode branches (positions/calendar/history) with mocked db: no ownership 404/403, DB called with `None`.

### Integration / E2E Tests:

- "Wszystkie" default selection, merged table, summed summary, read-only controls, wallet-tab scope-back.

### Manual Testing Steps:

1. Log in with a user owning ≥2 wallets sharing a ticker; open Mój portfel → "Wszystkie" is default; verify merged row + summed summary.
2. Confirm no edit/add controls; switch to a wallet tab → controls return, scope narrows.
3. Open Kalendarz in "Wszystkie" → combined grid + combined chart; switch range.
4. Reload with `?portfolio=all` → "Wszystkie" restored. Toggle dark mode; check console.

## Performance Considerations

All-mode positions fetch is one call; the merge is O(positions). Calendar/history queries scan the same tables with a wider `positions` CTE (all wallets) — negligible for realistic wallet counts. Existing 30s/300s caches apply under `…:all` keys.

## Migration Notes

None — no schema or data changes.

## References

- Research: `context/changes/pul-90-wszystkie-aggregate/research.md`
- Positions endpoint / merge site: `src/api.py:700-735`
- DB all-mode precedent: `db/bigquery.py:758-863` (filter `:777`); treemap `src/api.py:864-886`
- Calendar/history DB: `db/bigquery.py:362-457`, `:460-…`; handlers `src/api.py:900-969`
- Frontend tabs/render/summary: `static/index.html:3107-3391`, `:3121-3168`, `:3179-3236`
- E2E harness: `tests/e2e/conftest.py:289-414`, `tests/e2e/test_portfolio_positions.py`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Backend — portfolio_id=all sentinel + merge

#### Automated

- [x] 1.1 Unit tests pass: `uv run pytest tests/test_api.py`
- [x] 1.2 `_merge_positions_by_ticker` merge unit test (summed shares + weighted-avg)
- [x] 1.3 Positions all-mode branch test: merged rows, no 404
- [x] 1.4 Calendar + history all-mode branch tests: no 403, db called with None
- [x] 1.5 Full suite green: `uv run pytest` (593 passed; e2e excluded — browser-dependent, Phase 3)

#### Manual

- [x] 1.6 `curl` positions?portfolio_id=all returns merged rows (verified on real BQ: 11+7 wallets → 12 merged rows, shares sums exact)
- [x] 1.7 `curl` calendar/history ?portfolio_id=all return combined series without 403 (verified: calendar 200/18 days, history 200/20 pts)

### Phase 2: Frontend — "Wszystkie" tab, default, aggregate views, read-only

#### Automated

- [ ] 2.1 Existing e2e positions/calendar suites still pass locally

#### Manual

- [ ] 2.2 "Wszystkie" first + default; merged table + summed summary
- [ ] 2.3 No edit/add controls in all-mode; wallet tab restores them + scopes
- [ ] 2.4 Kalendarz all-mode: combined grid + combined chart; range switch works
- [ ] 2.5 `?portfolio=all` round-trips on reload; light+dark ok; no console errors

### Phase 3: E2E coverage + fixtures

#### Automated

- [ ] 3.1 New/updated e2e "Wszystkie" test passes
- [ ] 3.2 Full suite green: `uv run pytest`

#### Manual

- [ ] 3.3 E2E run shows merged shared-ticker row + read-only aggregate view
