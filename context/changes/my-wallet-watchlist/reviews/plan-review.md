<!-- PLAN-REVIEW-REPORT -->
# Plan Review: My Wallet — Personal Watchlist

- **Plan**: context/changes/my-wallet-watchlist/plan.md
- **Mode**: Deep
- **Date**: 2026-06-22
- **Verdict**: REVISE
- **Findings**: 1 critical, 3 warnings, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | FAIL |
| Plan Completeness | WARNING |

## Grounding

7/7 paths ✓, 6/6 symbols ✓, brief↔plan ✓

## Findings

### F1 — New startup hook will break the e2e test server

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 2, item 4 — Startup table creation
- **Detail**: `create_app()` has 3 call sites: `api_main.py:19` (prod), `tests/test_api.py:28` (plain `TestClient`, no `with` block — lifespan never fires), and `tests/e2e/conftest.py:182-186` — a real `uvicorn.Server` whose lifespan genuinely executes. That fixture already patches 7 `src.api.*` functions specifically to keep the e2e server BigQuery-free (`tests/e2e/conftest.py:174-181`). The plan's new `@app.on_event("startup")` hook calls `create_watchlist_table_if_not_exists()`/`ensure_watchlist_schema_current()` directly — neither is patched there, so every e2e test would attempt a live BigQuery call at server startup.
- **Fix**: Add `patch("src.api.create_watchlist_table_if_not_exists")` and `patch("src.api.ensure_watchlist_schema_current")` to the existing `with (...)` block in `tests/e2e/conftest.py:174-181`, as part of Phase 2.
- **Decision**: FIXED — added as Phase 2 item 5 + new Automated Verification bullet (2.4) in plan.md

### F2 — renderTable() hardcodes #table-body; reuse claim is unspecified

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 3, item 4 — Add/remove ticker UI
- **Detail**: `renderTable(data, r)` (`static/index.html:1234`) writes directly into `$('table-body')`, not parameterized by container. Phase 3 implies reuse for My Wallet's own table without saying how.
- **Fix**: Add a `containerId = 'table-body'` parameter to `renderTable`; default preserves the existing call site unchanged; My Wallet calls `renderTable(data, role, 'my-wallet-table-body')`. The `r !== 'admin'` branch already matches what My Wallet needs.
- **Decision**: FIXED — Phase 3 item 4 in plan.md now specifies the `containerId` parameter change

### F3 — Watchlist endpoints don't specify BigQueryError handling

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 2, item 2 — Watchlist endpoints
- **Detail**: Every existing endpoint wraps its DB call in try/except `BigQueryError` → `HTTPException(500)` (e.g. `src/api.py:630-633`). Phase 2 item 2's contract for `GET/POST/DELETE /watchlist` omits this.
- **Fix**: State explicitly that `GET/POST/DELETE /watchlist` follow the same try/except `BigQueryError` → 500 pattern as every other endpoint.
- **Decision**: FIXED — added to Phase 2 item 2 contract in plan.md

### F4 — Add-ticker INSERT race on rapid double-click

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 1, item 2 — CRUD functions
- **Detail**: `lessons.md` already documents this exact class of bug ("SPA pagination — out-of-order fetch responses can desync the URL"). `add_watchlist_ticker`'s `INSERT ... WHERE NOT EXISTS` does the existence check and insert as two logical steps; two rapid clicks can both pass the check before either commits, producing duplicate rows for one (client_id, ticker) pair. Not destructive, but cosmetic double-listing.
- **Fix A ⭐ Recommended**: Disable the add button synchronously in the click handler, before the `await fetch(...)` call.
  - Strength: Zero DB changes; mirrors the exact fix this project's lessons.md already prescribes for the pagination race.
  - Tradeoff: Doesn't close the race across two tabs/devices — acceptable given today's single real user.
  - Confidence: HIGH — directly reuses a validated pattern in this codebase.
  - Blind spot: None significant.
- **Fix B**: Replace the INSERT-WHERE-NOT-EXISTS with a BigQuery `MERGE` statement.
  - Strength: Atomic upsert at the DB layer; closes the race regardless of UI behavior or client count.
  - Tradeoff: New SQL pattern this codebase hasn't used; BQ `MERGE` under concurrency can throw retryable conflicts needing handling.
  - Confidence: MEDIUM — BigQuery MERGE concurrency behavior unverified against this project's usage.
  - Blind spot: Whether retry-on-conflict handling is worth building before any second real user exists.
- **Decision**: FIXED via Fix A — Phase 3 item 4 intent + new manual verification step (3.10) added to plan.md

### F5 — @app.on_event("startup") is deprecated but functional

- **Severity**: 📝 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 2, item 4
- **Detail**: `pyproject.toml` pins `fastapi>=0.136.1`, where `@app.on_event` still works but is deprecated in favor of `lifespan`. No existing `lifespan` pattern exists in this codebase to mirror instead.
- **Fix**: None required — accept as-is.
- **Decision**: ACCEPTED
