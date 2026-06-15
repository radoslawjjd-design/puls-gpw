---
date: 2026-06-15T12:33:48+0200
researcher: Radek
git_commit: 08e4888427aa4b6af7f8cb4216a309be1936c4c1
branch: master
repository: puls-gpw
topic: "x-publisher-core — publish generated X posts to X, flag-gated, wired into post pipeline"
tags: [research, codebase, x-publisher, tweepy, oauth, cloud-run, compliance]
status: complete
last_updated: 2026-06-15
last_updated_by: Radek
---

# Research: x-publisher-core

**Date**: 2026-06-15T12:33:48+0200
**Researcher**: Radek
**Git Commit**: 08e4888427aa4b6af7f8cb4216a309be1936c4c1
**Branch**: master
**Repository**: puls-gpw

## Research Question

How does the current puls-gpw post pipeline generate and store X (Twitter) posts, and
where/how does a **publishing** step plug in — given the decided scope (**client + wiring
into the existing pipeline**) and the decided model (**auto-publish gated by an
`X_AUTO_PUBLISH` flag, default OFF**)? What already exists vs. what is new work? Prior art:
`oldProjectData/agents/x_publisher.py` (tweepy + OAuth 1.0a) — used as a reference for *how
it was done before*, not as code to copy.

## Summary

The current codebase **generates and stores** an X thread but **never publishes it** — the
only output channel is email to the owner. There is **no publisher, no tweepy, no OAuth, no
`X_AUTO_PUBLISH`, no Secret-Manager API usage in Python, no Sentry** in the live tree. This
change adds the publish step from scratch (prior art only as reference) and wires it into the
existing one-shot Cloud Run Job `post_main.py`.

Key facts that shape the plan:

1. **The hook point is precise and small.** A generated+approved post is "done" today at
   `post_main.py:125-129` (the `if result.approved:` branch: `save_x_post(...)` →
   `send_post_email(...)` → `return`). The publish call slots **between `save_x_post` and
   `send_post_email`**, inside that approved branch, where the per-tweet list `post.tweets`
   is still in hand (storage flattens it to one `"\n\n"`-joined string).

2. **PUL-29 already provisioned the landing column.** `x_posts.tweet_ids` (STRING, NULLABLE,
   `db/bigquery.py:60-67`) exists but is **never written** — PUL-29 explicitly deferred its
   population to "PUL-27 / X auto-publish wiring". This change is that wiring; it must write
   the returned tweet IDs back.

3. **Config convention is per-module inline `os.environ.get`, secrets injected as env vars at
   deploy.** No central config module, and Secret Manager is an infra concern only — code just
   reads `os.environ`. So X creds = 4 Secret-Manager secrets → 4 env vars, plus `X_AUTO_PUBLISH`.

4. **The compliance gate that the old project ran before publishing only partially exists.**
   `src/post_supervisor.py` enforces per-tweet 280, ticker *presence*, `#GPW` presence,
   disclaimer, advice-language block, tweet count, truncation. It does **not** cap cashtag
   count (≤1), cap hashtag count (≤2), or validate tickers against a known GPW universe. Those
   are new if we want the old project's "compliance fail-fast" parity.

5. **Two-layer safety is the established shape.** Default-OFF `X_AUTO_PUBLISH` read at
   `post_main.py` (code gate) + pausable Cloud Scheduler jobs (infra gate). Neither a publish
   flag nor a kill-switch exists today — both are net-new.

## Detailed Findings

### Area 1 — Post generation & storage pipeline

- **Generation output is a thread** (`GeneratedPost.tweets: list[str]`), `src/post_generator.py:150-152`.
  `generate_post(announcements, window, previous_issues)` at `post_generator.py:168`. Shape:
  `1 hook + 1 per company + 1 closing`, `expected_tweets = n_companies + 2`
  (`post_generator.py:213-214`). Top-N = 4 companies → 6-tweet thread.
