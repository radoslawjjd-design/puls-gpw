<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Pagination

- **Plan**: context/changes/pagination/plan.md
- **Scope**: All phases (1–4)
- **Date**: 2026-06-12
- **Verdict**: NEEDS ATTENTION (all findings resolved in triage)
- **Findings**: 0 critical  3 warnings  5 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | WARNING |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | PASS |

## Findings

### F1 — javascript: URL bypass in href rendering

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: static/index.html:302
- **Detail**: The admin table rendered `<a href="${esc(row.url)}">` — esc() escapes & < > " but not `javascript:`. A poisoned BQ URL would execute JS in the admin's browser on click.
- **Fix**: Validate scheme before rendering anchor: `/^https?:\/\//.test(row.url)` — render plain text if false.
- **Decision**: FIXED — scheme guard added at static/index.html:302

### F2 — conftest teardown race: thread not joined before patch exits

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: tests/e2e/conftest.py:55
- **Detail**: `server.should_exit = True` was called but `thread.join()` was never called. Daemon thread could make real BQ calls after patch context exited, causing credentials errors in CI teardown.
- **Fix**: Add `thread.join(timeout=3)` after `server.should_exit = True`, inside the patch block.
- **Decision**: FIXED

### F3 — TestClient created at module scope before env fixture

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: tests/test_api.py:18
- **Detail**: `client = TestClient(create_app())` ran at import time, before `_env` autouse fixture set env vars. Future code caching keys at app-creation time would break all auth tests silently.
- **Fix**: Moved to a `@pytest.fixture def api_client(_env)` with explicit dependency. All test functions updated to accept `api_client` parameter.
- **Decision**: FIXED

### F4 — Non-401 error responses silently render as empty table

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Safety & Quality
- **Location**: static/index.html:264
- **Detail**: After 401 check, `r.json()` was called unconditionally. A 422/500 response rendered as "Brak wyników" with no error indicator.
- **Fix**: Added `if (!r.ok) throw new Error('HTTP ' + r.status);` — falls into existing catch error display.
- **Decision**: FIXED

### F5 — Rapid Prev/Next clicks fire concurrent overlapping fetches

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Safety & Quality
- **Location**: static/index.html:209–216
- **Detail**: Two rapid clicks incremented currentPage twice and fired two concurrent fetches. The slower one could win, showing stale data.
- **Fix**: Disable both nav buttons at start of `fetchAnnouncements`; re-enable after `renderTable` (or in catch).
- **Decision**: FIXED

### F6 — esc() does not escape single-quote

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Safety & Quality
- **Location**: static/index.html:343
- **Detail**: esc() escaped & < > " but not '. All current attributes use double-quote delimiters so no current risk, but contract was incomplete.
- **Fix**: Added `.replace(/'/g, "&#39;")` to esc().
- **Decision**: FIXED

### F7 — Hard-coded port 18099 may collide in parallel CI runs

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Pattern Consistency
- **Location**: tests/e2e/conftest.py:42
- **Detail**: Fixed port 18099 would collide if CI ran matrix jobs in parallel on the same host.
- **Fix**: Switched to `port=0`; actual port retrieved from `server.servers[0].sockets[0].getsockname()[1]` after `server.started`.
- **Decision**: FIXED

### F8 — BQ list functions lack page >= 1 guard

- **Severity**: OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Pattern Consistency
- **Location**: db/bigquery.py:354, 419
- **Detail**: API layer enforces `page >= 1` via `Query(1, ge=1)` but BQ functions had no internal guard. `page=0` from Python code would produce `OFFSET -20`, rejected by BQ with a runtime error.
- **Fix**: Added `if page < 1: raise ValueError(...)` at top of both list functions.
- **Decision**: FIXED
