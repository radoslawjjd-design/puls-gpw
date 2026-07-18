<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Onboarding Landing + Login/Register (PUL-72) — Phase 2

- **Plan**: context/changes/login-register-landing/plan.md
- **Scope**: Phase 2 of 3 (commit 02da22d — faro-v8 landing)
- **Date**: 2026-07-18
- **Verdict**: APPROVED
- **Findings**: 0 critical, 1 warning, 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS (intent 1:1; designer supersession explicit and user-approved) |
| Scope Discipline | PASS (designer extras are net improvements — CSV admin-gating closes a pre-existing user-CSV score/sentiment leak) |
| Safety & Quality | WARNING (F1 — fixed in triage) |
| Architecture | PASS (hash routing resyncs on every entry/exit; XSS clean — all card interpolations esc()'d) |
| Pattern Consistency | WARNING (F2/F4 observations) |
| Success Criteria | PASS (485 unit + 74 e2e re-run during review) |

## Findings

### F1 — Fake sample announcements reachable on production via /static/*.html

- **Severity**: ⚠️ WARNING · **Impact**: 🏃 LOW
- **Location**: static/index.html:1499 (+ faro-v8.html)
- **Detail**: `_LC_PREVIEW = /\.html$/.test(location.pathname)` is false at "/",
  but the static mount makes /static/index.html and /static/faro-v8.html live
  prod URLs where it is true. Real trigger: BQ outage → negative-cached [] →
  client renders `_LC_SAMPLES` — fabricated announcements with concrete figures
  presented as genuine analyses. (No score/sentiment either way.)
- **Fix**: guard preview on `location.protocol === 'file:'` — designer's
  from-disk preview keeps working; no served URL can ever show samples.
- **Decision**: FIXED

### F2 — renderTable: ${date}/${score}/${sc} interpolated without esc()

- **Severity**: 💡 OBSERVATION · **Impact**: 🏃 LOW
- **Location**: static/index.html:3504-3515
- **Detail**: safe today (Number/toLocaleString/fixed vocabulary) but breaks
  escaping discipline; a string score from the API would also throw at
  .toFixed and blank the table.
- **Decision**: FIXED (esc() wrapped in both admin and user row templates)

### F3 — Stale #/logowanie hash persists into the dashboard after login

- **Severity**: 💡 OBSERVATION · **Impact**: 🏃 LOW
- **Detail**: cosmetic; reload re-enters dashboard, first nav/logout clears it.
- **Decision**: DEFERRED to Phase 3 (auth wiring clears the hash after login —
  already planned)

### F4 — E2E locator scoping via CSS containers (.landing-nav / #api-key-panel)

- **Severity**: 💡 OBSERVATION · **Impact**: 🏃 LOW
- **Detail**: role locators do the matching; container scoping by class/id is a
  pragmatic concession where "Zaloguj się" is legitimately duplicated.
- **Decision**: SKIPPED (accepted trade-off)

## Triage summary

- Fixed: F1 (file: protocol guard), F2 (esc discipline)
- Deferred: F3 (phase 3)
- Skipped: F4
- Post-fix verification: 74 e2e passed (faro-v8.html re-synced from index.html)

## Notes

- Designer extras verified safe: CSV/`data-score` admin-only (modal guards
  hold), `_EVENT_TYPE_PL` diacritics (label↔code bijective).
- Commit-message nit: "test_login_ux rewritten (8 tests)" — file has 7; the
  8th (theme cycle) lives in test_profile_menu.py.
