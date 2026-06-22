# Admin Dashboard: Per-View URLs and Pagination in Browser History — Implementation Plan

## Overview

The admin SPA (`static/index.html`) has three views — announcements, portfolio
treemap, X-post history — plus pagination within the first two. None of this is
reflected in the browser URL in a deep-linkable way: switching to treemap or
x-history never touches the URL, x-history pagination has zero History API
integration, and the one `popstate` listener only knows about announcements.
This plan gives every view its own URL, with full filter + pagination state
folded in, so refresh / direct link / back-forward reproduce exactly what the
user was looking at.

## Current State Analysis

`static/index.html` is a single 1268-line vanilla-JS file (no framework, no
build step) served by FastAPI's `GET /` (`src/api.py:137`). State today:

- `showAnnouncementsView()` (`:860`), `showXHistoryView()` (`:869`),
  `showTreemapView()` (`:884`) toggle `display` on three sibling `<div>`s.
  Only the latter two trigger a fetch (`fetchXPosts()`, `fetchTreemap()`);
  `showAnnouncementsView()` is a pure DOM toggle (e.g. called from
  `topbar-home` click, `:600`) and relies on table content already being
  in the DOM from a prior fetch.
- `fetchAnnouncements()` (`:894`) already does `history.pushState`/
  `replaceState` (`:923-926`), but only `?page=&page_size=` on path `/` —
  filters (ticker/company/event_type/from/to) are sent to the API but never
  written to the URL.
- `fetchXPosts()` (`:935`) has **no** History API call at all.
- The single `popstate` listener (`:602`) calls `showAnnouncementsView()`
  unconditionally and only restores `page`/`page_size` from `e.state`.
- `init()` (`:548`) never looks at `location.search` — it only checks
  `sessionStorage` for `apiKey`/`role` and always lands on
  `showDashboard()` → `showAnnouncementsView()` + `fetchAnnouncements(false)`.
- `src/api.py` has no catch-all route — only `GET /` serves `index.html`.

## Desired End State

- Every view switch (topbar-home, treemap menu item, x-history menu item)
  produces a new browser-history entry.
- The URL always encodes: which view is active, its pagination (`page`,
  `page_size`), and (for announcements/x-history) its active filters.
- Refreshing the page, pasting the URL into a new tab, or using back/forward
  reproduces the exact view + page + filters the user was on.
- Announcements stays the **default** view: a URL with no `view=` param (or
  an unrecognized one) — including today's bare `?page=2&page_size=20`
  bookmarks — resolves to announcements, so no migration is needed for
  existing links.
- Zero backend changes: `?view=...` is a query string against the existing
  `GET /` route, which already ignores query params and always returns
  `index.html`.
- Modals (treemap click-popup, x-post detail modal) stay outside routing —
  opening/closing one never touches the URL or history stack (preserves the
  PUL-51 invariant that closing the popup must not navigate).

### Key Discoveries:

- `fetchAnnouncements()` (`static/index.html:897-910`) already builds a
  `URLSearchParams` for the API call that contains every filter value —
  the same object (plus `page`/`page_size`) is exactly what the history URL
  needs. Reusing it (instead of a second, separately-maintained URL builder)
  is what keeps the API query and the visible URL from drifting apart.
- `fetchXPosts()` (`static/index.html:940-953`) builds the equivalent
  `URLSearchParams` for x-history filters — same reuse opportunity.
- Date filters use a focus/blur type toggle (`bindDateToggle`, `:613`):
  inputs start as `type="text"` and become `type="datetime-local"` on focus,
  reverting to `text` on blur only if empty. Restoring a value from the URL
  must set `type="datetime-local"` *before* assigning `.value`, or the
  browser silently drops it.
- `event_type` is stored in the URL/API as the machine code (e.g.
  `dywidenda`) via `EVENT_TYPE_CODES`, but the `#f-event-type` input displays
  the human label (e.g. `Dywidenda`) via `EVENT_TYPE_LABELS` — restoring from
  the URL must go through `EVENT_TYPE_LABELS[code]`, not set the input to
  the raw code.
- `showXHistoryView()` and `showTreemapView()` already trigger their own
  fetch as a side effect of being called; `showAnnouncementsView()` does
  not. This asymmetry matters for where the URL write happens (see Phase 1
  Critical Implementation Details).
