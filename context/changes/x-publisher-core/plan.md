# X Publisher Core — Implementation Plan

## Overview

Add the missing **publish-to-X step** to the puls-gpw post pipeline. Generated, supervisor-approved
X threads are currently only emailed to the owner; this change publishes them to X (Twitter) via a new
`tweepy`/OAuth-1.0a client, gated by an `X_AUTO_PUBLISH` flag (default OFF), wired into the existing
`post_main.py` approved branch. It writes the returned tweet IDs and a publish status back into the
`x_posts` table, guards against double-posting and against publishing empty/substance-less threads,
and reports the outcome in the existing post email.

## Current State Analysis

(Grounded in `context/changes/x-publisher-core/research.md`.)

- **No publisher exists.** No `tweepy`, no OAuth, no `X_AUTO_PUBLISH`, no Secret-Manager API calls in
  Python, no Sentry. The only output channel is email.
- **Hook point** is `post_main.py:125-129` — the `if result.approved:` branch:
  `save_x_post(...)` → `send_post_email(...)` → `return`. The per-tweet list `post.tweets` is in scope
  here (storage flattens it to one `"\n\n"`-joined string).
- **Thread shape** (`src/post_generator.py:150-152, 213-214`): `GeneratedPost.tweets: list[str]` =
  `hook` + one body tweet per company + `closing`; `expected_tweets = n_companies + 2`. Window ∈
  `ranek|poludnie|wieczor`.
- **x_posts schema** (`db/bigquery.py:60-67`): `x_post_id`, `window`, `post_text`, `tweet_ids`,
  `posted_at`, `supervisor_attempts`. `tweet_ids` exists (provisioned by PUL-29) but is **never
  written** and is **omitted from the `save_x_post` INSERT** (`db/bigquery.py:524-529`).
  `save_x_post` already **returns** `x_post_id` (`db/bigquery.py:570`); `post_main.py:126` discards it.
  `ensure_schema_current()` migrates the **announcements** `_SCHEMA` only, not x_posts.
- **`window` is a BQ reserved keyword** — any hand-written SQL touching x_posts must backtick
  `` `window` `` and be round-tripped on real BQ (`context/foundation/lessons.md`, PUL-29 bug).
- **Config convention**: per-module inline `os.environ.get` (no central config); secrets injected as
  env vars at deploy (no SM API in code); `load_dotenv()` first in entry points; singleton + double-
  checked `threading.Lock` client pattern (`db/bigquery.py:69-93`); required-creds fail-fast
  (`api_main.py:12-14`); SM-injected values may carry a UTF-8 BOM → strip (`src/notifier.py:12-15`).
- **Supervisor** (`src/post_supervisor.py`) already gates approval (≤280, ticker presence, `#GPW`,
  disclaimer, advice language) but the user reports it occasionally approves an empty/substance-less
  thread — hence the independent non-empty guard below.
- **Deploy** (`.github/workflows/deploy.yml:47-60`): the `puls-gpw-post` job step updates `--image`
  only — **no `--command`, no `--set-env-vars`, no `--set-secrets`**; its command + env/secrets are
  persisted on the job from the original `gcloud run jobs create` (`infra.md:22-23`). Schedulers fire
  the post job 3×/weekday (08:30, 13:00, 17:30 Warsaw).

## Desired End State

When `X_AUTO_PUBLISH=true`, a successful post-pipeline run that produces a substance-bearing,
supervisor-approved thread **publishes that thread to X** (as a reply-chain), records the returned
tweet IDs and `x_publish_status='published'` in `x_posts`, and the owner email shows the published
tweet link(s). With the flag OFF (default), behavior is unchanged from today (email only,
`x_publish_status='skipped'`). Empty/substance-less threads and already-published windows are never
published. Mid-thread failures leave a `partial` record and send an alert email.

**Verification**: with flag OFF the pipeline behaves exactly as today; with flag ON in a controlled
test, a thread appears on the X account, `x_posts` shows the IDs + `published` status, and re-running
the same window does not double-post.

### Key Discoveries:

- Publish hook: `post_main.py:125-129` (approved branch).
- `tweet_ids` column already provisioned but unwritten: `db/bigquery.py:60-67, 524-529`.
- `save_x_post` already returns `x_post_id`: `db/bigquery.py:570`.
- `ensure_schema_current` is announcements-only — x_posts migration is new: `db/bigquery.py:135+`.
- Reserved-keyword `window` must be backticked + real-BQ round-trip: `context/foundation/lessons.md`.
- BOM-strip pattern for injected secrets: `src/notifier.py:12-15`.
- Singleton+lock client template: `db/bigquery.py:69-93`.
- Prior art (reference only, do NOT copy): `oldProjectData/agents/x_publisher.py`.

## What We're NOT Doing

- **No pre-publish compliance caps** (≤1 cashtag, ≤2 hashtags, known-GPW-ticker membership). The
  supervisor remains the quality gate; only a non-empty/substance guard is added. (Deferred — possible
  follow-up.)
- **No OAuth 2.0 / token-refresh flow** — OAuth 1.0a user-context with the four existing keys.
- **No rollback/deletion of already-posted tweets** on partial failure — record + alert only.
- **No approval/draft UI** — auto-publish gated by the flag, per decided scope.
- **No Sentry** — alerting routes through the existing email path.
- **No `published_at` timestamp column** — status column only; `posted_at` semantics unchanged.
- **No change to scheduler cadence** or window logic.
- **No automated creation of Secret Manager secret values** — human-only (CLAUDE.md).

## Implementation Approach

Four reversible phases: (1) a self-contained publisher module + dependency, unit-tested in isolation
with a mocked tweepy client; (2) the x_posts schema/status/idempotency persistence layer, round-tripped
on real BQ; (3) the `post_main.py` wiring that composes flag + guards + publish + persistence + email;
(4) the deploy/secret wiring. Phases 1 and 2 are independent; 3 depends on both; 4 is operational.

## Critical Implementation Details

- **Reserved keyword**: every new/changed x_posts SQL string must backtick `` `window` ``; add a cheap
  regression assert on the query string AND a real-BQ round-trip via `scripts/test_bq.py` calling
  `ensure_schema_current()` (mocked unit tests do NOT catch BQ syntax errors — PUL-29 lesson).
- **Ordering**: `save_x_post` must run (and return `x_post_id`) **before** publishing, so the
  idempotency guard has a row to check and the publish result has a row to update. Publish sits inside
  the `result.approved` branch so unapproved threads can never publish.
- **Thread = reply-chain**: tweet 1 is a root `create_tweet`; each subsequent tweet replies to the
  previous id. A failure on tweet N leaves tweets 1..N-1 live — capture those partial ids.
- **BOM/whitespace**: strip injected OAuth secret env vars with `.strip().lstrip("﻿")` exactly as
  `src/notifier.py:12-15` does.

---

## Phase 1: X Publisher module

### Overview

A self-contained, unit-tested publisher: instantiate a tweepy OAuth-1.0a client from env vars and
publish a single tweet or a reply-chained thread, returning published tweet ids (including partial ids
on mid-thread failure). No pipeline wiring yet.

### Changes Required:

#### 1. Add tweepy dependency

**File**: `pyproject.toml`

**Intent**: Add `tweepy` as a runtime dependency (the only new dep; `httpx` already present). Refresh
`uv.lock`.

**Contract**: new entry in `[project].dependencies`, e.g. `tweepy>=4.14`. `uv lock` updates `uv.lock`.

#### 2. Publisher module

**File**: `src/x_publisher.py` (new)

**Intent**: Encapsulate X publishing. A module-level singleton (double-checked `threading.Lock`,
mirroring `db/bigquery.py:69-93`) builds a `tweepy.Client` from the four `X_*` env vars (BOM/whitespace
stripped); fail-fast with a clear error if any is missing (mirroring `api_main.py:12-14`). Public API
publishes a thread as a reply-chain and returns the list of published tweet ids; on a mid-thread
exception it records the ids published so far and re-raises a typed error carrying those partial ids.

**Contract**:
- `get_x_publisher() -> XPublisher` — singleton accessor; raises `XPublisherError` (new, in
  `src/exceptions.py`) if creds are absent.
- `XPublisher.publish_thread(tweets: list[str]) -> list[str]` — posts `tweets` in order, each replying
  to the prior; returns all published ids. A single-element list is a single tweet.
- On failure with **≥1 tweet already posted**: raise `XPublishPartialError(published_ids: list[str],
  cause: Exception)` (new) — `published_ids` is non-empty by construction. On failure with **0 tweets
  posted** (e.g. the first `create_tweet` fails): raise a plain `XPublisherError` — there is nothing
  partial on X, so this is a full failure, not a partial. This keeps the status taxonomy honest:
  `partial` ⇒ a half-thread is live; `failed` ⇒ nothing was posted. The caller decides
  persistence/alerting — the module never writes BQ or sends email.
