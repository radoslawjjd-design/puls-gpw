# Dashboard Refresh Fix Implementation Plan

## Overview

Fix the dashboard's "stops loading after refresh" bug (PUL-37 / GitHub #40).
`static/index.html`'s top-level script calls `showDashboard(role)`
synchronously, before the script reaches the `const _ADMIN_COLS`/`_USER_COLS`
declarations that `renderHeaders()` depends on. On every page load where a
session already exists (`apiKey && role` truthy in `sessionStorage` — i.e.
every F5/Ctrl+F5 refresh after the first login), this throws
`ReferenceError: Cannot access '_ADMIN_COLS' before initialization`, which
aborts the rest of the top-level script and silently prevents every
subsequent `addEventListener` call (filters, date-toggle, pagination
buttons) from ever registering.

## Current State Analysis

`context/changes/dashboard-refresh-bug/frame.md` already diagnosed and
live-reproduced the root cause (Playwright, this session) — see its
Hypothesis Investigation table. Key facts carried forward as settled:

- The original ticket's stated cause (date-filter `toISOString()` throwing
  outside `try/catch`) is technically incapable of producing the reported
  symptom: `fetchAnnouncements` is an `async function` (`static/index.html:339`)
  called without `await`/`.catch` (`:310`) — any throw inside it becomes a
  rejected promise, never a synchronous exception that could block sibling
  code. Live repro confirmed the failure reproduces with a ticker-only
  filter, never touching the date fields.
- Confirmed via `grep '^(const|let) _?[A-Za-z]' static/index.html`: the only
  two top-level bindings declared *after* the synchronous bootstrap call
  (`:228`) are `_ADMIN_COLS` (`:314`) and `_USER_COLS` (`:324`). No other
  forward-reference hazard exists in the file.
- `ui_html` is read once, at server startup, from `static/index.html`
  (`src/api.py:78`) — a normal process restart picks up the fix; no special
  cache-busting needed.
- `/announcements` (`src/api.py:94-133`) is unaffected — this is purely a
  frontend script-ordering bug.
- The project already has Playwright e2e infra: `tests/e2e/conftest.py`
  (mocks `list_announcements_admin`/`list_announcements_user`, serves the
  real app via uvicorn) and `tests/e2e/test_pagination.py` (login + page
  navigation pattern to follow).
- CI (`​.github/workflows/deploy.yml:32-36`) already runs
  `uv run playwright install chromium --with-deps` then `uv run pytest`
  before every deploy — a new e2e test here is enforced automatically.

## Desired End State

Refreshing the dashboard (F5 or Ctrl+F5) with an already-authenticated
session reloads the table and headers correctly, regardless of which filter
(ticker, company, event type, or date) was set beforehand. The browser
console shows no `ReferenceError`. Filtering, pagination buttons, and the
date-field datetime-local toggle remain functional after refresh.

Verified by: the new Playwright regression test passing, the full
`uv run pytest` suite green, and a manual check in real Chrome.

### Key Discoveries:

- `static/index.html:228` (`if (apiKey && role) showDashboard(role); else showLogin();`)
  runs as the first executable statement of the script — before any
  function below it has had its closed-over `const`/`let` bindings
  initialized.
- `static/index.html:314,324` — `_ADMIN_COLS`/`_USER_COLS`, the only
  forward-referenced bindings.
- `static/index.html:347-348` — the date-param construction the original
  ticket flagged; not the root cause, but a real (if narrower) latent
  exception path worth closing while in this code.

## What We're NOT Doing

- **Not** fixing the pagination-position-loss-on-refresh bug (`currentPage`
  resets to 1; URL's `page`/`page_size` is only restored on `popstate`,
  never read on initial load — `static/index.html:225,257-265,366`).
  `frame.md` confirmed this is real but distinct from the reported "stops
  loading" defect. Tracked as a candidate follow-up change, not opened here.
- **Not** touching `/announcements` or any backend/BigQuery code — confirmed
  unaffected.
