<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Account settings + email-notifications opt-in (PUL-81 slice a)

- **Plan**: context/changes/email-notifications-settings/plan.md
- **Scope**: Phase 2 of 3 (API endpoints)
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
- Diff scope: `src/api.py` (+42), `tests/test_auth_api.py` (+43), `tests/e2e/conftest.py` (+26) + change docs. Exactly the planned surface — no scope creep (confirmed via `git show 8b4be55`).
- Plan adherence: 4 imports added; `NotificationSettingsIn` model; `_get_user_email` dependency; `GET`+`POST /api/notifications/settings` with `Depends(_get_user_id)`; email derived from JWT claim, not the body; DDL registered in the startup hook; conftest mocks all 4 new `db.bigquery.*` (incl. DDL no-ops) + stateful in-memory store.
- Safety: endpoints follow the watchlist/portfolio pattern (`try/except BigQueryError → 500`); BQ layer parameterized; address never taken from client input.
- Success criteria: 2.1 `test_auth_api.py`+`test_api.py` 186 passed; 2.2 E2E fixture boots; 2.3 full suite 658 passed; 2.4 verified live via TestClient round-trip (401 without cookie → default → enable (email from token, confirmed_at stamped) → persisted → disable).

## Findings

### F1 — POST decodes the session cookie twice

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/api.py — post_notifications_settings (_get_user_id + _get_user_email both call session_payload_from_request)
- **Detail**: POST depends on both `_get_user_id` and `_get_user_email`; each decodes the JWT independently → two decodes per request. Cosmetic (cookie-only decode, no I/O); mirroring `_get_user_id` keeps the deps symmetric.
- **Fix**: Optional — a single `_get_session_payload` dependency returning the full payload. Or accept.
- **Decision**: ACCEPTED — negligible at this scale; symmetry with `_get_user_id` preferred.
