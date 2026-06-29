---
date: 2026-06-28T00:00:00+02:00
researcher: Radek + Claude
git_commit: bdaabaa17ffa0fa0eec66fb4b9086537cb50c23e
branch: radoslawjjd/pul-64-treemapa-portfela-for-non-admin-users-own-data-not-admins
repository: puls-gpw
topic: "Non-admin portfolio treemap — implementation research"
tags: [research, codebase, portfolio-treemap, user-portfolio-positions, company-daily-stats, auth]
status: complete
last_updated: 2026-06-28
last_updated_by: Claude
---

# Research: Non-admin portfolio treemap (PUL-64)

**Date**: 2026-06-28  
**Researcher**: Radek + Claude  
**Git Commit**: `bdaabaa`  
**Branch**: `radoslawjjd/pul-64-treemapa-portfela-for-non-admin-users-own-data-not-admins`  
**Repository**: puls-gpw

## Research Question

What needs to be built so that non-admin users see a "Treemapa portfela" showing their own
portfolio (from `user_portfolio_positions`) — and what reusable pieces already exist?

## Summary

All four blockers from `frame.md` are resolved by PUL-65 (merged) and PUL-54 (merged):

| Blocker | Resolution |
|---|---|
| Per-user data model | `user_portfolio_positions` table + CRUD (PUL-65, `db/bigquery.py:382-530`) |
| Price feed | `company_daily_stats.kurs_zamkniecia` (PUL-54, `db/bigquery.py:1281-1325`) |
| Input/UI surface | Portfolio positions form + table shipped in PUL-65 (`src/api.py:397-450`) |
| JOIN between both | `list_user_portfolio_positions()` already LEFT JOINs with `company_daily_stats` (`db/bigquery.py:491-530`) |

PUL-64 boils down to **one new API endpoint + one new frontend section**:
- `GET /api/portfolio/treemap` — new non-admin endpoint that computes treemap positions from user data
- Frontend: treemap toggle/view inside the existing "Mój portfel" portfolio-positions view

**Critical insight**: do NOT reuse `compute_treemap_positions()` — it expects `portfolio_snapshots`
JSON strings (XTB upload format). For non-admin, compute directly from `list_user_portfolio_positions()` output.

## Detailed Findings

### Backend: auth gating pattern

**File**: `src/api.py:53-75`

Auth is via `X-API-Key` header compared to `ADMIN_API_KEY` / `USER_API_KEY` env vars:

```python
_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)     # line 53
_CLIENT_ID_HEADER = APIKeyHeader(name="X-Client-Id", auto_error=False)  # line 54

def _get_role(key=Security(_API_KEY_HEADER)) -> Role:   # lines 58-63
    if key == os.environ.get("ADMIN_API_KEY"): return "admin"
    if key == os.environ.get("USER_API_KEY"):  return "user"
    raise HTTPException(401, "Invalid or missing API key")

def _require_admin(role=Depends(_get_role)) -> Role:    # lines 66-69
    if role != "admin": raise HTTPException(403, "Admin access required")
    return role

def _get_client_id(client_id=Security(_CLIENT_ID_HEADER)) -> str:  # lines 72-75
    if not client_id: raise HTTPException(400, "Missing X-Client-Id header")
    return client_id
```

**Pattern for non-admin authenticated endpoint** — use `Depends(_get_role)` + `Depends(_get_client_id)`.
No `_require_admin`. Example: `GET /watchlist` at line 238.

No JWT — the same `X-Client-Id` UUID identifies the user across all per-user tables.

### Backend: existing admin treemap endpoint

**File**: `src/api.py:325-354`

```python
@app.get("/admin/portfolio/treemap")
async def admin_portfolio_treemap(role: Role = Depends(_require_admin)):
    result = {}
    for wallet in _TREEMAP_WALLETS:  # ["main", "ikze"]
        latest = get_latest_snapshot_for_wallet(wallet)
        ...
        positions = compute_treemap_positions(
            latest["positions_json"],
            prior["positions_json"] if prior else None,
            latest["total_value"],
        )
        result[wallet] = [TreemapPosition(**p).model_dump() for p in positions]
    result["as_of"] = ...
    return result
```

