---
change_id: xpost-generation
title: X-post generation + email delivery
status: plan_reviewed
created: 2026-06-08
updated: 2026-06-08
tracking:
  linear: null
  github: null
---

## Summary

Generates a 6-tweet X thread (hook + 4 per-company + summary) from top-4 approved
announcements in a time window, validates it with a rule-based supervisor (max 3 attempts),
saves post_text to BQ, and emails the ready-to-copy thread to the owner.

Three Cloud Scheduler triggers: 08:30, 13:00, 17:30 Europe/Warsaw.
The 13:00 slot is a no-op when fewer than 2 approved announcements exist in its window.

Also fixes the Gemini trailing-comma JSON bug (lessons.md) before adding new Gemini calls.
