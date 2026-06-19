# Admin UI: X post history view — Implementation Plan

## Overview

Add an admin-only "Historia postów X" view to the dashboard: a paginated,
filterable table over the `x_posts` BigQuery table (every generated X thread —
published, skipped, failed, or partial), with a row-click popup that
reconstructs the full numbered tweet thread. The feature must be invisible
end-to-end to non-admin sessions — no menu item, no DOM node, no network
request, no data, even under devtools inspection.

## Current State Analysis

- `static/index.html` is a single static HTML file + vanilla JS, no build
  step, no framework. The profile dropdown (`static/index.html:255-262`,
  shipped in PUL-47) currently has one static `<li>` ("Wyloguj"). The
  dashboard has exactly one content view; the only view-switch precedent is
  `login-screen` ↔ `dashboard-screen` (`static/index.html:585-604`).
- The existing `/announcements` admin table already proves every backend and
  frontend pattern this feature needs: paginated OFFSET/LIMIT queries
  (`db/bigquery.py:544-618`), parameterized exact/LIKE/date-range filters
  (`db/bigquery.py:514-541`), a generic `#modal-overlay`/`openModal()` popup
  driven by `data-*` attributes, and `_require_admin` true-403 gating
  (`src/api.py:52-55`).
- `x_posts` (`db/bigquery.py:69-75`) already has all 7 needed columns
  (`x_post_id`, `window`, `post_text`, `tweet_ids`, `posted_at`,
  `supervisor_attempts`, `x_publish_status`) but no endpoint queries it
  standalone today — only via a `LEFT JOIN` inside `list_announcements_admin`.
- Confirmed in `post_main.py:281` and `db/bigquery.py:742-785`: `post_text` is
  saved as `"\n\n".join(post.tweets)`; `tweet_ids` is saved comma-joined in
  the same publish order, `NULL` when nothing was published (skipped/failed).
  This is exactly what's needed to reconstruct the numbered thread in the
  popup.
- The codebase's only precedent for admin-only **UI elements** (not just
  endpoints) is the "Usuń" delete column: `renderHeaders`/`renderTable`
  (`static/index.html:625,673-717`) branch on role and never *generate* the
  admin-only markup for `user` sessions — they don't render-then-hide it.
  This view must follow that same JS-driven-conditional-construction pattern,
  per the user's explicit requirement that a `user` session see nothing in
  devtools/Elements/Network for this feature.

## Desired End State

An admin user opens the profile menu, sees a second item "Historia postów X"
above "Wyloguj", clicks it, and the dashboard's main content swaps to a
filterable, paginated table of `x_posts` rows (newest first). Clicking a row
opens the existing modal showing each tweet of the thread numbered, with a
link to the live tweet on X where an id is known; failed/skipped rows show a
"Brak treści" message instead. Clicking the "puls-gpw" topbar heading (or the
menu item again) returns to the announcements table. A `user`-role session
sees none of this: no menu item, no extra DOM nodes, no `/admin/x-posts`
network call ever, and a manually-crafted request to that endpoint gets a
403 with no payload.

### Key Discoveries:

- `post_text` reconstruction: `post_text.split('\n\n')` gives tweets in
  publish order; `tweet_ids ? tweet_ids.split(',') : []` gives matching ids
  by the same index — `post_main.py:281`, `db/bigquery.py:755`.
- `window` values: `ranek`/`poludnie`/`wieczor` (+ `test` in dev) —
  `post_main.py:180-218`. `x_publish_status` values:
  `published`/`skipped`/`failed`/`partial`, `NULL` for legacy rows —
  `post_main.py:154-178`.
- CSS `#pagination-bar` / `#pagination-bar button`
  (`static/index.html:220-230`) are ID-scoped, not class-scoped — the new
  view needs its own pagination bar with the same look, so this CSS must
  move to a `.pagination-bar` class shared by both bars. `.filters` and
  `.table-wrap` are already class-scoped and directly reusable as-is.
- `tests/e2e/conftest.py:39-64` patches `src.api.list_announcements_admin`
  et al. with fixed fake rows for the live E2E server — `list_x_posts_admin`
  needs the same treatment for E2E coverage.

## What We're NOT Doing

- No editing or re-publishing from the history view — read-only, per
  change.md.
