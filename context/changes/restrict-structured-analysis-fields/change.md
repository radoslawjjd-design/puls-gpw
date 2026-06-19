---
change_id: restrict-structured-analysis-fields
title: Restrict sentiment and other internal fields inside structured_analysis from user-facing /announcements response
status: implementing
created: 2026-06-19
updated: 2026-06-19
archived_at: null
tracking:
  linear: PUL-46
  github: 64
---

## Notes

Follow-up to remove-analysis-score (PUL-42 / GH#60). Found during manual
verification of that change: `structured_analysis` in the user-facing
`/announcements` response still exposes `sentiment` (and possibly other
internal fields) to non-admin API keys. PUL-42's plan explicitly deferred
this ("Not restricting nested fields inside structured_analysis... to be
decided separately"). This change is that "separately" — decide the
user-facing allowlist for `structured_analysis` and enforce it, same leak
pattern/location as PUL-42 (`AnnouncementUser` / `list_announcements_user`).
Admin response must stay unaffected.
