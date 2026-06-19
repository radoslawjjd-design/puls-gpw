---
date: 2026-06-19T00:00:00+02:00
researcher: Claude (Sonnet 4.6)
git_commit: 9ae2a4758b6cf8c5f3658ef314ae53239ae7d8ed
branch: radoslawjjd/pul-47-ui-profile-menu-button-role-icon-dropdown-hosting-logout
repository: puls-gpw
topic: "Admin UI: X post history view — add 'Historia postów X' to profile dropdown, new filterable x_posts table + detail popup"
tags: [research, codebase, frontend, bigquery, x-posts, admin-ui, profile-menu]
status: complete
last_updated: 2026-06-19
last_updated_by: Claude (Sonnet 4.6)
last_updated_note: "Added follow-up research resolving admin-only visibility (Open Question 2) — user must see nothing related to this feature in devtools/network/responses"
---

# Research: Admin UI X-post history view

**Date**: 2026-06-19
**Researcher**: Claude (Sonnet 4.6)
**Git Commit**: 9ae2a4758b6cf8c5f3658ef314ae53239ae7d8ed
**Branch**: radoslawjjd/pul-47-ui-profile-menu-button-role-icon-dropdown-hosting-logout
**Repository**: puls-gpw

## Research Question

User wants to add a second item to the profile dropdown (which currently only
has "Wyloguj") that opens a new view: a table listing every row of the
`x_posts` BigQuery table instead of the current analyses table. Columns
(left→right): Data (`posted_at`, Warsaw time), Okno (`window`), Status
(`x_publish_status`), Post ID (`x_post_id`), Tweets ID (`tweet_ids`), Próby
(`supervisor_attempts`), Treść (`post_text`). Row click → popup with a nicely
formatted view of the post. Filters: `window`, `post_text` (contains, e.g.
"PASSUS"/"PAS" should match), `x_publish_status`, and a date range.

This maps directly onto the existing-but-unimplemented change
`context/changes/admin-ui-x-post-history/change.md` (PUL-44 / GitHub #62,
status was `new`, now `preparing`). The user's filter requirements
(post_text contains, status, date range) are broader than that change's
original scope note ("optional filter by `window`" only) — see Open
Questions.

## Summary

Everything the user described is buildable by **directly reusing four
already-proven patterns** in this codebase — no new infrastructure needed:

1. **Dropdown menu item** — `static/index.html` profile menu (just shipped
   in PUL-47) is plain static HTML; adding a second `<li role="none">` next
   to the logout button is a one-line-pattern change.
2. **Table + pagination + filter form + row-click modal** — the existing
   `/announcements` admin table (`static/index.html` table/filter/modal code,
   backed by `list_announcements_admin()` in `db/bigquery.py`) already
   implements paginated OFFSET/LIMIT queries, parameterized filters
   (exact-match, `LIKE` substring, date-range), and a generic
   `#modal-overlay`/`openModal()` popup driven by `data-*` attributes on the
   `<tr>`. All of this can be cloned for `x_posts` with no architectural
   changes.
3. **`x_posts` schema** — already exists in BigQuery exactly as the user
   described, including `supervisor_attempts` (the one field absent from the
   original change.md scope note but present in the live schema and already
   surfaced elsewhere in the app).
4. **Warsaw timezone display + contains-filter + date-range** — all three
   have direct precedent to copy (`Intl.DateTimeFormat` with
   `timeZone: 'Europe/Warsaw'` client-side; `LOWER(col) LIKE LOWER(@p)` with
   `%...%` padding for contains; `col >= @from AND col <= @to` for range).

The one genuinely new piece is the **admin-only gate**: `/announcements` is
role-*branching* (`_get_role`, both roles see it, fields differ), but the
change.md for this feature calls for true admin-*only* 403-for-non-admin
behavior, like `DELETE /announcements/{id}` and `/admin/x-posts` (per
change.md) — i.e. use `_require_admin`, not `_get_role`.

## Detailed Findings

### 1. Profile dropdown (where the new menu item goes)

- Static HTML, no JS-generated menu items (`static/index.html:255-262`):
  ```html
  <div class="profile-menu-wrap">
    <button id="profile-menu-btn" aria-haspopup="true" aria-expanded="false" aria-controls="profile-menu">
      <span id="role-badge"></span> ☰
    </button>
    <ul id="profile-menu" class="profile-menu" role="menu" hidden>
      <li role="none"><button id="logout-btn" role="menuitem">Wyloguj</button></li>
    </ul>
  </div>
  ```
