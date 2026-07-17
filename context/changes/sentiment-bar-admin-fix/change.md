---
change_id: sentiment-bar-admin-fix
title: Fix sentiment 7-day bar — admin-only visibility and real data
status: implemented
created: 2026-07-17
updated: 2026-07-18
archived_at: null
tracking:
  linear: PUL-82
  github: 141
---

## Notes

fix the faro-v2 "Sentyment 7 dni" bar: add admin branch to /announcements/my-wallet (admin keeps sentiment + score), gate the bar behind role === 'admin' in static/index.html, e2e for both roles. Tracking: linear PUL-82, github 141

Root cause (verified in session 2026-07-17):
- `fetchWlSentimentSummary()` (static/index.html:1844) aggregates `structured_analysis.sentiment` + `analysis_score` from `GET /announcements/my-wallet`, but the endpoint unconditionally strips `sentiment` (src/api.py:432) and serializes through `AnnouncementUser` (no `analysis_score` field) — no admin branch, unlike `GET /announcements` (src/api.py:307). Bar shows zeros for every role.
- No role gate in the frontend — regular users see the (empty) bar; app convention is sentiment/score = admin-only (modal gates at static/index.html:3190). No data leak — backend strips the fields.
