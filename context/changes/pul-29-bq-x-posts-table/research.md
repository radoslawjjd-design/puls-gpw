---
date: 2026-06-14T09:12:44+0200
researcher: Radek
git_commit: f005ab25cb8739519a8909d204b6940dd1edfd94
branch: master
repository: puls-gpw
topic: "PUL-29 — Extract x_posts table, remove post_text duplication from announcements"
tags: [research, codebase, bigquery, schema, x-posts, post_text]
status: complete
last_updated: 2026-06-14
last_updated_by: Radek
---

# Research: PUL-29 — Extract `x_posts` table, remove `post_text` duplication

**Date**: 2026-06-14T09:12:44+0200
**Researcher**: Radek
**Git Commit**: f005ab25cb8739519a8909d204b6940dd1edfd94
**Branch**: master
**Repository**: puls-gpw

## Research Question

PUL-29 wants to stop `save_post_text()` writing the full X-thread text to every
contributing announcement row (N companies = N identical copies of `post_text` /
`posted_at` / `supervisor_attempts`). Solution: a dedicated `x_posts` table + an FK
column `announcements.x_post_id`. Before planning, map **every** writer and reader of
`post_text` / `posted_at` / `supervisor_attempts`, the table-management machinery, the
test surface, and the design points the ticket leaves open.

## Summary

The blast radius is **small and well-contained** — the ticket's prescription is
accurate. There is exactly **one writer** (`save_post_text`, called from two sites in
`post_main.py`) and the read path is limited to the **admin** list endpoint (the user
view never selected `post_text`). The real work is not the wiring but four design
decisions the ticket under-specifies:

1. **The BQ module is single-table by construction.** `_TABLE_NAME`, `_SCHEMA`,
   `_table_ref()`, `create_table_if_not_exists()`, `ensure_schema_current()` all assume
   one table (`announcements`). Adding `x_posts` means generalizing these or adding a
   parallel set.
2. **`save_x_post` is two operations** (INSERT `x_posts` + UPDATE `announcements`) and
   BQ standard queries are not transactional unless wrapped in a multi-statement
   `BEGIN TRANSACTION … COMMIT` script. Partial-failure semantics need a decision.
3. **Table-creation path:** the `x_posts` table is *written* by `post_main.py`, but
   `post_main.py` never calls `create_table_if_not_exists()` — only the scraper
   (`main.py:39-40`) and the API startup do. Something must create `x_posts` before the
   first post run.
4. **Admin read path gap:** after the change, new posts write `post_text` to `x_posts`,
   not `announcements`. `list_announcements_admin` reads `announcements.post_text`, which
   will be **NULL for all new posts** unless it JOINs `x_posts`. The ticket says the old
   columns are "kept for backwards-compat but no longer written" and doesn't address the
   admin read — this is the most important open question.

No data backfill is in scope (ticket: deprecated columns get "separate cleanup later").

## Detailed Findings

### Writer — the single source

- `db/bigquery.py:465-500` — `save_post_text(announcement_ids, post_text, supervisor_attempts)`.
  One `UPDATE … SET post_text=@post_text, supervisor_attempts=@supervisor_attempts,
  posted_at=CURRENT_TIMESTAMP() WHERE announcement_id IN UNNEST(@ids)`. This is the only
  place these three columns are written. Raises `BigQueryError` on job error or 0 rows.
- `post_main.py:113` — success path: `save_post_text(ann_ids, "\n\n".join(post.tweets), attempt)`.
- `post_main.py:120` — failure path: `save_post_text(ann_ids, None, _MAX_ATTEMPTS)` (records a
  failed generation as `post_text=NULL`).
