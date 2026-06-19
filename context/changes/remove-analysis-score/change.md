---
change_id: remove-analysis-score
title: Remove analysis_score from user-facing /announcements response
status: plan_reviewed
created: 2026-06-19
updated: 2026-06-19
archived_at: null
tracking:
  linear: PUL-42
  github: 60
---

## Notes

API: remove analysis_score from user-facing /announcements response. Internal
scoring field, not shown in the UI table, currently leaks to non-admin users
via AnnouncementUser model (src/api.py:89) and list_announcements_user BQ
query (db/bigquery.py:644). Admin response must stay unaffected.
