<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Email notifications delivery (PUL-81 slice b)

- **Plan**: context/changes/email-notifications-delivery/plan.md
- **Scope**: Phase 2 of 3 (branded digest email)
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
- Diff scope: `src/notifier.py` (+75), `tests/test_notifier.py` (+39) + plan. No unplanned code.
- Safety: every embedded field (company, ticker, title, event_type, link, logo) escaped via `_html_escape(..., quote=True)` (PR #159); the ticker link is built then whole-escaped.
- Pattern: mirrors `_verification_html`/`_password_reset_html` + `send_verification_email`.
- Success criteria: `tests/test_notifier.py` 3 passed; full suite 670 passed; 2.3 verified live (plural 1/2-4/5+, subject, ticker link, logo).

## Findings

### F1 — event_type shown as raw code (underscored) in the email

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/notifier.py — _announcement_digest_html
- **Detail**: The digest rendered event_type verbatim (e.g. "wyniki_finansowe"). The SPA has a JS-only friendly-label map; no Python equivalent existed. Functional but slightly technical for a user-facing email.
- **Fix**: Added `_event_type_label()` (code → humanized label, e.g. "Wyniki finansowe") + a unit test.
- **Decision**: FIXED (Ładna etykieta) — `_event_type_label` humanizes the code; `test_announcement_digest_humanizes_event_type` covers it.