- No total-count "page X of Y" pagination — Prev/Next based on
  `data.length < page_size`, matching the explicit house-style decision in
  `context/archive/2026-06-12-pagination/plan.md`.
- No deep-linking / browser-back support for the new view (no
  `history.pushState`) — it's a secondary admin utility view, not primary
  navigation; only the announcements view keeps that behavior.
- No default date-range restriction on the backend query — `x_posts` has
  existed only since 2026-06-14 (PUL-29) and accumulates ~3 rows/day, so an
  unfiltered full-table scan is trivial; this mirrors
  `list_announcements_admin`'s no-default precedent exactly.
- No changes to `post_main.py`, `x_publisher.py`, or how `x_posts` rows are
  written — this is a read-only reporting view over existing data.

## Implementation Approach

Clone the proven `/announcements` admin pattern for the backend (new query
function + new gated endpoint), and for the frontend's table/filter/
pagination/modal mechanics. The two genuinely new pieces — admin-only DOM
construction and in-dashboard view switching — get built by mirroring the
`renderHeaders`/`renderTable` role-branching pattern (construct-or-don't,
never hide-after-construct) rather than introducing new infrastructure.

## Critical Implementation Details

- **Role-conditional DOM construction must be idempotent across re-logins in
  the same tab.** `showDashboard(r)` can run multiple times in one page
  session (logout → login as a different role, no full reload). The
  injection helper must remove any previously-injected `#x-history-btn` /
  `#x-history-view` before conditionally re-adding them for `r === 'admin'`
  — otherwise a `user` session that follows an `admin` session in the same
  tab would inherit leftover DOM nodes.
- **Newlines survive inside HTML `data-*` attributes.** `esc()`
  (`static/index.html:791-795`) only escapes `& < > " '`, not whitespace, and
  HTML attribute parsing preserves embedded `\n` literally — confirmed
  necessary because the modal needs `post_text`'s `\n\n` separators intact in
  `dataset.postText` to split it back into individual tweets.
- **Tweet/id zip must tolerate length mismatch without throwing.** For
  `partial` status, `tweet_ids` may have fewer entries than tweets in
  `post_text` (thread died mid-publish) — index into the ids array with a
  bounds check, never assume equal length.

## Phase 1: Backend — query function + endpoint

### Overview

Add a `list_x_posts_admin()` query function and a `GET /admin/x-posts`
endpoint, mirroring `list_announcements_admin()` / `GET /announcements`
exactly in shape but scoped to `x_posts` columns and gated by `_require_admin`
instead of `_get_role`.

### Changes Required:

#### 1. BigQuery query function

**File**: `db/bigquery.py`

**Intent**: Query `x_posts` directly (no join needed) with the four filters
the user needs: exact-match `window`, exact-match `x_publish_status`,
contains-match `post_text`, and a `posted_at` date range. Paginate the same
way as `list_announcements_admin` (OFFSET/LIMIT, no `COUNT(*)`).

**Contract**: New function `list_x_posts_admin(page=1, page_size=20,
window=None, x_publish_status=None, post_text=None, from_dt=None,
to_dt=None) -> list[dict]`, returning dicts keyed by the 7 `x_posts` columns.
Filter clause construction mirrors `_build_filter_clauses()`'s shape
(`db/bigquery.py:514-541`) — `` `window` = @window `` (parameter name stays
`@window`; only the column reference needs backticking),
`x_publish_status = @x_publish_status` (exact match precedent:
`ticker`/`event_type`), `LOWER(post_text) LIKE LOWER(@post_text)` with
`%...%` padding (precedent: `company`), `posted_at >= @from_dt AND
posted_at <= @to_dt` (precedent: `published_at` range) — write as a small
dedicated clause-builder local to this function rather than extending
`_build_filter_clauses` (different table/columns; same pattern, not the
same function). **`window` is a BigQuery reserved keyword** — backtick it
everywhere it's referenced as a column (SELECT list, WHERE clause); this is
the exact PUL-29 bug already fixed elsewhere in this file
(`db/bigquery.py:695,805` both use `` `window` ``) — don't regress it here.
`ORDER BY posted_at DESC LIMIT @page_size OFFSET @offset`, raises
`BigQueryError` on failure exactly like `list_announcements_admin`.

#### 2. API endpoint

**File**: `src/api.py`

**Intent**: Expose the query function as a true admin-gated endpoint —
non-admin and unauthenticated requests must get 401/403 with zero `x_posts`
data, never a filtered 200.