Reads from `portfolio_snapshots` (XTB upload table). **Non-admin endpoint must NOT touch this.**

### Backend: compute_treemap_positions() — NOT for PUL-64

**File**: `src/portfolio_treemap.py:4-73`

Takes `today_positions_json: str` and `yesterday_positions_json: str | None` —
**strings in `portfolio_snapshots` format** (`{"positions": [{"ticker", "value", "pct"}]}`).
This function is designed for the XTB upload data model. For PUL-64 we compute directly.

**Output format** (same fields needed by `renderTreemap()`):
```python
{
    "ticker": str,
    "position_value_pln": float,
    "daily_change_pln": float | None,
    "daily_change_pct": float | None,
    "portfolio_share_pct": float | None,
    "since_purchase_pct": float | None,
    "since_purchase_pln": float | None,
}
```

### Backend: user_portfolio_positions table + JOIN

**File**: `db/bigquery.py:382-530`

Schema (columns): `user_id (STRING)`, `ticker`, `company_name`, `shares (FLOAT64)`,
`avg_buy_price (FLOAT64)`, `created_at`, `updated_at`.

Note: column is `user_id` (not `client_id` like watchlist uses) — but same UUID value
is passed from `X-Client-Id` header via `_get_client_id()`.

`list_user_portfolio_positions(user_id)` at `db/bigquery.py:491-530` already does:

```sql
WITH latest_stats AS (
  SELECT ticker, kurs_zamkniecia, zmiana_procentowa,
         CAST(snapshot_date AS STRING) AS price_as_of,
         ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC) AS rn
  FROM company_daily_stats
)
SELECT p.ticker, p.company_name, p.shares, p.avg_buy_price,
       ls.kurs_zamkniecia AS current_price,
       ls.zmiana_procentowa AS daily_change_pct,
       ls.price_as_of
FROM user_portfolio_positions p
LEFT JOIN latest_stats ls ON p.ticker = ls.ticker AND ls.rn = 1
WHERE p.user_id = @user_id
```

**Returns per row**: `ticker`, `company_name`, `shares`, `avg_buy_price`,
`current_price` (nullable), `daily_change_pct` (nullable), `price_as_of` (nullable).

### Backend: company_daily_stats price column

**File**: `db/bigquery.py:1281-1305`

Closing price column name: **`kurs_zamkniecia`** (FLOAT64, NULLABLE).
Daily change: **`zmiana_procentowa`** (FLOAT64, NULLABLE — % vs. previous close).
Table is partitioned by `snapshot_date` (DAY) and clustered by `ticker`.

### Backend: field computation for non-admin treemap

From `list_user_portfolio_positions()` output, all required fields computable:

```python
current_price = row["current_price"]          # kurs_zamkniecia — may be None
shares        = row["shares"]
avg_buy_price = row["avg_buy_price"]
d_pct         = row["daily_change_pct"]       # zmiana_procentowa — may be None

position_value_pln = shares * current_price if current_price else None
daily_change_pct   = d_pct                    # same % as stock (position doesn't change)
daily_change_pln   = (
    position_value_pln * d_pct / 100 / (1 + d_pct / 100)
    if position_value_pln and d_pct else None
)
since_purchase_pct = (
    (current_price / avg_buy_price - 1) * 100
    if current_price and avg_buy_price else None
)
since_purchase_pln = (
    (current_price - avg_buy_price) * shares
    if current_price else None
)
# portfolio_share_pct computed after summing total_value across all positions
```

Positions with `current_price=None` still appear (LEFT JOIN) but are rendered
as `treemap-cell no-data` (frontend already handles null gracefully).

### Backend: existing API endpoints (PUL-65)

**File**: `src/api.py:397-450`

```
GET    /api/portfolio/positions    → list_user_portfolio_positions(client_id) + pnl compute
POST   /api/portfolio/positions    → upsert_user_portfolio_position(client_id, ...)
DELETE /api/portfolio/positions/{ticker} → delete_user_portfolio_position(client_id, ticker)
```

