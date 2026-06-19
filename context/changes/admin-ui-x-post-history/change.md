---
change_id: admin-ui-x-post-history
title: Admin UI: X post history view
status: implemented
created: 2026-06-19
updated: 2026-06-20
archived_at: null
tracking:
  linear: PUL-44
  github: 62
---

## Notes

Admin-only view listing previously published X threads (morning ESPI digests,
portfolio summaries, etc.) with the ability to read the full post content.

Data source: `espi_ebi.x_posts` (`x_post_id`, `window`, `post_text`,
`tweet_ids`, `posted_at`, `x_publish_status`) — table already exists.

Scope:
- Frontend: new "Historia postów X" item in the admin profile menu
  (`static/index.html`), admin-only end-to-end (no menu item, DOM node, or
  network call for non-admin sessions). Table columns: date (`posted_at`,
  Warsaw time), window (`window`), status (`x_publish_status`), post ID
  (`x_post_id`), tweet IDs (`tweet_ids`), supervisor attempts
  (`supervisor_attempts`), preview (first line of `post_text`). Row click →
  popup with full thread (tweets split + numbered, linked to the live tweet
  on X where known). Pagination, newest first.
- Backend (`src/api.py`): `GET /admin/x-posts`, admin-only (`_require_admin`,
  403 for non-admin), paginated (page / page_size), filterable by `window`
  (exact), `x_publish_status` (exact), `post_text` (contains), and a
  `posted_at` date range.

Acceptance criteria:
- Admin sees all X-post attempts (published, skipped, failed, partial)
  sorted by date descending, filterable by window/status/post-text/date-range
- Full post content (numbered thread) readable per entry; failed/empty
  entries show a clear fallback instead of blank content
- Non-admin users get 403, and see no trace of the feature in the UI, DOM,
  or network requests
- Endpoint is paginated

Out of scope: editing/re-publishing from the history view (read-only v1);
deep-linking/back-button support for the new view.

Linear: https://linear.app/puls-gpw/issue/PUL-44/admin-ui-x-post-history-view-published-threads-morning-portfolio-etc