- Note: `post_main.py` already has `window` in scope (`post_main.py:72`) but does **not**
  pass it today. The ticket's `save_x_post(announcement_ids, post_text, window,
  supervisor_attempts)` adds it — a trivial wiring change at both call sites.

### Readers — admin only

- `db/bigquery.py:344-408` — `list_announcements_admin()` SELECTs `post_text, posted_at,
  supervisor_attempts` (`:368`) and maps them into the result dict (`:395-398`).
- `src/api.py:45-59` — `AnnouncementAdmin` Pydantic model declares `post_text`,
  `posted_at`, `supervisor_attempts` (`:53-56`); returned by `GET /announcements` for the
  admin role (`src/api.py:113-118`).
- `db/bigquery.py:411-462` — `list_announcements_user()` selects **only** `company,
  ticker, event_type, structured_analysis, analysis_score, published_at`. **The user
  view never touched `post_text`** → user-facing API is unaffected by this change.

### Schema & table-management machinery (single-table today)

- `db/bigquery.py:29-30` — `_DATASET`, `_TABLE_NAME = "announcements"` (module-level).
- `db/bigquery.py:32-50` — `_SCHEMA` (17 fields). `post_text`, `posted_at`,
  `supervisor_attempts` at `:39-42`. PUL-29 adds `x_post_id STRING NULLABLE` here.
- `db/bigquery.py:79-80` — `_table_ref(client)` hardcodes `_TABLE_NAME`.
- `db/bigquery.py:92-102` — `create_table_if_not_exists()` creates the one table from `_SCHEMA`.
- `db/bigquery.py:105-128` — `ensure_schema_current()` adds missing columns to the existing
  table. **Adding `x_post_id` to `_SCHEMA` means it is auto-added on next startup** that
  runs this (`main.py`/API) — no manual ALTER needed.
- The module-level header docstring (`db/bigquery.py:1-16`) documents nullable semantics
  per column and must be updated (`post_text`/`posted_at`/`supervisor_attempts` now
  "deprecated — moved to x_posts"; add `x_post_id`).

### Where table creation is wired (the gap for x_posts)

- `main.py:38-40` — scraper entrypoint calls `create_table_if_not_exists()` +
  `ensure_schema_current()` at startup.
- `src/api.py` startup — also ensures schema (grep: `create_table_if_not_exists` /
  `ensure_schema_current` imported in `main.py:16-17`; API wires the same — confirm exact
  call site during planning).
- `post_main.py` — **does not** create or ensure any table; it assumes the schema exists.
  → The plan must decide where `create_x_posts_table_if_not_exists()` runs so `x_posts`
  exists before `post_main` first writes (candidates: add to `main.py` + API startup; or
  call defensively at the top of `post_main.main()`).

### Test surface

- `tests/test_bigquery.py:112-138` — `test_save_post_text_calls_query_with_unnest`,
  `test_save_post_text_none_records_failure`; `:216-228`
  `test_save_post_text_stamps_posted_at` (asserts `posted_at = CURRENT_TIMESTAMP()` and no
  `processed_at`). These rewrite to target `save_x_post`.
- `tests/test_bigquery.py:14` — imports `save_post_text` (update import).
- `tests/test_api.py:43-44` — mock admin row dict includes `post_text/posted_at/
  supervisor_attempts`; add `x_post_id` (and keep old keys while columns survive).
- `tests/e2e/conftest.py:22-23` — same mock dict shape; same update.
- `scripts/test_bq.py` — manual real-BQ round-trip; does not touch `post_text`. A good
  place to add an `x_posts` round-trip for the non-unit-testable DDL/migration check.
- Mock helpers `_mock_bq_client(affected_rows=…)` exist in `tests/test_bigquery.py` and
  support asserting query text + bound params + `num_dml_affected_rows`.

## Code References

- `db/bigquery.py:465-500` — `save_post_text` (the only writer; becomes/spawns `save_x_post`)
- `db/bigquery.py:344-408` — `list_announcements_admin` (admin read path)
- `db/bigquery.py:411-462` — `list_announcements_user` (unaffected — no post_text)
- `db/bigquery.py:29-50` — `_TABLE_NAME` + `_SCHEMA` (add `x_post_id`; define `x_posts` schema)
- `db/bigquery.py:79-128` — `_table_ref` / `create_table_if_not_exists` / `ensure_schema_current`
- `db/bigquery.py:1-16` — column-semantics docstring (must update)
- `post_main.py:113,120` — two `save_post_text` call sites (rewire to `save_x_post`, pass `window`)
- `post_main.py:72` — `window` already resolved here
- `src/api.py:45-59,113-118` — `AnnouncementAdmin` model + admin response
- `main.py:38-40` — startup table creation/schema ensure
- `tests/test_bigquery.py:112-138,216-228` — save_post_text tests
- `tests/test_api.py:43-44`, `tests/e2e/conftest.py:22-23` — mock row dicts

## Architecture Insights

- **Single-table module by design.** Every table-aware helper is keyed to one module-level
  `_TABLE_NAME`. The cleanest extension is to parameterize `_table_ref(client, table)` and
  add `_X_POSTS_TABLE_NAME` / `_X_POSTS_SCHEMA` / `create_x_posts_table_if_not_exists()`,
  rather than a second hardcoded clone. Keep `ensure_schema_current()` working for both.
- **`x_post_id` is additive & auto-migrating.** Because it's just a new NULLABLE column on
  `announcements`, `ensure_schema_current()` adds it with zero manual DDL — consistent with
  how every prior column was rolled out (see archive `2026-06-11-bq-fields-audit`).
- **No transactions in BQ standard queries.** `save_x_post` (INSERT then UPDATE) is not
  atomic unless wrapped in a single multi-statement `BEGIN TRANSACTION … COMMIT` job, or
  ordered defensively (INSERT x_posts first, return id, then UPDATE; on UPDATE failure the
  x_posts row is an orphan but harmless — `posted_at` still records the post happened).
  Decide and document. (lessons.md: DML INSERT not streaming, so subsequent UPDATE in the
  same session isn't blocked — see `insert_announcement` docstring `db/bigquery.py:149-156`.)
- **UUID for `x_post_id`** — generate client-side with `uuid.uuid4().hex` / `str(uuid4())`
  (ticket: REQUIRED STRING UUID); keeps the INSERT and the UPDATE referring to the same id.
- **GCP client rules apply** (`.claude/rules/db-bigquery.md`, lessons.md): no new client is
  introduced (reuses `_get_client()`), so the `with_quota_project` guard is already
  satisfied; `load_dotenv()` ordering unchanged. **DROP/cleanup of the old columns is
  human-only and explicitly out of scope.**

## Historical Context (from prior changes)

- `context/archive/2026-06-08-xpost-generation/plan.md` — introduced `save_post_text` and
  the supervisor-retry/`posted_at` semantics this change refactors.
- `context/archive/2026-06-11-bq-fields-audit/plan.md` + `plan-brief.md` — most recent BQ
  schema migration; precedent for additive column rollout via `ensure_schema_current()` and
  for updating the column-semantics docstring + mock row dicts in tests (same files touched).
- `context/archive/2026-06-02-bigquery-schema/plan.md` — original schema/table-creation
  design (`create_table_if_not_exists`, `_SCHEMA` shape).
- `context/archive/2026-06-11-auth-public-url/research.md` — admin vs user list split;
  confirms only the admin path carries `post_text`.

## Related Research

- `context/archive/2026-06-09-prompt-review/research.md` — touches post generation pipeline
  context (supervisor attempts).

## Open Questions

1. **Admin read path (highest priority).** After the cutover, new posts have
   `announcements.post_text = NULL` and `x_post_id` set. Should `list_announcements_admin`
   LEFT JOIN `x_posts` on `x_post_id` to surface `post_text` (and later `tweet_ids`) so the
   admin panel keeps showing the generated thread? Or is losing post_text visibility for new
   posts acceptable until a follow-up? **Recommend: JOIN in this change** — otherwise the
   admin panel silently goes blank for every new post.
2. **Atomicity of `save_x_post`.** Multi-statement transaction (`BEGIN/COMMIT`) vs ordered
   best-effort (INSERT then UPDATE, tolerate orphan x_posts row on UPDATE failure)? Affects
   error handling and testability.
3. **Where to create `x_posts`.** Add `create_x_posts_table_if_not_exists()` to `main.py` +
   API startup, or defensively at the top of `post_main.main()` (post job currently creates
   nothing)? The post job is the only writer, so a defensive create there is the safest.
4. **`window` value mapping.** `x_posts.window` stores `ranek/poludnie/wieczor` (the raw key
   from `post_main`), not the display name `_WINDOW_NAMES`. Confirm raw key is intended.
5. **Backfill confirmed out of scope?** Ticket says deprecated columns get separate cleanup
   later → no migration of existing duplicated rows in this change. Confirm.