- `tests/e2e/test_refresh.py::test_refresh_with_existing_session_keeps_dashboard_functional`
  already navigates to page 2 and reloads, but never asserts the page-label
  after reload — so it doesn't actually pin today's "reload always resets to
  page 1" behavior. It will keep passing unmodified after this change, but it
  gives zero coverage of the new contract; Phase 4 must add an explicit
  state-preservation assertion, not just assume the existing test covers it.

## What We're NOT Doing

- No backend/route changes in `src/api.py` — query-param routing only, no
  catch-all path route, no server-side rendering by view.
- No new views, no new pagination on treemap (it has none today and isn't
  getting any).
- No history entries for modal open/close (treemap popup, x-post detail
  modal) — only view switches and in-view pagination/filter changes touch
  the URL.
- No migration/redirect logic for old-format bookmarks (`?page=2`) — they
  already resolve correctly under the "no `view=` → announcements" default,
  so there is nothing to migrate.
- No change to how long `currentPage`/`xpPage` persist across same-session
  view revisits (e.g. leaving x-history on page 3 and coming back later in
  the same session still shows page 3) — that in-memory behavior is
  untouched; only its reflection in the URL is new.

## Implementation Approach

Introduce one new module-level variable (`currentView`) and a small set of
shared helpers, then wire the existing view/fetch functions through them:

1. **`_announcementsParams()`** / **`_xPostsParams()`** — extract the
   existing inline `URLSearchParams`-building code out of `fetchAnnouncements`
   / `fetchXPosts` into standalone functions. Both the API fetch and the
   history URL writer call the same function, so they can never drift.
2. **`_writeUrl(view, params, push)`** — single function that prefixes
   `params` with `view=<name>` unless `view === 'announcements'`, then calls
   `history.pushState`/`replaceState` with the resulting query string.
3. **`_navigateToView(view)`** — bound to `topbar-home`, `treemap-btn`,
   `x-history-btn`. Sets `currentView`, calls the matching `show*View()`, and
   is the single place responsible for writing the URL when the act of
   showing the view doesn't already cause a fetch (announcements, treemap).
4. **`_applyUrlState()`** — parses `location.search`, determines the view,
   restores filter inputs + page from the matching params, and calls the
   matching `show*View()` + fetch with `push=false`. Used by both `init()`
   (deep-link / refresh) and the `popstate` listener (back/forward) — one
   code path for both, so they can't diverge.

## Critical Implementation Details

**State sequencing — who writes the URL on a view switch.** `showXHistoryView()`
and `showTreemapView()` trigger fetches as a side effect of being called;
`showAnnouncementsView()` does not. If both `_navigateToView()` and the
triggered fetch wrote the URL, a single click would push two history entries
instead of one. The contract: `fetchAnnouncements()`/`fetchXPosts()` own the
URL write whenever *they* run (covers x-history's case, since
`showXHistoryView()` always fetches); `_navigateToView()` itself only writes
the URL for the branches where no fetch is about to fire — announcements
(reuses `_announcementsParams()` without re-fetching, since the table is
already populated) and treemap (no params to build, no fetch writes a URL).

**Date round-trip is local time, not UTC.** `parseDateOrNull` converts an
input's value to `.toISOString()` (UTC) for the API/URL. Restoring a value
into a `datetime-local` input must NOT slice that ISO string directly —
`datetime-local` expects local wall-clock time. Convert through `Date`
getters:

```js
function _isoToLocalInputValue(iso) {
  const d = new Date(iso);
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
```

## Phase 1: Routing core — view-level URLs

### Overview

Give treemap and x-history their own URL on entry, centralize all view
switching through one function, and make `init()`/`popstate` view-aware.
Filters and pagination are NOT yet part of the URL — that's Phases 2-3. This
phase is deliberately scoped to just the `view=` param so it's independently
testable before the bigger filter-serialization work.

### Changes Required:

#### 1. Centralize view navigation

**File**: `static/index.html`

**Intent**: Replace the three independent click bindings
(`topbar-home` → `showAnnouncementsView`, `treemap-btn` → `showTreemapView`,
`x-history-btn` → `showXHistoryView`) with a single `_navigateToView(view)`
dispatcher so URL-writing has one entry point instead of three.