**Contract**: `GET /admin/x-posts` with `page`, `page_size` (same
`Query(..., ge=1, le=100)` bounds as `/announcements`), `window`,
`x_publish_status`, `post_text`, `from_dt`/`from`, `to_dt`/`to` — `role: Role
= Depends(_require_admin)` (not `_get_role`). Add a response model
`XPostAdmin(BaseModel)` mirroring `AnnouncementAdmin`'s pattern with the 7
`x_posts` fields, `model_config = ConfigDict(extra="ignore")`. Catches
`BigQueryError` → 500, same as `/announcements`.

### Success Criteria:

#### Automated Verification:

- [ ] Backend unit tests pass: `uv run pytest tests/test_bigquery.py -k x_posts_admin`
- [ ] API tests pass: `uv run pytest tests/test_api.py -k admin_x_posts`
- [ ] Full test suite still green: `uv run pytest tests/ --ignore=tests/e2e`

#### Manual Verification:

- [ ] `curl -H "X-API-Key: <admin-key>" .../admin/x-posts` returns real
      `x_posts` rows from BigQuery, newest first
- [ ] Same request with a `user` key returns `403`
- [ ] Same request with no key returns `401`
- [ ] `window=ranek`, `x_publish_status=published`, `post_text=PASSUS`, and
      `from`/`to` filters each narrow the result set as expected against
      live data

---

## Phase 2: Frontend — navigation infrastructure (admin-only)

### Overview

Build the role-conditional plumbing this view needs before any table markup
exists: the injected menu item, the injected (initially empty) view
container, the view-toggle functions, and the topbar home-link. Nothing in
this phase is x-posts-specific markup yet — it's the scaffolding Phase 3
fills in.

### Changes Required:

#### 1. Wrap the existing announcements markup

**File**: `static/index.html`

**Intent**: Give the existing filter-form + table-wrap a single container so
view-toggle code can show/hide it as a unit, without changing anything about
how it renders for either role today.

**Contract**: Wrap the existing `<form class="filters" id="filter-form">`
through `<div class="table-wrap">...</div>` block (`static/index.html:265-297`)
in a new `<div id="announcements-view">`. No id/class renames inside it, no
behavior change for either role.

#### 2. Add `id="topbar-home"` to the heading

**File**: `static/index.html`

**Intent**: Make the topbar title clickable to return to the announcements
view, bound by `id` so future copy or branding changes to the heading don't
break the handler.

**Contract**: `<h1 id="topbar-home">puls-gpw</h1>` (`static/index.html:254`).
Add `cursor: pointer` to `.topbar h1` in CSS for affordance. Bind a click
listener once at script init (not per-`showDashboard` call): `$('topbar-home').addEventListener('click', showAnnouncementsView)`.
This is generic dashboard chrome present for both roles — the handler itself
is a no-op for `user` sessions (there is nothing else to switch away from).

#### 3. Role-conditional injection helper

**File**: `static/index.html`

**Intent**: Mirror `renderHeaders`/`renderTable`'s pattern — admin-only DOM
is constructed from scratch only for `r === 'admin'`, never rendered then
hidden — for both the menu item and the (initially empty) view container.

**Contract**: New function `injectAdminOnlyChrome(r)`, called from
`showDashboard(r)` right after `renderHeaders(r)`. On every call: remove any
existing `#x-history-btn` `<li>` and `#x-history-view` element first
(idempotency across role-switching re-logins in one tab). If `r === 'admin'`:
create the `<li role="none"><button id="x-history-btn"
role="menuitem">Historia postów X</button></li>` and insert it into
`#profile-menu` *before* the existing "Wyloguj" `<li>`; create an empty
`<div id="x-history-view" style="display:none">` and append it as a sibling
of `#announcements-view` inside `#dashboard-screen`; wire
`$('x-history-btn').addEventListener('click', showXHistoryView)`. For
`r === 'user'`, after the removal step, nothing further happens — no menu
item, no container, anywhere in the DOM.

#### 4. View-toggle functions

**File**: `static/index.html`

**Intent**: Swap the visible content area between the two views; fetch
x-posts data only on demand (no prefetching), matching `fetchAnnouncements`'s
on-demand pattern.

