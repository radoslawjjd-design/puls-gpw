---
change_id: ci-node24
title: Bump GitHub Actions to Node 24-compatible versions
status: plan_reviewed
created: 2026-06-16
updated: 2026-06-16
archived_at: null
tracking:
  linear: PUL-36
  github: 39
---

## Notes

Bump the GitHub Actions in `.github/workflows/deploy.yml` to Node 24-compatible majors — `actions/checkout`, `astral-sh/setup-uv`, `google-github-actions/auth`, `google-github-actions/setup-gcloud`. GitHub forces Node 24 as the default from 2026-06-16 and removes Node 20 from runners on 2026-09-16. Deploys currently succeed (warning only); this is preventive maintenance. Verify build + deploy still pass after the bump.