- Env vars read: `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET`.
- This module performs **no** compliance/non-empty checks and **no** flag reading — those live in the
  caller (Phase 3), keeping the publisher a thin transport.

#### 3. Unit tests

**File**: `tests/test_x_publisher.py` (new)

**Intent**: Cover creds-missing fail-fast, single-tweet publish, multi-tweet reply-chain ordering
(assert each call after the first passes `in_reply_to_tweet_id` of the previous id), and partial-failure
(tweet 2 of 3 raises → `XPublishPartialError` with the one published id). Mock `tweepy.Client`
(monkeypatch the class / inject a fake) — do NOT hit the network. BOM-stripping of creds verified.

**Contract**: pytest module; fake tweepy client returning incrementing ids; no real `tweepy.Client`
instantiation.

### Success Criteria:

#### Automated Verification:

- Dependency resolves: `uv lock` clean and `uv sync` succeeds
- Unit tests pass: `uv run pytest tests/test_x_publisher.py`
- Linting passes: project lint command
- Import does not require creds at import time (only at `get_x_publisher()` call)

#### Manual Verification:

- (Deferred to Phase 3 / live smoke — no standalone manual step here)

**Implementation Note**: After automated verification passes, pause for human confirmation before
Phase 2.

---

## Phase 2: x_posts status + tweet_ids persistence & idempotency

### Overview

Extend the x_posts data layer to record publish outcome and support a double-post guard: add an
`x_publish_status` column, teach `ensure_schema_current()` to migrate x_posts, add an update function
for the publish result, and add an idempotency query. Round-trip on real BQ.

### Changes Required:

#### 1. Schema: add status column

**File**: `db/bigquery.py`

**Intent**: Add `x_publish_status` (STRING, NULLABLE) to `_X_POSTS_SCHEMA`. Values used by Phase 3:
`published`, `skipped`, `failed`, `partial` (NULL for legacy rows).

**Contract**: new `SchemaField("x_publish_status", "STRING", mode="NULLABLE")` in `_X_POSTS_SCHEMA`
(`db/bigquery.py:60-67`).

#### 2. x_posts migration support

**File**: `db/bigquery.py`

**Intent**: `ensure_schema_current()` today migrates only the announcements `_SCHEMA` against the
announcements `_table_ref(client)` (`db/bigquery.py:142,148-149`). **Parameterize** the migration over
`(table_ref, schema)` so the same additive-column mechanism serves both tables, and call it for x_posts
too. The x_posts migration MUST be invoked at post-job startup: add the x_posts call to the existing
setup block `post_main.py:90-92` (which already calls `ensure_schema_current()` for announcements) — a
new column never lands in prod unless that startup path runs it.

**Contract**: an idempotent schema-diff (add missing `_X_POSTS_SCHEMA` fields to the existing x_posts
table; no-op when current), reusing the announcements migration code path via parameterization rather
than a duplicated function. Wire the x_posts invocation into `post_main.py:90-92`.

#### 3. Persist publish result

**File**: `db/bigquery.py`

**Intent**: Add a function to write the publish outcome onto an existing x_posts row by `x_post_id`:
the published tweet ids (joined to the STRING `tweet_ids` column) and `x_publish_status`.

**Contract**: `update_x_post_publish_result(x_post_id: str, tweet_ids: list[str] | None, status: str)
-> None`. UPDATE on x_posts keyed by `x_post_id`; backtick any reserved-keyword columns touched. (Note:
`save_x_post` keeps its current signature; the new fn handles the publish write so the existing INSERT
path is untouched.)

#### 4. Idempotency query

**File**: `db/bigquery.py`

**Intent**: Provide a check the caller runs before publishing: has a thread for this window already been
published today? Used to prevent double-posting on job re-run/retry.

**Contract**: `x_post_already_published(window: str, day: date | None = None) -> bool` — SELECT over
x_posts for a row matching `` `window` `` AND `x_publish_status='published'` where the **dedup key is
`DATE(posted_at)` in the Warsaw timezone** (default `day` = today Warsaw). Key off `posted_at`'s Warsaw
calendar day — **not** `_window_bounds` (those cross midnight for `ranek` and bound *announcement fetch*,
not publish time; all three windows publish on their run day). Backtick `` `window` ``.