Auth: `Depends(_get_role)` + `Depends(_get_client_id)` (both user and admin can call).

### Frontend: admin treemap injection (reference)

**File**: `static/index.html`

Admin treemap is dynamically injected by `injectAdminOnlyChrome()` (line ~985):
```javascript
if (r !== 'admin') return;   // early exit for non-admin
// ... creates #treemap-btn nav item and #treemap-view div
```

`fetchTreemap()` (line ~1680) calls `/admin/portfolio/treemap` with `X-API-Key` only.

`renderTreemap(data, container)` (line ~1718) is a **pure renderer** — takes an array
of position objects and a DOM container. **Fully reusable for non-admin treemap.**

Data format expected by `renderTreemap()`:
```javascript
[{
  ticker: str,
  position_value_pln: number,      // used for cell sizing
  daily_change_pln: number | null,
  daily_change_pct: number | null,
  portfolio_share_pct: number | null,
  since_purchase_pln: number | null,
  since_purchase_pct: number | null
}]
```

CSS classes applied: `.treemap-cell.positive|negative|neutral|no-data` (already in stylesheet).
Layout: `computeTreemapLayout()` from `static/js/treemap-layout.js` (squarify algorithm).

### Frontend: PUL-65 portfolio positions view

**File**: `static/index.html` (on master, merged from PUL-65)

Nav item: `#nav-portfolio-positions-btn` (data-view="portfolio-positions")
View container: `#portfolio-positions-view`
Table body: `#pp-tbody`

Columns rendered: ticker, company_name, shares, avg_buy_price, current_price, daily_change_pct, pnl_pln/pnl_pct.

`fetchPortfolioPositions()` calls `GET /api/portfolio/positions` with both
`X-API-Key` and `X-Client-Id` headers.

### Frontend: client_id mechanism

**File**: `static/index.html:690-699`

```javascript
function initClientId() {
  clientId = localStorage.getItem('watchlist_client_id');  // same key as watchlist
  if (!clientId) {
    clientId = crypto.randomUUID();
    localStorage.setItem('watchlist_client_id', clientId);
  }
}
```

Same UUID flows to watchlist AND portfolio positions endpoints via `X-Client-Id` header.

### Tests: existing treemap coverage

**Unit tests** (`tests/test_portfolio_treemap.py:1-179`) — 13 tests for `compute_treemap_positions()`.
A new function for non-admin will need its own parallel unit tests.

**Integration tests** (`tests/test_api.py:273-421`) — 10 tests for `GET /admin/portfolio/treemap`.
Pattern: mock `get_latest_snapshot_for_wallet` + `get_latest_snapshot_before` with `side_effect`.

**E2E tests** (`tests/e2e/test_portfolio_treemap.py:1-166`) — 10 tests covering admin treemap.
Relevant for non-admin: cell rendering, click popup, URL param, resize reflow patterns.

### Tests: conftest.py and what needs adding

**File**: `tests/e2e/conftest.py`

Currently mocked in `live_server_url` fixture (lines 208-222):
- `get_latest_snapshot_for_wallet`, `get_latest_snapshot_before` (treemap)
- `list_watchlist_tickers`, `add_watchlist_ticker`, `remove_watchlist_ticker`
- `list_announcements_for_watchlist`
- `create_watchlist_table_if_not_exists`, `ensure_watchlist_schema_current`
- `create_companies_table_if_not_exists`, `ensure_companies_schema_current`

**After master merge**: PUL-65 should have added portfolio position mocks.
**Verify** whether these are already present: `list_user_portfolio_positions`,
`upsert_user_portfolio_position`, `delete_user_portfolio_position`,
`create_user_portfolio_positions_table_if_not_exists`, `ensure_user_portfolio_positions_schema_current`.
If not present, they must be added for E2E to work.

## Code References