- To add "Historia postów X": add a sibling `<li role="none"><button id="x-history-btn" role="menuitem">Historia postów X</button></li>` — same `.profile-menu button` CSS applies automatically (`static/index.html:82-87`).
- Open/close logic (`static/index.html:528-546`): `openProfileMenu()`/`closeProfileMenu()` toggle the `hidden` attribute + `aria-expanded`, manage focus; outside-click handler explicitly excludes clicks inside `#profile-menu-btn`/`#profile-menu` (critical detail called out in the archived plan, `context/archive/2026-06-19-profile-menu-dropdown/plan.md`).
- Escape key closes both the modal and the profile menu (`static/index.html:783-788`) — a new view should not break this.
- **No role-based menu item visibility exists yet** — both admin and user currently see the same single item. If "Historia postów X" should be admin-only in the UI (matching the admin-only backend), this will be the first instance of conditional menu rendering; the `role` JS variable (already used elsewhere, e.g. `static/index.html:758`) is available for this check.
- **View switching**: the dashboard currently has exactly one content view (the analyses table); switching is only ever `#login-screen` ↔ `#dashboard-screen` via `style.display` (`static/index.html:585-604` per the profile-menu plan). There is no existing "swap the main content area" mechanism *within* the dashboard — this will be new: add a sibling container (e.g. `#x-history-view`, `style="display:none"`) and toggle `display` between it and the existing table section when the menu item is clicked.
- E2E selectors follow Playwright role-based locators (`tests/e2e/test_profile_menu.py`): `get_by_role("button", name=...)`, `get_by_role("menuitem", name="Wyloguj")` — a new test should use `get_by_role("menuitem", name="Historia postów X")`.

### 2. Existing table/filter/pagination/modal pattern to clone

- **Backend** (`src/api.py:115-157`, `db/bigquery.py:544-618`):
  - `GET /announcements` takes `page`, `page_size`, `ticker`, `company`, `event_type`, `from_dt` (alias `from`), `to_dt` (alias `to`); role-branches via `Depends(_get_role)`.
  - `list_announcements_admin()` builds `WHERE` via `_build_filter_clauses()` (`db/bigquery.py:514-541`) and runs `SELECT ... LIMIT @page_size OFFSET @offset` (no `COUNT(*)` — Prev/Next only, no "page X of Y"; this was an explicit decision in `context/archive/2026-06-12-pagination/plan.md`).
  - Auth dependencies (`src/api.py:40-55`): `_get_role` (role-branching, 401 if no/bad key) vs `_require_admin` (wraps `_get_role`, 403 if role != "admin"). **Use `_require_admin`** for the new endpoint per change.md ("admin-only").
- **Frontend table + modal** (`static/index.html:673-789`):
  - `renderTable(data, role)` builds `<tr>` strings with escaped `data-*` attributes (`modalAttrs`, line 690-694) and a `clickable` class.
  - `tbody.querySelectorAll('tr.clickable').forEach(tr => tr.addEventListener('click', () => openModal(tr.dataset)))` (line 725-727) — generic, reusable as-is for any table.
  - `openModal(d)` (line 744-776) reads from `dataset`, builds `bodyHtml`, sets `#modal-title`/`#modal-meta`/`#modal-body`, shows `#modal-overlay` (`display:flex`). `closeModal()` reverses it. The modal DOM/CSS (`static/index.html:301-310`, CSS `:138-173` incl. mobile bottom-sheet) is generic — only the body-construction logic inside `openModal` needs an x-posts-specific branch (e.g. split `tweet_ids`/`post_text` into a numbered thread view).
  - Filter form pattern (`static/index.html:265-285`, JS `static/index.html:631-670`): plain `<form id="filter-form">`, `URLSearchParams` built from input values on submit, `fetchAnnouncements()`-style fetch + `currentPage = 1` reset. No debouncing; filters apply on submit only.
  - Pagination controls: `#f-page-size` select (20/50/100) + Prev/Next buttons disabled via `currentPage===1` / `data.length < page_size`; `history.pushState`/`popstate` makes it back-button-able.