Note (accepted risk): this is a check-then-act guard, not a lock — two concurrent invocations for the
same window could both pass before either writes. Acceptable given one Cloud Scheduler trigger per
window; not hardened against a double-fire race.

#### 5. Tests

**Files**: `tests/test_bigquery.py` (extend); `scripts/test_bq.py` (extend round-trip)

**Intent**: Unit-assert the SQL strings (status column present in schema; `` `window` `` backticked in
the new UPDATE/SELECT; status value written). Extend the real-BQ round-trip script to insert → migrate
(`ensure_schema_current`) → update publish result → read back, proving no BQ syntax error.

**Contract**: query-string asserts + a round-trip path exercising the new column and functions.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_bigquery.py`
- Query-string regression asserts pass (status column + backticked `window`)
- Linting passes

#### Manual Verification:

- Real-BQ round-trip succeeds: `uv run python scripts/test_bq.py` (insert → `ensure_schema_current` adds
  `x_publish_status` to the existing table → update result → read back) with no `400 Syntax error`
- New column visible on the deployed/test `x_posts` table after migration

**Implementation Note**: After automated verification passes, run the real-BQ round-trip and confirm
manually before Phase 3.

---

## Phase 3: Wire publish into post_main.py (flag + guards + email)

### Overview

Compose the pieces in the approved branch: read the flag, run the idempotency and non-empty/substance
guards, publish when allowed, persist the result + status, and report the outcome in the email — all
without changing behavior when the flag is OFF.

### Changes Required:

#### 1. Flag + non-empty/substance guard helpers

**File**: `post_main.py` (and/or a small helper in `src/`)

**Intent**: Read `X_AUTO_PUBLISH` (default OFF) via `os.environ.get`. Add a guard that decides whether a
thread is substantive enough to publish: it must have non-empty tweets, joined stripped text non-blank,
**at least 3 tweets** (hook + ≥1 company body + closing), **at least one body tweet (`tweets[1:-1]`)
referencing a ticker** (a `$TICKER` cashtag — i.e. a real company-analysis tweet), and contain no
placeholder marker. This guard is independent of `post_supervisor` (which sometimes approves empty).

**Contract**: `X_AUTO_PUBLISH = os.environ.get("X_AUTO_PUBLISH", "").lower() == "true"`;
`is_publishable(tweets: list[str]) -> bool` implementing the substance rule above. Cashtag detection via
a `$[A-Z0-9]{1,10}` regex over body tweets.

#### 2. Publish orchestration in the approved branch

**File**: `post_main.py` (the `if result.approved:` branch, currently lines 125-129)

**Intent**: Capture the `x_post_id` returned by `save_x_post` (stop discarding it). Then, still inside
the approved branch: if `X_AUTO_PUBLISH` is OFF → `update_x_post_publish_result(id, None, 'skipped')`.
If ON: run `is_publishable` (fail → `skipped`) and `x_post_already_published(window)` (already published
→ `skipped`, no re-post); if both pass, call `publish_thread(post.tweets)`, then
`update_x_post_publish_result(id, ids, 'published')`. On `XPublishPartialError` →
`update_x_post_publish_result(id, partial_ids, 'partial')` + send alert. On other publish errors →
`update_x_post_publish_result(id, None, 'failed')` + send alert. Publishing never raises out of the
branch (the email + job completion must still happen).

**Contract**: ordering `save_x_post` → guards → publish → persist result → email. Status values:
`published | skipped | failed | partial`. Reuses `send_alert` (`post_main.py:137-144`) for failure
alerts (no Sentry).

#### 3. Email reports publish outcome

**File**: `src/notifier.py` (`send_post_email`) + call site in `post_main.py`

**Intent**: Surface the publish result in the existing owner email: a line such as “Published to X:
<tweet url/ids>”, “Auto-publish OFF (draft)”, “Publish FAILED: <reason>”, or “Publish PARTIAL:
<n>/<m>”. Tweet URL can be derived from the first id.

**Contract**: extend `send_post_email(...)` with an optional publish-result argument (status + ids);
backward-compatible default keeps today’s body when not provided. The non-approved / all-fail path
(`send_no_post_email`) is unchanged and never publishes.

#### 4. Tests

**File**: `tests/test_post_main.py` (new) or extend existing post tests

**Intent**: Cover, with `x_publisher` and `db.bigquery` mocked: flag OFF → no publish, status
`skipped`, email unchanged-ish; flag ON + publishable + not-yet-published → publish called, ids
persisted, status `published`, email shows link; flag ON + empty/substance-less thread → no publish,
status `skipped` (the empty-post hard constraint); flag ON + already published → no publish; partial
error → status `partial` + alert; failure → status `failed` + alert.

**Contract**: pytest with monkeypatched `get_x_publisher`, `save_x_post`,
`update_x_post_publish_result`, `x_post_already_published`, `send_post_email`, `send_alert`.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_post_main.py tests/test_post_supervisor.py`
- Full suite passes: `uv run pytest`
- Linting passes
- Flag-OFF path asserts no call to `publish_thread` and status `skipped`
- Empty/substance-less thread asserts no publish (hard constraint regression test)

