<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Account settings page + email-notifications opt-in (PUL-81 slice a)

- **Plan**: context/changes/email-notifications-settings/plan.md
- **Mode**: Deep
- **Date**: 2026-07-21
- **Verdict**: SOUND
- **Findings**: 0 critical, 0 warnings, 2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | PASS |
| Plan Completeness | PASS |

## Grounding
8/8 paths ✓, 11/11 symbols ✓, brief↔plan ✓. Progress↔Phase consistent (P1: 1.1–1.3, P2: 2.1–2.4, P3: 3.1–3.6). Verified: `_get_user_id` uses `session_payload_from_request` (src/api.py:152, email claim reachable); nav gating `perUserNav = !apiKey ? '' : 'none'` (static/index.html:2023-2025); `_watchlist_store` conftest pattern (tests/e2e/conftest.py:219); `upsert_user_login`/`get_user_role`/`ensure_schema_current` templates (db/bigquery.py:990/1027/149); `PortfolioPositionIn` body-model + `/api/portfolio/positions` (src/api.py:261/675).

## Findings

### F1 — `confirmed_at = now` on enable creates an implicit slice-b contract

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots / Lean Execution
- **Location**: Phase 1 §2 — upsert_notification_settings
- **Detail**: Double opt-in is dropped but the plan still stamps `confirmed_at = CURRENT_TIMESTAMP()` on enable. Without a written invariant, slice (b) could filter delivery on `confirmed_at IS NOT NULL` instead of `enabled`, and the two columns could drift.
- **Fix**: Add an invariant to Migration Notes — `enabled=true` is the authoritative opt-in flag; slice (b) filters on `enabled=true`; `confirmed_at` is informational.
- **Decision**: FIXED (Fix in plan) — added "Opt-in invariant (slice a↔b contract)" paragraph to Migration Notes.

### F2 — Optimistic save has no in-flight guard (rapid-toggle desync)

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 3 §3 — notifications panel save
- **Detail**: Optimistic save reverts only on error. Two quick toggles race; if both 2xx responses land out of order, stored state can end opposite to the UI with no error to trigger a revert. Also makes E2E nondeterministic.
- **Fix**: Disable the switch while a save is in flight (re-enable in `finally`) to serialize toggles.
- **Decision**: FIXED (Fix in plan) — added in-flight disable/re-enable to Phase 3 §3 contract.