**Contract**: `showAnnouncementsView()` sets `#announcements-view` display
back to visible and, if `#x-history-view` exists, hides it. `showXHistoryView()`
hides `#announcements-view`, shows `#x-history-view`, closes the profile menu,
and calls `fetchXPosts()` (defined in Phase 3) to load fresh data every time
it's opened.

**`popstate` guard**: the existing global `popstate` handler
(`static/index.html:548-556`) unconditionally calls `fetchAnnouncements(false)`
on any browser back/forward, with no check on which view is currently
visible — since the new view never pushes its own history state, a back/
forward navigation taken while `#x-history-view` is showing would otherwise
silently re-render the hidden announcements table without restoring it to
view. Add a `showAnnouncementsView()` call into the `popstate` handler
(alongside the existing `fetchAnnouncements(false)`) so any back/forward
navigation always lands back on the announcements view — the only view
whose state is ever actually pushed.

### Success Criteria:

#### Automated Verification:

- [ ] Existing test suite (unit + e2e) still passes unmodified:
      `uv run pytest tests/ --ignore=tests/e2e/test_x_post_history.py`

#### Manual Verification:

- [ ] Logging in as admin shows "Historia postów X" above "Wyloguj" in the
      profile menu; clicking it swaps to an (empty, until Phase 3) view
- [ ] Logging in as user: profile menu has only "Wyloguj"; Elements panel
      has no `#x-history-btn` or `#x-history-view` node anywhere in the DOM
- [ ] Clicking the "puls-gpw" heading from the (empty) x-history view returns
      to the announcements table
- [ ] Logging out as admin and back in as user in the same tab (no reload)
      leaves no leftover `#x-history-btn`/`#x-history-view` nodes
- [ ] From the announcements table, change page (pushes history state), open
      "Historia postów X", then press the browser Back button — the view
      returns to the announcements table (not left showing the x-history
      view with a stale/empty state)

---

## Phase 3: Frontend — x-posts table (filter, fetch, render, paginate)

### Overview

Fill `#x-history-view` with the actual filter form, table, and pagination
bar, cloning `fetchAnnouncements()`/`renderTable()`'s shape for the 7
`x_posts` columns and the 4 requested filters.

### Changes Required:

#### 1. CSS: make pagination-bar reusable

**File**: `static/index.html`

**Intent**: The existing `#pagination-bar` CSS (`static/index.html:220-230`)
is ID-scoped to the one existing bar; the new view needs an identically
styled second one.

**Contract**: Rename the CSS selectors from `#pagination-bar` /
`#pagination-bar button` to `.pagination-bar` / `.pagination-bar button`; add
`class="pagination-bar"` to the existing `<div id="pagination-bar">` (keep
its `id` for the existing JS lookups) so both the old and new pagination bars
share the rule.

#### 2. Injected filter form + table + pagination markup

**File**: `static/index.html`

**Intent**: Build the x-posts view's content as one HTML string inside
`injectAdminOnlyChrome` (Phase 2's container), so it only ever exists for
admin sessions — same DOM-footprint guarantee as the menu item.

**Contract**: Inside `#x-history-view`: a filter `<form id="xp-filter-form">`
(class `filters`) with `<select>` for `window` (options: blank +
Ranek/Południe/Wieczór/Test, values `ranek`/`poludnie`/`wieczor`/`test`),
`<select>` for `x_publish_status` (blank + Opublikowany/Pominięty/Nieudany/
Częściowy, values `published`/`skipped`/`failed`/`partial`), text input for
`post_text` (contains), two `.date-toggle` inputs for `from`/`to` (the
existing document-level `.date-toggle` focus/blur binder at
`static/index.html:558-562` picks these up automatically since it's a live
`querySelectorAll` over the whole document at script-init time — confirm the
new inputs exist in the DOM *before* that binder runs, i.e. build this
markup at `injectAdminOnlyChrome` time during `showDashboard`'s first call,
which happens after script init), a page-size `<select id="xp-f-page-size">`
(20/50/100, matching the existing one), and a submit button; a
`class="table-wrap"` div with a `<table>` (`<thead id="xp-table-head">`/
`<tbody id="xp-table-body">`); a `class="pagination-bar"` div with
`<button id="xp-btn-prev">`/`<button id="xp-btn-next">` + `<span
id="xp-page-label">`.

**Every injected element that `fetchXPosts()` or its handlers look up by id
must use a name distinct from the announcements view's** (`xp-` prefix, as
above) — `$()` is `document.getElementById` (`static/index.html:332`),
which silently resolves to the *first* matching element when ids collide.
Since `#x-history-view` is appended after `#announcements-view` (Phase 2),
reusing an existing id (e.g. `filter-form`, `f-page-size`, `btn-prev`,
`btn-next`, `page-label`, `table-head`, `table-body`) would make `$()` calls
inside the new code silently resolve to the *announcements* view's elements
instead of the intended x-history ones.

