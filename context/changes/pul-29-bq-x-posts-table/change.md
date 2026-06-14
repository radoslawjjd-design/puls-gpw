---
change_id: pul-29-bq-x-posts-table
title: Extract x_posts table from announcements, remove post_text duplication
status: plan_reviewed
created: 2026-06-14
updated: 2026-06-14
archived_at: null
tracking:
  linear: PUL-29
  github: 25
---

## Notes

Schema change: `save_post_text()` currently writes the full X thread text to every
announcement row that contributed to the post (N companies = N identical copies of
`post_text`, `posted_at`, `supervisor_attempts`). Extract a dedicated `x_posts` table
and remove the duplication from `announcements`.

Foundation for PUL-27 (X auto-publish wiring). Linear PUL-29 status `ready`, High priority.
