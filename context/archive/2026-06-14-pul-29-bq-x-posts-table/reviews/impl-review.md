<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: PUL-29 — Extract x_posts table, remove post_text duplication

- **Plan**: context/changes/pul-29-bq-x-posts-table/plan.md
- **Scope**: Full plan (Phase 1 + 2 of 2)
- **Date**: 2026-06-14
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Findings

### F1 — Two beyond-plan edits (both necessary bug fixes)

- **Severity**: 🔵 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; nothing to change
- **Dimension**: Scope Discipline
- **Location**: db/bigquery.py:526, scripts/test_bq.py:42
- **Detail**: The plan's literal contract didn't anticipate (a) `window` being a BQ reserved keyword (now backtick-quoted in the save_x_post INSERT) or (b) the script needing `ensure_schema_current()` to migrate `announcements.x_post_id` onto the existing table. Both surfaced during the real-BQ round-trip, are correct, and are recorded in the Phase 2 commit message + the captured lesson (context/foundation/lessons.md). No scope creep — both fall inside the change's intent.
- **Fix**: None — accepted as documented improvements.
- **Decision**: ACCEPTED — documented bug fixes within scope.

### F2 — save_x_post is non-atomic (orphan x_posts row on UPDATE failure)

- **Severity**: 🔵 OBSERVATION
- **Impact**: 🏃 LOW — by design; no action
- **Dimension**: Safety & Quality
- **Location**: db/bigquery.py:505-565
- **Detail**: INSERT runs before UPDATE with no transaction; a failed/0-row UPDATE leaves an orphan x_posts row. This is the approved decision (BQ standard queries aren't transactional) and is documented in the docstring — the orphan is harmless (posted_at still records the post happened).
- **Fix**: None — explicitly accepted in the plan.
- **Decision**: ACCEPTED — by design per plan.

## Verification evidence

- Full suite: `uv run pytest` → 103 passed.
- No lingering references: `save_post_text` absent outside context/.
- Real-BQ round-trip: `scripts/test_bq.py` passed (x_posts insert + announcement link + cleanup).
- Live schema confirmed: `x_posts` (6 cols, modes match spec) + `announcements.x_post_id` (STRING NULLABLE).
- Admin `GET /announcements?ticker=…` returned `x_post_id` and non-null `post_text` via JOIN (live server check).

## Commits

- `6c38f5f` — Phase 1: BQ data layer
- `3e42548` — Phase 2: pipeline wiring, API model, real-BQ verification
- `742ce47` — epilogue (plan close-out)
- `73eb9f6` — lessons.md (reserved-keyword + mocked-test blind spot)