**Contract**: `_navigateToView('announcements' | 'treemap' | 'x-history')`
sets `currentView`, calls the corresponding `show*View()`, and — per the
sequencing rule in Critical Implementation Details — writes `?view=treemap`
itself for the treemap branch (`history.pushState`), does nothing for
x-history (its own fetch writes the URL once Phase 3 lands; until then, in
this phase, push `?view=x-history` directly the same way as treemap), and
for announcements pushes the bare `/` (no `view=` param).

#### 2. View-aware init and popstate

**File**: `static/index.html`

**Intent**: Replace the unconditional `showAnnouncementsView()` calls in
`init()`/`showDashboard()` and in the `popstate` listener with a shared
`_applyUrlState()` that reads `location.search` and dispatches to the right
view.

**Contract**: `_applyUrlState()` reads `view` from
`new URLSearchParams(location.search)`; `'treemap'` → `showTreemapView()`,
`'x-history'` → `showXHistoryView()`, anything else (missing, unrecognized,
or `'announcements'`) → `showAnnouncementsView()`. Called from `init()` right
after `injectAdminOnlyChrome(r)` (so the treemap/x-history DOM nodes and nav
buttons already exist) in place of the current `showAnnouncementsView()` +
`fetchAnnouncements(false)` pair, and from the `popstate` listener in place
of its current body.

### Success Criteria:

#### Automated Verification:

- Full test suite passes: `uv run pytest --tb=short`
- E2E suite passes: `uv run pytest tests/e2e/ -q`

#### Manual Verification:

- Clicking "Treemapa portfela" sets the URL to `...?view=treemap`
- Clicking "Historia postów X" sets the URL to `...?view=x-history`
- Clicking the "puls-gpw" topbar heading returns the URL to `/` (no `view=`)
- Refreshing while on `?view=treemap` lands back on the treemap view, not
  announcements
- Browser back after Treemapa → Historia postów X returns to the treemap
  view (URL shows `?view=treemap` again)

---

## Phase 2: Announcements — full state in the URL

### Overview

Extend the existing (partial) announcements History API integration so the
URL carries every filter plus page/page_size, and restoring from a URL
repopulates the filter form, not just the page counter.

### Changes Required:

#### 1. Extract and reuse the params builder

**File**: `static/index.html`

**Intent**: Pull the `URLSearchParams` construction (ticker, company,
event_type, from, to, page, page_size) out of `fetchAnnouncements()` into
`_announcementsParams()` so `_navigateToView('announcements')` (Phase 1) and
the URL writer below can build the identical query without duplicating the
filter-reading logic.

**Contract**: `_announcementsParams()` returns a `URLSearchParams` with the
same key set and value semantics `fetchAnnouncements()` already sends to the
API (including the label→code mapping for `event_type` via
`EVENT_TYPE_CODES`). `fetchAnnouncements()` calls it instead of inlining the
logic.

#### 2. Restore filters + page from the URL

**File**: `static/index.html`

**Intent**: When `_applyUrlState()` (Phase 1) resolves to the announcements
view, populate `#f-ticker`/`#f-company`/`#f-event-type`/`#f-from`/`#f-to`/
`#f-page-size` and `currentPage` from the URL's params before fetching, so a
refresh or deep link reproduces the exact filtered/paginated view.