- `src/api.py:53-75` — auth dependencies (_get_role, _require_admin, _get_client_id)
- `src/api.py:325-354` — GET /admin/portfolio/treemap (admin-only, reference)
- `src/api.py:397-450` — GET/POST/DELETE /api/portfolio/positions (PUL-65)
- `src/portfolio_treemap.py:4-73` — compute_treemap_positions() — NOT reused for PUL-64
- `db/bigquery.py:382-407` — user_portfolio_positions schema + table creation
- `db/bigquery.py:413-530` — CRUD: upsert/delete/list_user_portfolio_positions
- `db/bigquery.py:491-530` — list_user_portfolio_positions() with LEFT JOIN to company_daily_stats
- `db/bigquery.py:1281-1325` — company_daily_stats schema (kurs_zamkniecia = closing price)
- `static/index.html:690-699` — initClientId() localStorage mechanism
- `static/index.html:985-1090` — injectAdminOnlyChrome() (admin-only; PUL-64 bypasses this)
- `static/index.html:1680-1698` — fetchTreemap() (admin, reference only)
- `static/index.html:1718-1763` — renderTreemap(data, container) — reusable for PUL-64
- `static/js/treemap-layout.js:72-77` — computeTreemapLayout() — reusable for PUL-64
- `tests/e2e/conftest.py:202-238` — live_server_url fixture with BQ mocks
- `tests/e2e/test_portfolio_treemap.py:1-166` — E2E patterns (admin, adapt for non-admin)
- `tests/test_portfolio_treemap.py:1-179` — unit tests for compute_treemap_positions()
- `tests/test_api.py:273-421` — integration tests for admin treemap endpoint

## Architecture Insights

### Non-admin treemap: do NOT use compute_treemap_positions()

That function is an adapter for `portfolio_snapshots` JSON strings (XTB upload format).
For non-admin treemap, compute all fields directly from `list_user_portfolio_positions()` rows —
the LEFT JOIN already gives us current_price, daily_change_pct, and everything else is arithmetic.

### Single wallet (vs. admin's two wallets)

Admin treemap returns `{main: [...], ikze: [...], as_of: ...}`.
Non-admin has one portfolio — response shape: `{positions: [...], as_of: "YYYY-MM-DD" | null}`.
Frontend renders one container, not two side-by-side.

### Reuse renderTreemap() and computeTreemapLayout()

Both are pure functions. `renderTreemap(positions, container)` just needs the same
position object shape — no changes required. Same CSS classes apply.

### Frontend integration: tab inside "Mój portfel"

PUL-65 created `#portfolio-positions-view`. Adding a "Widok / Treemapa" tab switcher
inside that view is the cleanest path — same data, two presentations. Avoids
adding a third nav item for authenticated users (table and treemap are one feature).

### Branch prerequisite

PUL-64 branch predates PUL-65 merge. **Merge master before starting implementation.**
After merge: verify conftest has portfolio position mocks; if not, add them as first task.

## Historical Context

- `context/archive/2026-06-20-admin-ui-portfolio-treemap/plan.md` — PUL-45: original admin treemap
  (phases: compute_treemap_positions pure function → endpoint → frontend)
- `context/archive/2026-06-20-portfolio-treemap-multi-wallet/plan.md` — PUL-50: added
  second wallet, portfolio_share_pct, since_purchase fields, responsive layout

## Supplementary Research (2026-06-28) — Multi-wallet expansion

Scope of PUL-64 expanded to include multi-wallet portfolio management.
Full new scope: `user_portfolios` table + wallet types (Główny/IKZE/IKE/PPK/PPE/Inny)
+ treemapa for all roles using user_portfolio_positions (replaces portfolio_snapshots path).

### Edit modal pattern (from ui/portfolio-edit-modal, now on master)

**File**: `static/index.html:1889-2047`

Overlay pattern: `#pp-edit-overlay` (`role="dialog"`, `aria-modal="true"`) contains
`#pp-edit-modal`. Open: `_ppOpenEditModal()` sets `display = ''` + focuses first input.
Close: `_ppCloseEditModal()` sets `display = 'none'`. Save button calls
`_upsertPortfolioPosition()` after closing modal. Same pattern for "Dodaj portfel" modal.

### ensure_schema_current() — adding nullable columns to existing BQ tables

**File**: `db/bigquery.py:144-176`

```python
def ensure_schema_current(table_name, schema) -> None:
    # fetches table, finds missing columns, appends them, calls update_table
    table.schema = table.schema + missing   # line 168
    client.update_table(table, ["schema"])   # line 170
```