#### 3. Fetch + render + paginate logic

**File**: `static/index.html`

**Intent**: Mirror `fetchAnnouncements()`/`renderTable()` exactly, scoped to
`/admin/x-posts` and the 7 `x_posts` columns, with its own page-state
variable so it doesn't collide with the announcements table's `currentPage`.

**Contract**: `fetchXPosts(push)` parameter is unused/omitted (no
`pushState` per the "no deep-linking" decision) — build `URLSearchParams`
from the filter inputs, `fetch('/admin/x-posts?' + params, {headers:
{'X-API-Key': apiKey}})`, `401` → `doLogout()`, render rows into the new
table body. Columns left→right: Data (`posted_at`, Warsaw time via the same
`toLocaleString('sv-SE', {timeZone: 'Europe/Warsaw', ...})` pattern as
`static/index.html:681-684`), Okno (label-mapped), Status (label-mapped),
Post ID (`x_post_id`), Tweets ID (`tweet_ids`), Próby (`supervisor_attempts`),
Treść (first line / ~100 chars of `post_text`, ellipsised — full text only in
the popup). Each `<tr>` gets `class="clickable"` + `data-*` attributes
including `data-kind="xpost"` (so `openModal` in Phase 4 can branch) and the
raw (escaped) `post_text`/`tweet_ids` for thread reconstruction. Pagination:
own `xpPage` variable, looked up and updated via the `xp-`-prefixed ids from
Changes Required #2 (`$('xp-btn-prev')`, `$('xp-btn-next')`,
`$('xp-page-label')`, `$('xp-f-page-size')`) — never the bare
`btn-prev`/`btn-next`/`page-label`/`f-page-size` ids, which belong to the
announcements view. Prev/Next disabled the same way as the existing bar
(`xpPage === 1` / `data.length < page_size` equivalents).

### Success Criteria:

#### Automated Verification:

- [ ] Existing test suite still passes: `uv run pytest tests/ --ignore=tests/e2e`

#### Manual Verification:

- [ ] Admin sees all 7 columns populated with real data, newest first
- [ ] Each filter (window, status, post_text contains "PASSUS"/"PAS",
      date range) narrows results correctly against live data
- [ ] Page-size select and Prev/Next page through results correctly;
      Prev disabled on page 1, Next disabled when a page returns fewer rows
      than `page_size`
- [ ] Switching page size resets to page 1
- [ ] No `/admin/x-posts` request fires until the menu item is clicked
      (confirm in Network tab) — no prefetch on dashboard load

---

## Phase 4: Frontend — tweet-thread popup

### Overview

Extend the existing generic `openModal()`/`closeModal()` infrastructure with
an x-posts-specific body branch: reconstruct the numbered thread from
`post_text`/`tweet_ids`, link out to live tweets where an id is known, and
show a clear fallback for rows with no content.

### Changes Required:

#### 1. Modal body branch for x-posts rows

**File**: `static/index.html`

**Intent**: `openModal(d)` (`static/index.html:744-776`) currently has one
body-construction path (announcement analysis). Add a second path keyed on
`d.kind === 'xpost'` that renders the thread instead.

**Contract**: When `d.kind === 'xpost'`: title = window label + date; if
`post_text` is empty/null (failed/skipped with nothing generated), body =
`"Brak treści — supervisor odrzucił wszystkie próby."` (or a skipped-specific
variant); otherwise split `d.postText.split('\n\n')` into tweets, split
`d.tweetIds ? d.tweetIds.split(',') : []` into ids, render each tweet as
`<div class="modal-section"><h4>Tweet i/N</h4><p>${esc(tweet)}</p>` + (if
`ids[i]` exists) `<a href="https://x.com/i/web/status/${ids[i]}"
target="_blank">zobacz na X →</a>` `</div>`. Index lookups must use
`ids[i]` (which is `undefined`, not a thrown error, past array end) — no
length-equality assumption given `partial` status can have fewer ids than
tweets.

