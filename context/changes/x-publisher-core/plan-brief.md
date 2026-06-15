# X Publisher Core — Plan Brief

> Full plan: `context/changes/x-publisher-core/plan.md`
> Research: `context/changes/x-publisher-core/research.md`

## What & Why

The puls-gpw post pipeline generates and supervisor-approves an X (Twitter) thread three times a
weekday but only **emails it to the owner** — it never posts to X. This change adds the missing
**publish step**: a `tweepy`/OAuth-1.0a client wired into the existing approved branch, gated by an
`X_AUTO_PUBLISH` flag (default OFF), that posts the thread, records the result in `x_posts`, and
reports it in the email.

## Starting Point

`post_main.py` (a one-shot Cloud Run Job) ends a successful run at `post_main.py:125-129`:
`save_x_post(...)` → `send_post_email(...)` → `return`. The `x_posts.tweet_ids` column already exists
(provisioned by PUL-29) but is never written. There is no publisher, no `tweepy`, no `X_AUTO_PUBLISH`,
no Secret-Manager API in code, no Sentry. The supervisor gates approval but occasionally lets an
empty/substance-less thread through.

## Desired End State

With the flag ON, a substantive approved thread is published to X (as a reply-chain), its tweet ids +
`x_publish_status='published'` are written to `x_posts`, and the owner email shows the tweet link.
With the flag OFF (default), behavior is identical to today (email only, status `skipped`). Empty
threads and already-published windows are never posted; mid-thread failures leave a `partial` record
and send an alert.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
| --- | --- | --- | --- |
| Scope | Client + pipeline wiring | Make publishing actually run end-to-end, not just a library | Research |
| Publish model | Auto, gated by `X_AUTO_PUBLISH` (default OFF) | Two-layer safety: code flag + pausable schedulers | Research |
| Status model | New `x_publish_status` column + write `tweet_ids` | Distinguish published / skipped / failed / partial in data | Plan |
| Compliance gate | Non-empty/substance guard only (no cap rebuild) | Supervisor stays the quality gate; avoid duplicate logic | Plan |
| Non-empty definition | Non-blank + ≥3 tweets + ≥1 company-ticker body tweet | Must contain a real company-analysis tweet, not just hook/closing | Plan (user) |
| Thread failure | Record `partial` + alert, no deletion | Matches prior art; no destructive API calls | Plan |
| Idempotency | Guard on x_posts before publish | Prevent double-posting on job re-run/retry | Plan |
| Library / auth | `tweepy` + OAuth 1.0a (4 user keys) | Proven, keys already in hand, simplest path | Plan |
| Deploy wiring | `deploy.yml` post step (flag + secret bindings + `--command`) | Auditable in VCS; also fixes the job's missing command | Plan |
| Notification | Extend `send_post_email` with publish result | Keep one email; always see what happened + tweet link | Plan |

## Scope

**In scope:**
- New `src/x_publisher.py` (tweepy OAuth-1.0a, singleton, thread reply-chain, partial handling)
- `x_posts` status column + migration + publish-result update + idempotency query
- `post_main.py` wiring: flag, non-empty/substance guard, idempotency guard, publish, persist, email
- `deploy.yml` + `infra.md` secret/flag/command wiring

**Out of scope:**
- Compliance caps (≤1 cashtag, ≤2 hashtags, known-ticker membership) — deferred
- OAuth 2.0 / token refresh; approval/draft UI; Sentry; tweet deletion/rollback
- `published_at` column; scheduler cadence changes
- Creating/setting Secret Manager secret values (human-only)

## Architecture / Approach

Thin transport module (`x_publisher`) does only publishing and returns ids (or a typed partial error).
The persistence layer (`db/bigquery.py`) gains a status column, an additive x_posts migration, a
publish-result update, and an idempotency query. The orchestration lives in `post_main.py`'s existing
`result.approved` branch: `save_x_post` (returns id) → guards (substance + idempotency) → `publish_thread`
→ `update_x_post_publish_result` → email. Flag OFF short-circuits to status `skipped`, leaving today's
behavior untouched.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Publisher module | tweepy OAuth-1.0a client + thread/partial logic, unit-tested | Mocking tweepy correctly; reply-chain ordering |
| 2. x_posts persistence | status column + migration + update + idempotency, real-BQ round-trip | Reserved-keyword `window`; x_posts migration is new |
| 3. post_main wiring | flag + guards + publish + status + email | Empty-post hard constraint must hold; no double-post |
| 4. Deploy & secrets | deploy.yml flag/secret/command + infra docs | Latent missing `--command`; human secret setup |

**Prerequisites:** X developer account with the four OAuth-1.0a keys; confirm X API write tier/limits
before flipping the flag ON; ability to create Secret Manager secrets (human).
**Estimated effort:** ~3-4 sessions across 4 phases (phases 1 & 2 parallelizable).

## Open Risks & Assumptions

- **X API free-tier write limits (2026)** may not cover 3 threads/weekday — confirm before enabling
  (external; handle 429/403 as `failed`/`partial` + alert, no retry storm).
- **OAuth 1.0a** assumed correct (matches prior manual posting); confirm with the actual account.
- **Non-atomic `save_x_post`** + idempotency guard rely on save-before-publish ordering.
- **Latent deploy bug**: `puls-gpw-post` may currently run `main.py` (no `--command`); Phase 4 fixes it
  but confirm the live job's behavior.

## Success Criteria (Summary)

- Flag OFF → pipeline behaves exactly as today (email only); flag ON → thread appears on X with ids +
  `published` recorded and a tweet link in the email.
- An empty/substance-less thread is never published (hard constraint), and re-running a window never
  double-posts.
- Publish failures/partials are recorded with status and surfaced via alert email — the job still
  completes.