- **Not** introducing a JS bundler, linter, or TypeScript — staying within
  the existing single inline-`<script>` pattern.
- **Not** fixing the unrelated `colspan` mismatches noticed in passing
  (catch-block error row uses `colspan="7"` while the admin table has 8
  columns; `renderTable`'s empty-state `colspan="8"` doesn't match the
  5-column user view) — cosmetic, pre-existing, unrelated to this bug.

## Implementation Approach

Two independent, separately-committable fixes:

1. Eliminate the load-order bug structurally: wrap the session-resume
   bootstrap in a named `init()` function and call it as the literal last
   statement of the script, after every other declaration. This doesn't
   just patch the two known offending `const`s — it makes the entire class
   of "forward-referenced top-level binding" bug impossible for any future
   addition to this script.
2. Close the narrower, ticket-flagged latent risk in date-filter parsing by
   validating before constructing the ISO string, instead of relying on a
   `try/catch` around a call that can throw `RangeError`.

## Phase 1: Fix the script bootstrap load-order bug

### Overview

Stop `showDashboard()` from running before the script has finished
declaring everything it depends on, and add a regression test that fails
on current `master` and passes once fixed.

### Changes Required:

#### 1. Defer the session-resume bootstrap to the end of the script

**File**: `static/index.html`

**Intent**: Remove the possibility of any top-level `const`/`let` being
referenced before its declaration is reached, by running the bootstrap
check only after the entire script has executed once top-to-bottom.

**Contract**: Replace the bare `if (apiKey && role) showDashboard(role); else showLogin();`
at `:228-229` with a function declaration:
```js
function init() {
  if (apiKey && role) showDashboard(role);
  else                showLogin();
}
```
Add `init();` as the literal final statement of the `<script>` block (after
the existing `esc()` function definition, immediately before `</script>`).
Nothing else in the script changes — `apiKey`/`role`/`currentPage` stay
declared where they are (`:223-225`); only the *call* moves. Placement is
load-bearing: `init()` must remain the textual last statement — moving it
anywhere before a top-level `const`/`let` declaration reintroduces this
exact bug.

#### 2. Regression test reproducing the exact failure

**File**: `tests/e2e/test_refresh.py` (new)

**Intent**: Lock in the bug this plan fixes using the same mechanism that
diagnosed it in `frame.md` — log in, paginate, set a non-date filter,
reload, and assert the dashboard is still fully functional.

**Contract**: Follow the `_login` helper pattern from
`tests/e2e/test_pagination.py`. The test must, after `page.reload()`:
- assert zero `page.on("pageerror", ...)` events were captured (this is
  what currently fails — the `ReferenceError`),
- assert the table header is populated, e.g.
  `expect(page.get_by_role("columnheader", name="Spółka")).to_be_visible()`
  (`<th>` renders under a real `<thead>` with no `scope` override, so
  Chromium exposes it with the implicit "columnheader" role),
- assert at least one row is rendered in `#table-body` (the mocked
  fixture always returns 20 admin rows — proves `fetchAnnouncements`
  actually ran via `init()`, not just that headers rendered),