Adding `portfolio_id` (NULLABLE STRING) to `user_portfolio_positions` via this function
is safe — existing rows get NULL, no data loss. REQUIRED columns cannot be added this way.

### Navigation routing pattern (from ui/portfolio-edit-modal, now on master)

**File**: `static/index.html`

Full pattern to add a new view:
1. HTML button `id="nav-<view>-btn"` + `data-view="<view>"` (line 597 — portfolio-positions example)
2. Click listener: `$('nav-<view>-btn').addEventListener('click', () => _navigateToView('<view>'))` (line 916)
3. Container div: `<div id="<view>-view" style="display:none">` (line 652)
4. `_navigateToView()` dispatcher (lines 1360-1376) — add `else if (view === '<view>')`
5. `show<View>View()` function — hides all other views, lazy-builds DOM once via
   `_build<View>ViewContent()` if `!ppView.dataset.built`, calls fetch (lines 2042-2057)
6. `_applyUrlState()` — add case for URL deep-linking (lines 1423-1445)

**Key**: `showPortfolioPositionsView()` uses lazy DOM build pattern (`dataset.built = '1'`)
and calls `_setActiveNavItem('portfolio-positions')` for nav highlight.

Current state: `_applyUrlState()` does NOT yet have `portfolio-positions` case — must add.

Admin treemap button is in `injectAdminOnlyChrome()` (lines 985-1090, guard `if (r !== 'admin') return`).
For multi-wallet, treemap button moves to common code (injected for ALL roles).

### Portfolio positions toggle pattern

**File**: `static/index.html:2002-2016`

`#pp-add-toggle-btn` click → shows `#pp-form-wrap`, hides button.
`_ppResetToAddMode()` reverses it. Same pattern for "Dodaj portfel" button.

### Data model decisions (finalized in planning session 2026-06-28)

**New table: `user_portfolios`**
```
user_id       STRING   REQUIRED  — same X-Client-Id UUID as positions
portfolio_id  STRING   REQUIRED  — UUID generated server-side
portfolio_type STRING  REQUIRED  — "glowny"|"ikze"|"ike"|"ppk"|"ppe"|"inny"
portfolio_name STRING  NULLABLE  — custom name for "inny" type only
display_order INTEGER  REQUIRED  — fixed per type: glowny=1, ikze=2, ike=3, inny=4/5, ppk=6, ppe=7
created_at    TIMESTAMP REQUIRED
```

**Migration: `user_portfolio_positions`**
- Add `portfolio_id` (STRING, NULLABLE) via `ensure_schema_current()`
- Existing rows get NULL portfolio_id → treated as "Główny" until user creates that portfolio
- upsert MERGE key changes from `(user_id, ticker)` → `(portfolio_id, ticker)` (breaking change)
- Same ticker CAN appear in multiple portfolios (e.g., PKO in Główny AND in IKZE)

**Constraints (enforced at API layer)**:
- Główny/IKZE/IKE/PPK/PPE: max 1 of each type per user_id
- Inny: max 2 per user_id
- display_order fixed map: glowny→1, ikze→2, ike→3, inny→4 or 5 (by created_at), ppk→6, ppe→7

**Migration UX**: When user creates "Główny" portfolio, server auto-assigns all existing
NULL-portfolio_id positions to it via UPDATE query.

## Resolved Open Questions

1. **Multi-wallet**: YES — Główny, IKZE, IKE, PPK, PPE, Inny (max 2). One treemapa
   container per portfolio, side-by-side same as admin treemap.
2. **Position with no price**: Excluded from treemapa layout (computeTreemapLayout filters
   position_value_pln ≤ 0) + notice "Brak aktualnej ceny dla: X, Y" shown above treemapa.
3. **conftest after master merge**: PUL-65 added portfolio positions mocks to conftest.
   After merge, verify; add user_portfolios mocks in Phase 1.
4. **Data source for all roles**: user_portfolio_positions + company_daily_stats.
   GET /admin/portfolio/treemap stays in code but stops being called from frontend.
