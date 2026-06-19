# Admin UI: X post history view — Plan Brief

> Full plan: `context/changes/admin-ui-x-post-history/plan.md`
> Research: `context/changes/admin-ui-x-post-history/research.md`

## What & Why

Admins currently have no way to see what was ever posted to X — every
generated thread (published, skipped, failed, or partial) sits invisibly in
the `x_posts` BigQuery table. This adds a "Historia postów X" view to the
admin profile menu: a filterable, paginated table of every `x_posts` row,
with a popup that reconstructs the full numbered tweet thread per row.

## Starting Point

The dashboard has one content view today (the announcements table) and one
static profile-menu item ("Wyloguj"). `x_posts` already has all 7 needed
columns live in BigQuery, but no endpoint queries it directly — it's only
ever joined into `/announcements`. Every backend and frontend mechanic this
feature needs (pagination, filters, modal) already exists in proven form on
the `/announcements` admin table; the two genuinely new pieces are
role-conditional menu/view injection and switching the dashboard's main
content area between two views.

## Desired End State

Admin clicks "Historia postów X" in the profile menu → main content swaps to
a table of all X posts, newest first, filterable by window/status/post-text/
date-range. Clicking a row opens a popup with the thread split into numbered
tweets, each linking to the live tweet on X where known; failed/empty rows
show a clear "Brak treści" message. Clicking the "puls-gpw" heading returns
to the announcements table. A `user`-role session sees nothing of this —
no menu item, no DOM node, no network call, 403 on a manually-crafted
request.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Filter UI for window/status | `<select>` dropdowns | Closed 4-value enums — no typo risk, simpler than the autocomplete widget built for open text | Plan |
| View navigation | Toggle in place + explicit back-link on the "puls-gpw" heading | One clear way in (menu), one clear way out (heading), bound by `id` so future rebranding doesn't break it | Plan |
| Failed/empty post rows | Shown in table with `—` placeholders, popup says "Brak treści" | Matches change.md's status filter and supervisor_attempts column — hiding them would make "failed" unfilterable | Plan |
| Tweet popup links | Each numbered tweet links to the live tweet on X when its id is known | The practical reason to show tweet_ids at all is verifying what's actually public | Plan |
| Default backend date range | None — full table scan, same as `list_announcements_admin` | `x_posts` is 5 days old, ~3 rows/day — no scale problem to solve yet | Plan |
| E2E coverage | Included as Phase 5 of this plan (not deferred to a separate `/10x-e2e` pass) | Explicit user choice, overriding the project's usual split | Plan |
| Admin-only DOM | JS-constructed only when `role === 'admin'`, never rendered-then-hidden | Matches the existing "Usuń" column precedent; satisfies the "no DOM node at all for `user`" requirement | Research |
| Tweet reconstruction | Split `post_text` on `\n\n`, zip by index with `tweet_ids.split(',')` | Confirmed from `post_main.py:281` — exactly how the thread was joined before publishing | Research |

## Scope

**In scope:**
- `GET /admin/x-posts` (admin-gated, paginated, 4 filters)
- New `list_x_posts_admin()` BigQuery query function
- Admin-only "Historia postów X" menu item + view toggle + topbar home-link
- Filterable/paginated x-posts table (7 columns)
- Tweet-thread popup with live-tweet links and a failed/empty fallback
- Playwright E2E coverage

**Out of scope:**
- Editing or re-publishing X posts from this view (read-only v1)
- Deep-linking / browser-back support for the new view
- Any change to how `x_posts` rows are generated or written

## Architecture / Approach

Backend: one new query function (`list_x_posts_admin`, mirrors
`list_announcements_admin`'s shape) + one new `_require_admin`-gated
endpoint. Frontend: a role-conditional injection helper (mirrors
`renderHeaders`/`renderTable`'s pattern) builds the menu item and the entire
view's markup from scratch only for admin sessions; two toggle functions swap
the visible container; the existing generic modal gets one new body-branch
keyed by a `data-kind="xpost"` attribute.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Backend query + endpoint | `GET /admin/x-posts`, gated, filtered, paginated | Filter clause column-name mismatches (`posted_at` vs `published_at`) |
| 2. Frontend nav infrastructure | Admin-only menu item, view container, topbar home-link | Leftover DOM nodes if role switches mid-session without reload |
| 3. Frontend x-posts table | Filter form, fetch, render, pagination | CSS `#pagination-bar` is ID-scoped — needs class rename to share style |
| 4. Frontend tweet popup | Numbered thread + live-tweet links + fallback | Index mismatch between `post_text` tweets and `tweet_ids` on `partial` status |
| 5. E2E coverage | Playwright tests incl. user-sees-nothing assertions | Asserting DOM absence (`not_to_be_attached`), not just invisibility |

**Prerequisites:** None — `x_posts` table and all backend auth primitives already exist.
**Estimated effort:** ~2 sessions across 5 phases.

## Open Risks & Assumptions

- Assumes `post_text`'s `\n\n` join separator has had no exceptions since
  PUL-29 (2026-06-14) — if any row was ever saved with a different separator,
  that row's popup would show one run-on "tweet" instead of split lines
  (graceful degradation, not a crash).
- Assumes embedded `\n` characters survive unmodified through HTML `data-*`
  attribute parsing in all target browsers — verified against the `esc()`
  implementation and HTML5 attribute-parsing semantics, not yet verified
  with a live browser test until Phase 4's manual verification.

## Success Criteria (Summary)

- Admin sees all `x_posts` rows, newest first, filterable by window/status/
  post-text/date-range, with full thread content readable per row
- A `user`-role session has zero observable trace of this feature anywhere
  (menu, DOM, network, or a manually-crafted request)
- No regression to the existing announcements table, modal, or profile menu