- **Window concept**: `"ranek" | "poludnie" | "wieczor"` (`post_generator.py:216`); window only
  selects the hook variant (`post_generator.py:16-47, 217-220`). **No "sunday"/Premium long-form
  window** (the old project's 5000-char sunday path does not apply here).
- **Supervisor** `validate_post(post, tickers, expected_tweets)` is deterministic, no-Gemini,
  returns `ValidationResult(approved, issues)` (`src/post_supervisor.py:26-32`).
- **Entry point `post_main.py`** (`main()`, `post_main.py:65-144`) sequence:
  1. detect/parse window; exit 0 if none (`post_main.py:78-81`)
  2. schema setup incl. `create_x_posts_table_if_not_exists()` (`post_main.py:90-92`)
  3. `fetch_top_n_for_window(..., n=4)` (`post_main.py:95`)
  4. retry loop `_MAX_ATTEMPTS=3` (`post_main.py:119`): `generate_post` → `validate_post`
  5. **approved branch** (`post_main.py:125-129`): `save_x_post(ann_ids, "\n\n".join(post.tweets), window, attempt)` → `send_post_email(...)` → `return`  ← **publish hook here**
  6. all-fail: `save_x_post(ann_ids, None, ...)` + `send_no_post_email`
- **`_run_generate_post.py`** is a manual preview/email script, NOT production, not a publish hook.
- **No publish-to-X TODO/stub** exists in either entry point.

### Area 2 — x_posts schema & the publish marker

`_X_POSTS_SCHEMA` (`db/bigquery.py:60-67`):

| Column | Type | Mode |
|---|---|---|
| `x_post_id` | STRING | REQUIRED |
| `window` | STRING | NULLABLE |
| `post_text` | STRING | NULLABLE |
| `tweet_ids` | STRING | NULLABLE |
| `posted_at` | TIMESTAMP | REQUIRED |
| `supervisor_attempts` | INTEGER | NULLABLE |

- `save_x_post(...)` (`db/bigquery.py:505-570`) generates `x_post_id` (UUID hex), INSERTs the row
  but **omits `tweet_ids` from the INSERT column list** (`db/bigquery.py:524-529`), then UPDATEs
  `announcements.x_post_id`. **Non-atomic by design** (`db/bigquery.py:517-519`). It already
  **returns `x_post_id`** (`db/bigquery.py:570`) but `post_main.py:126` discards it.
- **`posted_at` is misleadingly named**: stamped `CURRENT_TIMESTAMP()` at insert
  (`db/bigquery.py:528`) = *generation* time, set even on failed attempts. NOT publish time.
- **`tweet_ids` is the half-built scaffold** (added under PUL-27, comments `db/bigquery.py:19,64`);
  never written anywhere. This change populates it.
- ⚠️ **Reserved-keyword lesson applies**: `window` is a BQ reserved word — any new hand-written
  SQL touching x_posts must backtick `` `window` `` and be round-tripped on real BQ
  (`scripts/test_bq.py` calling `ensure_schema_current()`), per `context/foundation/lessons.md`
  (PUL-29 bug). If a new publish-status column is added, migration is via `ensure_schema_current()`
  — but note that fn currently operates on the **announcements** `_SCHEMA`, not x_posts
  (`db/bigquery.py:135+`), so x_posts migration support may itself be new work.

### Area 3 — Secrets / config / GCP-init conventions

- **No central config module.** Env vars read inline per-module at import time:
  `db/bigquery.py:34` (`BIGQUERY_DATASET`), `:80` (`GOOGLE_CLOUD_PROJECT`),
  `src/gemini_client.py:10,23,24`, `src/http_client.py:12-14`, `src/scraper.py:38-41`,
  `src/api.py:23,25`, `src/notifier.py:11-23`.
- **Required-var fail-fast pattern**: `api_main.py:12-14` raises `RuntimeError` at startup if
  `ADMIN_API_KEY`/`USER_API_KEY` unset. This is the convention for must-have creds.
- **Secret Manager is NOT called from Python** anywhere; `google-cloud-secret-manager` is not a
  dependency. Secrets live in Secret Manager and are **injected as env vars at deploy time**;
  code only reads `os.environ`. (Old `bootstrap.py` SM-fetch pattern is NOT the puls-gpw way.)
- **BOM/CRLF caveat**: SM-injected values can carry a UTF-8 BOM + whitespace; `notifier`
  strips with `.strip().lstrip("﻿")` (`src/notifier.py:12-15`). OAuth token strings
  should get the same cleaning.
- **`load_dotenv()` first**: called as the first executable statement in every entry point
  (`main.py:5-7`, `post_main.py:8-10`, `api_main.py:1-3`) before any `db.*`/`src.*` import,
  because `db/bigquery.py` reads env at import time (`lessons.md`).
- **GCP client template** (`db/bigquery.py:69-93`): module-level singleton + double-checked
  `threading.Lock`, `google.auth.default()` + `with_quota_project` guard (via `hasattr`). Same
  shape in `src/gemini_client.py:12-27`, `src/http_client.py:17-30`. NOTE: the quota-project
  guard is ADC-specific — it does **not** apply to a tweepy OAuth-1.0a client (only if a new
  *Google* client is added). The singleton+lock shape, however, is the convention to mirror.
- **Sentry: ABSENT** (no `sentry-sdk`). The old project's Sentry alerting on partial publish
  has no equivalent here — alerting would route through the existing `send_alert` email path
  (`post_main.py:137-144`) instead.
- **Dependencies** (`pyproject.toml`): `tweepy` **ABSENT** (new dep). HTTP client is
  **`httpx>=0.27`** (no `requests`); Google: `google-cloud-bigquery`, `google-genai`;
  `python-dotenv`, `json5`. Dev: `pytest`, `respx` (httpx mocking), `pytest-playwright`,
  `pip-audit`.

### Area 4 — Runtime, scheduling, deploy, gate location

- **Three Cloud Run targets, one image** (`Dockerfile:13` default `CMD ["uv","run","python","main.py"]`,
  overridden per target at deploy):
  - `puls-gpw` — Job, scraper (`main.py`)
  - `puls-gpw-post` — Job, **post generation (`post_main.py`) — where publish belongs**
  - `puls-gpw-api` — Service, FastAPI (`api_main.py:19`, uvicorn :8080) — not in post path
- **Scheduling** (`context/foundation/infra.md:32-50`, lives in GCP, not in VCS): post job runs
  **3×/weekday Warsaw time** — `ranek 08:30` (`30 8 * * 1-5`), `poludnie 13:00`, `wieczor 17:30`.
  Window auto-detected in-code (`post_main.py:38-62`); outside windows the job **exits 0**
  (`post_main.py:78-81`) — an implicit guard before any publish path.
- ⚠️ **Deploy finding (verify in planning)**: the `Update Cloud Run Job (post)` step
  (`.github/workflows/deploy.yml:55-60`) passes **`--image` only** — no `--command`/`--args`
  and no `--set-env-vars`/`--set-secrets`. The post job's command + env/secrets are persisted
  on the job from its original `gcloud run jobs create` (`infra.md:22-23`), NOT in version
  control. The API service, by contrast, sets secrets/env inline every deploy
  (`deploy.yml:71-72`). Two implications:
  (a) confirm the deployed `puls-gpw-post` actually runs `post_main.py`, not the image default;
  (b) adding `X_AUTO_PUBLISH` + 4 `X_*` secrets needs a decision — one-time
  `gcloud run jobs update` (matches current model, invisible to VCS) **vs** add
  `--set-env-vars`/`--set-secrets` to the post step in `deploy.yml` (auditable, recommended;
  creds → Secret Manager, bound via `--set-secrets`, mirroring `gemini-api-key`).
- **Gate location** (both net-new): (1) **code gate** — read `X_AUTO_PUBLISH`
  (default OFF, e.g. `os.environ.get("X_AUTO_PUBLISH","0")`) and publish only inside the
  `result.approved` branch (`post_main.py:125-129`); default-OFF ⇒ job keeps emailing as today.
  (2) **infra gate** — pause the three `puls-gpw-post-*` Cloud Scheduler jobs. No existing
  kill-switch (`XPOST_DISABLED_WINDOWS` etc.) exists in this repo.

### Area 5 — Compliance & GPW tickers: live vs new work

**Enforced today in `src/post_supervisor.py`** (called only from `post_main.py:124`):

| Rule | Line |
|---|---|
| Exact tweet count (or min 3) | `:45-49` |
| Per-tweet ≤280 chars | `:51-53` |
| Each expected ticker present as `$TICKER` in body | `:55-58` |
| `#GPW` present in last tweet | `:60-62` |
| Disclaimer substring `rekomendacj` in last tweet | `:64-65` |
| No `...`/`…` truncation | `:67-69` |
| Investment-advice language block (12 PL regex) | `:8-23, 71-74` |

**NOT covered (new work if we want old-project compliance parity):**
- **≤1 cashtag cap** — not counted/capped anywhere (only generator-prompt soft instruction,
  `post_generator.py:135`; old `x_publisher.py:9`).
- **≤2 hashtag cap** — only `#GPW` *presence* checked; counting/capping is new (strategy doc
  `xpost-strategy.md:90`).
- **Known-GPW-ticker membership validation** — no validator, no helper. `data/company_list.json`
  (398 companies, bankier-style `.PL` tickers, NOT `$`-cashtags) and `data/ticker_display_names.json`
  (1 entry: `DRG`) are **inert/unused**. The old project's `utils/gpw_tickers.py` and
  `agents/xpost_compliance.py` **did not survive into `oldProjectData/`** (only `x_publisher.py`
  did) — so that logic must be rebuilt, not ported.

**X strategy rules** (`context/foundation/xpost-strategy.md`) a pre-publish gate should honor:
≤2 hashtags (`#GPW #ESPI`); thread 3–7 tweets (4 companies → 6); **no links in main post**
(links only in first reply); no engagement bait; always add own commentary; max 2 full
threads/day + 1 optional at 13:00. (Doc does **not** state a ≤1-cashtag rule — that's old-project lore.)

## Code References

- `post_main.py:125-129` — **publish hook point** (approved branch: save → email → return)
- `post_main.py:119-135` — supervisor retry loop (`_MAX_ATTEMPTS=3`); publish must sit inside `result.approved`
- `post_main.py:38-62`, `:78-81` — window detection + early exit guard
- `db/bigquery.py:60-67` — `_X_POSTS_SCHEMA` (incl. unused `tweet_ids`)
- `db/bigquery.py:505-570` — `save_x_post` (omits `tweet_ids`, returns `x_post_id`, non-atomic, stamps `posted_at` at insert)
- `db/bigquery.py:69-93` — GCP client singleton + lock + quota-project guard (convention)
- `src/post_supervisor.py:8-74` — live compliance rules
- `src/post_generator.py:150-152, 168, 213-220` — `GeneratedPost.tweets`, thread shape, window
- `src/notifier.py:12-15` — BOM/whitespace stripping for SM-injected secrets
- `api_main.py:12-14` — required-env fail-fast pattern
- `.github/workflows/deploy.yml:47-60` (jobs: image-only) vs `:71-72` (API: inline secrets/env)
- `context/foundation/infra.md:9-50` — Cloud Run jobs, secrets/env, scheduler crons, windows
- `context/foundation/xpost-strategy.md:79-104` — posting rules
- `data/company_list.json` — 398 GPW companies (unused), candidate ticker-universe source
- `oldProjectData/agents/x_publisher.py:57-170` — **reference only**: tweepy `Client` OAuth 1.0a, singleton, single/thread, compliance fail-fast, partial-publish

## Architecture Insights

- **Smallest-diff path**: the publish step is an insertion inside one already-existing branch,
  not a control-flow rewrite. `save_x_post` already returns the id needed to associate tweet IDs.
- **Storage flattens the thread** (`"\n\n".join`) — the publisher needs the live `post.tweets`
  list (still in scope at the hook), and posting a thread is reply-chaining tweet-by-tweet.
- **Two distinct "not published" states** to model: *disabled by flag* vs *publish failed* vs
  *partial publish*. `tweet_ids` NULL alone can't distinguish them — consider a status column
  or sentinel, but that pulls in x_posts schema-migration work (`ensure_schema_current` today
  only migrates announcements).
- **Convention to mirror, not the old bootstrap**: env-var-inline config, singleton+lock client,
  fail-fast on missing creds, alert-via-email (no Sentry), secrets-as-env-vars (no SM API calls).
- **Compliance belt-and-braces**: the supervisor already gates approval; a pre-publish compliance
  check (cashtag/hashtag/ticker caps) would be a second, publish-specific gate — decide whether
  to extend `post_supervisor` or add a dedicated pre-publish validator.

## Historical Context (from prior changes)

- `context/archive/2026-06-08-xpost-generation/change.md` — **PUL-16**: built generator +
  rule-based supervisor + email delivery + 3 schedulers. **Ends at email — no publishing.**
- `context/archive/2026-06-09-prompt-review/` — refined generator prompt / advice guards.
- `context/archive/2026-06-14-pul-29-bq-x-posts-table/{change.md,plan-brief.md}` — **PUL-29**:
  extracted dedicated `x_posts` table; **`tweet_ids` population explicitly "Out of scope (PUL-27)"**
  (`plan-brief.md:46`); table is "Foundation for PUL-27 (X auto-publish wiring)" (`change.md:20`).
  **This change is that PUL-27 wiring.**
- No `PUL-26` reference found anywhere in `context/**`.

## Related Research

- None prior under `context/changes/**/research.md`; this is the first research artifact for an
  X-publish change. Closest historical artifacts are the PUL-16 and PUL-29 archive folders above.

## Open Questions

1. **X API access tier / write limits (2026)** — free tier write caps (posts/month) vs the 3
   threads/weekday cadence (~6 tweets × 3 × 5 days). Needs confirmation against the actual
   developer account tier; affects whether auto-publish is even within quota. *(External; not a
   codebase question — resolve before enabling the flag.)*
2. **OAuth 1.0a (4 user keys, as in `x_credentials.json`) vs OAuth 2.0** — the prior art and
   your manual posting used 1.0a user-context; confirm that's the target for tweepy here.
3. **Secret name** — old `bootstrap.py` used `x-api-credentials`; you mentioned `x-credentials`.
   For puls-gpw the model is 4 separate Secret-Manager secrets → 4 env vars (not one JSON blob).
   Decide secret naming (e.g. `x-api-key`, `x-api-secret`, `x-access-token`, `x-access-secret`).
4. **Publish-status modeling** — reuse `tweet_ids` (NULL = not published) or add an explicit
   status column? The latter needs x_posts migration support in `ensure_schema_current()`.
5. **Pre-publish compliance parity** — do we add ≤1 cashtag / ≤2 hashtag / known-ticker checks
   now (rebuild the old `xpost_compliance` logic), or rely on the existing supervisor and defer?
6. **Deploy wiring for the flag + creds** — `deploy.yml` post step (auditable) vs one-time
   `gcloud run jobs update` (matches current persisted-config model). Also confirm `puls-gpw-post`
   actually runs `post_main.py` (deploy.yml sets no `--command` for it).
7. **Thread vs single & partial-publish handling** — posting is reply-chained; define behavior
   when tweet N of a thread fails (the old project returned partial IDs + alerted).