### 3. `x_posts` table — confirmed live schema

`db/bigquery.py:66-76`, dataset `espi_ebi` (env `BIGQUERY_DATASET`, default `"espi_ebi"`):

| Column | Type | Mode |
|---|---|---|
| `x_post_id` | STRING | REQUIRED |
| `window` | STRING | NULLABLE |
| `post_text` | STRING | NULLABLE |
| `tweet_ids` | STRING | NULLABLE |
| `posted_at` | TIMESTAMP | REQUIRED |
| `supervisor_attempts` | INTEGER | NULLABLE |
| `x_publish_status` | STRING | NULLABLE |

All seven user-requested columns exist under the exact names the user gave —
including `supervisor_attempts`, which is **not** in the original change.md
scope note but is real and already used elsewhere (`db/bigquery.py:575`,
`post_main.py:271-297`).

- **`window` values** (literals in `post_main.py:180-218`, `post_generator.py:18-38`): `"ranek"`, `"poludnie"`, `"wieczor"` (production), plus `"test"` (dev/UI-only).
- **`x_publish_status` values** (`post_main.py:154-177`, writer `db/bigquery.py:742-785`): `"published"`, `"skipped"`, `"failed"`, `"partial"`; legacy rows may have `NULL`.
- **`supervisor_attempts` semantics**: number of Gemini generation attempts before approval or final failure (1–3; written via `save_x_post(..., supervisor_attempts=attempt)` on success, `=3` on exhausted failure) — `post_main.py:271-297`, writer `db/bigquery.py:674-739` (`save_x_post`).
- **No existing endpoint queries `x_posts` standalone** — today it's only reached via the `LEFT JOIN` inside `list_announcements_admin()` (`db/bigquery.py:578-580`). A new function (e.g. `list_x_posts_admin()`) querying `x_posts` directly, mirroring `_build_filter_clauses()` + OFFSET/LIMIT, is the natural next step — and is exactly what change.md already specifies as the backend scope (`GET /admin/x-posts`).

### 4. Timezone, contains-filter, date-range — direct precedents

- **Warsaw time display**: client-side only, via `Intl`/`toLocaleString`:
  ```js
  new Date(row.published_at).toLocaleString('sv-SE', {
    timeZone: 'Europe/Warsaw', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit'
  })
  ```
  (`static/index.html:681-684`). Backend sends raw ISO timestamps; no server-side Warsaw conversion for API responses (server-side `ZoneInfo("Europe/Warsaw")` exists only in the scraper for parsing source HTML dates, `src/scraper.py:5,14,46,81` — unrelated to display formatting).
- **Contains filter** (for `post_text` matching "PASSUS"/"PAS"): exact precedent is the `company` filter —
  ```python
  clauses.append("LOWER(company) LIKE LOWER(@company)")
  params.append(bigquery.ScalarQueryParameter("company", "STRING", f"%{company}%"))
  ```
  (`db/bigquery.py:528-530`). Same shape applies to `LOWER(post_text) LIKE LOWER(@post_text)` with `%{q}%` padding.
- **Exact-match filter** (for `window`, `x_publish_status`): precedent is `ticker`/`event_type` —
  `clauses.append("window = @window")` style (`db/bigquery.py:525-527, 531-533`).
- **Date range**: precedent is `from_dt`/`to_dt` on `published_at` —
  `col >= @from_dt AND col <= @to_dt`, FastAPI params `from_dt: datetime | None = Query(None, alias="from")` / `to_dt` similarly (`db/bigquery.py:534-539`, `src/api.py:123-124,133-134`). Same pattern applies to `posted_at`.
- **Filter UI**: plain `<form>` with text/select/date inputs, `URLSearchParams` built on submit, no debounce (`static/index.html:265-285, 631-670`). The autocomplete-dropdown widget (`_setupAcInput()`, `static/index.html:413-434`) is reusable if `window`/`x_publish_status` filters should be dropdowns (closed enum, ~4 values each) rather than free text — likely a better fit than autocomplete given the small fixed value sets.

## Code References