### Success Criteria:

#### Automated Verification:

- [ ] Existing test suite still passes: `uv run pytest tests/ --ignore=tests/e2e`

#### Manual Verification:

- [ ] Clicking a `published` row's row shows all tweets numbered (i/N) with
      working "zobacz na X" links opening the correct live tweet
- [ ] Clicking a `partial` row shows the live-tweet link only for the tweets
      that actually have an id
- [ ] Clicking a `failed` or `skipped`-with-no-text row shows the
      "Brak treści" fallback, not a broken/empty popup
- [ ] Modal close (✕, overlay click, Escape) behaves exactly as it does for
      announcement rows today — no regression

---

## Phase 5: E2E coverage

### Overview

Add Playwright coverage for the full feature: admin-only visibility (menu
item, DOM, network), view toggle + topbar return, filtering, pagination, and
popup rendering for both populated and empty-content rows.

### Changes Required:

#### 1. Fake x-posts fixture data for the live E2E server

**File**: `tests/e2e/conftest.py`

**Intent**: The `live_server_url` fixture (`tests/e2e/conftest.py:39-64`)
patches `src.api.list_announcements_admin` et al. with fixed fake rows for
deterministic E2E runs — `list_x_posts_admin` needs the same treatment so
x-history tests have predictable data without hitting real BigQuery.

