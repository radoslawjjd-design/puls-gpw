<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Account settings + email-notifications opt-in (PUL-81 slice a)

- **Plan**: context/changes/email-notifications-settings/plan.md
- **Scope**: Phase 1 of 3 (BQ data layer)
- **Date**: 2026-07-21
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Evidence
- Diff scope: `db/bigquery.py` (+124), `tests/test_bigquery.py` (+74) + change-folder docs. No unplanned code files.
- Safety: all values bound as `ScalarQueryParameter` (`@user_id/@email/@enabled/@min_score`); only f-string interpolation is `_table_ref()` over internal env constants — identical to every other function. No injection surface. Error handling wraps queries in `BigQueryError`; upsert also checks `job.errors` (mirrors `upsert_user_login`). DDL is additive (`ensure_schema_current` adds columns only).
- Pattern: `_NOTIFICATION_SUBSCRIPTIONS_TABLE_NAME`/`_SCHEMA` + create/ensure mirror `_USERS_*`/`_WATCHLIST_*`; get/upsert mirror `get_user_role`/`upsert_user_login`.
- Success criteria: `uv run pytest tests/test_bigquery.py` → 96 passed (then 97 after F1 fix); full suite 551 non-e2e + 103 e2e = 654 passed; manual 1.3 verified live (unknown user → `{enabled: False, ...}`, no raise).

## Findings

### F1 — create/ensure DDL helpers have no direct test

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: db/bigquery.py — create_notification_subscriptions_table_if_not_exists / ensure_notification_subscriptions_schema_current
- **Detail**: The 4 unit tests cover get/upsert; the two DDL helpers had no direct test. Phase 2 conftest + Phase 3 E2E both mock them as no-ops, so no test executed their real body. In-repo precedent exists (`test_create_companies_table_creates_on_not_found`).
- **Fix**: Add a `create_notification_subscriptions_table_creates_on_not_found` test mirroring the companies test.
- **Decision**: FIXED (Fix now) — added test; `tests/test_bigquery.py` notification tests now 5 passed.