- `static/index.html:255-262` — profile menu HTML (where to add the new `<li>`)
- `static/index.html:528-546` — open/close/outside-click logic for the dropdown
- `static/index.html:585-604` — only existing view-switch precedent (`login-screen` ↔ `dashboard-screen`), pattern to copy for a new in-dashboard view toggle
- `static/index.html:265-285` — filter form markup
- `static/index.html:631-670` — `fetchAnnouncements()` fetch/pagination logic
- `static/index.html:673-728` — `renderTable()` row + modal wiring
- `static/index.html:744-776` — `openModal()` body construction (extend for tweet-thread rendering)
- `static/index.html:681-684` — Warsaw timezone display pattern
- `src/api.py:40-55` — `_get_role` / `_require_admin` auth dependencies
- `src/api.py:115-157` — `GET /announcements` endpoint shape to mirror
- `db/bigquery.py:66-76` — `_X_POSTS_SCHEMA` (confirmed live schema)
- `db/bigquery.py:514-541` — `_build_filter_clauses()` (exact-match / LIKE / date-range precedents)
- `db/bigquery.py:544-618` — `list_announcements_admin()` (pagination/query shape to mirror for a new `list_x_posts_admin()`)
- `db/bigquery.py:674-739` — `save_x_post()` (confirms `supervisor_attempts` semantics)
- `post_main.py:154-218,271-297` — `window` / `x_publish_status` literal values, attempt-counting logic
- `tests/e2e/test_profile_menu.py` — role-based Playwright selector conventions for the dropdown

## Architecture Insights

- The codebase is intentionally a **single static HTML file + vanilla JS**, no build step, no frontend framework — any new view must follow this convention (no React/Vue, no bundler).
- **No total-count pagination anywhere** — `Page X of Y` is explicitly rejected precedent (`context/archive/2026-06-12-pagination/plan.md`); Prev/Next based on `data.length < page_size` is the house style.
- **Filters apply on explicit submit, not live/debounced** — consistent across the only existing filtered list.
- **Generic modal, specific body** — `#modal-overlay`/`openModal`/`closeModal` are already generic infrastructure; only the body-html branch is feature-specific. This significantly de-risks the "nicely visualize the post" popup requirement — it's a new `if`/`else` branch in `openModal`, not new infrastructure.
- **Role enforcement is inconsistent today**: `/announcements` is role-*branching* (everyone gets a response, content differs), while `DELETE /announcements/{id}` is role-*gating* (`_require_admin`, true 403). The new `/admin/x-posts` endpoint must use the gating pattern per change.md acceptance criteria ("Non-admin users get 403").

## Historical Context (from prior changes)

- `context/changes/admin-ui-x-post-history/change.md` — the governing change for this work (PUL-44, GitHub #62, status now `preparing`). Original scope only mentions filtering by `window`; the user's latest request adds `post_text` (contains), `x_publish_status`, and date-range filters — broader than the recorded scope (see Open Questions).
- `context/archive/2026-06-19-profile-menu-dropdown/plan.md` + `plan-brief.md` — just-shipped plan that built the dropdown itself (role icon, hosting info, logout). Establishes the dropdown's accessibility pattern (focus management, `aria-expanded`, outside-click guard) that any new menu item must respect.
- `context/archive/2026-06-12-pagination/plan.md` — introduced the page/page_size OFFSET pattern now standard for all list endpoints; explicitly ruled out total-count and cursor pagination.
- `context/archive/2026-06-19-session-inactivity-timeout/plan.md`, `context/archive/2026-06-11-auth-public-url/plan.md` — touch the same auth/role surface (`_get_role`/`_require_admin`) but not the table/filter pattern; not directly load-bearing for this feature.

## Related Research

- None yet under `context/changes/**/research.md` or `context/archive/**/research.md` specific to x_posts admin tooling — this is the first.

## Open Questions

1. **Scope mismatch**: change.md's recorded scope says "optional filter by `window`" only; the user now also wants `post_text` contains, `x_publish_status`, and date-range filters, plus exact column set including `supervisor_attempts` (not listed in change.md's data-source note, though the table support already has it). Recommend updating `change.md`'s scope/acceptance-criteria section during `/10x-plan` to reflect the full filter set before planning, since the current text understates what's being built.
2. **Menu item visibility**: should "Historia postów X" be visible to non-admin users (and 403 on click), or hidden entirely for non-admins via the `role` JS variable? No existing precedent for conditional menu items — first instance either way.
3. **`window`/`x_publish_status` filter UI**: dropdown/select (closed small enum) vs free-text — no strong precedent either way (existing filters use autocomplete-text for open-ended fields like company/ticker, but `window`/`x_publish_status` are small fixed enums, more like `event_type`, which is also rendered via the autocomplete widget today — worth deciding rather than defaulting).
4. **Tweet thread rendering in the popup**: `tweet_ids` is a comma-separated string and `post_text` is the full joined thread text — exact split logic (how to know where one tweet ends and the next begins within `post_text`) needs to be confirmed against how `post_main.py`/the X publisher originally split the thread, so the popup can reconstruct numbered tweets correctly rather than guessing a delimiter.

