<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Remove analysis_score from user-facing /announcements response

- **Plan**: `context/changes/remove-analysis-score/plan.md`
- **Mode**: Deep
- **Date**: 2026-06-19
- **Verdict**: SOUND
- **Findings**: 0 critical, 1 warning, 0 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | WARNING |
| Plan Completeness | PASS |

## Grounding

Grounding: 5/5 paths ✓ (`src/api.py`, `db/bigquery.py`, `tests/test_api.py`, `tests/test_bigquery.py`, `context/changes/remove-analysis-score/frame.md`), 5/5 symbols ✓ (`AnnouncementUser`, `list_announcements_user`, `test_announcements_user_returns_subset_fields`, `test_announcements_admin_returns_list`, `test_list_announcements_user_only_approved`), brief↔plan ✓

Blast-radius sweep: grepped `analysis_score` project-wide — all other hits (`test_post_selection.py`, `test_post_generator.py`, `test_analyzer.py`, `tests/test_bigquery.py:64-181` for `fetch_top_n_for_window`/`save_x_post`, `tests/e2e/conftest.py`) belong to unrelated functions (`save_analysis_result`, `fetch_top_n_for_window`, the analyzer) — none touch `list_announcements_user` or `AnnouncementUser`. e2e fixture mocks `list_announcements_user` with `return_value=[]`, so no e2e coverage exercises this field either way — confirmed no blast radius beyond the two files in scope.

## Findings

### F1 — No real-BigQuery round-trip step for the SQL edit

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 1 — Manual Verification
- **Detail**: `context/foundation/lessons.md` ("BigQuery — kolumny o nazwach reserved keywords + limity mockowanych testów") states mocked BQ tests never send SQL to a real parser; a real round-trip via `scripts/test_bq.py` is the standing mandatory manual-verification step for any change to hand-built SQL in `db/bigquery.py`. The plan edited the `SELECT` clause in `list_announcements_user` (`db/bigquery.py:644-645`) but originally only listed `curl` checks against the API in Manual Verification — never executing the modified query against a real BigQuery table. Risk is low here (pure column removal, no new identifiers/keywords) but the project rule is unconditional for this class of edit.
- **Fix**: Add a manual verification bullet to Phase 1: run `scripts/test_bq.py` (or an ad-hoc call to `list_announcements_user(...)`) against the real BigQuery table after the SELECT-clause edit, confirming the query executes and returns rows without `analysis_score`.
- **Decision**: FIXED — added as Phase 1 Manual Verification bullet + Progress item 1.6.
