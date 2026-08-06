---
change_id: deploy-path-filter
title: Stop rebuilding and redeploying on documentation-only commits
status: archived
created: 2026-08-06
updated: 2026-08-06
archived_at: 2026-08-06T10:40:02Z
tracking:
  linear: PUL-122
  github: null
---

## Notes

`deploy.yml` triggers on every push to `master` with no path filter, so the two
documentation-only commits every 10x change produces (`chore(...): close out plan
(epilogue)` and `chore(archive): close ...`) each run the full pipeline: Playwright
install, full suite, `docker build`, `docker push`, four `gcloud run jobs update` and a
`gcloud run deploy`.

The fix is a `paths-ignore` on the push trigger. What makes it *sound* rather than merely
plausible is `.dockerignore`: skipping a deploy is only safe for paths that cannot reach
the image. `context/` is already excluded there, so a `context/**`-only commit provably
produces the same image. Markdown is not fully excluded — `AGENTS.md` and `CLAUDE.md`
reach the image through `COPY . .` — so ignoring `**.md` requires widening `.dockerignore`
first, not after.

That coupling between the two files is invisible and silently breakable, which is why it
gets a test rather than a comment.
