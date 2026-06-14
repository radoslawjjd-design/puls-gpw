# PUL-29 — Extract `x_posts` table, remove `post_text` duplication — Implementation Plan

## Overview

Stop `save_post_text()` from writing the full X-thread text (plus `posted_at` and
`supervisor_attempts`) to every announcement row that contributed to a post. Introduce a
dedicated `x_posts` table holding one row per published post, link it from `announcements`
via a new `x_post_id` FK column, and replace `save_post_text()` with `save_x_post()`. This
removes N-way duplication and gives PUL-27 a single place to store `tweet_ids`.

## Current State Analysis

- `db/bigquery.py` is **single-table by construction**: module-level `_TABLE_NAME =
  "announcements"`, one `_SCHEMA` (`:32-50`), `_table_ref(client)` hardcodes the table
  (`:79-80`), and `create_table_if_not_exists()` / `ensure_schema_current()` operate on it
  alone (`:92-128`).
- **One writer**: `save_post_text(announcement_ids, post_text, supervisor_attempts)`
  (`db/bigquery.py:465-500`) does a single `UPDATE … WHERE announcement_id IN UNNEST(@ids)`
  setting `post_text`, `supervisor_attempts`, `posted_at = CURRENT_TIMESTAMP()`.
- **Two call sites**, both in `post_main.py`: success `:113`
  `save_post_text(ann_ids, "\n\n".join(post.tweets), attempt)` and failure `:120`
  `save_post_text(ann_ids, None, _MAX_ATTEMPTS)`. `window` is already in scope at
  `post_main.py:72` but not currently persisted.
- **Read path is admin-only**: `list_announcements_admin` selects `post_text, posted_at,
  supervisor_attempts` (`db/bigquery.py:368`, mapped `:395-398`) → `AnnouncementAdmin`
  (`src/api.py:53-56`) → `GET /announcements` admin role. `list_announcements_user`
  (`:411-462`) never selects `post_text` — the user API is unaffected.
- **Table creation wiring**: only `main.py:38-40` (scraper) and the API startup call
  `create_table_if_not_exists()` + `ensure_schema_current()`. `post_main.py` creates
  nothing — it assumes the schema exists.
- **Test surface**: `tests/test_bigquery.py:112-138,216-228` (3 `save_post_text` tests +
  import `:14`); mock admin row dicts in `tests/test_api.py:43-44` and
  `tests/e2e/conftest.py:22-23`; manual round-trip `scripts/test_bq.py`.

## Desired End State

- A `x_posts` table exists with columns `x_post_id` (REQUIRED), `window`, `post_text`,
  `tweet_ids`, `posted_at` (REQUIRED), `supervisor_attempts`.
- `announcements` has a new NULLABLE `x_post_id` column (auto-added by
  `ensure_schema_current()` on next startup).
- `save_x_post(announcement_ids, post_text, window, supervisor_attempts) → x_post_id`
  inserts one `x_posts` row and stamps `x_post_id` onto the contributing announcements.
  `save_post_text` no longer exists.
- `post_main.py` calls `save_x_post(...)` at both sites, passing `window`, and ensures the
  `x_posts` table exists before writing.
- The admin list surfaces `post_text` for new posts via `LEFT JOIN x_posts` and exposes
  `x_post_id`; old (already-duplicated) rows still render via `COALESCE`.
- `uv run pytest` is green; `scripts/test_bq.py` round-trips `x_posts` against real BQ.

### Key Discoveries:

- Single-table helpers must be generalized — `_table_ref(client, table)` (`db/bigquery.py:79`).
- `x_post_id` is additive/NULLABLE → `ensure_schema_current()` (`:105-128`) migrates it with
  zero manual DDL, consistent with the `2026-06-11-bq-fields-audit` precedent.
- BQ standard queries are not transactional; `save_x_post` uses ordered best-effort
  (INSERT then UPDATE) per the approved decision.
- `window` stores the raw key (`ranek`/`poludnie`/`wieczor`) from `post_main.py:72`, not the
  display name `_WINDOW_NAMES`.

## What We're NOT Doing

- **No backfill / migration** of existing duplicated rows — deprecated columns get a
  separate cleanup later (ticket).