#### Manual Verification:

- Local dry-run with `X_AUTO_PUBLISH` unset behaves exactly as today (email only)
- Controlled live test (flag ON, test creds): a thread appears on the X account; `x_posts` row shows
  ids + `published`; the email shows the tweet link
- Re-running the same window does NOT post a second thread (idempotency)
- A deliberately empty/hook-only thread is NOT published (status `skipped`)

**Implementation Note**: The live test posts to a real account — do it deliberately and only after the
human confirms creds/account. Pause for confirmation before Phase 4.

---

## Phase 4: Deploy & secret wiring

### Overview

Make the flag and credentials reproducible and the post job correct in CI: bind the four X secrets and
the flag to the `puls-gpw-post` deploy step, fix its missing `--command`, and document it. Secret values
are created/set by a human.

### Changes Required:

#### 1. deploy.yml — post job step

**File**: `.github/workflows/deploy.yml` (the `Update Cloud Run Job (post)` step, ~lines 55-60)

**Intent**: Add `--command`/`--args` so the post job explicitly runs `post_main.py` (today it relies on
persisted config / image default `main.py` — latent bug). Add `--set-env-vars` including
`X_AUTO_PUBLISH` (default `false`) alongside the existing `GOOGLE_CLOUD_PROJECT`/`BIGQUERY_DATASET`, and
`--set-secrets` binding the four X creds to Secret Manager secrets (mirroring how the API service binds
`admin-api-key`/`user-api-key`, `deploy.yml:71-72`). Binding references SM by name; it does not expose
secret values.

**Contract**: `--command=uv --args=run,python,post_main.py` (match the scraper step form,
`deploy.yml:47-53`); `--set-secrets=X_API_KEY=x-api-key:latest,X_API_SECRET=x-api-secret:latest,
X_ACCESS_TOKEN=x-access-token:latest,X_ACCESS_SECRET=x-access-secret:latest`;
`--set-env-vars=...,X_AUTO_PUBLISH=false`.

#### 2. Infra docs

**File**: `context/foundation/infra.md`

**Intent**: Document the four new SM secrets, the `X_AUTO_PUBLISH` env var on `puls-gpw-post`, and the
two-layer safety (flag default OFF + pausable schedulers). Record that the post job command is now set
in deploy.yml.

**Contract**: prose + the secrets/env table update under the `puls-gpw-post` section.

### Success Criteria:

#### Automated Verification:

- `deploy.yml` is valid YAML and the post step references all four secrets + the flag + the command
- CI deploy workflow runs green on the branch/PR (build + tests)

#### Manual Verification:

- **Human-only**: create the four Secret Manager secrets (`x-api-key`, `x-api-secret`,
  `x-access-token`, `x-access-secret`) and set their values; grant the runner SA
  `secretmanager.secretAccessor` if not already
- After deploy, confirm `puls-gpw-post` runs `post_main.py` (not `main.py`) and has the four secrets +
  `X_AUTO_PUBLISH=false` bound
- Enabling `X_AUTO_PUBLISH=true` (when ready) publishes on the next window; flipping back to `false`
  stops it; pausing the schedulers stops the job firing

**Implementation Note**: Setting secret values and flipping `X_AUTO_PUBLISH=true` in production are
human decisions — the plan stops at wiring + `false` default.

---

## Testing Strategy

### Unit Tests:

