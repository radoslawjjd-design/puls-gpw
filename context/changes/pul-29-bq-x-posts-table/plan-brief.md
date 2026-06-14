# PUL-29 — Extract `x_posts` table — Plan Brief

> Full plan: `context/changes/pul-29-bq-x-posts-table/plan.md`
> Research: `context/changes/pul-29-bq-x-posts-table/research.md`

## What & Why

`save_post_text()` writes the full X-thread text (and `posted_at`, `supervisor_attempts`) to
every announcement row that contributed to a post — N companies = N identical copies. We
extract a dedicated `x_posts` table (one row per post) linked from `announcements` via a new
`x_post_id` FK, and replace `save_post_text()` with `save_x_post()`. Removes duplication and
gives PUL-27 a single home for `tweet_ids`.

## Starting Point

`db/bigquery.py` is single-table by construction. One writer (`save_post_text`,
`db/bigquery.py:465`) called from two sites in `post_main.py`; the only reader is the admin
list (`list_announcements_admin` → `AnnouncementAdmin`). The user API never reads
`post_text`.

## Desired End State

A `x_posts` table holds each post once; `announcements.x_post_id` links to it. `save_x_post`
inserts the post row and stamps the FK onto contributors. The admin panel still shows the
thread (via `LEFT JOIN x_posts` + `COALESCE`). `save_post_text` is gone. Existing rows are
left untouched.

## Key Decisions Made

| Decision | Choice | Why | Source |
| --- | --- | --- | --- |
| Admin read path | `LEFT JOIN x_posts` + `COALESCE(post_text)` | New posts write to x_posts; without JOIN the panel goes blank | Plan |
| `save_x_post` atomicity | Ordered best-effort (INSERT then UPDATE) | BQ has no transactions in plain queries; orphan x_posts row is harmless | Plan |
| `x_posts` table creation | Defensive in `post_main.main()` + startup parity | Post job creates no tables today; it's the only writer | Plan |
| `window` value | Raw key (`ranek`/`poludnie`/`wieczor`) | Stable for analytics; display name is a presentation concern | Plan |
| Old `save_post_text` | Remove entirely | Only caller is rewired; no dead code (git covers rollback) | Plan |
| Backfill | Out of scope | Deprecated columns get a separate cleanup later (ticket) | Research |

## Scope

**In scope:** `x_posts` schema + creation helper; `x_post_id` on `announcements`
(auto-migrated); `save_x_post`; admin JOIN; `post_main` wiring; API model + test fixtures;
real-BQ round-trip in `scripts/test_bq.py`.

**Out of scope:** data backfill; DROP of deprecated columns (human-only); `tweet_ids`
population (PUL-27); multi-statement BQ transactions; user-API changes.

## Architecture / Approach

Generalize `_table_ref(client, table)`; add `_X_POSTS_SCHEMA` +
`create_x_posts_table_if_not_exists()`. `save_x_post` generates a UUID, INSERTs one
`x_posts` row, then UPDATEs `x_post_id` onto the contributing announcements. Admin list
LEFT-JOINs `x_posts`. `x_post_id` is a NULLABLE column → `ensure_schema_current()` adds it
with zero manual DDL.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. BQ data layer | schema, `x_posts` helper, `save_x_post`, admin JOIN, remove `save_post_text` (TDD) | Admin JOIN column ambiguity |
| 2. Wiring + verification | `post_main`/startup wiring, API model, mock fixtures, real-BQ round-trip | DDL only verifiable on real BQ |

**Prerequisites:** ADC for the real-BQ round-trip (`gcloud auth application-default login`).
**Estimated effort:** ~1 session across 2 phases.

## Open Risks & Assumptions

- `save_x_post` is non-atomic — a failed UPDATE leaves an orphan `x_posts` row (accepted).
- Admin JOIN must qualify columns to avoid ambiguity with deprecated `announcements` columns.
- Existing duplicated rows remain until the separate cleanup change.

## Success Criteria (Summary)

- New posts write `post_text` once (in `x_posts`), not N times.
- Admin `GET /announcements` still shows the thread and exposes `x_post_id`.
- `uv run pytest` green; `scripts/test_bq.py` round-trips `x_posts` on real BigQuery.