**Contract**: `event_type` restore goes through `EVENT_TYPE_LABELS[code]`
(see Key Discoveries) to set the *label* on `#f-event-type`. `from`/`to`
restore goes through `_isoToLocalInputValue()` (Critical Implementation
Details) and sets `type="datetime-local"` before assigning `.value`. After
populating, call `fetchAnnouncements(false)` (replaceState, not a new
history entry — we're applying the existing URL, not creating a new one).

#### 3. Add `_writeUrl()` and write full state on every announcements fetch

**File**: `static/index.html`

**Intent**: `fetchAnnouncements()`'s existing `pushState`/`replaceState`
call (`:923-926`) currently writes only `?page=&page_size=`. Replace it with
the shared `_writeUrl(view, params, push)` helper from Implementation
Approach (prefixes `params` with `view=<name>` unless `view ===
'announcements'`, then calls `pushState`/`replaceState`), so `_writeUrl()`
is a real shared function from this phase onward rather than introduced
ad hoc in Phase 3 for x-history alone — `fetchAnnouncements()` calls
`_writeUrl('announcements', _announcementsParams(), push)`.

**Contract**: Same `push` boolean parameter and pushState-vs-replaceState
split as today; only the query string content changes (full filter set
instead of just page/page_size), now routed through `_writeUrl()`.

### Success Criteria:

#### Automated Verification:

- Full test suite passes: `uv run pytest --tb=short`
- E2E suite passes: `uv run pytest tests/e2e/ -q`
- `tests/e2e/test_pagination.py` passes unmodified
- `tests/e2e/test_refresh.py` passes (with the Phase 4 assertion additions)

#### Manual Verification:

- Setting a ticker filter and paging to page 2 shows both `ticker=` and
  `page=2` in the URL
- Refreshing on that URL restores the ticker filter field's value, the
  event-type dropdown's label, and page 2's data
- Setting a date filter, refreshing, re-opening the date field shows the
  same date/time (not shifted by timezone)
- An old-style bookmark (`?page=2&page_size=20`, no filters, no `view=`)
  still lands on announcements page 2 with empty filters

---

## Phase 3: X-history — full state in the URL (new ground)

### Overview

Same treatment as Phase 2, but for x-history, which today has zero History
API integration — this is where the user's reported bug ("page 2 doesn't
change the URL") gets fixed directly.

### Changes Required:

#### 1. Extract params builder + add push/replace to fetchXPosts

**File**: `static/index.html`

**Intent**: Pull the `URLSearchParams` construction (window, status,
post_text, from, to, page, page_size) out of `fetchXPosts()` into
`_xPostsParams()`, and give `fetchXPosts()` the same `push` parameter and
end-of-fetch `history.pushState`/`replaceState` call pattern
`fetchAnnouncements()` has.

**Contract**: `_xPostsParams()` mirrors `_announcementsParams()`'s shape for
this view's filter set. `fetchXPosts(push = true)` writes
`_writeUrl('x-history', _xPostsParams(), push)` (see Implementation
Approach) after a successful response, matching `fetchAnnouncements()`'s
existing pattern.

#### 2. Drop the Phase 1 interim push from `_navigateToView`'s x-history branch

**File**: `static/index.html`

**Intent**: Phase 1 had `_navigateToView('x-history')` push `?view=x-history`
directly as an interim measure, since `fetchXPosts()` didn't write the URL
yet. Now that step 1 above makes `fetchXPosts()` own that write on every
call — and `showXHistoryView()` always calls `fetchXPosts()` as a side
effect — leaving the Phase 1 push in place means a single click on
"Historia postów X" fires two `pushState` calls (the interim one,
synchronously, then `fetchXPosts()`'s own push once the fetch resolves),
landing two history entries for one click instead of one.

**Contract**: `_navigateToView`'s `'x-history'` branch calls
`showXHistoryView()` and does nothing else — no `history.pushState` call of
its own — matching the final-state contract already described in Critical
Implementation Details ("`_navigateToView()` itself only writes the URL for
the branches where no fetch is about to fire").

#### 3. Wire prev/next/filter-submit/page-size to the push contract

**File**: `static/index.html`

**Intent**: `xp-btn-prev`/`xp-btn-next` (pagination) and
`xp-filter-form` submit / `xp-f-page-size` change (filter changes, which
reset `xpPage` to 1) need to call `fetchXPosts()` the same way the
announcements equivalents already call `fetchAnnouncements()` — no
signature changes needed there since `push` defaults to `true`.

**Contract**: No behavior change to these handlers beyond what falls out of
`fetchXPosts()` now writing the URL — they already call `fetchXPosts()` with
no arguments, which keeps the push default.

#### 4. Restore x-history filters + page from the URL

**File**: `static/index.html`

**Intent**: When `_applyUrlState()` resolves to `'x-history'`, populate
`#xp-f-window`/`#xp-f-status`/`#xp-f-post-text`/`#xp-f-from`/`#xp-f-to`/
`#xp-f-page-size` and `xpPage` from the URL before fetching.

**Contract**: Same date round-trip helper as Phase 2
(`_isoToLocalInputValue`). One ordering gotcha specific to this view: the
x-history view's filter form is built lazily on first
`showXHistoryView()` call (`_xHistoryViewBuilt` guard, `:694`) — restoring
filter values must happen *after* `showXHistoryView()` has run (so the
`#xp-f-*` elements exist), not before.

### Success Criteria:

#### Automated Verification:

- Full test suite passes: `uv run pytest --tb=short`
- E2E suite passes: `uv run pytest tests/e2e/ -q`

#### Manual Verification:

- Opening "Historia postów X" and clicking "Następna" changes the URL to
  include `view=x-history&page=2` (this is the exact bug reported — paging
  must now visibly change the URL)
- Refreshing on that URL restores x-history view, page 2
- Setting a window/status filter, refreshing, restores the dropdown
  selections
- Browser back after paging x-history steps back to page 1 of x-history (not
  to announcements)

---

## Phase 4: Polish, edge cases, and test coverage

### Overview

Close the gaps that only show up once all three views carry URL state:
verify modal open/close stays outside routing, confirm old-link
compatibility end-to-end, and bring automated coverage up to the new
contract.

### Changes Required:

#### 1. Confirm modal isolation

**File**: `static/index.html`

**Intent**: Verify (and adjust if needed) that `_openTreemapPopup`/
`_closeTreemapPopup` and `openModal`/`closeModal` (x-post detail, ogłoszenie
detail) make no `history.pushState`/`replaceState` calls anywhere in their
paths — they should be unaffected by Phases 1-3 since none of the new code
touches them, but this is the regression PUL-51 already had to fix once.

**Contract**: No code change expected; this is a verification pass with an
E2E assertion (see Testing below) acting as the regression guard.

### Testing Strategy:

#### Unit Tests:

- None planned — `_announcementsParams()`/`_xPostsParams()`/
  `_isoToLocalInputValue()` are small, pure-ish DOM-reading functions with no
  existing unit-test harness for `static/index.html`'s inline script; E2E
  coverage below exercises them through real browser behavior, which is
  also what would catch a regression in `datetime-local` round-tripping.

#### E2E Tests:

- New file `tests/e2e/test_url_routing.py`:
  - Switching announcements → treemap → x-history → announcements produces
    URLs `/`, `?view=treemap`, `?view=x-history`, `/` in that order, with
    each switch back-navigable (`page.go_back()` returns to the prior view).
  - Deep link: `page.goto(f"{live_server_url}?view=treemap")` after login
    lands directly on the treemap view.
  - Refresh on x-history page 2 with a status filter set restores all three
    (view, page, filter) after `page.reload()`.
  - An old-format URL (`?page=2&page_size=50`) resolves to announcements
    page 2, page-size 50.
- `tests/e2e/test_portfolio_treemap.py`: add an assertion that
  `_open_treemap()` results in `?view=treemap` in `page.url`, and that
  closing the position popup (existing `test_closing_popup_does_not_navigate`)
  still leaves `page.url` unchanged (extend that existing test rather than
  adding a new one).
  - **Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.
- `tests/e2e/test_x_post_history.py`: add an assertion that
  `_open_x_history()` results in `?view=x-history` in `page.url`, and that
  paging to page 2 (`xp-btn-next`) changes `page.url` to include `page=2`
  (the direct regression test for the reported bug).
- `tests/e2e/test_refresh.py`: add a new test —
  `test_refresh_preserves_page_and_filters` — that navigates to page 2,
  sets a filter, submits it (unlike the existing test, which fills but never
  submits), reloads, and asserts both the page label and the filter field's
  value survive. This is the explicit assertion the Key Discoveries section
  flagged as missing from the current suite.

#### Manual Testing Steps:

1. Log in, click through all three views via the menu, confirm each shows
   the expected URL.
2. On announcements, set two filters + page 2, copy the URL, open it in a
   new private window, log in there — confirm filters + page 2 reproduce.
3. On x-history, page forward twice, hit browser back twice — confirm it
   steps back through x-history pages, not into announcements.
4. Open the treemap popup, hit Escape — confirm the URL did not change and
   browser back does not need an extra press to leave the treemap view.

## Performance Considerations

None — this is client-side URL bookkeeping on top of fetches that already
happen; no new network calls are introduced.

## Migration Notes

None — old-format bookmarks (`?page=2&page_size=20`) continue to resolve via
the "no recognized `view=` → announcements" default; no redirect or
one-time-rewrite logic is needed (see What We're NOT Doing).

## References

- Change folder: `context/changes/spa-view-url-routing/`
- Linear: PUL-52 · GitHub: #79
- Prior art this builds on: `static/index.html:894-932` (existing partial
  announcements History API integration from PUL-23)
- Related, already-shipped invariant this must not regress:
  `tests/e2e/test_portfolio_treemap.py::test_closing_popup_does_not_navigate`
  (PUL-51)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Routing core — view-level URLs

#### Automated

- [x] 1.1 Full test suite passes: `uv run pytest --tb=short` — a469523
- [x] 1.2 E2E suite passes: `uv run pytest tests/e2e/ -q` — a469523

#### Manual

- [x] 1.3 Clicking "Treemapa portfela" sets the URL to `...?view=treemap` — a469523
- [x] 1.4 Clicking "Historia postów X" sets the URL to `...?view=x-history` — a469523
- [x] 1.5 Clicking the "puls-gpw" topbar heading returns the URL to `/` (no `view=`) — a469523
- [x] 1.6 Refreshing while on `?view=treemap` lands back on the treemap view, not announcements — a469523
- [x] 1.7 Browser back after Treemapa → Historia postów X returns to the treemap view (URL shows `?view=treemap` again) — a469523

### Phase 2: Announcements — full state in the URL

#### Automated

- [x] 2.1 Full test suite passes: `uv run pytest --tb=short` — d1aa5b2
- [x] 2.2 E2E suite passes: `uv run pytest tests/e2e/ -q` — d1aa5b2
- [x] 2.3 `tests/e2e/test_pagination.py` passes unmodified — d1aa5b2
- [x] 2.4 `tests/e2e/test_refresh.py` passes (with the Phase 4 assertion additions) — d1aa5b2

#### Manual

- [x] 2.5 Setting a ticker filter and paging to page 2 shows both `ticker=` and `page=2` in the URL — d1aa5b2
- [x] 2.6 Refreshing on that URL restores the ticker filter field's value, the event-type dropdown's label, and page 2's data — d1aa5b2
- [x] 2.7 Setting a date filter, refreshing, re-opening the date field shows the same date/time (not shifted by timezone) — d1aa5b2
- [x] 2.8 An old-style bookmark (`?page=2&page_size=20`, no filters, no `view=`) still lands on announcements page 2 with empty filters — d1aa5b2

### Phase 3: X-history — full state in the URL (new ground)

#### Automated

- [x] 3.1 Full test suite passes: `uv run pytest --tb=short` — a13e78c
- [x] 3.2 E2E suite passes: `uv run pytest tests/e2e/ -q` — a13e78c

#### Manual

- [x] 3.3 Opening "Historia postów X" and clicking "Następna" changes the URL to include `view=x-history&page=2` — a13e78c
- [x] 3.4 Refreshing on that URL restores x-history view, page 2 — a13e78c
- [x] 3.5 Setting a window/status filter, refreshing, restores the dropdown selections — a13e78c
- [x] 3.6 Browser back after paging x-history steps back to page 1 of x-history (not to announcements) — a13e78c

### Phase 4: Polish, edge cases, and test coverage

#### Automated

- [x] 4.1 New `tests/e2e/test_url_routing.py` passes (view-switch URLs, back-navigation, deep link, refresh, old-format bookmark) — e7f60e5
- [x] 4.2 `tests/e2e/test_portfolio_treemap.py` updated assertions pass (view URL on open, popup close leaves URL unchanged) — e7f60e5
- [x] 4.3 `tests/e2e/test_x_post_history.py` updated assertions pass (view URL on open, page URL on next) — e7f60e5
- [x] 4.4 `tests/e2e/test_refresh.py::test_refresh_preserves_page_and_filters` passes — e7f60e5

#### Manual

- [x] 4.5 Log in, click through all three views via the menu, confirm each shows the expected URL — e7f60e5
- [x] 4.6 Filters + page 2 on announcements reproduce in a fresh private window via copied URL — e7f60e5
- [x] 4.7 Browser back twice on x-history after paging forward twice steps back through x-history pages, not into announcements — e7f60e5
- [x] 4.8 Treemap popup open + Escape leaves the URL unchanged and does not consume an extra back-press — e7f60e5