- **No DROP** of the deprecated `post_text` / `posted_at` / `supervisor_attempts` columns on
  `announcements` (human-only, out of scope per `.claude/rules/db-bigquery.md`).
- **No `tweet_ids` population** — the column is created NULLABLE now; PUL-27 fills it.
- **No multi-statement BQ transaction** — best-effort ordered writes by decision.
- **No user-API change** — `list_announcements_user` is untouched.

## Implementation Approach

Two phases. Phase 1 is the BQ data layer — fully unit-testable, driven test-first
(`/10x-tdd`): schema, the `x_posts` table helper, `save_x_post`, the admin JOIN, and removal
of `save_post_text`. Phase 2 wires the pipeline and API model, adds defensive table
creation, fixes the test mock fixtures, and verifies an end-to-end round-trip on real BQ
(the non-unit-testable slice → `/10x-implement` + manual check).

## Critical Implementation Details

- **`save_x_post` ordering & atomicity**: generate `x_post_id` client-side
  (`uuid.uuid4().hex`), INSERT the `x_posts` row first, then UPDATE
  `announcements SET x_post_id WHERE announcement_id IN UNNEST(@ids)`. If the UPDATE fails or
  matches 0 rows, raise `BigQueryError`; the orphan `x_posts` row is harmless (`posted_at`
  still records the post happened). Use DML INSERT (not streaming) so the subsequent UPDATE
  isn't blocked by the streaming buffer — same constraint noted for `insert_announcement`
  (`db/bigquery.py:149-156`).
- **No new GCP client** — `save_x_post` and `create_x_posts_table_if_not_exists()` reuse
  `_get_client()`, so the `with_quota_project` guard and `load_dotenv()` ordering rules
  (`.claude/rules/db-bigquery.md`) are already satisfied.

## Phase 1: BQ data layer

### Overview

All schema/query changes in `db/bigquery.py` plus their unit tests. Test-first.

### Changes Required:

#### 1. Generalize table reference + add `x_post_id` to announcements schema

**File**: `db/bigquery.py`

**Intent**: Make the module able to address more than one table, and register the new FK
column so it auto-migrates onto `announcements`.

**Contract**: `_table_ref(client, table: str = _TABLE_NAME) -> str`. Add
`bigquery.SchemaField("x_post_id", "STRING", mode="NULLABLE")` to `_SCHEMA`. Update the
module docstring (`:1-16`): mark `post_text`/`posted_at`/`supervisor_attempts` as deprecated
(moved to `x_posts`), document `x_post_id` (FK to `x_posts.x_post_id`).

#### 2. Define `x_posts` schema + creation helper

**File**: `db/bigquery.py`

**Intent**: Declare the new table and a creation helper mirroring
`create_table_if_not_exists()`.

**Contract**: `_X_POSTS_TABLE_NAME = "x_posts"`; `_X_POSTS_SCHEMA` with
`x_post_id STRING REQUIRED`, `window STRING NULLABLE`, `post_text STRING NULLABLE`,
`tweet_ids STRING NULLABLE`, `posted_at TIMESTAMP REQUIRED`, `supervisor_attempts INTEGER
NULLABLE`. `create_x_posts_table_if_not_exists() -> None` using
`_table_ref(client, _X_POSTS_TABLE_NAME)`.

#### 3. Replace `save_post_text` with `save_x_post`

**File**: `db/bigquery.py`

**Intent**: Insert one `x_posts` row and link it onto the contributing announcements;
remove the old duplicating writer entirely.

**Contract**: `save_x_post(announcement_ids: list[str], post_text: str | None, window:
str, supervisor_attempts: int) -> str`. Generates `x_post_id = uuid.uuid4().hex`; INSERT
into `x_posts` (`posted_at = CURRENT_TIMESTAMP()`); then `UPDATE announcements SET x_post_id
= @x_post_id WHERE announcement_id IN UNNEST(@ids)`; raise `BigQueryError` on job error or 0
affected rows; return `x_post_id`. Delete `save_post_text` (`:465-500`).

#### 4. Admin list: LEFT JOIN x_posts

**File**: `db/bigquery.py`

**Intent**: Keep the admin panel showing the generated thread after the cutover, and expose
the new FK.