## Follow-up Research 2026-06-19 (later same session)

**User clarification**: the feature must be **admin-only end-to-end** — a
`user`-role session must see nothing related to it: no menu item, no DOM
node for it, no network request, no data in any response, even under
devtools inspection. This resolves Open Question 2 above.

### How the codebase already enforces this for other admin-only UI

The "Usuń" (delete) column and button are the existing precedent for
exactly this requirement, and the mechanism is **JS-driven conditional
DOM construction at dashboard-show time**, not static HTML + CSS
`display:none`:

- `showDashboard(r)` (`static/index.html:597-601`):
  ```js
  $('dashboard-screen').style.display = 'block';
  $('role-badge').textContent = r === 'admin' ? '👤 admin' : '👤 user';
  $('data-table').classList.toggle('admin-table', r === 'admin');
  renderHeaders(r);       // headers, including "Usuń" <th>, only built for r === 'admin'
  loadAutocomplete();
  ```
- `renderHeaders(r)` and `renderTable(data, r)` (`static/index.html:625, 673-717`) take the role and only emit admin-only markup (the "Usuń" `<th>`/`<td>` with `btn-del`) inside an `if (r === 'admin') { ... } else { ... }` branch — for a `user` session this HTML is **never generated**, not generated-then-hidden. So it never appears in the DOM, the Elements panel, or as a dead click target.
- The delete fetch itself (`deleteRow()`, `static/index.html:731-741`) is only ever wired up via `tbody.querySelectorAll('.btn-del').forEach(...)` (line 720-722) — since no `.btn-del` exists for `user`, the click handler is never attached, so the request can never fire from the UI for that role.

### Recommended approach for "Historia postów X"

Mirror this exact pattern instead of static-HTML-plus-hide:

1. **Don't** add the new `<li>` as static markup in the initial `#profile-menu` HTML (`static/index.html:259-262`). A `user` session loading the page would have the node present in the DOM (inspectable in Elements, even if CSS-hidden) — not what was asked.
2. **Do** inject the `<li>`/`<button>` into `#profile-menu` from JS, inside `showDashboard(r)` (or a small helper called from there), guarded by `if (r === 'admin')`, exactly like `renderHeaders(r)` already does for the "Usuń" column. For a `user` session, the element is simply never created.
3. **Wire the click handler only on the admin-injected button** — same as `.btn-del`'s handler is only attached to elements that exist. No event listener exists for `user` sessions, so there is nothing to trigger a fetch.
4. **Backend stays the hard boundary regardless of #1-3**: `GET /admin/x-posts` must use `_require_admin` (`src/api.py:52-55`), not `_get_role` — so even if a `user`-role session crafted the request manually from devtools/console (bypassing the UI entirely), the response is a `403 {"detail": "Admin access required"}` with zero `x_posts` data, never a 200 with filtered/stripped content. This is the same protection already used for `DELETE /announcements/{id}`.
5. **No prefetching**: the new view's data must only be fetched on-demand when the menu item is clicked (matching `fetchAnnouncements()`'s on-demand pattern) — never eagerly on dashboard load — so a `user` session's Network tab has no trace of an `/admin/x-posts` call even before any click could happen.

Net effect: for a `user` role, there is no menu item in the DOM, no event listener, no fetch ever issued, and even a manually-crafted request hits a 403 with no payload — satisfying "user should see nothing about this in devtools/network/responses."

### Updated code reference

- `static/index.html:597-601` — `showDashboard(r)`, the right place to hook admin-only menu injection
- `static/index.html:625, 673-717` — `renderHeaders`/`renderTable` admin-only-markup branching pattern to mirror for the menu item
- `static/index.html:720-722, 731-741` — `.btn-del` conditional-listener-attachment precedent
