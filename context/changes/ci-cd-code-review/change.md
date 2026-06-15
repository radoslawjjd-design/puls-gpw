---
change_id: ci-cd-code-review
title: CI/CD AI code-review pipeline (10xChampion path A)
status: implemented
created: 2026-06-15
updated: 2026-06-15
archived_at: null
tracking:
  linear: PUL-33
  github: 29
---

## Notes

10xChampion certification project (Module 5, path A — CI/CD code-review pipeline). An AI code-review agent runs on every pull request to `master`, scores the diff, and posts a binding verdict as a PR comment.

Stack: **Vercel AI SDK 6 + Gemini** (reuses the existing project Gemini API key — no new vendor/secret). Agent as a standalone Node/TS package → composite GitHub Action (pinned to SHA) → workflow on `pull_request` + `workflow_dispatch` → promptfoo eval suite (regression gate + Gemini Flash vs Pro comparison).

Adopting PR-based workflow (until now commits went straight to `master`). Reuses course skill `/10x-impl-review-ci` (m5l3). Requirements seeded in `requirements.md` (next: `/10x-research`).
