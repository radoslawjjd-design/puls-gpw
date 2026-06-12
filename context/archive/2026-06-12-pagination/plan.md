# Pagination Implementation Plan

## Overview

Replace the single `limit` query parameter on `GET /announcements` with proper OFFSET-based pagination (`page` + `page_size`). Update the BQ layer, FastAPI layer, HTML panel, and add E2E tests via Playwright.

## Current State Analysis

- `db/bigquery.py`: `list_announcements_admin/user(limit: int = 20, ...)` — both use `LIMIT @limit` in BQ query; `_build_filter_clauses()` handles WHERE but not LIMIT/OFFSET.
- `src/api.py`: `limit: int = Query(20, ge=1, le=100)` — single param, no page concept.
- `static/index.html`: `<input type="number" id="f-limit">` + `params.set('limit', ...)` — no pagination UI.
- `pyproject.toml`: Playwright is NOT installed — Phase 4 must add it first.

## Desired End State

`GET /announcements?page=2&page_size=50` returns rows 51–100 ordered by `published_at DESC`. The panel shows Prev/Next buttons and a page_size selector (20/50/100). `limit` param is removed — breaking change is safe since the panel is the only client.

### Key Discoveries

- `_build_filter_clauses()` — `db/bigquery.py:314` — returns `(where_str, params_list)`; OFFSET is added to the calling query, not here.
- BQ OFFSET is a scan of all preceding rows — acceptable for the current data volume (hundreds per day on GPW/NewConnect).
- Both `list_announcements_admin` and `list_announcements_user` have identical pagination signatures — change both together in Phase 1.
- Playwright not in `pyproject.toml` — must `uv add --dev pytest-playwright` before writing E2E tests.

## What We're NOT Doing

- No total page count / "Page X of Y" — would require an extra `COUNT(*)` BQ query on every request.
- No cursor-based pagination — OFFSET is sufficient for current data scale.
- No session token management — API keys are static; 401→showLogin() already works.
- No `limit` backwards-compatibility alias.
- No infinite scroll.

## Implementation Approach

Four sequential phases following the data flow: BQ → API → Frontend → E2E. Each phase is independently testable and commits cleanly before the next starts.

## Phase 1: BQ Layer — page/page_size in list functions

### Overview

Replace `limit: int` with `page: int` and `page_size: int` in both BQ list functions. Compute `offset = (page - 1) * page_size` and inject as `LIMIT @page_size OFFSET @offset`.

### Changes Required

#### 1. `db/bigquery.py` — `list_announcements_admin` and `list_announcements_user`

**File**: `db/bigquery.py`

**Intent**: Replace the `limit` parameter with `page` (1-indexed) and `page_size` in both list functions. Compute offset inside the function and pass it to the BQ query.

**Contract**: Both functions get new signature `(page: int = 1, page_size: int = 20, ticker, company, event_type, from_dt, to_dt)`. Query becomes `... LIMIT @page_size OFFSET @offset` with two new `ScalarQueryParameter` entries (`page_size` INT64, `offset` INT64). The `limit` parameter is removed entirely.

#### 2. `tests/test_bigquery.py` — update pagination tests

**File**: `tests/test_bigquery.py`

**Intent**: Update any test that passes `limit=` to use `page=` and `page_size=` instead. Add one test verifying offset math: call with `page=2, page_size=1` and confirm the BQ query receives `OFFSET 1`.

### Success Criteria

#### Automated Verification

- Tests pass: `uv run pytest tests/test_bigquery.py -v`
- No regressions in full suite: `uv run pytest`

#### Manual Verification

- Code review: both list functions use `LIMIT @page_size OFFSET @offset`; `limit` param is gone

---

## Phase 2: API Layer — page/page_size in GET /announcements

### Overview

Replace `limit: int = Query(20, ge=1, le=100)` with `page: int = Query(1, ge=1)` and `page_size: int = Query(20, ge=1, le=100)` in the `/announcements` endpoint. Pass them through to the BQ functions.

### Changes Required

#### 1. `src/api.py` — `/announcements` endpoint signature

**File**: `src/api.py`

**Intent**: Remove `limit` and add `page` + `page_size` query params. Pass both to `list_announcements_admin` and `list_announcements_user`.