- Publisher: creds fail-fast, single vs thread, reply-chain ordering, partial-failure error.
- BQ: status column in schema, backticked `` `window` `` in new UPDATE/SELECT, idempotency query shape.
- post_main: flag OFF/ON matrix, empty-thread skip (hard constraint), idempotency skip, partial/failed
  status + alert, email shows result.

### Integration Tests:

- Real-BQ round-trip (`scripts/test_bq.py`): insert → migrate → update publish result → read back.

### Manual Testing Steps:

1. Flag unset locally → run post pipeline → email only, no publish, status `skipped`.
2. Flag ON with test creds → controlled run → thread on X, ids + `published` in x_posts, email link.
3. Re-run same window → no second thread (idempotency).
4. Force a hook-only/empty thread → not published, status `skipped`.
5. Simulate mid-thread failure (test creds / forced error) → `partial` + alert email.

## Performance Considerations

Negligible. One extra SELECT (idempotency) + one UPDATE per approved run, and up to ~6 sequential
`create_tweet` calls per thread (3×/weekday). Mind X API free-tier write limits when enabling the flag
(external; see research Open Questions) — handle 429/403 as a publish `failed`/`partial` + alert, no
retry storm.

## Migration Notes

- `x_publish_status` is additive/NULLABLE — legacy x_posts rows read as NULL. Migration is applied by
  the extended `ensure_schema_current()` on the existing table (no rebuild).
- Backfill is unnecessary (historical rows were never published to X).

## References

- Research: `context/changes/x-publisher-core/research.md`
- Hard constraint (never auto-publish empty): `change.md` + memory `feedback-no-empty-xpost-autopublish`
- Hook point: `post_main.py:125-129`
- x_posts schema: `db/bigquery.py:60-67`, `save_x_post` `:505-570`
- GCP client/singleton template: `db/bigquery.py:69-93`
- BOM-strip pattern: `src/notifier.py:12-15`
- Reserved-keyword lesson: `context/foundation/lessons.md`
- Prior art (reference only): `oldProjectData/agents/x_publisher.py`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: X Publisher module

#### Automated

- [ ] 1.1 Dependency resolves: `uv lock` / `uv sync` succeed
- [ ] 1.2 Unit tests pass: `uv run pytest tests/test_x_publisher.py`
- [ ] 1.3 Linting passes
- [ ] 1.4 Import does not require creds at import time (only at `get_x_publisher()`)

#### Manual

- [ ] 1.5 (Deferred to Phase 3 live smoke — no standalone manual step)

### Phase 2: x_posts status + tweet_ids persistence & idempotency

#### Automated

- [ ] 2.1 Unit tests pass: `uv run pytest tests/test_bigquery.py`
- [ ] 2.2 Query-string regression asserts pass (status column + backticked `window`)
- [ ] 2.3 Linting passes

#### Manual

- [ ] 2.4 Real-BQ round-trip succeeds: `uv run python scripts/test_bq.py` (insert → migrate → update → read back), no syntax error
- [ ] 2.5 `x_publish_status` visible on the x_posts table after migration

### Phase 3: Wire publish into post_main.py

#### Automated

- [ ] 3.1 Unit tests pass: `uv run pytest tests/test_post_main.py tests/test_post_supervisor.py`
- [ ] 3.2 Full suite passes: `uv run pytest`
- [ ] 3.3 Linting passes
- [ ] 3.4 Flag-OFF path asserts no `publish_thread` call and status `skipped`
- [ ] 3.5 Empty/substance-less thread asserts no publish (hard-constraint regression)

#### Manual

- [ ] 3.6 Flag-unset local dry-run behaves as today (email only)
- [ ] 3.7 Controlled live test: thread on X, ids + `published` in x_posts, email link
- [ ] 3.8 Re-run same window does NOT double-post (idempotency)
- [ ] 3.9 Hook-only/empty thread is NOT published (status `skipped`)

### Phase 4: Deploy & secret wiring

#### Automated

- [ ] 4.1 `deploy.yml` valid; post step references 4 secrets + flag + `--command`
- [ ] 4.2 CI deploy workflow runs green (build + tests)

#### Manual

- [ ] 4.3 Human creates 4 SM secrets + sets values; runner SA has `secretmanager.secretAccessor`
- [ ] 4.4 Deployed `puls-gpw-post` runs `post_main.py` with 4 secrets + `X_AUTO_PUBLISH=false` bound
- [ ] 4.5 Toggling `X_AUTO_PUBLISH` true/false (and pausing schedulers) behaves as expected