**Contract**: Add a `_FAKE_X_POSTS_ROWS` list (mix of `published`, `partial`,
and `failed` statuses, covering the popup's three render paths) and patch
`src.api.list_x_posts_admin` alongside the existing patches in the same
`with (...)` block.

#### 2. E2E test file

**File**: `tests/e2e/test_x_post_history.py`

**Intent**: Cover the acceptance criteria end-to-end using Playwright
role-based locators, following `tests/e2e/test_profile_menu.py`'s
conventions (`get_by_role`, no CSS/XPath selectors, no
`page.waitForTimeout()`).

**Contract**: Tests (admin session, using the `_login` helper pattern from
`test_profile_menu.py`): menu shows "Historia postów X" above "Wyloguj";
clicking it renders the x-posts table; clicking the "puls-gpw" heading
returns to the announcements table; filtering by window/status narrows
visible rows; clicking a row with thread content opens the modal showing
numbered tweets; clicking a `failed` row shows the "Brak treści" fallback.
Separate test(s) for a `user`-role session: `get_by_role("menuitem", name="Historia postów X")`
is not present (`expect(...).not_to_be_attached()` or equivalent, not just
`to_be_hidden()`, to assert it's absent from the DOM, not just invisible).

### Success Criteria:

#### Automated Verification:

- [ ] E2E suite passes: `uv run pytest tests/e2e/test_x_post_history.py`
- [ ] Full E2E suite still green (no regressions): `uv run pytest tests/e2e/`

#### Manual Verification:

- [ ] Re-run the Phase 2-4 manual verification steps once more after E2E
      tests land, confirming no regression was introduced while wiring test
      fixtures

---

## Testing Strategy

### Unit Tests:

- `list_x_posts_admin()`: no filters selects all; each of the 4 filters
  passes the right parameter; offset math for page > 1 (mirror the existing
  `list_announcements_admin` test shapes in `tests/test_bigquery.py:503-549`);
  assert the emitted SQL backticks `` `window` `` wherever it's referenced as
  a column — mirrors the PUL-29 regression test pattern in
  `context/foundation/lessons.md` (`assert "`window`" in query`)
- `GET /admin/x-posts`: admin 200 with rows; user 403; no key 401; BQ error
  500; each filter query-param passed through to the mocked query function
  (mirror `tests/test_api.py:48-134`)

### Integration Tests:

- None beyond the API-level tests above — no new cross-service integration
  surface.

### Manual Testing Steps:

1. Log in as admin, open profile menu, confirm "Historia postów X" appears
   above "Wyloguj"
2. Click it, confirm the table loads with real `x_posts` data newest-first
3. Apply each filter individually and in combination, confirm results narrow
   correctly
4. Click a `published`/`partial` row, confirm numbered tweets + working X
   links; click a `failed` row, confirm the fallback message
5. Click the "puls-gpw" heading, confirm return to announcements table
6. Log out, log in as user: confirm no menu item, no DOM node (Elements
   panel), no `/admin/x-posts` call ever (Network tab), and that manually
   running `fetch('/admin/x-posts', {headers: {'X-API-Key': '<user-key>'}})`
   in the console returns 403 with no `x_posts` data

## Performance Considerations

None beyond what `list_announcements_admin` already handles — `x_posts` is a
small, low-write-volume table (~3 rows/day); no indexing or caching changes
needed.

## Migration Notes

None — purely additive (new function, new endpoint, new UI), no schema or
existing-row changes.

## References

- Related research: `context/changes/admin-ui-x-post-history/research.md`
- Pattern to mirror (backend): `db/bigquery.py:514-618` (`_build_filter_clauses`, `list_announcements_admin`)
- Pattern to mirror (auth): `src/api.py:40-55` (`_get_role`, `_require_admin`)
- Pattern to mirror (frontend table/modal): `static/index.html:631-789`
- Admin-only DOM precedent: `static/index.html:597-601,625,673-722` (`showDashboard`, `renderHeaders`, `renderTable`, `.btn-del` wiring)
- Tweet/post_text provenance: `post_main.py:281`, `db/bigquery.py:674-785`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Backend — query function + endpoint

#### Automated

- [x] 1.1 Backend unit tests pass: `uv run pytest tests/test_bigquery.py -k x_posts_admin` — c9de7be
- [x] 1.2 API tests pass: `uv run pytest tests/test_api.py -k admin_x_posts` — c9de7be
- [x] 1.3 Full test suite still green: `uv run pytest tests/ --ignore=tests/e2e` — c9de7be

#### Manual

- [x] 1.4 Admin curl to `/admin/x-posts` returns real `x_posts` rows newest-first — c9de7be
- [x] 1.5 User key on `/admin/x-posts` returns 403 — c9de7be
- [x] 1.6 No key on `/admin/x-posts` returns 401 — c9de7be
- [x] 1.7 Each filter (window, x_publish_status, post_text, from/to) narrows results against live data — c9de7be

### Phase 2: Frontend — navigation infrastructure (admin-only)

#### Automated

- [x] 2.1 Existing test suite still passes unmodified: `uv run pytest tests/ --ignore=tests/e2e/test_x_post_history.py` — 6e6e6f0

#### Manual

- [x] 2.2 Admin sees "Historia postów X" above "Wyloguj"; clicking it swaps views — 6e6e6f0
- [x] 2.3 User sees only "Wyloguj"; no `#x-history-btn`/`#x-history-view` node in Elements panel — 6e6e6f0
- [x] 2.4 Clicking "puls-gpw" heading from x-history view returns to announcements table — 6e6e6f0
- [x] 2.5 Logout-as-admin then login-as-user in same tab leaves no leftover injected nodes — 6e6e6f0
- [x] 2.6 Paginate announcements, open x-history view, press browser Back — returns to announcements view (popstate guard) — 6e6e6f0

### Phase 3: Frontend — x-posts table (filter, fetch, render, paginate)

#### Automated

- [ ] 3.1 Existing test suite still passes: `uv run pytest tests/ --ignore=tests/e2e`

#### Manual

- [ ] 3.2 All 7 columns populated with real data, newest first
- [ ] 3.3 Each filter narrows results correctly against live data
- [ ] 3.4 Page-size select and Prev/Next page correctly; disabled states correct
- [ ] 3.5 Changing page size resets to page 1
- [ ] 3.6 No `/admin/x-posts` request fires before the menu item is clicked

### Phase 4: Frontend — tweet-thread popup

#### Automated

- [ ] 4.1 Existing test suite still passes: `uv run pytest tests/ --ignore=tests/e2e`

#### Manual

- [ ] 4.2 Published row popup shows numbered tweets with working X links
- [ ] 4.3 Partial row popup shows links only for tweets that have an id
- [ ] 4.4 Failed/empty row popup shows "Brak treści" fallback
- [ ] 4.5 Modal close behaviors (✕, overlay click, Escape) unchanged for announcement rows

### Phase 5: E2E coverage

#### Automated

- [ ] 5.1 New E2E suite passes: `uv run pytest tests/e2e/test_x_post_history.py`
- [ ] 5.2 Full E2E suite still green: `uv run pytest tests/e2e/`

#### Manual

- [ ] 5.3 Re-run Phase 2-4 manual checks once more after E2E fixtures land