- assert clicking "Filtruj" still fires a request to `/announcements`
  (proves the submit listener got attached — currently it doesn't),
- assert clicking the `#f-from` field still switches it to
  `type="datetime-local"` (proves the `.date-toggle` listener got
  attached — currently it doesn't),
- assert clicking "Następna" after reload still advances to "Strona 2"
  (proves the pagination button listener survived too).

Use only a ticker filter before reload, deliberately not touching the date
fields, to keep this test independent from Phase 2's concern.

### Success Criteria:

#### Automated Verification:

- `uv run pytest tests/e2e/test_refresh.py -v` passes
- `uv run pytest` (full suite) passes

#### Manual Verification:

- In real Chrome: log in, click "Następna", refresh with F5 — table and
  header render, DevTools console shows no `ReferenceError`, filtering
  still works
- Repeat with Ctrl+F5 (hard reload) — identical result

---

## Phase 2: Defensive date-filter parsing guard

### Overview

Close the narrower latent risk the original ticket flagged: constructing an
ISO date string from unvalidated input can throw `RangeError`. Validate
before constructing instead of risking an unhandled promise rejection.

### Changes Required:

#### 1. Validate-before-construct helper

**File**: `static/index.html`

**Intent**: Replace the throwing `new Date(...).toISOString()` calls in
`fetchAnnouncements` with a helper that returns `null` for an unparseable
value instead of throwing, and skip setting that query param when `null`.

**Contract**: Add a small helper near `esc()` in the "util" section:
```js
function parseDateOrNull(value) {
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}
```
Replace `static/index.html:347-348` (`params.set('from', new Date($('f-from').value).toISOString())`
and the `to` equivalent) with calls to `parseDateOrNull`, only calling
`params.set(...)` when the result is non-`null`. An unparseable date value
silently drops that filter rather than throwing; no user-facing message is
added (out of scope — this guards a currently-unconfirmed edge case, not a
reported UX defect).

Note for the automated test: `#f-from` flips to `type="datetime-local"` on
focus (`:267-271`), and `page.fill()` always focuses first — so a native
datetime-local input will reject a free-text garbage value before it ever
reaches `parseDateOrNull`. Set the invalid value via
`page.evaluate("document.getElementById('f-from').value = '...'")` followed
by dispatching a `change` event, instead of `page.fill()` — the same
mechanism the manual-verification step already uses via DevTools.

### Success Criteria:

#### Automated Verification:

- New or extended e2e assertion confirms an invalid value placed into
  `#f-from` does not throw and the request still fires without a `from`
  param: `uv run pytest tests/e2e/test_refresh.py -v`
- Full suite passes: `uv run pytest`

#### Manual Verification:

- Via DevTools, set `#f-from`'s value to a non-date string, trigger the
  filter request — no uncaught promise rejection in console, request
  still completes

---

## Testing Strategy

### Unit Tests:

- None needed — no backend/Python logic changes.

### Integration Tests:

- Both phases verified via Playwright e2e tests against the real app served
  by `create_app()`, following the existing `tests/e2e/conftest.py` fixture.

### Manual Testing Steps:

1. Run the app locally, log in as admin, navigate to page 2, set a ticker
   filter, refresh (F5) — confirm table reloads and stays interactive.
2. Repeat with Ctrl+F5.
3. Set an intentionally garbage value into the date field via DevTools and
   confirm no console exception when filtering.

## Performance Considerations

None — this only changes script execution order and adds one validation
branch; no measurable runtime cost.

## Migration Notes

None — static asset change only; a normal deploy/restart picks up the new
`static/index.html` (`ui_html` is read once at startup, `src/api.py:78`).

## References

- Frame brief: `context/changes/dashboard-refresh-bug/frame.md`
- `static/index.html:219-372` (full init/fetch/listener flow)
- `tests/e2e/conftest.py`, `tests/e2e/test_pagination.py` (test pattern to follow)
- Tracking: Linear PUL-37, GitHub #40

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Fix the script bootstrap load-order bug

#### Automated

- [x] 1.1 `uv run pytest tests/e2e/test_refresh.py -v` passes
- [x] 1.2 Full suite passes: `uv run pytest`

#### Manual

- [x] 1.3 Real Chrome, F5 refresh with existing session — table renders, no console error, filtering works (user-verified on localhost)
- [x] 1.4 Real Chrome, Ctrl+F5 — identical result (user-verified on localhost)

### Phase 2: Defensive date-filter parsing guard

#### Automated

- [x] 2.1 Invalid date value doesn't throw, request fires without `from` param: `uv run pytest tests/e2e/test_refresh.py -v`
- [x] 2.2 Full suite passes: `uv run pytest` (140 passed)

#### Manual

- [x] 2.3 DevTools garbage date value — no uncaught promise rejection, request still completes (covered by e2e `test_invalid_date_filter_does_not_throw_and_drops_param`, which injects a garbage value and asserts no pageerror + request fires without `from`)
