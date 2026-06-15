<!-- PLAN-REVIEW-REPORT -->
# Plan Review: X Publisher Core

- **Plan**: context/changes/x-publisher-core/plan.md
- **Mode**: Deep
- **Date**: 2026-06-15
- **Verdict**: REVISE → SOUND after fixes
- **Findings**: 0 critical, 1 warning, 2 observations (all FIXED)

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | WARNING (F1, F3) |
| Plan Completeness | WARNING (F2) |

## Grounding

10/10 paths ✓, 6/6 symbols ✓, brief↔plan ✓. Verified against live code:
`save_x_post` returns `x_post_id` and omits `tweet_ids` from INSERT, `window` already backticked
(`db/bigquery.py:505-570`); `ensure_schema_current` migrates announcements `_SCHEMA` only
(`db/bigquery.py:142,148-149`); `send_post_email(window_name, date_str, tweets, scores=None)` has a
single caller `post_main.py:127`; `_window_bounds` 'ranek' crosses midnight for announcement fetch
(`post_main.py:49-62`).

## Findings

### F1 — "partial" status is wrong when the FIRST tweet fails (0 published)

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 1 (publisher contract) ↔ Phase 3 (status mapping)
- **Detail**: Phase 1 raised `XPublishPartialError(published_ids, cause)` on any mid-thread failure;
  Phase 3 maps that to `x_publish_status='partial'`. If tweet 1 fails, `published_ids=[]` — nothing is
  on X, yet it would be recorded as 'partial', corrupting the status precision the column exists for.
- **Fix (applied, variant a)**: Publisher raises a plain `XPublisherError` when 0 tweets posted;
  `XPublishPartialError` only when ≥1 posted (non-empty `published_ids`). Phase 3 already maps
  partial→'partial' and other errors→'failed', so a first-tweet failure now lands as 'failed'.
- **Decision**: FIXED (Fix A) — Phase 1 publisher contract updated.

### F2 — x_posts migration: invocation site + extend-vs-sibling left open

- **Severity**: 🔭 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 2, item 2
- **Detail**: `ensure_schema_current()` is hardcoded to `_SCHEMA` + announcements `_table_ref`. Plan
  said "extend OR add a sibling" without pinning the invocation site; `post_main.py:90-92` already
  calls `ensure_schema_current()` at startup — a sibling would need adding there too or the column
  never lands.
- **Fix (applied)**: Parameterize the migration over `(table_ref, schema)`, call it for x_posts, and
  wire the x_posts invocation explicitly into `post_main.py:90-92`.
- **Decision**: FIXED — Phase 2 item 2 updated.

### F3 — idempotency guard: key off posted_at run-day, and it's not a hard lock

- **Severity**: 🔭 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 2, item 4 (`x_post_already_published`)
- **Detail**: x_posts has no date column, only `posted_at`. Dedup key must be `window` +
  `DATE(posted_at)` in Warsaw — not `_window_bounds` (those cross midnight for 'ranek', bounding
  announcement fetch not publish). Also the guard is check-then-act, not a lock (TOCTOU).
- **Fix (applied)**: Phase 2 item 4 specifies the Warsaw-calendar-day dedup key and a one-line
  accepted-risk note on the TOCTOU window.
- **Decision**: FIXED — Phase 2 item 4 updated.