**Contract**: `page: int = Query(1, ge=1)`, `page_size: int = Query(20, ge=1, le=100)`. Both BQ call sites updated: `list_announcements_admin(page=page, page_size=page_size, ...)` and same for user.

#### 2. `tests/test_api.py` — update endpoint tests

**File**: `tests/test_api.py`

**Intent**: Update tests that pass `?limit=` to use `?page=&page_size=` instead. Add tests for: `page=1` returns first batch, `page=2` passes correct params to BQ mock, `page_size` out-of-range returns 422.

### Success Criteria

#### Automated Verification

- Tests pass: `uv run pytest tests/test_api.py -v`
- Full suite: `uv run pytest`

#### Manual Verification

- `GET /announcements?page=1&page_size=20` with admin key returns 200
- `GET /announcements?limit=10` returns 422 (param removed)

---

## Phase 3: Frontend — Prev/Next + page_size select

### Overview

Replace the `#f-limit` number input with a page_size `<select>` (20/50/100) and add Prev/Next buttons with a page indicator. Track `currentPage` in JS state; reset to 1 on filter submit.

### Changes Required

#### 1. `static/index.html` — filters section

**File**: `static/index.html`

**Intent**: Remove the limit `<input type="number">` and add a `<select id="f-page-size">` with options 20/50/100.

**Contract**: `<select id="f-page-size"><option value="20" selected>20</option><option value="50">50</option><option value="100">100</option></select>` — same position in the filters bar.

#### 2. `static/index.html` — pagination controls below table

**File**: `static/index.html`

**Intent**: Add a pagination bar below `.table-wrap` with Prev button, "Strona X" label, and Next button.

**Contract**: A `<div id="pagination-bar">` with `<button id="btn-prev">`, `<span id="page-label">Strona 1</span>`, `<button id="btn-next">`. Styled consistently with existing `.filters button`.

#### 3. `static/index.html` — JS state and fetch logic

**File**: `static/index.html`

**Intent**: Add `currentPage` state variable; update `fetchAnnouncements()` to pass `page` and `page_size` instead of `limit`; wire Prev/Next button handlers; reset `currentPage = 1` on filter form submit.

**Contract**:
- `let currentPage = 1;` alongside existing `let apiKey`, `let role`
- `params.set('page', currentPage)` and `params.set('page_size', $('f-page-size').value || '20')` — replaces `params.set('limit', ...)`
- Next button: `currentPage++; fetchAnnouncements()`; disabled when response length < page_size
- Prev button: `if (currentPage > 1) { currentPage--; fetchAnnouncements(); }`; disabled when `currentPage === 1`
- Filter form `submit` handler: `currentPage = 1` before calling `fetchAnnouncements()`
- Page_size select `change` handler: `$('f-page-size').addEventListener('change', () => { currentPage = 1; fetchAnnouncements(); })`
- `$('page-label').textContent = 'Strona ' + currentPage` updated on each fetch

### Success Criteria

#### Automated Verification

- Full suite still passes: `uv run pytest`

#### Manual Verification

- Panel shows page_size select (20/50/100) instead of limit input
- Next/Prev buttons visible; Prev disabled on page 1
- Changing page_size resets to page 1 and refetches
- Applying filter resets to page 1
- Next disabled when returned rows < page_size (last page signal)

---

## Phase 4: E2E — Playwright setup + pagination tests

### Overview

Install `pytest-playwright`, add a minimal test server fixture, and write E2E tests covering the pagination UX.

### Changes Required

#### 1. `pyproject.toml` — add playwright dev dependency

**File**: `pyproject.toml`

**Intent**: Add `pytest-playwright` to the `[dependency-groups] dev` section.

**Contract**: `"pytest-playwright>=0.6"` in dev deps. Run `uv sync` after edit. Then install browsers: `uv run playwright install chromium`.

#### 2. `tests/e2e/conftest.py` — test server fixture

**File**: `tests/e2e/conftest.py` (new file)

**Intent**: Start the FastAPI app on a real TCP port in a background thread so that Playwright (a real browser) can connect to it. `TestClient` uses an in-memory ASGI transport with no TCP socket — it cannot be used here.