**Contract**: `list_announcements_admin` SELECT becomes a `LEFT JOIN
x_posts x ON a.x_post_id = x.x_post_id` (alias `announcements` as `a`); return
`COALESCE(x.post_text, a.post_text) AS post_text`, `COALESCE(x.posted_at, a.posted_at) AS
posted_at`, `COALESCE(x.supervisor_attempts, a.supervisor_attempts) AS supervisor_attempts`,
and add `a.x_post_id`. Result dict gains `x_post_id`. `_build_filter_clauses` needs **no
change**: its columns (`analysis_approved`, `ticker`, `company`, `event_type`,
`published_at`) do not exist in `x_posts`, so they remain unambiguous under the JOIN — do not
over-qualify them. Only `post_text`/`posted_at`/`supervisor_attempts` overlap, and those are
handled by the `COALESCE` + `a.`/`x.` qualification in the SELECT.

#### 5. Unit tests

**File**: `tests/test_bigquery.py`

**Intent**: Drive items 1-4 test-first; replace the obsolete `save_post_text` tests.

**Contract**: Remove `save_post_text` import (`:14`) and its 3 tests (`:114-138`,
`:216-228`). Add: `save_x_post` inserts into `x_posts` and updates `x_post_id` (assert both
queries fire, UUID returned, `posted_at = CURRENT_TIMESTAMP()` in the INSERT, `IN
UNNEST(@ids)` in the UPDATE); `save_x_post` raises on 0 affected rows;
`create_x_posts_table_if_not_exists` creates on `NotFound`; `list_announcements_admin`
query contains `LEFT JOIN` and `x_posts`. Reuse `_mock_bq_client(affected_rows=…)`.

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_bigquery.py`
- Full suite passes: `uv run pytest`
- No lingering references: `grep -rn "save_post_text" db/ src/ tests/` returns nothing

#### Manual Verification:

- `save_x_post` signature and `x_posts` schema match the PUL-29 spec.

**Implementation Note**: After completing this phase and all automated verification passes,
pause for manual confirmation before proceeding to Phase 2.

---

## Phase 2: Pipeline wiring, API model & real-BQ verification

### Overview

Wire `post_main.py` to `save_x_post`, ensure the table exists, surface `x_post_id` in the
API model, fix mock fixtures, and round-trip against real BigQuery.

### Changes Required:

#### 1. post_main wiring + defensive table creation

**File**: `post_main.py`

**Intent**: Use the new writer (passing `window`) and make the post job self-sufficient for
schema — it must guarantee both the `x_posts` table AND the `announcements.x_post_id` column
exist before the first write, independent of whether the scraper has run since deploy. The
post job creates/migrates nothing today, and `ensure_schema_current()` (the only thing that
adds `x_post_id`) runs only in `main.py:39-40`.

**Contract**: Import `save_x_post`, `create_table_if_not_exists`, `ensure_schema_current`,
`create_x_posts_table_if_not_exists` (drop `save_post_text`). At the start of `main()` call
`create_table_if_not_exists()` + `ensure_schema_current()` + `create_x_posts_table_if_not_exists()`
(mirrors `main.py:38-40`; all idempotent, ≤3×/day). Replace `:113` with
`save_x_post(ann_ids, "\n\n".join(post.tweets), window, attempt)` and `:120` with
`save_x_post(ann_ids, None, window, _MAX_ATTEMPTS)` (raw `window` key).

#### 2. Startup parity (scraper)

**File**: `main.py`

**Intent**: Create `x_posts` alongside `announcements` in the scraper startup for
consistency (idempotent no-op). NOTE: `src/api.py` has no startup/lifespan hook — do not add
one; the schema is ensured by `main.py` and `post_main.py` (item #1).

**Contract**: Add `create_x_posts_table_if_not_exists()` next to the existing
`create_table_if_not_exists()` / `ensure_schema_current()` calls (`main.py:39-40`). Update
imports.

#### 3. API admin model

**File**: `src/api.py`

**Intent**: Expose the new FK in the admin response.

**Contract**: Add `x_post_id: str | None = None` to `AnnouncementAdmin` (`:45-59`).

#### 4. Test fixtures

**Files**: `tests/test_api.py`, `tests/e2e/conftest.py`

**Intent**: Keep mock admin rows shape-compatible with the new dict key.

**Contract**: Add `"x_post_id": None` to the mock row dicts (`tests/test_api.py:43-44`,
`tests/e2e/conftest.py:22-23`). Keep the deprecated keys (columns still exist).

#### 5. Real-BQ round-trip

**File**: `scripts/test_bq.py`

**Intent**: Cover the non-unit-testable DDL/write path end-to-end.

**Contract**: Extend the script to `create_x_posts_table_if_not_exists()`, call
`save_x_post([ann_id], "tweet1\n\ntweet2", "poludnie", 1)`, read back the `x_posts` row and
the announcement's `x_post_id`, and clean up the `x_posts` row in the `finally` block.

### Success Criteria:

#### Automated Verification:

- Full suite passes: `uv run pytest`
- No lingering references: `grep -rn "save_post_text" .` (excluding context/) returns nothing

#### Manual Verification:

- `uv run python scripts/test_bq.py` completes all steps including the `x_posts` round-trip
  against real BigQuery.
- Admin `GET /announcements` returns `x_post_id` and a non-null `post_text` for a row linked
  to an `x_posts` entry.
- `x_posts` table visible in BigQuery with the correct schema.

**Implementation Note**: Pause for manual confirmation after automated checks pass.

---

## Testing Strategy

### Unit Tests:

- `save_x_post`: INSERT into `x_posts` + UPDATE `x_post_id`; UUID returned; `posted_at =
  CURRENT_TIMESTAMP()`; `IN UNNEST(@ids)`; raises on 0 affected rows.
- `create_x_posts_table_if_not_exists`: creates table on `NotFound`.
- `list_announcements_admin`: query contains `LEFT JOIN` + `x_posts`; result dict has
  `x_post_id`.

### Integration Tests:

- `scripts/test_bq.py` round-trip against real BigQuery (insert announcement → save_x_post →
  read back x_posts + x_post_id → cleanup).

### Manual Testing Steps:

1. Run `uv run python scripts/test_bq.py` — all steps pass.
2. Hit admin `GET /announcements` — confirm `x_post_id` present and `post_text` rendered via
   the JOIN.
3. Inspect BigQuery — `x_posts` schema correct, no `post_text` written to new
   `announcements` rows.

## Performance Considerations

The admin `LEFT JOIN` is on `x_post_id` over a small table; negligible. `save_x_post` adds
one INSERT per post (≤3× per day per window), no hot path.

## Migration Notes

`x_post_id` is added to `announcements` by `ensure_schema_current()`, which runs in both
entry points after this change: `main.py` (scraper) and `post_main.py` (post job). The post
job ensures the column itself before its first `save_x_post`, so it never depends on the
scraper having run first. `x_posts` is created by `create_x_posts_table_if_not_exists()` in
both jobs. The admin API has no startup hook and does not migrate schema — its `LEFT JOIN`
selecting `a.x_post_id` is safe once either job has run once post-deploy (one-time,
self-healing). Existing duplicated rows are left as-is (deprecated columns still populated
historically; admin JOIN falls back to them via `COALESCE`).

## References

- Research: `context/changes/pul-29-bq-x-posts-table/research.md`
- Prior schema migration: `context/archive/2026-06-11-bq-fields-audit/plan.md`
- Original schema/table-creation design: `context/archive/2026-06-02-bigquery-schema/plan.md`
- Post-generation origin of `save_post_text`: `context/archive/2026-06-08-xpost-generation/plan.md`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: BQ data layer

#### Automated

- [ ] 1.1 Unit tests pass: `uv run pytest tests/test_bigquery.py`
- [ ] 1.2 Full suite passes: `uv run pytest`
- [ ] 1.3 No lingering `save_post_text` references in db/ src/ tests/

#### Manual

- [ ] 1.4 `save_x_post` signature and `x_posts` schema match the PUL-29 spec

### Phase 2: Pipeline wiring, API model & real-BQ verification

#### Automated

- [ ] 2.1 Full suite passes: `uv run pytest`
- [ ] 2.2 No lingering `save_post_text` references (excluding context/)

#### Manual

- [ ] 2.3 `uv run python scripts/test_bq.py` round-trips `x_posts` against real BigQuery
- [ ] 2.4 Admin `GET /announcements` returns `x_post_id` and non-null `post_text` via JOIN
- [ ] 2.5 `x_posts` table visible in BigQuery with the correct schema