**Contract**: A session-scoped `pytest` fixture `live_server_url` that:
1. Sets `os.environ["ADMIN_API_KEY"]` and `os.environ["USER_API_KEY"]` to test values before the thread starts (monkeypatch does not cross thread boundaries).
2. Creates `uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=18099, log_level="error"))` and starts it in a `threading.Thread(daemon=True)`.
3. Polls `httpx.get("http://127.0.0.1:18099/health")` in a retry loop (max 5s) to wait until the server is ready.
4. Yields `"http://127.0.0.1:18099"`.
5. Sets `server.should_exit = True` on teardown.

#### 3. `tests/e2e/test_pagination.py` — E2E pagination tests

**File**: `tests/e2e/test_pagination.py` (new file)

**Intent**: Cover the 4 key pagination behaviours via browser automation: initial page load, Next button advances page, filter submit resets to page 1, page_size change resets to page 1.

**Contract**: Use `getByRole`/`getByText` locators only (no CSS selectors). Tests:
- `test_initial_page_shows_page_1`: load panel, login, verify "Strona 1" visible and Prev disabled
- `test_next_advances_page`: click Next, verify "Strona 2" and Prev enabled
- `test_filter_resets_page`: navigate to page 2, submit filter form, verify "Strona 1"
- `test_page_size_resets_page`: navigate to page 2, change page_size select, verify "Strona 1"

### Success Criteria

#### Automated Verification

- Playwright tests pass: `uv run pytest tests/e2e/ -v`
- Full suite: `uv run pytest`

#### Manual Verification

- All 4 E2E scenarios pass in headed mode: `uv run pytest tests/e2e/ --headed`

---

## Testing Strategy

### Unit Tests

- `tests/test_bigquery.py` — offset math, both list functions
- `tests/test_api.py` — page/page_size param validation, BQ mock call args

### E2E Tests (Playwright)

- Initial state, Next/Prev navigation, filter reset, page_size change

### Manual Testing Steps

1. Start server: `ADMIN_API_KEY=test USER_API_KEY=test uv run python api_main.py`
2. Open `http://localhost:8080`, log in as admin
3. Verify page_size select visible; limit input gone
4. Click Next → "Strona 2", Prev → "Strona 1"
5. Apply filter → page resets to 1
6. Change page_size → page resets to 1

## References

- BQ list functions: `db/bigquery.py:344` (`list_announcements_admin`), `db/bigquery.py:406` (`list_announcements_user`)
- API endpoint: `src/api.py:93`
- Frontend: `static/index.html:119` (limit input), `static/index.html:202` (fetch logic)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: BQ Layer — page/page_size in list functions

#### Automated

- [x] 1.1 Tests pass: uv run pytest tests/test_bigquery.py -v — c5b1d52
- [x] 1.2 Full suite: uv run pytest — c5b1d52

#### Manual

- [x] 1.3 Code review: both list functions use LIMIT @page_size OFFSET @offset; limit param is gone — c5b1d52

### Phase 2: API Layer — page/page_size in GET /announcements

#### Automated

- [x] 2.1 Tests pass: uv run pytest tests/test_api.py -v — 8317c9d
- [x] 2.2 Full suite: uv run pytest — 8317c9d

#### Manual

- [x] 2.3 GET /announcements?page=1&page_size=20 with admin key returns 200 — 8317c9d
- [x] 2.4 GET /announcements?limit=10 returns 422 (param removed) — 8317c9d

### Phase 3: Frontend — Prev/Next + page_size select

#### Automated

- [x] 3.1 Full suite: uv run pytest — b4703ec

#### Manual

- [x] 3.2 Panel shows page_size select (20/50/100) instead of limit input — b4703ec
- [x] 3.3 Next/Prev buttons visible; Prev disabled on page 1 — b4703ec
- [x] 3.4 Changing page_size resets to page 1 and refetches — b4703ec
- [x] 3.5 Applying filter resets to page 1 — b4703ec
- [x] 3.6 Next disabled when returned rows < page_size (last page signal) — b4703ec

### Phase 4: E2E — Playwright setup + pagination tests

#### Automated

- [x] 4.1 Playwright tests pass: uv run pytest tests/e2e/ -v — 524cf7e
- [x] 4.2 Full suite: uv run pytest — 524cf7e

#### Manual

- [x] 4.3 All 4 E2E scenarios pass in headed mode: uv run pytest tests/e2e/ --headed — 524cf7e
